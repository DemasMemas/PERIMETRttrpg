"""Server-authoritative installation of equipment modules."""

from copy import deepcopy
from itertools import chain
import re
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
from app.models.templates import ItemTemplate
from app.services.character import CharacterService
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.gas_mask_filters import filter_charges, is_gas_mask_filter
from app.services.magazine import inventory_slot
from app.services import power_cells
from app.services.transaction import atomic_operation


SUPPORTED_SLOTS = {"filter", "artifact", "exoskeleton_battery"}
IDENTITY_FIELDS = ("id", "templateId", "name", "category")


def _value_at_path(data, path, *, label):
    if not isinstance(path, list) or not path:
        raise ValidationError(f"Выберите {label}")
    value = data
    for key in path:
        if isinstance(value, dict) and isinstance(key, str) and key in value:
            value = value[key]
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
            value = value[key]
        else:
            raise ConflictError(f"{label.capitalize()} перемещён. Выберите его заново.")
    if not isinstance(value, dict):
        raise ConflictError(f"{label.capitalize()} перемещён. Выберите его заново.")
    return value


def _template(item):
    try:
        template_id = int((item or {}).get("templateId") or 0)
    except (TypeError, ValueError):
        template_id = 0
    return db.session.get(ItemTemplate, template_id) if template_id else None


def _slots(item):
    template = _template(item)
    attributes = template.attributes if template and isinstance(template.attributes, dict) else {}
    slots = attributes.get("slots") or (item.get("attributes") or {}).get("slots") or item.get("slots") or []
    return {
        str(slot.get("type") or "").strip().lower()
        for slot in slots
        if isinstance(slot, dict)
    }


def _validate_target(target, slot_type):
    if slot_type not in SUPPORTED_SLOTS:
        raise ValidationError("Этот тип модуля пока нельзя устанавливать сервером")
    if slot_type == "artifact":
        attrs = _container_attributes(target)
        if (target.get("category") != "container" or target.get("quantity", 1) != 1
                or attrs.get("artifact_container_kind") not in {"storage", "equipped"}):
            raise ValidationError("Выберите отдельный контейнер для артефактов")
        return
    if slot_type == 'exoskeleton_battery':
        from app.services.combat import CombatService

        if target.get('quantity', 1) != 1 or not CombatService._is_exoskeleton_item(target, _template(target)):
            raise ValidationError('Выберите отдельный экзоскелет')
        return
    installed = target.get("installedModules") or []
    has_installed_slot = any(
        isinstance(module, dict) and module.get("slotType") == slot_type
        for module in installed
    )
    if slot_type not in _slots(target) and not has_installed_slot:
        raise ValidationError("У выбранной экипировки нет слота для фильтра")
    category = str(target.get("category") or (_template(target).category if _template(target) else "")).lower()
    if category not in {"gas_mask", "helmet"}:
        raise ValidationError("Фильтр можно установить только в противогаз или противогазо-шлем")


def _container_attributes(target):
    template = _template(target)
    return {**((template.attributes or {}) if template else {}), **(target.get("attributes") or {})}


def _artifact_slot(target):
    for key in ("installedModules", "contents"):
        for index, item in enumerate(target.get(key) or []):
            if isinstance(item, dict) and (item.get("category") == "artifact" or item.get("slotType") == "artifact"):
                return target[key], index
    return None


def _artifact_source(data, path):
    _validate_source_path(path)
    if "installedModules" in path or "containers" in path:
        raise ValidationError("Сначала извлеките артефакт из контейнера или снимите его с крепления")
    parent, index = inventory_slot(data, path)
    source = parent[index]
    quantity = source.get("quantity", 1)
    if source.get("category") != "artifact" or type(quantity) is not int or quantity < 1:
        raise ValidationError("Выберите артефакт в инвентаре")
    if path[-2] == "contents":
        container = _value_at_path(data, path[:-2], label="контейнер")
        if _container_attributes(container).get("artifact_container_kind") in {"storage", "equipped"}:
            raise ValidationError("Сначала извлеките артефакт из контейнера")
    return parent, index


def _artifact_action_details(data, target, details, selection, in_combat):
    target_path = details["target_path"]
    worn = (len(target_path) == 5 and target_path[:3] == ["equipment", "armor", "containers"]
            and type(target_path[3]) is int and target_path[4] == "item")
    if not worn:
        if "installedModules" in target_path:
            raise ValidationError("Контейнер должен быть доступен в инвентаре или на броне")
        _validate_source_path(target_path)
        inventory_slot(data, target_path)
    installed = _artifact_slot(target)
    if details["operation"] == "install":
        if worn and _container_attributes(target).get("artifact_container_kind") == "storage":
            raise ValidationError("Контейнер хранения нельзя использовать на броне")
        if installed is not None:
            raise ValidationError("В контейнере уже есть артефакт")
        parent, index = _artifact_source(data, details["item_path"])
        _validate_identity(parent[index], selection.get("source"), label="артефакт")
        cost = 4
    else:
        if installed is None:
            raise ValidationError("В контейнере нет артефакта")
        parent, index = installed
        _validate_identity(parent[index], selection.get("source"), label="артефакт")
        cost = 8
    details["action_points"] = cost if in_combat else 0
    return details


