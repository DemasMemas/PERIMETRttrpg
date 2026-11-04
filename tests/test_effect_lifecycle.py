import pytest

from app.services.effects import (
    advance_timed_effects,
    apply_effect_to_health,
    apply_periodic_effects_to_health,
    canonical_type,
    create_effect_draft,
    get_bleeding_state,
    normalize_effect,
    normalize_effect_list,
    sync_health_derived_statuses,
    tick_effect,
    tick_effects,
)


def test_effect_aliases_are_canonicalized():
    assert canonical_type("heal") == "heal"
    assert canonical_type("bleeding_internal_extreme") == "bleeding_internal_extreme"
    assert canonical_type("Пользовательский эффект") == "custom"
    assert canonical_type("") == "generic"


def test_world_time_completes_all_medication_ticks_and_crash_once():
    health = {'current': 100, 'max': 300, 'radiation': 25, 'effects': [
        {'type': 'regeneration', 'value': 10, 'remaining': 10, 'tick': 'turn_end', 'source': 'consumable-template:1'},
        {'type': 'radiation_treatment', 'value': -2.5, 'remaining': 10, 'tick': 'turn_end', 'source': 'consumable-template:2'},
        {'type': 'stimulant_crash', 'remaining': 10, 'tick': 'turn_end', 'source': 'consumable-template:1:crash',
         'onExpire': [{'field': 'current', 'delta': -20, 'min': 0}]},
    ]}
    health['effects'] = advance_timed_effects(health, health['effects'], 600, advance_medication_turns=True)
    assert health['current'] == 180
    assert health['radiation'] == 0
    assert not health['effects']
    advance_timed_effects(health, health['effects'], 600, advance_medication_turns=True)
    assert health['current'] == 180


def test_world_time_preserves_partial_medication_tick_and_stress_pain_expiry():
    health = {'current': 100, 'max': 300, 'painLevel': 0, 'exhaustion': 0,
              'combatMeta': {'blockedPain': 4}, 'effects': [
        {'type': 'regeneration', 'value': 10, 'remaining': 2, 'tick': 'turn_end', 'source': 'consumable-template:1'},
        {'type': 'pain_block', 'remaining': 2, 'tick': 'turn_end', 'source': 'consumable-template:2',
         'blocks_new_pain': True, 'return_fraction': 0.5, 'exhaustion_on_expire': 1},
    ]}
    for seconds, current, count in [(3,100,2), (3,110,2), (5,110,2), (1,120,0)]:
        health['effects'] = advance_timed_effects(health, health['effects'], seconds, advance_medication_turns=True)
        assert health['current'] == current
        assert len(health['effects']) == count
    assert health['painLevel'] == 2
    assert health['exhaustion'] == 1


def test_world_time_processes_activated_medication_during_remaining_interval():
    health = {'current': 100, 'max': 300, 'effects': [{
        'type': 'delayed_adjustment', 'remaining': 1, 'tick': 'time_elapsed',
        'source': 'consumable-template:1', 'activate_effects': [{
            'type': 'regeneration', 'value': 10, 'remaining': 2, 'tick': 'turn_end',
            'source': 'consumable-template:1',
        }],
    }]}
    health['effects'] = advance_timed_effects(health, health['effects'], 600, advance_medication_turns=True)
    assert health['current'] == 120
    assert not health['effects']


def test_world_time_keeps_stress_block_while_other_deadline_fires():
    health={'stress':0,'effects':[
        {'type':'stress_resistance','blocks_new_stress':True,'remaining':3,'tick':'turn_end','source':'consumable-template:1'},
        {'type':'delayed_adjustment','remaining':12,'time_unit':'second','tick':'time_elapsed',
         'adjustments':[{'field':'stress','delta':2,'min':0}]},
    ]}
    health['effects']=advance_timed_effects(health,health['effects'],60,advance_medication_turns=True)
    assert health['stress']==0


