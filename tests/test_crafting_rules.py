"""Границы однозначных правил; бросок создания намеренно не выдумывается."""
from pathlib import Path
import json

import pytest

from app.services.crafting_rules import creation_quote, modification_quote, skill_profile, catalog
from app.services.exceptions import ValidationError


@pytest.mark.parametrize('level,difficulty,automatic', [(20, 39, True), (20, 40, False),
    (19, 24, True), (19, 25, False), (17, 24, True), (16, 1, False)])
def test_crafting_auto_success_has_strict_original_difficulty_boundary(level, difficulty, automatic):
    assert creation_quote(level, difficulty)['automatic_success'] is automatic


def test_reducing_difficulty_does_not_create_auto_success_and_low_skill_blocks_recipe_auto():
    result = creation_quote(19, 50, difficulty_modifier=-40)
    assert result['difficulty'] == -15
    assert not result['automatic_success']
    assert not creation_quote(4, 0, base_automatic=True)['automatic_success']
    assert creation_quote(5, 0, base_automatic=True)['automatic_success']


@pytest.mark.parametrize('level,modifier,random_loss,chosen_loss,all_loss', [
    (20,-30,0,0,False), (17,-25,0,1,False), (15,-20,1,0,False),
    (13,-10,1,1,False), (10,0,2,0,False), (8,10,3,1,False),
    (5,20,5,0,False), (2,30,0,0,True), (1,50,0,0,True)])
def test_failure_components_follow_all_skill_bands(level, modifier, random_loss, chosen_loss, all_loss):
    result = skill_profile(level)
    assert result['difficulty_modifier'] == modifier
    assert result['failure_loss'] == {'random':random_loss, 'chosen':chosen_loss, 'all':all_loss}


def test_modification_cost_time_tool_requirements_and_armor_material_discount():
    weapon = modification_quote('weapon', 2, 3, 17, double_charges=True, material_value=5000, workbench=True)
    assert weapon['difficulty'] == 25  # Общая скидка Инженерии также действует.
    assert weapon['professional_price'] == 30000
    assert weapon['tool_uses'] == 20 and weapon['duration_seconds'] == 1800
    assert weapon['required_engineering'] == 13 and weapon['skill_sufficient']
    armor = modification_quote('armor', 2, 2, 13, double_charges=True, material_value=299)
    assert armor['difficulty'] == 39
    assert armor['required_engineering'] == 14 and not armor['skill_sufficient']
    assert armor['tool_grade'] == 'standard'


@pytest.mark.parametrize('value', [0, -1, True, float('nan'), float('inf'), '17'])
def test_invalid_skill_cannot_reach_quote(value):
    with pytest.raises(ValidationError):
        creation_quote(value, 50)


def test_unknown_class_price_is_not_invented():
    assert modification_quote('weapon',2,'Н',17)['professional_price']==40000
    assert modification_quote('armor',3,'Экзоскелет',20)['professional_price']==90000
    with pytest.raises(ValidationError):
        modification_quote('weapon', 2, 'неизвестный', 17)


def test_catalog_contains_all_variants_and_preserves_recipe_choices():
    data = catalog()
    weapon = [item for item in data['modifications'] if item['kind'] == 'weapon']
    assert len(weapon) == 42
    assert len({item['family'] for item in weapon}) == 7
    pda = [item for item in data['modifications'] if item['kind'] == 'pda']
    assert len(pda) == 25 and all('материалы' not in item['name'].casefold() for item in pda)
    assert all(item.get('level', 1) <= 3 for item in data['modifications'])
    assert not any(item['description'] == 'Эффект' for item in data['modifications'])
    recipe = next(item for item in data['recipes'] if item['id'] == 'recipe-f4')
    assert recipe['output_name'] == 'Фонарик'
    assert recipe['duration_seconds'] == 1200
    assert recipe['difficulty_options'] == [25]
    assert next(item for item in data['recipes'] if item['id'] == 'recipe-aa15')['difficulty_options'] == [45,35,35]
    penicillin = next(item for item in data['recipes'] if item['id'] == 'recipe-aa24')
    assert penicillin['output_count'] == 10 and penicillin['duration_seconds'] == 86400
    assert penicillin['active_seconds'] == 600
    assert {item['name']:item['price'] for item in data['workbenches']}['Станок Электрика'] == 80000


def test_ingredient_uses_and_numeric_table_rows_are_not_false_recipes():
    data=catalog()
    ids={item['id'] for item in data['recipes']}
    assert not ids.intersection({f'recipe-v{row}' for row in range(51,56)})
    mold=next(item for item in data['recipes'] if item['id']=='recipe-v38')
    assert mold['components']==[{'name':'Металлические детали','quantity':3,
        'source_text':'Металлические детали. 3 Использования'}]
    lockpick=next(item for item in data['recipes'] if item['id']=='recipe-m27')
    assert lockpick['output_name']=='Отмычка' and lockpick['output_count']==2


def test_integer_fuel_liters_use_one_liter_catalog_units():
    data=catalog()
    for recipe_id,quantity in [('recipe-v12',3),('recipe-v13',1)]:
        recipe=next(item for item in data['recipes'] if item['id']==recipe_id)
        fuel=next(item for item in recipe['components'] if item['name']=='Топливо')
        assert fuel['quantity']==quantity
    fractional=next(item for item in data['recipes'] if item['id']=='recipe-v11')
    assert any('0.5л' in item['name'] for item in fractional['components'])


def test_molotov_inline_station_override_is_not_an_ingredient():
    recipe=next(item for item in catalog()['recipes'] if item['id']=='recipe-v11')
    assert recipe['tools_text']=='Не нужен'
    assert any(item['name']=='Тканные материалы' for item in recipe['components'])
    assert not any('Станок' in item['name'] for item in recipe['components'])


def test_published_creation_data_matches_unchanged_source_when_available():
    from app.services.crafting_catalog import parse_creation_catalog
    root = Path(__file__).resolve().parents[1]
    source = root / '.private-testing/Создание.xlsx'
    if not source.exists():
        pytest.skip('Исходная книга приватна и не входит в репозиторий')
    assert parse_creation_catalog(source) == json.loads((root / 'app/data/creation_rules.json').read_text(encoding='utf-8'))
