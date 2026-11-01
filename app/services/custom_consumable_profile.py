"""Validate profiles authored by the in-game consumable editor."""

from math import isfinite
import json

from app.models.templates import ItemTemplate
from app.services.exceptions import ValidationError


IMMEDIATE_FIELDS = {
    "hp", "radiation_delta", "stress_delta", "pain_delta", "exhaustion_delta",
    "psy_delta", "infection_delta", "intoxication_delta", "temperature_delta",
}
DELAYED_FIELDS = {
    "radiation": "radiation", "stress": "stress", "painLevel": "painLevel",
    "exhaustion": "exhaustion", "psyState": "psyState", "infection": "infection",
    "intoxication": "intoxication", "temperature": "temperature",
}
MODIFIER_STATS = {"strength", "agility", "accuracy", "will", "psy_defense", "weight"}
TICKS = {"turn_end", "time_elapsed", "movement_end", "day_start"}


def _number(value, label, *, minimum=-1000, maximum=1000):
    if isinstance(value, bool):
        raise ValidationError(f"Некорректное значение: {label}")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"Некорректное значение: {label}") from error
    if not isfinite(number) or not minimum <= number <= maximum:
        raise ValidationError(f"Некорректное значение: {label}")
    return number


def _has_immediate_effect(direct):
    # Диапазоны/типы уже проверены вызывающим валидатором своего формата.
    return any(
        value is not None and _number(value, key, minimum=-1_000_000, maximum=1_000_000) != 0
        for key, value in direct.items() if key in IMMEDIATE_FIELDS
    )


def validate_custom_consumable(category, attributes):
    if not isinstance(attributes, dict):
        return
    profile = attributes.get("consumable")
    if not isinstance(profile, dict) or profile.get("editor_version") not in (1, 2):
        return
    if category != "consumable":
        raise ValidationError("Профиль препарата можно сохранить только у расходника")
    if profile["editor_version"] == 2:
        _validate_catalog_profile(profile, attributes)
        return
    if set(profile) - {"editor_version", "direct", "modifiers", "effects"}:
        raise ValidationError("Неизвестные поля профиля расходника")
    direct = profile.get("direct")
    if not isinstance(direct, dict) or set(direct) - IMMEDIATE_FIELDS - {"action_points_cost"}:
        raise ValidationError("Неизвестный мгновенный эффект расходника")
    for key, value in direct.items():
        _number(value, key, minimum=0 if key == "action_points_cost" else -1000)
    modifiers = profile.get("modifiers")
    effects = profile.get("effects")
    if not isinstance(modifiers, list) or not isinstance(effects, list) or len(modifiers) + len(effects) + len(direct) > 32:
        raise ValidationError("Слишком много эффектов расходника")
    if not _has_immediate_effect(direct) and not modifiers and not effects:
        raise ValidationError("Добавьте хотя бы один эффект препарата")
    seen_stats = set()
    for modifier in modifiers:
        if not isinstance(modifier, dict) or set(modifier) - {"stat", "value", "remaining", "tick"}:
            raise ValidationError("Некорректный временный эффект")
        if modifier.get("stat") not in MODIFIER_STATS or modifier.get("tick") not in TICKS:
            raise ValidationError("Неизвестная характеристика или срок действия")
        if modifier["stat"] in seen_stats:
            raise ValidationError("Для одной характеристики можно задать только один временный бонус")
        seen_stats.add(modifier["stat"])
        if _number(modifier.get("value"), "бонус") == 0:
            raise ValidationError("Бонус должен отличаться от нуля")
        remaining = _number(modifier.get("remaining"), "длительность", minimum=1, maximum=10000)
        if not remaining.is_integer():
            raise ValidationError("Длительность должна быть целым числом")
    seen_effects = set()
    for effect in effects:
        if not isinstance(effect, dict) or set(effect) - {
            "type", "name", "remaining", "tick", "time_unit", "adjustments", "area",
        } or effect.get("type") != "delayed_adjustment":
            raise ValidationError("Некорректный отложенный эффект")
        if effect.get("tick") not in TICKS:
            raise ValidationError("Неизвестный момент срабатывания")
        if not isinstance(effect.get("area"), str) or not effect["area"].startswith("editor-") or len(effect["area"]) > 20:
            raise ValidationError("Некорректный идентификатор эффекта")
        if effect["area"] in seen_effects:
            raise ValidationError("Повторяется идентификатор отложенного эффекта")
        seen_effects.add(effect["area"])
        if effect.get("time_unit") not in (None, "minute") or (
            effect.get("time_unit") == "minute" and effect.get("tick") != "time_elapsed"
        ):
            raise ValidationError("Некорректная единица времени")
        remaining = _number(effect.get("remaining"), "задержка", minimum=1, maximum=10000)
        if not remaining.is_integer():
            raise ValidationError("Задержка должна быть целым числом")
        adjustments = effect.get("adjustments")
        if not isinstance(adjustments, list) or len(adjustments) != 1:
            raise ValidationError("Отложенный эффект должен менять одно состояние")
        adjustment = adjustments[0]
        if not isinstance(adjustment, dict) or set(adjustment) - {"field", "delta", "min", "max"}:
            raise ValidationError("Некорректное отложенное изменение")
        field = adjustment.get("field")
        if field not in DELAYED_FIELDS:
            raise ValidationError("Неизвестное состояние для отложенного эффекта")
        if _number(adjustment.get("delta"), "изменение") == 0:
            raise ValidationError("Изменение должно отличаться от нуля")
        if adjustment.get("min") != 0 or adjustment.get("max") not in (None, 10, 50, 100):
            raise ValidationError("Некорректные границы отложенного эффекта")
    _number(attributes.get("uses", 1), "заряды", minimum=1, maximum=10000)


