"""Legacy catalogue overrides represented as data rather than parser branches.

These rules preserve workbook items whose descriptions do not fully describe
their mechanics. New items should store the same fields in their own profile.
"""

from copy import deepcopy


INHIBITOR_REMOVALS = (
    "bleeding", "bleeding_external_light", "bleeding_external_medium",
    "bleeding_external_severe", "bleeding_external_extreme",
    "bleeding_internal_light", "bleeding_internal_medium",
    "bleeding_internal_severe", "bleeding_internal_extreme",
    "pain", "exhaustion", "stress", "intoxication", "infection",
    "blindness", "deafness", "shock", "unconsciousness",
    "amputation", "organ_loss",
)


EARLY_CATALOG_RULES = (
    {"contains_all": ("жаропонижающее средство", "мороз"),
     "direct": {},
     "notes": ("temperature_drop_1",)},
    {"contains": ("ингибитор",), "direct": {"delay": 1, "sleep_duration": 60},
     "status_removals": INHIBITOR_REMOVALS, "notes": ("inhibitor_cleanup",)},
    {"contains": ("подавитель эмоций",), "direct": {
        "stress_advantage": True, "stress_attack_block_chance": 50, "psy_delta": -25,
    }},
    {"contains_all": ("стимулятор", "котик"), "direct": {"addiction_block_hours": 24}},
    {"contains_all": ("психостимулирующее", "прорыв"), "direct": {
        "withdrawal_check_difficulty_reduction": 5,
        "withdrawal_support_delay_minutes": 1,
        "withdrawal_support_duration_minutes": 60,
    }},
    {"contains_any": ("препарат к.о.д.", "препарат код"), "direct": {
        "withdrawal_check_difficulty_reduction": 10,
        "withdrawal_support_delay_minutes": 1,
        "withdrawal_support_duration_minutes": 60,
    }},
    {"contains": ("стимулятор воля",), "direct": {
        "pain_block_turns": 3, "stress_block_turns": 3,
    }},
    {"contains": ("стимулятор гармония",), "direct": {
        "stress_delta": -3, "psy_delta": -20, "stress_advantage": True,
        "duration": 30, "duration_phase": "time_elapsed",
    }},
    {"contains": ("стимулятор гора-д",), "direct": {
        "strength_delta": 2, "weight_delta": -3, "hp": -100,
    }},
    {"contains": ("стимулятор мозгоправ",), "direct": {
        "psy_defense_delta": 25, "will_delta": 3,
        "exhaustion_delta": 1, "psy_delta": -5,
    }},
    {"contains": ("стимулятор покой",), "direct": {
        "stress_in_combat_delta": -3, "stress_safe_delta": -5,
        "psy_delta": -10,
    }},
    {"contains": ("стимулятор скала-н",), "direct": {
        "organ_toughness_multiplier": 2, "hp_max_delta": 200,
        "hp": 200, "temporary_hp_return": 200, "post_duration_hp_delta": -100,
    }},
    {"contains": ("стимулятор скала",), "exclude_any": ("стимулятор скала-н",),
     "direct": {"organ_toughness_multiplier": 2, "post_duration_hp_delta": -100}},
    {"contains": ("стимулятор сова-н",), "direct": {"sleep_block_hours": 8}},
    {"contains": ("стимулятор шумодав",), "direct": {
        "deafness": True, "vision_awareness_bonus": 4, "duration": 3,
    }},
    {"contains": ("губка коллагеновая",),
     "direct": {"bleeding_stop_light_cost": 1,
                "bleeding_stop_medium_cost": 2,
                "bleeding_stop_type": "external"},
     "notes": ("collagen_sponge",)},
    {"contains": ("бинт",), "unless_direct": ("bleeding_stop_light_cost",),
     "direct": {"bleeding_stop_light_cost": 1,
                "bleeding_stop_type": "external"},
     "notes": ("bandage_bleeding_stop",)},
    {"contains": ("вода",),
     "direct_defaults": {"radiation_delta": -1, "intoxication_delta": -1,
                         "exhaustion_delta": -1, "uses": 3},
     "notes": ("water_basic_cleanup",)},
    {"contains_any": ("физраствор", "соляной раствор"),
     "direct": {"not_consumed": True}, "notes": ("saline_not_consumed",)},
    {"exact": ("-",), "direct": {"nutrition": 1}, "notes": ("food",)},
    {"contains": ("хлеб",), "direct": {"nutrition": 1}, "notes": ("food",)},
    {"contains": ("протеин",),
     "direct": {"rest_heal_multiplier": 2, "requires_water_fraction": 1},
     "notes": ("protein_rest_bonus",)},
    {"contains_all": ("протеин", "здоров"),
     "direct": {"rest_heal_multiplier": 2},
     "notes": ("protein_rest_bonus_health",)},
    {"contains": ("набор для определения группы крови",),
     "exclusive_group": "blood_type_test",
     "direct": {"blood_type_test": True}, "notes": ("blood_type_test",)},
    {"contains_all": ("групп", "кров", "определ"),
     "exclusive_group": "blood_type_test",
     "direct": {"blood_type_test": True}, "notes": ("blood_type_test",)},
    {"contains_any": ("вино", "алког", "спирт"),
     "direct_defaults": {"intoxication_delta": 1},
     "notes": ("alcohol_intoxication",)},
    {"contains_all": ("рад. защита противогаза не работает", "заряд"),
     "direct": {"filter_charges": 20, "requires_gas_mask": True},
     "notes": ("gas_mask_filter",)},
    {"contains_all": ("кустарный фильтр", "заряд"),
     "direct": {"filter_charges": 10, "requires_gas_mask": True},
     "notes": ("gas_mask_filter",)},
)