def _validate_source_path(path, *, allow_held=False):
    direct_inventory = (
        isinstance(path, list)
        and len(path) >= 3
        and path[0] == 'inventory'
        and (path[1] in ('pockets', 'backpack') or allow_held and path[1] == 'held')
    )
    equipped_pouch = (
        isinstance(path, list)
        and len(path) >= 6
        and path[0] == "equipment"
        and path[1] in {"belt", "vest"}
        and path[2] == "pouches"
        and type(path[3]) is int
        and path[4] == "contents"
    )
    from app.services.belt_attachments import tool_path
    if not direct_inventory and not equipped_pouch and not tool_path(path):
        raise ValidationError("Предмет нужно выбрать в инвентаре, подсумке или на поясе")


def _validate_identity(item, expected, *, label):
    if not isinstance(expected, dict) or not any(field in expected for field in IDENTITY_FIELDS):
        raise ValidationError(f"Не сохранён выбранный {label}")
    if any(item.get(field) != expected[field] for field in IDENTITY_FIELDS if field in expected):
        raise ConflictError(f"{label.capitalize()} изменился. Выберите его заново.")


def _battery_index(target):
    return next((index for index, module in enumerate(target.get('installedModules') or [])
                 if isinstance(module, dict) and power_cells.kind(module) == 'exoskeleton'), None)


def battery_replacement_recipient(actor, target_character_id):
    from app.services.combat import CombatService

    if type(target_character_id) is not int or target_character_id == actor.character_id:
        raise ValidationError('Выберите другого персонажа в экзоскелете')
    target = LocationCharacter.query.filter_by(
        location_id=actor.location_id, character_id=target_character_id,
    ).first()
    if not target or not CombatService._is_adjacent(actor, target):
        raise ValidationError('Для замены батареи нужно находиться рядом с персонажем')
    if not CombatService._exoskeleton_power_profile(target.character.data)['is_exoskeleton']:
        raise ValidationError('Персонаж должен быть в экзоскелете')
    if CombatService.active_combat_placements([target_character_id], exclude_location_id=actor.location_id):
        raise ValidationError('Персонаж участвует в бою на другой подлокации')
    return target.character


def battery_operation_binding(data, target_path, *, recipient_data=None):
    from app.services.combat import CombatService

    target = _value_at_path(data if recipient_data is None else recipient_data, target_path, label='экзоскелет')
    index = _battery_index(target)
    battery = target['installedModules'][index] if index is not None else {}
    return {
        'target': {key: target.get(key) for key in IDENTITY_FIELDS},
        'battery': battery.get('id') or battery.get('templateId'),
        'occupied': index is not None,
        'wearing_exoskeleton': CombatService._exoskeleton_power_profile(data)['is_exoskeleton'],
    }


def _exoskeleton_battery_details(data, target, details, selection, in_combat):
    from app.services.combat import CombatService

    target_path = details['target_path']
    if target_path != ['equipment', 'armor']:
        _validate_source_path(target_path, allow_held=True)
        inventory_slot(data, target_path)
    tools = (item for item, _ in CombatService._iter_inventory_items_with_paths(data, include_held=True))
    belt = (data.get('equipment') or {}).get('belt') or {}
    tools_on_belt = (item for item in belt.get('installedModules') or []
                     if isinstance(item, dict) and item.get('slotType') == 'belt_tool')
    if not any(item.get('category') in {'misc', 'tool'} and item.get('quantity', 1) > 0
               and str(item.get('name') or '').strip().casefold().replace('ё', 'е') in {'отвертка', 'отвертка саперная'}
               for item in chain(tools, tools_on_belt)):
        raise ValidationError('Нужна отвёртка для аккумулятора экзоскелета')
    if details['operation'] == 'uninstall':
        if _battery_index(target) is None:
            raise ValidationError('В экзоскелете нет аккумулятора')
        return details
    path = details['item_path']
    _validate_source_path(path, allow_held=True)
    parent, index = inventory_slot(data, path)
    battery = parent[index]
    _validate_identity(battery, selection.get('source'), label='аккумулятор')
    if (battery.get('category') != 'exoskeleton_module' or power_cells.kind(battery) != 'exoskeleton'
            or power_cells.percentage(battery) <= 0 or battery.get('quantity', 1) < 1):
        raise ValidationError('Выберите заряженный аккумулятор экзоскелета')
    if in_combat:
        details['action_points'] = 24 if CombatService._exoskeleton_power_profile(data)['is_exoskeleton'] else 12
    return details


