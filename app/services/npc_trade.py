"""Торговец с явным прайсом ГМа, конечными товарами и конечным запасом денег."""
from copy import deepcopy
from math import isfinite

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import Lobby, LobbyCharacter
from app.models.templates import ItemTemplate
from app.services.character_roles import is_npc
from app.services.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from app.services.trade import money
from app.services.transaction import atomic_operation


def preserve(previous, incoming):
    # Настройки прайса меняются отдельным действием ГМа, а не автосохранением листа.
    if 'merchant' in (previous or {}):
        incoming['merchant'] = deepcopy(previous['merchant'])
    else:
        incoming.pop('merchant', None)


def enabled(character):
    return is_npc(character) and ((character.data or {}).get('merchant') or {}).get('enabled') is True


def _virtual_templates():
    from app.services.weapon_modules import builtin_templates
    from app.services.portable_lights import missing_templates
    return {entry['id']: entry for entry in [*builtin_templates(), *missing_templates([])]}


@atomic_operation
def configure(lobby_id, character_id, user_id, payload):
    lobby = db.session.get(Lobby, lobby_id)
    if not lobby or lobby.gm_id != user_id:
        raise PermissionDenied('Торговца настраивает только ГМ')
    character = db.session.get(LobbyCharacter, character_id)
    if not character or character.lobby_id != lobby_id:
        raise NotFoundError('Персонаж не найден в этой комнате')
    if not is_npc(character):
        raise ValidationError('Автоматическую торговлю можно включить только для НПС')
    if (not isinstance(payload, dict) or not {'enabled', 'prices', 'character_revision'} <= set(payload)
            or set(payload) - {'enabled', 'prices', 'character_revision', 'money','repair_enabled','repair_markup',
                               'modification_enabled','scientist_enabled','workbenches'}):
        raise ValidationError('Некорректные настройки торговца')
    if type(payload['character_revision']) is not int or payload['character_revision'] != character.revision:
        raise ConflictError('Лист НПС изменился. Откройте настройки заново.')
    if type(payload['enabled']) is not bool or not isinstance(payload['prices'], dict) or len(payload['prices']) > 2000:
        raise ValidationError('Некорректный прайс торговца')
    repair_enabled=payload.get('repair_enabled',((character.data or {}).get('merchant') or {}).get('repair_enabled',False))
    repair_markup=payload.get('repair_markup',((character.data or {}).get('merchant') or {}).get('repair_markup',1))
    modification_enabled=payload.get('modification_enabled',((character.data or {}).get('merchant') or {}).get('modification_enabled',False))
    scientist_enabled=payload.get('scientist_enabled',((character.data or {}).get('merchant') or {}).get('scientist_enabled',False))
    from app.services.workbenches import validate as validate_workbenches
    workbenches=validate_workbenches(payload.get('workbenches',
        ((character.data or {}).get('merchant') or {}).get('workbenches',[])))
    if type(modification_enabled) is not bool or type(scientist_enabled) is not bool:
        raise ValidationError('Настройки платной установки должны быть логическими значениями')
    if type(repair_enabled) is not bool or type(repair_markup) not in {int,float} or not isfinite(repair_markup) or not 0<repair_markup<=10:
        raise ValidationError('Настройки ремонта: укажите множитель услуг механика больше 0 и не выше 10')
    prices = {}
    for key, value in payload['prices'].items():
        try:
            template_id = int(key)
        except (TypeError, ValueError):
            raise ValidationError('Неизвестный товар') from None
        if str(template_id) != str(key) or not template_id:
            raise ValidationError('Неизвестный товар')
        if not isinstance(value, dict) or set(value) != {'buy', 'sell'}:
            raise ValidationError('У товара должны быть цены выкупа и продажи')
        entry = {side: money(amount, label='Цена за единицу') if amount is not None else None
                 for side, amount in value.items()}
        if any(amount is not None for amount in entry.values()):
            prices[str(template_id)] = entry
    positive = [int(key) for key in prices if int(key) > 0]
    templates = {template.id: template for template in ItemTemplate.query.filter(ItemTemplate.id.in_(positive)).all()}
    virtual = _virtual_templates()
    for key in prices:
        template = templates.get(int(key)) if int(key) > 0 else virtual.get(int(key))
        if template is None:
            raise ValidationError('В прайсе есть товар, которого нет в общем каталоге')
        from app.services.equipment_catalog import retired_catalog_template
        if retired_catalog_template(template):
            raise ValidationError('Этот предмет выведен из каталога')
        attrs = template.attributes if isinstance(template, ItemTemplate) else template['attributes']
        category = template.category if isinstance(template, ItemTemplate) else template['category']
        if category in {'background', 'special_trait', 'modification'} or (attrs or {}).get('not_for_sale') or (attrs or {}).get('craft_only'):
            raise ValidationError('Этот элемент правил нельзя включить в торговый прайс')
    data = deepcopy(character.data or {})
    if 'money' in payload:
        data.setdefault('inventory', {})['money'] = money(payload['money'], label='Деньги торговца')
    data['merchant'] = {'enabled': payload['enabled'], 'prices': prices,
                        'repair_enabled':repair_enabled,'repair_markup':repair_markup,
                        'modification_enabled':modification_enabled,'scientist_enabled':scientist_enabled,
                        'workbenches':workbenches}
    character.data = data
    flag_modified(character, 'data')
    db.session.flush()
    return {'character_id': character.id, 'revision': character.revision, 'merchant': deepcopy(data['merchant']),
            'money': (data.get('inventory') or {}).get('money', 0)}


