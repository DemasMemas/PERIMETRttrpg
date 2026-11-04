import pytest

from app.services.consumable_effects import parse_consumable_effects
from app.services.effects import (
    advance_timed_effects,
    apply_intoxication_dose,
    apply_effect_to_health,
    apply_expired_effects_to_health,
    apply_periodic_effects_to_health,
    get_bleeding_state,
    tick_effects,
    sync_health_derived_statuses,
)


@pytest.mark.parametrize('value', [99, 100, 101])
def test_intoxication_coma_threshold_and_single_saved_roll(monkeypatch, value):
    draws = []
    monkeypatch.setattr('app.services.effects.random.randint', lambda *args: draws.append(args) or 16)
    health = {'intoxication': value, 'effects': []}
    sync_health_derived_statuses(health)
    sync_health_derived_statuses(health)
    coma = [e for e in health['effects'] if e['source'] == 'deadly_intoxication']
    assert len(draws) == (value >= 100)
    assert len(coma) == (value >= 100)
    if coma:
        assert coma[0]['type'] == 'unconsciousness'
        assert coma[0]['remaining_seconds'] == 86400


def test_intoxication_coma_persists_below_threshold_and_wakes_at_24_hours(monkeypatch):
    from copy import deepcopy
    monkeypatch.setattr('app.services.effects.random.randint', lambda *_: 100)
    health = {'intoxication': 120, 'effects': []}
    sync_health_derived_statuses(health)
    health['effects'] = advance_timed_effects(health, health['effects'], 3600)
    reloaded = deepcopy(health)
    apply_intoxication_dose(reloaded, -30)
    reloaded['effects'] = advance_timed_effects(reloaded, reloaded['effects'], 23 * 3600 - 1)
    assert reloaded['effects'][0]['remaining_seconds'] == 1
    reloaded['effects'] = advance_timed_effects(reloaded, reloaded['effects'], 1)
    assert reloaded['intoxication'] == 20
    assert not reloaded['effects']


def test_new_alcohol_dose_rechecks_death_without_resetting_coma_clock(monkeypatch):
    draws = iter([16, 100, 15])
    monkeypatch.setattr('app.services.effects.random.randint', lambda *_: next(draws))
    health = {'intoxication': 100, 'effects': []}
    sync_health_derived_statuses(health)
    health['effects'] = advance_timed_effects(health, health['effects'], 3600)
    apply_intoxication_dose(health, 20)
    assert health['intoxication'] == 120
    assert health['effects'][0]['remaining_seconds'] == 23 * 3600
    sync_health_derived_statuses(health)
    apply_intoxication_dose(health, 20)
    assert [e['type'] for e in health['effects']] == ['death']
    apply_intoxication_dose(health, 20)  # На уже мёртвом персонаже новых бросков нет.


@pytest.mark.parametrize('hours,expected,roll_count,remaining', [
    (24, 170, 2, 86400), (48, 100, 3, 86400), (72, 30, 3, None),
    (49, 100, 3, 23 * 3600),
])
def test_intoxication_wake_repeats_days_and_handles_large_time_steps(
        monkeypatch, hours, expected, roll_count, remaining):
    draws = []
    monkeypatch.setattr('app.services.effects.random.randint', lambda *args: draws.append(args) or 100)
    health = {'intoxication': 240, 'effects': []}
    sync_health_derived_statuses(health)
    health['effects'] = advance_timed_effects(health, health['effects'], hours * 3600)
    assert health['intoxication'] == expected
    assert len(draws) == roll_count
    assert (health['effects'][0]['remaining_seconds'] if health['effects'] else None) == remaining


def test_intoxication_can_kill_at_repeated_waking_check(monkeypatch):
    rolls = iter([16, 15])
    monkeypatch.setattr('app.services.effects.random.randint', lambda *_: next(rolls))
    health = {'intoxication': 170, 'effects': []}
    sync_health_derived_statuses(health)
    health['effects'] = advance_timed_effects(health, health['effects'], 72 * 3600)
    assert health['intoxication'] == 100
    assert [e['type'] for e in health['effects']] == ['death']


