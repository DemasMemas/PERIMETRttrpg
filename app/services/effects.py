from __future__ import annotations

from copy import deepcopy
import math
import random
from typing import Any, Dict, Iterable, List, Optional

EFFECT_TYPE_META = {
    "burning": {"label": "Горение", "group": "injury"},
    "concussion": {"label": "Контузия", "group": "injury"},
    # `generic` is retained only for legacy or malformed records. New effects
    # created by a GM use `custom`, which makes their manual nature explicit.
    "generic": {"label": "Неопределённый эффект", "group": "technical"},
    "custom": {"label": "Пользовательский эффект", "group": "status"},
    "heal": {"label": "Лечение", "group": "medical"},
    "regeneration": {"label": "Регенерация", "group": "medical"},
    "radiation": {"label": "Радиация", "group": "medical"},
    "bleeding": {"label": "Кровотечение", "group": "injury"},
    "bleeding_external_light": {"label": "Кровотечение внешнее лёгкое", "group": "injury"},
    "bleeding_external_medium": {"label": "Кровотечение внешнее среднее", "group": "injury"},
    "bleeding_external_severe": {"label": "Кровотечение внешнее сильное", "group": "injury"},
    "bleeding_external_extreme": {"label": "Кровотечение внешнее экстремальное", "group": "injury"},
    "bleeding_internal_light": {"label": "Кровотечение внутреннее лёгкое", "group": "injury"},
    "bleeding_internal_medium": {"label": "Кровотечение внутреннее среднее", "group": "injury"},
    "bleeding_internal_severe": {"label": "Кровотечение внутреннее сильное", "group": "injury"},
    "bleeding_internal_extreme": {"label": "Кровотечение внутреннее экстремальное", "group": "injury"},
    "pain": {"label": "Боль", "group": "injury"},
    "exhaustion": {"label": "Истощение", "group": "need"},
    "stress": {"label": "Стресс", "group": "mental"},
    "stress_effect": {"label": "Эффект стресса", "group": "mental"},
    "stress_resistance": {"label": "Сопротивление стрессу", "group": "mental"},
    "stress_stupor": {"label": "Ступор", "group": "mental"},
    "phobia": {"label": "Фобия", "group": "mental"},
    "intoxication": {"label": "Опьянение", "group": "need"},
    "infection": {"label": "Заражение", "group": "disease"},
    "fracture": {"label": "Перелом", "group": "injury"},
    "fracture_fixed": {"label": "Зафиксированный перелом", "group": "injury"},
    "fracture_unfixed": {"label": "Незафиксированный перелом", "group": "injury"},
    "fracture_sequela": {"label": "Постоянный штраф после перелома", "group": "injury"},
    "mangled_limb": {"label": "Искореженная конечность", "group": "injury"},
    "temporary_limb_restoration": {"label": "Временное восстановление конечности", "group": "medical"},
    "delayed_limb_treatment": {"label": "Отложенное лечение конечности", "group": "medical"},
    "organ_loss": {"label": "Повреждённый орган", "group": "injury"},
    "organ_failure": {"label": "Смертельное повреждение органа", "group": "critical"},
    "shock": {"label": "Шок", "group": "injury"},
    "unconsciousness": {"label": "Без сознания", "group": "critical"},
    "critical_condition": {"label": "Критическое состояние", "group": "critical"},
    "death": {"label": "Смерть", "group": "critical"},
    "blindness": {"label": "Слепота", "group": "sense"},
    "deafness": {"label": "Глухота", "group": "sense"},
    "sleep": {"label": "Сон", "group": "critical"},
    "radiation_treatment": {"label": "Выведение радиации", "group": "medical"},
    "blood_recovery": {"label": "Восстановление кровопотери", "group": "medical"},
    "periodic_adjustment": {"label": "Периодический эффект", "group": "status"},
    "delayed_adjustment": {"label": "Отложенный эффект", "group": "status"},
    "deferred_adjustment": {"label": "Отложенный эффект", "group": "status"},
    "delayed_treatment": {"label": "Ожидание действия препарата", "group": "medical"},
    "next_rest_healing": {"label": "Лечение на следующем отдыхе", "group": "medical"},
    "untreated_wound": {"label": "Необработанная рана", "group": "injury"},
    "tourniquet": {"label": "Наложен жгут", "group": "medical"},
    "blood_loss_freeze": {"label": "Стабилизация кровопотери", "group": "medical"},
    "bleeding_prevention": {"label": "Блок новых кровотечений", "group": "medical"},
    "infection_growth_block": {"label": "Блок нарастания заражения", "group": "medical"},
    "analgesia": {"label": "Обезболивание", "group": "medical"},
    "stimulant_crash": {"label": "Последствие стимулятора", "group": "medical"},
    "radiation_filter": {"label": "Защита от входящей радиации", "group": "medical"},
    "temperature_control": {"label": "Контроль температуры", "group": "medical"},
    "limb_trauma_suppression": {"label": "Подавление травмы конечности", "group": "medical"},
    "pain_block": {"label": "Блок новых уровней боли", "group": "medical"},
    "addiction_withdrawal": {"label": "Ломка", "group": "need"},
    "withdrawal_support": {"label": "Поддержка при ломке", "group": "medical"},
    "withdrawal_support_pending": {"label": "Поддержка при ломке: задержка", "group": "medical"},
}

