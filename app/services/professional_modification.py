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
from app.services.crafting import modification_target_plan, modification_targets
from app.services.equipment_modifications import install, make_record, slots as item_slots
from app.services.exceptions import ConflictError, ValidationError
from app.services.professional_repair import markup_value
from app.services.trade import balance, money
from app.services.transaction import atomic_operation


def _prepare(location_id, user_id, payload):
    if not isinstance(payload, dict) or set(payload) - {
        'actor_location_character_id', 'specialist_character_id', 'target_path', 'item_id',
        'rule_id', 'replace_rule_id', 'actor_revision', 'specialist_revision', 'operation_id', 'execute', 'action',
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
    settings = (specialist.character.data or {}).get('merchant') or {}
    if payload.get('action') == 'unlock_slot':
        if payload.get('rule_id') is not None or payload.get('replace_rule_id') is not None:
            raise ValidationError('Для открытия слота не выбирают модификацию')
        if settings.get('repair_enabled') is not True:
            raise ValidationError('Открыть слот можно только у НПС-механика')
        target = next((entry['item'] for entry in modification_targets(actor.character.data or {})
                       if entry['path'] == path), None)
        if not target or not isinstance(payload.get('item_id'), str) or target.get('id') != payload['item_id']:
            raise ConflictError('Предмет перемещён. Откройте услугу заново.')
        from app.services.workbenches import for_item, require
        require(settings,for_item(target.get('category') or ('weapon' if target.get('model') else '')))
        if type(target.get('quantity', 1)) is not int or target.get('quantity', 1) != 1:
            raise ValidationError('Перед открытием слота разделите стопку')
        status = item_slots(target)
        if status['available'] >= status['maximum']:
            raise ValidationError('Все слоты уже открыты')
        return actor, specialist, {'action': 'unlock_slot', 'item_name': target.get('name') or target.get('model'),
            'target_path': path, 'price': 2500, 'slots': status}
    if payload.get('action') == 'install_pda':
        from app.services.pda_modifications import GROUPS, rule_by_id, target as pda_target
        from app.services.workbenches import require
        if settings.get('modification_enabled') is not True:
            raise ValidationError('Этот НПС не устанавливает модификации КПК')
        require(settings, 'Станок Электрика')
        device = pda_target(actor.character.data or {}, {'target_path': path, 'item_id': payload.get('item_id')})
        rule = rule_by_id(payload.get('rule_id'))
        if any(m.get('ruleId') == rule['id'] for m in device.get('modifications') or [] if isinstance(m, dict)):
            raise ValidationError('Модификация уже установлена на этот КПК')
        difficulty, equivalent, _ = GROUPS[rule['difficulty_group']]
        price = money(int(Decimal(equivalent) * (Decimal(100 + difficulty) / 100)), label='Цена установки')
        return actor, specialist, {'action': 'install_pda', 'rule_id': rule['id'], 'rule_name': rule['name'],
            'item_name': device['name'], 'target_path': path, 'price': price,
            'previous': [m.get('ruleId') for m in device.get('modifications') or [] if isinstance(m, dict) and m.get('ruleId')]}
    if payload.get('action') not in {None, 'install'}:
        raise ValidationError('Неизвестная услуга специалиста')
    common = modification_target_plan(actor.character.data or {}, payload)
    target, rule, slots = common['target'], common['rule'], common['slots']
    if payload.get('item_id') is not None and target.get('id') != payload['item_id']:
        raise ConflictError('Предмет перемещён. Откройте установку заново.')
    if type(target.get('quantity', 1)) is not int or target.get('quantity', 1) != 1:
        raise ValidationError('Перед установкой разделите стопку')
    scientist = rule.get('scientist_only') is True
    if settings.get('scientist_enabled' if scientist else 'modification_enabled') is not True:
        raise ValidationError('Этот НПС не устанавливает выбранные модификации')
    from app.services.workbenches import require
    require(settings,'Станок Электрика' if scientist else
            'Станок Оружейника' if rule['kind']=='weapon' else 'Станок Бронника')
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
        raise ValidationError('Не хватает денег на услугу')
    if plan.get('action') == 'unlock_slot':
        target = next(entry['item'] for entry in modification_targets(actor_data)
                      if entry['path'] == plan['target_path'])
        target['unlockedModificationSlots'] = int(target.get('unlockedModificationSlots') or 0) + 1
        label = 'Открытие слота у механика'
        outcome = f"{plan['item_name']}: открыт слот, {plan['price']} ₽"
    elif plan.get('action') == 'install_pda':
        from app.services.pda_modifications import record, rule_by_id, target as pda_target
        from app.services.equipment_modifications import installed
        target = pda_target(actor_data, {'target_path': plan['target_path'], 'item_id': payload.get('item_id')})
        if [m['ruleId'] for m in installed(target)] != plan['previous']:
            raise ConflictError('Модификации КПК изменились')
        install(target, record(rule_by_id(plan['rule_id']), specialist.character_id))
        label = 'Установка модификации КПК у электрика'
        outcome = f"{plan['item_name']}: {plan['rule_name']}, {plan['price']} ₽"
    else:
        common = modification_target_plan(actor_data, payload)
        target = common['target']
        from app.services.character_traits import background_is
        record = make_record(plan['rule_id'], gunsmith=background_is(specialist_data, 'Оружейник'),
                             installer_id=specialist.character_id)
        install(target, record, plan['replace_rule_id'])
        label = 'Установка модификации у НПС'
        outcome = f"{plan['item_name']}: {plan['rule_name']}, {plan['price']} ₽"
    actor_data.setdefault('inventory', {})['money'] = balance(actor_data) - plan['price']
    specialist_data.setdefault('inventory', {})['money'] = money(balance(specialist_data) + plan['price'])
    journal = actor_data.setdefault('health', {}).setdefault('combatMeta', {}).setdefault('actionJournal', [])
    journal.append({'id': 'professional-modification:' + operation_id,
        'timestamp': datetime.now(timezone.utc).isoformat(), 'action': 'professional_modification',
        'kind': 'professional_modification', 'label': label,
        'status': 'completed', 'location_id': location_id, 'action_points': 0,
        'movement_points': 0, 'free_actions': 0,
        'outcome': outcome})
    actor_data['health']['combatMeta']['actionJournal'] = journal[-100:]
    actor.character.data, specialist.character.data = actor_data, specialist_data
    flag_modified(actor.character, 'data')
    flag_modified(specialist.character, 'data')
    db.session.flush()
    return {'duplicate': False, 'price': plan['price'], 'action': plan.get('action', 'install'),
        'rule_id': plan.get('rule_id'), 'item_name': plan['item_name'], 'rule_name': plan.get('rule_name'),
        'slots': None if plan.get('action') == 'install_pda' else item_slots(target),
        'actor_character_id': actor.character_id, 'specialist_character_id': specialist.character_id}
