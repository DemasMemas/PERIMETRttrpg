"""Особые свойства: личное время ношения и общий запас применений физического артефакта."""
from copy import deepcopy
from hashlib import sha256
import json
import math
from types import SimpleNamespace

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
from app.services.artifact_area import AREA_EFFECTS, apply as apply_area
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.transaction import atomic_operation

STATE_KEY = 'artifactSpecial'
DAY = 86400
HEALING = {'Ядро твари': (50, 10), 'Звезда гнили': (100, 5)}
FALL_ARTIFACT = 'Сфера притяжения'
RADIATION_AMPLIFIER_COST = {'Хозяин свечения': 20, 'Реактор': 50}
PREFIX = 'artifact-special:'
BUFFS = {
    'Искровая жилка': (6, {'movement_penalty': -3}),
    'Нервный катализатор': (12, {'movement_penalty': -5, 'skill_strength': 2}),
    'Генератор': (600, {'movement_penalty': -2}),
    'Пылающий осколок': (18, {'movement_penalty': -3, 'physical_protection': 10}),
    'Гнилая железа': (60, {'chemical_protection': 20}),
    'Сердце жара': (6, {'immune_thermal': 1}),
    'Ядро пепелища': (18, {'immune_thermal': 1, 'movement_penalty': -4}),
    'Печенка': (6, {'immune_chemical': 1}),
    'Сердце притяжения': (6, {'immune_physical': 1}),
    'Огненная вена': (18, {'breathless_immunity': 1}),
    'Сердце грозы': (12, {'movement_penalty': -20, 'skill_strength': 5, 'breathless_immunity': 1}),
    'Панцирь': (60, {'armor_wear_immunity': 1}),
    'Живой щит': (60, {'armor_wear_immunity': 1, 'armor_restore_on_hit': 1}),
    'Ментальный бастион': (30, {'directed_psi_immunity': 1}),
    'Щит сознания': (60, {'directed_psi_immunity': 1, 'stress_immunity': 1}),
    'Дар пламени': (18, {'physical_protection': 30}),
    'Чернобыльская губка': (4 * 3600, {'radiation_sickness_immunity': 1}),
    'Хозяин свечения': (60, {'positive_multiplier': 2}),
    'Реактор': (60, {'positive_multiplier': 3}),
    'Ядро мысли': (60, {'positive_multiplier': 2}),
    'Сердце Эха': (60, {'positive_multiplier': 2}),
    'Горелка': (600, {}),
}
STAT_LABELS = {'movement_penalty': 'Штраф перемещения', 'skill_strength': 'Сила',
               'physical_protection': 'Защита от физического урона', 'chemical_protection': 'Защита от химического урона',
               'immune_thermal': 'Игнорирование термического урона', 'immune_chemical': 'Игнорирование химического урона',
               'immune_physical': 'Игнорирование физического урона'}
STAT_LABELS['breathless_immunity'] = 'Бег и спринт без одышки'
STAT_LABELS['armor_wear_immunity'] = 'Броня не теряет прочность'
STAT_LABELS['armor_restore_on_hit'] = 'Восстановление прочности брони от входящего урона'
STAT_LABELS['directed_psi_immunity'] = 'Иммунитет к направленным пси-атакам'
STAT_LABELS['stress_immunity'] = 'Стресс не повышается'
STAT_LABELS['radiation_sickness_immunity'] = 'Иммунитет к последствиям радиации'
STAT_LABELS['positive_multiplier'] = 'Множитель положительных эффектов артефакта ×'


def is_special_effect(effect):
    return isinstance(effect, dict) and str(effect.get('source') or '').startswith(PREFIX)


def _effect_source(item_id, stat):
    return PREFIX + sha256(str(item_id).encode()).hexdigest()[:24] + ':' + stat


def damage_immunity(data, damage_type):
    if not isinstance(damage_type, str) or damage_type not in {'physical', 'thermal', 'chemical'}:
        return False
    return any(is_special_effect(effect) and effect.get('active', True)
               and effect.get('artifact_stat') == 'immune_' + damage_type
               and _number(effect.get('value')) > 0
               and _number(effect.get('remaining_seconds', effect.get('remaining'))) > 0
               for effect in ((data or {}).get('health') or {}).get('effects') or [])


