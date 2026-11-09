"""Предметы, удерживаемые в руках вне оружейных и приборных слотов."""
from app.services.exceptions import ValidationError


def items(data):
    stored = ((data or {}).get('inventory') or {}).get('held')
    return stored if isinstance(stored, list) else []


def preserve(current, incoming):
    inventory = incoming.setdefault('inventory', {})
    previous = items(current)
    if 'held' not in inventory:
        if previous:
            inventory['held'] = previous.copy()
        return
    known = {item['id']: item for item in previous}
    requested = inventory['held']
    if not isinstance(requested, list) or any(
        not isinstance(item, dict) or not isinstance(item.get('id'), str) or known.get(item['id']) != item
        for item in requested
    ) or len({item['id'] for item in requested}) != len(requested):
        raise ValidationError('Предметы в руках изменились. Обновите лист')
