import pytest

from app.services.consumable_catalog_rules import apply_catalog_rules
from app.services.consumable_effects import parse_consumable_effects


@pytest.mark.parametrize('name,field,old_type', [
    ('Борщевик','intoxication_delta','intoxication'),
    ('Самопальный стимпак','intoxication_delta','intoxication'),
    ('Регенеративный стимулятор Альфа','exhaustion_delta','temporary_exhaustion'),
    ('Регенеративный стимулятор Бета','exhaustion_delta','temporary_exhaustion'),
    ('Препарат "Радист"','exhaustion_delta','temporary_exhaustion'),
    ('Научный стимпак','radiation_delta','radiation'),
])
def test_official_legacy_profiles_replace_single_dose_with_periodic_consequences(name, field, old_type):
    from copy import deepcopy
    from app.services.consumable_effects import enrich_imported_consumable
    old={'direct':{field:1},'effects':[{'type':old_type,'name':'Старая схема'}]}
    attributes={'catalog_name':name,'raw_description':'Ампула.','consumable':old}
    migrated=enrich_imported_consumable(attributes,'Ампула.')
    assert field not in migrated['direct']
    assert not any(e.get('name')=='Старая схема' for e in migrated['effects'])
    assert any(e['type'] in {'periodic_adjustment','radiation_treatment'} for e in migrated['effects'])
    custom=deepcopy(attributes);custom['consumable']['editor_version']=1
    assert enrich_imported_consumable(custom,'Ампула.')==custom['consumable']


def test_old_official_halfblood_and_morphine_profiles_receive_author_corrections():
    from copy import deepcopy
    from app.services.consumable_effects import enrich_imported_consumable
    for name in ('Стимулятор "Полукровка"','Морфин'):
        description='Ампула.';profile=parse_consumable_effects(name+'. '+description)
        old=deepcopy(profile)
        if name=='Морфин':
            delayed=next(effect for effect in old['effects'] if effect['type']=='delayed_treatment')
            delayed['activate_effects']=[effect for effect in delayed['activate_effects'] if effect['type']!='periodic_adjustment']
        else:old['direct'].pop('infection_delta');old['direct']['infection_multiplier']=.5
        result=enrich_imported_consumable({'catalog_name':name,'raw_description':description,'consumable':old},description)
        if name=='Морфин':
            delayed=next(effect for effect in result['effects'] if effect['type']=='delayed_treatment')
            assert any(effect['type']=='periodic_adjustment' and effect['remaining']==2 for effect in delayed['activate_effects'])
        else:assert result['direct']['infection_delta']==-50 and 'infection_multiplier' not in result['direct']


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
    assert 'exhaustion_delta' not in standard['direct']
    assert any(effect["type"] == "periodic_adjustment" and effect['remaining'] == 3 for effect in standard["effects"])


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