EVERYDAY_CATALOG_RULES = (
    {"contains_any": ("хлеб", "колбаса", "консервы", "сушеная рыба", "сухой паек"),
     "direct_min": {"nutrition": 1}, "direct": {"satisfy_food": True}},
    {"starts_with": ("вода.",), "direct": {"satisfy_water": True, "water_source": True}},
    {"contains": (" вода.",), "direct": {"satisfy_water": True, "water_source": True}},
    {"contains": ("сухой паек",), "direct": {"satisfy_water": True}},
    {"contains": ("консервы",),
     "direct": {"exhaustion_delta": -1, "nutrition": 1, "uses": 2}},
    {"contains": ("сушеная рыба",), "direct_remove": ("intoxication_delta",),
     "effects_remove_types": ("intoxication",),
     "direct": {"stress_delta": -1, "exhaustion_delta": -0.5,
                "nutrition": 1, "uses": 3, "requires_water_fraction": 1 / 3,
                "water_or_alcohol": True}},
    {"starts_with": ("сахар.",), "direct": {
        "stress_delta": -1, "exhaustion_delta": -3, "uses": 3,
        "requires_water_fraction": 2 / 3,
    }},
    {"contains": (" сахар.",), "direct": {
        "stress_delta": -1, "exhaustion_delta": -3, "uses": 3,
        "requires_water_fraction": 2 / 3,
    }},
    {"contains": ("кофе",), "direct": {
        "action_points_delta": 2, "exhaustion_delta": -2,
        "uses": 3, "requires_water_fraction": 1 / 3,
    }},
    {"starts_with": ("энергетик.",), "direct": {
        "action_points_delta": 5, "exhaustion_delta": -1, "duration": 3,
        "weight_delta": -2, "use_limit": 1, "limit_scope": "turn",
        "exclusive_group": "combat_stimulant", "clear_breathless": True,
    }},
    {"starts_with": ("адреналин.",), "direct_remove": ("weight_delta",),
     "direct": {"action_points_delta": 3, "action_points_duration": 3,
                "duration": 3, "hp": -50, "exhaustion_delta": -1,
                "use_limit": 1, "exclusive_group": "combat_stimulant",
                "clear_breathless": True}},
    {"starts_with": ("эпинефрин.",), "direct_remove": ("weight_delta",),
     "direct": {"action_points_delta": 5, "action_points_duration": 3,
                "duration": 3, "hp": 50, "exhaustion_delta": -2,
                "use_limit": 1, "exclusive_group": "combat_stimulant",
                "clear_breathless": True}},
    {"contains": ("водка",), "exclusive_group": "alcohol",
     "direct": {"radiation_delta": -1, "intoxication_delta": 15,
                "exhaustion_delta": -0.25, "stress_delta": -1,
                "pain_delta": -1, "uses": 6, "is_alcohol": True,
                "radiation_reduction_ceiling": 50},
     "effects": ({"type": "deferred_adjustment", "value": 0.5,
                  "name": "Истощение на следующие сутки", "remaining": 1,
                  "tick": "day_start", "trigger": "next_day",
                  "adjustments": [{"field": "exhaustion", "delta": 0.5,
                                   "min": 0, "max": 10}]},)},
    {"contains": ("самогон",), "exclusive_group": "alcohol",
     "direct": {"radiation_delta": -2.5, "intoxication_delta": 25,
                "exhaustion_delta": -0.5, "stress_delta": -1,
                "pain_delta": -2, "uses": 8, "is_alcohol": True},
     "effects": ({"type": "deferred_adjustment", "value": 1,
                  "name": "Истощение на следующие сутки", "remaining": 1,
                  "tick": "day_start", "trigger": "next_day",
                  "adjustments": [{"field": "exhaustion", "delta": 1,
                                   "min": 0, "max": 10}]},)},
    {"contains": ("вино",), "exclusive_group": "alcohol",
     "direct": {"radiation_delta": -5, "intoxication_delta": 10,
                "exhaustion_delta": 0, "stress_delta": -1,
                "pain_delta": -1, "uses": 10, "is_alcohol": True}},
    {"contains": ("банка пива",), "exclusive_group": "alcohol",
     "direct": {"radiation_delta": -1, "intoxication_delta": 20,
                "exhaustion_delta": -0.5, "stress_delta": -1,
                "pain_delta": 0, "uses": 1, "is_alcohol": True,
                "radiation_reduction_ceiling": 50}},
    {"contains": ("самокрутка",), "exclusive_group": "tobacco",
     "direct": {"requires_fire": True, "radiation_reduction_ceiling": 50},
     "effects": ({"type": "delayed_adjustment", "value": 1,
                  "name": "Снижение стресса через 5 минут", "remaining": 5,
                  "tick": "time_elapsed", "time_unit": "minute",
                  "remaining_seconds": 300,
                  "adjustments": [{"field": "stress", "delta": -1,
                                   "min": 0, "max": 10}]},)},
    {"contains": ("сигареты ",), "exclusive_group": "tobacco",
     "direct": {"requires_fire": True, "uses": 20,
                "radiation_reduction_ceiling": 50},
     "effects": ({"type": "delayed_adjustment", "value": 2,
                  "name": "Снижение стресса через 5 минут", "remaining": 5,
                  "tick": "time_elapsed", "time_unit": "minute",
                  "remaining_seconds": 300,
                  "adjustments": [{"field": "stress", "delta": -2,
                                   "min": 0, "max": 10}]},)},
    {"contains": ("сигары",), "exclusive_group": "tobacco",
     "direct": {"requires_fire": True, "uses": 5},
     "effects": ({"type": "delayed_adjustment", "value": 2,
                  "name": "Снижение стресса через 5 минут", "remaining": 5,
                  "tick": "time_elapsed", "time_unit": "minute",
                  "remaining_seconds": 300,
                  "adjustments": [{"field": "stress", "delta": -2,
                                   "min": 0, "max": 10}]},)},
    {"contains": ("протеин",),
     "direct": {"rest_heal_multiplier": 2, "requires_water_fraction": 1 / 3},
     "effects": ({"type": "next_rest_healing", "value": 2,
                  "name": "Удвоенное лечение на следующем отдыхе", "tick": "rest",
                  "trigger": "next_rest"},)},
)