def test_legacy_survived_intoxication_result_starts_coma_without_reroll(monkeypatch):
    def forbidden(*_):
        raise AssertionError('Сохранённый результат не перебрасывается')
    monkeypatch.setattr('app.services.effects.random.randint', forbidden)
    health = {'intoxication': 110, 'effects': [],
              'combatMeta': {'intoxicationDeathChecked': True, 'intoxicationDeathRoll': 85}}
    sync_health_derived_statuses(health)
    assert health['effects'][0]['remaining_seconds'] == 86400


@pytest.mark.parametrize('roll', [15, 16])
def test_new_dose_on_unconscious_below_threshold_rechecks_without_starting_extra_coma(monkeypatch, roll):
    draws = []
    monkeypatch.setattr('app.services.effects.random.randint', lambda *args: draws.append(args) or roll)
    health = {'intoxication': 10, 'effects': [{'type': 'unconsciousness', 'source': 'other_injury'}]}
    apply_intoxication_dose(health, 20)
    assert draws == [(1, 100)]
    assert health['intoxication'] == 30
    assert any(e['type'] == 'death' for e in health['effects']) == (roll == 15)
    assert not any(e['source'] == 'deadly_intoxication' and e['type'] == 'unconsciousness' for e in health['effects'])


def test_catalog_iodine_keeps_fractional_radiation_treatment_and_real_temporary_types():
    from app.services.effects import normalize_effect_list
    profile = parse_consumable_effects('Йод-плюс. Ампула. -2.5 радиации')
    health = {'radiation': 10, 'effects': normalize_effect_list(profile['effects'])}
    apply_periodic_effects_to_health(health, health['effects'], 'turn_end')
    assert health['radiation'] == 7.5
    for expected in [5, 2.5, 0]:
        apply_periodic_effects_to_health(health, health['effects'], 'turn_end')
        assert health['radiation'] == expected
    effects = normalize_effect_list([{'type': 'temporary_exhaustion'}, {'type': 'temporary_hp_max'}])
    assert [effect['type'] for effect in effects] == ['temporary_exhaustion', 'temporary_hp_max']


def test_deadly_intoxication_roll_happens_once_until_level_drops(monkeypatch):
    rolls = iter((15, 99))
    monkeypatch.setattr('app.services.effects.random.randint', lambda _low, _high: next(rolls))
    health = {'intoxication': 100, 'effects': []}

    sync_health_derived_statuses(health)
    sync_health_derived_statuses(health)

    assert health['combatMeta']['intoxicationDeathRoll'] == 15
    assert sum(effect['type'] == 'death' for effect in health['effects']) == 1

    health['intoxication'] = 99
    health['effects'] = []
    sync_health_derived_statuses(health)
    health['intoxication'] = 100
    sync_health_derived_statuses(health)

    assert health['combatMeta']['intoxicationDeathRoll'] == 99
    assert not any(effect['type'] == 'death' for effect in health['effects'])


def test_dried_fish_reduces_stress_without_intoxication():
    profile = parse_consumable_effects(
        "Сушеная рыба. Для использования нужна 1/3 бутылки воды или 1 порция любого алкоголя. "
        "Снижает Уровень стресса на 1. -1/2 Уровня истощения. 3 Использования"
    )

    assert "intoxication_delta" not in profile["direct"]
    assert profile["direct"]["stress_delta"] == -1
    assert profile["direct"]["requires_water_fraction"] == 1 / 3


def test_beer_is_marked_as_alcohol_for_food_requirements():
    profile = parse_consumable_effects(
        "\u0411\u0430\u043d\u043a\u0430 \u043f\u0438\u0432\u0430. "
        "+20 \u043e\u043f\u044c\u044f\u043d\u0435\u043d\u0438\u044f."
    )

    assert profile["direct"]["is_alcohol"] is True


def test_tobacco_requires_fire_and_uses_five_minute_delay():
    profile = parse_consumable_effects(
        "Сигареты Тест. Снижение стресса через 5 минут"
    )

    assert profile["direct"]["requires_fire"] is True
    delayed = next(
        effect for effect in profile["effects"]
        if effect["type"] == "delayed_adjustment"
    )
    assert delayed["time_unit"] == "minute"
    assert delayed["remaining_seconds"] == 300


