"""N+: граница площади аномалии после сохранения; до допуска NOT_RUN.

Точный оракул использует известные интервалы прямоугольника при 90 градусах,
а не алгоритм поворота/отсечения из приложения. Все ORM-действия предназначены
только для guarded memory app; в этом пакете модуль не исполняется.
"""
from copy import deepcopy
from fractions import Fraction
from math import pi
from types import SimpleNamespace

import pytest

import app.services.combat as combat_module
from app.extensions import db
from app.models import LobbyCharacter, Location, LocationCharacter, LocationObject
from app.services.anomaly_area import anomaly_covers_tile
from app.services.combat import CombatService
from test_combat_anomalies import _combat_with_anomaly


PERCENTAGES = [pytest.param(74, id='below74'), pytest.param(75, id='equal75'),
               pytest.param(76, id='above76')]
STORAGES = ['tiles', 'object']
FOREIGN_EFFECT = {
    'id': 'nplus-foreign-blindness', 'type': 'blindness',
    'name': 'Посторонняя слепота', 'source': 'nplus-unrelated',
    'value': 10, 'tick': 'manual', 'active': True,
}
OWN_EFFECT = {
    'id': 'nplus-own-blindness', 'type': 'blindness',
    'name': 'Слепота от Тьмы', 'source': 'Тьма',
    'value': 90, 'tick': 'manual', 'active': True,
}


def exact_overlap(percent, tile_x, tile_y):
    """После поворота: x=[2, 7/2], z=[5/2-p/200, 5/2+p/200]."""
    half_width = Fraction(percent, 200)
    overlap_x = max(Fraction(0), min(Fraction(7, 2), tile_x + 1)
                    - max(Fraction(2), tile_x))
    overlap_z = max(Fraction(0), min(Fraction(5, 2) + half_width, tile_y + 1)
                    - max(Fraction(5, 2) - half_width, tile_y))
    return overlap_x * overlap_z


def geometry_payload(storage, percent, key):
    dimensions = {'width': percent / 100, 'depth': 1.5, 'height': 1.0}
    if storage == 'tiles':
        return {'type': 'anomaly', 'anomalyKey': key, 'dimensions': dimensions,
                'rotation': 90, 'x': 0.25, 'z': 0.0}
    return {'anomaly_key': key, 'dimensions': dimensions,
            'rotation': pi / 2, 'offset_x': 0.25, 'offset_z': 0.0}


def prepare_saved_case(storage, percent, key='otboynik', initial_effects=()):
    user, location, actor, placed, _ = _combat_with_anomaly(key)
    # Проверяем геометрию аномалии при ходьбе вперёд, без платы за шаг спиной.
    placed.facing_x,placed.facing_y=1,0
    tiles = deepcopy(location.tiles_data)
    tiles[2][2]['objects'] = []
    payload = geometry_payload(storage, percent, key)
    anomaly = None
    if storage == 'tiles':
        tiles[2][2]['objects'] = [payload]
    else:
        anomaly = LocationObject(location_id=location.id, type='anomaly',
                                 tile_x=2, tile_y=2, properties=payload)
        db.session.add(anomaly)
    location.tiles_data = tiles
    if initial_effects:
        data = deepcopy(actor.data)
        data['health']['effects'] = deepcopy(list(initial_effects))
        actor.data = data
    db.session.flush()
    case = SimpleNamespace(
        storage=storage, percent=percent, key=key, user_id=user.id,
        location_id=location.id, actor_id=actor.id, placed_id=placed.id,
        object_id=anomaly.id if anomaly is not None else None,
        expected_payload=deepcopy(payload), initial_payload=payload,
    )
    db.session.commit()
    db.session.expire_all()
    return case


def reload_saved_geometry(case):
    # expire заставляет ORM заново прочитать JSON из тестовой БД.
    db.session.expire_all()
    location = db.session.get(Location, case.location_id)
    if case.storage == 'tiles':
        objects = location.tiles_data[2][2]['objects']
        assert len(objects) == 1
        obj = payload = objects[0]
    else:
        assert location.tiles_data[2][2]['objects'] == []
        obj = db.session.get(LocationObject, case.object_id)
        assert obj.type == 'anomaly'
        assert obj.location_id == case.location_id
        assert (obj.tile_x, obj.tile_y) == (2, 2)
        payload = obj.properties
    assert payload == case.expected_payload
    assert payload is not case.initial_payload
    assert payload['dimensions'] == {'width': case.percent / 100,
                                     'depth': 1.5, 'height': 1.0}
    assert payload['rotation'] == (90 if case.storage == 'tiles' else pi / 2)
    assert payload.get('x', payload.get('offset_x')) == 0.25
    assert payload.get('z', payload.get('offset_z')) == 0.0
    return location, obj


def reload_actor(case):
    db.session.expire_all()
    return (db.session.get(LobbyCharacter, case.actor_id),
            db.session.get(LocationCharacter, case.placed_id))


def walk_across(case):
    return CombatService.move_character(case.location_id, case.user_id,
                                        case.actor_id, 4, 2, movement_mode='walk')


def assert_entered_profile(case, health):
    active = health['combatMeta']['activeAnomaly']
    name, category, dc = ('Тьма', 'psi', 15) if case.key == 'tma' else ('Отбойник', 'gravity', 14)
    assert active['key'] == case.key
    assert (active['name'], active['category'], active['dc']) == (name, category, dc)
    assert (active['tile_x'], active['tile_y']) == (2, 2)
    assert (active['origin_x'], active['origin_y']) == (2, 2)
    assert active['object_id'] == case.object_id
    assert active['entered_round'] == 1
    assert active['rounds'] == 0
    assert (active['entry_dx'], active['entry_dy']) == (1, 0)


