"""Повторно применяемые пулы добычи: настройки ГМа, предметы из общего каталога."""
from copy import deepcopy
import random

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import Lobby, LobbyCharacter, LocationCharacter, LocationCombatState
from app.models.templates import ItemTemplate
from app.services.character_roles import is_npc
from app.services.character_starting_purchase import item_from_template
from app.services.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from app.services.trade import money, balance
from app.services.transaction import atomic_operation

EXCLUDED = {'background', 'special_trait', 'modification'}


def preserve(previous, incoming):
    if 'npcLoot' in (previous or {}):
        incoming['npcLoot'] = deepcopy(previous['npcLoot'])
    else:
        incoming.pop('npcLoot', None)


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f'{label}: допустимо целое число от {minimum} до {maximum}')
    return value


def validate_profile(profile):
    required = {'name', 'budget', 'money_min', 'money_max', 'pools'}
    if not isinstance(profile, dict) or set(profile) != required:
        raise ValidationError('Некорректный профиль добычи')
    if not isinstance(profile['name'], str) or not 1 <= len(profile['name'].strip()) <= 100:
        raise ValidationError('Укажите название профиля добычи')
    result = {**deepcopy(profile), 'name': profile['name'].strip(), 'budget': money(profile['budget'])}
    for key in ('money_min', 'money_max'):
        result[key] = money(profile[key])
    if result['money_min'] > result['money_max']:
        raise ValidationError('Минимум денег не может превышать максимум')
    if not isinstance(profile['pools'], list) or not 1 <= len(profile['pools']) <= 30:
        raise ValidationError('Нужно от 1 до 30 пулов добычи')
    for pool in result['pools']:
        if not isinstance(pool, dict) or set(pool) != {'category', 'subcategory', 'item_class', 'min_price', 'max_price', 'draws', 'quantity', 'chance'}:
            raise ValidationError('Некорректные параметры пула')
        for key in ('category', 'subcategory', 'item_class'):
            if not isinstance(pool[key], str) or len(pool[key]) > 100:
                raise ValidationError('Некорректный фильтр пула')
        if not pool['category'] or pool['category'] in EXCLUDED:
            raise ValidationError('Выберите категорию вещей')
        for key in ('min_price', 'max_price'):
            pool[key] = money(pool[key])
        if pool['min_price'] > pool['max_price']:
            raise ValidationError('Минимальная цена не может превышать максимальную')
        _integer(pool['draws'], 1, 50, 'Число выборов')
        _integer(pool['quantity'], 1, 1000, 'Размер стопки')
        _integer(pool['chance'], 0, 100, 'Шанс')
    if sum(pool['draws'] for pool in result['pools']) > 200:
        raise ValidationError('Допустимо не больше 200 выборов за наполнение')
    return result


def candidates(templates, pool):
    return [template for template in templates
            if template.category == pool['category']
            and not (template.attributes or {}).get('not_for_sale')
            and (not pool['subcategory'] or template.subcategory == pool['subcategory'])
            and (not pool['item_class'] or template.item_class == pool['item_class'])
            and type(template.price) is int and pool['min_price'] <= template.price <= pool['max_price']]


def roll_loot(profile, templates, *, rng=None):
    profile = validate_profile(profile)
    rng = rng or random.SystemRandom()
    items, receipt, skipped, spent = [], [], [], 0
    for index, pool in enumerate(profile['pools']):
        possible = candidates(templates, pool)
        if not possible and pool['chance']:
            raise ValidationError(f'Пул {index + 1}: нет предметов с выбранными фильтрами')
        for _ in range(pool['draws']):
            if rng.randint(1, 100) > pool['chance']:
                continue
            affordable = [template for template in possible
                          if template.price * pool['quantity'] <= profile['budget'] - spent]
            if not affordable:
                skipped.append(index + 1)
                continue
            template = rng.choice(affordable)
            quantity = pool['quantity']
            # Отдельные экземпляры имеют собственный износ, содержимое и заряды.
            separate = template.category in {'weapon', 'melee_weapon', 'armor', 'helmet', 'gas_mask', 'backpack', 'vest', 'container'}
            if separate and quantity > 50:
                raise ValidationError('Для снаряжения с собственным состоянием допустимо до 50 экземпляров за выбор')
            items.extend(item_from_template(template, 1 if separate else quantity) for _ in range(quantity if separate else 1))
            if len(items) > 1000:
                raise ValidationError('Слишком много отдельных экземпляров: уменьшите число выборов')
            spent += template.price * quantity
            receipt.append({'templateId': template.id, 'name': template.name, 'quantity': quantity, 'unitPrice': template.price, 'pool': index + 1})
    return items, {'items': receipt, 'spent': spent, 'budget': profile['budget'],
                   'money': rng.randint(profile['money_min'], profile['money_max']), 'skipped_pools': sorted(set(skipped))}


