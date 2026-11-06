from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.weapon_catalog import attachment_slots, parse_weapon_size


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        text = re.sub(r"[\s\u00a0]+", "", str(value)).replace(",", ".")
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        text = re.sub(r"[\s\u00a0]+", "", str(value)).replace(",", ".")
        if text in {"---", "-", "Нет"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _parse_penetration(value: Any, default: float = 0.0) -> float:
    text = _normalize_text(value)
    if not text or text in {"---", "-", "Нет"}:
        return default
    text = text.replace("%", "").replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _ammo_variant_label_from_text(text: str) -> Optional[str]:
    lowered = _normalize_text(text).lower()
    if not lowered or lowered in {"нет", "---", "-"}:
        return None

    if "убп" in lowered:
        return "ubp"
    if "rip" in lowered:
        return "rip"
    if "бронеб" in lowered or re.search(r"\bбп\b", lowered):
        return "bp"
    if "экспанс" in lowered or re.search(r"\bэп\b", lowered):
        return "ep"
    if "разрыв" in lowered or "взрыв" in lowered:
        return "explosive"
    if "зажиг" in lowered:
        return "incendiary"
    if "светошум" in lowered:
        return "flashbang"
    if "дым" in lowered:
        return "smoke"
    if "газ" in lowered:
        return "gas"
    return None


def _ammo_variants_from_text(text: Any) -> List[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []

    lowered = normalized.lower()
    if lowered in {"нет", "---", "-"}:
        return []
    if lowered == "есть":
        return []

    variants: List[str] = []
    for part in re.split(r"[,/;\n]+", normalized):
        variant = _ammo_variant_label_from_text(part)
        if variant and variant not in variants:
            variants.append(variant)
    return variants


def _detect_ammo_variants(row: Dict[str, str]) -> List[str]:
    variants: List[str] = []
    for key in ("P", "S"):
        text = _normalize_text(row.get(key))
        if not text:
            continue
        lowered = text.lower()
        if lowered == "есть":
            if key == "P":
                candidates = ["bp", "ep", "ubp", "rip"]
            else:
                candidates = ["incendiary", "explosive"]
        else:
            candidates = _ammo_variants_from_text(text)
        for variant in candidates:
            if variant not in variants:
                variants.append(variant)
    return variants


def _looks_like_grenade_ammo(row: Dict[str, str]) -> bool:
    searchable = " ".join(_normalize_text(row.get(key)) for key in ("A", "K", "L", "V", "P", "S"))
    lowered = searchable.lower()
    grenade_markers = (
        "гранат",
        "вог",
        "ргд",
        "ф-1",
        "ф1",
        "рго",
        "ргн",
        "ог-",
        "молотов",
        "m67",
        "m26",
    )
    return any(marker in lowered for marker in grenade_markers)


def _normalize_equipment_name(value: Any) -> str:
    text = _normalize_text(value).lower()
    text = text.replace("*", "x")
    text = text.replace("х", "x")
    text = text.replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def _equipment_alias(value: Any) -> str:
    text = _normalize_equipment_name(value)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    # Workbook aliases such as "ППСП5" refer to weapons named "ПП СП5".
    text = re.sub(r"^пп(?=[a-zа-я]*\d)", "пп ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^(пистолет|револьвер|дробовик|автомат|пп|пулемет|ручной пулемет|"
        r"магазин|снайперский|самозарядный карабин)\s+",
        "",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def _weapon_aliases(name: Any) -> set[str]:
    normalized = _normalize_text(name)
    aliases = {_equipment_alias(normalized)}
    aliases.update(_equipment_alias(part) for part in re.findall(r"\(([^)]+)\)", normalized))
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", normalized.split("(", 1)[0])
    aliases.update(
        _equipment_alias(word)
        for word in words
        if len(word) > 1 and word.isupper()
    )
    number = re.search(r"(\d+(?:[-\s]\d+)*)\s*$", normalized.split("(", 1)[0])
    if len(words) >= 2:
        acronym = "".join(word if len(word) <= 3 and word.isupper() else word[0] for word in words)
        if number:
            acronym += " " + number.group(1)
        aliases.add(_equipment_alias(acronym))
    return {alias for alias in aliases if alias}


def _parse_shared_strings(archive: zipfile.ZipFile) -> List[str]:
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


def _read_sheet_rows(workbook_path: Path, sheet_name: str, *, include_row_numbers=False) -> List[Dict[str, str]]:
    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings = _parse_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

        target = None
        for sheet in workbook.findall(".//m:sheet", NS):
            if sheet.attrib.get("name") == sheet_name:
                rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
                from posixpath import normpath
                relative_target = rel_targets[rel_id]
                target = relative_target.lstrip('/') if relative_target.startswith('/') else normpath('xl/' + relative_target)
                break
        if not target:
            raise ValueError(f"Sheet '{sheet_name}' was not found in workbook")

        root = ET.fromstring(archive.read(target))
        rows: List[Dict[str, str]] = []
        by_number = {}
        for row in root.findall(".//m:sheetData/m:row", NS):
            parsed: Dict[str, str] = {}
            if include_row_numbers:
                parsed['__row__'] = int(row.attrib['r'])
            for cell in row.findall("m:c", NS):
                ref = cell.attrib.get("r", "")
                column = "".join(ch for ch in ref if ch.isalpha())
                value = ""
                raw_v = cell.find("m:v", NS)
                inline = cell.find("m:is", NS)
                if raw_v is not None and raw_v.text is not None:
                    value = raw_v.text
                    if cell.attrib.get("t") == "s" and value.isdigit():
                        value = shared_strings[int(value)]
                elif inline is not None:
                    parts: List[str] = []
                    for text_node in inline.iterfind(".//m:t", NS):
                        parts.append(text_node.text or "")
                    value = "".join(parts)
                parsed[column] = value
            rows.append(parsed)
            by_number[int(row.attrib['r'])] = parsed
        # Вертикально объединённая ячейка относится ко всем строкам группы.
        # Например, совместимость магазинов и размеры приборов заданы один раз.
        for merged in root.findall('.//m:mergeCell', NS):
            match = re.fullmatch(r'([A-Z]+)(\d+):\1(\d+)', merged.attrib.get('ref', ''))
            if not match:
                continue
            column, start, end = match[1], int(match[2]), int(match[3])
            value = by_number.get(start, {}).get(column)
            if value in (None, ''):
                continue
            for number in range(start + 1, end + 1):
                destination = by_number.setdefault(number, {'__row__': number} if include_row_numbers else {})
                if destination.get(column) in (None, ''):
                    destination[column] = value
                    if include_row_numbers:
                        destination.setdefault('__merged__', []).append(column)
        return [by_number[number] for number in sorted(by_number)]


def _parse_od(text: str) -> Optional[int]:
    match = re.search(r"(\d+)", text or "")
    if not match:
        return None
    return int(match.group(1))


def _parse_name_list(text: str) -> List[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    parts = re.split(r"[,/\n]+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _canonical_caliber(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = re.sub(r"^граната\s+", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("х", "x").replace("*", "x")
    normalized = re.sub(r"аср", "ACP", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace(" ,", ",").replace("..", ".")
    normalized = re.sub(r"\s+", " ", normalized)
    aliases = {
        "вог-25": "ВОГ-25",
        "ог-12": "ОГ-12",
        "n-101-2": "N-101-2",
    }
    normalized = aliases.get(normalized.lower(), normalized)
    return normalized


def _magazine_volume(value: Any, name: Any = "") -> float:
    if "клипс" in _normalize_text(name).lower():
        return 0.25
    volume = _as_float(value, 0.0)
    return volume if 0 <= volume <= 100 else 1.0


def _ammo_damage_from_row(row: Dict[str, str]) -> Optional[float]:
    damage_raw = _normalize_text(row.get("O"))
    if damage_raw in {"", "---", "Нет", "Смотрите другую таблицу"}:
        return None
    return _as_float(damage_raw, 0.0)


def _parse_weight(value: Any) -> float:
    match = re.search(r"-?\d+(?:[.,]\d+)?", _normalize_text(value))
    return _as_float(match.group(0), 0.0) if match else 0.0


def _parse_first_int(value: Any, default: int = 0) -> int:
    match = re.search(r"-?\d+", _normalize_text(value))
    return int(match.group(0)) if match else default


def _parse_burst_profile(value: Any) -> Dict[str, Any]:
    raw = _normalize_text(value)
    lowered = raw.lower()
    duplex_match = re.search(r"одиночн\w*\s*-\s*(\d+)", lowered)
    burst_match = re.search(r"очеред\w*\s*(\d+)", lowered)
    plain_burst_match = re.match(r"^\s*(\d+)", lowered)
    duplex_size = int(duplex_match.group(1)) if duplex_match else None
    burst_size = None
    if burst_match:
        burst_size = int(burst_match.group(1))
    elif plain_burst_match and not duplex_size:
        burst_size = int(plain_burst_match.group(1))

    is_machine_gun = "пулеметн" in lowered or "пулемётн" in lowered
    supports_burst = bool(burst_size or is_machine_gun)
    single_options = [1]
    if duplex_size and duplex_size not in single_options:
        single_options.append(duplex_size)

    penalty_match = re.search(r"штраф\D*(\d+)", lowered)
    return {
        "raw": raw,
        "single_shot_options": single_options,
        "duplex_size": duplex_size,
        "burst_size": burst_size,
        "burst_penalty": int(penalty_match.group(1)) if penalty_match else None,
        "machine_gun_burst": is_machine_gun,
        "supports_burst": supports_burst,
        "supports_suppression": supports_burst,
        "supports_area_fire": supports_burst,
    }


def _protection(row: Dict[str, str], columns: tuple[str, str, str, str, str]) -> Dict[str, float]:
    return {
        key: round(_as_float(row.get(column), 0.0), 4)
        for key, column in zip(("physical", "chemical", "thermal", "electric", "radiation"), columns)
    }


def _parse_ranged_weapons(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    categories = {
        "Пистолеты",
        "Дробовики",
        "Пистолеты-пулеметы",
        "Штурмовые винтовки и карабины",
        "Снайперские винтовки",
        "Гранатометы",
        "Пулемёты",
    }
    templates: List[Dict[str, Any]] = []
    subcategory = ""
    for row in rows:
        name = _normalize_text(row.get("B"))
        if name in categories:
            subcategory = name
            continue
        if not subcategory or not name or name == "Параметр минимальной силы для оружия":
            continue
        if _normalize_text(row.get("Q")) not in {"1", "2", "3", "Н"}:
            continue

        burst_profile = _parse_burst_profile(row.get("I"))
        magazine_raw = _normalize_text(row.get("C"))
        damage_raw = _normalize_text(row.get("J"))
        attributes = {
            "import_source": "equipment_workbook",
            "magazine_size": _parse_first_int(magazine_raw),
            "magazine_size_raw": magazine_raw,
            "accuracy": _as_int(row.get("D")),
            "noise": _as_int(row.get("E")),
            "caliber": _canonical_caliber(row.get("F")),
            "range": _as_int(row.get("G")),
            "ergonomics": _as_int(row.get("H")),
            "burst": burst_profile["raw"],
            "fire_modes": burst_profile,
            "damage": _as_float(damage_raw) if "/" not in damage_raw else damage_raw,
            "damage_raw": damage_raw,
            "max_durability": _as_int(row.get("K"), 100),
            "fire_rate": _as_int(row.get("L")),
            "min_strength": _as_int(row.get("N")),
            **parse_weapon_size(row.get("P")),
            "weapon_class": _normalize_text(row.get("Q")),
            "special_rules": _normalize_text(row.get("R")),
            "fixedMagazine": False,
            "slots": attachment_slots(subcategory),
            "raw_row": row,
        }
        templates.append(
            {
                "name": name,
                "category": "weapon",
                "subcategory": subcategory,
                "item_class": attributes["weapon_class"],
                "description": attributes["special_rules"],
                "price": _as_int(row.get("O")),
                "weight": _as_float(row.get("M")),
                "volume": float(attributes["size"]),
                "attributes": attributes,
                "compatible_ids": [],
            }
        )
    return templates


def _finalize_weapon_magazine_attributes(template: Dict[str, Any], fixed_magazine: bool) -> None:
    attributes = template.get("attributes")
    if not isinstance(attributes, dict):
        return
    attributes["fixedMagazine"] = fixed_magazine
    attributes.pop("magazine", None)
    attributes.pop("magazine_size_raw", None)
    if not fixed_magazine:
        attributes.pop("magazine_size", None)


MELEE_PRICE_OVERRIDES = {
    "Спиральный нож": 3500,
    "Топор": 5000,
    "Сабля": 8000,
    "Кортик": 9000,
    "Кувалда": 10000,
    "Меч": 10000,
}


def _parse_melee_weapons(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    templates: List[Dict[str, Any]] = []
    ignored = {"Название", "Ваши руки", "Ваш приклад"}
    for row in rows:
        name = _normalize_text(row.get("S"))
        damage_raw = _normalize_text(row.get("U"))
        if not name or name in ignored or not damage_raw:
            continue
        allowed_attacks = []
        for column in ("AC", "AD", "AE", "AF", "AG", "AH"):
            attack = _normalize_text(row.get(column))
            if attack and attack != "-" and attack not in allowed_attacks:
                allowed_attacks.append(attack)
        if name == "Томагавк":
            allowed_attacks = [
                attack for attack in allowed_attacks
                if "режущ" not in attack.lower().replace("ё", "е")
            ]
        if not allowed_attacks and name != "Нож стреляющий":
            continue
        description = _normalize_text(row.get("T"))
        normalized_description = description.lower().replace("ё", "е")
        if "очень тяжел" in normalized_description:
            weight_class = "Очень тяжелое"
        elif "тяжел" in normalized_description:
            weight_class = "Тяжелое"
        elif "легк" in normalized_description:
            weight_class = "Легкое"
        elif name == "Спиральный нож":
            weight_class = "Легкое"
        else:
            weight_class = None
        penetration_match = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%", description)
        templates.append(
            {
                "name": name,
                "category": "melee_weapon",
                "subcategory": "Оружие ближнего боя",
                "item_class": None,
                "description": description,
                "price": MELEE_PRICE_OVERRIDES.get(name, _as_int(row.get("W"))),
                "weight": _parse_weight(row.get("Y")),
                "volume": float(_as_int(row.get("Z"))),
                "attributes": {
                    "import_source": "equipment_workbook",
                    "damage": _as_float(damage_raw),
                    "accuracy": _as_int(row.get("V")),
                    "armor_piercing": _as_float(penetration_match.group(1)) if penetration_match else 0.0,
                    "bleeding": _normalize_text(row.get("X")),
                    "size": _normalize_text(row.get("Z")),
                    "weight_class": weight_class,
                    "allowed_attacks": allowed_attacks,
                    "max_durability": 100,
                    "raw_row": row,
                },
                "compatible_ids": [],
            }
        )
    return templates


def _armor_protection_zones(name: str) -> List[str]:
    normalized = _normalize_text(name).lower().replace("ё", "е")
    torso_only = {"армейский бронежилет"}
    torso_and_arms = {
        "кожаная куртка",
        "бандитская куртка",
        "броня путника",
    }
    includes_head = {
        "костюм химзащиты",
        "комбинезон купол",
        "комбинезон купол м",
        "комбинезон купол-м",
        "комбинезон гроб",
        "экзоскелет",
    }
    if normalized in torso_only:
        return ["torso"]
    if normalized in torso_and_arms:
        return ["torso", "arms"]
    if normalized in includes_head:
        return ["torso", "arms", "legs", "head"]
    return ["torso", "arms", "legs"]


def _integrated_helmet_profile(name: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_text(name).lower().replace("ё", "е")
    profiles = {
        "костюм химзащиты": {"physical": 0.0, "charisma_penalty": 2, "accuracy_penalty": 2},
        "комбинезон купол": {"physical": 0.1, "charisma_penalty": 3, "accuracy_penalty": 3},
        "комбинезон купол м": {"physical": 0.35, "charisma_penalty": 2, "accuracy_penalty": 3},
        "комбинезон купол-м": {"physical": 0.35, "charisma_penalty": 2, "accuracy_penalty": 3},
        "комбинезон гроб": {"physical": 0.4, "charisma_penalty": 4, "accuracy_penalty": 4},
        "экзоскелет": {
            "physical_formula": "armor_physical_minus_10_percent",
            "charisma_penalty": 0,
            "accuracy_penalty": 2,
        },
    }
    profile = profiles.get(normalized)
    return dict(profile) if profile else None


def _integrated_helmet_name(name: str) -> Optional[str]:
    normalized = _normalize_text(name).lower().replace("ё", "е")
    names = {
        "костюм химзащиты": "Шлем Костюма Химзащиты",
        "комбинезон купол": "Шлем Купол",
        "комбинезон купол м": "Шлем Купол-М",
        "комбинезон купол-м": "Шлем Купол-М",
        "комбинезон гроб": "Шлем ГРОБ",
        "экзоскелет": "Шлем Экзоскелета",
    }
    return names.get(normalized)


def _parse_armor(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    templates: List[Dict[str, Any]] = []
    for row in rows[2:20]:
        name = _normalize_text(row.get("B"))
        if not name:
            continue
        weight_penalty = _parse_first_int(row.get("M"))
        attributes = {
            "import_source": "equipment_workbook",
            "max_durability": _as_int(row.get("C"), 1),
            "protection": _protection(row, ("D", "E", "F", "G", "H")),
            "material": _normalize_text(row.get("I")),
            "movement_penalty": _parse_first_int(row.get("J")),
            "container_slots": _as_int(row.get("K")),
            "inventory_weight_penalty": weight_penalty,
            "modification_category": _normalize_text(row.get("P")),
            "protection_zones": _armor_protection_zones(name),
            "raw_row": row,
        }
        if "head" in attributes["protection_zones"]:
            attributes["integrated_helmet"] = True
            attributes["integrated_helmet_profile"] = _integrated_helmet_profile(name)
            attributes["integrated_helmet_name"] = _integrated_helmet_name(name)
        if _normalize_text(name).lower().replace("ё", "е") == "экзоскелет":
            attributes.update({
                "is_exoskeleton": True,
                "powered": False,
                "requires_exoskeleton_battery": True,
                "slots": [{"type": "exoskeleton_battery", "label": "Аккумулятор", "maxItems": 1}],
                "movement_penalty_mode": "set",
                "ignore_weight_penalty_when_powered": True,
            })
        templates.append(
            {
                "name": name,
                "category": "armor",
                "subcategory": _normalize_text(row.get("P")),
                "item_class": _normalize_text(row.get("N")),
                "description": _normalize_text(row.get("M")),
                "price": _as_int(row.get("O")),
                "weight": float(weight_penalty),
                "volume": _as_float(row.get("L")),
                "attributes": attributes,
                "compatible_ids": [],
            }
        )
    return templates


def _parse_helmets(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    templates: List[Dict[str, Any]] = []
    header=next((r for r in rows if _normalize_text(r.get('B')) in {'Шлема','Шлемы'}),{})
    modern='категор' in _normalize_text(header.get('M')).casefold() and 'цена' in _normalize_text(header.get('N')).casefold()
    columns=({'accuracy':'J','ergonomics':'K','charisma':'L','class':'M','price':'N','movement':'O','volume':'P'} if modern
        else {'accuracy':'K','ergonomics':'L','charisma':'M','class':'N','price':'O','movement':'P','volume':'J'})
    for row in rows[22:45]:
        name = _normalize_text(row.get("B"))
        if not name:
            continue
        name = re.sub(r"^Прочее\s+", "", name, flags=re.IGNORECASE)
        is_gas_mask = name.lower().startswith(("противогаз", "респиратор"))
        requires_filter = _normalize_text(row.get("A")) == "Противогазо-шлем"
        integrated_visor = name.lower().startswith("шлем") and 'face' in _helmet_protection_zones(name)
        material = _normalize_text(row.get("I"))
        charisma_bonus = _normalize_text(row.get(columns['charisma']))
        if name.lower().endswith("ушанка"):
            charisma_bonus = "1.5"
        slots = []
        if is_gas_mask or requires_filter:
            slots.append({"type": "filter", "label": "Фильтр", "maxItems": 1})
        templates.append(
            {
                "name": name,
                "category": "gas_mask" if is_gas_mask else "helmet",
                "subcategory": _normalize_text(row.get("I")),
                "item_class": _normalize_text(row.get(columns['class'])),
                "description": _normalize_text(row.get("A")),
                "price": _as_int(row.get(columns['price'])),
                "weight": _parse_weight(row.get("Q")),
                "volume": _as_float(row.get(columns['volume'])),
                "attributes": {
                    "import_source": "equipment_workbook",
                    "max_durability": _as_int(row.get("C"), 1),
                    "protection": _protection(row, ("D", "E", "F", "G", "H")),
                    "armor_type": _normalize_text(row.get("I")),
                    "material": material,
                    "accuracy_penalty": _normalize_text(row.get(columns['accuracy'])),
                    "ergonomics_penalty": _normalize_text(row.get(columns['ergonomics'])),
                    "charisma_bonus": charisma_bonus,
                    "movement_penalty": _as_int(row.get(columns['movement'])),
                    "soft_headwear":material.casefold()=='текстиль' and not is_gas_mask and not requires_filter,
                    "modification_category":('Ушанка' if name.casefold()=='ушанка' else 'Противогазы' if is_gas_mask else
                        'Шлема-противогазы' if requires_filter else 'Шлема с забралом' if integrated_visor else 'Шлема без забрала'),
                    "requires_filter": requires_filter,
                    "integrated_visor": integrated_visor,
                    "slots": slots,
                    "protection_zones": _helmet_protection_zones(name),
                    "raw_row": row,
                },
                "compatible_ids": [],
            }
        )

    return templates


def _helmet_protection_zones(name: str) -> List[str]:
    normalized = _normalize_text(name).lower().replace("ё", "е")
    crown_and_back = {"советский котелок 68г", "шлем ударник"}
    crown_back_and_ears = {
        "шлем витязь 4в76",
        "шлем ударник-м",
        "шлем первопроходец",
    }
    if normalized in crown_and_back:
        return ["crown", "back"]
    if normalized in crown_back_and_ears:
        return ["crown", "back", "ears"]
    return ["crown", "back", "ears", "face"]


def _parse_exoskeleton_battery(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    for row in rows:
        name = _normalize_text(row.get("A"))
        if name.lower().replace("ё", "е") != "аккумуляторы экзоскелета":
            continue
        return [{
            "name": name,
            "category": "exoskeleton_module",
            "subcategory": "battery",
            "item_class": None,
            "description": _normalize_text(row.get("E")),
            "price": _as_int(row.get("C")),
            "weight": _as_float(row.get("B")),
            "volume": _as_float(row.get("D")),
            "attributes": {
                "import_source": "equipment_workbook",
                "slot_type": "exoskeleton_battery",
                "charge_days": 1,
                "remaining_days": 1,
                "raw_row": row,
            },
            "compatible_ids": [],
        }]
    return []


def _parse_tools(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    profiles = {
        "набор смазочных приспособлений": {
            "kind": "weapon", "repair_amount": 10, "duration_minutes": 5,
            "minimum_durability": 75, "engineering_min": 0, "consumed_on_use": True,
            "can_lubricate_prostheses": True,
        },
        "набор инструментов оружейника (упрощеные) 15/15": {
            "kind": "weapon", "repair_amount": 5, "duration_minutes": 30,
            "minimum_durability": 75, "engineering_min": 10, "max_item_class": 2,
        },
        "набор инструментов оружейника (стандартные) 25/25": {
            "kind": "weapon", "repair_amount": 10, "duration_minutes": 30,
            "minimum_durability": 45, "engineering_min": 13, "max_item_class": 3,
        },
        "набор инструментов оружейника (расширенные) 40/40": {
            "kind": "weapon", "repair_amount": 20, "duration_minutes": 30,
            "minimum_durability": 20, "engineering_min": 17,
        },
        "полевой ремкомплект для брони": {
            "kind": "armor_current_stage", "duration_minutes": 5, "engineering_min": 0,
            "consumed_on_use": True,
        },
        "набор инструментов бронника (упрощеные) 10/10": {
            "kind": "armor", "repair_stages": 1, "duration_minutes": 30,
            "maximum_damage_stage": 3, "engineering_min": 11, "max_item_class": 2,
        },
        "набор инструментов бронника (стандартные) 20/20": {
            "kind": "armor", "repair_stages": 1, "duration_minutes": 30,
            "maximum_damage_stage": 4, "engineering_min": 14, "max_item_class": 3,
        },
        "набор инструментов бронника (расширенные) 35/35": {
            "kind": "armor", "repair_stages": 2, "duration_minutes": 30,
            "maximum_damage_stage": 5, "engineering_min": 17,
        },
        "набор восстановления инструментов": {
            "kind": "restore_tool", "restore_fraction": 0.5, "duration_minutes": 0,
        },
    }
    profiles = {_normalize_equipment_name(name): profile for name, profile in profiles.items()}
    templates: List[Dict[str, Any]] = []
    section_starts = [
        index for index, row in enumerate(rows)
        if _normalize_text(row.get("A")) == "Инструменты"
    ]
    if not section_starts:
        return templates
    for row in rows[section_starts[-1] + 1:]:
        name = _normalize_text(row.get("A"))
        if name == "Вообще другое":
            break
        if not name:
            continue
        normalized = _normalize_equipment_name(name)
        uses_match = re.search(r"(\d+)\s*/\s*\d+\s*$", normalized)
        attributes: Dict[str, Any] = {
            "import_source": "equipment_workbook",
            "section": "Инструменты",
            "raw_row": row,
        }
        if uses_match:
            attributes["uses"] = _as_int(uses_match.group(1), 0)
        profile = profiles.get(normalized)
        if profile:
            attributes["repair_profile"] = profile
            attributes["usable"] = True
            if profile.get("consumed_on_use"):
                attributes["uses"] = 1
        templates.append({
            "name": name,
            "category": "tool",
            "subcategory": "Инструменты",
            "item_class": None,
            "description": _normalize_text(row.get("E")),
            "price": _as_int(row.get("C")),
            "weight": _as_float(row.get("B")),
            "volume": _as_float(row.get("D")),
            "attributes": attributes,
            "compatible_ids": [],
        })
    return templates


def parse_equipment_templates(workbook_path: Path) -> List[Dict[str, Any]]:
    rows = _read_sheet_rows(workbook_path, "Магазины и Патроны")
    weapon_rows = _read_sheet_rows(workbook_path, "Оружие")
    armor_rows = _read_sheet_rows(workbook_path, "Броня")
    misc_rows = _read_sheet_rows(workbook_path, "Прочее")
    from app.services.catalog_import import additional_templates
    additional_rows = []
    for sheet in ('Косметическая Экипировка', 'Разгрузочные системы'):
        try:
            additional_rows.append(_read_sheet_rows(workbook_path, sheet))
        except ValueError as error:
            if 'was not found in workbook' not in str(error):
                raise
            additional_rows.append([])
    templates: List[Dict[str, Any]] = [
        *_parse_ranged_weapons(weapon_rows),
        *_parse_melee_weapons(weapon_rows),
        *_parse_armor(armor_rows),
        *_parse_helmets(armor_rows),
        *_parse_exoskeleton_battery(misc_rows),
        *_parse_tools(misc_rows),
    ]
    for row in rows:
        name_a = _normalize_text(row.get("A"))
        has_numeric_capacity = bool(re.fullmatch(r"-?\d+(?:[.,]\d+)?", _normalize_text(row.get("B"))))
        if not name_a:
            continue

        # Magazines and loaders are stored on the left side of the sheet.
        if (
            has_numeric_capacity
            and (
                name_a.startswith("Магазин")
                or name_a.startswith("Барабанный Магазин")
                or name_a.startswith("Клипса")
                or name_a.startswith("Пулеметный короб")
                or "Подавач" in name_a
                or "Лента" in name_a
                or "Спидлоадер" in name_a
            )
        ):
            caliber = _canonical_caliber(row.get("G"))
            compatible_names = _parse_name_list(row.get("H"))
            reload_time = _parse_od(row.get("C"))
            compatible_weapon_names = compatible_names
            is_loader = any(fragment in name_a.lower() for fragment in ("подавач", "лента"))
            templates.append(
                {
                    "name": _canonical_caliber(name_a),
                    "category": "magazine",
                    "subcategory": caliber or None,
                    "item_class": row.get("F") or None,
                    "description": row.get("H") or "",
                    "price": _as_int(row.get("D"), 0),
                    "weight": 0.0,
                    "volume": _magazine_volume(row.get("E"), name_a),
                    "attributes": {
                        "import_source": "equipment_workbook",
                        "caliber": caliber,
                        "capacity": _as_int(row.get("B"), 0),
                        "reload_time_od": reload_time,
                        "ergonomics": _as_int(row.get("F"), 0),
                        "compatible_weapon_names": compatible_weapon_names,
                        "compatible_weapons": [],
                        "isLoader": is_loader,
                        "universalLoader": is_loader and not caliber,
                        "loadingDevice": is_loader,
                        "raw_row": row,
                    },
                    "compatible_ids": [],
                }
            )

        # Ammo items are listed on the right side of the same sheet.
        name_l = _normalize_text(row.get("L"))
        has_numeric_price = bool(re.fullmatch(r"-?\d+(?:[.,]\d+)?", _normalize_text(row.get("M"))))
        if not name_l or not has_numeric_price:
            continue
        if _looks_like_grenade_ammo(row):
            continue

        damage = _ammo_damage_from_row(row)
        caliber = _canonical_caliber(name_l)
        ammo_group = _normalize_text(row.get("K"))
        effective_range = _as_int(row.get("U"), 0)
        penetration = _parse_penetration(row.get("Q"), 0.0)
        boundary = _as_float(row.get("R"), 0.0)
        purchase_category = _normalize_text(row.get("N"))
        ammo_variants = _detect_ammo_variants(row)
        price = _as_int(row.get("M"), 0)

        attributes = {
            "import_source": "equipment_workbook",
            "caliber": caliber,
            "ammo_group": ammo_group,
            "purchase_category": purchase_category,
            "penetration_boundary": boundary,
            "activation_time": _normalize_text(row.get("T")),
            "effective_range": effective_range,
            "notes": _normalize_text(row.get("V")),
            "ammo_variants": ammo_variants,
            "ammo_variant": ammo_variants[0] if len(ammo_variants) == 1 else None,
            "raw_row": row,
        }

        templates.append(
            {
                "name": _canonical_caliber(name_l),
                "category": "ammo",
                "subcategory": caliber or ammo_group or None,
                "item_class": purchase_category or None,
                "description": _normalize_text(row.get("V")),
                "price": price,
                "weight": 0.25 if caliber else 0.0,
                "volume": 0.04 if caliber and "12x70" in caliber else 0.02,
                "attributes": {
                    **attributes,
                    "damage": damage,
                    "penetration": penetration,
                    "range": effective_range,
                },
                "compatible_ids": [],
            }
        )

    detachable_aliases = {
        _equipment_alias(name)
        for template in templates
        if template["category"] == "magazine"
        for name in template["attributes"].get("compatible_weapon_names", [])
    }
    for template in templates:
        if template["category"] == "weapon":
            name = template["name"]
            subcategory = template["subcategory"]
            aliases = _weapon_aliases(name)
            explicitly_detachable = bool(aliases & detachable_aliases)
            generally_detachable = subcategory in {
                "Пистолеты-пулеметы",
                "Штурмовые винтовки и карабины",
                "Пулемёты",
            }
            caliber_key = re.sub(r"[^0-9a-zа-я]+", "", str(template["attributes"].get("caliber") or "").lower())
            pistol_with_fixed_cylinder = (
                name.startswith("Револьвер")
                or name == "Нож стреляющий"
                or caliber_key == "18x45"
            )
            fixed_magazine = not (
                explicitly_detachable or generally_detachable or (
                    subcategory == "Пистолеты" and not pistol_with_fixed_cylinder
                )
            )
            _finalize_weapon_magazine_attributes(template, fixed_magazine)
            normalized_name = _equipment_alias(name)
            bolt_names = {_equipment_alias(value) for value in (
                "Суслик", "Малинова", "Мачеха 51", "Свет-99", "Пылесос",
            )}
            pump_names = {_equipment_alias(value) for value in (
                "Гора Б88", "Гора 580Б2", "Ремень 787", "Спаситель 70",
            )}
            lowered_name = name.lower()
            matches_bolt = (
                any(value in normalized_name for value in bolt_names)
                or bool(re.search(r"(?:^|\s)ау(?:\s|$)", lowered_name))
            )
            matches_pump = (
                any(value in normalized_name for value in pump_names)
                or bool(re.search(r"(?:^|\s)д-?2(?:\s|$)", lowered_name))
            )
            if matches_bolt:
                template["attributes"]["manual_cycle"] = "bolt"
            elif matches_pump:
                template["attributes"]["manual_cycle"] = "pump"
    # Специализированные оружейные и ремонтные профили имеют приоритет.
    known = {(entry['category'], _normalize_equipment_name(entry['name'])) for entry in templates}
    for entry in additional_templates(misc_rows, *additional_rows):
        key = (entry['category'], _normalize_equipment_name(entry['name']))
        if key not in known:
            templates.append(entry)
            known.add(key)
    for source_order, template in enumerate(templates):
        template.setdefault("attributes", {})["source_order"] = source_order
    return [template for template in templates if not (template['category']=='gloves' and template['name']=='Спортивные')]


def insert_missing_templates(templates, session=None):
    """Добавляет отсутствующее, сохраняя цены, профили и ID существующих записей."""
    from copy import deepcopy
    session = session or db.session
    existing = session.query(ItemTemplate).all()
    known = {(item.category, _normalize_equipment_name(item.name)) for item in existing}
    inserted = 0
    for original in sorted(templates, key=lambda entry: entry['category'] == 'magazine'):
        data = deepcopy(original)
        data.pop('id', None)
        key = (data['category'], _normalize_equipment_name(data['name']))
        if key in known:
            continue
        if data['category'] == 'magazine':
            session.flush()
            aliases = {}
            for weapon in session.query(ItemTemplate).filter_by(category='weapon').all():
                for alias in _weapon_aliases(weapon.name):
                    aliases.setdefault(alias, []).append(weapon.id)
            compatible = sorted({weapon_id for name in data['attributes'].get('compatible_weapon_names', [])
                                 for weapon_id in aliases.get(_equipment_alias(name), [])})
            data['compatible_ids'] = compatible
            data['attributes']['compatible_weapons'] = compatible
        session.add(ItemTemplate(**data))
        known.add(key)
        inserted += 1
    session.flush()
    return {'parsed': len(templates), 'inserted': inserted, 'updated': 0, 'skipped': len(templates) - inserted}


def upsert_equipment_templates(workbook_path: str | Path, session=None) -> Dict[str, int]:
    path = Path(workbook_path).expanduser().resolve()
    templates = parse_equipment_templates(path)
    session = session or db.session

    header_names = {_normalize_equipment_name(name) for name in {"Магазины", "Магазин Калибр"}}
    removed_headers = 0
    for item in ItemTemplate.query.filter_by(category="magazine").all():
        if _normalize_equipment_name(item.name) in header_names:
            session.delete(item)
            removed_headers += 1

    removed_grenades = 0
    for item in ItemTemplate.query.filter_by(category="ammo").all():
        if _looks_like_grenade_ammo({"A": item.name, "K": item.subcategory or "", "L": item.name, "V": item.description or "", "P": "", "S": ""}):
            session.delete(item)
            removed_grenades += 1

    imported_categories = [
        "ammo", "magazine", "weapon", "melee_weapon", "armor", "helmet",
        "gas_mask", "exoskeleton_module", "tool",
    ]
    existing_by_category: Dict[str, List[ItemTemplate]] = {}
    for item in ItemTemplate.query.filter(ItemTemplate.category.in_(imported_categories)).all():
        existing_by_category.setdefault(item.category, []).append(item)

    inserted = 0
    updated = 0
    used_ids: set[int] = set()
    ordered_templates = sorted(templates, key=lambda data: data["category"] == "magazine")
    for data in ordered_templates:
        category = data["category"]
        normalized_name = _normalize_equipment_name(data["name"])
        candidates = existing_by_category.get(category, [])
        item = None
        for candidate in candidates:
            if candidate.id in used_ids:
                continue
            candidate_name = _normalize_equipment_name(candidate.name)
            if candidate_name == normalized_name:
                item = candidate
                break

        if category == "magazine":
            session.flush()
            weapon_alias_map: Dict[str, List[int]] = {}
            for weapon in ItemTemplate.query.filter_by(category="weapon").all():
                for alias in _weapon_aliases(weapon.name):
                    weapon_alias_map.setdefault(alias, []).append(weapon.id)
            compatible_ids: List[int] = []
            for weapon_name in data["attributes"].get("compatible_weapon_names", []):
                for weapon_id in weapon_alias_map.get(_equipment_alias(weapon_name), []):
                    if weapon_id not in compatible_ids:
                        compatible_ids.append(weapon_id)
            data["attributes"]["compatible_weapons"] = compatible_ids
            data["compatible_ids"] = compatible_ids

        if item is None:
            item = ItemTemplate(**data)
            session.add(item)
            existing_by_category.setdefault(category, []).append(item)
            inserted += 1
            continue

        used_ids.add(item.id)
        if category == "weapon" and isinstance((item.attributes or {}).get("slots"), list):
            # Сохраняем ограничения слотов, вручную заданные ведущим.
            data["attributes"]["slots"] = attachment_slots(item.subcategory, item.attributes["slots"])
        changed = False
        for field in ["name", "subcategory", "item_class", "description", "price", "weight", "volume"]:
            value = data[field]
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
        "updated": updated + removed_headers + removed_grenades,
    }
