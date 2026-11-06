from __future__ import annotations

import math
import re
import random
from typing import Any, Dict, Iterable, List

from app.services.combat import CombatService


ARMOR_CATEGORIES = {"armor", "helmet", "gas_mask"}
WEAPON_CATEGORIES = {"weapon", "melee_weapon"}
LUBRICATION_KIT_NAME = "набор смазочных приспособлений"
LUBRICATION_KIT_PROFILE = {
    "kind": "weapon",
    "repair_amount": 10,
    "duration_minutes": 5,
    "minimum_durability": 75,
    "engineering_min": 0,
    "consumed_on_use": True,
    "can_lubricate_prostheses": True,
}


def _normalized_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("ё", "е").split())


def _repair_profile(tool: Dict[str, Any], template: Any) -> Dict[str, Any] | None:
    attributes = {**(getattr(template, "attributes", None) or {}), **(tool.get("attributes") or {})}
    profile = attributes.get("repair_profile")
    if _normalized_name(tool.get("name") or getattr(template, "name", "")) == LUBRICATION_KIT_NAME:
        return {**(profile if isinstance(profile, dict) else {}), **LUBRICATION_KIT_PROFILE}
    return profile if isinstance(profile, dict) else None


def resolve_item_path(character_data: Dict[str, Any], path: Iterable[Any]) -> Dict[str, Any]:
    value: Any = character_data
    for part in list(path or []):
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except (IndexError, TypeError, ValueError):
                raise ValueError("Предмет больше не находится по указанному пути")
        elif isinstance(value, dict) and str(part) in value:
            value = value[str(part)]
        else:
            raise ValueError("Предмет больше не находится по указанному пути")
    if not isinstance(value, dict):
        raise ValueError("По указанному пути нет предмета")
    return value


def _effective_skill(character_data: Dict[str, Any], category: str, skill: str) -> int:
    value = ((character_data.get("skills") or {}).get(category) or {}).get(skill) or {}
    if not isinstance(value, dict):
        return 0
    return CombatService._coerce_int(value.get("base", value.get("value", 5)), 5) + CombatService._coerce_int(
        value.get("bonus"), 0,
    )


def _item_class(item: Dict[str, Any], template: Any) -> int:
    values = [
        item.get("itemClass"), item.get("item_class"),
        (item.get("attributes") or {}).get("item_class"),
        getattr(template, "item_class", None),
        getattr(template, "subcategory", None),
    ]
    for value in values:
        match = re.search(r"\d+", str(value or ""))
        if match:
            return int(match.group())
    return 1


def _available_uses(tool: Dict[str, Any], profile: Dict[str, Any]) -> int | None:
    maximum = tool.get("maxUses", (tool.get("attributes") or {}).get("uses"))
    if maximum is None:
        return None
    return max(0, CombatService._coerce_int(tool.get("uses", maximum), 0))


def _spend_tool_use(tool: Dict[str, Any], profile: Dict[str, Any]) -> None:
    available = _available_uses(tool, profile)
    if available is None:
        return
    if available <= 0:
        raise ValueError("У набора не осталось прочности")
    tool["uses"] = available - 1
    tool.setdefault("attributes", {})["uses_remaining"] = tool["uses"]


