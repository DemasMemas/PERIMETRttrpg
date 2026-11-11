"""Независимые числовые ожидания для обеих колонок всех артефактов каталога."""

from copy import deepcopy

import pytest

from app.services.artifact_effects import artifact_passive_profile
from app.services.combat import CombatService
from app.services.effects import advance_timed_effects
from app.services.world_rules import artifact_catalog
from app.extensions import db
from test_grenade_rules import grenade_game


# Ожидания выписаны из правил, без вызова парсера для их построения.
# Реакции на урон и активируемые свойства проверяются отдельными сценариями.
PASSIVES = [
    ('Щепка сознания', {'skill_awareness': 2}, {'strenuous_action_cost': 1}),
    ('Эхо чужой мысли', {'accuracy': 1}, {'protection.psi': -10, 'strenuous_action_cost': 1}),
    ('Пси-щиток', {'protection.psi': 5}, {'strenuous_action_cost': 1}),
    ('Ржавый фрагмент', {'protection.physical': 5}, {'radiation_per_movement': 1}),
    ('Пепельный осколок', {'radiation_per_movement': -1}, {'health_per_movement': -25}),
    ('Светящийся обрубок', {}, {'radiation_per_movement': 1}),
    ('Искровая крошка', {'protection.electric': 5}, {'health_per_movement': -25}),
    ('Искровая жилка', {'protection.electric': 5}, {'accuracy': -1}),
    ('Металлическая стружка', {'protection.physical': 5},
     {'protection.electric': -5, 'blocked_devices': ['compass']}),
    ('Сухая сажа', {'protection.thermal': 5}, {'breath_duration_bonus': 1}),
    ('Копоть стекла', {'protection.thermal': 5}, {'strenuous_action_cost': 1}),
    ('Обугленный след', {'protection.thermal': 5}, {'skill_stealth': -2}),
    ('Ядовитое стекло', {'protection.chemical': 5, 'health_per_movement': 25}, {'radiation_per_movement': 1}),
    ('Окаменелая пленка', {'armor_damage_reduction': 10}, {'movement_penalty': 1}),
    ('Сопля собаки', {'protection.chemical': 5}, {'health_per_movement': -25}),
    ('Осколок притяжения', {'protection.physical': 5}, {'movement_penalty': 1, 'radiation_per_movement': 1}),
    ('Осколок тяготения', {'melee_damage_percent': 10}, {'bleeding_severity': 1, 'incoming_physical_percent': 5}),
    ('Капля горизонта', {'throw_range_bonus': 2, 'skill_strength': 2},
     {'protection.electric': -5, 'radiation_per_movement': 1}),
    ('Отголосок', {'skill_other': 2, 'skill_physical': 2}, {'pain_per_movement': 1, 'bleeding_severity': 1}),
    ('Шепот', {'accuracy': 2}, {'protection.psi': -20, 'strenuous_action_cost': 1}),
    ('Ментальная преграда', {'protection.psi': 10, 'skill_will': 2}, {'pain_per_movement': 1}),
    ('Костяная искра', {'protection.physical': 10, 'bleeding_severity': -1}, {'radiation_per_movement': 3}),
    ('Глотатель пыли', {'radiation_per_movement': -2}, {'health_per_movement': -25, 'bleeding_severity': 1}),
    ('Жар-пыль', {}, {'radiation_per_movement': 2}),
    ('Кристалл статики', {'protection.electric': 15}, {'health_per_movement': -50}),
    ('Жгут плазмы', {'protection.electric': 10}, {'strenuous_action_cost': 1}),
    ('Полярный осколок', {'protection.physical': 10},
     {'protection.electric': -25, 'blocked_devices': ['compass', 'radio']}),
    ('Угольная печать', {'protection.thermal': 15}, {'healing_multiplier': .75, 'breath_duration_bonus': 1}),
    ('Искра стекла', {'protection.thermal': 10}, {'strenuous_action_cost': 1}),
    ('Жаровая спираль', {'protection.thermal': 10}, {'skill_stealth': -3}),
    ('Зеркало алхимика', {'protection.chemical': 10, 'health_per_movement': 25}, {'radiation_per_movement': 2}),
    ('Живой морфий', {'health_per_movement': 25, 'pain_per_movement': -1}, {'all_protection': -10}),
    ('Кислотный панцирь', {'armor_damage_reduction': 20}, {'movement_penalty': 1}),
    ('Желчный пузырь', {'protection.chemical': 10}, {'health_per_movement': -25}),
    ('Кость притяжения', {'protection.physical': 10}, {'movement_penalty': 2, 'radiation_per_movement': 2}),
    ('Камень тяготения', {'melee_damage_percent': 20}, {'bleeding_severity': 2, 'incoming_physical_percent': 10}),
    ('Слеза горизонта', {'throw_range_bonus': 4, 'skill_strength': 4},
     {'protection.electric': -5, 'radiation_per_movement': 2}),
    ('Резонанс разума', {'skill_other': 3, 'skill_physical': 3}, {'pain_per_movement': 2, 'bleeding_severity': 1}),
    ('Отражение разума', {'accuracy': 2, 'skill_physical': 2},
     {'protection.psi': -30, 'strenuous_action_cost': 1, 'breath_duration_bonus': 1}),
    ('Психический купол', {'protection.psi': 15, 'skill_will': 4}, {'movement_penalty': 1}),
    ('Мох', {'protection.thermal': 15, 'protection.radiation': 15}, {'radiation_per_movement': 3}),
    ('Жгутник', {'radiation_per_movement': -4}, {'health_per_movement': -50, 'bleeding_severity': 2}),
    ('Клеймо чернобыля', {}, {'radiation_per_movement': 3}),
    ('Штормовой камень', {'protection.electric': 20}, {'health_per_movement': -50}),
    ('Нервный катализатор', {'protection.electric': 15}, {'accuracy': -2, 'radiation_per_movement': 1}),
    ('Ферромагнит', {'protection.physical': 20},
     {'protection.electric': -25, 'blocked_devices': ['compass', 'radio', 'detector']}),
    ('Генератор', {'movement_penalty': -1}, {'protection.electric': -20}),
    ('Пепельная роза', {'protection.thermal': 20}, {'healing_multiplier': .75, 'bleeding_severity': 2}),
    ('Огненная слеза', {'protection.thermal': 15}, {'movement_penalty': 1, 'strenuous_action_cost': 1}),
    ('Пламень крови', {'protection.thermal': 15}, {'incoming_thermal_percent': 10}),
    ('Токсичный светильник', {'protection.chemical': 15, 'health_per_movement': 50}, {'radiation_per_movement': 3}),
    ('Родник жизни', {'health_per_movement': 50, 'pain_per_movement': -2},
     {'all_protection': -10, 'strenuous_action_cost': 1}),
    ('Кристалл гидры', {'armor_damage_reduction': 35}, {'movement_penalty': 2}),
    ('Отросток мутанта', {'protection.chemical': 15}, {'health_per_movement': -50}),
    ('Ребро притяжения', {'protection.physical': 15}, {'movement_penalty': 3, 'radiation_per_movement': 3}),
    ('Наконечник тяготения', {'melee_damage_percent': 30}, {'bleeding_severity': 3, 'incoming_physical_percent': 15}),
    ('Жемчужина горизонта', {'throw_range_bonus': 8, 'skill_strength': 6},
     {'protection.electric': -10, 'radiation_per_movement': 3}),
    ('Ядро мысли', {'skill_other': 5, 'skill_physical': 3}, {'pain_per_movement': 2, 'health_per_movement': -25}),
    ('Тень чужого', {'accuracy': 2, 'skill_physical': 2}, {'protection.psi': -50}),
    ('Ментальный бастион', {'protection.psi': 25, 'skill_will': 6}, {'movement_penalty': 2}),
    ('Ядро твари', {'health_per_movement': 50, 'protection.radiation': 10}, {'radiation_per_movement': 5}),
    ('Пиявка', {'radiation_per_movement': -8}, {'movement_penalty': 1, 'healing_multiplier': .75}),
    ('Хозяин свечения', {}, {'radiation_per_movement': 5}),
    ('Громоотвод', {'protection.electric': 25}, {'blocked_devices': ['electronics']}),
    ('Живой ток', {'protection.electric': 20}, {'strenuous_action_cost': 1, 'radiation_per_movement': 2}),
    ('Железное сердце', {'protection.physical': 30}, {'blocked_devices': ['electronics']}),
    ('Горелка', {'movement_penalty': -2}, {'protection.thermal': -25, 'radiation_per_movement': 2}),
    ('Сердце жара', {'protection.thermal': 25}, {'protection.physical': -5}),
    ('Пылающий осколок', {'protection.thermal': 20}, {}),
    ('Огненная вена', {'protection.thermal': 20}, {'healing_multiplier': .5}),
    ('Алхимический веер', {'protection.chemical': 20, 'health_per_movement': 50}, {'radiation_per_movement': 4}),
    ('Вторая кожа', {'health_per_movement': 25, 'pain_per_movement': -3, 'pain_relief_per_round': 1},
     {'all_protection': -15, 'radiation_per_movement': 2}),
    ('Панцирь', {'armor_damage_reduction': 50}, {'movement_penalty': 2}),
    ('Гнилая железа', {'protection.chemical': 20}, {'health_per_movement': -75}),
    ('Сфера притяжения', {'protection.physical': 20}, {'movement_penalty': 4, 'radiation_per_movement': 4}),
    ('Ядро тяготения', {'melee_damage_percent': 40}, {'bleeding_severity': 4, 'incoming_physical_percent': 20}),
    ('Глаз горизонта', {'throw_range_bonus': 12, 'skill_strength': 8},
     {'protection.electric': -10, 'radiation_per_movement': 4}),
    ('Сердце Эха', {'skill_other': 8, 'skill_physical': 4}, {'pain_per_movement': 3, 'health_per_movement': -50}),
    ('Лик чужого', {'accuracy': 3, 'skill_physical': 3}, {'protection.psi': -100}),
    ('Щит сознания', {'protection.psi': 40, 'skill_will': 8}, {'movement_penalty': 3}),
    ('Звезда гнили', {'health_per_movement': 200, 'protection.radiation': 20}, {'radiation_per_movement': 8}),
    ('Чернобыльская губка', {}, {'movement_penalty': 2, 'healing_multiplier': .5}),
    ('Реактор', {}, {'radiation_per_movement': 8}),
    ('Проводник бури', {'protection.electric': 30}, {'blocked_devices': ['electronics'], 'skill_stealth': -6}),
    ('Сердце грозы', {'protection.electric': 25}, {'radiation_per_movement': 3}),
    ('Полюс', {'protection.electric': 35}, {'blocked_devices': ['electronics']}),
    ('Ядро пепелища', {'protection.thermal': 30}, {'health_per_movement': -75}),
    ('Ядро возгорания', {'protection.thermal': 25}, {'skill_stealth': -10}),
    ('Дар пламени', {'protection.thermal': 25}, {}),
    ('Философский лист', {'protection.chemical': 25, 'health_per_movement': 100}, {'radiation_per_movement': 5}),
    ('Живой щит', {'armor_damage_reduction': 75}, {'movement_penalty': 3}),
    ('Печенка', {'protection.chemical': 25}, {'health_per_movement': -100}),
    ('Сердце притяжения', {'protection.physical': 25}, {'movement_penalty': 5, 'radiation_per_movement': 5}),
    ('Тяжесть', {'melee_damage_percent': 50}, {'bleeding_severity': 5, 'incoming_physical_percent': 25}),
    ('Свет горизонта', {'throw_range_bonus': 15, 'skill_strength': 10},
     {'protection.electric': -15, 'radiation_per_movement': 5}),
]