def module_action_details(
    data,
    operation,
    target_path,
    slot_type,
    *,
    item_path=None,
    selection=None,
    in_combat=False,
    recipient_data=None,
):
    operation = str(operation or "").strip().lower()
    slot_type = str(slot_type or "").strip().lower()
    if operation not in {"install", "uninstall"}:
        raise ValidationError("Неизвестная операция с модулем")
    if recipient_data is not None and (slot_type != 'exoskeleton_battery' or operation != 'install'
                                       or target_path != ['equipment', 'armor']):
        raise ValidationError('На другом персонаже можно только заменить батарею надетого экзоскелета')
    target = _value_at_path(data if recipient_data is None else recipient_data, target_path, label="экипировку")
    _validate_target(target, slot_type)
    selection = selection if isinstance(selection, dict) else {}
    if recipient_data is None:
        _validate_identity(target, selection.get("target"), label="целевая экипировка")
    details = {
        "operation": operation,
        "slot_type": slot_type,
        "target_path": deepcopy(target_path),
        "item_path": deepcopy(item_path),
        "action_points": 0,
        "inventory_access": None,
    }
    if slot_type == "artifact":
        return _artifact_action_details(data, target, details, selection, in_combat)
    if slot_type == 'exoskeleton_battery':
        return _exoskeleton_battery_details(data, target, details, selection, in_combat)
    if operation == "uninstall":
        if not any(
            isinstance(module, dict) and module.get("slotType") == slot_type
            for module in (target.get("installedModules") or [])
        ):
            raise ValidationError("В этом слоте нет фильтра")
        return details

    _validate_source_path(item_path)
    parent, index = inventory_slot(data, item_path)
    source = parent[index]
    _validate_identity(source, selection.get("source"), label="фильтр")
    if not is_gas_mask_filter(source) or filter_charges(source) <= 0:
        raise ValidationError("Выберите непустой фильтр противогаза")
    if in_combat:
        from app.services.inventory_access import calculate_inventory_access

        access = calculate_inventory_access(data, item_path)
        details["inventory_access"] = access
        template = _template(source)
        attributes = {**((template.attributes or {}) if template else {}), **(source.get('attributes') or {})}
        direct = (attributes.get('consumable') or {}).get('direct') or {}
        base_cost = attributes.get('install_action_points', direct.get('filter_install_action_points'))
        if base_cost is None:
            description = attributes.get('raw_description') or (template.description if template else '') or source.get('description') or ''
            match = re.search(r'замена фильтра[^.]*?[-−–—]\s*(\d+)\s*од', description, re.I)
            base_cost = int(match.group(1)) if match else 0
        details['base_action_points'] = max(0, int(base_cost))
        details["action_points"] = int(access["retrieval_action_points"]) + max(0,
            details['base_action_points'] - int(access['use_action_discount']))
    return details


def _normalized_filter(source):
    installed = deepcopy(source)
    charges = filter_charges(installed)
    attributes = installed.setdefault("attributes", {})
    capacity = max(
        charges,
        float(installed.get("maxDurability") or attributes.get("max_durability") or charges),
    )
    installed.update({
        "category": "gas_mask_module",
        "subcategory": "filter",
        "slotType": "filter",
        "quantity": 1,
        "durability": charges,
        "maxDurability": capacity,
    })
    attributes.update({
        "slot_type": "filter",
        "durability": charges,
        "max_durability": capacity,
    })
    installed.pop("sourcePath", None)
    return installed


