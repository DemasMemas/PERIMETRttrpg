"""Проверка предложения обмена до согласия и повторная проверка перед передачей."""
from copy import deepcopy

from app.services.exceptions import ValidationError

MAX_MONEY = 2**53 - 1


def _contains_individual_items(item):
    if any(item.get(key) for key in ('contents','installedModules','installedMagazine','fixedAmmo','ammo','loadedGrenade','trapGrenades')) or (item.get('controllerState') or {}).get('kind'):
        return True
    return any(_contains_individual_items(pouch) for pouch in item.get('pouches') or [] if isinstance(pouch,dict))


def money(value, *, label='Сумма'):
    if type(value) not in {int, float} or not 0 <= value <= MAX_MONEY or int(value) != value:
        raise ValidationError(f'{label}: укажите неотрицательное целое число рублей')
    return int(value)


def balance(data):
    return money((data.get('inventory') or {}).get('money', 0), label='Баланс персонажа')


def prepare_offer(data, offer):
    from app.services.combat import CombatService
    from app.services.character_interaction import CharacterInteractionService
    if not isinstance(offer, list) or len(offer) > 200:
        raise ValidationError('Некорректный список предметов обмена')
    normalized = []
    paths = []
    for entry in offer:
        if not isinstance(entry, dict) or type(entry.get('amount', 1)) is not int or entry.get('amount', 1) < 1:
            raise ValidationError('Количество предметов должно быть положительным целым числом')
        path = CharacterInteractionService._resolve_item_path(data, entry.get('item_id'), entry.get('path'))
        if not isinstance(path, list) or not path or path[0] not in {'inventory', 'equipment'}:
            raise ValidationError('Предмет обмена перемещён. Откройте обмен заново.')
        if any(path[:len(old)] == old or old[:len(path)] == path for old in paths):
            raise ValidationError('Нельзя одновременно передать предмет дважды или передать контейнер и его содержимое')
        paths.append(path)
        actual = CombatService._inventory_item_at_path(data, path)
        available = CombatService._coerce_int(actual.get('quantity'), 1)
        if entry.get('amount', 1) > available:
            raise ValidationError('В стопке недостаточно предметов для предложения')
        if entry.get('amount',1) < available and _contains_individual_items(actual):
            raise ValidationError('Нельзя разделить стопку с общим содержимым или установленными предметами. Сначала освободите или разделите её в инвентаре.')
        item = CombatService._take_inventory_item(deepcopy(data), path, entry.get('amount', 1))
        if entry.get('item_id') is not None and entry['item_id'] != item.get('id'):
            raise ValidationError('Предмет обмена изменился. Откройте обмен заново.')
        normalized.append({'item_id': item.get('id'), 'path': path, 'amount': entry.get('amount', 1),
                           'name': item.get('name') or 'Предмет'})
    return normalized


def prepare_payload(actor, target, payload):
    if not isinstance(payload, dict) or set(payload) - {'actor_offer', 'target_offer', 'actor_money', 'target_money',
                                                       'actor_revision', 'target_revision', 'merchant_mode'}:
        raise ValidationError('Некорректное предложение обмена')
    result = {}
    for key, character in (('actor', actor), ('target', target)):
        revision = payload.get(f'{key}_revision')
        if revision is not None and (type(revision) is not int or revision != character.revision):
            from app.services.exceptions import ConflictError
            raise ConflictError('Снаряжение или деньги изменились. Откройте обмен заново.')
        result[f'{key}_offer'] = prepare_offer(character.data or {}, payload.get(f'{key}_offer', []))
        result[f'{key}_money'] = money(payload.get(f'{key}_money', 0))
    from app.services.npc_trade import enabled, totals
    if type(payload.get('merchant_mode', False)) is not bool:
        raise ValidationError('Некорректный режим торговли')
    if payload.get('merchant_mode'):
        if not enabled(target):
            raise ValidationError('Этот НПС не ведёт автоматическую торговлю')
        if any(type(payload.get(key)) is not int for key in ('actor_revision', 'target_revision')):
            raise ValidationError('Для торговли обновите листы покупателя и торговца')
        result.update(totals(actor.data or {}, target.data or {}, result['actor_offer'], result['target_offer']))
        result['_merchant'] = True
    for key, character in (('actor', actor), ('target', target)):
        if result[f'{key}_money'] > balance(character.data or {}):
            raise ValidationError('У персонажа недостаточно денег для предложения')
    if not result['actor_offer'] and not result['target_offer'] and not result['actor_money'] and not result['target_money']:
        raise ValidationError('Выберите предметы или сумму для передачи')
    result['_expected_revisions'] = {'actor': actor.revision, 'target': target.revision}
    return result


def exchange_money(actor_data, target_data, payload):
    give = money(payload.get('actor_money', 0))
    receive = money(payload.get('target_money', 0))
    actor_balance, target_balance = balance(actor_data), balance(target_data)
    if give > actor_balance or receive > target_balance:
        raise ValidationError('Денег для обмена больше не хватает')
    actor_balance += receive - give
    target_balance += give - receive
    money(actor_balance)
    money(target_balance)
    actor_data.setdefault('inventory', {})['money'] = actor_balance
    target_data.setdefault('inventory', {})['money'] = target_balance
    return {'actor_to_target_money': give, 'target_to_actor_money': receive}
