"""Server-authoritative use of non-targeted consumables."""

from copy import deepcopy
from dataclasses import dataclass
from math import ceil, isfinite
import random

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter
from app.models.templates import ItemTemplate
from app.services.addictions import resolve_addiction_profile
from app.services.character import CharacterService
from app.services.consumable_use import apply_consumable_use, validate_consumable_use
from app.services.consumable_effects import with_darkness_modifiers
from app.services.deferred_consumable import claim_completion, finish_completion
from app.services.effects import apply_effect_to_health, normalize_effect_list, sync_health_derived_statuses
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.item_template_lookup import resolve_item_template
from app.services.magazine import inventory_slot
from app.services.medical_procedure import (
    _combat_context,
    _consume_uses,
    _profile,
    minimum_consumable_payment,
)
from app.services.transaction import atomic_operation


UNSUPPORTED_GENERAL_FIELDS = {
    "applications", "requires_injury", "target_body_part", "target_required",
    "requires_infusion_tool", "blood_compatibility_required", "blood_collection",
    "blood_type_test", "requires_shock",
    "fracture_splint", "surgical_kit", "catastrophic_limb_surgery",
    "special_limb_treatment", "restore_missing_part", "restore_full_body_part",
    "restore_limb_health", "temporary_limb_health_minutes", "temporary_limb_health_turns",
    "affects_all_limbs", "close_area_bleeding", "filter_charges", "requires_gas_mask",
    "wound_treatment", "tourniquet", "limb_only", "stop_all_bleeding",
    "bleeding_stop_light_cost", "bleeding_stop_medium_cost",
}


@dataclass
class GeneralConsumableResult:
    character: LobbyCharacter
    result: dict
    posture_updates: list