def _painkiller_rule(name, delay, pain, duration, temperature, infection, stress):
    return {
        "contains": (name,), "exclusive_group": "painkiller",
        "direct_remove": ("pain_delta",),
        "effects": ({
            "type": "delayed_treatment", "value": pain,
            "name": f"{name.capitalize()}: ожидание действия",
            "remaining": delay, "tick": "turn_end",
            "adjustments": [
                {"field": "painLevel", "delta": -pain, "min": 0, "max": 10},
                {"field": "temperature", "delta": temperature, "min": 0},
                {"field": "infection", "delta": infection, "min": 0, "max": 100},
                {"field": "stress", "delta": stress, "min": 0, "max": 10},
            ],
            "activate_effects": [{"type": "analgesia", "name": f"Обезболивание {pain}",
                                  "value": pain, "remaining": duration,
                                  "tick": "turn_end"}],
        },),
    }


def _bleeding_application(stage, *, internal=False, ap=1, treated=False, **extra):
    return {"kind": "bleeding", "max_stage": stage, "internal": internal,
            "action_points": ap, "treated": treated, "all": False,
            "allow_weakening": True, **extra}


def _bandage_medium_application(treated):
    return {"kind": "bleeding", "max_stage": "medium", "internal": False,
            "action_points": 1, "treated": treated, "item_uses": 2,
            "medicine_bonus": 2, "allow_weakening": True}


