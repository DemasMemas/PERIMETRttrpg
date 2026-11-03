from app.services.equipment_importer import (
    _canonical_caliber,
    _finalize_weapon_magazine_attributes,
    _integrated_helmet_name,
    _looks_like_grenade_ammo,
    _helmet_protection_zones,
    _magazine_volume,
    _parse_exoskeleton_battery,
    _parse_burst_profile,
    _parse_helmets,
    _parse_melee_weapons,
    _parse_ranged_weapons,
    _parse_tools,
)
from app.services.weapon_catalog import attachment_slots, parse_weapon_size, normalize_template
from app.schemas.templates import ItemTemplateSchema


def test_molotov_cocktail_is_not_imported_as_ammunition():
    assert _looks_like_grenade_ammo({"L": "Коктейль Молотова"}) is True


def test_repair_tools_are_imported_with_machine_readable_profiles():
    rows = [
        {"A": "Инструменты"},
        {
            "A": "Набор инструментов Оружейника (Упрощёные) 15/15",
            "B": "1", "C": "3000", "D": "8", "E": "Ремонт оружия",
        },
        {
            "A": "Набор инструментов Бронника (Расширенные) 35/35",
            "B": "5", "C": "15000", "D": "16", "E": "Ремонт брони",
        },
        {"A": "Вообще другое"},
    ]

    weapon_tool, armor_tool = _parse_tools(rows)

    assert weapon_tool["category"] == "tool"
    assert weapon_tool["attributes"]["uses"] == 15
    assert weapon_tool["attributes"]["repair_profile"]["repair_amount"] == 5
    assert weapon_tool["attributes"]["repair_profile"]["engineering_min"] == 10
    assert armor_tool["attributes"]["uses"] == 35
    assert armor_tool["attributes"]["repair_profile"]["repair_stages"] == 2


def test_field_repair_kits_are_single_use_tools():
    rows = [
        {"A": "Инструменты"},
        {"A": "Набор смазочных приспособлений", "E": "Полевой ремонт оружия"},
        {"A": "Полевой ремкомплект для брони", "E": "Полевой ремонт брони"},
        {"A": "Вообще другое"},
    ]

    weapon_kit, armor_kit = _parse_tools(rows)

    assert weapon_kit["attributes"]["uses"] == 1
    assert weapon_kit["attributes"]["repair_profile"]["consumed_on_use"] is True
    assert weapon_kit["attributes"]["repair_profile"]["minimum_durability"] == 75
    assert weapon_kit["attributes"]["repair_profile"]["repair_amount"] == 10
    assert weapon_kit["attributes"]["repair_profile"]["duration_minutes"] == 5
    assert weapon_kit["attributes"]["repair_profile"]["can_lubricate_prostheses"] is True
    assert armor_kit["attributes"]["uses"] == 1
    assert armor_kit["attributes"]["repair_profile"]["consumed_on_use"] is True


def test_dash_disables_all_automatic_fire_modes():
    profile = _parse_burst_profile("-")

    assert profile["single_shot_options"] == [1]
    assert profile["supports_burst"] is False
    assert profile["supports_suppression"] is False
    assert profile["supports_area_fire"] is False


def test_grenade_launcher_calibers_preserve_letters():
    assert _canonical_caliber("Вог-25") == "ВОГ-25"
    assert _canonical_caliber("Граната ОГ-12") == "ОГ-12"
    assert _canonical_caliber("N-101-2") == "N-101-2"


def test_duplex_is_a_single_fire_option_not_a_burst():
    profile = _parse_burst_profile("Дуплет(Одиночный - 2 выстрела)")

    assert profile["single_shot_options"] == [1, 2]
    assert profile["duplex_size"] == 2
    assert profile["burst_size"] is None
    assert profile["supports_burst"] is False


def test_combined_duplex_and_burst_retains_both_modes():
    profile = _parse_burst_profile("Дуплет(Одиночный - 2 выстрела), Очередь 4")

    assert profile["single_shot_options"] == [1, 2]
    assert profile["burst_size"] == 4
    assert profile["supports_burst"] is True
    assert profile["supports_suppression"] is True
    assert profile["supports_area_fire"] is True


