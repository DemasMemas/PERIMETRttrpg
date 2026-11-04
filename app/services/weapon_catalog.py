"""Совместимость старых оружейных шаблонов с правилами навесных модулей."""

from copy import deepcopy
from datetime import datetime, timedelta
import re


def is_standard_nvg(item):
    return re.sub(r'\s+','',str((item or {}).get('name') or '')).casefold() in {'пнв-1п','сидр-1г','supernova'}


def parse_weapon_size(value):
    raw = str(value if value is not None else "").strip().replace(",", ".")
    # Excel превращает размеры вроде 8/7 в дату 8 июля. В XML остаётся
    # серийный номер; для оружейного размера восстанавливаем день/месяц.
    try:
        serial = float(raw)
        if serial.is_integer() and 20000 <= serial <= 80000:
            date = datetime(1899, 12, 30) + timedelta(days=int(serial))
            raw = f"{date.day}/{date.month}"
    except ValueError:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", raw):
            try:
                date = datetime.fromisoformat(raw)
                raw = f"{date.day}/{date.month}"
            except ValueError:
                pass
    pair = re.fullmatch(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", raw)
    if pair:
        options = [float(part) for part in pair.groups()]
        return {"size": options[0], "size_raw": raw, "size_options": options}
    try:
        return {"size": float(raw), "size_raw": raw}
    except ValueError:
        return {"size": 0, "size_raw": raw}


def _repair_size_fields(item):
    attributes = item.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    for container in (item, attributes):
        for field in ("volume", "size"):
            if container.get(field) is None:
                continue
            profile = parse_weapon_size(container[field])
            # Числовые значения и ручные размеры не переписываем.
            if "size_options" in profile:
                container[field] = profile["size"]
                attributes.update(profile)
    if attributes:
        item["attributes"] = attributes
    return item


def normalize_character_weapons(data):
    """Меняет только переданный снимок снаряжения, не исходную запись БД."""
    def visit(value, weapon=False):
        if isinstance(value, list):
            for child in value:
                visit(child, weapon)
        elif isinstance(value, dict):
            if is_standard_nvg(value):value.update(weight=1,volume=1)
            if weapon or value.get("category") == "weapon":
                _repair_size_fields(value)
            for key in ("contents", "pouches", "installedModules", "storedItem", "installedMagazine"):
                visit(value.get(key))
    visit(data.get("weapons"), True)
    inventory = data.get("inventory") or {}
    visit(inventory.get("backpack"))
    visit(inventory.get("pockets"))
    for item in (data.get("equipment") or {}).values():
        visit(item)
    return data


def attachment_slots(subcategory, configured=None):
    slots = deepcopy(configured) if isinstance(configured, list) else [
        {"type": "scope", "label": "Прицел", "maxItems": 1},
        {"type": "barrel", "label": "Ствол", "maxItems": 1},
        {"type": "handguard", "label": "Цевье", "maxItems": 1},
    ]
    if re.search(r"гранатом[её]т|grenade.launcher", str(subcategory or ""), re.I):
        slots = [slot for slot in slots if slot.get("type") != "barrel"]
    return slots


def normalize_template(data):
    if is_standard_nvg(data):data={**data,'weight':1,'volume':1}
    if data.get('category') == 'weapon_module':
        from app.services.weapon_modules import normalize_module_template
        return normalize_module_template(data)
    if data.get("category") != "weapon":
        return data
    data = deepcopy(data)
    _repair_size_fields(data)
    attributes = data.get("attributes") or {}
    data["attributes"] = attributes
    attributes["slots"] = attachment_slots(data.get("subcategory"), attributes.get("slots"))
    return data