@pytest.mark.parametrize('storage', STORAGES)
@pytest.mark.parametrize('percent', PERCENTAGES)
def test_rotated_offset_rectangle_has_independent_exact_threshold(storage, percent):
    payload = geometry_payload(storage, percent, 'otboynik')
    obj = payload if storage == 'tiles' else SimpleNamespace(properties=payload)
    # При 90 градусах depth идёт вдоль x, width вдоль z; смещение x=1/4.
    assert exact_overlap(percent, 2, 2) == Fraction(percent, 100)
    assert exact_overlap(percent, 3, 2) == Fraction(percent, 200)
    for x in range(1, 5):
        expected = exact_overlap(percent, x, 2) >= Fraction(3, 4)
        assert anomaly_covers_tile(obj, 2, 2, x, 2) is expected
    assert anomaly_covers_tile(obj, 2, 2, 2, 1) is False
    assert anomaly_covers_tile(obj, 2, 2, 2, 3) is False


@pytest.mark.parametrize('storage', STORAGES)
@pytest.mark.parametrize('percent', PERCENTAGES)
def test_saved_geometry_controls_first_trigger_on_real_route(app, storage, percent):
    case = prepare_saved_case(storage, percent)
    location, obj = reload_saved_geometry(case)
    expected_trigger = exact_overlap(percent, 2, 2) >= Fraction(3, 4)
    assert anomaly_covers_tile(obj, 2, 2, 2, 2) is expected_trigger
    matches = CombatService._anomalies_at_tile(location, 2, 2)
    assert [match['key'] for match in matches] == (['otboynik'] if expected_trigger else [])
    assert CombatService._anomalies_at_tile(location, 1, 2) == []
    assert CombatService._anomalies_at_tile(location, 3, 2) == []
    assert CombatService._anomalies_at_tile(location, 4, 2) == []

    moved, cost, snapshot = walk_across(case)
    expected_x = 2 if expected_trigger else 4
    assert (moved.pos_x, moved.pos_y) == (expected_x, 2)
    assert cost == expected_x - 1
    actor, placed = reload_actor(case)
    assert (placed.pos_x, placed.pos_y) == (expected_x, 2)
    assert placed.action_points_current == 5
    assert placed.movement_points_current == 20 - (expected_x - 1)
    health = actor.data['health']
    assert health['current'] == 700
    assert health['effects'] == []
    snapshot_actor = next(row for row in snapshot['characters']
                          if row['location_character_id'] == case.placed_id)
    if expected_trigger:
        assert_entered_profile(case, health)
        assert snapshot_actor['active_anomaly']['key'] == 'otboynik'
    else:
        assert 'activeAnomaly' not in health.get('combatMeta', {})
        assert snapshot_actor['active_anomaly'] is None
    # Движение не должно переписывать сохранённые размеры/поворот/смещение.
    reload_saved_geometry(case)


@pytest.mark.parametrize('storage', STORAGES)
def test_real_entry_then_escape_preserves_foreign_effect_and_spends_three_ap(app, monkeypatch, storage):
    # Оба исходных эффекта — явные условия проверки очистки после выхода.
    # Свой представляет сохранённое прошлое воздействие; его создание не проверяется.
    case = prepare_saved_case(storage, 75, key='tma',
                              initial_effects=(OWN_EFFECT, FOREIGN_EFFECT))
    reload_saved_geometry(case)
    moved, _, _ = walk_across(case)
    assert (moved.pos_x, moved.pos_y) == (2, 2)
    actor, placed = reload_actor(case)
    assert_entered_profile(case, actor.data['health'])
    effects_before = {effect['id']: deepcopy(effect) for effect in actor.data['health']['effects']}
    assert effects_before[OWN_EFFECT['id']]['source'] == 'Тьма'
    assert effects_before[FOREIGN_EFFECT['id']]['source'] == 'nplus-unrelated'
    ap_before = placed.action_points_current
    movement_before = placed.movement_points_current
    hp_before = actor.data['health']['current']
    draws = []

    def successful_d20(low, high):
        assert (low, high) == (1, 20)
        assert draws == []
        draws.append(20)
        return 20

    with monkeypatch.context() as controlled:
        controlled.setattr(combat_module.random, 'randint', successful_d20)
        result = CombatService.perform_action(case.location_id, case.user_id,
                                              case.placed_id, 'escape_anomaly',
                                              target_x=1, target_y=2)
    assert draws == [20]
    assert result['anomaly']['key'] == 'tma'
    assert result['anomaly']['category'] == 'psi'
    assert result['anomaly']['outcome'] == 'success'
    assert result['anomaly']['exits'] is True
    assert result['anomaly']['fraction'] == 0.0
    assert result['anomaly']['check']['roll'] == 20
    assert result['anomaly']['check']['success'] is True
    actor, placed = reload_actor(case)
    assert (placed.pos_x, placed.pos_y) == (1, 2)
    assert placed.action_points_current == ap_before - 3 == 2
    assert placed.movement_points_current == movement_before
    assert actor.data['health']['current'] == hp_before == 700
    assert 'activeAnomaly' not in actor.data['health']['combatMeta']
    effects_after = {effect['id']: effect for effect in actor.data['health']['effects']}
    assert OWN_EFFECT['id'] not in effects_after
    assert all(effect.get('source') != 'Тьма' for effect in effects_after.values())
    assert effects_after[FOREIGN_EFFECT['id']] == effects_before[FOREIGN_EFFECT['id']]
    assert effects_after[FOREIGN_EFFECT['id']]['value'] == 10
    snapshot_actor = next(row for row in result['state']['characters']
                          if row['location_character_id'] == case.placed_id)
    assert snapshot_actor['active_anomaly'] is None
    reload_saved_geometry(case)
