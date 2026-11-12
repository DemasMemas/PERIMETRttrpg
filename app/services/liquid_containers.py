"""Правила хранения воды и топлива в инвентаре."""
from math import isfinite
import re

from app.services.exceptions import ValidationError
from app.services.portable_lights import walk

GLASS_BOTTLE = 'Стеклянная бутылка (Объем 1 литр) (3 использования воды)'
PLASTIC_BOTTLE = 'Пластиковая бутылка (Объем 1 литр) (3 использования воды)'


def liquid_kind(item):
    name = re.sub(r'\s+', '', str(item.get('name') or '').casefold()) if isinstance(item, dict) else ''
    if name == 'топливо(1литр)':
        return 'fuel'
    if name == 'вода(1литр)':
        return 'water'
    return None


def is_canister(item):
    if not isinstance(item, dict):
        return False
    attrs = item.get('attributes') or {}
    return (str(item.get('name') or '').casefold().startswith('канистра')
            and attrs.get('liquid_capacity_liters') == 15)


def liquid_capacity(item):
    if is_canister(item):
        return 15
    name = str(item.get('name') or '').casefold() if isinstance(item, dict) else ''
    if name.startswith(('стеклянная бутылка', 'пластиковая бутылка')):
        return 1
    return 1.5 if name.startswith('фляга') else 0


def return_empty_bottle(data, beverage):
    from app.models.templates import ItemTemplate
    from app.services.character_starting_purchase import item_from_template
    from uuid import uuid4

    name = {'вода': PLASTIC_BOTTLE, 'радиоактивная вода': PLASTIC_BOTTLE,
            'водка': GLASS_BOTTLE, 'вино': GLASS_BOTTLE}.get(beverage.strip().casefold())
    if not name:
        return
    template = ItemTemplate.query.filter_by(name=name).order_by(ItemTemplate.id).first()
    bottle = item_from_template(template) if template else {
        'id': uuid4().hex, 'name': name, 'category': 'misc', 'quantity': 1, 'contents': [],
        'isContainer': True, 'capacity': 1, 'volume': 2, 'weight': .5 if name == GLASS_BOTTLE else .25,
        'attributes': {'liquid_capacity_liters': 1},
    }
    data.setdefault('inventory', {}).setdefault('pockets', []).append(bottle)


def preserve_and_validate(current, incoming):
    previous = {item.get('id'): item for _, item in walk(current or {}, True)
                if liquid_capacity(item) and item.get('id')}
    for _, item in walk(incoming, True):
        capacity = liquid_capacity(item)
        if not capacity:
            continue
        if item.get('quantity', 1) != 1:
            if not item.get('contents') and not item.get('fuelUsed') and not is_canister(item):
                continue
            raise ValidationError('Заполненные и использованные под топливо ёмкости хранятся по отдельности')
        contents = item.get('contents') or []
        if not isinstance(contents, list):
            raise ValidationError('Содержимое ёмкости повреждено')
        used = 0.0
        has_fuel = False
        has_water = False
        for liquid in contents:
            kind = liquid_kind(liquid)
            if not isinstance(liquid, dict) or kind is None or liquid.get('contents'):
                raise ValidationError('В этой ёмкости можно хранить только воду и топливо')
            amount = liquid.get('quantity', 1)
            if (type(amount) not in (int, float) or not isfinite(amount)
                    or amount <= 0 or round(amount * 10) != amount * 10):
                raise ValidationError('Количество жидкости должно быть кратно 0,1 л')
            used += amount
            has_fuel |= kind == 'fuel'
            has_water |= kind == 'water'
        if used > capacity + 1e-7:
            raise ValidationError(f'В ёмкости может быть не более {capacity:g} л жидкости')
        old = previous.get(item.get('id')) or {}
        old_fuel = any(liquid_kind(entry) == 'fuel' for entry in old.get('contents') or [])
        fuel_used = bool(old.get('fuelUsed') or old_fuel or has_fuel)
        if has_water and fuel_used:
            raise ValidationError('После топлива воду в этой ёмкости хранить нельзя')
        item['isContainer'] = True
        item['capacity'] = capacity
        item['fuelUsed'] = fuel_used
