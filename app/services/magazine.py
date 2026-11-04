"""Inventory mutations for detachable magazines; callers own the transaction."""
from copy import deepcopy
from math import isfinite
import re

from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.exceptions import ConflictError, ValidationError
from app.services.inventory import normalize_caliber
from app.services.transaction import atomic_operation


def _template(item):
    try:
        template_id = int(item.get('templateId') or 0)
    except (TypeError, ValueError):
        return None
    return db.session.get(ItemTemplate, template_id) if template_id else None


def _attributes(item):
    template = _template(item)
    return {**((template.attributes or {}) if template else {}), **(item.get('attributes') or {})}


def inventory_slot(data, path):
    if not isinstance(path, list) or len(path) < 3 or not isinstance(path[0], str) or path[0] not in {'inventory', 'equipment'}:
        raise ValidationError('Выберите магазин в инвентаре')
    parent = data
    for key in path[:-1]:
        if isinstance(parent, dict) and isinstance(key, str) and key in parent:
            parent = parent[key]
        elif isinstance(parent, list) and type(key) is int and 0 <= key < len(parent):
            parent = parent[key]
        else:
            raise ConflictError('Предмет перемещён. Выберите магазин заново.')
    index = path[-1]
    if not isinstance(parent, list) or type(index) is not int or not 0 <= index < len(parent) or not isinstance(parent[index], dict):
        raise ConflictError('Предмет перемещён. Выберите магазин заново.')
    return parent, index


def _caliber(item):
    attributes = _attributes(item)
    value = normalize_caliber(attributes.get('caliber') or item.get('caliber') or attributes.get('magazine_caliber') or '')
    numeric = re.match(r'^\d+x\d+', value)
    return numeric.group() if numeric else value


def validate_magazine_selection(data, weapon_index, item_path, template_id=None, selection=None):
    from app.services.weapon_grip import ensure_reload_allowed
    ensure_reload_allowed(data)
    weapons = data.get('weapons') or []
    if type(weapon_index) is not int or not 0 <= weapon_index < len(weapons) or not isinstance(weapons[weapon_index], dict):
        raise ValidationError('Оружие не найдено')
    weapon = weapons[weapon_index]
    if _attributes(weapon).get('fixedMagazine'):
        raise ValidationError('У этого оружия несъёмный магазин')
    parent, index = inventory_slot(data, item_path)
    item = parent[index]
    attributes = _attributes(item)
    if item.get('category') != 'magazine' or attributes.get('isLoader') or attributes.get('loadingDevice'):
        raise ValidationError('Нужен съёмный магазин, а не устройство зарядки')
    if any(word in str(item.get('name') or '').lower() for word in ('спидлоадер', 'лента', 'подавач')):
        raise ValidationError('Устройство зарядки нельзя установить вместо магазина')
    if type(item.get('quantity', 1)) not in (int, float) or item.get('quantity', 1) != 1:
        raise ValidationError('Магазины должны храниться отдельными предметами')
    if template_id is not None and str(item.get('templateId')) != str(template_id):
        raise ConflictError('Выбранный магазин изменился')
    if selection is not None:
        if not isinstance(selection, dict):
            raise ValidationError('Некорректный выбор магазина')
        expected_item = selection.get('magazine')
        expected_weapon = selection.get('weapon')
        if not isinstance(expected_item, dict) or not isinstance(expected_weapon, dict):
            raise ValidationError('Не указан выбранный магазин или оружие')
        for actual, expected in ((item, expected_item), (weapon, expected_weapon)):
            if any(actual.get(key) != value for key, value in expected.items()):
                raise ConflictError('Магазин или оружие изменились. Выберите их заново.')
    template = _template(item)
    compatible = (template.attributes or {}).get('compatible_weapons') if template else None
    if compatible is None:
        compatible = attributes.get('compatible_weapons') or []
    if compatible and str(weapon.get('templateId')) not in {str(value) for value in compatible}:
        raise ValidationError('Этот магазин не подходит к данному оружию')
    weapon_caliber, magazine_caliber = _caliber(weapon), _caliber(item)
    if weapon_caliber and magazine_caliber and weapon_caliber != magazine_caliber:
        raise ValidationError('Калибр магазина не соответствует оружию')
    count = _ammo_count(item)
    capacity = attributes.get('capacity', item.get('capacity'))
    if capacity is not None:
        try:
            capacity = float(capacity)
        except (TypeError, ValueError):
            raise ValidationError('Некорректная ёмкость магазина')
        if not isfinite(capacity) or capacity < 0 or count > capacity:
            raise ValidationError('В магазине больше патронов, чем он вмещает')
    for stack in item.get('ammo') or []:
        caliber = _caliber(stack)
        expected = weapon_caliber or magazine_caliber
        if stack['quantity'] > 0 and expected and caliber and caliber != expected:
            raise ValidationError('В магазине патроны неподходящего калибра')
    return weapon, item, attributes