BLEEDING_CATALOG_RULES = (
    {"contains_all": ("бинт", "стерильный"), "exclusive_group": "bandage",
     "direct": {"uses": 1, "treatment_action_points": 4,
                "treatment_time_uses_medicine": True},
     "applications": (
         _bleeding_application("light", treated=True),
         _bandage_medium_application(True),
     )},
    {"contains": ("бинт",), "exclusive_group": "bandage",
     "direct": {"uses": 1, "treatment_action_points": 4,
                "treatment_time_uses_medicine": True},
     "applications": (
         _bleeding_application("light"),
         _bandage_medium_application(False),
     )},
    {"contains": ("антисептический тампон",),
     "direct": {"treatment_action_points": 2,
                "treatment_time_uses_medicine": True},
     "applications": (_bleeding_application("light", treated=True),)},
    {"contains": ("пластырь с гемостатиком",),
     "applications": (_bleeding_application("medium", ap=2),
                      _bleeding_application("severe", ap=4))},
    {"contains": ('пластырь "стазис"',),
     "applications": (_bleeding_application("medium"),
                      _bleeding_application("severe", ap=2))},
    {"contains": ("губка коллагеновая",),
     "applications": (_bleeding_application("light"),
                      _bleeding_application("medium", ap=2))},
    {"contains_any": ("жгут", "турникет"), "exclude_any": ("шина шарнирова",),
     "contains_all": ("альфа",), "exclusive_group": "tourniquet",
     "direct": {"treatment_action_points": 4,
                "treatment_time_uses_medicine": True,
                "tourniquet": True, "limb_only": True},
     "applications": (_bleeding_application("extreme"),)},
    {"contains": ("турникет",), "exclude_any": ("шина шарнирова",),
     "exclusive_group": "tourniquet",
     "direct": {"treatment_action_points": 4,
                "treatment_time_uses_medicine": True,
                "tourniquet": True, "limb_only": True},
     "applications": (_bleeding_application("severe"),)},
    {"contains": ("жгут",), "exclude_any": ("шина шарнирова",),
     "exclusive_group": "tourniquet",
     "direct": {"treatment_action_points": 4,
                "treatment_time_uses_medicine": True,
                "tourniquet": True, "limb_only": True},
     "applications": (_bleeding_application("medium"),)},
    {"contains": ("оксицел",),
     "applications": (_bleeding_application("light"),)},
    {"contains": ("тромбин-л",), "exclusive_group": "thrombin",
     "applications": (_bleeding_application("light", internal=True),)},
    {"contains": ("тромбин",), "exclusive_group": "thrombin",
     "applications": (_bleeding_application("severe"),
                      _bleeding_application("medium", internal=True))},
    {"contains_any": ("глобулин", "контрикал"),
     "applications": (_bleeding_application("severe", internal=True),)},
    {"contains": ('гемостатик "шов"',),
     "applications": (_bleeding_application("severe", internal=True,
                                            internal_only=True),)},
    {"contains": ("соляного раствора",), "direct_remove": ("not_consumed",),
     "direct": {"wound_treatment": True, "pain_delta": 1,
                "uses": 10, "action_points_cost": 1}},
    {"starts_with": ("спирт.",), "direct_remove": ("intoxication_delta",),
     "effects_remove_types": ("intoxication",),
     "direct": {"wound_treatment": True, "pain_delta": 3,
                "uses": 10, "action_points_cost": 1,
                "external_use_only": True}},
    {"contains": (" спирт.",), "direct_remove": ("intoxication_delta",),
     "effects_remove_types": ("intoxication",),
     "direct": {"wound_treatment": True, "pain_delta": 3,
                "uses": 10, "action_points_cost": 1,
                "external_use_only": True}},
)


MEDICAL_CATALOG_RULES = (
    {"contains": ('кровоостанавливающее "желе"',),
     "effects_remove_types": ("bleeding_block",),
     "effects": ({"type": "blood_loss_freeze", "value": 1,
                  "name": "Стабилизация кровопотери", "remaining": 5,
                  "tick": "turn_end"},)},
    {"contains_all": ('кровоостанавливающее', 'хлопок'),
     "effects_remove_types": ("bleeding_block",),
     "direct": {"stop_all_bleeding": True, "exhaustion_delta": 1},
     "effects": ({"type": "bleeding_prevention", "value": 1,
                  "name": "Блок новых кровотечений", "remaining": 10,
                  "tick": "turn_end"},)},
    {"contains": ('стимулятор "полукровка"',),
     "direct_remove": ("infection_multiplier",),
     "direct": {"infection_delta": -50, "infection_block_days": 3},
     "effects": ({"type": "infection_growth_block", "value": 1,
                  "name": "Блок нарастания заражения", "remaining": 3,
                  "tick": "day_start", "time_unit": "day"},)},
    {"contains": ("пенициллин",),
     "direct": {"infection_delta": -5, "infection_block_chance": 20,
                "temperature_delta": 1}},
    {"contains": ("сангвинил",),
     "direct": {"infection_delta": -5, "infection_block_chance": 50,
                "temperature_delta": 1}},
    {"contains": ("настойка мяты",),
     "direct": {"infection_delta": -25, "exhaustion_delta": 1}},
    {"starts_with": ("пакет физраствора.",),
     "direct_remove": ("not_consumed",), "effects_remove_types": ("blood_recovery",),
     "direct": {"requires_infusion_tool": True, "blood_compatibility_required": False},
     "effects": ({"type": "blood_recovery", "value": 1,
                  "name": "Восстановление стадии кровопотери", "remaining": 2,
                  "tick": "turn_end"},)},
    {"starts_with": ("пакет крови.",),
     "direct_remove": ("not_consumed",), "effects_remove_types": ("blood_recovery",),
     "direct": {"requires_infusion_tool": True, "blood_compatibility_required": True,
                "blood_packet": True},
     "effects": ({"type": "blood_recovery", "value": 1,
                  "name": "Восстановление стадии кровопотери", "remaining": 4,
                  "tick": "turn_end"},)},
    {"starts_with": ('стимулятор "полнокровка".',),
     "effects_remove_types": ("blood_recovery",),
     "direct": {"exhaustion_delta": 1},
     "effects": ({"type": "blood_recovery", "value": 1,
                  "name": "Восстановление стадии кровопотери", "remaining": 5,
                  "tick": "turn_end"},)},
    {"contains": ("набор для забора крови",),
     "direct_remove": ("requires_infusion_tool", "blood_compatibility_required"),
     "effects_remove_types": ("blood_recovery",),
     "direct": {"blood_collection": True, "target_required": True,
                "blood_stage_delta": 2, "exhaustion_delta": 2}},
    {"contains": ("бутылек нашатыря",),
     "direct": {"target_required": True, "requires_shock": True,
                "not_consumed": True, "will_shock_bonus": 2,
                "will_shock_advantage": True}},
    _painkiller_rule("анальгин", 2, 1, 8, 0.5, 0, 0),
    _painkiller_rule("аспирин", 2, 3, 2, -1, 0, 0),
    _painkiller_rule("ибупрофен", 1, 1, 10, -0.5, -10, -1),
    _painkiller_rule("настойка боярышника", 2, 1, 3, 0, 0, 0),
    {"contains": ("морфин",), "direct": {"exhaustion_delta": -1},
     "effects": ({"type": "delayed_treatment", "value": 3,
                  "name": "Морфин: ожидание действия", "remaining": 1,
                  "tick": "turn_end",
                  "adjustments": [{"field": "painLevel", "delta": -3, "min": 0, "max": 10},
                                  {"field": "stress", "delta": -2, "min": 0, "max": 10}],
                  "activate_effects": [{"type": "analgesia", "name": "Морфин: блок боли",
                                        "value": 3, "remaining": 3, "tick": "turn_end",
                                        "blocks_new_pain": True},
                                       {"type":"periodic_adjustment","name":"Морфин: облегчение боли",
                                        "value":-3,"remaining":2,"tick":"turn_end",
                                        "adjustments":[{"field":"painLevel","delta":-3,"min":0,"max":10}]}]},
                 {"type": "deferred_adjustment", "value": 2,
                  "name": "Морфин: истощение через час", "remaining": 1,
                  "tick": "hour_start", "trigger": "one_hour",
                  "adjustments": [{"field": "exhaustion", "delta": 2,
                                   "min": 0, "max": 10}]})},
)