def _validate_json_tree(value, depth=0):
    if depth > 8:
        raise ValidationError("Профиль препарата слишком глубоко вложен")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        _number(value, "параметр профиля", minimum=-1_000_000, maximum=1_000_000)
    elif isinstance(value, str):
        if len(value) > 500:
            raise ValidationError("Слишком длинный текст в профиле препарата")
    elif isinstance(value, list):
        if len(value) > 100:
            raise ValidationError("Слишком много элементов профиля препарата")
        for child in value:
            _validate_json_tree(child, depth + 1)
    elif isinstance(value, dict):
        if len(value) > 150:
            raise ValidationError("Слишком много полей профиля препарата")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 100 or key.startswith("__"):
                raise ValidationError("Некорректное имя поля профиля")
            _validate_json_tree(child, depth + 1)
    else:
        raise ValidationError("Некорректное значение в профиле препарата")


def _catalog_field_registry():
    direct_types = {key: {int, float} for key in IMMEDIATE_FIELDS | {"action_points_cost"}}
    effect_types = {"delayed_adjustment"}
    effect_fields = {"type", "name", "remaining", "tick", "time_unit", "adjustments", "area"}
    modifier_stats = set(MODIFIER_STATS)
    modifier_fields = {"stat", "value", "remaining", "tick"}
    for template in ItemTemplate.query.filter(ItemTemplate.category.in_(("consumable", "gas_mask_module"))).all():
        profile = ((template.attributes or {}).get("consumable") or {})
        if not isinstance(profile, dict):
            continue
        for key, value in (profile.get("direct") or {}).items():
            direct_types.setdefault(key, set()).add(type(value))
        for effect in profile.get("effects") or []:
            if isinstance(effect, dict):
                effect_types.add(effect.get("type"))
                effect_fields.update(effect)
        for modifier in profile.get("modifiers") or []:
            if isinstance(modifier, dict):
                modifier_stats.add(modifier.get("stat"))
                modifier_fields.update(modifier)
    return direct_types, effect_types, effect_fields, modifier_stats, modifier_fields


def _validate_catalog_profile(profile, attributes):
    if set(profile) - {
        "editor_version", "direct", "effects", "modifiers", "addiction",
        "status_removals", "status_additions", "requirements", "notes",
    }:
        raise ValidationError("Неизвестный раздел профиля препарата")
    if len(json.dumps(profile, ensure_ascii=False)) > 80_000:
        raise ValidationError("Профиль препарата слишком большой")
    _validate_json_tree(profile)
    direct = profile.get("direct")
    effects = profile.get("effects", [])
    modifiers = profile.get("modifiers", [])
    if not isinstance(direct, dict) or not isinstance(effects, list) or not isinstance(modifiers, list):
        raise ValidationError("Некорректные разделы профиля препарата")
    allowed_direct, allowed_effect_types, allowed_effect_fields, allowed_stats, allowed_modifier_fields = _catalog_field_registry()
    for key, value in direct.items():
        types = allowed_direct.get(key)
        if not types:
            raise ValidationError(f"Неизвестное правило препарата: {key}")
        actual_type = type(value)
        numeric_match = actual_type in (int, float) and bool({int, float} & types)
        if value is not None and actual_type not in types and not numeric_match:
            raise ValidationError(f"Некорректный тип правила: {key}")
        if key == "applications":
            if not isinstance(value, list) or len(value) > 20 or any(not isinstance(entry, dict) for entry in value):
                raise ValidationError("Некорректные варианты лечения")
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("type") not in allowed_effect_types or set(effect) - allowed_effect_fields:
            raise ValidationError("Неизвестный тип или параметр эффекта")
    for modifier in modifiers:
        if not isinstance(modifier, dict) or modifier.get("stat") not in allowed_stats or set(modifier) - allowed_modifier_fields:
            raise ValidationError("Неизвестный временный модификатор")
    for key in ("status_removals", "status_additions", "requirements", "notes"):
        entries = profile.get(key, [])
        if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
            raise ValidationError(f"Некорректный список: {key}")
    addiction = profile.get("addiction")
    if addiction is not None:
        if not isinstance(addiction, dict) or set(addiction) - {"key", "label", "chance", "multipliers", "kind"}:
            raise ValidationError("Некорректное правило зависимости")
        if not isinstance(addiction.get("key"), str) or not isinstance(addiction.get("kind"), str):
            raise ValidationError("У зависимости должны быть ключ и вид")
        _number(addiction.get("chance"), "шанс зависимости", minimum=0, maximum=100)
        if not isinstance(addiction.get("multipliers", {}), dict):
            raise ValidationError("Некорректные множители зависимости")
    if not direct and not effects and not modifiers and not profile.get("status_additions"):
        raise ValidationError("Добавьте хотя бы одно правило препарата")
    # Для общей с простым редактором формы цена и нулевые изменения не дают эффекта.
    # Каталожные процедуры/сроки со своими direct-полями сохраняют прежний контракт.
    if (set(direct) <= IMMEDIATE_FIELDS | {"action_points_cost"}
            and not effects and not modifiers
            and not profile.get("status_additions") and not profile.get("status_removals")
            and not _has_immediate_effect(direct)):
        raise ValidationError("Добавьте хотя бы один эффект препарата")
    uses = _number(attributes.get("uses", 1), "заряды", minimum=1, maximum=10000)
    if not uses.is_integer():
        raise ValidationError("Количество зарядов должно быть целым")