@pytest.mark.parametrize('kind', [
    'temporary_exhaustion', 'temporary_hp_max', 'amputation', 'anomaly_compulsion',
    'anomaly_electric_penalty', 'anomaly_will_penalty', 'circular_attack_recovery',
    'death_order', 'mutant_poison', 'psy_roar_disadvantage', 'zombification',
])
def test_production_effect_types_keep_identity_and_executable_metadata(kind):
    effect = normalize_effect({'id': 'known-effect', 'type': kind, 'remaining': 3,
                              'tick': 'turn_end', 'damage_per_turn': 5})
    assert effect['type'] == kind
    assert effect['id'] == 'known-effect'
    assert effect['damage_per_turn'] == 5


def test_bleeding_impact_matches_calculation_for_legacy_value_one():
    from app.services.effects import get_effect_impact
    effect = {'type': 'bleeding_external_medium', 'value': 1}
    assert get_effect_impact(effect)['severity'] == get_bleeding_state({'effects': [effect]})['totalSeverity']


@pytest.mark.parametrize('status', [
    {'type':'death'}, {'type':'unconsciousness'},
    {'type':'stress_resistance','blocks_new_stress':True,'remaining':3},
])
def test_periodic_stress_respects_unconsciousness_death_and_medicine(status):
    health = {'stress':1,'effects':[status]}
    apply_periodic_effects_to_health(health,[{'type':'periodic_adjustment','tick':'turn_end',
                                            'adjustments':[{'field':'stress','delta':2}]}])
    assert health['stress'] == 1


def test_custom_effect_is_distinct_from_legacy_generic_effect():
    manual = create_effect_draft("custom", {"name": "Осмотр аномалии"})
    legacy = normalize_effect({"type": "unknown_legacy_effect", "name": "Старый статус"})

    assert manual["type"] == "custom"
    assert manual["name"] == "Осмотр аномалии"
    assert legacy["type"] == "generic"
    assert legacy["name"] == "Старый статус"


def test_stress_manifestation_is_not_normalized_as_generic():
    current = normalize_effect({
        "type": "stress_effect",
        "name": "Retreat from the stress source",
        "source": "stress_manifestation",
    })
    legacy = normalize_effect({
        "type": "generic",
        "name": "\u041d\u0435\u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0451\u043d\u043d\u044b\u0439 \u044d\u0444\u0444\u0435\u043a\u0442",
        "source": "stress_manifestation",
    })

    assert current["type"] == "stress_effect"
    assert current["name"] == "Retreat from the stress source"
    assert legacy["type"] == "stress_effect"
    assert legacy["name"] == "\u042d\u0444\u0444\u0435\u043a\u0442 \u0441\u0442\u0440\u0435\u0441\u0441\u0430"


def test_normalization_preserves_executable_metadata():
    effect = normalize_effect({
        "type": "delayed_adjustment",
        "turns": 2,
        "tickPhase": "day_start",
        "adjustments": [{"field": "stress", "delta": -2}],
        "custom_flag": True,
    })

    assert effect["remaining"] == 2
    assert effect["tick"] == "day_start"
    assert effect["adjustments"] == [{"field": "stress", "delta": -2}]
    assert effect["custom_flag"] is True


def test_normalization_removes_legacy_trauma_report_but_keeps_manual_generic_effect():
    effects = normalize_effect_list([
        {
            "type": "generic",
            "name": "Неопределённый эффект",
            "chance_roll": 74,
            "roll": 12,
            "fracture": False,
            "bleeding": None,
            "pain": 0,
            "shock": False,
            "organ": None,
            "fall_or_drop": None,
        },
        {"type": "generic", "name": "Старый пользовательский эффект", "value": 2},
    ])

    assert len(effects) == 1
    assert effects[0]["name"] == "Старый пользовательский эффект"


def test_manual_effect_does_not_tick():
    effect = tick_effect({
        "type": "analgesia",
        "remaining": 3,
        "tick": "manual",
    })

    assert effect["remaining"] == 3