def tool_price(item, full_price):
    """Цена за экземпляр с учётом оставшихся зарядов инструмента."""
    price = money(full_price, label='Цена инструмента')
    if item.get('category') == 'tool':
        attrs = item.get('attributes') or {}
        maximum = item.get('maxUses', attrs.get('max_uses', attrs.get('uses')))
        if type(maximum) is int and maximum > 0:
            remaining = item.get('uses', attrs.get('uses_remaining', maximum))
            if type(remaining) is not int or not 0 <= remaining <= maximum:
                raise ValidationError('Некорректное количество зарядов инструмента')
            price = price * remaining // maximum
    return money(price, label='Цена инструмента')


def price_for(data, item, side):
    from app.services.equipment_catalog import retired_catalog_template
    if retired_catalog_template(item):
        raise ValidationError('Этот предмет выведен из каталога')
    if (item.get('attributes') or {}).get('not_for_sale'):
        raise ValidationError('Этот предмет нельзя продать или купить у торговца')
    if (item.get('attributes') or {}).get('craft_only'):
        raise ValidationError('Этот предмет доступен только через создание')
    if item.get('createdByPlayer'):
        raise ValidationError('Торговец не принимает предметы, созданные игроком вручную')
    key = str(item.get('templateId') or '')
    price = (((data or {}).get('merchant') or {}).get('prices') or {}).get(key, {}).get(side)
    if price is None:
        raise ValidationError('Для этого предмета торговец не установил цену')
    return tool_price(item, price)


def totals(actor_data, target_data, actor_offer, target_offer):
    from app.services.combat import CombatService
    def total(data, offer, side):
        result = 0
        for entry in offer:
            path = entry['path']
            if side == 'sell' and path[:1] != ['inventory']:
                raise ValidationError('Торговец продаёт товары из инвентаря, а не надетое снаряжение')
            item = CombatService._inventory_item_at_path(data, path)
            result += price_for(target_data, item, side) * entry['amount']
        return money(result, label='Стоимость предложения')
    sale = total(actor_data, actor_offer, 'buy')
    purchase = total(target_data, target_offer, 'sell')
    return {'actor_money': max(0, purchase - sale), 'target_money': max(0, sale - purchase),
            'purchase_total': purchase, 'sale_total': sale}


def require_contact(actor, target):
    # Передача из рук в руки использует ту же проверку расстояния/укрытий, что обливание.
    from app.services.burning import water_target_available
    if not water_target_available(actor, target):
        raise ValidationError('Для торговли подойдите к НПС: не дальше 1,5 м, без сплошной преграды')
