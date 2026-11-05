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


def test_small_item_fraction_and_pouch_variants_have_different_semantics():
    from app.services.catalog_import import excel_fraction, volume, rig_templates
    assert excel_fraction('43862') == '1/2'
    assert volume('44682') == .2
    assert volume('1/10') == .1
    items = rig_templates([{'A': 'Гранатный подсумок -', 'B': '43862', 'C': '150/250', 'D': '43862'}])
    assert [item['attributes']['capacity'] for item in items] == [1, 2]
    assert [item['price'] for item in items] == [150, 250]
    from app.services.consumable_importer import _as_weight_float
    assert _as_weight_float('45931') == .1


def test_missing_catalog_import_preserves_customized_records_and_is_idempotent(app):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.catalog_import import template
    from app.services.equipment_importer import insert_missing_templates
    old = ItemTemplate(name='Пояс', category='belt', price=735, attributes={'custom_rule': True})
    db.session.add(old)
    db.session.flush()
    old_id = old.id
    entries = [template('Пояс', 'belt', 500, 0, 0), template('КПК', 'misc', 1500, .25, 1)]
    assert insert_missing_templates(entries)['inserted'] == 1
    assert insert_missing_templates(entries)['inserted'] == 0
    db.session.commit()
    assert db.session.get(ItemTemplate, old_id).price == 735
    assert db.session.get(ItemTemplate, old_id).attributes == {'custom_rule': True}
    assert ItemTemplate.query.count() == 2


def test_new_equipment_book_includes_side_tables_and_all_rig_variants():
    import pytest


def test_new_helmet_headers_keep_price_class_volume_and_penalties_in_their_columns():
    rows=[{} for _ in range(45)]
    rows[21]={'B':'Шлема','M':'Категория','N':'Цена'}
    rows[24]={'B':'Прочее Ушанка','C':'5','D':'0','I':'Текстиль','J':'0','K':'0','L':'44682',
        'M':'1','N':'1000','O':'0','P':'1','Q':'Не весит'}
    rows[27]={'B':'Противогаз ГП-5','C':'8','I':'Текстиль','J':'3','K':'10','L':'-3','M':'1','N':'2500','O':'0','P':'4','Q':'1 кг'}
    result=_parse_helmets(rows);hat,mask=result
    assert (hat['item_class'],hat['price'],hat['volume'])==('1',1000,1)
    assert hat['attributes']['ergonomics_penalty']=='0' and hat['attributes']['charisma_bonus']=='1.5'
    assert hat['attributes']['modification_category']=='Ушанка'
    assert (mask['price'],mask['volume'])==(2500,4)
    assert mask['attributes']['accuracy_penalty']=='3' and mask['attributes']['ergonomics_penalty']=='10'
    assert mask['attributes']['charisma_bonus']=='-3'


def test_shifted_helmet_repair_requires_proof_and_preserves_manual_changes():
    from copy import deepcopy
    from app.services.equipment_catalog import shifted_helmet_updates
    row={'M':'1','N':'1000','O':'0','P':'1','J':'0','K':'0','L':'44682'}
    previous={'name':'Ушанка','category':'helmet','item_class':'1000','price':0,'volume':0,
        'attributes':{'import_source':'equipment_workbook','raw_row':row,'accuracy_penalty':'0','ergonomics_penalty':'44682',
            'charisma_bonus':'1.5','movement_penalty':1,'manual_note':'Сохранить'}}
    canonical={'item_class':'1','price':1000,'volume':1,'attributes':{'accuracy_penalty':'0','ergonomics_penalty':'0',
        'charisma_bonus':'1.5','movement_penalty':0,'modification_category':'Ушанка','soft_headwear':True,'integrated_visor':False}}
    result=shifted_helmet_updates(previous,canonical)
    assert result['item_class']=='1' and result['price']==1000 and result['volume']==1
    assert result['attributes']['ergonomics_penalty']=='0' and result['attributes']['manual_note']=='Сохранить'
    manual=deepcopy(previous);manual['price']=3000
    assert shifted_helmet_updates(manual,canonical) is None
    already={**previous,**result};assert shifted_helmet_updates(already,canonical) is None