def test_machine_gun_uses_variable_burst_length():
    profile = _parse_burst_profile("Пулеметная. Без штрафа")

    assert profile["machine_gun_burst"] is True
    assert profile["burst_size"] is None
    assert profile["supports_burst"] is True


def test_melee_weapon_imports_weight_class_from_rules_description():
    rows = [{
        "S": "\u0422\u0435\u0441\u0442\u043e\u0432\u044b\u0439 \u0442\u043e\u043f\u043e\u0440",
        "T": "\u0422\u044f\u0436\u0435\u043b\u043e\u0435. 15% \u0431\u0440\u043e\u043d\u0435\u0431\u043e\u0439\u043d\u043e\u0441\u0442\u0438",
        "U": "40",
        "AC": "\u0420\u0443\u0431\u044f\u0449\u0438\u0439",
        "AD": "\u041a\u0440\u0443\u0433\u043e\u0432\u043e\u0439",
    }]

    weapon = _parse_melee_weapons(rows)[0]

    assert weapon["attributes"]["weight_class"] == "\u0422\u044f\u0436\u0435\u043b\u043e\u0435"


def test_melee_weapon_rule_overrides_update_prices_and_tomahawk_attacks():
    rows = [
        {"S": "Топор", "T": "Тяжелое", "U": "40", "W": "2500", "AC": "Рубящий"},
        {
            "S": "Томагавк", "T": "Легкое", "U": "30", "W": "1000",
            "AC": "Рубящий", "AD": "Режущий", "AE": "Колющий",
        },
    ]

    axe, tomahawk = _parse_melee_weapons(rows)

    assert axe["price"] == 5000
    assert tomahawk["attributes"]["allowed_attacks"] == ["Рубящий", "Колющий"]


def test_ranged_weapon_keeps_fractional_and_textual_characteristics():
    rows = [
        {"B": "Пистолеты"},
        {
            "B": "Тестовый пистолет",
            "C": "2(1 в одном)",
            "D": "1",
            "E": "2",
            "F": "9 * 18",
            "G": "8",
            "H": "75",
            "I": "Дуплет(Одиночный - 2 выстрела)",
            "J": "50",
            "K": "100",
            "L": "5",
            "M": "0.5",
            "N": "6",
            "O": "1 500",
            "P": "2",
            "Q": "1",
        },
    ]

    weapon = _parse_ranged_weapons(rows)[0]

    assert weapon["price"] == 1500
    assert weapon["weight"] == 0.5
    assert weapon["attributes"]["magazine_size"] == 2
    assert weapon["attributes"]["magazine_size_raw"] == "2(1 в одном)"
    assert weapon["attributes"]["fire_modes"]["single_shot_options"] == [1, 2]


def test_detachable_weapon_does_not_keep_own_magazine_capacity():
    template = {
        "attributes": {
            "magazine": "legacy",
            "magazine_size": 30,
            "magazine_size_raw": "30",
        },
    }

    _finalize_weapon_magazine_attributes(template, fixed_magazine=False)

    assert template["attributes"] == {"fixedMagazine": False}


def test_fixed_weapon_keeps_only_internal_magazine_capacity():
    template = {
        "attributes": {
            "magazine": "legacy",
            "magazine_size": 6,
            "magazine_size_raw": "6",
        },
    }

    _finalize_weapon_magazine_attributes(template, fixed_magazine=True)

    assert template["attributes"] == {
        "fixedMagazine": True,
        "magazine_size": 6,
    }


def test_cyrillic_acp_is_canonicalized():
    assert _canonical_caliber(".45 аср") == ".45 ACP"


def test_excel_date_serial_is_not_used_as_magazine_volume():
    assert _magazine_volume("45748", "Клипса СП-4") == 0.25


def test_weapon_slots_are_available_without_reimporting_old_catalog():
    original = {"category": "weapon", "subcategory": "Штурмовые винтовки и карабины", "attributes": {"accuracy": 1}}
    result = ItemTemplateSchema().dump(original)
    assert [slot["type"] for slot in result["attributes"]["slots"]] == ["scope", "barrel", "handguard"]
    assert original["attributes"] == {"accuracy": 1}
    assert [slot["type"] for slot in attachment_slots("Гранатомёты")] == ["scope", "handguard"]
    assert attachment_slots("Пистолеты", []) == []