@pytest.mark.parametrize(
    ('name', 'reduction'),
    [
        ('Психостимулирующее "Прорыв"', 5),
        ('Препарат К.О.Д.', 10),
    ],
)
def test_withdrawal_support_drugs_have_delayed_one_hour_effect(name, reduction):
    direct = parse_consumable_effects(name)['direct']

    assert direct['withdrawal_check_difficulty_reduction'] == reduction
    assert direct['withdrawal_support_delay_minutes'] == 1
    assert direct['withdrawal_support_duration_minutes'] == 60


def test_dry_ration_counts_as_food_and_water():
    profile = parse_consumable_effects(
        "\u0421\u0443\u0445\u043e\u0439 \u043f\u0430\u0435\u043a. "
        "\u0414\u043b\u044f \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u044f "
        "\u043d\u0443\u0436\u043d\u0430 1/3 \u0431\u0443\u0442\u044b\u043b\u043a\u0438 \u0432\u043e\u0434\u044b. "
        "\u0421\u0447\u0438\u0442\u0430\u0435\u0442\u0441\u044f \u0435\u0434\u043e\u0439 \u0438 \u0432\u043e\u0434\u043e\u0439."
    )

    assert profile["direct"]["nutrition"] == 1
    assert profile["direct"]["satisfy_food"] is True
    assert profile["direct"]["satisfy_water"] is True
    assert profile["direct"]["requires_water_fraction"] == 1 / 3


def test_water_requirement_is_inferred_for_any_consumable_description():
    profile = parse_consumable_effects(
        "\u0414\u043b\u044f \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u044f "
        "\u043d\u0443\u0436\u043d\u044b 2/3 \u0431\u0443\u0442\u044b\u043b\u043a\u0438 \u0432\u043e\u0434\u044b."
    )

    assert profile["direct"]["requires_water_fraction"] == 2 / 3


def test_jelly_and_cotton_have_different_bleeding_effects():
    jelly = parse_consumable_effects(
        'Кровоостанавливающее "Желе". Ампула. Останавливает ухудшение стадий кровопотери на 5 ходов.'
    )
    cotton = parse_consumable_effects(
        'Кровоостанавливающее "Хлопок". Ампула. Останавливает все кровотечения. '
        'Блокирует появление новых кровотечений на 10 ходов. +1 Уровень истощения.'
    )

    jelly_types = {effect["type"] for effect in jelly["effects"]}
    cotton_types = {effect["type"] for effect in cotton["effects"]}
    assert "blood_loss_freeze" in jelly_types
    assert "bleeding_prevention" not in jelly_types
    assert "bleeding_prevention" in cotton_types
    assert cotton["direct"]["stop_all_bleeding"] is True


def test_antirad_is_periodic_instead_of_immediate():
    profile = parse_consumable_effects("Антирад-Б. Ампула. -5 радиации. Действует 4 хода. +1 Уровень истощения")
    treatment = next(effect for effect in profile["effects"] if effect["type"] == "radiation_treatment")

    assert "radiation_delta" not in profile["direct"]
    assert treatment["value"] == -5
    assert treatment["remaining"] == 4
    assert treatment["tick"] == "turn_end"


def test_bleedings_on_different_body_parts_do_not_merge():
    health = {"effects": []}
    apply_effect_to_health(health, {"id": "a", "type": "bleeding_external_light", "area": "leftArm"})
    apply_effect_to_health(health, {"id": "b", "type": "bleeding_external_light", "area": "rightArm"})

    bleeding = get_bleeding_state(health)
    assert len(health["effects"]) == 2
    assert bleeding["totalSeverity"] == 2
    assert {item["area"] for item in bleeding["effects"]} == {"leftArm", "rightArm"}


def test_periodic_effect_applies_on_every_remaining_turn():
    health = {
        "current": 100,
        "max": 700,
        "zones": {
            "leftArm": {"current": 10, "max": 200},
            "rightArm": {"current": 20, "max": 200},
        },
        "effects": [],
    }
    effects = [{"type": "regeneration", "value": 50, "remaining": 2, "tick": "turn_end"}]

    apply_periodic_effects_to_health(health, effects, "turn_end")
    effects = tick_effects(effects, "turn_end")
    apply_periodic_effects_to_health(health, effects, "turn_end")
    effects = tick_effects(effects, "turn_end")

    assert health["current"] == 200
    assert health["zones"]["leftArm"]["current"] == 60
    assert health["zones"]["rightArm"]["current"] == 70
    assert effects == []


