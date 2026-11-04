import pytest

from app.services.consumable_catalog_rules import apply_catalog_rules
from app.services.consumable_effects import parse_consumable_effects


@pytest.mark.parametrize(
    ("description", "expected_stage"),
    [
        ("Жгут.", "medium"),
        ("Турникет.", "severe"),
        ("Жгут Альфа.", "extreme"),
    ],
)
def test_tourniquet_variants_keep_distinct_treatment_stage(description, expected_stage):
    direct = parse_consumable_effects(description)["direct"]

    assert direct["applications"] == [{
        "kind": "bleeding", "max_stage": expected_stage, "internal": False,
        "action_points": 1, "treated": False, "all": False,
        "allow_weakening": True,
    }]
    assert direct["treatment_action_points"] == 4


def test_sterile_bandage_marks_both_applications_as_treated():
    direct = parse_consumable_effects("Бинт стерильный.")["direct"]

    assert [application["treated"] for application in direct["applications"]] == [True, True]
    assert direct["applications"][1]["item_uses"] == 2


def test_iodine_treatment_keeps_ten_turn_duration():
    profile = parse_consumable_effects("Йод-плюс. Ампула. -2.5 радиации")
    treatment = next(effect for effect in profile["effects"] if effect["type"] == "radiation_treatment")

    assert treatment["remaining"] == 10
    assert treatment["value"] == -2.5
    assert "radiation_delta" not in profile["direct"]


def test_radist_variants_do_not_share_capacity_or_temporary_exhaustion():
    light = parse_consumable_effects('Препарат "Радист"-Л. Ампула.')
    standard = parse_consumable_effects('Препарат "Радист". Ампула.')

    assert light["direct"]["radiation_filter_capacity"] == 50
    assert standard["direct"]["radiation_filter_capacity"] == 100
    assert not any(effect["type"] == "temporary_exhaustion" for effect in light["effects"])
    assert any(effect["type"] == "temporary_exhaustion" for effect in standard["effects"])


def test_owl_variants_have_different_sleep_and_aftereffects():
    owl = parse_consumable_effects("Стимулятор Сова. Действует 4 часа. +1 истощения")
    owl_n = parse_consumable_effects("Стимулятор Сова-Н. Действует 8 часов. +1 истощения")

    assert owl["direct"]["satisfy_sleep"] is True
    assert 'sleep_block_hours' not in owl['direct']
    assert {m['stat']: m['value'] for m in owl['modifiers']}['light_awareness'] == -3
    assert all(m['remaining'] == 240 for m in owl['modifiers'])
    assert "exhaustion_delta" not in owl["direct"]
    assert any(effect["type"] == "deferred_adjustment" for effect in owl["effects"])
    assert owl_n["direct"]["satisfy_sleep"] is True
    assert 'sleep_block_hours' not in owl_n['direct']
    assert all(m['remaining'] == 480 for m in owl_n['modifiers'])
    assert not any(m['stat'] == 'light_awareness' for m in owl_n['modifiers'])
    assert owl_n["direct"]["exhaustion_delta"] == 1
    assert not any(effect["type"] == "deferred_adjustment" for effect in owl_n["effects"])


def test_declarative_rule_does_not_reuse_mutable_effect_between_profiles():
    rule = ({"contains": ("test",), "direct": {"stress_advantage": True},
             "effects": ({"type": "deferred_adjustment", "value": 1,
                          "adjustments": [{"field": "stress", "delta": -1}]},)},)
    first = {"direct": {}, "effects": [], "modifiers": [], "status_removals": [],
             "status_additions": [], "requirements": [], "notes": []}
    second = {"direct": {}, "effects": [], "modifiers": [], "status_removals": [],
              "status_additions": [], "requirements": [], "notes": []}

    apply_catalog_rules(first, "test", rule)
    first["effects"][0]["adjustments"][0]["delta"] = -9
    apply_catalog_rules(second, "test", rule)

    assert second["effects"][0]["adjustments"][0]["delta"] == -1
    assert second["direct"]["stress_advantage"] is True
