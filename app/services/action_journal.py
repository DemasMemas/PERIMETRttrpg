"""Личный серверный журнал последних действий; результаты боя не пересчитываются."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
from inspect import signature
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db, socketio
from app.models import LobbyCharacter, LocationCharacter, DeferredCombatAction, ChatMessage
from app.services.exceptions import ServiceError

LIMIT = 100
STATUSES = {'paying':'Ожидает доплаты', 'ready':'Оплачено, ожидает исполнения',
            'completed':'Исполнено', 'cancelled':'Отменено', 'rejected':'Отклонено'}
REASONS={'Unknown action':'Неизвестное действие','Not enough action points':'Недостаточно ОД',
    'Draw this weapon first':'Сначала возьмите оружие в руки','Weapon not found':'Оружие не найдено',
    'The target is outside the character\'s field of view':'Цель вне поля зрения',
    'Target character not found':'Цель не найдена','It is not this character\'s turn':'Сейчас ход другого персонажа',
    'Invalid action point cost':'Неверная стоимость действия','Access denied':'Нет доступа',
    'The weapon is not loaded':'Оружие не заряжено','Clear the weapon jam before firing':'Сначала устраните клин',
    'Cycle the weapon action before firing':'Сначала досылайте патрон или передёрните затвор'}


def preserve(current, incoming):
    old = (((current or {}).get('health') or {}).get('combatMeta') or {})
    meta = incoming.setdefault('health', {}).setdefault('combatMeta', {})
    if 'actionJournal' in old:
        meta['actionJournal'] = deepcopy(old['actionJournal'])
    else:
        meta.pop('actionJournal', None)


def entries(character):
    """Статус долга берётся из серверной записи, включая отмену на новом ходу."""
    data = character.data or {}
    result = deepcopy(((data.get('health') or {}).get('combatMeta') or {}).get('actionJournal') or [])
    for entry in result:
        if entry.get('deferred_id'):
            row = db.session.get(DeferredCombatAction, entry['deferred_id'])
            owner = db.session.get(LocationCharacter, row.location_character_id) if row else None
            if owner and owner.character_id == character.id:
                entry['status'] = row.status
                entry['remaining_action_points'] = row.remaining_action_points
                entry['action_points'] = max(0, entry.get('total_action_points', 0) - row.remaining_action_points)
        entry['status_label'] = STATUSES.get(entry['status'], entry['status'])
        entry['character_id'] = character.id
        entry['character_name'] = character.name
    return result


def record(actor, values, before, result=None, error=None):
    from app.services.combat import ACTION_CATALOG
    result = result if isinstance(result, dict) else getattr(result,'result',None)
    data = deepcopy(actor.character.data or {})
    meta = data.setdefault('health', {}).setdefault('combatMeta', {})
    history = meta.setdefault('actionJournal', [])
    payload=values.get('payload') if isinstance(values.get('payload'),dict) else {}
    action_id = values.get('resume_pending_action_id') or values.get('pending_action_id') or payload.get('deferred_action_id') or payload.get('operation_id')
    existing = next((item for item in history if action_id and item['id'] == action_id), None)
    # Повторное исполнение уже принятого идентификатора не создаёт второй исход.
    if existing and error:
        return
    pending = bool(result and result.get('pending_action'))
    key = values.get('action_key') or ('move' if 'new_x' in values else values.get('_journal_kind', 'action'))
    label = next((item['label'] for item in ACTION_CATALOG if item['key'] == key),
                 {'move':'Перемещение','end_turn':'Конец хода','end_combat':'Конец боя',
                  'weapon_grip':'Смена хвата','consumable_use':'Применение предмета','medical_procedure':'Медицинская процедура',
                  'spend_resources':'Расход ресурсов','portable_light':'Фонарь','body_transport':'Переноска'}.get(key, 'Действие'))
    row = db.session.get(DeferredCombatAction, str(action_id)) if action_id else None
    paid = max(0, before[0] - (actor.action_points_current or 0)) if key not in {'end_turn','end_combat'} else 0
    previous_total=existing.get('total_action_points') if existing and existing.get('status')!='rejected' else None
    entry = existing or {'id':action_id or str(uuid4()), 'timestamp':datetime.now(timezone.utc).isoformat()}
    entry.update({'action':key, 'label':label, 'location_id':actor.location_id,
        'status':'rejected' if error else 'paying' if pending else 'completed',
        'action_points':paid, 'movement_points':max(0, round(before[1] - (actor.movement_points_current or 0), 3)),
        'free_actions':max(0, before[2] - (actor.free_actions_current or 0))})
    if key in {'end_turn','end_combat'}:
        entry['movement_points']=entry['free_actions']=0
    if row:
        entry['deferred_id'] = row.id
        entry['total_action_points'] = previous_total if previous_total is not None else paid + row.remaining_action_points
        entry['action_points'] = max(0,entry['total_action_points']-row.remaining_action_points)
    if error:
        entry['reason'] = REASONS.get(str(error),str(error))[:300]
        entry['action_points'] = entry['movement_points'] = entry['free_actions'] = 0
    else:
        entry.pop('reason',None)
    if result:
        from app.services.combat import CombatService
        summaries = [CombatService.format_attack_summary(result)]
        if key in {'medical_procedure','consumable_use'}:
            source=(payload.get('source') or {}).get('snapshot') or {}
            item_name=result.get('item_name') or source.get('name')
            if item_name:summaries.append(str(item_name))
            if result.get('roll') is not None:
                summaries.append(f"d20 {result['roll']} против СЛ {result.get('difficulty','—')}: "
                    +('успех' if result.get('success') else 'провал'))
        narrative=result.get('narrative_action') or {}
        if narrative.get('name'):
            if key=='narrative_action':entry['label']=narrative['name']
            else:summaries.append(narrative['name'])
        check=narrative.get('check')
        if isinstance(check,dict):
            summaries.append(f"d20 {check.get('roll','—')}, итог {check.get('total','—')} против СЛ {check.get('difficulty','—')}: "
                +('успех' if check.get('success') else 'провал'))
        entry['outcome'] = '\n'.join(text for text in summaries if text)[:4000]
    if not existing:
        history.append(entry)
    meta['actionJournal'] = history[-LIMIT:]
    actor.character.data = data
    flag_modified(actor.character, 'data')
    db.session.commit()
    if error:
        # Отказ тоже меняет ревизию листа из-за записи журнала.
        # Подписчики должны получить её до следующего сохранения/действия.
        from app.services.character_events import emit_character_update
        emit_character_update({'character_id':actor.character_id,
            'updates':{'data':actor.character.data_snapshot()},'source':'action_journal'})
    # Уведомление не содержит имён, координат и результатов скрытого действия.
    socketio.emit('action_journal_updated', {'lobby_id':actor.character.lobby_id},
                  room=f'lobby_{actor.character.lobby_id}')


def journaled(operation=None,*,kind=None,actor_key=None):
    if operation is None:return lambda wrapped:journaled(wrapped,kind=kind,actor_key=actor_key)
    call_signature = signature(operation)

    @wraps(operation)
    def run(*args, **kwargs):
        from app.services.combat import CombatService
        values = dict(call_signature.bind(*args, **kwargs).arguments)
        values['_journal_kind'] = kind or operation.__name__
        actor_id = values.get(actor_key or 'location_character_id')
        actor = db.session.get(LocationCharacter, actor_id) if actor_id else None
        character_id=values.get('character_id') or values.get('actor_id')
        if not actor and character_id:
            query=LocationCharacter.query.filter_by(character_id=character_id)
            if values.get('location_id'):query=query.filter_by(location_id=values['location_id'])
            placed=query.all()
            actor=placed[0] if len(placed)==1 else None
        if not actor and operation.__name__ in {'end_turn','end_combat'}:
            from app.models import LocationCombatState
            state = LocationCombatState.query.filter_by(location_id=values['location_id']).first()
            actor = db.session.get(LocationCharacter, state.current_location_character_id) if state and state.current_location_character_id else None
        if not actor:
            return operation(*args, **kwargs)
        try:
            is_gm = CombatService._ensure_access(actor.location, values['user_id'])
            allowed = CombatService._can_end_turn_for_character(actor, values['user_id'], is_gm=is_gm)
        except ServiceError:
            allowed = False
        if not allowed:
            return operation(*args, **kwargs)
        before = (actor.action_points_current or 0, actor.movement_points_current or 0, actor.free_actions_current or 0)
        try:
            result = operation(*args, **kwargs)
        except ServiceError as error:
            db.session.rollback()
            record(actor, values, before, error=error)
            raise
        record(actor, values, before, result=result)
        return result
    return run


def read(lobby_id, user_id):
    from app.services.character import CharacterService
    result = []
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby_id).all():
        try:
            CharacterService.check_access(character, user_id)
        except ServiceError:
            continue
        result.extend(entries(character))
    from sqlalchemy import or_
    rolls=ChatMessage.query.filter(ChatMessage.lobby_id==lobby_id,or_(ChatMessage.username=='System (Roll)',
        ChatMessage.message.startswith('/roll '),ChatMessage.message.startswith('🎲'))).order_by(ChatMessage.id.desc()).limit(200).all()
    for roll in rolls:
        result.append({'id':f'chat:{roll.id}','timestamp':roll.timestamp.replace(tzinfo=timezone.utc).isoformat(),
            'character_id':0,'character_name':roll.username,'label':'Бросок','status':'completed',
            'status_label':'Исполнено','outcome':roll.message,'kind':'roll'})
    return sorted(result, key=lambda entry:entry['timestamp'])[-200:]