SURGERY_PRE_CATALOG_RULES = (
    {"contains": ("шина шарнирова",), "exclusive_group": "splint",
     "direct": {"fracture_splint": True, "fracture_restore_health": 1,
                "temporary_limb_health_minutes": 10, "action_points_cost": 6,
                "requires_injury": True}},
    {"starts_with": ("шина",), "exclusive_group": "splint",
     "direct": {"fracture_splint": True, "fracture_restore_health": 1,
                "temporary_limb_health_turns": 4, "action_points_cost": 6,
                "requires_injury": True}},
    {"contains_any": ("шина.", "химера", "вторая жизнь",
                      "хирургический набор", 'кустарный набор "айболит"',
                      "набор полного восстановления конечности"),
     "direct": {"requires_injury": True, "target_body_part": True}},
    {"contains_all": ("кустарный набор", "айболит"),
     "direct": {"surgical_kit": True, "requires_injury": True,
                "target_body_part": True}},
    {"contains": ("набор полного восстановления конечности",),
     "direct": {"surgical_kit": True,
                "catastrophic_limb_surgery": "full_restoration",
                "restore_missing_part": True, "restore_full_body_part": True}},
    {"contains": ("хирургический набор",),
     "direct": {"surgical_kit": True,
                "catastrophic_limb_surgery": "surgeon"}},
)


SURGERY_POST_CATALOG_RULES = (
    {"contains": ("химера",),
     "direct": {"cure_fracture": True, "close_area_bleeding": True,
                "delay": 1, "delayed_limb_treatment_minutes": 1,
                "special_limb_treatment": "chimera",
                "invalid_limb_damage": -200, "head_lethal": True}},
    {"contains": ("вторая жизнь",),
     "direct": {"cure_fracture": True, "restore_limb_health": 50,
                "close_area_bleeding": True, "pain_delta": 5, "delay": 1,
                "delayed_limb_treatment_minutes": 1,
                "special_limb_treatment": "second_life"}},
)


