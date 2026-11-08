from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.combat import CombatService
from app.services.consumable_effects import parse_consumable_effects, with_darkness_modifiers
from app.services.effects import advance_timed_effects
from app.services.general_consumable import _apply_profile
from app.services.lighting_rules import location_is_dark, world_is_dark


OWL = (
    'Стимулятор Сова. Ампула. '
    '+3 на броски внимательности в темноте. -3 к броскам внимательности на свету. Длится 4 часа'
)
OWL_N = (
    'Стимулятор Сова-Н. Ампула. '
    '+5 на броски внимательности в темноте. Длится 8 часов'
)


@pytest.mark.parametrize('minute,dark', [
    (21 * 60 + 59, False), (22 * 60, True),
    (5 * 60 + 59, True), (6 * 60, False),
])
def test_world_darkness_boundaries(minute, dark):
    assert world_is_dark(minute) is dark


def test_owl_rules_are_reusable_modifiers_and_refresh_on_repeat():
    profile = parse_consumable_effects(OWL)
    modifiers = {entry['stat']: entry for entry in profile['modifiers']}
    assert modifiers['darkness_awareness']['value'] == 3
    assert modifiers['light_awareness']['value'] == -3
    assert all(entry['remaining'] == 240 and entry['tick'] == 'time_elapsed' for entry in modifiers.values())
    assert len(with_darkness_modifiers(profile, OWL)['modifiers']) == len(profile['modifiers'])

    health = {'current': 100, 'max': 100, 'effects': []}
    item = {'name': 'Стимулятор Сова'}
    assert _apply_profile(health, profile, {}, item, in_combat=False)
    advance_timed_effects(health, [], 3 * 3600)
    assert health['combatMeta']['consumableModifiers'][0]['remaining_seconds'] == 3600
    assert _apply_profile(health, profile, {}, item, in_combat=False)
    assert len(health['combatMeta']['consumableModifiers']) == 3
    assert {entry['stat'] for entry in health['combatMeta']['consumableModifiers']} == {
        'darkness_awareness', 'darkness_accuracy', 'light_awareness',
    }
    assert all(entry['remaining_seconds'] == 4 * 3600 for entry in health['combatMeta']['consumableModifiers'])


def test_owl_n_applies_five_only_in_darkness_and_preserves_other_penalties(monkeypatch):
    profile = parse_consumable_effects(OWL_N)
    assert {item['stat'] for item in profile['modifiers']} == {
        'darkness_awareness', 'darkness_accuracy',
    }
    assert all(item['remaining'] == 480 for item in profile['modifiers'])
    health = {'current': 100, 'max': 100, 'effects': []}
    assert _apply_profile(health, profile, {}, {'name': 'Стимулятор Сова-Н'}, in_combat=False)
    data = {'health': health, 'skills': {'physical': {'awareness': {'base': 10, 'bonus': 0}}}}
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 10)
    dark = CombatService._narrative_skill_check(data, 'skills.physical.awareness.visual', darkness=True)
    light = CombatService._narrative_skill_check(data, 'skills.physical.awareness.visual', darkness=False)
    assert dark['total'] == 10 and not dark['disadvantage']
    assert light['total'] == 10
    assert not CombatService._has_roll_disadvantage(data, 'skills.physical.shooting', darkness=True)
    wounded = deepcopy(data)
    wounded['health']['effects'].append({
        'type': 'test', 'active': True, 'disadvantage_skills': ['skills.physical.shooting'],
    })
    assert CombatService._has_roll_disadvantage(wounded, 'skills.physical.shooting', darkness=True)
    advance_timed_effects(health, [], 8 * 3600)
    assert not health['combatMeta']['consumableModifiers']


def test_location_darkness_uses_lamps_and_open_portals():
    wall = SimpleNamespace(type='wall', tile_x=2, tile_y=2, properties={
        'dimensions': {'width': 3, 'depth': 0.2, 'height': 2.4},
    })
    door = SimpleNamespace(type='door', tile_x=2, tile_y=2, properties={
        'dimensions': {'width': 0.9, 'depth': 0.18, 'height': 2}, 'is_open': False,
    })
    settings = {'mode': 'world', 'dark_tiles': [{'x': 2, 'z': 3}]}
    assert location_is_dark(settings, 2, 3, 12 * 60, [wall, door])
    door.properties['is_open'] = True
    assert not location_is_dark(settings, 2, 3, 12 * 60, [wall, door])
    assert location_is_dark(settings, 2, 3, 23 * 60, [wall, door])
    settings['local_lights'] = [{'x': 2.5, 'z': 3.5, 'intensity': 3, 'distance': 5}]
    assert not location_is_dark(settings, 2, 3, 23 * 60, [wall, door])
    assert location_is_dark({'mode': 'world'}, 2, 3, 23 * 60)
    assert not location_is_dark({'mode': 'world'}, 2, 3, 12 * 60)