RULES = {rule['name']: rule for rule in artifact_catalog()}


def character_with_artifact(name, column=None, radiation=0):
    attributes = deepcopy(RULES[name])
    if column:
        other_column = 'negative_effect' if column == 'positive_effect' else 'positive_effect'
        attributes[other_column] = ''
    item = {'id': 'catalog-passive', 'name': name, 'category': 'artifact', 'attributes': attributes}
    return {
        'health': {'current': 350, 'max': 700, 'radiation': radiation, 'effects': []},
        'equipment': {'armor': {'containers': [{'item': item}]}},
    }


def expected_profile(name, values):
    expected = artifact_passive_profile({})
    for key, value in values.items():
        if key == 'all_protection':
            expected['protection'] = dict.fromkeys(expected['protection'], value)
        elif key == 'blocked_devices':
            expected[key] = {kind: [name] for kind in value}
        elif key.startswith('protection.'):
            expected['protection'][key.removeprefix('protection.')] = value
        else:
            expected[key] = value
    return expected


def test_every_catalog_artifact_has_independent_expectations():
    names = [row[0] for row in PASSIVES]
    assert len(names) == len(set(names)) == 95
    assert set(names) == set(RULES)


@pytest.mark.parametrize('name,positive,negative', PASSIVES, ids=[row[0] for row in PASSIVES])
@pytest.mark.parametrize('column', ['positive_effect', 'negative_effect'])
def test_each_catalog_passive_column(name, positive, negative, column):
    data = character_with_artifact(name, column)
    values = positive if column == 'positive_effect' else negative
    assert artifact_passive_profile(data) == expected_profile(name, values)