def test_effect_only_ticks_during_matching_phase():
    effect = {
        "type": "infection_growth_block",
        "remaining": 2,
        "tick": "day_start",
    }

    after_turn = tick_effect(effect, "turn_end")
    after_day = tick_effect(effect, "day_start")

    assert after_turn["remaining"] == 2
    assert after_day["remaining"] == 1


def test_expired_effect_is_removed_from_active_list():
    effects = tick_effects([
        {"type": "analgesia", "remaining": 1, "tick": "turn_end"},
        {"type": "fracture", "remaining": None, "tick": "manual"},
    ])

    assert [effect["type"] for effect in effects] == ["fracture"]


def test_persist_at_zero_keeps_expired_effect():
    effects = tick_effects([{
        "type": "generic",
        "remaining": 1,
        "tick": "turn_end",
        "persist_at_zero": True,
    }])

    assert len(effects) == 1
    assert effects[0]["remaining"] == 0


def test_bleeding_prevention_blocks_new_bleeding():
    health = {
        "effects": [{
            "type": "bleeding_prevention",
            "remaining": 3,
            "tick": "turn_end",
        }],
    }

    apply_effect_to_health(health, {
        "type": "bleeding_external_severe",
        "area": "leftArm",
    })

    assert get_bleeding_state(health)["totalSeverity"] == 0
    assert len(health["effects"]) == 1


def test_closed_and_suppressed_bleeding_do_not_raise_difficulty():
    health = {
        "effects": [
            {"type": "bleeding_external_light", "closed": True},
            {"type": "bleeding_internal_severe", "suppressed": True},
            {"type": "bleeding_external_medium", "area": "rightLeg"},
        ],
    }

    bleeding = get_bleeding_state(health)

    assert bleeding["totalSeverity"] == 3
    assert bleeding["difficulty"] == 8
    assert len(bleeding["effects"]) == 1


def test_periodic_adjustment_respects_bounds():
    health = {"stress": 1, "exhaustion": 9}
    effects = [{
        "type": "periodic_adjustment",
        "remaining": 2,
        "tick": "turn_end",
        "adjustments": [
            {"field": "stress", "delta": -5, "min": 0, "max": 10},
            {"field": "exhaustion", "delta": 5, "min": 0, "max": 10},
        ],
    }]

    apply_periodic_effects_to_health(health, effects)

    assert health["stress"] == 0
    assert health["exhaustion"] == 10


def test_blood_recovery_improves_stage_without_passing_normal():
    health = {"blood": "medium", "bloodStage": "medium"}
    effect = {
        "type": "blood_recovery",
        "value": 1,
        "remaining": 3,
        "tick": "turn_end",
    }

    apply_periodic_effects_to_health(health, [effect])
    assert health["blood"] == "light"

    apply_periodic_effects_to_health(health, [effect])
    apply_periodic_effects_to_health(health, [effect])
    assert health["blood"] == "normal"


def test_numeric_status_effects_are_clamped_except_unbounded_intoxication(monkeypatch):
    monkeypatch.setattr('app.services.effects.random.randint', lambda *_: 100)
    health = {
        "radiation": 2,
        "painLevel": 9,
        "stress": 1,
        "intoxication": 95,
    }

    apply_effect_to_health(health, {"type": "radiation", "value": -10})
    apply_effect_to_health(health, {"type": "pain", "value": 5})
    apply_effect_to_health(health, {"type": "stress", "value": -5})
    apply_effect_to_health(health, {"type": "intoxication", "value": 20})

    assert health["radiation"] == 0
    assert health["painLevel"] == 10
    assert health["stress"] == 0
    assert health["intoxication"] == 115


def test_new_fracture_adds_three_pain_only_once():
    health = {"painLevel": 1, "effects": []}
    fracture = {
        "type": "fracture",
        "area": "rightArm",
        "source": "combat_attack",
    }

    apply_effect_to_health(health, fracture)
    apply_effect_to_health(health, fracture)

    assert health["painLevel"] == 4
    assert len([
        effect
        for effect in health["effects"]
        if effect["type"] == "fracture"
    ]) == 1