def stat_value(data, stat, *, item_id=None):
    source = _effect_source(item_id, stat) if item_id is not None else None
    return sum(float(effect.get('value') or 0)
               for effect in ((data or {}).get('health') or {}).get('effects') or []
               if is_special_effect(effect) and effect.get('active', True) and effect.get('artifact_stat') == stat
               and (source is None or effect.get('source') == source)
               and _number(effect.get('remaining_seconds', effect.get('remaining'))) > 0
               and type(effect.get('value')) in (int, float) and math.isfinite(effect['value']))


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
    previous = ((current or {}).get('health') or {}).get('effects') or []
    effects = (incoming.get('health') or {}).get('effects') or []
    if any(is_special_effect(effect) for effect in [*previous, *effects]):
        incoming.setdefault('health', {})['effects'] = [effect for effect in effects if not is_special_effect(effect)] + [
            deepcopy(effect) for effect in previous if is_special_effect(effect)]


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
    fall_reaction = item.get('name') == FALL_ARTIFACT
    supported = item.get('name') in HEALING or item.get('name') in BUFFS or item.get('name') in AREA_EFFECTS or fall_reaction
    reason = None
    if not capacity:
        reason = 'Нужно Выживание не ниже 9'
    elif worn < required:
        reason = f'Ношение: осталось {math.ceil((required - worn) / 3600)} ч'
    elif used >= capacity:
        reason = f'Перезарядка: осталось {math.ceil(cooldown / 3600)} ч'
    elif not supported:
        reason = 'Применение этого свойства ещё не реализовано'
    elif item.get('name') in HEALING and _number((data.get('health') or {}).get('radiation')) <= 0:
        reason = 'Нет радиации для восстановления здоровья'
    return {'item_id': item.get('id'), 'name': item.get('name'), 'supported': supported,
            'worn_seconds': worn, 'required_seconds': required, 'cooldown_seconds': cooldown,
            'capacity': capacity, 'remaining_uses': max(0, capacity - used),
            'action_points': 1 if level >= 15 else 2, 'available': reason is None, 'reason': reason,
            'power_cost': 50 if item.get('name') == 'Горелка' else None,
            'fall_reaction': fall_reaction, 'armed': fall_reaction and
            (data.get(STATE_KEY) or {}).get('fall_item_id') == item.get('id')}


def snapshot(data):
    return [status(data, item) for item in _equipped(data)]


def _identity(payload):
    identity = payload.get('operation_id')
    if not isinstance(identity, str) or not 1 <= len(identity) <= 100:
        raise ValidationError('Нужен идентификатор применения артефакта')
    choice = {'item_id': payload.get('item_id')}
    if 'power' in payload:
        choice['power'] = payload['power']
    signature = sha256(json.dumps(choice, sort_keys=True).encode()).hexdigest()
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
    if state['fall_reaction']:
        raise ValidationError('Сфера притяжения применяется в момент падения')
    if not state['available']:
        raise ValidationError(state['reason'])
    if item.get('name') in HEALING and _number((data.get('health') or {}).get('radiation')) <= 0:
        raise ValidationError('Нет радиации для восстановления здоровья')
    plan = {'item': item, 'state': state, 'action_points': state['action_points']}
    if state['power_cost']:
        power = payload.get('power')
        if type(power) is not int or not 1 <= power <= 5:
            raise ValidationError('Выберите снижение штрафа от 1 до 5')
        cost = power * state['power_cost']
        if _number((data.get('health') or {}).get('current')) < cost:
            raise ValidationError(f'Недостаточно ОЗ для применения: требуется {cost}')
        plan.update(power=power, health_cost=cost)
    return plan


