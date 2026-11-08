"""Особые свойства: личное время ношения и общий запас применений физического артефакта."""
from copy import deepcopy
from hashlib import sha256
import json
import math
from types import SimpleNamespace

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.transaction import atomic_operation

STATE_KEY = 'artifactSpecial'
DAY = 86400
HEALING = {'Ядро твари': (50, 10), 'Звезда гнили': (100, 5)}


def _number(value):
    try:
        value = float(value)
        return max(0, value) if math.isfinite(value) else 0
    except (TypeError, ValueError):
        return 0


def _equipped(data):
    from app.services.artifact_effects import equipped_artifacts
    return [item for item in equipped_artifacts(data)
            if item.get('id') and (item.get('attributes') or {}).get('special_property') not in (None, '', 'Нет')]


def preserve(current, incoming):
    """Прогресс принадлежит листу носителя; перенос предмета его не копирует."""
    if STATE_KEY in (current or {}):
        incoming[STATE_KEY] = deepcopy(current[STATE_KEY])
    else:
        incoming.pop(STATE_KEY, None)


def advance(data, seconds):
    seconds = _number(seconds)
    if not seconds:
        return
    equipped = _equipped(data)
    if not equipped and STATE_KEY not in data:
        return
    ledger = data.setdefault(STATE_KEY, {}).setdefault('items', {})
    for state in ledger.values():
        remaining = _number(state.get('cooldown_seconds'))
        if remaining:
            state['cooldown_seconds'] = max(0, remaining - seconds)
            if not state['cooldown_seconds']:
                state['used'] = 0
    for identity in {str(item['id']) for item in equipped}:
        state = ledger.setdefault(identity, {})
        state['worn_seconds'] = min(2 * DAY, _number(state.get('worn_seconds')) + seconds)


def status(data, item):
    from app.services.combat import CombatService
    level = CombatService._skill_value(data, 'skills.other.survival')
    capacity = 2 if level >= 15 else 1 if level >= 9 else 0
    required = DAY if level >= 15 else 2 * DAY
    state = ((data.get(STATE_KEY) or {}).get('items') or {}).get(str(item.get('id')), {})
    worn = _number(state.get('worn_seconds'))
    used = _number(state.get('used'))
    cooldown = _number(state.get('cooldown_seconds'))
    supported = item.get('name') in HEALING
    reason = None
    if not capacity:
        reason = 'Нужно Выживание не ниже 9'
    elif worn < required:
        reason = f'Ношение: осталось {math.ceil((required - worn) / 3600)} ч'
    elif used >= capacity:
        reason = f'Перезарядка: осталось {math.ceil(cooldown / 3600)} ч'
    elif not supported:
        reason = 'Применение этого свойства ещё не реализовано'
    elif _number((data.get('health') or {}).get('radiation')) <= 0:
        reason = 'Нет радиации для восстановления здоровья'
    return {'item_id': item.get('id'), 'name': item.get('name'), 'supported': supported,
            'worn_seconds': worn, 'required_seconds': required, 'cooldown_seconds': cooldown,
            'capacity': capacity, 'remaining_uses': max(0, capacity - used),
            'action_points': 1 if level >= 15 else 2, 'available': reason is None, 'reason': reason}


def snapshot(data):
    return [status(data, item) for item in _equipped(data)]


def _identity(payload):
    identity = payload.get('operation_id')
    if not isinstance(identity, str) or not 1 <= len(identity) <= 100:
        raise ValidationError('Нужен идентификатор применения артефакта')
    signature = sha256(json.dumps({'item_id': payload.get('item_id')}, sort_keys=True).encode()).hexdigest()
    return identity, signature


def prepare(actor, data, payload, user_id):
    if not isinstance(payload, dict):
        raise ValidationError('Выберите артефакт')
    identity, signature = _identity(payload)
    prior = next((row for row in (data.get(STATE_KEY) or {}).get('receipts', []) if row['id'] == identity), None)
    if prior:
        if prior['signature'] != signature:
            raise ConflictError('Идентификатор применения уже использован')
        return {'prior': prior, 'action_points': 0}
    items = [item for item in _equipped(data) if item['id'] == payload.get('item_id')]
    if len(items) != 1 or items[0].get('quantity', 1) != 1:
        raise ValidationError('Выберите один активный артефакт на броне или поясе')
    item = items[0]
    state = status(data, item)
    if not state['available']:
        raise ValidationError(state['reason'])
    if _number((data.get('health') or {}).get('radiation')) <= 0:
        raise ValidationError('Нет радиации для восстановления здоровья')
    return {'item': item, 'state': state, 'action_points': state['action_points']}


def execute(actor, data, payload, user_id, combat=None):
    plan = prepare(actor, data, payload, user_id)
    if plan.get('prior'):
        return {**deepcopy(plan['prior']['result']), 'replayed': True, 'action_points': 0}
    from app.services.artifact_effects import healing_amount
    from app.services.effects import sync_health_derived_statuses
    item = plan['item']
    limit, rate = HEALING[item['name']]
    health = data.setdefault('health', {})
    radiation = min(limit, _number(health.get('radiation')))
    before = _number(health.get('current'))
    health['radiation'] = _number(health.get('radiation')) - radiation
    health['current'] = min(_number(health.get('max', 700)), before + healing_amount(data, radiation * rate))
    sync_health_derived_statuses(health)
    ledger = data.setdefault(STATE_KEY, {})
    progress = ledger.setdefault('items', {}).setdefault(str(item['id']), {})
    if not _number(progress.get('cooldown_seconds')):
        progress['cooldown_seconds'] = DAY
    progress['used'] = _number(progress.get('used')) + 1
    result = {'name': f"Особое свойство: {item['name']}", 'item_id': item['id'], 'radiation_removed': radiation,
              'healed': health['current'] - before, 'action_points': plan['action_points'] if combat else 0,
              'roll_required': False, 'replayed': False}
    identity, signature = _identity(payload)
    ledger['receipts'] = [*ledger.get('receipts', [])[-127:],
                          {'id': identity, 'signature': signature, 'result': deepcopy(result)}]
    return result


@atomic_operation
def operate(character_id, user_id, payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    from app.services.item_controllers import _context
    character = db.session.get(LobbyCharacter, character_id)
    if not character:
        raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character, user_id, edit=True)
    data = deepcopy(character.data or {})
    plan = prepare(None, data, payload, user_id)
    if plan.get('prior'):
        return character, {**deepcopy(plan['prior']['result']), 'replayed': True, 'action_points': 0}
    active = LocationCharacter.query.join(LocationCombatState, LocationCombatState.location_id == LocationCharacter.location_id).filter(
        LocationCharacter.character_id == character_id, LocationCombatState.status == 'active').first()
    if active:
        raise ValidationError('В бою используйте действие артефакта с оплатой ОД')
    if CombatService._character_condition(data)['state'] != 'active':
        raise ValidationError('Персонаж не может действовать')
    if payload.get('character_revision') != character.revision:
        raise ConflictError('Лист изменился')
    actor = _context(character, payload.get('location_id')) or SimpleNamespace(character=character)
    result = execute(actor, data, payload, user_id)
    character.data = data
    flag_modified(character, 'data')
    return character, result