def _number(value, default=0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _adjust(health, field, delta, minimum=0, maximum=None):
    if delta is None:
        return False
    amount = _number(delta)
    if not amount:
        return False
    current = _number(health.get(field), 36 if field == "temperature" else 0)
    value = current + amount
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    health[field] = value
    return True


def _source_name(item):
    template_id = item.get("templateId")
    if template_id not in (None, ""):
        return f"consumable-template:{template_id}"
    return str(item.get("name") or "").strip() or item.get("id") or "consumable"


def _inventory_entries(data):
    entries = []

    def visit(items):
        if not isinstance(items, list):
            return
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            entries.append((items, index, item))
            visit(item.get("contents"))

    inventory = (data or {}).get("inventory") or {}
    visit(inventory.get("pockets"))
    visit(inventory.get("backpack"))
    equipment = (data or {}).get("equipment") or {}
    for container in ("belt", "vest"):
        for pouch in ((equipment.get(container) or {}).get("pouches") or []):
            if isinstance(pouch, dict):
                visit(pouch.get("contents"))
    return entries


def _template_for_item(item):
    try:
        template_id = int((item or {}).get("templateId") or 0)
    except (TypeError, ValueError):
        template_id = 0
    return db.session.get(ItemTemplate, template_id) if template_id else None


def _available_uses(item, template=None):
    attributes = (template.attributes or {}) if template else {}
    maximum = max(1, int(_number(
        item.get("maxUses") or (item.get("attributes") or {}).get("uses") or attributes.get("uses"), 1,
    )))
    quantity = max(0, int(_number(item.get("quantity"))))
    uses = max(0, int(_number(
        item.get("uses") or (item.get("attributes") or {}).get("uses_remaining"), maximum,
    )))
    return uses + max(0, quantity - 1) * maximum


def _consume_entry(parent, index, amount):
    item = parent[index]
    template = _template_for_item(item)
    maximum = max(1, int(_number(
        item.get("maxUses")
        or (item.get("attributes") or {}).get("uses")
        or ((template.attributes or {}).get("uses") if template else None),
        1,
    )))
    quantity = max(0, int(_number(item.get("quantity"))))
    uses = max(0, int(_number(
        item.get("uses") or (item.get("attributes") or {}).get("uses_remaining"), maximum,
    )))
    remaining = max(1, int(_number(amount, 1)))
    if uses + max(0, quantity - 1) * maximum < remaining:
        return False
    while remaining:
        spent = min(uses or maximum, remaining)
        uses = (uses or maximum) - spent
        remaining -= spent
        if uses <= 0:
            quantity -= 1
            uses = maximum if quantity > 0 else 0
    if quantity <= 0:
        parent.pop(index)
    else:
        item["quantity"] = quantity
        item["uses"] = uses
        item["maxUses"] = maximum
        item.setdefault("attributes", {})["uses_remaining"] = uses
    return True


def _is_alcohol(item, lobby_id=None):
    template = resolve_item_template(item, lobby_id)
    attributes = {
        **((template.attributes or {}) if template else {}),
        **((item.get("attributes") or {})),
    }
    profile = attributes.get("consumable") or {}
    direct = profile.get("direct") if isinstance(profile, dict) else {}
    if attributes.get("is_alcohol") is True or attributes.get("alcohol") is True or direct.get("is_alcohol") is True:
        return True
    if isinstance(profile, dict) and isinstance(direct, dict) and direct:
        return False
    section = str(attributes.get("section") or item.get("subcategory") or "").strip().casefold()
    if section == "продукты" and _number(direct.get("intoxication_delta")) > 0:
        return True
    return any(name in str(item.get("name") or "").casefold() for name in ("водка", "самогон", "вино", "пиво", "алкогол"))


def _consume_requirements(data, direct, lobby_id=None):
    result = {}
    if direct.get("requires_water_fraction"):
        charges = max(1, ceil(_number(direct["requires_water_fraction"]) * 3 - 1e-6))
        water = next((
            entry for entry in _inventory_entries(data)
            if _is_water_supply(entry[2], lobby_id)
            and _available_uses(entry[2], _template_for_item(entry[2])) >= charges
        ), None)
        if water:
            _consume_entry(water[0], water[1], charges)
            result["water_charges"] = charges
        elif direct.get("water_or_alcohol"):
            alcohol = next((
                entry for entry in _inventory_entries(data)
                if _is_alcohol(entry[2], lobby_id) and _available_uses(entry[2], _template_for_item(entry[2])) >= 1
            ), None)
            if not alcohol:
                if not direct.get("exhaustion_if_no_water"):
                    raise ValidationError("Для использования не хватает воды или алкоголя")
                direct["exhaustion_delta"] = _number(direct.get("exhaustion_delta")) + _number(
                    direct.get("exhaustion_if_no_water")
                )
                result["without_water"] = True
            else:
                result["alcohol"] = alcohol[2].get("name") or "Алкоголь"
                _consume_entry(alcohol[0], alcohol[1], 1)
        elif direct.get("exhaustion_if_no_water"):
            direct["exhaustion_delta"] = _number(direct.get("exhaustion_delta")) + _number(
                direct.get("exhaustion_if_no_water")
            )
            result["without_water"] = True
        else:
            raise ValidationError("Для использования не хватает воды")

    if direct.get("requires_fire"):
        fire_sources = [
            entry for entry in _inventory_entries(data)
            if int(_number(entry[2].get("quantity"))) > 0
            and any(name in str(entry[2].get("name") or "").casefold() for name in ("зажигал", "спич"))
        ]
        lighter = next((entry for entry in fire_sources if "зажигал" in str(entry[2].get("name") or "").casefold()), None)
        matches = next((entry for entry in fire_sources if "спич" in str(entry[2].get("name") or "").casefold()), None)
        if not lighter and not matches:
            raise ValidationError("Для курения нужна зажигалка или спичка")
        if matches and not lighter:
            result["fire_source"] = matches[2].get("name") or "Спички"
            _consume_entry(matches[0], matches[1], 1)
        else:
            result["fire_source"] = lighter[2].get("name") or "Зажигалка"
    return result


def _is_water_supply(item, lobby_id=None):
    template = resolve_item_template(item, lobby_id)
    profile = ((template.attributes or {}).get("consumable") or {}) if template else {}
    direct = profile.get("direct") if isinstance(profile, dict) else None
    if isinstance(direct, dict) and direct:
        return bool(direct.get("water_source") or (direct.get("satisfy_water") and direct.get("nutrition") is None))
    return str(item.get("name") or "").strip().casefold() == "вода"


def _validate_general_profile(direct, application):
    if not isinstance(application, dict) or application.get("kind") != "self":
        raise ValidationError("Этот расходник требует выбора медицинской процедуры")
    blocked = sorted(key for key in UNSUPPORTED_GENERAL_FIELDS if direct.get(key))
    if blocked:
        raise ValidationError("Этот расходник нужно применять через медицинское действие")
    if direct.get("medical_difficulty") is not None or direct.get("application_form") == "injectable":
        raise ValidationError("Этот препарат требует проверки Медицины")


def _effect_types_replaced_by_direct(direct):
    result = set()
    if direct.get("hp") is not None:
        result.update({"heal", "damage"})
    for key, effect_type in (
        ("radiation_delta", "radiation"), ("intoxication_delta", "intoxication"),
        ("exhaustion_delta", "exhaustion"), ("stress_delta", "stress"),
        ("stress_in_combat_delta", "stress"), ("stress_safe_delta", "stress"),
        ("pain_delta", "pain"), ("infection_delta", "infection"),
        ("temperature_delta", "temperature"), ("psy_delta", "psy"),
        ("strength_delta", "strength"), ("agility_delta", "agility"),
        ("accuracy_delta", "accuracy"), ("weight_delta", "weight"),
        ("will_delta", "will"), ("psy_defense_delta", "psy_defense"),
    ):
        if direct.get(key) is not None:
            result.add(effect_type)
    return result


def _modifier_seconds(remaining, tick):
    seconds_per_unit = {
        "turn_end": 6,
        "time_elapsed": 60,
        "movement_end": 600,
        "hour_start": 3600,
        "day_start": 86400,
    }.get(str(tick or "turn_end"), 6)
    return _number(remaining) * seconds_per_unit


def _add_modifier(health, stat, value, remaining, tick, note, source):
    if remaining is None or _number(remaining) <= 0:
        return False
    modifiers = health.setdefault("combatMeta", {}).setdefault("consumableModifiers", [])
    replacement = {
        "stat": str(stat or "generic"), "value": _number(value),
        "remaining": _number(remaining), "tick": tick or "turn_end", "note": note or "",
        "source": source,
        "remaining_seconds": _modifier_seconds(remaining, tick),
    }
    existing = next((
        modifier for modifier in modifiers
        if isinstance(modifier, dict)
        and str(modifier.get("stat") or "generic") == replacement["stat"]
        and str(modifier.get("source") or "") == str(source or "")
        and str(modifier.get("note") or "") == replacement["note"]
    ), None)
    if existing is None:
        modifiers.append(replacement)
    else:
        existing.update(replacement)
    return True


def _apply_profile(health, profile, direct, item, *, in_combat,character_data=None):
    from app.services.character_traits import consumable_profile
    profile,direct=consumable_profile(profile,direct,item,character_data)
    changed = False
    source = _source_name(item)
    replaced_types = _effect_types_replaced_by_direct(direct)
    has_profile_radiation_filter = False
    for raw_effect in profile.get("effects") or []:
        if not isinstance(raw_effect, dict) or raw_effect.get("source") == "direct":
            continue
        if str(raw_effect.get("type") or "").strip().lower() in replaced_types:
            continue
        effect = deepcopy(raw_effect)
        effect["source"] = effect.get("source") or source
        if effect.get("type") == "radiation_filter":
            has_profile_radiation_filter = True
            hours = max(1, _number(effect.get("remaining"), _number(effect.get("max_hours"), 24)))
            effect.update({
                "remaining": hours, "duration": hours, "tick": "time_elapsed",
                "time_unit": "hour", "remaining_seconds": hours * 3600,
            })
            capacity = _number(effect.get("capacity"), _number(direct.get("radiation_filter_capacity")))
            effect["capacity"] = capacity
            effect["remaining_capacity"] = capacity
        elif effect.get("tick") == "time_elapsed" and effect.get("remaining") is not None:
            unit_seconds = {"second": 1, "minute": 60, "hour": 3600}
            seconds = unit_seconds.get(str(effect.get("time_unit") or "minute"), 60)
            effect["remaining_seconds"] = _number(effect["remaining"]) * seconds
        apply_effect_to_health(health, effect, character_data=character_data)
        changed = True

    if not has_profile_radiation_filter and _number(direct.get("radiation_filter_percent")) > 0:
        percent = _number(direct["radiation_filter_percent"])
        capacity = _number(direct.get("radiation_filter_capacity"))
        apply_effect_to_health(health, {
            "type": "radiation_filter", "name": f"Выведение входящей радиации {percent:g}%",
            "value": percent, "remaining": 24, "duration": 24, "tick": "time_elapsed",
            "time_unit": "hour", "remaining_seconds": 86400, "capacity": capacity,
            "remaining_capacity": capacity, "max_hours": 24, "source": source,
        })
        changed = True

    item_name = str(item.get("name") or "")
    if direct.get("hp") is not None:
        hp = _number(direct.get("hp"))
        if hp > 0:
            apply_effect_to_health(health, {"type": "heal", "value": hp, "source": source})
        elif hp < 0:
            health["current"] = max(0, _number(health.get("current")) + hp)
        changed = changed or bool(hp)

    radiation_delta = direct.get("radiation_delta")
    current_radiation = _number(health.get("radiation"))
    food = direct.get("nutrition") is not None or direct.get("satisfy_food")
    reduction_ceiling = direct.get("radiation_reduction_ceiling")
    if reduction_ceiling is None and _number(radiation_delta) < 0:
        # Profiles imported before the ceiling field can still be identified by
        # their effects, without depending on the inventory item's display name.
        if (direct.get("requires_fire") and _number(direct.get("uses"), 1) != 5) or (
            direct.get("is_alcohol") and _number(radiation_delta) == -1
        ):
            reduction_ceiling = 50
        elif food:
            reduction_ceiling = 75
    if _number(radiation_delta) < 0 and reduction_ceiling is not None and current_radiation > _number(reduction_ceiling):
        radiation_delta = None

    changed |= _adjust(health, "radiation", radiation_delta, 0)
    changed |= _adjust(health, "temperature", direct.get("temperature_delta"), 0)
    changed |= _adjust(health, "psyState", direct.get("psy_delta"), 0, 50)
    changed |= _adjust(health, "infection", direct.get("infection_delta"), 0, 100)
    changed |= _adjust(health, "painLevel", direct.get("pain_delta"), 0, 10)
    changed |= _adjust(health, "exhaustion", direct.get("exhaustion_delta"), 0, 10)
    stress_before = _number(health.get("stress"))
    changed |= _adjust(health, "stress", direct.get("stress_delta"), 0, 10)
    changed |= _adjust(
        health, "stress",
        direct.get("stress_in_combat_delta") if in_combat else direct.get("stress_safe_delta"),
        0, 10,
    )
    stress_after = _number(health.get("stress"))
    if stress_after < stress_before:
        for effect in health.get("effects") or []:
            if effect.get("expires_on_stress_decrease"):
                effect["active"] = False
            if stress_after == 0 and effect.get("expires_on_stress_zero"):
                effect["active"] = False
    changed |= _adjust(health, "intoxication", direct.get("intoxication_delta"), 0, 100)

    meta = health.setdefault("combatMeta", {})
    duration = direct.get("duration")
    tick = direct.get("duration_phase") or "turn_end"
    def positive_duration(stat,value,remaining,phase):
        beneficial=stat in {'strength','agility','accuracy','will','psy_defense','action_points','organ_toughness_multiplier','hp_max'} and _number(value)>0 or stat in {'weight','movement_points'} and _number(value)<0
        if beneficial and phase in {'turn_end','round_end'} and _number(remaining)>1:
            return _number(remaining)+_number(direct.get('positive_duration_bonus'))
        return remaining
    for stat in ("strength", "agility", "accuracy", "weight", "movement_points", "will", "psy_defense"):
        value = direct.get(f"{stat}_delta")
        if value is not None:
            changed |= _add_modifier(health, stat, value, positive_duration(stat,value,duration,tick), tick, "stat_bonus", source)
    for modifier in profile.get("modifiers") or []:
        if not isinstance(modifier, dict):
            continue
        stat = str(modifier.get("stat") or "generic")
        if direct.get(f"{stat}_delta") is not None:
            continue
        changed |= _add_modifier(
            health, stat, modifier.get("value"), positive_duration(stat,modifier.get('value'),modifier.get("remaining", duration),modifier.get('tick') or tick),
            modifier.get("tick") or tick, modifier.get("note") or "", source,
        )

    crash_source = f"{source}:crash"
    active_crash = next((
        effect for effect in health.get("effects", [])
        if isinstance(effect, dict)
        and effect.get("type") == "stimulant_crash"
        and effect.get("source") == crash_source
        and effect.get("active", True)
    ), None)
    max_return_source=f'{source}:max-health-return'
    active_max_return=next((e for e in health.get('effects',[]) if e.get('source')==max_return_source and e.get('active',True)),None)
    if direct.get("hp_max_delta") is not None:
        delta = _number(direct.get("hp_max_delta"))
        if active_crash is None and active_max_return is None:
            health["max"] = max(1, _number(health.get("max")) + delta)
        changed = True
    if direct.get("post_duration_hp_delta") is not None or direct.get("hp_max_delta") is not None:
        on_expire = []
        if direct.get("post_duration_hp_delta") is not None:
            previous_delta = sum(
                _number(adjustment.get("delta"))
                for adjustment in (active_crash or {}).get("onExpire", [])
                if isinstance(adjustment, dict) and adjustment.get("field") == "current"
            )
            on_expire.append({
                "field": "current",
                "delta": previous_delta + _number(direct["post_duration_hp_delta"]),
                "min": 0,
            })
        if direct.get("hp_max_delta") is not None:
            adjustment={"field": "max", "delta": -_number(direct["hp_max_delta"]), "min": 1}
            max_duration=positive_duration('hp_max',direct['hp_max_delta'],duration,tick)
            if max_duration!=duration:
                apply_effect_to_health(health,{'type':'temporary_hp_max','name':f'{item_name}: окончание бонуса ОЗ',
                    'source':max_return_source,'remaining':max_duration,'tick':tick,
                    'remaining_seconds':_modifier_seconds(max_duration,tick),'onExpire':[adjustment]})
            else:on_expire.append(adjustment)
        apply_effect_to_health(health, {
            "type": "stimulant_crash", "name": f"{item_name}: окончание действия",
            "remaining": _number(duration, 1), "tick": tick, "source": crash_source,
            "remaining_seconds": _modifier_seconds(_number(duration, 1), tick),
            "onExpire": on_expire,
        })
        changed = True
    if direct.get("organ_toughness_multiplier") is not None:
        changed |= _add_modifier(
            health, "organ_toughness_multiplier", direct.get("organ_toughness_multiplier"),
            positive_duration('organ_toughness_multiplier',direct.get('organ_toughness_multiplier'),_number(duration,1),tick), tick, item_name, source,
        )
    if direct.get("action_points_duration"):
        changed |= _add_modifier(
            health, "action_points", direct.get("action_points_delta"),
            direct.get("action_points_duration"), "turn_end", item_name, source,
        )

    if direct.get("pain_block_turns"):
        turns = _number(direct["pain_block_turns"])
        apply_effect_to_health(health, {
            "type": "pain_block", "name": f"{item_name}: блок боли", "value": turns,
            "remaining": turns, "tick": "turn_end", "source": source,
            "blocks_new_pain": True,
            "return_fraction": _number(direct.get("blocked_pain_return_fraction"), 1),
            "exhaustion_on_expire": _number(direct.get("exhaustion_on_expire")),
        })
        changed = True
    for field, target in (
        ("pain_block_turns", "painBlockTurns"), ("stress_block_turns", "stressBlockTurns"),
        ("addiction_block_hours", "addictionBlockHours"), ("sleep_block_hours", "sleepBlockHours"),
        ("will_shock_bonus", "willShockBonus"),
    ):
        if direct.get(field) is not None:
            meta[target] = _number(direct[field])
            changed = True
    if direct.get("will_shock_advantage") is not None:
        meta["willShockAdvantage"] = bool(direct["will_shock_advantage"])
        changed = True

    stress_advantage = bool(direct.get("stress_advantage"))
    attack_block_chance = _number(direct.get("stress_attack_block_chance"))
    if stress_advantage or attack_block_chance > 0:
        effect_duration = max(1, _number(duration, 1))
        effect = {
            "type": "stress_resistance",
            "name": f"{item_name}: сопротивление стрессу",
            "source": source,
            "remaining": effect_duration,
            "tick": tick,
            "stress_advantage": stress_advantage,
            "stress_attack_block_chance": max(0, min(100, attack_block_chance)),
        }
        if tick == "time_elapsed":
            effect.update({
                "time_unit": "minute",
                "remaining_seconds": effect_duration * 60,
            })
        apply_effect_to_health(health, effect, character_data=character_data)
        changed = True

    if direct.get("withdrawal_check_difficulty_reduction"):
        reduction = _number(direct["withdrawal_check_difficulty_reduction"])
        delay = _number(direct.get("withdrawal_support_delay_minutes"), 1)
        support_duration = _number(direct.get("withdrawal_support_duration_minutes"), 60)
        support = {
            "type": "withdrawal_support", "name": f"{item_name}: поддержка при ломке",
            "source": source, "active": True, "tick": "time_elapsed", "time_unit": "minute",
            "remaining": support_duration, "remaining_seconds": support_duration * 60,
            "withdrawal_check_difficulty_reduction": reduction,
            "forced_intoxication_stage": "extreme",
            "note": f"Сложность проверок ломки снижена на {reduction:g}.",
        }
        if delay > 0:
            apply_effect_to_health(health, {
                "type": "withdrawal_support_pending", "name": f"{item_name}: действие через {delay:g} мин.",
                "source": source, "active": True, "tick": "time_elapsed", "time_unit": "minute",
                "remaining": delay, "remaining_seconds": delay * 60, "activate_effects": [support],
            })
        else:
            apply_effect_to_health(health, support)
        changed = True

    if direct.get("bleeding_modifier_delta") is not None:
        bleeding_modifiers = meta.setdefault("bleedingModifiers", [])
        bleeding_scope = direct.get("bleeding_modifier_scope") or (
            "combat" if "hematogen_bleeding_reduction" in (profile.get("notes") or []) else "character"
        )
        replacement = {
            "stat": "bleeding", "value": _number(direct["bleeding_modifier_delta"]),
            "remaining": duration, "note": "hematogen" if bleeding_scope == "combat" else "consumable",
            "scope": bleeding_scope,
            "tick": tick, "source": source,
        }
        existing = next((
            modifier for modifier in bleeding_modifiers
            if isinstance(modifier, dict)
            and modifier.get("stat") == replacement["stat"]
            and modifier.get("source") == source
            and modifier.get("note") == replacement["note"]
        ), None)
        if existing is None:
            bleeding_modifiers.append(replacement)
        else:
            existing.update(replacement)
        changed = True
    if direct.get("nutrition") is not None:
        meta["nutrition"] = _number(meta.get("nutrition")) + _number(direct["nutrition"])
        changed = True

    if direct.get("clear_breathless"):
        old = health.get("effects") or []
        health["effects"] = [
            effect for effect in old
            if effect.get("type") not in {"breathless", "shortness_of_breath"}
        ]
        changed |= len(old) != len(health["effects"])
    removals = {str(value).strip() for value in profile.get("status_removals") or [] if str(value).strip()}
    if removals:
        old = health.get("effects") or []
        health["effects"] = [effect for effect in old if str(effect.get("type") or "").strip() not in removals]
        changed |= len(old) != len(health["effects"])
    for status in profile.get("status_additions") or []:
        status = str(status or "").strip()
        if not status:
            continue
        meta.setdefault("consumableStatusAdditions", []).append(status)
        apply_effect_to_health(health, {
            "type": status, "name": status, "value": 0,
            "remaining": duration, "tick": "manual", "source": source,
        })
        changed = True

    if direct.get("infection_block_days"):
        apply_effect_to_health(health, {
            "type": "infection_growth_block", "name": "Блок нарастания заражения",
            "remaining": _number(direct["infection_block_days"]), "tick": "day_start",
            "time_unit": "day", "source": source,
        })
        changed = True
    if direct.get("infection_block_chance") is not None:
        chance = max(0, min(100, _number(direct["infection_block_chance"])))
        if random.random() * 100 < chance:
            apply_effect_to_health(health, {
                "type": "infection_growth_block", "name": "Блок нарастания заражения",
                "remaining": 1, "tick": "day_start", "time_unit": "day", "source": source,
            })
        changed = True

    if direct.get("satisfy_sleep") or direct.get("nutrition") is not None or direct.get("satisfy_water"):
        needs = health.setdefault("needs", {})
        needs["mealsToday"] = max(0, min(3, int(_number(needs.get("mealsToday")))))
        needs["drinksToday"] = max(0, min(3, int(_number(needs.get("drinksToday")))))
        needs["sleptToday"] = needs.get("sleptToday") is True
        if direct.get("satisfy_sleep"):
            needs["sleptToday"] = True
        if direct.get("nutrition") is not None:
            needs["mealsToday"] = min(3, needs["mealsToday"] + 1)
        if direct.get("satisfy_water"):
            needs["drinksToday"] = min(3, needs["drinksToday"] + 1)
        changed = True

    return changed


class GeneralConsumableService:
    @staticmethod
    @atomic_operation
    def apply(user_id, character_id, payload):
        if not isinstance(payload, dict):
            raise ValidationError("Некорректные параметры применения расходника")
        character = db.session.get(LobbyCharacter, character_id)
        if not character:
            raise NotFoundError("Персонаж не найден")
        CharacterService.check_access(character, user_id, edit=True)
        actor_model, _, state = _combat_context(character, character, user_id, payload)
        if actor_model and not payload.get("deferred_action_id"):
            raise ValidationError("В бою применение предмета нужно сначала оплатить")

        source = payload.get("source")
        operation_id = payload.get("operation_id")
        if not isinstance(source, dict) or not isinstance(source.get("snapshot"), dict):
            raise ValidationError("Не указан выбранный расходник")
        envelope = {
            "id": operation_id,
            "deferred_action_id": payload.get("deferred_action_id"),
            "source": source,
            "combat_location_id": payload.get("combat_location_id"),
            "side_effects": {},
        }
        completion = claim_completion(character, character, user_id, envelope)
        validate_consumable_use(character, envelope)

        data = deepcopy(character.data or {})
        parent, index = inventory_slot(data, source.get("path"))
        item = parent[index]
        if item != source["snapshot"]:
            raise ConflictError("Расходник изменился. Выберите его заново.")
        template, direct = _profile(item, character.lobby_id)
        direct = deepcopy(direct)
        profile = with_darkness_modifiers(
            (template.attributes or {}).get("consumable") or {}, template.description or "",
        )
        from app.services.character_medicine import prepare_profile
        profile,direct=prepare_profile(profile,direct,item,data,template)
        application = payload.get("application")
        _validate_general_profile(direct, application)
        if completion and completion.payload["selection"].get("application") != application:
            raise ConflictError("Выбранное применение не совпадает с оплаченным")

        payment = minimum_consumable_payment(data, data, {
            "source": source, "application": application,
            "server_authoritative": True, "server_operation": "general",
        }, character.lobby_id)
        if completion and completion.payload.get("paid_action_points", -1) < payment["total_action_points"]:
            raise ConflictError("Применение предмета оплачено неверно. Отмените его и начните заново.")

        health = data.setdefault("health", {})
        usage_key = str(direct.get("exclusive_group") or item.get("templateId") or item.get("name"))
        if direct.get("use_limit"):
            usage = health.setdefault("combatMeta", {}).setdefault("consumableUsage", {})
            if _number(usage.get(usage_key)) >= _number(direct.get("use_limit"), 1):
                raise ConflictError("Лимит использования этого препарата исчерпан")

        # Spend the selected item first: a prerequisite can live earlier in the
        # same list and removing it would otherwise invalidate the saved index.
        if not direct.get("not_consumed"):
            _consume_uses(parent, index, 1, template,actor_data=data)
        requirements = _consume_requirements(data, direct, character.lobby_id)
        intoxication_before=_number(health.get('intoxication'))
        if not _apply_profile(health, profile, direct, item, in_combat=state is not None,character_data=data):
            raise ValidationError("Предмет не имеет применимых эффектов")
        if direct.get("use_limit"):
            usage = health.setdefault("combatMeta", {}).setdefault("consumableUsage", {})
            usage[usage_key] = _number(usage.get(usage_key)) + 1

        health["effects"] = normalize_effect_list(health.get("effects") or [])
        sync_health_derived_statuses(health)
        character.data = data
        flag_modified(character, "data")
        posture_updates = CharacterService.sync_location_health(character, health)

        envelope["side_effects"] = {
            "exposure": {
                "item_name": item.get("name") or template.name,
                "price": max(0, _number(template.price or item.get("price") or 0)),
                "intoxication": max(0, _number(health.get('intoxication'))-intoxication_before),
                "exhaustion_relief": max(0, -_number(direct.get("exhaustion_delta"))),
                "addiction_block_hours": max(0, _number(direct.get("addiction_block_hours"))),
            },
        }
        if state is not None and _number(direct.get("action_points_delta")):
            envelope["side_effects"]["action_points_delta"] = _number(direct["action_points_delta"])
        use_result = apply_consumable_use(
            character, character, envelope, location_id=payload.get("combat_location_id"),
            addiction_profile=resolve_addiction_profile(
                profile, (template.attributes or {}).get("catalog_name") or template.name, template.price,
            ),
        )
        finish_completion(character, completion)
        return GeneralConsumableResult(character, {
            "item_name": item.get("name") or template.name,
            "payment_details": payment,
            "requirements": requirements,
            "consumable": use_result,
        }, posture_updates)