def test_fixed_fracture_is_kept_as_separate_status_without_extra_pain():
    health = {"painLevel": 3, "effects": []}

    apply_effect_to_health(health, {
        "type": "fracture_fixed",
        "area": "rightArm",
        "source": "splint",
    })

    assert health["painLevel"] == 3
    assert any(
        effect["type"] == "fracture_fixed" and effect["area"] == "rightArm"
        for effect in health["effects"]
    )


def test_fixed_fracture_expires_after_one_day_without_restoring_original_fracture():
    health = {"painLevel": 3, "effects": []}

    effects = [{
        "type": "fracture_fixed",
        "area": "rightArm",
        "remaining": 24,
        "remaining_seconds": 86400,
        "time_unit": "hour",
        "tick": "time_elapsed",
    }]

    effects = advance_timed_effects(health, effects, 86399)
    assert effects[0]["remaining_seconds"] == 1

    assert advance_timed_effects(health, effects, 1) == []
    assert health["effects"] == []


def test_regular_healing_does_not_restore_knocked_out_limb():
    health = {
        "current": 500,
        "max": 700,
        "zones": {
            "leftArm": {"current": 0, "max": 90},
            "rightArm": {"current": 50, "max": 90},
        },
        "effects": [],
    }

    apply_effect_to_health(health, {"type": "heal", "value": 20})

    assert health["current"] == 520
    assert health["zones"]["leftArm"]["current"] == 0
    assert health["zones"]["rightArm"]["current"] == 70


def test_temporary_limb_restoration_caps_healing_and_returns_limb_to_zero():
    health = {
        "current": 500,
        "max": 700,
        "zones": {"leftArm": {"current": 1, "max": 90}},
        "effects": [{
            "type": "temporary_limb_restoration",
            "area": "leftArm",
            "previous_health": 0,
            "health_cap": 1,
            "remaining": 4,
            "tick": "turn_end",
        }],
    }

    apply_effect_to_health(health, {"type": "heal", "value": 20})
    assert health["zones"]["leftArm"]["current"] == 1

    assert advance_timed_effects(health, health["effects"], 24, include_turn_effects=True) == []
    assert health["zones"]["leftArm"]["current"] == 0


def test_delayed_limb_treatment_cures_fractures_and_sets_zone_health_after_minute():
    health = {
        "current": 500,
        "max": 700,
        "zones": {"leftArm": {"current": 0, "max": 90}},
        "effects": [
            {"type": "fracture", "area": "leftArm", "active": True},
            {"type": "fracture_fixed", "area": "leftArm", "active": True},
            {
                "type": "delayed_limb_treatment",
                "area": "leftArm",
                "cure_fracture": True,
                "restore_limb_health": 50,
                "remaining": 1,
                "remaining_seconds": 60,
                "tick": "time_elapsed",
                "time_unit": "minute",
            },
        ],
    }

    effects = advance_timed_effects(health, health["effects"], 59)
    assert health["zones"]["leftArm"]["current"] == 0
    assert any(effect["type"] == "fracture" for effect in effects)

    effects = advance_timed_effects(health, effects, 1)
    assert health["zones"]["leftArm"]["current"] == 50
    assert health["current"] == 550
    assert not any(effect["type"] in {"fracture", "fracture_fixed"} for effect in effects)


def test_lost_organ_treatment_window_expires_without_removing_status():
    health = {"effects": []}
    effect = create_effect_draft("organ_loss", {"area": "leftEye"})

    effects = advance_timed_effects(health, [effect], 3600)

    assert len(effects) == 1
    assert effects[0]["type"] == "organ_loss"
    assert effects[0]["treatment_window_expired"] is True
    assert effects[0]["treatment_window_seconds"] == 0


def test_lost_organ_is_normalized_as_named_status_with_treatment_window():
    effect = normalize_effect({
        "type": "organ_loss",
        "name": "Повреждённый орган",
        "area": "rightLung",
        "source": "organ_damage",
    })

    assert effect["type"] == "organ_loss"
    assert effect["name"] == "Повреждённый орган: Правое лёгкое"
    assert effect["treatment_window_seconds"] == 3600
    assert effect["treatment_window_expired"] is False