def _ammo_count(magazine):
    stacks = magazine.get('ammo') or []
    if not isinstance(stacks, list):
        raise ValidationError('Некорректные патроны в магазине')
    count = 0
    for stack in stacks:
        quantity = stack.get('quantity') if isinstance(stack, dict) else None
        if type(quantity) not in (int, float) or not isfinite(quantity) or quantity < 0 or quantity != int(quantity):
            raise ValidationError('Некорректное количество патронов в магазине')
        count += int(quantity)
    return count


def magazine_as_inventory_item(magazine):
    # Keep custom fields, provenance, ammo variants and modules on round trips.
    item = deepcopy(magazine)
    item.pop('sourcePath', None)
    item.update(category='magazine', quantity=1)
    attributes = item.setdefault('attributes', {})
    for attribute, field in (('capacity', 'capacity'), ('caliber', 'caliber'), ('emptyWeight', 'emptyWeight'),
                             ('loadedWeight', 'loadedWeight'), ('ergonomics', 'ergonomics'), ('reload_time_od', 'reloadTimeActionPoints')):
        if attribute not in attributes and field in item:
            attributes[attribute] = item[field]
    item.setdefault('volume', 0.2)
    item['currentAmmo'] = _ammo_count(item)
    item['weight'] = (item.get('loadedWeight') or 0.25) if item['currentAmmo'] else (item.get('emptyWeight') or 0)
    return item


def install_magazine(data, weapon_index, item_path, template_id=None, selection=None):
    weapon, item, attributes = validate_magazine_selection(data, weapon_index, item_path, template_id, selection)
    ammo_count = _ammo_count(item)
    magazine = deepcopy(item)
    magazine.update(
        caliber=attributes.get('caliber') or item.get('caliber') or '',
        capacity=attributes.get('capacity', item.get('capacity', 30)),
        emptyWeight=item.get('emptyWeight', attributes.get('emptyWeight', 0)),
        loadedWeight=item.get('loadedWeight', attributes.get('loadedWeight', 0)),
        ergonomics=attributes.get('ergonomics', item.get('ergonomics', 0)),
        reloadTimeActionPoints=attributes.get('reload_time_od', 0),
        ammo=deepcopy(item.get('ammo') or []), sourcePath=deepcopy(item_path), currentAmmo=ammo_count,
    )
    old = weapon.get('installedMagazine')
    old_item = magazine_as_inventory_item(old) if isinstance(old, dict) else None
    parent, index = inventory_slot(data, item_path)
    if old_item is None:
        parent.pop(index)
    else:
        parent[index] = old_item
    weapon['installedMagazine'] = magazine
    weapon['ammo'] = ammo_count
    return magazine


@atomic_operation
def install_outside_combat(character_id, user_id, payload):
    from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
    from app.services.character import CharacterService
    from app.services.exceptions import NotFoundError
    character = db.session.get(LobbyCharacter, character_id)
    if not character:
        raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character, user_id, edit=True)
    active = LocationCombatState.query.join(LocationCharacter, LocationCharacter.location_id == LocationCombatState.location_id).filter(
        LocationCharacter.character_id == character_id, LocationCombatState.status == 'active',
    ).first()
    if active:
        raise ValidationError('В бою используйте действие перезарядки с оплатой ОД')
    if type(payload.get('character_revision')) is not int or payload['character_revision'] != character.revision:
        raise ConflictError()
    data = deepcopy(character.data or {})
    install_magazine(data, payload.get('weapon_index'), payload.get('item_path'),
                     payload.get('magazine_template_id'), payload.get('magazine_selection'))
    character.data = data
    return character
