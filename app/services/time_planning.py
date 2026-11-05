"""Запросы ГМа на планы игрового времени без автоматического расхода ресурсов."""
from copy import deepcopy
from datetime import datetime, timezone
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db, socketio
from app.models import LobbyCharacter, LobbyParticipant, TimePlan, TimePlanRequest
from app.services.character_roles import is_npc
from app.services.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError


def _now():
    return datetime.now(timezone.utc)


def _commit():
    try:db.session.commit()
    except StaleDataError as error:
        db.session.rollback()
        raise ConflictError('План изменился. Обновите его') from error


def _notify(plan):
    payload={'lobby_id':plan.request.lobby_id,'request_id':plan.request_id,'plan_id':plan.id}
    for user_id in {plan.request.gm_id,plan.owner_id}:
        socketio.emit('time_plans_changed',payload,room=f'user_{user_id}')


def _gm(lobby,user_id):
    if lobby.gm_id!=user_id:raise PermissionDenied('Только ГМ управляет запросами времени')


def _plan(lobby,plan_id,user_id):
    plan=db.session.get(TimePlan,plan_id) if type(plan_id) is int else None
    if not plan or plan.request.lobby_id!=lobby.id:raise NotFoundError('План времени не найден')
    if user_id not in {lobby.gm_id,plan.owner_id}:raise PermissionDenied('План другого владельца недоступен')
    return plan


def _summary(plan):
    activities=deepcopy(plan.activities or [])
    occupied=sum(item['duration_minutes'] for item in activities)
    return {'id':plan.id,'request_id':plan.request_id,'character_id':plan.character_id,
        'character_name':plan.character.name if plan.character else 'Персонаж',
        'owner_id':plan.owner_id,'start_minute':plan.request.start_minute,
        'window_minutes':plan.request.window_minutes,'activities':activities,
        'occupied_minutes':occupied,'free_minutes':max(0,plan.request.window_minutes-occupied),
        'overflow_minutes':max(0,occupied-plan.request.window_minutes),
        'overflow_state':plan.overflow_state,'confirmed':bool(plan.confirmed),
        'revision':plan.revision}


def list_plans(lobby,user_id):
    query=TimePlan.query.join(TimePlanRequest).filter(TimePlanRequest.lobby_id==lobby.id)
    if user_id!=lobby.gm_id:query=query.filter(TimePlan.owner_id==user_id)
    plans=query.order_by(TimePlanRequest.created_at.desc(),TimePlan.id).all()
    owners=[]
    if user_id==lobby.gm_id:
        from app.models import User
        owner_ids={character.owner_id for character in LobbyCharacter.query.filter_by(lobby_id=lobby.id).all()
            if not is_npc(character.data or {})}
        owners=[{'id':owner.id,'name':owner.username} for owner in User.query.filter(User.id.in_(owner_ids)).order_by(User.username).all()]
    from app.services.combat import CombatService
    return {'plans':[_summary(plan) for plan in plans],'owners':owners,
        'skills':CombatService.NARRATIVE_SKILLS,'is_gm':user_id==lobby.gm_id}


def create_request(lobby,user_id,payload):
    _gm(lobby,user_id)
    if not isinstance(payload,dict) or set(payload)-{'request_id','owner_ids','window_minutes'}:
        raise ValidationError('Некорректный запрос игрового времени')
    request_id=payload.get('request_id')
    owners=payload.get('owner_ids')
    minutes=payload.get('window_minutes')
    if not isinstance(request_id,str) or not 8<=len(request_id)<=80 or not all(c.isalnum() or c in '-_' for c in request_id):
        raise ValidationError('Нужен идентификатор запроса')
    if not isinstance(owners,list) or not owners or len(owners)>100 or any(type(x) is not int or x<=0 for x in owners) or len(set(owners))!=len(owners):
        raise ValidationError('Выберите владельцев персонажей')
    if type(minutes) is not int or not 1<=minutes<=10080:raise ValidationError('Время должно быть от 1 минуты до 7 суток')
    owners=sorted(owners)
    existing=db.session.get(TimePlanRequest,request_id)
    if existing:
        if existing.lobby_id!=lobby.id or existing.owner_ids!=owners or existing.window_minutes!=minutes:
            raise ConflictError('Идентификатор запроса уже использован')
        return {'request_id':request_id,'plans':[_summary(plan) for plan in existing.plans],'replayed':True}
    members={member.user_id for member in LobbyParticipant.query.filter_by(lobby_id=lobby.id).all()}
    if not set(owners)<=members:raise ValidationError('Выбранный владелец не участвует в комнате')
    characters=[character for character in LobbyCharacter.query.filter_by(lobby_id=lobby.id).order_by(LobbyCharacter.id).all()
        if character.owner_id in owners and not is_npc(character.data or {})]
    if {character.owner_id for character in characters}!=set(owners):
        raise ValidationError('У каждого выбранного владельца должен быть персонаж')
    request=TimePlanRequest(id=request_id,lobby_id=lobby.id,gm_id=user_id,
        start_minute=(lobby.game_day or 1)*1440+(lobby.game_time_minutes or 0),
        window_minutes=minutes,owner_ids=owners,created_at=_now())
    db.session.add(request)
    plans=[TimePlan(request=request,character_id=character.id,owner_id=character.owner_id,
        activities=[],confirmed=False,overflow_state='none',revision=1,updated_at=_now())
        for character in characters]
    db.session.add_all(plans);_commit()
    for plan in plans:_notify(plan)
    return {'request_id':request_id,'plans':[_summary(plan) for plan in plans],'replayed':False}