def test_five_minute_delayed_effect_advances_in_six_second_rounds():
    health = {"stress": 5}
    effects = [{
        "type": "delayed_adjustment",
        "name": "Stress after five minutes",
        "remaining": 5,
        "remaining_seconds": 300,
        "time_unit": "minute",
        "tick": "time_elapsed",
        "adjustments": [{"field": "stress", "delta": -2, "min": 0, "max": 10}],
    }]

    effects = advance_timed_effects(health, effects, 294)
    assert effects[0]["remaining_seconds"] == 6
    assert health["stress"] == 5

    effects = advance_timed_effects(health, effects, 6)
    assert effects == []
    assert health["stress"] == 3


def test_rest_expires_minute_and_turn_effects():
    health = {"painLevel": 3}
    effects = [
        {
            "type": "limb_trauma_suppression",
            "remaining": 10,
            "time_unit": "minute",
            "tick": "time_elapsed",
        },
        {"type": "analgesia", "remaining": 3, "tick": "turn_end"},
    ]

    assert advance_timed_effects(
        health, effects, 3600, include_turn_effects=True
    ) == []


def test_elapsed_time_expires_consumable_stat_modifiers():
    health = {
        "combatMeta": {
            "consumableModifiers": [
                {"stat": "strength", "value": 5, "remaining": 2, "tick": "turn_end"},
                {"stat": "accuracy", "value": 5, "remaining": 3, "tick": "turn_end"},
            ],
        },
    }

    advance_timed_effects(health, [], 12)

    assert health["combatMeta"]["consumableModifiers"] == [
        {
            "stat": "accuracy",
            "value": 5,
            "remaining": 1,
            "remaining_seconds": 6,
            "tick": "turn_end",
        },
    ]


def test_radiation_filter_uses_max_hours_as_duration():
    health = {}
    effects = [{
        "type": "radiation_filter",
        "name": "Incoming radiation reduction",
        "remaining": None,
        "max_hours": 24,
        "tick": "movement_end",
    }]

    effects = advance_timed_effects(health, effects, 8 * 3600)

    assert effects[0]["remaining"] == 16
    assert effects[0]["time_unit"] == "hour"
    assert effects[0]["remaining_seconds"] == 16 * 3600
    assert advance_timed_effects(health, effects, 16 * 3600) == []


def test_zero_total_health_creates_permanent_death_once():
    health = {
        "current": 0,
        "max": 700,
        "zones": {},
        "effects": [{"type": "shock", "source": "combat_damage"}],
    }

    sync_health_derived_statuses(health)
    sync_health_derived_statuses(health)

    deaths = [effect for effect in health["effects"] if effect["type"] == "death"]
    assert len(deaths) == 1
    assert deaths[0]["source"] == "zero_total_health"
    assert deaths[0]["tick"] == "manual"


def test_zero_head_health_does_not_create_death():
    health = {
        "current": 650,
        "max": 700,
        "zones": {"head": {"current": 0, "max": 50}},
        "effects": [],
    }

    sync_health_derived_statuses(health)

    assert not any(effect["type"] == "death" for effect in health["effects"])


@pytest.mark.parametrize(
    ("organ", "source"),
    [("brain", "zero_brain_health"), ("skull", "zero_skull_health")],
)
def test_zero_brain_or_skull_health_creates_death(organ, source):
    health = {
        "current": 650,
        "max": 700,
        "organs": {organ: {"current": 0, "max": 1}},
        "effects": [],
    }

    sync_health_derived_statuses(health)

    death = next(effect for effect in health["effects"] if effect["type"] == "death")
    assert death["source"] == source