TYPE_ALIASES = {
    "burning": "burning", "горение": "burning",
    "concussion": "concussion", "контузия": "concussion",
    "custom": "custom",
    "пользовательский эффект": "custom",
    "organ_failure": "organ_failure",
    "heal": "heal",
    "healing": "heal",
    "лечение": "heal",
    "radiation": "radiation",
    "radiaton": "radiation",
    "radiation_reduction": "radiation",
    "bleed": "bleeding",
    "bleeding": "bleeding",
    "кровотечение": "bleeding",
    "external_bleeding": "bleeding_external_light",
    "internal_bleeding": "bleeding_internal_light",
    "bleeding_external_light": "bleeding_external_light",
    "bleeding_external_medium": "bleeding_external_medium",
    "bleeding_external_severe": "bleeding_external_severe",
    "bleeding_external_extreme": "bleeding_external_extreme",
    "bleeding_internal_light": "bleeding_internal_light",
    "bleeding_internal_medium": "bleeding_internal_medium",
    "bleeding_internal_severe": "bleeding_internal_severe",
    "bleeding_internal_extreme": "bleeding_internal_extreme",
    "pain": "pain",
    "боль": "pain",
    "exhaustion": "exhaustion",
    "истощение": "exhaustion",
    "stress": "stress",
    "stress_effect": "stress_effect",
    "stress_resistance": "stress_resistance",
    "stress_stupor": "stress_stupor",
    "phobia": "phobia",
    "стресс": "stress",
    "intoxication": "intoxication",
    "опьянение": "intoxication",
    "infection": "infection",
    "заражение": "infection",
    "fracture": "fracture",
    "перелом": "fracture",
    "fracture_fixed": "fracture_fixed",
    "fixed_fracture": "fracture_fixed",
    "зафиксированный перелом": "fracture_fixed",
    "фиксированный перелом": "fracture_fixed",
    "fracture_unfixed": "fracture_unfixed",
    "незафиксированный перелом": "fracture_unfixed",
    "fracture_sequela": "fracture_sequela",
    "последствие незафиксированного перелома": "fracture_sequela",
    "shock": "shock",
    "шок": "shock",
    "pain_shock": "shock",
    "болевой шок": "shock",
    "unconsciousness": "unconsciousness",
    "unconscious": "unconsciousness",
    "без сознания": "unconsciousness",
    "critical_condition": "critical_condition",
    "критическое состояние": "critical_condition",
    "death": "death",
    "dead": "death",
    "смерть": "death",
    "мертв": "death",
    "blindness": "blindness",
    "blind": "blindness",
    "слепота": "blindness",
    "deafness": "deafness",
    "deaf": "deafness",
    "глухота": "deafness",
    "sleep": "sleep",
    "сон": "sleep",
    "regeneration": "regeneration",
    "regen": "regeneration",
    "регенерация": "regeneration",
    "amputation": "amputation",
    "ампутация": "amputation",
    "mangled_limb": "mangled_limb",
    "искореженная конечность": "mangled_limb",
    "organloss": "organ_loss",
    "organ_loss": "organ_loss",
    "потеряоргана": "organ_loss",
    "потеря_органа": "organ_loss",
    **{effect_type: effect_type for effect_type in (
        "radiation_treatment", "blood_recovery", "periodic_adjustment", "delayed_adjustment",
        "deferred_adjustment", "delayed_treatment", "next_rest_healing", "untreated_wound",
        "tourniquet", "blood_loss_freeze", "bleeding_prevention", "infection_growth_block",
        "analgesia", "stimulant_crash", "radiation_filter", "temperature_control",
        "limb_trauma_suppression",
        "temporary_limb_restoration",
        "delayed_limb_treatment",
        "pain_block",
        "addiction_withdrawal",
        "withdrawal_support",
        "withdrawal_support_pending",
        "stress_resistance",
    )},
}

STATUS_EFFECT_TYPES = {
    "bleeding", "pain", "exhaustion", "stress", "intoxication",
    "infection", "fracture", "fracture_fixed", "shock", "unconsciousness", "critical_condition", "death",
    "blindness", "deafness",
    "sleep",
    "amputation", "organ_loss", "organ_failure", "mangled_limb",
    "bleeding_external_light", "bleeding_external_medium", "bleeding_external_severe", "bleeding_external_extreme",
    "bleeding_internal_light", "bleeding_internal_medium", "bleeding_internal_severe", "bleeding_internal_extreme",
}

ORGAN_LABELS = {
    "heart": "Сердце", "rightLung": "Правое лёгкое", "leftLung": "Левое лёгкое",
    "rightKidney": "Правая почка", "leftKidney": "Левая почка", "stomach": "Желудок",
    "liver": "Печень", "brain": "Мозг", "skull": "Череп", "spine": "Позвоночник",
    "rightEye": "Правый глаз", "leftEye": "Левый глаз",
    "rightEar": "Правое ухо", "leftEar": "Левое ухо", "nose": "Нос", "jaw": "Челюсть",
}

