"""Публичная проекция внешности и поддерживаемых 3D-моделей экипировки."""

import re

GEAR_COLORS = {'charcoal', 'gray', 'brown', 'olive', 'khaki', 'navy', 'burgundy'}
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
    **dict.fromkeys(('beretColor', 'beanieColor', 'capColor', 'panamaColor'), GEAR_COLORS),
    **dict.fromkeys(('balaclavaColor', 'ghostMaskColor'), GEAR_COLORS | {'black', 'dark_gray'}),
    **dict.fromkeys((
        'vityazColor', 'udarnikColor', 'udarnikMColor', 'pervoprohodetsColor',
        'kysa2Color', 'gusarColor', 'shz13mColor', 'karbovanetsColor', 'volkAColor',
    ), {'green', 'sand', 'black', 'olive'}),
    'ushankaColor': {'gray', 'brown'},
    **dict.fromkeys(('respiratorColor', 'gp5Color', 'gp9vColor', 'gp7Color', 'sp10Color'), {'white', 'black'}),
}


EQUIPMENT_MODELS = {
    'gasMask': tuple((model, re.compile(pattern, re.IGNORECASE)) for model, pattern in (
        ('mp50', r'(?<!\w)мп[\s\-–‑]*50(?!\w)'),
        ('gasmask_respirator', r'^(?:противогаз\s+)?респиратор$'),
        ('gasmask_gp5', r'^(?:противогаз\s+)?гп[\s\-–‑]*5$'),
        ('gasmask_gp9v', r'^(?:противогаз\s+)?гп[\s\-–‑]*9[вv]$'),
        ('gasmask_gp7', r'^(?:противогаз\s+)?гп[\s\-–‑]*7$'),
        ('gasmask_sp10', r'^(?:противогаз\s+)?сп[\s\-–‑]*10$'),
    )),
    'helmet': tuple((model, re.compile(pattern, re.IGNORECASE)) for model, pattern in (
        ('helmet_68g', r'^советский\s+кот[её]лок\s+68г$'), ('headwear_beret', r'^берет$'),
        ('headwear_beanie', r'^шапка$'), ('headwear_cap', r'^кепка$'), ('headwear_panama', r'^панама$'),
        ('headwear_ushanka', r'^(?:прочее\s+)?ушанка$'),
        ('helmet_vityaz', r'^(?:шлем\s+)?витязь\s+4[вbv]76$'),
        ('helmet_udarnik', r'^(?:шлем\s+)?ударник$'),
        ('helmet_udarnik_m', r'^(?:шлем\s+)?ударник[\s\-–‑]+м$'),
        ('helmet_pervoprohodets', r'^(?:шлем\s+)?первопроходец$'),
        ('helmet_blockpost', r'^(?:шлем\s+)?блокпост$'),
        ('helmet_voevoda', r'^(?:шлем\s+)?воевода$'),
        ('helmet_kuznets', r'^(?:шлем\s+)?кузнец$'),
        ('helmet_kysa_2', r'^(?:шлем\s+)?кыса[\s\-–‑]+2$'),
        ('helmet_gusar', r'^(?:шлем\s+)?гусар$'),
        ('helmet_shz_13m', r'^(?:шлем\s+)?шз[\s\-–‑]+13м$'),
        ('helmet_karbovanets', r'^(?:шлем\s+)?карбованец$'),
        ('helmet_volk_a', r'^(?:шлем\s+)?волк[\s\-–‑]+а$'),
    )),
    'mask': (
        ('mask_balaclava', re.compile(r'^балаклава$', re.IGNORECASE)),
        ('mask_ghost', re.compile(r'^балаклава\s+призрак$', re.IGNORECASE)),
    ),
}


def equipment_visual(item, slot):
    if not isinstance(item, dict) or not (item.get('templateId') or item.get('model')):
        return None
    attributes = item.get('attributes')
    names = (attributes.get('catalog_name') if isinstance(attributes, dict) else None,
             item.get('model'), item.get('name'))
    return next((model for name in names if isinstance(name, str)
                 for model, pattern in EQUIPMENT_MODELS[slot] if pattern.search(name.strip())), None)


def public_character_appearance(data, *, include_belt=True):
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
    if isinstance(equipment, dict):
        for slot in EQUIPMENT_MODELS:
            item = equipment.get(slot)
            if slot == 'mask' and not item:
                item = equipment.get('helmet')
            model = equipment_visual(item, slot)
            if model:
                gear = clean.setdefault('gear', [])
                gear.append(model)
                modifications = item.get('modifications')
                if model == 'headwear_ushanka' and isinstance(modifications, list) and any(
                    isinstance(modification, dict) and modification.get('ruleId') == 'armor-g7'
                    for modification in modifications
                ):
                    gear.append('headwear_ushanka_star')
    if include_belt:
        from app.services.belt_attachments import belt_visuals
        if belt := belt_visuals(data, (data.get('activeWeaponIndex'),)):
            clean['belt'] = belt
    return clean or None