def apply_rolled_loot(data, items, receipt):
    """Добавить результат розыгрыша в лист без повторного выбора предметов."""
    inventory = data.setdefault('inventory', {})
    inventory['money'] = money(balance(data) + receipt['money'], label='Итоговые деньги НПС')
    for item in items:
        if item['category'] in {'weapon', 'melee_weapon'}:
            data.setdefault('weapons', []).append(item)
        else:
            inventory.setdefault('backpack', []).append(item)


def _character(lobby_id, character_id, user_id):
    lobby = db.session.get(Lobby, lobby_id)
    if not lobby or lobby.gm_id != user_id:
        raise PermissionDenied('Пулы добычи настраивает и применяет только ГМ')
    character = db.session.get(LobbyCharacter, character_id)
    if not character or character.lobby_id != lobby_id:
        raise NotFoundError('Персонаж не найден в этой комнате')
    if not is_npc(character):
        raise ValidationError('Пулы добычи предназначены для НПС')
    return character


def _revision(character, payload):
    if type(payload.get('character_revision')) is not int or payload['character_revision'] != character.revision:
        raise ConflictError('Лист НПС изменился. Откройте окно заново.')


@atomic_operation
def configure(lobby_id, character_id, user_id, payload):
    character = _character(lobby_id, character_id, user_id)
    if not isinstance(payload, dict) or set(payload) != {'character_revision', 'profile'}:
        raise ValidationError('Некорректное сохранение пула добычи')
    _revision(character, payload)
    profile = validate_profile(payload['profile'])
    templates = ItemTemplate.query.order_by(ItemTemplate.id).all()
    for index, pool in enumerate(profile['pools']):
        if pool['chance'] and not candidates(templates, pool):
            raise ValidationError(f'Пул {index + 1}: нет предметов с выбранными фильтрами')
    data = deepcopy(character.data or {})
    data.setdefault('npcLoot', {})['profile'] = profile
    character.data = data
    flag_modified(character, 'data')
    db.session.flush()
    return character


@atomic_operation
def generate(lobby_id, character_id, user_id, payload):
    character = _character(lobby_id, character_id, user_id)
    if not isinstance(payload, dict) or set(payload) != {'character_revision', 'operation_id'}:
        raise ValidationError('Некорректный запрос наполнения')
    operation = payload['operation_id']
    if not isinstance(operation, str) or not 1 <= len(operation) <= 100:
        raise ValidationError('Нужен идентификатор наполнения')
    data = deepcopy(character.data or {})
    config = data.get('npcLoot') or {}
    previous = next((entry for entry in config.get('receipts', []) if entry['operation_id'] == operation), None)
    if previous:
        return character, deepcopy(previous)
    _revision(character, payload)
    active = db.session.query(LocationCharacter.id).join(LocationCombatState, LocationCombatState.location_id == LocationCharacter.location_id).filter(
        LocationCharacter.character_id == character.id, LocationCombatState.status == 'active').first()
    if active:
        raise ValidationError('Наполнять инвентарь НПС можно вне активного боя')
    items, receipt = roll_loot(config.get('profile'), ItemTemplate.query.order_by(ItemTemplate.id).all())
    apply_rolled_loot(data, items, receipt)
    receipt['operation_id'] = operation
    config['receipts'] = [*config.get('receipts', []), receipt][-50:]
    data['npcLoot'] = config
    character.data = data
    flag_modified(character, 'data')
    db.session.flush()
    return character, deepcopy(receipt)