@pytest.mark.parametrize('name,positive,negative', PASSIVES, ids=[row[0] for row in PASSIVES])
def test_catalog_artifact_in_backpack_has_no_passive_influence(name, positive, negative):
    data = character_with_artifact(name)
    item = data['equipment']['armor']['containers'].pop()['item']
    data['inventory'] = {'backpack': [item]}
    assert artifact_passive_profile(data) == artifact_passive_profile({})


@pytest.mark.parametrize('name,positive,negative', PASSIVES, ids=[row[0] for row in PASSIVES])
def test_catalog_physical_modifier_changes_real_armor_calculation(name, positive, negative):
    data = character_with_artifact(name)
    data['equipment']['armor'].update({
        'name': 'Проверочная броня', 'protection': {'physical': 50},
        'durability': 100, 'maxDurability': 100,
    })
    modifier = positive.get('protection.physical', positive.get('all_protection', 0))
    modifier += negative.get('protection.physical', negative.get('all_protection', 0))
    protection, _ = CombatService._target_armor(data, 'chest')
    assert protection == 50 + modifier


@pytest.mark.parametrize('name,positive,negative', PASSIVES, ids=[row[0] for row in PASSIVES])
def test_catalog_artifacts_do_not_change_mechanical_limb_protection(name, positive, negative):
    data = character_with_artifact(name)
    data['equipment']['armor'].update({
        'name': 'Проверочная броня', 'protection': {'physical': 50},
        'durability': 100, 'maxDurability': 100,
    })
    protection, _ = CombatService._target_armor(
        data, 'left_arm', limb_hit={'mechanical': True, 'protection': 10},
    )
    assert protection == 50


