from copy import deepcopy
from typing import Any, Dict, Iterable, List, Tuple


FILTER_SLOT = "filter"


def _number(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _filter_capacity(item: Dict[str, Any]) -> float:
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    consumable = attributes.get("consumable") if isinstance(attributes.get("consumable"), dict) else {}
    direct = consumable.get("direct") if isinstance(consumable.get("direct"), dict) else {}
    return max(0, _number(
        item.get("maxDurability",
            attributes.get("max_durability",
                attributes.get("filter_charges", direct.get("filter_charges", 0)))),
    ))


def filter_charges(item: Dict[str, Any]) -> float:
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    return max(0, _number(
        item.get("durability", attributes.get("durability", _filter_capacity(item))),
    ))


def is_gas_mask_filter(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    consumable = attributes.get("consumable") if isinstance(attributes.get("consumable"), dict) else {}
    direct = consumable.get("direct") if isinstance(consumable.get("direct"), dict) else {}
    return bool(
        item.get("slotType") == FILTER_SLOT
        or attributes.get("slot_type") == FILTER_SLOT
        or (
            str(item.get("category") or "").lower() == "gas_mask_module"
            and str(item.get("subcategory") or "").lower() == FILTER_SLOT
        )
        or attributes.get("filter_charges") is not None
        or direct.get("filter_charges") is not None
    )


def _inventory_roots(character_data: Dict[str, Any]) -> Iterable[Any]:
    inventory = character_data.get("inventory") if isinstance(character_data.get("inventory"), dict) else {}
    equipment = character_data.get("equipment") if isinstance(character_data.get("equipment"), dict) else {}
    yielded = set()
    for root in (
        inventory.get("pockets"),
        (equipment.get("vest") or {}).get("pouches") if isinstance(equipment.get("vest"), dict) else None,
        (equipment.get("belt") or {}).get("pouches") if isinstance(equipment.get("belt"), dict) else None,
        inventory.get("backpack"),
    ):
        if root is not None and id(root) not in yielded:
            yielded.add(id(root))
            yield root
    for root in inventory.values():
        if root is not None and id(root) not in yielded:
            yielded.add(id(root))
            yield root


def _find_filter_in_node(node: Any) -> Tuple[List[Any], int, Dict[str, Any]] | None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            if is_gas_mask_filter(item) and filter_charges(item) > 0:
                return node, index, item
            found = _find_filter_in_node(item)
            if found:
                return found
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "installedModules":
                continue
            found = _find_filter_in_node(value)
            if found:
                return found
    return None


def _take_inventory_filter(character_data: Dict[str, Any]) -> Dict[str, Any] | None:
    found = next((
        result
        for root in _inventory_roots(character_data)
        if (result := _find_filter_in_node(root)) is not None
    ), None)
    if not found:
        return None
    container, index, source = found
    replacement = deepcopy(source)
    quantity = max(1, int(_number(source.get("quantity"), 1)))
    if quantity > 1:
        source["quantity"] = quantity - 1
        replacement["quantity"] = 1
    else:
        container.pop(index)

    charges = filter_charges(replacement)
    capacity = max(charges, _filter_capacity(replacement))
    replacement["category"] = "gas_mask_module"
    replacement["subcategory"] = FILTER_SLOT
    replacement["slotType"] = FILTER_SLOT
    replacement["durability"] = charges
    replacement["maxDurability"] = capacity
    replacement.pop("sourcePath", None)
    attributes = replacement.setdefault("attributes", {})
    attributes["slot_type"] = FILTER_SLOT
    attributes["durability"] = charges
    attributes["max_durability"] = capacity
    return replacement


def consume_equipped_filter_charges(character_data: Dict[str, Any], amount: float = 1) -> Dict[str, Any]:
    equipment = character_data.get("equipment") if isinstance(character_data.get("equipment"), dict) else {}
    from app.services.character_traits import passive_profile
    spent = max(0, _number(amount, 0))*passive_profile(character_data)['filter_usage_multiplier']
    result = {"changed": False, "consumed": 0, "removed": 0, "replaced": 0, "empty": 0}
    if spent <= 0:
        return result

    for slot in ("gasMask", "helmet"):
        item = equipment.get(slot)
        if not isinstance(item, dict):
            continue
        modules = item.get("installedModules")
        if not isinstance(modules, list):
            continue
        filter_index = next((
            index for index, module in enumerate(modules)
            if is_gas_mask_filter(module)
        ), None)
        if filter_index is None:
            continue

        installed = modules[filter_index]
        before = filter_charges(installed)
        after = max(0, before - spent)
        installed["durability"] = after
        installed.setdefault("attributes", {})["durability"] = after
        result["changed"] = True
        result["consumed"] += min(before, spent)
        if after > 0:
            continue

        result["empty"] += 1
        if item.get("autoReplaceFilters") is not True:
            continue
        modules.pop(filter_index)
        result["removed"] += 1
        replacement = _take_inventory_filter(character_data)
        if replacement:
            modules.append(replacement)
            result["replaced"] += 1

    return result