def execute(actor, data, payload, user_id, combat=None):
    plan = prepare(actor, data, payload, user_id)
    if plan.get('prior'):
        return {**deepcopy(plan['prior']['result']), 'replayed': True, 'action_points': 0}
    from app.services.artifact_effects import healing_amount
    from app.services.effects import sync_health_derived_statuses
    item = plan['item']
    health = data.setdefault('health', {})
    before = _number(health.get('current'))
    radiation = 0
    affected = []
    if item['name'] in HEALING:
        limit, rate = HEALING[item['name']]
        radiation = min(limit, _number(health.get('radiation')))
        health['radiation'] = _number(health.get('radiation')) - radiation
        health['current'] = min(_number(health.get('max', 700)), before + healing_amount(data, radiation * rate))
    elif item['name'] in AREA_EFFECTS:
        radiation, affected = apply_area(actor, data, item['name'])
    else:
        duration, modifiers = BUFFS[item['name']]
        if plan.get('health_cost'):
            health['current'] = before - plan['health_cost']
            modifiers = {'movement_penalty': -plan['power']}
        duration_label = f'{duration // 3600} ч' if duration >= 3600 and duration % 3600 == 0 else f'{duration} сек.'
        effects = health.setdefault('effects', [])
        for stat, amount in modifiers.items():
            source = _effect_source(item['id'], stat)
            label = STAT_LABELS[stat]
            if stat == 'positive_multiplier':
                label += f'{amount:g}'
            elif not (stat.startswith('immune_') or stat.endswith('_immunity')):
                label += f' {amount:+g}'
            effects[:] = [effect for effect in effects if effect.get('source') != source]
            effects.append({'id': source, 'source': source, 'type': 'custom', 'name': f"{item['name']}: особое свойство",
                            'note': f'{label} на {duration_label}',
                            'artifact_stat': stat, 'value': amount, 'active': True, 'tick': 'time_elapsed',
                            'time_unit': 'second', 'remaining': duration, 'remaining_seconds': duration})
            if item['name'] == 'Сердце грозы' and stat == 'movement_penalty':
                effects[-1]['activate_effects'] = [{'type': 'sleep', 'name': 'Сон после Сердца грозы',
                    'source': source + ':sleep', 'remaining': 10, 'remaining_seconds': 600,
                    'time_unit': 'minute', 'tick': 'time_elapsed'}]
        if item['name'] == 'Огненная вена' and hasattr(actor, 'strenuous_movement_blocked_until_round'):
            actor.strenuous_movement_blocked_until_round = 0
        if item['name'] in RADIATION_AMPLIFIER_COST:
            from app.services.combat import CombatService
            health['radiation'] = _number(health.get('radiation')) + RADIATION_AMPLIFIER_COST[item['name']]
            CombatService._apply_radiation_threshold_states(health)
        if item['name'] in {'Дар пламени', 'Ядро мысли', 'Сердце Эха'}:
            # Каждое применение оплачивается отдельно, даже при обновлении самого бонуса.
            source = _effect_source(f"{item['id']}\0{payload['operation_id']}", 'delayed-cost')
            cost = {'id': source, 'source': source, 'type': 'custom', 'name': f"Цена: {item['name']}",
                    'active': True, 'tick': 'time_elapsed', 'time_unit': 'second',
                    'remaining': duration, 'remaining_seconds': duration}
            if item['name'] == 'Сердце Эха':
                cost.update(note='Через минуту штраф перемещения +3 на 10 минут.', activate_effects=[{
                    'type': 'custom', 'source': _effect_source(item['id'], 'echo-movement-cost'),
                    'name': 'Штраф после Сердца Эха', 'artifact_stat': 'movement_penalty', 'value': 3,
                    'tick': 'time_elapsed', 'time_unit': 'second', 'remaining': 600, 'remaining_seconds': 600}])
            else:
                hp = 250 if item['name'] == 'Дар пламени' else 150
                cost.update(type='delayed_adjustment', note=f'Потеря {hp} ОЗ через {duration_label}',
                            adjustments=[{'field': 'current', 'delta': -hp, 'min': 0}])
            effects.append(cost)
    sync_health_derived_statuses(health)
    ledger = _spend(data, item['id'])
    result = {'name': f"Особое свойство: {item['name']}", 'item_id': item['id'], 'radiation_removed': radiation,
              'healed': max(0, _number(health.get('current')) - before), 'action_points': plan['action_points'] if combat else 0,
              'roll_required': False, 'replayed': False}
    if plan.get('health_cost'):
        result['health_spent'] = plan['health_cost']
    if affected:
        result['_affected_character_ids'] = affected
    identity, signature = _identity(payload)
    ledger['receipts'] = [*ledger.get('receipts', [])[-127:],
                          {'id': identity, 'signature': signature, 'result': deepcopy(result)}]
    return result


def _spend(data, item_id):
    ledger = data.setdefault(STATE_KEY, {})
    progress = ledger.setdefault('items', {}).setdefault(str(item_id), {})
    if not _number(progress.get('cooldown_seconds')):
        progress['cooldown_seconds'] = DAY
    progress['used'] = _number(progress.get('used')) + 1
    return ledger


def _configure_fall(data, payload):
    item_id, armed = payload.get('item_id'), payload.get('arm_fall')
    items = [item for item in _equipped(data) if item['id'] == item_id and item['name'] == FALL_ARTIFACT
             and item.get('quantity', 1) == 1]
    if type(armed) is not bool or len(items) != 1:
        raise ValidationError('Выберите активную Сферу притяжения')
    state = status(data, items[0])
    if armed and not state['available']:
        raise ValidationError(state['reason'])
    ledger = data.setdefault(STATE_KEY, {})
    if armed:
        ledger['fall_item_id'] = item_id
    elif ledger.get('fall_item_id') == item_id:
        ledger.pop('fall_item_id')
    return {'item_id': item_id, 'armed': armed, 'action_points': 0}


def prevent_fall(actor, data):
    item_id = (data.get(STATE_KEY) or {}).get('fall_item_id')
    if item_id is None:
        return False
    items = [item for item in _equipped(data) if item['id'] == item_id and item['name'] == FALL_ARTIFACT
             and item.get('quantity', 1) == 1]
    if len(items) != 1:
        return False
    state = status(data, items[0])
    from app.services.combat import CombatService
    if not state['available'] or CombatService._character_condition(data)['state'] != 'active':
        return False
    combat = LocationCombatState.query.filter_by(location_id=actor.location_id, status='active').first()
    if combat:
        cost = state['action_points']
        if (actor.action_points_current or 0) < cost:
            return False
        actor.action_points_current -= cost
    _spend(data, item_id).pop('fall_item_id')
    flag_modified(actor.character, 'data')
    return True


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
    if 'arm_fall' in payload:
        if payload.get('character_revision') != character.revision:
            raise ConflictError('Лист изменился')
        result = _configure_fall(data, payload)
        character.data = data
        flag_modified(character, 'data')
        return character, result
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
    if plan['item']['name'] in AREA_EFFECTS and not payload.get('location_id'):
        raise ValidationError('Для этого особого свойства нужна подлокация')
    actor = _context(character, payload.get('location_id')) or SimpleNamespace(character=character)
    result = execute(actor, data, payload, user_id)
    character.data = data
    flag_modified(character, 'data')
    return character, result
