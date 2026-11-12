"""Заряжаемые элементы питания и их состояние при замене в приборе."""
from copy import deepcopy
from math import isfinite

from app.services.exceptions import ValidationError

NAME = 'Аккумуляторная батарея'


def kind(item):
    attrs = item.get('attributes') or {}
    if item.get('slotType') == 'exoskeleton_battery' or attrs.get('slot_type') == 'exoskeleton_battery':
        return 'exoskeleton'
    if str(item.get('name') or '').strip().casefold() == NAME.casefold():
        return 'rechargeable'
    return None


def percentage(item):
    attrs = item.get('attributes') or {}
    if kind(item) == 'exoskeleton':
        days = attrs.get('remaining_days', 0)
        limit = capacity(item)
        value = 100 * days / limit if limit and type(days) in (int, float) else 0
    else:
        value = attrs.get('charge_percent', 100)
    return max(0, min(100, value)) if type(value) in (int, float) and isfinite(value) else 0


def capacity(item):
    value = (item.get('attributes') or {}).get('charge_days', 1)
    return value if type(value) in (int, float) and isfinite(value) and value > 0 else 0


def set_percentage(item, value):
    attrs = item.setdefault('attributes', {})
    if kind(item) == 'exoskeleton':
        attrs['remaining_days'] = capacity(item) * value / 100
    else:
        attrs['charge_percent'] = value


def usable(item, *, flashlight=False):
    cell_kind = kind(item)
    if cell_kind == 'rechargeable':
        return item.get('quantity', 1) == 1 and percentage(item) > 0
    name = str(item.get('name') or '').strip().casefold()
    return not cell_kind and (name == 'батарейки' or flashlight and 'батаре' in name)


def choices(data, *, flashlight=False):
    from app.services.portable_lights import walk

    result = []
    for path, item in walk(data, True):
        if path[0] not in {'equipment', 'inventory'} or 'installedModules' in path or not usable(item, flashlight=flashlight):
            continue
        result.append({'path': path, 'name': item.get('name'), 'standard_fit': usable(item),
            'charge': percentage(item) if kind(item) == 'rechargeable' else None})
    return result


def eject(data, device, remaining):
    installed = device.pop('rechargeableBattery', None)
    if not installed:
        return False
    set_percentage(installed, remaining)
    data.setdefault('inventory', {}).setdefault('pockets', []).append(installed)
    return True


def swap(data, device, path, remaining, *, flashlight=False):
    from app.services.combat import CombatService
    from app.services.portable_lights import walk
    from uuid import uuid4

    item = next((item for address, item in walk(data, True)
        if address == path and address[0] in {'inventory', 'equipment'} and 'installedModules' not in address), None)
    if not item or not usable(item, flashlight=flashlight):
        raise ValidationError('Выберите заряженную батарейку в инвентаре')
    charge = percentage(item) if kind(item) == 'rechargeable' else 100
    taken = CombatService._take_inventory_item(data, path, 1)
    eject(data, device, remaining)
    if kind(item) == 'rechargeable':
        if not taken.get('id'):
            taken['id'] = uuid4().hex
        device['rechargeableBattery'] = taken
    return charge


def preserve(current, incoming):
    from app.services.electronics import items

    previous = {item['id']: item for _, item in items(current) if item.get('id')}
    for _, item in items(incoming):
        old = previous.get(item.get('id'), {})
        installed = old.get('rechargeableBattery')
        if installed:
            item['rechargeableBattery'] = deepcopy(installed)
        else:
            item.pop('rechargeableBattery', None)
        if kind(item) == 'rechargeable' and kind(old) == 'rechargeable':
            set_percentage(item, percentage(old))


def templates():
    from app.services.catalog_import import template

    return [template(NAME, 'device', 750, 0, .2,
        'Многоразовый элемент питания. Заряжается в работающем бытовом генераторе.',
        'battery', {'rechargeable_battery': True, 'charge_percent': 100})]