@pytest.mark.parametrize('name,positive,negative', PASSIVES, ids=[row[0] for row in PASSIVES])
def test_catalog_skill_modifiers_reach_actual_skill_calculation(name, positive, negative):
    data = character_with_artifact(name)
    paths = {
        'physical.will': 'skill_will',
        'physical.awareness': 'skill_awareness',
        'physical.agility': None,
        'other.stealth': 'skill_stealth',
        'other.medicine': None,
        'social.barter': None,
    }
    data['skills'] = {'physical': {}, 'other': {}, 'social': {}}
    for path, specific in paths.items():
        group, skill = path.split('.')
        data['skills'][group][skill] = {'base': 10, 'bonus': 0}
        field = 'skill_physical' if group == 'physical' else 'skill_other'
        expected = 10+positive.get(field, 0)+negative.get(field, 0)
        expected += positive.get(specific, 0)+negative.get(specific, 0)
        assert CombatService._skill_value(data, 'skills.'+path) == max(0, expected)


@pytest.mark.parametrize('name,positive,negative', PASSIVES, ids=[row[0] for row in PASSIVES])
def test_catalog_elemental_modifiers_change_real_protection(name, positive, negative):
    data = character_with_artifact(name)
    kinds = ('chemical', 'thermal', 'electric', 'radiation', 'psi')
    data['equipment']['armor']['protection'] = dict.fromkeys(kinds, 50)
    for kind in kinds:
        modifier = positive.get('protection.'+kind, positive.get('all_protection', 0))
        modifier += negative.get('protection.'+kind, negative.get('all_protection', 0))
        assert CombatService._target_elemental_protection(data, kind) == max(0, 50+modifier)


