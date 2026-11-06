from types import SimpleNamespace

import pytest

from app.services.equipment_repair import (
    armor_repair_durability_loss,
    repair_equipment,
    weapon_maximum_penalty,
)


def character_data(engineering=10, *, tool=None, target=None):
    return {
        "skills": {"other": {"engineering": {"base": engineering, "bonus": 0}}},
        "inventory": {"pockets": [tool]},
        "weapons": [target],
    }


def weapon_tool(**overrides):
    profile = {
        "kind": "weapon", "repair_amount": 5, "duration_minutes": 30,
        "minimum_durability": 75, "engineering_min": 10, "max_item_class": 2,
    }
    profile.update(overrides)
    return {
        "name": "Набор оружейника", "category": "tool", "uses": 15, "maxUses": 15,
        "attributes": {"uses": 15, "repair_profile": profile},
    }


def test_weapon_maximum_repair_penalties_follow_workbook_thresholds():
    assert [weapon_maximum_penalty(value) for value in (90, 75, 60, 45, 30, 10, 0)] == [
        0, 2, 5, 8, 15, 25, 40,
    ]


def test_weapon_tool_repairs_weapon_and_spends_one_charge():
    tool = weapon_tool()
    weapon = {
        "name": "Пистолет", "category": "weapon", "itemClass": "Оружие 2",
        "durability": 80, "maxDurability": 100,
    }
    data = character_data(tool=tool, target=weapon)

    result = repair_equipment(data, ["inventory", "pockets", 0], ["weapons", 0])

    assert result["after"] == 85
    assert result["maximum_after"] == 100
    assert tool["uses"] == 14


def test_weapon_tool_checks_engineering_and_minimum_durability():
    data = character_data(engineering=9, tool=weapon_tool(), target={
        "category": "weapon", "durability": 80, "maxDurability": 100,
    })
    with pytest.raises(ValueError, match="Инженерия 10"):
        repair_equipment(data, ["inventory", "pockets", 0], ["weapons", 0])

    data = character_data(tool=weapon_tool(), target={
        "category": "weapon", "durability": 75, "maxDurability": 100,
    })
    with pytest.raises(ValueError, match="выше 75"):
        repair_equipment(data, ["inventory", "pockets", 0], ["weapons", 0])


def test_full_weapon_repairs_can_reach_reduced_maximum_and_clear_jam_12():
    tools = [weapon_tool(
        repair_amount=10, duration_minutes=5, minimum_durability=0,
        engineering_min=0, max_item_class=None, consumed_on_use=True,
    ) for _ in range(3)]
    weapon = {
        "category": "weapon", "durability": 0, "maxDurability": 100,
        "jam": {"result": 12, "repair_required": "full"},
    }
    data = character_data(engineering=0, tool=tools[0], target=weapon)
    data["inventory"]["pockets"] = tools

    for _ in range(3):
        repair_equipment(data, ["inventory", "pockets", 0], ["weapons", 0])

    assert weapon["durability"] == weapon["maxDurability"] == 20
    assert "jam" not in weapon


def test_single_use_field_repair_kit_is_removed_after_repair():
    tool = weapon_tool(
        repair_amount=10, duration_minutes=5, minimum_durability=75,
        engineering_min=0, max_item_class=None, consumed_on_use=True,
    )
    weapon = {"category": "weapon", "durability": 80, "maxDurability": 100}
    data = character_data(engineering=0, tool=tool, target=weapon)

    repair_equipment(data, ["inventory", "pockets", 0], ["weapons", 0])

    assert data["inventory"]["pockets"] == []


def test_legacy_lubrication_kit_only_repairs_weapons_above_75():
    legacy_tool = {
        "name": "Набор смазочных приспособлений",
        "category": "tool",
        "attributes": {"repair_profile": {
            "kind": "weapon", "repair_amount": 10, "duration_minutes": 5,
            "minimum_durability": 0, "engineering_min": 0, "consumed_on_use": True,
        }},
    }
    weapon = {"category": "weapon", "durability": 75, "maxDurability": 100}
    data = character_data(engineering=0, tool=legacy_tool, target=weapon)

    with pytest.raises(ValueError, match="выше 75"):
        repair_equipment(data, ["inventory", "pockets", 0], ["weapons", 0])

    weapon["durability"] = 76
    result = repair_equipment(data, ["inventory", "pockets", 0], ["weapons", 0])

    assert result["after"] == 86
    assert data["inventory"]["pockets"] == []


def test_armor_tool_repairs_stage_and_reduces_base_durability():
    tool = {
        "name": "Набор бронника", "category": "tool", "uses": 20, "maxUses": 20,
        "attributes": {"uses": 20, "repair_profile": {
            "kind": "armor", "repair_stages": 1, "duration_minutes": 30,
            "maximum_damage_stage": 4, "engineering_min": 14, "max_item_class": 3,
        }},
    }
    armor = {
        "name": "Броня", "category": "armor", "itemClass": "Броня 3",
        "stage": 4, "durability": 20, "maxDurability": 20,
        "attributes": {"material": "Композит", "max_durability": 20},
        "currentStageDurability": 1,
    }
    data = character_data(engineering=14, tool=tool, target=armor)

    result = repair_equipment(data, ["inventory", "pockets", 0], ["weapons", 0])

    assert result["stage_after"] == 3
    assert armor["durability"] == 15
    assert armor["currentStageDurability"] == armor["stageDurability"]
    assert tool["uses"] == 19
    assert armor_repair_durability_loss(4) == 5


def test_tool_restoration_recovers_half_charges_and_consumes_restore_kit():
    restore_kit = {
        "name": "Набор восстановления инструментов", "category": "tool", "quantity": 1,
        "attributes": {"repair_profile": {
            "kind": "restore_tool", "restore_fraction": 0.5, "duration_minutes": 0,
        }},
    }
    damaged_tool = {
        "name": "Набор оружейника", "category": "tool", "uses": 2, "maxUses": 15,
        "attributes": {"uses": 15},
    }
    data = {
        "skills": {"other": {"engineering": {"base": 5, "bonus": 0}}},
        "inventory": {"pockets": [restore_kit, damaged_tool]},
    }

    result = repair_equipment(
        data,
        ["inventory", "pockets", 0],
        ["inventory", "pockets", 1],
    )

    assert result["after"] == 9
    assert data["inventory"]["pockets"] == [damaged_tool]


def test_tool_restoration_does_not_consume_kit_for_zero_rounded_recovery():
    kit={"name":"Набор восстановления инструментов","category":"tool","quantity":1,
        "attributes":{"repair_profile":{"kind":"restore_tool","restore_fraction":0.5}}}
    target={"name":"Мини-инструмент","category":"tool","uses":0,"maxUses":1}
    data={"inventory":{"pockets":[kit,target]}}
    with pytest.raises(ValueError,match="ни одного заряда"):
        repair_equipment(data,["inventory","pockets",0],["inventory","pockets",1])
    assert data["inventory"]["pockets"]==[kit,target]