def test_fracture_becomes_unfixed_after_thirty_minutes(monkeypatch):
    health = {"effects": []}
    apply_effect_to_health(health, {"type": "fracture", "area": "leftLeg"})

    effects = advance_timed_effects(health, health["effects"], 10 * 60)
    fracture = next(effect for effect in effects if effect["type"] == "fracture")
    assert fracture["regular_fixation_seconds"] == 20 * 60
    assert fracture["hinged_fixation_seconds"] == 20 * 60

    monkeypatch.setattr("app.services.effects.random.randint", lambda *_: 25)
    effects = advance_timed_effects(health, effects, 20 * 60)

    unfixed = next(effect for effect in effects if effect["type"] == "fracture_unfixed")
    consequence = next(effect for effect in effects if effect["type"] == "fracture_sequela")
    assert unfixed["permanent_penalty"] is True
    assert consequence["area"] == "leftLeg"


@pytest.mark.parametrize('condition', [
    {'effects': [{'type': 'death'}]},
    {'effects': [{'type': 'unconscious'}]},
    {'effects': [{'type': 'sleep'}]},
    {'effects': [{'type': 'critical_condition'}]},
    {'current': 0, 'max': 100},
    {'organs': {'brain': {'current': 0}}},
    {'organs': {'skull': {'current': 0}}},
    {'zones': {'head': {'current': 0}}},
    {'zones': {'chest': {'current': 0}}},
    {'bloodStage': 'fatal'}, {'blood': 'critical'},
    {'temperature': 29}, {'temperature': 41},
    {'intoxication': 96}, {'intoxication': 100},
])
@pytest.mark.parametrize('new_effect', [
    {'type': 'stress', 'value': 1},
    {'type': 'stress_effect', 'source': 'stress_manifestation'},
    {'type': 'stress_stupor'},
    {'type': 'phobia', 'source': 'stress_manifestation'},
])
def test_new_stress_is_blocked_without_mutating_unconscious_or_dead_health(monkeypatch, condition, new_effect):
    from copy import deepcopy
    import app.services.effects as effects_module

    def unexpected_rng(*args):
        pytest.fail('Заблокированный стресс не должен использовать RNG')

    monkeypatch.setattr(effects_module.random, 'randint', unexpected_rng)
    health = {'stress': 4, **deepcopy(condition)}
    health.setdefault('effects', []).append({'id': 'old', 'type': 'stress_effect'})
    before = deepcopy(health)
    assert apply_effect_to_health(health, new_effect) is health
    assert health == before


@pytest.mark.parametrize('effect_type', ['death', 'unconsciousness', 'sleep', 'critical_condition'])
@pytest.mark.parametrize('status', [{'active': False}, {'remaining': 0}, {'remaining': -1}])
def test_inactive_or_expired_conditions_do_not_prevent_stress(effect_type, status):
    health = {'stress': 4, 'effects': [{'type': effect_type, **status}]}
    apply_effect_to_health(health, {'type': 'stress', 'value': 1})
    assert health['stress'] == 5


@pytest.mark.parametrize('condition', [
    {}, {'effects': [{'type': 'shock'}]}, {'effects': [{'type': 'stress_stupor'}]},
    {'current': 1}, {'temperature': 29.1}, {'temperature': 40.9}, {'intoxication': 95},
])
def test_conscious_conditions_keep_normal_stress_increases(condition):
    health = {'stress': 4, **condition}
    apply_effect_to_health(health, {'type': 'stress', 'value': 1})
    assert health['stress'] == 5


@pytest.mark.parametrize('condition_type', ['death', 'unconsciousness'])
def test_stress_reduction_and_healing_remain_available(condition_type):
    from copy import deepcopy

    health = {'stress': 4, 'current': 10, 'max': 100, 'effects': [
        {'id': 'condition', 'type': condition_type},
        {'id': 'old', 'type': 'stress_effect'},
    ]}
    effects_before = deepcopy(health['effects'])
    apply_effect_to_health(health, {'type': 'stress', 'value': -1})
    assert health['stress'] == 3
    assert health['effects'] == effects_before
    apply_effect_to_health(health, {'type': 'heal', 'value': 5})
    assert health['current'] == 15
