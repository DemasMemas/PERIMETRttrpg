import math
import re
import uuid
from copy import deepcopy


AMMO_SLOT_LIMITS = {
    "9x18": 50,
    "9x19": 50,
    "9x21": 50,
    "45acp": 50,
    "545x39": 50,
    "556x45": 50,
    "18x45": 50,
    "57x28": 50,
    "762x25": 50,
    "12x70": 25,
    "762x39": 25,
    "762x51": 25,
    "762x54": 25,
    "127x55": 25,
    "9x39": 25,
    "sp4": 25,
}


def inventory_item_volume(item):
    if not isinstance(item, dict):
        return math.inf
    try:
        volume = float(item.get('volume') or 0)
        quantity = float(item.get('quantity') if item.get('quantity') is not None else 1)
    except (TypeError, ValueError):
        return math.inf
    if not math.isfinite(volume) or volume < 0 or not math.isfinite(quantity) or quantity < 0:
        return math.inf
    total = volume * quantity
    contents = item.get('contents')
    if isinstance(contents, list):
        total += sum(inventory_item_volume(child) for child in contents)
    return total


def store_removed_equipment(data, item):
    from app.services.character_traits import passive_profile

    inventory = data.setdefault('inventory', {})
    if not isinstance(inventory.get('pockets'), list):
        inventory['pockets'] = []
    pockets = inventory['pockets']
    if not (data.get('equipment') or {}).get('backpack'):
        capacity = max(0, 10 + passive_profile(data)['pocket_cells'])
        if sum(inventory_item_volume(existing) for existing in pockets) + inventory_item_volume(item) <= capacity + 1e-7:
            pockets.append(item)
            return
    if not isinstance(inventory.get('backpack'), list):
        inventory['backpack'] = []
    inventory['backpack'].append(item)


def normalize_caliber(value):
    text = (
        str(value or "")
        .strip()
        .lower()
        .replace("аср", "acp")
        .replace("сп", "sp")
    )
    text = re.sub(r"[xхХ×*]", "x", text)
    return re.sub(r"[^0-9a-zа-яёx]+", "", text)


def ammo_slot_limit(item):
    if not isinstance(item, dict) or str(item.get("category") or "").lower() != "ammo":
        return None
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    caliber = (
        attributes.get("caliber")
        or item.get("caliber")
        or item.get("subcategory")
        or attributes.get("ammo_group")
    )
    return AMMO_SLOT_LIMITS.get(normalize_caliber(caliber))


def _update_ammo_stack_weight(item):
    quantity = max(0, int(item.get("quantity") or 0))
    if quantity <= 0:
        item["weight"] = 0
        return
    try:
        single_volume = float(item.get("volume") or 0.02)
    except (TypeError, ValueError):
        single_volume = 0.02
    item["weight"] = 0.1 if single_volume * quantity < 0.5 else 0.25


def split_ammo_stack(item):
    if not isinstance(item, dict) or str(item.get("category") or "").lower() != "ammo":
        return [item]
    limit = ammo_slot_limit(item)
    try:
        quantity = max(0, int(float(item.get("quantity", 1) or 0)))
    except (TypeError, ValueError):
        quantity = 1
    if not limit or quantity <= limit:
        item["quantity"] = quantity
        if limit:
            _update_ammo_stack_weight(item)
        return [item] if quantity > 0 else []

    stacks = []
    remaining = quantity
    while remaining > 0:
        stack = item if not stacks else deepcopy(item)
        stack["quantity"] = min(limit, remaining)
        if stacks:
            stack["id"] = f"item_{uuid.uuid4().hex}"
        _update_ammo_stack_weight(stack)
        stacks.append(stack)
        remaining -= stack["quantity"]
    return stacks


def _normalize_item_list(items):
    if not isinstance(items, list):
        return
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        if isinstance(item.get("contents"), list):
            _normalize_item_list(item["contents"])
        normalized.extend(split_ammo_stack(item))
    items[:] = normalized


def normalize_inventory_ammo_stacks(character_data):
    if not isinstance(character_data, dict):
        return character_data
    inventory = character_data.get("inventory")
    if isinstance(inventory, dict):
        _normalize_item_list(inventory.get("pockets"))
        _normalize_item_list(inventory.get("backpack"))
    equipment = character_data.get("equipment")
    if isinstance(equipment, dict):
        for section in ("belt", "vest"):
            holder = equipment.get(section)
            pouches = holder.get("pouches") if isinstance(holder, dict) else None
            if not isinstance(pouches, list):
                continue
            for pouch in pouches:
                if isinstance(pouch, dict):
                    _normalize_item_list(pouch.get("contents"))
    return character_data
