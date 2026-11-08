"""Authoritative action-point cost for retrieving an inventory item."""

from math import isfinite

from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.exceptions import ValidationError
from app.services.magazine import inventory_slot


ACCESS_KEYS = (
    "accessActionPoints",
    "access_action_points",
    "retrievalActionPoints",
    "retrieval_action_points",
    "openActionPoints",
    "open_action_points",
    "caseAccessActionPoints",
    "case_access_action_points",
    "accessCostOd",
    "access_cost_od",
)


def _template(item):
    try:
        template_id = int((item or {}).get("templateId") or 0)
    except (TypeError, ValueError):
        template_id = 0
    return db.session.get(ItemTemplate, template_id) if template_id else None


def _attributes(item):
    template = _template(item)
    return {
        **((template.attributes or {}) if template else {}),
        **(((item or {}).get("attributes") or {})),
    }


def _value_at_path(data, path):
    value = data
    for key in path:
        if isinstance(value, dict) and isinstance(key, str) and key in value:
            value = value[key]
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
            value = value[key]
        else:
            raise ValidationError("Предмет перемещён. Выберите его заново.")
    return value


def _non_negative_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    return max(0, number)


def item_access_action_points(item):
    attributes = _attributes(item)
    for value in (
        (item or {}).get("accessActionPoints"),
        (item or {}).get("access_action_points"),
        *(attributes.get(key) for key in ACCESS_KEYS),
    ):
        number = _non_negative_number(value)
        if number is not None:
            return number
    return 0


def _item_category(item):
    template = _template(item)
    return str((item or {}).get("category") or (template.category if template else "")).strip().lower()


def _quick_access_category(item):
    template = _template(item)
    category = _item_category(item)
    if category != "consumable":
        return category
    attributes = _attributes(item)
    section = str(
        (item or {}).get("subcategory")
        or attributes.get("section")
        or (template.subcategory if template else "")
        or ""
    ).strip().lower()
    return "consumable" if section == "продукты" else "med"


def _pouch_allows(item, pouch):
    allowed = (
        (pouch or {}).get("allowed_categories")
        or (pouch or {}).get("allowedCategories")
        or ((pouch or {}).get("attributes") or {}).get("allowed_categories")
        or _attributes(pouch).get("allowed_categories")
    )
    if not isinstance(allowed, list) or not allowed:
        return True
    category = _quick_access_category(item)
    return category in {str(value).lower() for value in allowed}


def _skill_level(character_data, group, skill):
    value = (((character_data or {}).get("skills") or {}).get(group) or {}).get(skill) or {}
    try:
        base = float(value.get("base", value.get("value", 0)) or 0)
        bonus = float(value.get("bonus", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return base + bonus


def calculate_inventory_access(character_data, item_path, item=None):
    parent, index = inventory_slot(character_data or {}, item_path)
    stored_item = parent[index]
    if item is not None and stored_item != item:
        raise ValidationError("Расходник изменился. Выберите его заново.")
    item = stored_item
    path = item_path
    base_action_points = 0
    quick_access_discount = 0
    source = "unknown"
    from app.services.belt_attachments import accessible

    if path[:2] == ["inventory", "pockets"]:
        base_action_points = 1
        source = "pockets"
        from app.services.character_traits import passive_profile
        if not passive_profile(character_data)['pockets_quick_access']:base_action_points=2
    elif path[:2] == ["inventory", "backpack"]:
        base_action_points = 2
        source = "backpack"
    elif accessible(character_data, path, item):
        base_action_points = 1
        source = "belt_tool"
    elif len(path) >= 6 and path[0] == "equipment" and path[1] in {"belt", "vest"} and path[2] == "pouches":
        pouch = _value_at_path(character_data, path[:4])
        if _pouch_allows(item, pouch):
            base_action_points = 1
            quick_access_discount = 2 if _skill_level(character_data, "other", "tactics") >= 15 else 1
            source = "compatible_pouch"
        else:
            base_action_points = 2
            source = "incompatible_pouch"

    for position, part in enumerate(path):
        if part != "contents":
            continue
        if path[0] == "equipment" and len(path) > 2 and path[2] == "pouches" and position == 4:
            continue
        container = _value_at_path(character_data, path[:position])
        access_cost=item_access_action_points(container)
        if 'кейс' in str((container or {}).get('name','')).casefold() or _item_category(container)=='case':
            from app.services.character_traits import action_cost
            access_cost=action_cost(character_data,'case',access_cost)
        base_action_points += access_cost

    zones = (((character_data or {}).get("health") or {}).get("zones") or {})
    from app.services.prostheses import limb
    limb_penalty = int(any(
        isinstance(zones.get(area), dict) and _non_negative_number(zones[area].get("current")) == 0
        for area in ("leftArm", "rightArm") if not limb(character_data,area)
    ))
    retrieval = max(0, base_action_points - quick_access_discount + limb_penalty)
    return {
        "source": source,
        "base_action_points": base_action_points,
        "quick_access_discount": quick_access_discount,
        "limb_penalty": limb_penalty,
        "retrieval_action_points": retrieval,
        "use_action_discount": max(0, quick_access_discount - base_action_points),
    }