def test_immediate_healing_restores_pool_and_distributes_over_zones():
    health = {
        "current": 100,
        "max": 700,
        "zones": {
            "leftLeg": {"current": 20, "max": 100},
            "rightLeg": {"current": 40, "max": 100},
        },
    }

    apply_effect_to_health(health, {"type": "heal", "value": 30})

    assert health["current"] == 130
    assert health["zones"]["leftLeg"]["current"] == 35
    assert health["zones"]["rightLeg"]["current"] == 55


def test_zone_healing_redistributes_overflow_from_nearly_full_zone():
    health = {
        "current": 100,
        "max": 700,
        "zones": {
            "leftArm": {"current": 95, "max": 100},
            "rightArm": {"current": 20, "max": 100},
        },
    }

    apply_effect_to_health(health, {"type": "heal", "value": 30})

    assert health["zones"]["leftArm"]["current"] == 100
    assert health["zones"]["rightArm"]["current"] == 45


def test_zone_healing_distributes_only_whole_points():
    health = {
        "current": 100,
        "max": 700,
        "zones": {
            name: {"current": 100, "max": 150}
            for name in ("head", "chest", "abdomen", "leftArm", "rightArm", "leftLeg")
        },
    }

    apply_effect_to_health(health, {"type": "heal", "value": 25})

    currents = [zone["current"] for zone in health["zones"].values()]
    assert currents == [105, 104, 104, 104, 104, 104]
    assert all(isinstance(value, int) for value in currents)


def test_internal_bleeding_keeps_anatomical_source():
    health = {"effects": []}
    apply_effect_to_health(health, {
        "id": "organ-tear",
        "type": "bleeding_internal_severe",
        "area": "chest",
    })

    bleeding = get_bleeding_state(health)

    assert bleeding["breakdown"]["internal"]["severe"] == 1
    assert bleeding["breakdown"]["internal"]["total"] == 5
    assert bleeding["effects"][0]["kind"] == "internal"
    assert bleeding["effects"][0]["area"] == "chest"


def test_delayed_treatment_activates_adjustment_and_duration_effect():
    health = {"painLevel": 4, "effects": []}
    delayed = {
        "type": "delayed_treatment",
        "remaining": 1,
        "tick": "turn_end",
        "adjustments": [{"field": "painLevel", "delta": -1, "min": 0, "max": 10}],
        "activate_effects": [{"type": "analgesia", "value": 1, "remaining": 8, "tick": "turn_end"}],
    }

    apply_expired_effects_to_health(health, [delayed])

    assert health["painLevel"] == 3
    assert any(effect["type"] == "analgesia" and effect["remaining"] == 8 for effect in health["effects"])


def test_blood_collection_kit_is_not_mistaken_for_blood_packet():
    profile = parse_consumable_effects(
        "Набор для забора крови. При использовании выкачивает кровь и преобразует Набор для забора крови в Пакет крови."
    )

    assert profile["direct"]["blood_collection"] is True
    assert "requires_infusion_tool" not in profile["direct"]
    assert not any(effect["type"] == "blood_recovery" for effect in profile["effects"])


def test_hinged_splint_is_not_mistaken_for_tourniquet():
    profile = parse_consumable_effects(
        "Шина Шарнирова. Используется при лечении переломов. При использовании со жгутом позволяет использовать его на любой конечности."
    )

    assert profile["direct"]["fracture_splint"] is True
    assert "applications" not in profile["direct"]
    assert "tourniquet" not in profile["direct"]


def test_named_limb_restoratives_target_a_limb_for_ten_minutes():
    cases = (
        ("Ампула Миколия", False),
        ("Стимулятор Варвар", False),
        ("Стимулятор Викинг", True),
        ("Препарат 02", False),
    )

    for name, keeps_limb_active in cases:
        direct = parse_consumable_effects(name)["direct"]
        assert direct["temporary_limb_health_minutes"] == 10
        assert direct["suppress_limb_trauma"] is True
        assert direct["affects_all_limbs"] is True
        assert bool(direct.get("minimum_limb_health")) is keeps_limb_active


