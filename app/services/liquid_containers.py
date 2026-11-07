"""Правила содержимого канистры в листе персонажа."""
from math import isfinite
import re

from app.services.exceptions import ValidationError
from app.services.portable_lights import walk


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


def preserve_and_validate(current, incoming):
    previous = {item.get('id'): item for _, item in walk(current or {}, True)
                if is_canister(item) and item.get('id')}
    for _, item in walk(incoming, True):
        if not is_canister(item):
            continue
        if item.get('quantity', 1) != 1:
            raise ValidationError('Канистры нужно хранить по отдельности')
        contents = item.get('contents') or []
        if not isinstance(contents, list):
            raise ValidationError('Содержимое канистры повреждено')
        used = 0.0
        has_fuel = False
        has_water = False
        for liquid in contents:
            kind = liquid_kind(liquid)
            if not isinstance(liquid, dict) or kind is None or liquid.get('contents'):
                raise ValidationError('В канистре можно хранить только воду и топливо')
            amount = liquid.get('quantity', 1)
            if (type(amount) not in (int, float) or not isfinite(amount)
                    or amount <= 0 or round(amount * 10) != amount * 10):
                raise ValidationError('Количество жидкости должно быть кратно 0,1 л')
            used += amount
            has_fuel |= kind == 'fuel'
            has_water |= kind == 'water'
        if used > 15.000001:
            raise ValidationError('В канистре может быть не более 15 л жидкости')
        old = previous.get(item.get('id')) or {}
        old_fuel = any(liquid_kind(entry) == 'fuel' for entry in old.get('contents') or [])
        fuel_used = bool(old.get('fuelUsed') or old_fuel or has_fuel)
        if has_water and fuel_used:
            raise ValidationError('После топлива воду в этой канистре хранить нельзя')
        item['isContainer'] = True
        item['fuelUsed'] = fuel_used
