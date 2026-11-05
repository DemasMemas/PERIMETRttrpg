"""Платная установка модификаций при взаимодействии с НПС."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCombatState
from app.services.character_interaction import CharacterInteractionService
from app.services.character_roles import is_npc
from app.services.combat import CombatService
from app.services.crafting import modification_target_plan
from app.services.equipment_modifications import install, make_record
from app.services.exceptions import ConflictError, ValidationError
from app.services.professional_repair import markup_value
from app.services.trade import balance, money
from app.services.transaction import atomic_operation


def _prepare(location_id, user_id, payload):
    if not isinstance(payload, dict) or set(payload) - {
        'actor_location_character_id', 'specialist_character_id', 'target_path', 'item_id',
        'rule_id', 'replace_rule_id', 'actor_revision', 'specialist_revision', 'operation_id', 'execute',
    }:
        raise ValidationError('Некорректные параметры платной установки')
    _, actor, specialist = CharacterInteractionService._pair(location_id, user_id,
        payload.get('actor_location_character_id'), payload.get('specialist_character_id'))
    if not is_npc(specialist.character):
        raise ValidationError('Установку выполняет НПС-специалист')
    if not CombatService._is_adjacent(actor, specialist):
        raise ValidationError('Подойдите к специалисту')
    state = LocationCombatState.query.filter_by(location_id=location_id).first()
    if state and state.status == 'active':
        raise ValidationError('Платная установка доступна только вне боя')
    path = payload.get('target_path')
    if not isinstance(path, list) or not path or path[0] not in {'weapons', 'equipment', 'inventory'}:
        raise ValidationError('Выберите своё оружие или снаряжение')
    common = modification_target_plan(actor.character.data or {}, payload)
    target, rule, slots = common['target'], common['rule'], common['slots']
    if payload.get('item_id') is not None and target.get('id') != payload['item_id']:
        raise ConflictError('Предмет перемещён. Откройте установку заново.')
    if type(target.get('quantity', 1)) is not int or target.get('quantity', 1) != 1:
        raise ValidationError('Перед установкой разделите стопку')
    settings = (specialist.character.data or {}).get('merchant') or {}
    scientist = rule.get('scientist_only') is True
    if settings.get('scientist_enabled' if scientist else 'modification_enabled') is not True:
        raise ValidationError('Этот НПС не устанавливает выбранные модификации')
    factor = slots['factor']
    if type(factor) is not int or factor not in {1, 2, 3, 4, 6}:
        raise ValidationError('Класс предмета не позволяет рассчитать цену установки')
    base = Decimal(1000 if rule['kind'] == 'belt' else 5000 * factor * rule['level'])
    if scientist and rule.get('branch') == 'Физический':
        base *= Decimal('1.5')
    markup = markup_value(settings.get('repair_markup', 1))
    price = money(int((base * markup).to_integral_value(rounding=ROUND_FLOOR)), label='Цена установки')
    return actor, specialist, {'rule_id': rule['id'], 'rule_name': rule.get('name') or rule['description'],
        'item_name': target.get('name') or target.get('model'), 'target_path': path,
        'replace_rule_id': payload.get('replace_rule_id'), 'base_price': int(base),
        'price': price, 'markup': float(markup), 'slots': slots,
        'previous': common['previous']}


def quote(location_id, user_id, payload):
    _, _, plan = _prepare(location_id, user_id, payload)
    return plan


@atomic_operation
def perform(location_id, user_id, payload):
    if type(payload.get('actor_revision')) is not int or type(payload.get('specialist_revision')) is not int:
        raise ValidationError('Обновите листы персонажа и специалиста')
    operation_id = payload.get('operation_id')
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 100:
        raise ValidationError('Укажите идентификатор операции')
    _, actor, _ = CharacterInteractionService._pair(location_id, user_id,
        payload.get('actor_location_character_id'), payload.get('specialist_character_id'))
    journal = (((actor.character.data or {}).get('health') or {}).get('combatMeta') or {}).get('actionJournal') or []
    if any(row.get('id') == 'professional-modification:' + operation_id for row in journal):
        return {'duplicate': True}
    actor, specialist, plan = _prepare(location_id, user_id, payload)
    if actor.character.revision != payload['actor_revision'] or specialist.character.revision != payload['specialist_revision']:
        raise ConflictError('Предмет или деньги изменились. Откройте установку заново.')
    actor_data, specialist_data = deepcopy(actor.character.data or {}), deepcopy(specialist.character.data or {})
    if balance(actor_data) < plan['price']:
        raise ValidationError('Не хватает денег на установку')
    common = modification_target_plan(actor_data, payload)
    target = common['target']
    from app.services.character_traits import background_is
    record = make_record(plan['rule_id'], gunsmith=background_is(specialist_data, 'Оружейник'),
                         installer_id=specialist.character_id)
    install(target, record, plan['replace_rule_id'])
    actor_data.setdefault('inventory', {})['money'] = balance(actor_data) - plan['price']
    specialist_data.setdefault('inventory', {})['money'] = money(balance(specialist_data) + plan['price'])
    journal = actor_data.setdefault('health', {}).setdefault('combatMeta', {}).setdefault('actionJournal', [])
    journal.append({'id': 'professional-modification:' + operation_id,
        'timestamp': datetime.now(timezone.utc).isoformat(), 'action': 'professional_modification',
        'kind': 'professional_modification', 'label': 'Установка модификации у НПС',
        'status': 'completed', 'location_id': location_id, 'action_points': 0,
        'movement_points': 0, 'free_actions': 0,
        'outcome': f"{plan['item_name']}: {plan['rule_name']}, {plan['price']} ₽"})
    actor_data['health']['combatMeta']['actionJournal'] = journal[-100:]
    actor.character.data, specialist.character.data = actor_data, specialist_data
    flag_modified(actor.character, 'data')
    flag_modified(specialist.character, 'data')
    db.session.flush()
    return {'duplicate': False, 'price': plan['price'], 'rule_id': plan['rule_id'],
        'item_name': plan['item_name'], 'rule_name': plan['rule_name'],
        'actor_character_id': actor.character_id, 'specialist_character_id': specialist.character_id}