def _remove_or_spend_item(character_data: Dict[str, Any], path: List[Any]) -> None:
    item = resolve_item_path(character_data, path)
    quantity = max(1, CombatService._coerce_int(item.get("quantity"), 1))
    if quantity > 1:
        item["quantity"] = quantity - 1
        return
    parent: Any = character_data
    for part in path[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[str(part)]
    key = path[-1]
    if isinstance(parent, list):
        parent.pop(int(key))
    elif isinstance(parent, dict):
        parent.pop(str(key), None)


def weapon_maximum_penalty(current_durability: int) -> int:
    current = max(0, int(current_durability))
    if current <= 0:
        return 40
    if current <= 10:
        return 25
    if current <= 30:
        return 15
    if current <= 45:
        return 8
    if current <= 60:
        return 5
    if current <= 75:
        return 2
    return 0


def armor_repair_durability_loss(stage: int) -> int:
    return {2: 1, 3: 3, 4: 5, 5: 10}.get(int(stage), 0)


def _repair_weapon(target: Dict[str, Any], profile: Dict[str, Any], template: Any) -> Dict[str, Any]:
    current, maximum = CombatService._weapon_durability(target)
    if current >= maximum:
        raise ValueError("Оружие уже имеет максимальную прочность")
    minimum = max(0, CombatService._coerce_int(profile.get("minimum_durability"), 0))
    if minimum and current <= minimum:
        raise ValueError(f"Этот набор ремонтирует оружие только при прочности выше {minimum}")
    maximum_class = profile.get("max_item_class")
    if maximum_class is not None and _item_class(target, template) > int(maximum_class):
        raise ValueError(f"Этот набор не ремонтирует оружие категории выше Оружие {maximum_class}")

    penalty = weapon_maximum_penalty(current)
    new_maximum = max(1, maximum - penalty)
    before = current
    target["maxDurability"] = new_maximum
    target["durability"] = min(new_maximum, current + max(1, int(profile.get("repair_amount") or 0)))
    CombatService._weapon_durability(target)
    return {
        "kind": "weapon", "before": before, "after": target["durability"],
        "maximum_before": maximum, "maximum_after": new_maximum,
        "maximum_penalty": penalty,
    }


def _repair_armor(target: Dict[str, Any], profile: Dict[str, Any], template: Any) -> Dict[str, Any]:
    attributes = {**(getattr(template, "attributes", None) or {}), **(target.get("attributes") or {})}
    stage = max(1, min(5, CombatService._coerce_int(target.get("stage"), 1)))
    maximum_class = profile.get("max_item_class")
    if maximum_class is not None and _item_class(target, template) > int(maximum_class):
        raise ValueError(f"Этот набор не ремонтирует броню категории выше Броня {maximum_class}")
    maximum_damage_stage = profile.get("maximum_damage_stage")
    if maximum_damage_stage is not None and stage > int(maximum_damage_stage):
        raise ValueError("Этот набор не подходит для настолько повреждённой брони")

    capacity_before = CombatService._armor_stage_capacity(target, attributes)
    current_stage = max(0, CombatService._coerce_int(
        target.get("currentStageDurability", capacity_before), capacity_before,
    ))
    current_base = max(0, CombatService._coerce_int(
        target.get("durability", attributes.get("max_durability", 1)), 1,
    ))
    if profile.get("kind") == "armor_current_stage":
        if current_stage >= capacity_before:
            raise ValueError("Прочность текущей стадии уже максимальна")
        repaired_stage = stage
    else:
        if stage <= 1:
            raise ValueError("Броня уже находится на целой стадии")
        repaired_stage = max(1, stage - max(1, int(profile.get("repair_stages") or 1)))

    durability_loss = armor_repair_durability_loss(stage)
    target["durability"] = max(0, current_base - durability_loss)
    target["stage"] = repaired_stage
    target["stageDurability"] = CombatService._armor_stage_capacity(target, attributes)
    target["currentStageDurability"] = target["stageDurability"]
    if target["durability"] <= 0:
        target["protectionDisabled"] = True
    return {
        "kind": "armor", "stage_before": stage, "stage_after": repaired_stage,
        "durability_before": current_base, "durability_after": target["durability"],
        "durability_loss": durability_loss,
    }


def repair_equipment(
    character_data: Dict[str, Any], tool_path: List[Any], target_path: List[Any],
    *, tool_template: Any = None, target_template: Any = None,
) -> Dict[str, Any]:
    tool = resolve_item_path(character_data, tool_path)
    target = resolve_item_path(character_data, target_path)
    profile = _repair_profile(tool, tool_template)
    from app.services.character_traits import trait_count,background_is
    if isinstance(profile,dict):
        profile=dict(profile)
        if profile.get('maximum_damage_stage') is not None:
            profile['maximum_damage_stage']=min(5,int(profile['maximum_damage_stage'])+trait_count(character_data,'Как новенький'))
    if not isinstance(profile, dict) or profile.get("kind") not in {
        "weapon", "armor", "armor_current_stage", "restore_tool",
    }:
        raise ValueError("Этот предмет не предназначен для ремонта снаряжения")
    if profile["kind"] == "restore_tool":
        target_attributes = {
            **(getattr(target_template, "attributes", None) or {}),
            **(target.get("attributes") or {}),
        }
        if str(target.get("category") or getattr(target_template, "category", "")).lower() != "tool":
            raise ValueError("Нужно выбрать набор инструментов")
        maximum = target.get("maxUses", target_attributes.get("uses"))
        if maximum is None:
            raise ValueError("У выбранного инструмента нет восстанавливаемых зарядов")
        maximum = max(1, int(maximum))
        current = max(0, int(target.get("uses", maximum)))
        if current >= maximum:
            raise ValueError("Набор инструментов уже полностью восстановлен")
        restored = math.floor(maximum * float(profile.get("restore_fraction") or 0.5))
        if restored <= 0:
            raise ValueError("Набор не восстановит ни одного заряда")
        target["uses"] = min(maximum, current + restored)
        target.setdefault("attributes", {})["uses_remaining"] = target["uses"]
        result = {
            "kind": "restore_tool", "before": current, "after": target["uses"],
            "maximum_after": maximum, "maximum_penalty": 0,
            "tool_name": tool.get("name") or "Набор восстановления инструментов",
            "target_name": target.get("name") or getattr(target_template, "name", "Инструменты"),
            "tool_uses": None,
            "duration_minutes": max(0, int(profile.get("duration_minutes") or 0)),
        }
        _remove_or_spend_item(character_data, tool_path)
        return result
    required_engineering = max(0, int(profile.get("engineering_min") or 0))
    engineering = _effective_skill(character_data, "other", "engineering")
    if engineering < required_engineering:
        raise ValueError(f"Требуется Инженерия {required_engineering}, доступно {engineering}")

    category = str(target.get("category") or getattr(target_template, "category", "")).lower()
    if profile["kind"] == "weapon":
        if category not in WEAPON_CATEGORIES:
            raise ValueError("Нужно выбрать оружие")
        result = _repair_weapon(target, profile, target_template)
    else:
        if category not in ARMOR_CATEGORIES:
            raise ValueError("Нужно выбрать броню или шлем")
        result = _repair_armor(target, profile, target_template)
    if profile.get("consumed_on_use"):
        _remove_or_spend_item(character_data, tool_path)
    else:
        saved_use=background_is(character_data,'Конструктор') and random.randint(1,100)<=50
        if not saved_use:_spend_tool_use(tool, profile)
        result['tool_use_saved']=bool(saved_use)
    result.update({
        "tool_name": tool.get("name") or getattr(tool_template, "name", "Инструменты"),
        "target_name": target.get("name") or getattr(target_template, "name", "Снаряжение"),
        "tool_uses": _available_uses(tool, profile),
        "duration_minutes": max(0, int(profile.get("duration_minutes") or 0)),
    })
    return result