def test_helmet_repair_migration_is_idempotent_and_preserves_manual_price(app,monkeypatch):
    import importlib.util
    from pathlib import Path
    from app.extensions import db
    from app.models.templates import ItemTemplate
    row={'M':'1','N':'1000','O':'0','P':'1','J':'0','K':'0','L':'44682'}
    attrs={'import_source':'equipment_workbook','raw_row':row,'accuracy_penalty':'0','ergonomics_penalty':'44682',
        'charisma_bonus':'1.5','movement_penalty':1,'manual_note':'Сохранить'}
    hat=ItemTemplate(name='Ушанка',category='helmet',item_class='1000',price=0,volume=0,attributes=attrs)
    custom=ItemTemplate(name='Балаклава',category='helmet',item_class='150',price=999,volume=0,attributes={**attrs,'raw_row':{**row,'N':'150','L':'.5'}})
    db.session.add_all([hat,custom]);db.session.flush();identity=hat.id
    path=Path(__file__).resolve().parents[1]/'migrations/versions/b17f4a9c2d60_correct_shifted_helmet_columns.py'
    spec=importlib.util.spec_from_file_location('helmet_columns_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    monkeypatch.setattr(migration.op,'get_bind',lambda:db.session.connection())
    migration.upgrade();migration.upgrade();db.session.expire_all()
    assert (hat.id,hat.item_class,hat.price,hat.volume)==(identity,'1',1000,1)
    assert hat.attributes['ergonomics_penalty']=='0' and hat.attributes['manual_note']=='Сохранить'
    assert custom.price==999
    from pathlib import Path
    from app.services.equipment_importer import parse_equipment_templates
    path = Path(__file__).resolve().parents[1] / '.private-testing' / 'Снаряжение (4).xlsx'
    if not path.exists():
        pytest.skip('Локальная исходная книга недоступна')
    entries = parse_equipment_templates(path)
    lookup = {(item['category'], item['name']): item for item in entries}
    assert lookup['backpack', 'БФРР']['attributes']['limit'] == 100
    assert lookup['backpack', 'БФРР']['attributes']['weight_reduction'] == 3
    case = lookup['container', 'Патронный-М2У']
    assert case['volume'] == 15
    assert case['attributes']['capacity'] == 30
    assert case['attributes']['access_action_points'] == 2
    assert lookup['crafting_material', 'Пустая ампула']['volume'] == .2
    assert lookup['glasses', 'Противоосколочные очки СтекИКо']['attributes']['eye_physical_protection'] == 5
    assert lookup['headphones', 'ТактикКiт']['attributes']['noise_absorption'] == 10
    assert len([item for item in entries if item['category'] == 'armor_plate']) == 8
    assert len([item for item in entries if item['category'] == 'vest']) == 25
    assert len([item for item in entries if item['category'] == 'pouch']) == 22
    assert not any(item['category'] == 'ammo' and item['name'].startswith('Подствольный') for item in entries)
    assert all(item['attributes']['compatible_weapon_names'] for item in entries if item['category'] == 'magazine')


def test_full_catalog_migration_preserves_existing_items_and_resolves_magazines(app, monkeypatch):
    import importlib.util
    from pathlib import Path
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.equipment_catalog import load_catalog
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/a0e6b8243d15_complete_equipment_catalog.py'
    spec = importlib.util.spec_from_file_location('equipment_catalog_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    previous = ItemTemplate(name='Пояс', category='belt', price=750, attributes={'user_configured': True})
    db.session.add(previous)
    db.session.flush()
    monkeypatch.setattr(migration.op, 'get_bind', lambda: db.session.connection())
    migration.upgrade()
    migration.upgrade()
    db.session.expire_all()
    assert ItemTemplate.query.count() == len(load_catalog())
    assert previous.price == 750 and previous.attributes == {'user_configured': True}
    magazine = ItemTemplate.query.filter_by(category='magazine').filter(ItemTemplate.compatible_ids != []).first()
    assert magazine is not None and magazine.compatible_ids
    assert all(db.session.get(ItemTemplate, id).category == 'weapon' for id in magazine.compatible_ids)


def test_starting_purchase_creates_usable_cases_with_independent_contents():
    from types import SimpleNamespace
    from app.services.character_starting_purchase import item_from_template
    case = SimpleNamespace(id=234, name='Медицинский-Л', category='container', subcategory='Кейсы',
        item_class=None, description='', price=1000, weight=.25, volume=5, compatible_ids=[],
        attributes={'capacity': 15, 'allowed_categories': ['med']})
    first, second = item_from_template(case), item_from_template(case)
    assert first['isContainer'] and first['capacity'] == 15 and first['volume'] == 5
    first['contents'].append({'name': 'Бинт'})
    assert second['contents'] == []


def test_catalog_descriptions_are_idempotent_and_do_not_repeat_compatibility():
    from app.services.equipment_catalog import prepare_catalog
    from app.services.catalog_import import template
    raw = template('Магазин', 'magazine', 50, .1, 2, 'Пистолет А, Пистолет Б',
        attributes={'compatible_weapon_names':['Пистолет А', 'Пистолет Б']})
    result = prepare_catalog([raw])
    assert result[0]['description'] == 'Совместимость: Пистолет А, Пистолет Б.'
    assert prepare_catalog(result) == result
    cartridge = template('12.7x55', 'ammo', 250, .25, .02,
        'При полном пробитии одна Дополнительная трамва')
    result = prepare_catalog([cartridge])[0]
    assert 'две проверки' in result['description']
    assert 'одна Дополнительная' not in result['description']
    assert result['attributes']['source_description'] == cartridge['description']


def test_published_equipment_matches_current_book_and_accepted_builtin_rules():
    import pytest
    from pathlib import Path
    from app.services.equipment_catalog import load_catalog, parse_catalog
    source = Path(__file__).resolve().parents[1] / '.private-testing/Снаряжение (4).xlsx'
    if not source.exists():
        pytest.skip('Книга приватна и не включена в репозиторий')
    assert parse_catalog(source) == load_catalog()


def test_water_variants_have_author_effects_without_price_or_trade(app, monkeypatch):
    import importlib.util
    from pathlib import Path
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.equipment_catalog import WATER_VARIANTS, load_catalog
    from app.services.npc_trade import price_for
    from app.services.exceptions import ValidationError
    from app.services.general_consumable import _apply_profile
    import pytest

    catalog = {item['name']: item for item in load_catalog()}
    for name, radiation, exhaustion in WATER_VARIANTS:
        item = catalog[name]
        attrs = item['attributes']
        direct = attrs['consumable']['direct']
        assert (direct['radiation_delta'], direct['exhaustion_delta']) == (radiation, exhaustion)
        assert direct['water_source'] and direct['uses'] == 3
        assert item['price'] == 0 and attrs['not_for_sale']
        assert 'price' in attrs['unspecified_fields']
        assert {(effect['type'], effect['value']) for effect in attrs['consumable']['effects']
                if effect['type'] in {'radiation', 'exhaustion'}} == {
                    ('radiation', radiation), ('exhaustion', exhaustion)}
        health = {'radiation': 0, 'exhaustion': 0}
        _apply_profile(health, attrs['consumable'], direct, item, in_combat=False)
        assert (health['radiation'], health['exhaustion']) == (radiation, exhaustion)

    path = Path(__file__).resolve().parents[1] / 'migrations/versions/c28f5b0e1a71_add_water_variants.py'
    spec = importlib.util.spec_from_file_location('water_variants_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration.op, 'get_bind', lambda: db.session.connection())
    migration.upgrade()
    migration.upgrade()
    assert ItemTemplate.query.filter(ItemTemplate.name.in_([row[0] for row in WATER_VARIANTS])).count() == 3
    template = ItemTemplate.query.filter_by(name='Болотная вода').one()
    from app.services.crafting import _template
    assert _template('Радиоактивная вода').name == 'Радиоактивная вода'
    with pytest.raises(ValidationError, match='нельзя продать'):
        price_for({'merchant': {'prices': {str(template.id): {'buy': 10, 'sell': 20}}}},
                  {'templateId': template.id, 'attributes': template.attributes}, 'buy')


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