def test_dual_weapon_size_survives_excel_date_conversion():
    expected = {"size": 8, "size_raw": "8/7", "size_options": [8, 7]}
    for value in ("8/7", "44750", 44750, "2022-07-08T00:00:00"):
        assert parse_weapon_size(value) == expected
    assert parse_weapon_size("2,5")["size"] == 2.5
    rows = [{"B": "Штурмовые винтовки и карабины"}, {"B": "АС 19-Б10", "P": "44750", "Q": "2"}]
    template = _parse_ranged_weapons(rows)[0]
    assert template["volume"] == 8
    assert template["attributes"]["size_options"] == [8, 7]
    assert template["attributes"]["raw_row"]["P"] == "44750"


def test_old_weapon_volume_is_repaired_in_snapshot_without_mutating_saved_data():
    from app.models import LobbyCharacter
    original = {"weapons": [{"id": "gun", "volume": 44750}], "inventory": {"backpack": [
        {"category": "container", "contents": [{"category": "weapon", "volume": 44750, "attributes": {"size": 44750}}]},
        {"category": "other", "volume": 44750},
    ]}, "health": {"effects": [{"item_snapshot": {"category": "weapon", "volume": 44750}}]}}
    character = LobbyCharacter(id=1, revision=3, data=original)
    snapshot = character.data_snapshot()
    assert snapshot["weapons"][0]["volume"] == 8
    item = snapshot["inventory"]["backpack"][0]["contents"][0]
    assert item["volume"] == 8
    assert item["attributes"]["size_options"] == [8, 7]
    assert snapshot["inventory"]["backpack"][1]["volume"] == 44750
    assert snapshot["health"] == original["health"]
    assert original["weapons"][0]["volume"] == 44750
    normalized = normalize_template({"category": "weapon", "volume": 44750, "attributes": {"size": 44750}})
    assert normalized["volume"] == 8
    assert normalized["attributes"]["size_raw"] == "8/7"


def test_exoskeleton_battery_is_imported_as_one_day_module():
    templates = _parse_exoskeleton_battery([{
        "A": "Аккумуляторы Экзоскелета",
        "B": "2.5",
        "C": "3500",
        "D": "5",
        "E": "Поддерживают работоспособность экзоскелета.",
    }])

    assert templates == [{
        "name": "Аккумуляторы Экзоскелета",
        "category": "exoskeleton_module",
        "subcategory": "battery",
        "item_class": None,
        "description": "Поддерживают работоспособность экзоскелета.",
        "price": 3500,
        "weight": 2.5,
        "volume": 5.0,
        "attributes": {
            "import_source": "equipment_workbook",
            "slot_type": "exoskeleton_battery",
            "charge_days": 1,
            "remaining_days": 1,
            "raw_row": {
                "A": "Аккумуляторы Экзоскелета",
                "B": "2.5",
                "C": "3500",
                "D": "5",
                "E": "Поддерживают работоспособность экзоскелета.",
            },
        },
        "compatible_ids": [],
    }]


def test_integrated_helmet_uses_armor_specific_name():
    assert _integrated_helmet_name("Комбинезон Купол") == "Шлем Купол"
    assert _integrated_helmet_name("Экзоскелет") == "Шлем Экзоскелета"


def test_helmet_import_removes_misc_prefix_and_skips_embedded_templates():
    rows = [{} for _ in range(61)]
    rows[22] = {
        "A": "Головной убор",
        "B": "Прочее Ушанка",
        "C": "1",
        "I": "Текстиль",
    }
    rows[56] = {
        "A": "Шлем Купол",
        "C": "10%",
        "D": "3",
        "E": "3",
    }

    helmets = _parse_helmets(rows)

    assert [template["name"] for template in helmets] == ["Ушанка"]
    assert all(template["subcategory"] != "Встроенный" for template in helmets)


def test_helmet_protection_zones_follow_rulebook_groups():
    assert _helmet_protection_zones("Советский Котелок 68Г") == ["crown", "back"]
    assert _helmet_protection_zones("Шлем Ударник-М") == ["crown", "back", "ears"]
    assert _helmet_protection_zones("Шлем КыСа-2") == ["crown", "back", "ears", "face"]