def apply_module_action(data, details, *, recipient_data=None):
    target_data = data if recipient_data is None else recipient_data
    target = _value_at_path(target_data, details["target_path"], label="экипировку")
    slot_type = details["slot_type"]
    _validate_target(target, slot_type)
    if slot_type == "artifact":
        return _apply_artifact_action(data, target, details)
    modules = target.setdefault("installedModules", [])
    if slot_type == 'exoskeleton_battery':
        from app.services.combat import CombatService

        existing = _battery_index(target)
        installed = None
        if details['operation'] == 'install':
            parent, index = inventory_slot(data, details['item_path'])
            split = parent[index].get('quantity', 1) > 1
            installed = CombatService._take_inventory_item(data, details['item_path'], 1)
            if split or not installed.get('id'):
                installed['id'] = str(uuid4())
            installed['slotType'] = slot_type
            installed.pop('sourcePath', None)
        removed = modules.pop(existing) if existing is not None else None
        if removed is not None:
            target_data.setdefault('inventory', {}).setdefault('backpack', []).append(removed)
        if installed is not None:
            modules.append(installed)
        target['powered'] = installed is not None
        return {**details, 'installed': deepcopy(installed),
                ('replaced' if installed is not None else 'removed'): deepcopy(removed)}
    existing_index = next((
        index for index, module in enumerate(modules)
        if isinstance(module, dict) and module.get("slotType") == slot_type
    ), None)

    if details["operation"] == "uninstall":
        if existing_index is None:
            raise ConflictError("Фильтр уже снят")
        removed = modules.pop(existing_index)
        data.setdefault("inventory", {}).setdefault("backpack", []).append(removed)
        return {**details, "removed": deepcopy(removed), "installed": None}

    parent, source_index = inventory_slot(data, details["item_path"])
    source = parent[source_index]
    if not is_gas_mask_filter(source) or filter_charges(source) <= 0:
        raise ConflictError("Фильтр изменился. Выберите его заново.")
    installed = _normalized_filter(source)
    quantity = max(1, int(source.get("quantity") or 1))
    if quantity > 1:
        source["quantity"] = quantity - 1
    else:
        parent.pop(source_index)

    replaced = None
    if existing_index is not None:
        replaced = modules.pop(existing_index)
        data.setdefault("inventory", {}).setdefault("backpack", []).append(replaced)
    modules.append(installed)
    return {
        **details,
        "installed": deepcopy(installed),
        "replaced": deepcopy(replaced),
    }


def _apply_artifact_action(data, target, details):
    from app.services.artifact_effects import needs_clocks, sync_clocks

    slot = _artifact_slot(target)
    if details["operation"] == "install":
        if slot is not None:
            raise ConflictError("В контейнере уже есть артефакт")
        parent, index = _artifact_source(data, details["item_path"])
        source = parent[index]
        installed = deepcopy(source)
        installed.update(quantity=1, slotType="artifact")
        if source.get("quantity", 1) > 1:
            source["quantity"] -= 1
            installed["id"] = str(uuid4())
        else:
            parent.pop(index)
            if not installed.get("id"):
                installed["id"] = str(uuid4())
        target["attributes"] = _container_attributes(target)
        target.setdefault("installedModules", []).append(installed)
        result = {"installed": deepcopy(installed), "replaced": None}
    else:
        if slot is None:
            raise ConflictError("Артефакт уже извлечён")
        parent, index = slot
        item = parent.pop(index)
        item.pop("slotType", None)
        data.setdefault("inventory", {}).setdefault("backpack", []).append(item)
        result = {"removed": deepcopy(item), "installed": None}
    if needs_clocks(data):
        data.setdefault("health", {})["effects"] = sync_clocks(data)
    return {**details, **result}


@atomic_operation
def change_outside_combat(character_id, user_id, payload):
    character = db.session.get(LobbyCharacter, character_id)
    if not character:
        raise NotFoundError("Персонаж не найден")
    CharacterService.check_access(character, user_id, edit=True)
    active = (
        LocationCombatState.query
        .join(LocationCharacter, LocationCharacter.location_id == LocationCombatState.location_id)
        .filter(
            LocationCharacter.character_id == character_id,
            LocationCombatState.status == "active",
        )
        .first()
    )
    if active:
        raise ValidationError("В бою используйте действие установки модуля с оплатой ОД")
    if type(payload.get("character_revision")) is not int or payload["character_revision"] != character.revision:
        raise ConflictError()
    recipient = None
    recipient_data = None
    if payload.get('target_character_id') is not None:
        from app.services.combat import CombatService

        if type(payload.get('location_id')) is not int:
            raise ValidationError('Выберите подлокацию для замены батареи')
        location = CombatService._get_location(payload['location_id'])
        is_gm = CombatService._ensure_access(location, user_id)
        actor = LocationCharacter.query.filter_by(location_id=location.id, character_id=character_id).first()
        if not actor or not CombatService._can_end_turn_for_character(actor, user_id, is_gm=is_gm):
            raise ValidationError('Нужен управляемый персонаж на этой подлокации')
        CombatService.ensure_character_can_act(actor, 'change_equipment_module')
        recipient = battery_replacement_recipient(actor, payload['target_character_id'])
        recipient_data = deepcopy(recipient.data)
    data = deepcopy(character.data or {})
    details = module_action_details(
        data,
        payload.get("operation"),
        payload.get("target_path"),
        payload.get("slot_type"),
        item_path=payload.get("item_path"),
        selection=payload.get("module_selection"),
        recipient_data=recipient_data,
    )
    result = apply_module_action(data, details, recipient_data=recipient_data)
    if recipient:
        recipient.data = recipient_data
        flag_modified(recipient, 'data')
        result['target_character_id'] = recipient.id
    character.data = data
    flag_modified(character, "data")
    return character, result