LATE_CATALOG_RULES = (
    {"contains":("стимулятор бык",),"direct":{"movement_points_delta":-1,"weight_delta":-3,
        "duration":6,"duration_phase":"movement_end","pain_delta":2,"exhaustion_delta":2}},
    {"contains":("стимулятор геракл",),"direct":{"movement_points_delta":-2,"weight_delta":-5,
        "duration":6,"duration_phase":"movement_end","pain_delta":4,"exhaustion_delta":3}},
    {"contains": ("стимулятор морковчатник",),
     "direct": {"duration":6,"duration_phase":"movement_end"},
     "modifiers": ({"stat":"awareness","value":6,"remaining":6,"tick":"movement_end","note":"Внимательность"},)},
    {"contains": ("стимулятор грация",),
     "direct": {"duration":5,"duration_phase":"movement_end","movement_points_delta":-1,"exhaustion_delta":1},
     "modifiers": ({"stat":"anomaly_escape_roll","value":2,"remaining":5,"tick":"movement_end","note":"Выход из аномалии"},)},
    {"contains": ("стимулятор б.о.л.т.",),
     "direct_remove": ("hp","exhaustion_delta"), "effects_remove_types": ("exhaustion","damage"),
     "direct": {"duration":5,"duration_phase":"turn_end","movement_points_delta":-3,"weight_delta":-3,
                "post_duration_hp_delta":-50,"post_duration_exhaustion_delta":1}},
    {"contains": ("стимулятор болид",),
     "direct_remove": ("hp","exhaustion_delta"), "effects_remove_types": ("exhaustion","damage"),
     "direct": {"duration":5,"duration_phase":"turn_end","movement_points_delta":-1,"clear_breathless":True,
                "post_duration_hp_delta":-100,"post_duration_exhaustion_delta":1},
     "modifiers": ({"stat":"breathless_immunity","value":1,"remaining":5,"tick":"turn_end","note":"Блок одышки"},)},
    {"contains_any": ("стимулятор орёл", "стимулятор орел"),
     "direct_remove": ("post_duration_exhaustion_delta",), "effects_remove_types": ("exhaustion",),
     "direct": {"duration":10,"duration_phase":"turn_end","accuracy_delta":3,"strength_delta":2,"agility_delta":2,
                "exhaustion_delta":1,
                "post_duration_modifiers":[{"stat":"accuracy","value":-5,"remaining":3,"tick":"turn_end","note":"Орёл: последствия"}]}},
    {"contains": ("военный стимулятор пчела",),
     "direct_remove": ("post_duration_exhaustion_delta",), "effects_remove_types": ("exhaustion",),
     "direct": {"duration":10,"duration_phase":"turn_end","agility_delta":4,"accuracy_delta":1,"post_duration_hp_delta":-100,"exhaustion_delta":1},
     "modifiers": ({"stat":"melee","value":4,"remaining":10,"tick":"turn_end","note":"Ближний бой"},)},
    {"contains": ("стимулятор психолог-м",),
     "direct": {"duration":5,"duration_phase":"movement_end","psy_defense_delta":50,"will_delta":5,"psy_delta":-10}},
    {"contains": ("настойка подорожника",),
     "direct": {"hp":25,"satisfy_water":True,"duration":1,"duration_phase":"movement_end"}},
    {"contains": ("стимулятор гора.",),
     "direct": {"strength_delta":6,"pain_delta":-1,"duration":5,"duration_phase":"turn_end",
                "post_duration_hp_delta":-50,"post_duration_pain_delta":1}},
    {"contains_all": ("жаропонижающее средство", "мороз"),
     "direct_remove": ("temperature_delta",), "effects_remove_types": ("temperature_control",),
     "effects": ({"type": "periodic_adjustment", "value": -1,
                  "name": "Жаропонижающее", "remaining": 4,
                  "adjustments": [{"field":"temperature","delta":-1,"min":0}],
                  "tick": "round_end"},)},
    {"contains": ("научный стимулятор волкодав",),
     "status_additions_remove": ("sleep",),
     "direct": {"strength_delta": 5, "accuracy_delta": 5, "duration": 15},
     "effects": ({"type": "stimulant_crash", "value": 0, "name": "Волкодав",
                  "remaining": 15, "tick": "turn_end",
                  "on_expire": [{"field": "current", "delta": -50, "min": 0}],
                  "activate_effects": [{"type": "sleep", "name": "Сон после Волкодава",
                                        "remaining": 1, "tick": "hour_start", "time_unit": "hour"}]},)},
    {"contains": ("стимулятор варвар",), "direct": {
        "pain_delta": -3, "temporary_limb_health_minutes": 10,
        "suppress_limb_trauma": True, "affects_all_limbs": True,
    }},
    {"contains": ("стимулятор викинг",), "direct": {
        "pain_delta": -5, "temporary_limb_health_minutes": 10,
        "suppress_limb_trauma": True, "minimum_limb_health": 1,
        "affects_all_limbs": True,
    }},
    {"contains_any": ("ампула миколия", "миколий", "препарат 02"), "direct": {
        "temporary_limb_health_minutes": 10, "suppress_limb_trauma": True,
        "affects_all_limbs": True,
    }},
    {"contains_any": ("миколий", "ампула миколия"), "direct": {"pain_delta":-1}},
    {"contains": ("препарат 02",), "direct": {"pain_delta":-3,"stress_delta":-2}},
    {"contains": ("питательный стимулятор",), "direct": {"pain_delta":-1,"duration":3,"duration_phase":"turn_end",
        "post_duration_pain_delta":3}},
    {"contains": ("регенеративный стимулятор альфа",), "direct_remove": ("exhaustion_delta",),
     "effects_remove_types": ("exhaustion", "temporary_exhaustion"),
     "effects": ({"type":"periodic_adjustment","name":"Альфа: +1 истощения за ход","remaining":3,"tick":"turn_end",
        "adjustments":[{"field":"exhaustion","delta":1,"min":0,"max":10}]},)},
    {"contains": ("регенеративный стимулятор бета",), "direct_remove": ("exhaustion_delta",),
     "effects_remove_types": ("exhaustion", "temporary_exhaustion"),
     "effects": ({"type":"periodic_adjustment","name":"Бета: +1 истощения за ход","remaining":2,"tick":"turn_end",
        "adjustments":[{"field":"exhaustion","delta":1,"min":0,"max":10}]},)},
    {"contains": ("регенеративный стимулятор звезда",), "direct": {"exhaustion_delta":1}},
    {"contains_any": ("борщевик", "самопальный стимпак"),
     "direct_remove": ("intoxication_delta",), "effects_remove_types": ("intoxication",),
     "effects": ()},
    {"contains": ("борщевик",),
     "effects": ({"type":"periodic_adjustment","name":"Борщевик: +5 опьянения за ход","remaining":10,"tick":"turn_end",
        "adjustments":[{"field":"intoxication","delta":5,"min":0}]},)},
    {"contains": ("самопальный стимпак",),
     "effects": ({"type":"periodic_adjustment","name":"Самопальный стимпак: +15 опьянения за ход","remaining":2,"tick":"turn_end",
        "adjustments":[{"field":"intoxication","delta":15,"min":0}]},)},
    {"contains": ("научный стимпак",), "direct_remove": ("radiation_delta",), "effects_remove_types": ("radiation",),
     "effects": ({"type":"radiation_treatment","name":"Выведение радиации 5/ход","value":-5,"remaining":3,"tick":"turn_end"},)},
    {"contains": ("военный стимпак",), "direct": {"bleeding_modifier_delta": -2}},
    {"contains": ("стимулятор шумодав",), "direct": {"duration": 3}, "status_additions_remove": ("deafness",),
     "effects": ({"type": "deafness", "value": 100, "name": "Глухота",
                  "remaining": 3, "tick": "movement_end", "minimum_value": 100},),
     "modifiers": ({"stat": "vision_awareness", "value": 4, "remaining": 3,
                    "tick": "movement_end", "note": "Шумодав"},)},
    {"starts_with": ("стимулятор воля-н.",), "direct": {
        "pain_block_turns": 5, "stress_block_turns": 5,
        "blocked_pain_return_fraction": 0.5, "exhaustion_on_expire": 0,
    }},
    {"starts_with": ("стимулятор воля.",),
     "direct_remove": ("exhaustion_delta",), "effects_remove_types": ("exhaustion",), "direct": {
        "pain_block_turns": 3, "stress_block_turns": 3,
        "blocked_pain_return_fraction": 1, "exhaustion_on_expire": 1,
    }},
    {"contains": ("стимулятор сова-н",), "status_additions_remove": ("sleep",),
     "direct_remove": ("sleep_block_hours",), "direct": {"satisfy_sleep": True}},
    {"contains": ("стимулятор сова",), "exclude_any": ("стимулятор сова-н",),
     "status_additions_remove": ("sleep",), "direct_remove": ("exhaustion_delta", "sleep_block_hours"),
     "effects_remove_types": ("exhaustion",),
     "direct": {"satisfy_sleep": True},
     "effects": ({"type": "deferred_adjustment", "value": 2,
                  "name": "Истощение на следующие сутки", "tick": "day_start",
                  "remaining": 1, "trigger": "next_day",
                  "adjustments": [{"field": "exhaustion", "delta": 2, "min": 0, "max": 10}]},)},
    {"contains": ("антирад-а",),
     "direct_remove": ("radiation_delta",), "effects_remove_types": ("radiation",),
     "direct": {"exhaustion_delta": 1},
     "effects": ({"type": "radiation_treatment", "value": -2.5,
                  "name": "Выведение радиации 2.5/ход", "remaining": 4,
                  "tick": "turn_end"},)},
    {"contains": ("йод-плюс",),
     "direct_remove": ("radiation_delta",), "effects_remove_types": ("radiation",),
     "direct": {"exhaustion_delta": 1},
     "effects": ({"type": "radiation_treatment", "value": -2.5,
                  "name": "Выведение радиации 2.5/ход", "remaining": 10,
                  "tick": "turn_end"},)},
    {"contains": ("антирад-б",),
     "direct_remove": ("radiation_delta",), "effects_remove_types": ("radiation",),
     "direct": {"exhaustion_delta": 1},
     "effects": ({"type": "radiation_treatment", "value": -5,
                  "name": "Выведение радиации 5/ход", "remaining": 4,
                  "tick": "turn_end"},)},
    {"contains": ("антирад-г",),
     "direct_remove": ("radiation_delta",), "effects_remove_types": ("radiation",),
     "direct": {"exhaustion_delta": 1},
     "effects": ({"type": "radiation_treatment", "value": -3,
                  "name": "Выведение радиации 3/ход", "remaining": 10,
                  "tick": "turn_end"},)},
    {"contains": ('препарат "радист"-л',),
     "direct": {"radiation_filter_percent": 100,
                "radiation_filter_capacity": 50, "exhaustion_delta": 1},
     "effects": ({"type": "radiation_filter", "value": 100,
                  "name": "Выведение входящей радиации 100%", "tick": "time_elapsed",
                  "remaining": 24, "time_unit": "hour", "remaining_seconds": 86400,
                  "capacity": 50, "remaining_capacity": 50, "max_hours": 24},)},
    {"contains": ('препарат "радист"',), "exclude_any": ('препарат "радист"-л',),
     "direct": {"radiation_filter_percent": 100,
                "radiation_filter_capacity": 100}, "direct_remove": ("exhaustion_delta",),
     "effects_remove_types": ("exhaustion", "temporary_exhaustion"),
     "effects": ({"type": "radiation_filter", "value": 100,
                  "name": "Выведение входящей радиации 100%", "tick": "time_elapsed",
                  "remaining": 24, "time_unit": "hour", "remaining_seconds": 86400,
                  "capacity": 100, "remaining_capacity": 100, "max_hours": 24},
                 {"type": "periodic_adjustment", "value": 1,
                  "name": "Радист: +1 истощения за ход", "remaining": 3, "tick": "turn_end",
                  "adjustments": [{"field": "exhaustion", "delta": 1, "min": 0, "max": 10}]} )},
    {"contains": ('противорадиационное "брезент"-пб',),
     "direct": {"radiation_filter_percent": 50,
                "radiation_filter_capacity": 100, "exhaustion_delta": 1},
     "effects": ({"type": "radiation_filter", "value": 50,
                  "name": "Выведение входящей радиации 50%", "tick": "time_elapsed",
                  "remaining": 24, "time_unit": "hour", "remaining_seconds": 86400,
                  "capacity": 100, "remaining_capacity": 100, "max_hours": 24},)},
)


