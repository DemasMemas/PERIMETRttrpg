from __future__ import annotations

import difflib
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.addictions import addiction_profile
from app.services.consumable_effects import parse_consumable_effects


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

SECTION_HEADER_NAMES = {
    "Еда",
    "Кровь",
    "Обезболивающее",
    "Стимуляторы",
    "Восстановление здоровья",
    "Радиация",
    "Травмы",
}

CANONICAL_CONSUMABLE_NAMES = {
    "стимулятор мул": "Стимулятор Бык",
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _as_weight_float(value: Any, default: float = 0.0) -> float:
    from app.services.catalog_import import excel_fraction
    text = excel_fraction(value).replace(",", ".")
    if not text:
        return default
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            return float(left) / float(right)
        except (TypeError, ValueError, ZeroDivisionError):
            return default
    return _as_float(text, default)


def _normalize_consumable_name(value: Any) -> str:
    text = _normalize_text(value).lower()
    if text.endswith("]") and "[" in text:
        text = text[: text.rfind("[")].strip()
    for prefix in ("ампула ", "капельница "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    text = text.replace("регенаративный", "регенеративный")
    text = text.replace("б.о.л.т", "б.о.л.т.")
    return text


def _canonical_consumable_name(value: Any) -> str:
    name = _normalize_text(value)
    return CANONICAL_CONSUMABLE_NAMES.get(name.lower().replace("ё", "е"), name)


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _parse_signed_amount(text: str, pattern: str) -> Optional[float]:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    sign = -1 if match.group("sign") == "-" else 1
    raw = match.group("value").replace(",", ".")
    if "/" in raw:
        left, right = raw.split("/", 1)
        try:
            return sign * (float(left) / float(right))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        return sign * float(raw)
    except (TypeError, ValueError):
        return None


def _parse_duration(text: str, pattern: str) -> Optional[int]:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return _as_int(match.group(1), None)


def _build_effect(effect_type: str, value: Any, *, remaining: Optional[int] = None, tick: str = "turn_end") -> Dict[str, Any]:
    effect = {
        "type": effect_type,
        "name": effect_type,
        "value": _as_float(value, 0),
        "tick": tick,
        "active": True,
    }
    if remaining is not None:
        effect["remaining"] = max(0, _as_int(remaining, 0))
    return effect


def parse_consumable_profile(description: str) -> Dict[str, Any]:
    return parse_consumable_effects(description)


def _read_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: List[str] = []
    for si in root.findall("m:si", NS):
        parts: List[str] = []
        for text_node in si.iterfind(".//m:t", NS):
            parts.append(text_node.text or "")
        values.append("".join(parts))
    return values


def _read_sheet_rows(workbook_path: Path, sheet_name: str = "Расходники") -> List[Dict[str, str]]:
    from app.services.equipment_importer import _read_sheet_rows as read_rows
    return [row for row in read_rows(workbook_path, sheet_name, include_row_numbers=True)
            if 'A' not in row.get('__merged__', [])]


def parse_consumable_templates(workbook_path: Path) -> List[Dict[str, Any]]:
    rows = _read_sheet_rows(workbook_path, "Расходники")
    templates: List[Dict[str, Any]] = []
    current_section: Optional[str] = None

    for row in rows:
        name = _canonical_consumable_name(row.get("A"))
        col_b = _normalize_text(row.get("B"))
        col_c = _normalize_text(row.get("C"))
        col_d = _normalize_text(row.get("D"))
        col_e = _normalize_text(row.get("E"))
        description = _normalize_text(row.get("F"))

        if not name:
            continue
        if col_b == "Категория" and col_c == "Цена" and col_d == "Вес" and col_e == "Размер":
            current_section = name
            continue
        if not current_section:
            continue
        if name in SECTION_HEADER_NAMES and col_b == "Категория":
            current_section = name
            continue

        volume = _as_float(col_e, 0.0)
        if volume > 1000:
            volume = 1.0

        profile = parse_consumable_profile(description)
        profile["addiction"] = addiction_profile(name, _as_int(col_c, 0))
        template = {
            "name": name,
            "category": "consumable",
            "subcategory": current_section,
            "item_class": col_d or None,
            "description": description,
            "price": _as_int(col_c, 0),
            "weight": 0.0,
            "volume": volume,
            "attributes": {
                "section": current_section,
                "source_order": len(templates),
                "import_tier": col_b if col_b else None,
                "consumable": profile,
                "effects": profile["effects"],
                "uses": profile["direct"].get("uses"),
                "duration": profile["direct"].get("duration"),
                "delay": profile["direct"].get("delay"),
                "raw_description": description,
            },
            "compatible_ids": [],
        }
        templates.append(template)

    return templates


def parse_consumable_templates_v2(workbook_path: Path) -> List[Dict[str, Any]]:
    rows = _read_sheet_rows(workbook_path, "Расходники")
    templates: List[Dict[str, Any]] = []
    current_section: Optional[str] = None
    layout: Optional[str] = None

    def is_new_header(row: Dict[str, str]) -> bool:
        return (
            _normalize_text(row.get("B")) == "Вес, кг"
            and _normalize_text(row.get("C")) == "Категория"
            and _normalize_text(row.get("D")) == "Цена"
            and _normalize_text(row.get("E")) == "Размер"
        )

    def is_old_header(row: Dict[str, str]) -> bool:
        return (
            _normalize_text(row.get("B")) == "Категория"
            and _normalize_text(row.get("C")) == "Цена"
            and _normalize_text(row.get("D")) == "Вес"
            and _normalize_text(row.get("E")) == "Размер"
        )

    for row in rows:
        name = _canonical_consumable_name(row.get("A"))
        col_b = _normalize_text(row.get("B"))
        col_c = _normalize_text(row.get("C"))
        col_d = _normalize_text(row.get("D"))
        col_e = _normalize_text(row.get("E"))
        description = _normalize_text(row.get("F"))

        if not name:
            continue
        if is_new_header(row):
            current_section = name
            layout = "new"
            continue
        if is_old_header(row):
            current_section = name
            layout = "old"
            continue
        if not current_section:
            continue

        if layout == "new":
            weight = _as_weight_float(col_b, 0.0)
            item_class = col_c or None
            price = _as_int(col_d, 0)
            volume = _as_weight_float(col_e, 0.0)
            import_tier = col_c if col_c else None
        else:
            weight = _as_weight_float(col_d, 0.0)
            item_class = col_d or None
            price = _as_int(col_c, 0)
            volume = _as_weight_float(col_e, 0.0)
            import_tier = col_b if col_b else None

        from app.services.catalog_import import volume as parse_volume
        volume = parse_volume(col_e)

        profile = parse_consumable_profile(f"{name}. {description}")
        profile["addiction"] = addiction_profile(name, price)
        filter_charges = profile["direct"].get("filter_charges")
        is_gas_mask_filter = filter_charges is not None
        attributes = {
            "section": current_section,
            "catalog_name": name,
            "source_order": len(templates),
            "import_tier": import_tier,
            "consumable": profile,
            "is_alcohol": bool(profile["direct"].get("is_alcohol")),
            "effects": profile["effects"],
            "uses": profile["direct"].get("uses"),
            "duration": profile["direct"].get("duration"),
            "delay": profile["direct"].get("delay"),
            "raw_description": description,
        }
        if is_gas_mask_filter:
            attributes.update({
                "slot_type": "filter",
                "filter_charges": filter_charges,
                "durability": filter_charges,
                "max_durability": filter_charges,
                "install_action_points": profile['direct'].get('filter_install_action_points', 0),
            })
        templates.append(
            {
                "name": name,
                "category": "gas_mask_module" if is_gas_mask_filter else "consumable",
                "subcategory": "filter" if is_gas_mask_filter else current_section,
                "item_class": item_class,
                "description": description,
                "price": price,
                "weight": weight,
                "volume": volume,
                "attributes": attributes,
                "compatible_ids": [],
            }
        )

    return templates


def upsert_consumable_templates(workbook_path: str | Path, session=None) -> Dict[str, int]:
    path = Path(workbook_path).expanduser().resolve()
    templates = parse_consumable_templates_v2(path)
    session = session or db.session

    existing_by_section: Dict[str, List[ItemTemplate]] = {}
    existing_items = ItemTemplate.query.filter(
        ItemTemplate.category.in_(["consumable", "gas_mask_module"])
    ).all()
    for item in existing_items:
        section = (item.attributes or {}).get("section") or item.subcategory or ""
        existing_by_section.setdefault(section, []).append(item)

    inserted = 0
    updated = 0
    used_ids: set[int] = set()
    for data in templates:
        section = data["attributes"].get("section") or data["subcategory"] or ""
        candidates = existing_by_section.get(section, [])
        normalized_name = _normalize_consumable_name(data["name"])
        item = None
        best_score = 0.0
        matched_by_description = False
        for candidate in candidates:
            if candidate.id in used_ids:
                continue
            original_name = (candidate.attributes or {}).get("catalog_name")
            if original_name and _normalize_consumable_name(original_name) == normalized_name:
                item = candidate
                break
        if item is None and data["description"]:
            matching_descriptions = [
                candidate for candidate in candidates
                if candidate.id not in used_ids
                and (candidate.attributes or {}).get("raw_description") == data["description"]
            ]
            if matching_descriptions:
                item = next((
                    candidate for candidate in matching_descriptions
                    if (candidate.attributes or {}).get("source_order") == data["attributes"]["source_order"]
                ), matching_descriptions[0])
                matched_by_description = True
        for candidate in candidates if item is None else []:
            if candidate.id in used_ids:
                continue
            candidate_name = _normalize_consumable_name((candidate.attributes or {}).get("catalog_name") or candidate.name)
            if candidate_name == normalized_name:
                item = candidate
                break
            score = difflib.SequenceMatcher(None, candidate_name, normalized_name, autojunk=False).ratio()
            if score > best_score:
                best_score = score
                item = candidate

        if item is not None and not matched_by_description and _normalize_consumable_name((item.attributes or {}).get("catalog_name") or item.name) != normalized_name and best_score < 0.72:
            item = None

        if item is None:
            session.add(ItemTemplate(**data))
            inserted += 1
            continue
        used_ids.add(item.id)

        changed = False
        for field in ["name", "category", "subcategory", "item_class", "description", "price", "weight", "volume"]:
            value = data[field]
            if field == "name" and (
                (item.attributes or {}).get("catalog_name") not in (None, item.name)
                or (matched_by_description and _normalize_consumable_name(item.name) != normalized_name)
            ):
                continue
            if getattr(item, field) != value:
                setattr(item, field, value)
                changed = True
        if item.attributes != data["attributes"]:
            item.attributes = data["attributes"]
            changed = True
        if item.compatible_ids != data["compatible_ids"]:
            item.compatible_ids = data["compatible_ids"]
            changed = True
        if changed:
            updated += 1

    session.commit()
    return {
        "parsed": len(templates),
        "inserted": inserted,
        "updated": updated,
    }