@pytest.mark.parametrize('name,positive,negative', PASSIVES, ids=[row[0] for row in PASSIVES])
def test_catalog_radiation_modifier_changes_ambient_protection(name, positive, negative):
    data = character_with_artifact(name)
    data['equipment']['armor']['protection'] = {'radiation': 50}
    modifier = positive.get('protection.radiation', positive.get('all_protection', 0))
    modifier += negative.get('protection.radiation', negative.get('all_protection', 0))
    assert CombatService._equipped_radiation_protection(data) == 50+modifier


@pytest.mark.parametrize('name,expected', [
    ('Живой морфий', 6), ('Родник жизни', 6), ('Вторая кожа', 6.5),
])
def test_all_protection_penalty_changes_received_ambient_radiation(name, expected):
    data = character_with_artifact(name)
    data['equipment']['armor']['protection'] = {'radiation': 50}
    result = CombatService._apply_incoming_radiation(data, 10)
    assert result['received'] == expected
    assert data['health']['radiation'] == expected
    data['equipment']['armor']['protection'] = {'radiation': 2}
    assert CombatService._equipped_radiation_protection(data) == 0


@pytest.mark.parametrize('name,protection', [
    ('Живой морфий', 40), ('Родник жизни', 40), ('Сердце жара', 45), ('Вторая кожа', 35),
])
def test_negative_physical_modifier_changes_actual_bullet_penetration(grenade_game, name, protection):
    actor = grenade_game['actor_location']
    target = grenade_game['target_location']
    data = character_with_artifact(name)
    data['equipment']['armor'].update({
        'name': 'Проверочная броня', 'protection': {'physical': 50},
        'durability': 100, 'maxDurability': 100,
    })
    target.character.data = data
    db.session.commit()
    result = CombatService._resolve_attack(
        target, actor,
        {'fire_mode': 'aimed', 'hit_difficulty': 1, 'target_distance': 1, 'round_number': 1},
        forced_roll=10, aimed_zone='chest', ignore_cover=True,
        profile_override={'damage': 100, 'armor_piercing': protection, 'damage_type': 'bullet'},
    )
    assert result['armor'] == protection
    assert result['damage'] == 100
    assert target.character.data['health']['current'] == 250


@pytest.mark.parametrize('name', ['Живой морфий', 'Родник жизни', 'Сердце жара', 'Вторая кожа'])
def test_negative_physical_modifier_cannot_lower_protection_below_zero(name):
    data = character_with_artifact(name)
    data['equipment']['armor']['protection'] = {'physical': 2}
    assert CombatService._target_armor(data, 'chest')[0] == 0


@pytest.mark.parametrize('name,positive,negative', PASSIVES, ids=[row[0] for row in PASSIVES])
def test_catalog_ten_minute_properties_wait_for_time_then_apply_both_columns(name, positive, negative):
    data = character_with_artifact(name)
    health = data['health']
    health['painLevel'] = 0
    health['effects'] = advance_timed_effects(health, [], 599, character_data=data)
    assert (health['current'], health['radiation'], health['painLevel']) == (350, 0, 0)
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    expected_health = 350+positive.get('health_per_movement', 0)+negative.get('health_per_movement', 0)
    expected_radiation = max(0, positive.get('radiation_per_movement', 0)+negative.get('radiation_per_movement', 0))
    expected_pain = max(0, positive.get('pain_per_movement', 0)+negative.get('pain_per_movement', 0))
    assert (health['current'], health['radiation'], health['painLevel']) == (
        expected_health, expected_radiation, expected_pain,
    )


@pytest.mark.parametrize('name,threshold,values', [
    ('Светящийся обрубок', 5, {'movement_penalty': -1}),
    ('Жар-пыль', 10, {'movement_penalty': -1, 'protection.physical': 5}),
    ('Клеймо чернобыля', 20, {'movement_penalty': -2, 'accuracy': 1}),
    ('Хозяин свечения', 30, {'movement_penalty': -2, 'all_protection': 10}),
    ('Реактор', 40, {'movement_penalty': -2, 'all_protection': 20}),
])
def test_conditional_catalog_properties_activate_at_exact_radiation_threshold(name, threshold, values):
    data = character_with_artifact(name, 'positive_effect', threshold-1)
    assert artifact_passive_profile(data) == expected_profile(name, {})
    data['health']['radiation'] = threshold
    assert artifact_passive_profile(data) == expected_profile(name, values)
    data['health']['radiation'] = threshold-1
    assert artifact_passive_profile(data) == expected_profile(name, {})