def _activity_input(value):
    if not isinstance(value,dict) or set(value)-{'id','name','duration_minutes','note'}:
        raise ValidationError('Некорректная активность')
    identifier=value.get('id');name=value.get('name');duration=value.get('duration_minutes');note=value.get('note','')
    if not isinstance(identifier,str) or not 8<=len(identifier)<=80 or not all(c.isalnum() or c in '-_' for c in identifier):
        raise ValidationError('Некорректный идентификатор активности')
    if not isinstance(name,str) or not 1<=len(name.strip())<=120:raise ValidationError('Укажите название активности')
    if type(duration) is not int or not 1<=duration<=10080:raise ValidationError('Длительность активности должна быть от 1 минуты до 7 суток')
    if not isinstance(note,str) or len(note)>1000:raise ValidationError('Пояснение слишком длинное')
    return {'id':identifier,'name':name.strip(),'duration_minutes':duration,'note':note.strip()}


def _owner_fields(activities):
    return [{key:entry[key] for key in ('id','name','duration_minutes','note')} for entry in activities]


def save_plan(lobby,plan_id,user_id,payload):
    plan=_plan(lobby,plan_id,user_id)
    if user_id!=plan.owner_id:raise PermissionDenied('План заполняет владелец персонажа')
    if not isinstance(payload,dict) or set(payload)-{'revision','activities','confirm'}:
        raise ValidationError('Некорректный план времени')
    incoming=payload.get('activities');confirm=payload.get('confirm')
    if not isinstance(incoming,list) or len(incoming)>100 or type(confirm) is not bool:
        raise ValidationError('Укажите активности и подтверждение')
    normalized=[_activity_input(entry) for entry in incoming]
    if len({entry['id'] for entry in normalized})!=len(normalized):raise ValidationError('Активность повторяется')
    if sum(entry['duration_minutes'] for entry in normalized)>10080:raise ValidationError('План длиннее 7 суток')
    if type(payload.get('revision')) is not int:raise ConflictError('Обновите план перед сохранением')
    if payload['revision']!=plan.revision:
        if _owner_fields(plan.activities or [])==normalized and plan.confirmed==confirm:
            return {**_summary(plan),'replayed':True}
        raise ConflictError('План изменился. Обновите его перед сохранением')
    if _owner_fields(plan.activities or [])==normalized and plan.confirmed==confirm:
        return {**_summary(plan),'replayed':True}
    old={entry['id']:entry for entry in plan.activities or []}
    old_approved=[(index,entry) for index,entry in enumerate(plan.activities or []) if entry.get('approved')]
    elapsed=0
    for entry in plan.activities or []:
        elapsed+=entry['duration_minutes']
        if elapsed>plan.request.window_minutes and entry.get('approved'):
            raise ConflictError('План с утверждённой активностью сверх лимита нельзя менять')
    for index,entry in old_approved:
        if index>=len(normalized) or normalized[index]!=_owner_fields([entry])[0]:
            raise ConflictError('Утверждённую активность нельзя менять')
    activities=[]
    for entry in normalized:
        previous=old.get(entry['id'])
        activities.append({**entry,'approved':bool(previous and previous.get('approved')),
            'roll':deepcopy(previous.get('roll')) if previous and previous.get('roll') else None})
    if old_approved and not confirm:raise ConflictError('Утверждённый план нельзя снять с подтверждения')
    occupied=sum(entry['duration_minutes'] for entry in activities)
    plan.activities=activities
    plan.confirmed=confirm
    plan.overflow_state=('pending' if occupied>plan.request.window_minutes else 'none') if confirm else 'none'
    plan.updated_at=_now()
    _commit();_notify(plan)
    return {**_summary(plan),'replayed':False}


