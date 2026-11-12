"""Публичная проекция внешности и поддерживаемых 3D-моделей экипировки."""

import re

APPEARANCE_OPTIONS = {
    'top': {'sweater', 'gorka', 'leather_coat', 'trench_coat'},
    'bottom': {'jeans', 'gorka', 'tactical', 'hunting'},
    'hair': {'short', 'bald'},
    'hairColor': {'black', 'brown', 'blond'},
    'sweaterColor': {'black', 'green', 'blue'},
    'leatherCoatColor': {'black', 'brown'},
    'trenchCoatColor': {'black', 'brown', 'navy'},
    'jeansColor': {'black', 'green', 'blue'},
    'tacticalColor': {'charcoal', 'khaki'},
    'huntingColor': {'olive', 'brown'},
    'expression': {'calm', 'wounded'},
}


MP50_NAME = re.compile(r'(?<!\w)мп[\s\-–‑]*50(?!\w)', re.IGNORECASE)


def public_character_appearance(data):
    """Возвращает только известные параметры внешности и идентификаторы моделей."""
    if not isinstance(data, dict):
        return None
    appearance = data.get('appearance')
    if not isinstance(appearance, dict):
        appearance = {}
    clean = {
        key: value
        for key, allowed in APPEARANCE_OPTIONS.items()
        if isinstance((value := appearance.get(key)), str) and value in allowed
    }
    equipment = data.get('equipment')
    mask = equipment.get('gasMask') if isinstance(equipment, dict) else None
    if isinstance(mask, dict) and (mask.get('templateId') or mask.get('model')):
        attributes = mask.get('attributes')
        catalog_name = attributes.get('catalog_name') if isinstance(attributes, dict) else None
        names = (mask.get('name'), mask.get('model'), catalog_name)
        if any(isinstance(name, str) and MP50_NAME.search(name) for name in names):
            clean['gear'] = ['mp50']
    return clean or None
