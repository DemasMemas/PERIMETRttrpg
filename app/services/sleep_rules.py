"""Условия восстановления во время общего сна."""
from app.services.exceptions import ValidationError
from app.services.item_template_lookup import resolve_item_template
from app.services.portable_lights import walk


SLEEP_FRACTIONS = {'none': 0.0, 'bed': 0.5, 'tent': 0.5, 'sleeping_bag': 0.375}
SLEEP_ITEMS = {'tent': 'Палатка', 'sleeping_bag': 'Спальный мешок'}


def sleep_fraction(character):
    data = character.data or {}
    mode = (((data.get('health') or {}).get('combatMeta') or {}).get('sleepMode') or 'none')
    if mode not in SLEEP_FRACTIONS:
        raise ValidationError(f'{character.name}: выберите место сна заново')
    item_name = SLEEP_ITEMS.get(mode)
    if item_name:
        found = False
        for path, item in walk(data, True):
            if path[0] != 'inventory' or item.get('name') != item_name:
                continue
            if type(item.get('quantity', 1)) is not int or item.get('quantity', 1) < 1:
                continue
            template = resolve_item_template(item, character.lobby_id)
            if (template and template.name == item_name and template.category == 'misc'
                    and (template.attributes or {}).get('import_source') == 'equipment_workbook'):
                found = True
                break
        if not found:
            raise ValidationError(f'{character.name}: для сна нужен предмет «{item_name}» в инвентаре')
    return SLEEP_FRACTIONS[mode]
