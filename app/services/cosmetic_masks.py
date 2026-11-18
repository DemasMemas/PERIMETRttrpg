"""Перенос тканевых масок из прежнего слота шлема."""

MASK_NAMES = ('Балаклава', 'Балаклава Призрак')
_MASK_NAMES = {name.casefold(): name for name in MASK_NAMES}


def mask_name(value):
    return _MASK_NAMES.get(' '.join(value.casefold().split())) if isinstance(value, str) else None


def cosmetic_mask_name(item):
    if not isinstance(item, dict):
        return None
    attrs = item.get('attributes')
    catalog_name = attrs.get('catalog_name') if isinstance(attrs, dict) else None
    return mask_name(catalog_name) or mask_name(item.get('model')) or mask_name(item.get('name'))


def normalize_character_masks(data, template_names=None):
    from app.services.portable_lights import walk

    inventory, equipment = data.get('inventory'), data.get('equipment')
    items = {'inventory': inventory if isinstance(inventory, dict) else {},
             'equipment': equipment if isinstance(equipment, dict) else {}}
    changed = False
    for path, item in walk(items, all_items=True):
        category = item.get('category')
        template_name = (template_names or {}).get(str(item.get('templateId')))
        if category != 'helmet' and path != ['equipment', 'helmet'] and not (not category and template_name):
            continue
        recognized = cosmetic_mask_name(item)
        name = recognized or template_name
        if name and not item.get('integratedWithArmor'):
            if not recognized:
                attrs = item.get('attributes')
                item['attributes'] = {**(attrs if isinstance(attrs, dict) else {}), 'catalog_name': name}
                changed = True
            if item.get('category') != 'mask':
                item['category'] = 'mask'
                changed = True
    equipment = items['equipment']
    helmet = equipment.get('helmet')
    if isinstance(helmet, dict) and helmet.get('category') == 'mask' and not helmet.get('integratedWithArmor'):
        equipment.pop('helmet')
        if equipment.get('mask'):
            inventory = data['inventory'] = items['inventory']
            if not isinstance(inventory.get('backpack'), list):
                inventory['backpack'] = []
            inventory['backpack'].append(helmet)
        else:
            equipment['mask'] = helmet
        changed = True
    return changed