def test_special_ampoules_use_delayed_targeted_limb_treatment():
    second_life = parse_consumable_effects(
        '"Вторая жизнь". Ампула. Излечивает перелом и восстанавливает конечность. '
        'Она имеет 50 здоровья. Срабатывает через 1 минуту.'
    )["direct"]
    chimera = parse_consumable_effects(
        '"Химера". Ампула. Излечивает перелом. Срабатывает через 1 минуту. '
        'При использовании на конечности без перелома -200 Здоровья в эту конечность.'
    )["direct"]

    assert second_life["special_limb_treatment"] == "second_life"
    assert second_life["delayed_limb_treatment_minutes"] == 1
    assert chimera["special_limb_treatment"] == "chimera"
    assert chimera["invalid_limb_damage"] == -200


def test_aybolit_is_a_surgical_kit():
    direct = parse_consumable_effects(
        'Кустарный набор "Айболит". Восстанавливает часть тела. Она имеет 1 здоровье.'
    )["direct"]

    assert direct["surgical_kit"] is True


def test_full_limb_restoration_kit_parses_cost_pain_and_full_healing():
    direct = parse_consumable_effects(
        "Набор полного восстановления конечности. Время использования - 15 ОД. "
        "Восстанавливает утерянный орган или искореженную конечность. "
        "Бонус медикамента +3. 5 использований. Усиливает боль на 3 уровня"
    )["direct"]

    assert direct["action_points_cost"] == 15
    assert direct["pain_delta"] == 3
    assert direct["med_bonus"] == 3
    assert direct["uses"] == 5
    assert direct["surgical_kit"] is True
    assert direct["restore_missing_part"] is True
    assert direct["restore_full_body_part"] is True
    assert direct["catastrophic_limb_surgery"] == "full_restoration"


def test_surgeon_kit_can_restore_mangled_limb():
    direct = parse_consumable_effects(
        'Хирургический набор "Хирург". Время использования - 10 ОД. '
        'Восстанавливает часть тела. Она имеет 30 здоровья.'
    )["direct"]

    assert direct["catastrophic_limb_surgery"] == "surgeon"
    assert direct["surgical_kit"] is True


def test_hemostatic_applications_allow_weakening_next_bleeding_stage():
    profile = parse_consumable_effects(
        "Губка коллагеновая. Имеет возможность остановить Слабое кровотечение за 1 ОД "
        "или Среднее за 2 ОД."
    )

    applications = profile["direct"]["applications"]
    assert applications == [
        {
            "kind": "bleeding",
            "max_stage": "light",
            "internal": False,
            "action_points": 1,
            "treated": False,
            "all": False,
            "allow_weakening": True,
        },
        {
            "kind": "bleeding",
            "max_stage": "medium",
            "internal": False,
            "action_points": 2,
            "treated": False,
            "all": False,
            "allow_weakening": True,
        },
    ]


def test_bandage_uses_four_action_point_treatment_time():
    direct = parse_consumable_effects(
        "Бинт. Останавливает Слабое кровотечение."
    )["direct"]

    assert direct["treatment_action_points"] == 4
    assert direct["treatment_time_uses_medicine"] is True


def test_tampon_and_tourniquet_have_their_treatment_times():
    tampon = parse_consumable_effects(
        "Антисептический тампон. Останавливает Слабое кровотечение."
    )["direct"]
    tourniquet = parse_consumable_effects(
        "Жгут. Останавливает Среднее кровотечение."
    )["direct"]

    assert tampon["treatment_action_points"] == 2
    assert tampon["treatment_time_uses_medicine"] is True
    assert tourniquet["treatment_action_points"] == 4
    assert tourniquet["treatment_time_uses_medicine"] is True


def test_internal_hemostatic_keeps_internal_targeting_and_weakening():
    profile = parse_consumable_effects(
        "Глобулин. Останавливает внутреннее Сильное кровотечение."
    )

    application = profile["direct"]["applications"][0]
    assert application["max_stage"] == "severe"
    assert application["internal"] is True
    assert application["allow_weakening"] is True