def correct_owl_profile(profile, direct, name):
    """Уточнение автора: сон закрывается сразу, часы относятся к зрению; также для старого каталога."""
    name = str(name or '').casefold()
    if 'стимулятор сова' not in name:
        return profile, direct
    profile, direct = deepcopy(profile), deepcopy(direct)
    improved = 'стимулятор сова-н' in name
    direct.pop('sleep_block_hours', None)
    direct['satisfy_sleep'] = True
    profile['direct'] = direct
    stats = {'darkness_awareness', 'darkness_accuracy', 'light_awareness', 'darkness_visual_immunity', 'darkness_shooting_immunity'}
    profile['modifiers'] = [m for m in profile.get('modifiers') or [] if m.get('stat') not in stats]
    for stat, value, note in (
        ('darkness_awareness', 5 if improved else 3, 'Внимательность в темноте'),
        ('darkness_accuracy', 5 if improved else 3, 'Точность в темноте без ПНВ'),
        *([] if improved else [('light_awareness', -3, 'Внимательность на свету')]),
    ):
        profile['modifiers'].append({'stat': stat, 'value': value, 'remaining': (8 if improved else 4) * 60,
                                     'tick': 'time_elapsed', 'note': note})
    return profile, direct


def apply_catalog_rules(profile, text, rules):
    lower = text.casefold()
    matched_groups = set()
    for rule in rules:
        group = rule.get("exclusive_group")
        if group in matched_groups:
            continue
        if rule.get("contains") and not all(term in lower for term in rule["contains"]):
            continue
        if rule.get("contains_all") and not all(term in lower for term in rule["contains_all"]):
            continue
        if rule.get("contains_any") and not any(term in lower for term in rule["contains_any"]):
            continue
        if rule.get("starts_with") and not any(lower.startswith(term) for term in rule["starts_with"]):
            continue
        if rule.get("exact") and lower not in rule["exact"]:
            continue
        if rule.get("exclude_any") and any(term in lower for term in rule["exclude_any"]):
            continue
        if rule.get("unless_direct") and any(key in profile["direct"] for key in rule["unless_direct"]):
            continue
        for key in rule.get("direct_remove", ()):
            profile["direct"].pop(key, None)
        if rule.get("effects_remove_types"):
            blocked_types = set(rule["effects_remove_types"])
            profile["effects"] = [
                effect for effect in profile["effects"]
                if effect.get("type") not in blocked_types
            ]
        if rule.get("status_additions_remove"):
            blocked_statuses = set(rule["status_additions_remove"])
            profile["status_additions"] = [
                status for status in profile["status_additions"]
                if status not in blocked_statuses
            ]
        for key, minimum in rule.get("direct_min", {}).items():
            profile["direct"][key] = max(minimum, profile["direct"].get(key, minimum))
        for key, value in rule.get("direct_defaults", {}).items():
            profile["direct"].setdefault(key, deepcopy(value))
        profile["direct"].update(deepcopy(rule.get("direct", {})))
        if rule.get("applications"):
            profile["direct"].setdefault("applications", []).extend(deepcopy(rule["applications"]))
        for field in ("effects", "modifiers", "status_removals", "status_additions", "requirements", "notes"):
            for value in rule.get(field, ()):
                if field in {"effects", "modifiers", "notes"} or value not in profile[field]:
                    entry = deepcopy(value)
                    if field == "effects":
                        entry.setdefault("active", True)
                        entry.setdefault("tick", "manual")
                    profile[field].append(entry)
        if group:
            matched_groups.add(group)