def review_plan(lobby,plan_id,user_id,payload):
    _gm(lobby,user_id)
    plan=_plan(lobby,plan_id,user_id)
    if not isinstance(payload,dict) or set(payload)-{'revision','action','activity_id'}:
        raise ValidationError('Некорректное решение ГМа')
    if type(payload.get('revision')) is not int or payload['revision']!=plan.revision:
        raise ConflictError('План изменился. Обновите его')
    if not plan.confirmed:raise ValidationError('Владелец ещё не подтвердил план')
    action=payload.get('action')
    if action in {'approve_overflow','deny_overflow'}:
        if sum(entry['duration_minutes'] for entry in plan.activities or [])<=plan.request.window_minutes:
            raise ValidationError('Превышения нет')
        if action=='deny_overflow':
            elapsed=0
            for entry in plan.activities or []:
                elapsed+=entry['duration_minutes']
                if elapsed>plan.request.window_minutes and entry.get('approved'):
                    raise ConflictError('Сначала снимите утверждение активности сверх лимита')
        plan.overflow_state='approved' if action=='approve_overflow' else 'denied'
    elif action in {'approve_activity','deny_activity'}:
        activity_id=payload.get('activity_id')
        if not isinstance(activity_id,str):raise ValidationError('Выберите активность')
        activities=deepcopy(plan.activities or [])
        elapsed=0;selected=None
        for entry in activities:
            elapsed+=entry['duration_minutes']
            if entry['id']==activity_id:selected=entry;break
        if selected is None:raise NotFoundError('Активность не найдена')
        if action=='approve_activity' and elapsed>plan.request.window_minutes and plan.overflow_state!='approved':
            raise ValidationError('Сначала согласуйте превышение времени')
        if action=='deny_activity' and selected.get('roll'):
            raise ConflictError('Активность с броском уже нельзя отклонить')
        selected['approved']=action=='approve_activity'
        plan.activities=activities
    else:raise ValidationError('Неизвестное решение ГМа')
    plan.updated_at=_now()
    _commit();_notify(plan)
    return _summary(plan)


def roll_activity(lobby,plan_id,user_id,payload):
    _gm(lobby,user_id)
    plan=_plan(lobby,plan_id,user_id)
    if not isinstance(payload,dict) or set(payload)-{'activity_id','skill_path','difficulty'}:
        raise ValidationError('Некорректная проверка активности')
    from app.services.combat import CombatService
    activity_id=payload.get('activity_id');path=payload.get('skill_path');difficulty=payload.get('difficulty')
    if not isinstance(activity_id,str) or path not in CombatService.NARRATIVE_SKILLS or type(difficulty) is not int or not 1<=difficulty<=40:
        raise ValidationError('Выберите активность, навык и СЛ от 1 до 40')
    activities=deepcopy(plan.activities or [])
    entry=next((item for item in activities if item['id']==activity_id),None)
    if entry is None:raise NotFoundError('Активность не найдена')
    if not plan.confirmed or not entry.get('approved'):raise ValidationError('Сначала утвердите активность')
    elapsed=0
    for item in activities:
        elapsed+=item['duration_minutes']
        if item['id']==activity_id:break
    if elapsed>plan.request.window_minutes and plan.overflow_state!='approved':
        raise ValidationError('Сначала согласуйте превышение времени')
    if entry.get('roll'):
        if entry['roll']['skill_path']==path and entry['roll']['difficulty']==difficulty:
            return {**_summary(plan),'replayed':True}
        raise ConflictError('Бросок этой активности уже выполнен')
    from app.services.social_check import run as run_skill_check
    operation_id=f'time-plan-{plan.id}-{activity_id}'
    result=run_skill_check(plan.character_id,user_id,{
        'operation_id':operation_id,'skill_path':path,'difficulty':difficulty})
    check=result['check']
    if check['skill_path']!=path or check['difficulty']!=difficulty:
        raise ConflictError('Для этой активности уже выбран другой бросок')
    entry['roll']={'skill_path':path,'skill_label':CombatService.NARRATIVE_SKILLS[path],
        'difficulty':difficulty,'rolls':check.get('rolls',[]),'total':check.get('total'),
        'success':bool(check['success']),'failure_reason':check.get('failure_reason')}
    plan.activities=activities;plan.updated_at=_now()
    _commit();_notify(plan)
    return {**_summary(plan),'replayed':False,'rolled_activity_id':activity_id}