EFFECT_IMPACT_RULES = {
    "generic": {"areas": [], "requiresMedicineCheck": False, "treatment": "manual"},
    "custom": {"areas": [], "requiresMedicineCheck": False, "treatment": "manual"},
    "heal": {"areas": ["whole_body"], "requiresMedicineCheck": False, "treatment": "oral_or_medical"},
    "regeneration": {"areas": ["whole_body"], "requiresMedicineCheck": False, "treatment": "medical"},
    "radiation": {"areas": ["whole_body"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding_external_light": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding_external_medium": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding_external_severe": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding_external_extreme": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding_internal_light": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding_internal_medium": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding_internal_severe": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "bleeding_internal_extreme": {"areas": ["wound"], "requiresMedicineCheck": True, "treatment": "medical"},
    "pain": {"areas": ["whole_body"], "requiresMedicineCheck": True, "treatment": "medical"},
    "exhaustion": {"areas": ["whole_body"], "requiresMedicineCheck": True, "treatment": "medical"},
    "stress": {"areas": ["whole_body", "mind"], "requiresMedicineCheck": True, "treatment": "medical"},
    "intoxication": {"areas": ["whole_body"], "requiresMedicineCheck": True, "treatment": "medical"},
    "infection": {"areas": ["whole_body"], "requiresMedicineCheck": True, "treatment": "medical"},
    "fracture": {"areas": ["limb"], "requiresMedicineCheck": True, "treatment": "medical"},
    "fracture_fixed": {"areas": ["limb"], "requiresMedicineCheck": True, "treatment": "medical"},
    "shock": {"areas": ["whole_body"], "requiresMedicineCheck": True, "treatment": "medical"},
    "unconsciousness": {"areas": ["whole_body"], "requiresMedicineCheck": True, "treatment": "medical"},
    "critical_condition": {"areas": ["whole_body"], "requiresMedicineCheck": True, "treatment": "medical"},
    "death": {"areas": ["whole_body"], "requiresMedicineCheck": False, "treatment": "none"},
    "blindness": {"areas": ["eyes", "vision", "head"], "requiresMedicineCheck": True, "treatment": "medical"},
    "deafness": {"areas": ["ears", "hearing", "head"], "requiresMedicineCheck": True, "treatment": "medical"},
    "sleep": {"areas": ["whole_body", "mind"], "requiresMedicineCheck": False, "treatment": "rest"},
    "amputation": {"areas": ["missing_limb"], "requiresMedicineCheck": True, "treatment": "medical"},
    "mangled_limb": {"areas": ["limb"], "requiresMedicineCheck": True, "treatment": "surgery"},
    "organ_loss": {"areas": ["missing_organ"], "requiresMedicineCheck": True, "treatment": "medical"},
    "addiction_withdrawal": {"areas": ["whole_body", "mind"], "requiresMedicineCheck": False, "treatment": "withdrawal"},
    "withdrawal_support": {"areas": ["whole_body", "mind"], "requiresMedicineCheck": False, "treatment": "medical"},
    "withdrawal_support_pending": {"areas": ["whole_body", "mind"], "requiresMedicineCheck": False, "treatment": "medical"},
}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _clamp(value: int, minimum: Optional[int] = 0, maximum: Optional[int] = None) -> int:
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def canonical_type(effect_type: Any = None, name: str = "") -> str:
    raw = str(effect_type or name or "").strip().lower()
    if not raw:
        return "generic"
    if raw in TYPE_ALIASES:
        return TYPE_ALIASES[raw]
    compact = raw.replace(" ", "").replace("-", "").replace("_", "")
    if compact in TYPE_ALIASES:
        return TYPE_ALIASES[compact]
    if "внутрен" in raw and "кров" in raw:
        if "экстрем" in raw:
            return "bleeding_internal_extreme"
        if "сильн" in raw:
            return "bleeding_internal_severe"
        if "средн" in raw:
            return "bleeding_internal_medium"
        if "легк" in raw or "лёгк" in raw:
            return "bleeding_internal_light"
        return "bleeding_internal_light"
    if "внешн" in raw and "кров" in raw:
        if "экстрем" in raw:
            return "bleeding_external_extreme"
        if "сильн" in raw:
            return "bleeding_external_severe"
        if "средн" in raw:
            return "bleeding_external_medium"
        if "легк" in raw or "лёгк" in raw:
            return "bleeding_external_light"
        return "bleeding_external_light"
    if "кров" in raw:
        return "bleeding"
    if "радиац" in raw:
        return "radiation"
    if "истощ" in raw:
        return "exhaustion"
    if "стресс" in raw:
        return "stress"
    if "опьян" in raw:
        return "intoxication"
    if "зафикс" in raw or "фиксир" in raw or "fixed fracture" in raw:
        return "fracture_fixed"
    if "перелом" in raw:
        return "fracture"
    if "боль" in raw:
        return "pain"
    if "шок" in raw:
        return "shock"
    if "критичес" in raw:
        return "critical_condition"
    if "смерт" in raw or "мертв" in raw:
        return "death"
    if "слеп" in raw:
        return "blindness"
    if "глух" in raw:
        return "deafness"
    if "заражен" in raw:
        return "infection"
    if "леч" in raw or "heal" in raw:
        return "heal"
    if "реген" in raw:
        return "regeneration"
    if "ампута" in raw:
        return "amputation"
    if "потер" in raw and "орган" in raw:
        return "organ_loss"
    return "generic"


def get_effect_meta(effect_type: str) -> Dict[str, str]:
    return EFFECT_TYPE_META.get(effect_type, EFFECT_TYPE_META["generic"])


BLEEDING_STAGE_ORDER = ["normal", "light", "medium", "severe", "critical", "fatal"]
BLEEDING_STAGE_PENALTIES = {
    "normal": 0,
    "light": 1,
    "medium": 2,
    "severe": 3,
    "critical": 4,
}

BLEEDING_EFFECT_RULES = {
    "bleeding": {"severity": 1, "kind": "external", "stage": "light", "areas": ["wound"]},
    "bleeding_external_light": {"severity": 1, "kind": "external", "stage": "light", "areas": ["wound"]},
    "bleeding_external_medium": {"severity": 3, "kind": "external", "stage": "medium", "areas": ["wound"]},
    "bleeding_external_severe": {"severity": 5, "kind": "external", "stage": "severe", "areas": ["wound"]},
    "bleeding_external_extreme": {"severity": 8, "kind": "external", "stage": "extreme", "areas": ["wound"]},
    "bleeding_internal_light": {"severity": 1, "kind": "internal", "stage": "light", "areas": ["wound"]},
    "bleeding_internal_medium": {"severity": 3, "kind": "internal", "stage": "medium", "areas": ["wound"]},
    "bleeding_internal_severe": {"severity": 5, "kind": "internal", "stage": "severe", "areas": ["wound"]},
    "bleeding_internal_extreme": {"severity": 8, "kind": "internal", "stage": "extreme", "areas": ["wound"]},
}


def get_effect_type_options() -> List[Dict[str, str]]:
    return [
        {"value": key, "label": meta["label"], "group": meta["group"]}
        for key, meta in EFFECT_TYPE_META.items()
    ]


def create_effect_draft(effect_type: str = "generic", overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    overrides = overrides or {}
    normalized_type = canonical_type(effect_type, overrides.get("name", ""))
    meta = get_effect_meta(normalized_type)
    remaining = overrides.get("remaining", overrides.get("duration", None))
    result = {
        "id": overrides.get("id") or None,
        "type": normalized_type,
        "name": overrides.get("name") or meta["label"],
        "value": overrides.get("value", 0),
        "duration": overrides.get("duration", None),
        "remaining": remaining,
        "stacks": max(1, _to_int(overrides.get("stacks", 1), 1)),
        "source": overrides.get("source"),
        "area": overrides.get("area") or overrides.get("zone") or overrides.get("bodyPart") or overrides.get("target"),
        "note": overrides.get("note", ""),
        "tick": overrides.get("tick", "manual"),
        "scope": overrides.get("scope", "character"),
        "active": overrides.get("active", True),
    }
    if normalized_type == "organ_loss":
        result["treatment_window_seconds"] = overrides.get("treatment_window_seconds", 3600)
        result["treatment_window_expired"] = overrides.get("treatment_window_expired", False)
    return result


def normalize_effect(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return create_effect_draft()
    if isinstance(raw, str):
        return create_effect_draft(canonical_type(raw, raw), {"name": raw})
    if not isinstance(raw, dict):
        return create_effect_draft("generic", {"value": raw})

    source = raw.get("source") or raw.get("origin")
    effect_type = canonical_type(raw.get("type") or raw.get("kind") or raw.get("effectType"), raw.get("name", ""))
    if effect_type=="generic" and str(raw.get("name") or "").casefold()=="контузия":
        effect_type="concussion"
    if effect_type=="generic" and str(raw.get("name") or "").casefold()=="горение":
        effect_type="burning"
    if source == "stress_manifestation" and effect_type == "generic":
        effect_type = "stress_effect"
    value = raw.get("value", raw.get("amount", raw.get("power", 0)))
    duration = raw.get("duration", raw.get("turns", raw.get("remaining", None)))
    remaining = raw.get("remaining", duration)

    data = create_effect_draft(effect_type, {
        "id": raw.get("id"),
        "name": raw.get("name") or raw.get("label") or get_effect_meta(effect_type)["label"],
        "value": _to_int(value, 0),
        "duration": None if duration in (None, "") else _to_int(duration, None),
        "remaining": None if remaining in (None, "") else _to_int(remaining, None),
        "stacks": raw.get("stacks", 1),
        "source": source,
        "note": raw.get("note") or raw.get("description", ""),
        "tick": raw.get("tick") or raw.get("tickPhase") or "manual",
        "scope": raw.get("scope", "character"),
        "active": raw.get("active", True),
        "area": raw.get("area") or raw.get("zone") or raw.get("bodyPart") or raw.get("target"),
        "treatment_window_seconds": raw.get("treatment_window_seconds", 3600),
        "treatment_window_expired": raw.get("treatment_window_expired", False),
    })
    if effect_type=="concussion":
        data["rollPenalty"]=3
    # Consumables attach executable metadata (delayed adjustments, expiry
    # consequences, wound state). Keep it while still normalizing core fields.
    for key, value in raw.items():
        if key not in {"type", "kind", "effectType", "turns", "tickPhase", "zone", "bodyPart", "target"}:
            data.setdefault(key, value)
    if effect_type=='burning':
        data['tick']='round_end'
        data['damage_per_round']=data.get('damage_per_round') or 50
        data.setdefault('thermal_threshold',35)
    generic_names = {
        "", "общий", "generic", "неопределенный эффект", "неопределённый эффект",
    }
    if str(data.get("name") or "").strip().lower() in generic_names and effect_type != "generic":
        data["name"] = get_effect_meta(effect_type)["label"]
    if effect_type == "organ_loss" and data.get("area"):
        organ_label = ORGAN_LABELS.get(data["area"], data["area"])
        data["name"] = f"Повреждённый орган: {organ_label}"
    max_hours = _to_int(data.get("max_hours"), 0)
    if data.get("remaining") is None and max_hours > 0:
        data["remaining"] = max_hours
        data["duration"] = data.get("duration") or max_hours
        data["time_unit"] = "hour"
    return data


def _is_legacy_additional_trauma_effect(effect: Any) -> bool:
    """Identify combat roll reports that were accidentally stored as effects."""
    if not isinstance(effect, dict):
        return False
    raw_type = str(
        effect.get("type") or effect.get("kind") or effect.get("effectType") or ""
    ).strip().lower()
    if raw_type not in {"additional_trauma", "generic"}:
        return False
    outcome_keys = {
        "fracture", "bleeding", "pain", "shock", "organ", "fall_or_drop"
    }
    return (
        "chance_roll" in effect
        and "roll" in effect
        and len(outcome_keys.intersection(effect)) >= 3
    )


def normalize_effect_list(effects: Any) -> List[Dict[str, Any]]:
    if not isinstance(effects, list):
        return []
    return [
        normalize_effect(effect)
        for effect in effects
        if not _is_legacy_additional_trauma_effect(effect)
    ]


def _bleeding_rule(effect_type: str) -> Optional[Dict[str, Any]]:
    return BLEEDING_EFFECT_RULES.get(effect_type) if effect_type in BLEEDING_EFFECT_RULES else None


def _bleeding_stage_value(stage: Any) -> int:
    try:
        return BLEEDING_STAGE_ORDER.index(str(stage or "normal").lower())
    except ValueError:
        return 0


def get_bleeding_state(health: Dict[str, Any]) -> Dict[str, Any]:
    effects = normalize_effect_list((health or {}).get("effects") or [])
    combat_meta = (health or {}).get("combatMeta") or {}
    breakdown = {
        "external": {"light": 0, "medium": 0, "severe": 0, "extreme": 0, "total": 0},
        "internal": {"light": 0, "medium": 0, "severe": 0, "extreme": 0, "total": 0},
    }
    effect_details: List[Dict[str, Any]] = []
    total_severity = 0

    for effect in effects:
        rule = _bleeding_rule(effect["type"])
        if not rule or not effect.get("active", True) or effect.get("closed") or effect.get("suppressed"):
            continue
        stacks = max(1, _to_int(effect.get("stacks", 1), 1))
        base_severity = max(1, _to_int(effect.get("value", 0), rule.get("severity", 1)))
        resolved_severity = max(rule.get("severity", 1), base_severity) * stacks
        group = rule.get("kind", "external")
        stage = rule.get("stage", "light")
        if group not in breakdown:
            continue
        breakdown[group][stage] = breakdown[group].get(stage, 0) + stacks
        breakdown[group]["total"] += resolved_severity
        total_severity += resolved_severity
        effect_details.append({
            "id": effect.get("id"),
            "type": effect["type"],
            "name": effect.get("name") or get_effect_meta(effect["type"])["label"],
            "kind": group,
            "stage": stage,
            "severity": resolved_severity,
            "stacks": stacks,
            "area": effect.get("area"),
            "treated": bool(effect.get("treated", False)),
        })

    blood_stage = str((health or {}).get("blood") or (health or {}).get("bloodStage") or "normal").lower()
    stage_penalty = _bleeding_stage_value(blood_stage)
    if isinstance(combat_meta.get("bleedingModifiers"), list):
        modifier_total = sum(_to_int(item.get("value", item), 0) if isinstance(item, dict) else _to_int(item, 0) for item in combat_meta.get("bleedingModifiers", []))
    else:
        modifier_total = _to_int(combat_meta.get("bleedingModifierTotal", (health or {}).get("bleedingModifierTotal", 0)), 0)
    return {
        "baseDifficulty": 5,
        "totalSeverity": total_severity,
        "bloodStage": blood_stage,
        "stagePenalty": stage_penalty,
        "modifierTotal": modifier_total,
        "difficulty": max(0, 5 + total_severity - stage_penalty + modifier_total),
        "breakdown": breakdown,
        "effects": effect_details,
    }


def sync_health_derived_statuses(health: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(health, dict):
        return health
    effects = normalize_effect_list(health.get("effects") or [])
    combat_meta = health.setdefault("combatMeta", {})
    intoxication = _to_float(health.get("intoxication"), 0)
    if intoxication < 100:
        combat_meta.pop("intoxicationDeathChecked", None)
        combat_meta.pop("intoxicationDeathRoll", None)
    elif not combat_meta.get("intoxicationDeathChecked"):
        death_roll = random.randint(1, 100)
        combat_meta["intoxicationDeathChecked"] = True
        combat_meta["intoxicationDeathRoll"] = death_roll
        if death_roll <= 15 and not any(effect.get("type") == "death" for effect in effects):
            effects.append(normalize_effect({
                "type": "death", "name": "Смерть от опьянения",
                "source": "deadly_intoxication", "tick": "manual",
            }))
    organs = health.get("organs") or {}
    brain = organs.get("brain")
    skull = organs.get("skull")
    fatal_total_health = (
        health.get("current") is not None
        and health.get("max") is not None
        and _to_float(health.get("max"), 0) > 0
        and _to_float(health.get("current"), 0) <= 0
    )
    fatal_brain_health = isinstance(brain, dict) and _to_float(brain.get("current"), 1) <= 0
    fatal_skull_health = isinstance(skull, dict) and _to_float(skull.get("current"), 1) <= 0
    if (fatal_total_health or fatal_brain_health or fatal_skull_health) and not any(
        effect.get("type") == "death" for effect in effects
    ):
        death_source = (
            "zero_brain_health" if fatal_brain_health
            else "zero_skull_health" if fatal_skull_health
            else "zero_total_health"
        )
        effects.append(normalize_effect({
            "type": "death",
            "name": "Смерть",
            "source": death_source,
            "tick": "manual",
        }))
    if _to_int(health.get("painLevel", 0), 0) >= 10 and not any(
        effect.get("type") == "shock" and effect.get("active", True)
        for effect in effects
    ):
        effects.append(normalize_effect({
            "type": "shock",
            "name": "Болевой шок",
            "source": "maximum_pain",
            "tick": "manual",
        }))
    health["effects"] = effects
    bleeding = get_bleeding_state(health)
    health["bleeding"] = bleeding
    health["bleedingSeverity"] = bleeding["totalSeverity"]
    health["bleedingDifficulty"] = bleeding["difficulty"]
    health["bleedingStagePenalty"] = bleeding["stagePenalty"]
    health["bleedingModifierTotal"] = bleeding["modifierTotal"]
    health["bloodStage"] = bleeding["bloodStage"]
    health["bleedingEffects"] = bleeding["effects"]
    return health


def normalize_character_effects(character_data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(character_data, dict):
        return character_data
    health = character_data.get("health")
    if isinstance(health, dict) and isinstance(health.get("effects"), list):
        health["effects"] = normalize_effect_list(health["effects"])
        sync_health_derived_statuses(health)
    if isinstance(character_data.get("effects"), list):
        character_data["effects"] = normalize_effect_list(character_data["effects"])
    return character_data


def get_effect_impact(effect: Any) -> Dict[str, Any]:
    normalized = normalize_effect(effect)
    rule = EFFECT_IMPACT_RULES.get(normalized["type"], EFFECT_IMPACT_RULES["generic"])
    areas = list(rule.get("areas", []))
    if normalized.get("area") and normalized["area"] not in areas:
        areas.append(normalized["area"])
    bleeding_rule = _bleeding_rule(normalized["type"])
    return {
        "type": normalized["type"],
        "name": normalized.get("name"),
        "areas": areas,
        "requires_medicine_check": bool(rule.get("requiresMedicineCheck", False)),
        "treatment": rule.get("treatment", "manual"),
        "severity": max(1, _to_int(normalized.get("value", 0), bleeding_rule.get("severity", 1))) if bleeding_rule else None,
        "bleeding_kind": bleeding_rule.get("kind") if bleeding_rule else None,
    }


def summarize_effect_impact(effect: Any) -> str:
    impact = get_effect_impact(effect)
    areas = ", ".join(impact["areas"]) if impact["areas"] else "нет"
    treatment = "требует Медицины" if impact["requires_medicine_check"] else "без проверки"
    bleeding_part = f": {impact['bleeding_kind']} {impact['severity']}" if impact.get("bleeding_kind") else ""
    return f"{impact['name']}{bleeding_part}, {areas} ({treatment})"


def _adjust_field(health: Dict[str, Any], field: str, delta: int, minimum: Optional[int] = 0, maximum: Optional[int] = None) -> None:
    current = _to_int(health.get(field, 0), 0)
    health[field] = _clamp(current + delta, minimum, maximum)


def _distribute_zone_healing(health: Dict[str, Any], amount: float) -> None:
    remaining = max(0, int(_to_float(amount, 0)))
    temporary_caps = {
        str(effect.get("area") or ""): max(1, int(_to_float(effect.get("health_cap"), 1)))
        for effect in normalize_effect_list(health.get("effects") or [])
        if effect.get("type") == "temporary_limb_restoration"
        and effect.get("health_cap") is not None
        and effect.get("active", True)
        and (effect.get("remaining") is None or _to_float(effect.get("remaining"), 0) > 0)
    }
    zones = [
        (area, zone)
        for area, zone in (health.get("zones") or {}).items()
        if isinstance(zone, dict)
    ]
    for area, zone in zones:
        maximum = max(0, int(round(_to_float(zone.get("max", 0), 0))))
        maximum = min(maximum, temporary_caps.get(area, maximum))
        zone["current"] = min(maximum, max(0, int(round(_to_float(zone.get("current", 0), 0)))))
    while remaining > 0:
        damaged = [
            (area, zone) for area, zone in zones
            if _to_float(zone.get("current", 0), 0) > 0
            and _to_float(zone.get("current", 0), 0) < min(
                _to_float(zone.get("max", 0), 0),
                temporary_caps.get(area, float("inf")),
            )
        ]
        if not damaged:
            break
        share = remaining // len(damaged)
        if share == 0:
            for _, zone in damaged[:remaining]:
                zone["current"] += 1
            break
        applied = 0
        for area, zone in damaged:
            current = int(zone.get("current", 0))
            maximum = min(int(zone.get("max", 0)), temporary_caps.get(area, int(zone.get("max", 0))))
            healed = min(share, maximum - current)
            zone["current"] = current + healed
            zone["destructionDamage"] = max(0, maximum - zone["current"])
            applied += healed
        if applied <= 0:
            break
        remaining -= applied


def _heal_health_and_zones(health: Dict[str, Any], amount: float) -> None:
    """Heal the shared pool fully and distribute one healing budget over zones."""
    current = _to_float(health.get("current", 0), 0)
    max_value = health.get("max")
    max_value = None if max_value in (None, "") else _to_float(max_value, None)
    health["current"] = _clamp(current + amount, 0, max_value)
    _distribute_zone_healing(health, amount)


def _restore_limb_health_and_pool(health: Dict[str, Any], area: str, restored_health: Any) -> None:
    """Set a treated limb's health and add only the recovered amount to the pool."""
    zone = (health.get("zones") or {}).get(area)
    if not isinstance(zone, dict) or restored_health is None:
        return
    maximum = _to_float(zone.get("max"), _to_float(restored_health, 0))
    before = max(0, _to_float(zone.get("current"), 0))
    after = min(maximum, max(0, _to_float(restored_health, 0)))
    zone["current"] = after
    zone["destructionDamage"] = max(0, maximum - after)
    recovered = max(0, after - before)
    if recovered <= 0:
        return
    pool_max = health.get("max")
    maximum_pool = None if pool_max in (None, "") else _to_float(pool_max, None)
    health["current"] = _clamp(_to_float(health.get("current"), 0) + recovered, 0, maximum_pool)


def health_blocks_new_stress(health: Dict[str, Any]) -> bool:
    """Смерть/потеря сознания по физическим условиям _character_condition, без RNG."""
    active_types = {
        effect.get("type") for effect in normalize_effect_list(health.get("effects") or [])
        if effect.get("active", True)
        and (effect.get("remaining") is None or _to_float(effect.get("remaining"), 0) > 0)
    }
    if active_types.intersection({"death", "unconsciousness", "sleep", "critical_condition"}):
        return True
    zones = health.get("zones") if isinstance(health.get("zones"), dict) else {}
    organs = health.get("organs") if isinstance(health.get("organs"), dict) else {}
    return bool(
        _to_float(health.get("current"), 1) <= 0
        or any(_to_float((zones.get(zone) or {}).get("current"), 1) <= 0
               for zone in ("head", "chest"))
        or any(_to_float((organs.get(organ) or {}).get("current"), 1) <= 0
               for organ in ("brain", "skull"))
        or str(health.get("bloodStage") or health.get("blood") or "normal").lower()
        in {"critical", "fatal"}
        or _to_float(health.get("temperature"), 36) <= 29
        or _to_float(health.get("temperature"), 36) >= 41
        or _to_float(health.get("intoxication"), 0) >= 96
    )


def apply_effect_to_health(health: Dict[str, Any], raw_effect: Any) -> Dict[str, Any]:
    effect = normalize_effect(raw_effect)
    if effect['type']=='concussion':
        current=normalize_effect_list(health.get('effects') or [])
        previous=[e for e in current if e['type']=='concussion' and e.get('active',True)]
        effect['remaining']=max([effect.get('remaining') or 0]+[e.get('remaining') or 0 for e in previous]) or None
        health['effects']=[e for e in current if e['type']!='concussion']+[effect]
        sync_health_derived_statuses(health)
        return health
    signed_value = _to_float(effect.get("value", 0), 0)
    magnitude = abs(_to_float(effect.get("value", 0), 0))
    effect_type = effect["type"]

    new_stress = (
        (effect_type == "stress" and signed_value > 0)
        or effect_type in {"stress_effect", "stress_stupor"}
        or (effect_type == "phobia" and effect.get("source") == "stress_manifestation")
    )
    if new_stress and health_blocks_new_stress(health):
        return health

    active_effects = normalize_effect_list(health.get("effects") or [])
    if effect_type.startswith("bleeding_") and any(
        item.get("type") == "bleeding_prevention" and item.get("active", True)
        for item in active_effects
    ):
        return health

    if effect_type == "heal":
        _heal_health_and_zones(health, magnitude)
        return health

    if effect_type == "regeneration":
        health.setdefault("effects", [])
        existing = None
        for idx, item in enumerate(health["effects"]):
            current = normalize_effect(item)
            same_id = effect.get("id") and current.get("id") == effect.get("id")
            if same_id or (
                not effect_type.startswith("bleeding_")
                and current["type"] == effect_type
                and (current.get("source") or None) == (effect.get("source") or None)
                and (current.get("area") or None) == (effect.get("area") or None)
            ):
                existing = idx
                break
        if existing is None:
            health["effects"].append(effect)
        else:
            current = normalize_effect(health["effects"][existing])
            current.update(effect)
            current["stacks"] = max(_to_int(current.get("stacks", 1), 1), _to_int(effect.get("stacks", 1), 1))
            health["effects"][existing] = current
        sync_health_derived_statuses(health)
        return health

    if effect_type == "radiation":
        _adjust_field(health, "radiation", int(signed_value) if float(signed_value).is_integer() else signed_value, 0, None)
        return health

    if effect_type == "pain":
        pain_delta = int(signed_value) if float(signed_value).is_integer() else signed_value
        blockers = [item for item in active_effects if item.get("blocks_new_pain") and item.get("active", True)]
        if pain_delta > 0 and blockers:
            meta = health.setdefault("combatMeta", {})
            meta["blockedPain"] = _to_float(meta.get("blockedPain", 0), 0) + pain_delta
            return health
        _adjust_field(health, "painLevel", pain_delta, 0, 10)
        meta = health.setdefault("combatMeta", {})
        meta["painIncreased"] = True
        return health

    if effect_type == "exhaustion":
        _adjust_field(health, "exhaustion", int(signed_value) if float(signed_value).is_integer() else signed_value, 0, 10)
        return health

    if effect_type == "stress":
        _adjust_field(health, "stress", int(signed_value) if float(signed_value).is_integer() else signed_value, 0, 10)
        return health

    if effect_type == "intoxication":
        _adjust_field(health, "intoxication", int(signed_value) if float(signed_value).is_integer() else signed_value, 0, 100)
        return health

    if effect_type == "infection":
        _adjust_field(health, "infection", int(signed_value) if float(signed_value).is_integer() else signed_value, 0, 100)
        sync_health_derived_statuses(health)
        return health

    if effect_type in {
        "bleeding",
        "fracture",
        "fracture_fixed",
        "fracture_unfixed",
        "fracture_sequela",
        "shock",
        "unconsciousness",
        "critical_condition",
        "death",
        "blindness",
        "deafness",
        "bleeding_external_light",
        "bleeding_external_medium",
        "bleeding_external_severe",
        "bleeding_external_extreme",
        "bleeding_internal_light",
        "bleeding_internal_medium",
        "bleeding_internal_severe",
        "bleeding_internal_extreme",
    }:
        health.setdefault("effects", [])
        existing = None
        for idx, item in enumerate(health["effects"]):
            current = normalize_effect(item)
            same_id = effect.get("id") and current.get("id") == effect.get("id")
            if same_id or (
                not effect_type.startswith("bleeding_")
                and current["type"] == effect_type
                and (current.get("source") or None) == (effect.get("source") or None)
                and (current.get("area") or None) == (effect.get("area") or None)
            ):
                existing = idx
                break

        is_new_effect = existing is None
        if is_new_effect:
            health["effects"].append(effect)
        else:
            current = normalize_effect(health["effects"][existing])
            current.update(effect)
            current["stacks"] = max(_to_int(current.get("stacks", 1), 1), _to_int(effect.get("stacks", 1), 1))
            health["effects"][existing] = current
        if effect_type == "fracture" and is_new_effect:
            health["effects"][-1]["regular_fixation_seconds"] = _to_float(
                effect.get("regular_fixation_seconds"), 1800
            )
            health["effects"][-1]["hinged_fixation_seconds"] = _to_float(
                effect.get("hinged_fixation_seconds"), 1800
            )
            apply_effect_to_health(health, {
                "type": "pain",
                "value": 3,
                "source": "fracture",
                "area": effect.get("area"),
            })
        sync_health_derived_statuses(health)
        return health

    health.setdefault("effects", [])
    existing = None
    for idx, item in enumerate(health["effects"]):
        current = normalize_effect(item)
        if (
            (effect.get("id") and current.get("id") == effect.get("id"))
            or (
                current["type"] == effect_type
                and (current.get("source") or None) == (effect.get("source") or None)
                and (current.get("area") or None) == (effect.get("area") or None)
            )
        ):
            existing = idx
            break

    if existing is None:
        health["effects"].append(effect)
    else:
        current = normalize_effect(health["effects"][existing])
        current.update(effect)
        current["stacks"] = max(_to_int(current.get("stacks", 1), 1), _to_int(effect.get("stacks", 1), 1))
        health["effects"][existing] = current
    sync_health_derived_statuses(health)
    return health


def tick_effect(effect: Dict[str, Any], phase: str = "turn_end") -> Dict[str, Any]:
    normalized = normalize_effect(effect)
    if not normalized.get("active", True):
        return normalized
    if normalized.get("tick") == "manual" or normalized.get("tick") not in (None, phase):
        return normalized
    if normalized.get("remaining") is not None:
        normalized["remaining"] = max(0, _to_int(normalized.get("remaining"), 0) - 1)
    return normalized


def apply_periodic_effects_to_health(health: Dict[str, Any], effects: Iterable[Any], phase: str = "turn_end") -> Dict[str, Any]:
    if not isinstance(health, dict):
        return health
    for raw_effect in effects or []:
        effect = normalize_effect(raw_effect)
        if not effect.get("active", True):
            continue
        if effect.get("tick") == "manual" or effect.get("tick") not in (None, phase):
            continue
        magnitude = abs(_to_float(effect.get("value", 0), 0))
        if effect["type"] == "regeneration":
            _heal_health_and_zones(health, magnitude)
        elif effect["type"] == "radiation_treatment":
            _adjust_field(health, "radiation", -magnitude, 0, None)
        elif effect["type"] == "blood_recovery":
            order = ["normal", "light", "medium", "severe", "critical"]
            stage = str(health.get("blood") or health.get("bloodStage") or "normal").lower()
            index = order.index(stage) if stage in order else 0
            next_stage = order[max(0, index - max(1, _to_int(effect.get("value", 1), 1)))]
            health["blood"] = next_stage
            health["bloodStage"] = next_stage
        elif effect["type"] == "periodic_adjustment":
            for adjustment in effect.get("adjustments") or []:
                if not isinstance(adjustment, dict):
                    continue
                field = str(adjustment.get("field") or "").strip()
                if field:
                    _adjust_field(health, field, _to_float(adjustment.get("delta", 0), 0), adjustment.get("min", 0), adjustment.get("max"))
    sync_health_derived_statuses(health)
    return health


def apply_expired_effects_to_health(health: Dict[str, Any], effects: Iterable[Any], phase: str = "turn_end") -> Dict[str, Any]:
    activated: List[Dict[str, Any]] = []
    cured_fracture_areas = set()
    for raw_effect in effects or []:
        effect = normalize_effect(raw_effect)
        if effect.get("tick") == "manual" or effect.get("tick") not in (None, phase):
            continue
        if effect.get("remaining") is None or _to_int(effect.get("remaining"), 0) > 1:
            continue
        for adjustment in effect.get("onExpire") or effect.get("on_expire") or []:
            if not isinstance(adjustment, dict):
                continue
            field = str(adjustment.get("field") or "").strip()
            if field:
                _adjust_field(health, field, _to_float(adjustment.get("delta", 0), 0), adjustment.get("min", 0), adjustment.get("max"))
        if effect.get("type") in {"delayed_adjustment", "delayed_treatment"}:
            for adjustment in effect.get("adjustments") or []:
                if not isinstance(adjustment, dict):
                    continue
                field = str(adjustment.get("field") or "").strip()
                if field:
                    _adjust_field(health, field, _to_float(adjustment.get("delta", 0), 0), adjustment.get("min", 0), adjustment.get("max"))
        for activated_effect in effect.get("activate_effects") or effect.get("activateEffects") or []:
            if isinstance(activated_effect, dict):
                activated.append(activated_effect)
        if effect.get("death_on_expire"):
            activated.append({
                "type": "death", "name": "Смерть",
                "source": effect.get("source") or "expired_organ_failure",
                "tick": "manual",
            })
        if effect.get("type") == "pain_block":
            meta = health.setdefault("combatMeta", {})
            blocked = _to_float(meta.pop("blockedPain", 0), 0)
            returned = blocked * _to_float(effect.get("return_fraction", 1), 1)
            _adjust_field(health, "painLevel", returned, 0, 10)
            _adjust_field(health, "exhaustion", _to_float(effect.get("exhaustion_on_expire", 0), 0), 0, 10)
        if effect.get("type") == "temporary_limb_restoration" and effect.get("restore_on_expire", True):
            zone = (health.get("zones") or {}).get(effect.get("area"))
            if isinstance(zone, dict):
                zone["current"] = min(
                    _to_float(zone.get("current"), 0),
                    _to_float(effect.get("previous_health"), 0),
                )
        if effect.get("type") == "delayed_limb_treatment":
            area = str(effect.get("area") or "")
            if effect.get("cure_fracture") and area:
                cured_fracture_areas.add(area)
            _restore_limb_health_and_pool(health, area, effect.get("restore_limb_health"))
    for activated_effect in activated:
        apply_effect_to_health(health, activated_effect)
    if cured_fracture_areas:
        health["effects"] = [
            effect for effect in normalize_effect_list(health.get("effects") or [])
            if not (
                effect.get("type") in {
                    "fracture", "fracture_fixed", "fracture_unfixed", "fracture_sequela"
                }
                and str(effect.get("area") or "") in cured_fracture_areas
            )
        ]
    if health.get("max") is not None and health.get("current") is not None:
        health["current"] = min(_to_float(health.get("current"), 0), _to_float(health.get("max"), 0))
    sync_health_derived_statuses(health)
    return health


def tick_effects(effects: Iterable[Any], phase: str = "turn_end") -> List[Dict[str, Any]]:
    updated = []
    for effect in effects or []:
        normalized = tick_effect(effect, phase=phase)
        if normalized.get("remaining") == 0 and not normalized.get("persist_at_zero"):
            continue
        updated.append(normalized)
    return updated


def advance_timed_effects(
    health: Dict[str, Any],
    effects: Iterable[Any],
    elapsed_seconds: float,
    *,
    include_turn_effects: bool = False,
) -> List[Dict[str, Any]]:
    """Advance real-time effects without simulating thousands of combat ticks."""
    unit_seconds = {"second": 1, "minute": 60, "movement": 600, "hour": 3600}
    survivors = []
    activated = []
    cured_fracture_areas = set()
    elapsed = max(0.0, _to_float(elapsed_seconds, 0))

    for raw_effect in effects or []:
        effect = normalize_effect(raw_effect)
        if effect.get("type") == "fracture":
            regular = max(0, _to_float(effect.get("regular_fixation_seconds"), 1800) - elapsed)
            hinged = max(0, _to_float(effect.get("hinged_fixation_seconds"), 1800) - elapsed)
            effect["regular_fixation_seconds"] = regular
            effect["hinged_fixation_seconds"] = hinged
            effect["regular_fixation_expired"] = regular <= 0
            if hinged <= 0:
                consequence_roll = random.randint(1, 100)
                effect.update({
                    "type": "fracture_unfixed",
                    "name": "Незафиксированный перелом",
                    "tick": "manual",
                    "fixation_consequence_roll": consequence_roll,
                    "permanent_penalty": consequence_roll <= 50,
                })
                if consequence_roll <= 50:
                    activated.append({
                        "type": "fracture_sequela",
                        "name": "Постоянный штраф после перелома",
                        "area": effect.get("area"),
                        "source": "unfixed_fracture",
                        "tick": "manual",
                    })
            survivors.append(effect)
            continue
        if effect.get("treatment_window_seconds") is not None:
            treatment_window = max(
                0,
                _to_float(effect.get("treatment_window_seconds"), 0) - elapsed,
            )
            effect["treatment_window_seconds"] = treatment_window
            effect["treatment_window_expired"] = treatment_window <= 0
        unit = str(effect.get("time_unit") or "").lower()
        tick = str(effect.get("tick") or "")
        if effect.get("remaining") is None and _to_float(effect.get("max_hours"), 0) > 0:
            effect["remaining"] = _to_float(effect.get("max_hours"), 0)
            effect["time_unit"] = "hour"
            unit = "hour"
        if not unit:
            unit = {
                "time_elapsed": "minute",
                "movement_end": "movement",
                "hour_start": "hour",
            }.get(tick, "")
        if include_turn_effects and not unit and tick == "turn_end":
            unit = "second"
            effect.setdefault("seconds_per_unit", 6)
        if unit not in unit_seconds or effect.get("remaining") is None:
            survivors.append(effect)
            continue

        seconds_per_unit = max(
            0.001,
            _to_float(effect.get("seconds_per_unit"), unit_seconds[unit]),
        )
        remaining_seconds = _to_float(
            effect.get("remaining_seconds"),
            _to_float(effect.get("remaining"), 0) * seconds_per_unit,
        ) - elapsed
        if remaining_seconds > 0:
            effect["remaining_seconds"] = remaining_seconds
            effect["remaining"] = max(1, math.ceil(remaining_seconds / seconds_per_unit))
            survivors.append(effect)
            continue

        for adjustment in effect.get("onExpire") or effect.get("on_expire") or []:
            if isinstance(adjustment, dict) and adjustment.get("field"):
                _adjust_field(
                    health, str(adjustment["field"]),
                    _to_float(adjustment.get("delta", 0), 0),
                    adjustment.get("min", 0), adjustment.get("max"),
                )
        if effect.get("type") in {"delayed_adjustment", "delayed_treatment", "deferred_adjustment"}:
            for adjustment in effect.get("adjustments") or []:
                if isinstance(adjustment, dict) and adjustment.get("field"):
                    _adjust_field(
                        health, str(adjustment["field"]),
                        _to_float(adjustment.get("delta", 0), 0),
                        adjustment.get("min", 0), adjustment.get("max"),
                    )
        activated.extend(
            item for item in (effect.get("activate_effects") or effect.get("activateEffects") or [])
            if isinstance(item, dict)
        )
        if effect.get("death_on_expire"):
            activated.append({
                "type": "death", "name": "Смерть",
                "source": effect.get("source") or "expired_organ_failure",
                "tick": "manual",
            })
        if effect.get("type") == "temporary_limb_restoration" and effect.get("restore_on_expire", True):
            zone = (health.get("zones") or {}).get(effect.get("area"))
            if isinstance(zone, dict):
                zone["current"] = min(
                    _to_float(zone.get("current"), 0),
                    _to_float(effect.get("previous_health"), 0),
                )
        if effect.get("type") == "delayed_limb_treatment":
            area = str(effect.get("area") or "")
            if effect.get("cure_fracture") and area:
                cured_fracture_areas.add(area)
            _restore_limb_health_and_pool(health, area, effect.get("restore_limb_health"))

    combat_meta = health.get("combatMeta")
    if isinstance(combat_meta, dict):
        modifiers = combat_meta.get("consumableModifiers")
        if isinstance(modifiers, list):
            active_modifiers = []
            for raw_modifier in modifiers:
                if not isinstance(raw_modifier, dict):
                    active_modifiers.append(raw_modifier)
                    continue
                modifier = dict(raw_modifier)
                remaining = modifier.get("remaining")
                if remaining is None:
                    active_modifiers.append(modifier)
                    continue
                unit = str(modifier.get("time_unit") or "").lower()
                tick = str(modifier.get("tick") or "turn_end")
                if not unit:
                    unit = {
                        "turn_end": "second",
                        "time_elapsed": "minute",
                        "movement_end": "movement",
                        "hour_start": "hour",
                    }.get(tick, "")
                if unit not in unit_seconds:
                    active_modifiers.append(modifier)
                    continue
                seconds_per_unit = max(
                    0.001,
                    _to_float(
                        modifier.get("seconds_per_unit"),
                        6 if tick == "turn_end" else unit_seconds[unit],
                    ),
                )
                remaining_seconds = _to_float(
                    modifier.get("remaining_seconds"),
                    _to_float(remaining, 0) * seconds_per_unit,
                ) - elapsed
                if remaining_seconds <= 0:
                    continue
                modifier["remaining_seconds"] = remaining_seconds
                modifier["remaining"] = max(1, math.ceil(remaining_seconds / seconds_per_unit))
                active_modifiers.append(modifier)
            combat_meta["consumableModifiers"] = active_modifiers

    if cured_fracture_areas:
        survivors = [
            effect for effect in survivors
            if not (
                effect.get("type") in {
                    "fracture", "fracture_fixed", "fracture_unfixed", "fracture_sequela"
                }
                and str(effect.get("area") or "") in cured_fracture_areas
            )
        ]
    health["effects"] = survivors
    for effect in activated:
        apply_effect_to_health(health, effect)
    sync_health_derived_statuses(health)
    return normalize_effect_list(health.get("effects") or [])


def effect_summary(effect: Any) -> str:
    normalized = normalize_effect(effect)
    parts = [normalized.get("name") or get_effect_meta(normalized["type"])["label"]]
    if normalized.get("value"):
        parts.append(f"+{normalized['value']}")
    if normalized.get("remaining") is not None:
        parts.append(f"{normalized['remaining']}t")
    return " ".join(parts)