def test_shov_hemostatic_targets_only_severe_internal_bleeding():
    profile = parse_consumable_effects(
        'Гемостатик "Шов". Ампула. Останавливает Сильное внутреннее кровотечение. '
        'Не пригодно для внешних кровотечений. Бонус медикамента -8.'
    )

    assert profile["direct"]["medical_difficulty"] == 4
    assert profile["direct"]["application_form"] == "injectable"
    assert profile["direct"]["med_bonus"] == -8
    assert profile["direct"]["applications"] == [
        {
            "kind": "bleeding",
            "max_stage": "severe",
            "internal": True,
            "internal_only": True,
            "action_points": 1,
            "treated": False,
            "all": False,
            "allow_weakening": True,
        }
    ]


def test_adrenaline_keeps_its_own_action_point_profile():
    profile = parse_consumable_effects(
        "Адреналин. Ампула. +3 ОД на 3 хода. Убирает одышку. -50 здоровья -1 Уровень истощения. "
        "Нельзя использовать в один ход с энергетиками и эпинефрином"
    )

    assert profile["direct"]["action_points_delta"] == 3
    assert profile["direct"]["hp"] == -50
    assert "weight_delta" not in profile["direct"]


def test_movement_duration_is_applied_to_stat_modifier():
    profile = parse_consumable_effects(
        "Стимулятор. +2 к Силе на 3 перемещения."
    )

    strength = next(modifier for modifier in profile["modifiers"] if modifier["stat"] == "strength")
    assert profile["direct"]["duration"] == 3
    assert profile["direct"]["duration_phase"] == "movement_end"
    assert strength["remaining"] == 3


def test_emotion_suppressor_blocks_attack_stress_and_grants_manifestation_advantage():
    direct = parse_consumable_effects(
        "Подавитель эмоций. Действует 6 перемещений. -25 Пси-состояний."
    )["direct"]

    assert direct["stress_advantage"] is True
    assert direct["stress_attack_block_chance"] == 50
    assert direct["duration"] == 6
    assert direct["duration_phase"] == "movement_end"


def test_harmony_grants_stress_manifestation_advantage_for_thirty_minutes():
    direct = parse_consumable_effects(
        "Стимулятор Гармония. -3 Стресса. -20 Пси-состояний. Действует 30 минут."
    )["direct"]

    assert direct["stress_advantage"] is True
    assert direct["duration"] == 30
    assert direct["duration_phase"] == "time_elapsed"


def test_pain_block_returns_accumulated_pain_on_expiry():
    health = {"painLevel": 1, "exhaustion": 0, "combatMeta": {"blockedPain": 3}, "effects": []}
    effect = {
        "type": "pain_block", "remaining": 1, "tick": "turn_end",
        "return_fraction": 1, "exhaustion_on_expire": 1,
    }

    apply_expired_effects_to_health(health, [effect])

    assert health["painLevel"] == 4
    assert health["exhaustion"] == 1
    assert "blockedPain" not in health["combatMeta"]


def test_world_radiation_drugs_keep_percent_capacity_and_day_duration():
    cases = [
        ('Препарат "Радист"-Л. Ампула.', 100, 50),
        ('Препарат "Радист". Ампула.', 100, 100),
        ('Противорадиационное "Брезент"-ПБ. Ампула.', 50, 100),
    ]

    for description, percent, capacity in cases:
        profile = parse_consumable_effects(description)
        effect = next(
            item for item in profile["effects"]
            if item["type"] == "radiation_filter"
        )
        assert profile["direct"]["exhaustion_delta"] == 1
        assert effect["value"] == percent
        assert effect["capacity"] == capacity
        assert effect["remaining_capacity"] == capacity
        assert effect["remaining"] == 24
        assert effect["time_unit"] == "hour"
        assert effect["remaining_seconds"] == 24 * 60 * 60

    radist = parse_consumable_effects('Препарат "Радист". Ампула.')
    temporary_exhaustion = next(
        item for item in radist["effects"]
        if item["type"] == "temporary_exhaustion"
    )
    assert temporary_exhaustion["remaining"] == 3
    assert temporary_exhaustion["tick"] == "turn_end"
