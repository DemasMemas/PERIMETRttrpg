"""Установка протезов: операция по игровому времени, расходники и один исход."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import random
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState, DeferredCombatAction, CharacterInteractionRequest
from app.services.exceptions import ValidationError, ConflictError, NotFoundError
from app.services.prostheses import installed, profile, loss, sync_weapons

DOCTOR_KEY='prosthesisSurgeryId'
PATIENT_KEY='prosthesisSurgeryPatientId'


def _meta(character):
    return ((character.data or {}).get('health') or {}).get('combatMeta') or {}


def busy(data):
    meta=((data or {}).get('health') or {}).get('combatMeta') or {}
    return bool(meta.get(DOCTOR_KEY) or meta.get(PATIENT_KEY))


def _validate_pair(doctor,patient):
    from app.services.combat import CombatService
    if doctor.location_id!=patient.location_id or not CombatService._is_adjacent(doctor,patient) and doctor.id!=patient.id:
        raise ValidationError('Для операции врач и пациент должны находиться рядом на одном этаже')
    if LocationCombatState.query.filter_by(location_id=doctor.location_id,status='active').first():
        raise ValidationError('Операция выполняется только вне боя')
    if CombatService._character_condition(doctor.character.data)['state']!='active':
        raise ValidationError('Врач не может проводить операцию')
    if CombatService._character_condition(patient.character.data)['state']=='dead':
        raise ValidationError('Нельзя оперировать погибшего персонажа')


def _validate_item(patient_data,zone,item,operation):
    from app.services.prostheses import ZONES
    rule=profile(item)
    if operation!='surgery_install':raise ValidationError('Правила снятия операционного крепления ещё не уточнены')
    if zone not in ZONES or not rule or rule['mount'] not in {'clamp','integrated'}:
        raise ValidationError('Выберите конечность и протез с операционным креплением')
    parts=installed(patient_data).get(zone) or {}
    if parts.get('base'):raise ValidationError('На конечности уже установлен протез')
    injury=loss(patient_data,zone)
    if not injury or injury.get('loss_extent')!=rule['extent'] or rule['kind']!=('arm' if zone.endswith('Arm') else 'leg'):
        raise ValidationError('Протез не подходит степени утраты конечности')
    if item.get('broken') or item.get('durability',rule['durability'])<=0:
        raise ValidationError('Сломанный протез нельзя восстановить операцией')
    return rule


def _validate_doctor(data,rule):
    from app.services.combat import CombatService
    from app.services.weapon_grip import functional_arms
    if functional_arms(data)<1:raise ValidationError('Для операции нужна действующая рука')
    for skill in ('medicine','engineering'):
        if CombatService._skill_value(data,'skills.other.'+skill)<rule['installation']['skill_minimum']:
            raise ValidationError(f"Для операции нужны Медицина и Инженерия не ниже {rule['installation']['skill_minimum']}")


def _supplies(data,requirements,lobby_id):
    """Сначала план всех расходов; индексы не сдвигаются до завершения проверки."""
    from app.services.medical_procedure import _profile, _validated_uses
    from app.services.combat import CombatService
    result=[];remaining=dict(requirements)
    def visit(value,path):
        if isinstance(value,list):
            for index,item in enumerate(value):visit(item,[*path,index])
        elif isinstance(value,dict):
            if value.get('category')=='consumable' and value.get('name'):
                try:template,direct=_profile(value,lobby_id)
                except ValidationError:return
                name=template.name.casefold().replace('ё','е')
                key=('surgical_uses' if direct.get('surgical_kit') else
                    'morphine_ampoules' if 'морфи' in name else
                    'emotion_suppressor_ampoules' if 'подавитель эмоций' in name else None)
                if key and remaining[key]>0:
                    quantity,maximum,uses,_=_validated_uses(value,1,template,actor_data=data)
                    amount=min(remaining[key],(quantity-1)*maximum+uses)
                    result.append((path,amount,template));remaining[key]-=amount
            for key in ('contents','pouches'):
                if key in value:visit(value[key],[*path,key])
    for key,value in (data.get('inventory') or {}).items():visit(value,['inventory',key])
    for key,value in (data.get('equipment') or {}).items():visit(value,['equipment',key])
    labels={'surgical_uses':'зарядов хирургического набора','morphine_ampoules':'ампул Морфия','emotion_suppressor_ampoules':'ампул Подавителя эмоций'}
    for key,amount in remaining.items():
        if amount>0:raise ValidationError(f'Не хватает {labels[key]}: {amount}')
    return result


def start(doctor_character,user_id,payload):
    from app.services.character_interaction import CharacterInteractionService
    from app.services.combat import CombatService
    from app.services.magazine import inventory_slot
    from app.services.medical_procedure import _consume_uses
    patient_id=payload.get('target_character_id',doctor_character.id)
    patient_character=db.session.get(LobbyCharacter,patient_id) if type(patient_id) is int else None
    if not patient_character or patient_character.lobby_id!=doctor_character.lobby_id:raise NotFoundError('Пациент не найден')
    if busy(doctor_character.data) or busy(patient_character.data):raise ConflictError('Врач или пациент уже участвует в операции')
    if type(payload.get('target_revision')) is not int or payload['target_revision']!=patient_character.revision:
        raise ConflictError('Данные пациента изменились. Осмотрите его заново.')
    doctors=LocationCharacter.query.filter_by(character_id=doctor_character.id).all()
    if len(doctors)!=1:raise ValidationError('Для операции врач должен находиться на одной подлокации')
    doctor=doctors[0];patient=LocationCharacter.query.filter_by(character_id=patient_id,location_id=doctor.location_id).first()
    if not patient:raise NotFoundError('Пациент не найден на подлокации')
    _validate_pair(doctor,patient)
    consent=None
    if doctor.id!=patient.id:
        is_gm=CombatService._ensure_access(doctor.location,user_id)
        if not is_gm and CombatService._location_character_condition(patient)['state']=='active':
            consent=CharacterInteractionService.validate_treatment(payload.get('interaction_request_id'),doctor,patient)
            if consent.actor_user_id!=user_id:raise ValidationError('Согласие выдано другому врачу')
        elif payload.get('interaction_request_id'):
            consent=CharacterInteractionService.validate_treatment(payload['interaction_request_id'],doctor,patient)
        elif not is_gm:
            CombatService._validate_incapacitated_interaction(doctor.location_id,user_id,doctor.id,patient_id)
    operation=payload['operation'];zone=payload.get('zone');data=deepcopy(doctor_character.data or {})
    target_data=data if doctor_character.id==patient_id else deepcopy(patient_character.data or {})
    source=CombatService._inventory_item_at_path(data,payload.get('path'))
    rule=_validate_item(target_data,zone,source,operation);_validate_doctor(data,rule)
    operation_id=payload.get('operation_id')
    if not isinstance(operation_id,str) or not 1<=len(operation_id)<=120:raise ValidationError('Нужен идентификатор операции')
    if db.session.get(DeferredCombatAction,operation_id):raise ConflictError('Эта операция уже принята')
    supplies={key:rule['installation'][key] for key in ('surgical_uses','morphine_ampoules','emotion_suppressor_ampoules')}
    # Копия проверяется целиком; отказ не расходует ни один препарат.
    planned=_supplies(data,supplies,doctor_character.lobby_id)
    source=CombatService._take_inventory_item(data,payload['path'],1)
    source.setdefault('id',str(uuid4()));source['quantity']=1
    # Удаление протеза могло сдвинуть индексы расходников.
    planned=_supplies(data,supplies,doctor_character.lobby_id)
    # Сначала вложенные предметы и последние индексы каждого контейнера.
    for path,amount,template in sorted(planned,key=lambda entry:tuple(str(key).zfill(12) if isinstance(key,int) else key for key in entry[0]),reverse=True):
        parent,index=inventory_slot(data,path);_consume_uses(parent,index,amount,template,actor_data=data)
    details={'operation':operation,'zone':zone,'patient_id':patient_id,'item':deepcopy(source),
        'remaining_seconds':rule['installation']['duration_seconds'],'duration_seconds':rule['installation']['duration_seconds'],
        'consent_id':consent.id if consent else None,'user_id':user_id,'supplies':supplies,
        'difficulty':max(1,rule['installation']['base_difficulty']-sum(CombatService._skill_modifier(data,'skills.other.'+skill) for skill in ('medicine','engineering')))}
    db.session.add(DeferredCombatAction(id=operation_id,location_character_id=doctor.id,
        payload={'action_key':'prosthesis_surgery',**details},label='Операция установки протеза',status='operating',remaining_action_points=0))
    data.setdefault('health',{}).setdefault('combatMeta',{})[DOCTOR_KEY]=operation_id
    data['health']['combatMeta']['prosthesisSurgeryBackup']={'id':operation_id,**deepcopy(details)}
    target_data.setdefault('health',{}).setdefault('combatMeta',{})[PATIENT_KEY]=operation_id
    doctor_character.data=data;flag_modified(doctor_character,'data')
    patient_character.data=target_data;flag_modified(patient_character,'data')
    if consent:
        consent.status='in_progress';consent.payload={**(consent.payload or {}),'prosthesis_surgery_id':operation_id}
        flag_modified(consent,'payload')
    return details


def advance_lobby(lobby,seconds):
    from app.services.combat import CombatService
    from app.services.prostheses import advance
    if not math.isfinite(float(seconds or 0)) or seconds<0:return []
    rows=DeferredCombatAction.query.join(LocationCharacter,LocationCharacter.id==DeferredCombatAction.location_character_id).join(
        LobbyCharacter,LobbyCharacter.id==LocationCharacter.character_id).filter(LobbyCharacter.lobby_id==lobby.id,DeferredCombatAction.status=='operating').all()
    changed=[]
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby.id).all():
        meta=_meta(character);identity=meta.get(DOCTOR_KEY) or meta.get(PATIENT_KEY)
        if not identity or db.session.get(DeferredCombatAction,identity):continue
        data=deepcopy(character.data);meta=data['health']['combatMeta'];backup=meta.get('prosthesisSurgeryBackup') or {}
        if backup.get('id')==identity and backup.get('operation')=='surgery_install':
            data.setdefault('inventory',{}).setdefault('pockets',[]).append(deepcopy(backup['item']))
        for key in (DOCTOR_KEY,PATIENT_KEY,'prosthesisSurgeryBackup'):meta.pop(key,None)
        meta['prosthesisSurgeryResult']={'id':identity,'success':False,'reason':'Место операции или её участник удалён','supplies_lost':True}
        character.data=data;flag_modified(character,'data');changed.append(character)
    for row in rows:
        details=deepcopy(row.payload or {})
        if details.get('action_key')!='prosthesis_surgery':continue
        doctor=db.session.get(LocationCharacter,row.location_character_id)
        patient_character=db.session.get(LobbyCharacter,details['patient_id'])
        patient=LocationCharacter.query.filter_by(character_id=details['patient_id'],location_id=doctor.location_id).first() if doctor else None
        if seconds==0 and not details.get('cancel_requested'):continue
        reason=details.get('cancel_requested')
        try:
            if not doctor or not patient:raise ValidationError('Врач или пациент покинул место операции')
            _validate_pair(doctor,patient)
            _validate_doctor(doctor.character.data,profile(details['item']))
            _validate_item(patient_character.data,details['zone'],details['item'],details['operation'])
        except ValidationError as error:reason=str(error)
        details['remaining_seconds']=max(0,details['remaining_seconds']-seconds)
        if not reason and details['remaining_seconds']>0:
            row.payload=details;flag_modified(row,'payload');continue
        doctor_character=doctor.character if doctor else None
        if not doctor_character:continue
        data=deepcopy(doctor_character.data or {});target_data=data if patient_character and patient_character.id==doctor_character.id else deepcopy(patient_character.data or {}) if patient_character else None
        roll=random.randint(1,20) if not reason else None
        success=not reason and roll>=details['difficulty']
        item=deepcopy(details['item']);rule=profile(item)
        if success:
            parts=target_data.setdefault('health',{}).setdefault('prostheses',{}).setdefault(details['zone'],{})
            item.setdefault('durability',rule['durability']);item.setdefault('energy_seconds',0);item.setdefault('lubrication_elapsed',0)
            item['attributes']={**(item.get('attributes') or {}),'prosthesis':deepcopy(rule)}
            parts['base']=item
            # Остаток большого перевода времени относится уже к установленному протезу.
            extra=max(0,seconds-row.payload['remaining_seconds'])
            if extra:advance({'health':{'prostheses':{details['zone']:{'base':item}}}},extra)
        else:
            data.setdefault('inventory',{}).setdefault('pockets',[]).append(item)
        result={'id':row.id,'success':bool(success),'roll':roll,'difficulty':details['difficulty'],'reason':reason,
            'name':item['name'],'zone':details['zone'],'supplies':details['supplies'],'duration_seconds':details['duration_seconds']}
        data.setdefault('health',{}).setdefault('combatMeta',{}).pop(DOCTOR_KEY,None)
        data['health']['combatMeta'].pop('prosthesisSurgeryBackup',None)
        data['health']['combatMeta']['prosthesisSurgeryResult']=result
        if target_data is not None:
            target_data.setdefault('health',{}).setdefault('combatMeta',{}).pop(PATIENT_KEY,None)
            target_data['health']['combatMeta']['prosthesisSurgeryResult']=result
            sync_weapons(target_data);patient_character.data=target_data;flag_modified(patient_character,'data');changed.append(patient_character)
        sync_weapons(data);doctor_character.data=data;flag_modified(doctor_character,'data');changed.append(doctor_character)
        row.status='cancelled' if reason else 'completed';details['result']=result;row.payload=details;flag_modified(row,'payload')
        consent=db.session.get(CharacterInteractionRequest,details['consent_id']) if details.get('consent_id') else None
        if consent:
            consent.status='cancelled' if reason else 'completed';consent.result=result;consent.resolved_at=datetime.now(timezone.utc)
    return list({character.id:character for character in changed}.values())


def cancel(character,user_id):
    identity=_meta(character).get(DOCTOR_KEY) or _meta(character).get(PATIENT_KEY)
    row=db.session.get(DeferredCombatAction,identity) if identity else None
    if not row and identity:return advance_lobby(character.lobby,0)
    if not row or row.status!='operating':raise ConflictError('Операция не выполняется')
    row.payload={**row.payload,'cancel_requested':'Операция отменена участником'};flag_modified(row,'payload')
    return advance_lobby(character.lobby,0)


def participants(character):
    identity=_meta(character).get(DOCTOR_KEY) or _meta(character).get(PATIENT_KEY)
    row=db.session.get(DeferredCombatAction,identity) if identity else None
    actor=db.session.get(LocationCharacter,row.location_character_id) if row else None
    return [actor.character_id,row.payload['patient_id']] if actor else []


def interrupt_location(location):
    """Начало боя отменяет операцию сразу, даже без перевода мирового времени."""
    present={character.character_id for character in LocationCharacter.query.filter_by(location_id=location.id).all()}
    found=False
    for row in DeferredCombatAction.query.filter_by(status='operating').all():
        details=row.payload or {}
        if details.get('action_key')!='prosthesis_surgery':continue
        doctor=db.session.get(LocationCharacter,row.location_character_id)
        if doctor and (doctor.location_id==location.id or details.get('patient_id') in present):
            row.payload={**details,'cancel_requested':'Операция прервана началом боя'};flag_modified(row,'payload');found=True
    return advance_lobby(location.lobby,0) if found else []
