"""Лёд: настоящий серверный маршрут, ресурсы, броски и непрерывность участка."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import LocationObject
from app.services.combat import CombatService
from app.services.exceptions import ValidationError


@pytest.fixture
def ice_game(client, create_user, auth_headers):
    from test_character_interactions import _setup_pair
    game = _setup_pair(client, create_user, auth_headers)
    game['actor_headers'] = auth_headers(game['actor_user'])
    location, actor = game['location'], game['actor_location']
    location.grid_width, location.grid_height = 10, 1
    location.tiles_data = [[{'terrain':'ice' if 3 <= x <= 6 else 'grass', 'height':1} for x in range(10)]]
    actor.pos_x, actor.pos_y = 1, 0
    actor.facing_x, actor.facing_y = 1, 0
    actor.movement_points_current = 10
    actor.free_actions_current = 1
    data = deepcopy(actor.character.data)
    data.setdefault('skills', {}).setdefault('physical', {})['agility'] = {'base':10, 'bonus':0}
    actor.character.data = data
    game['target_location'].pos_x, game['target_location'].pos_y = 9, 0
    db.session.commit()
    return game


def rolls(monkeypatch, values):
    import app.services.combat as combat
    sequence, drawn = iter(values), []
    original = combat.random.randint
    def randint(low, high):
        if (low, high) == (1, 20):
            value = next(sequence)
            drawn.append(value)
            return value
        return original(low, high)
    monkeypatch.setattr(combat.random, 'randint', randint)
    return drawn


def move(game, x, mode='run'):
    return CombatService.move_character(game['location'].id, game['actor_user']['id'],
                                        game['actor_character'].id, x, 0, movement_mode=mode)


@pytest.mark.parametrize('mode,expected', [('walk',8.5), ('run',4.25), ('sprint',8.5/3)])
def test_ice_costs_two_without_rounding_or_repeated_checks(ice_game,monkeypatch,mode,expected):
    drawn = rolls(monkeypatch, [] if mode == 'walk' else [12])
    actor,cost,_ = move(ice_game,6,mode)
    assert cost == pytest.approx(expected)
    assert actor.movement_points_current == pytest.approx(10-expected)
    assert actor.pos_x == 6
    assert len(drawn) == (0 if mode == 'walk' else 1)
    assert actor.facing_x == 1 and actor.facing_y == 0


@pytest.mark.parametrize('mode', ['run','sprint'])
def test_failure_continues_same_route_with_fractional_remaining_op(ice_game,monkeypatch,mode):
    actor = ice_game['actor_location']
    actor.movement_points_current = 4.25
    db.session.commit()
    drawn = rolls(monkeypatch,[11])
    actor,cost,state = move(ice_game,6,mode)
    before = 1.5/(2 if mode == 'run' else 3)
    expected_x = 2.5+(4.25-before)/2
    assert actor.pos_x == pytest.approx(expected_x)
    assert cost == pytest.approx(4.25)
    assert actor.movement_points_current == 0
    assert actor.movement_mode_this_turn == 'walk'
    assert actor.action_points_current == (3 if mode == 'run' else 1)
    assert actor.free_actions_current == 1
    assert actor._movement_slip_stopped
    assert actor._movement_fall is None and actor.posture == 'standing'
    assert actor._movement_surfaces[-1]['x'] == pytest.approx(expected_x)
    assert state['current_character']['slippery_movement']['forced_from'] == mode
    assert drawn == [11]


def test_success_persists_across_reload_and_exiting_ice_clears_continuity(ice_game,monkeypatch):
    drawn = rolls(monkeypatch,[20])
    move(ice_game,5)
    db.session.expire_all()
    move(ice_game,6.2)
    actor,_,_ = move(ice_game,7)
    assert drawn == [20]
    assert not actor.character.data['health']['combatMeta']['slipperyMovement']['checked_on_ice']


def test_two_separated_ice_patches_require_two_checks(ice_game,monkeypatch):
    ice_game['location'].tiles_data = [[{'terrain':'ice' if x in {3,4,7,8} else 'grass','height':1} for x in range(10)]]
    db.session.commit()
    drawn = rolls(monkeypatch,[20,12])
    actor,_,_ = move(ice_game,8)
    assert drawn == [20,12]
    assert len(actor._movement_slip_checks) == 2


@pytest.mark.parametrize('roll,mode', [(11,'walk'),(12,'run')])
def test_start_running_on_ice_without_displacement_checks_once(ice_game,monkeypatch,roll,mode):
    actor = ice_game['actor_location']
    actor.pos_x = 3
    db.session.commit()
    drawn = rolls(monkeypatch,[roll])
    actor,cost,_ = move(ice_game,3)
    assert cost == 0 and actor.pos_x == 3
    assert actor.movement_mode_this_turn == mode
    assert drawn == [roll]
    move(ice_game,4,mode)
    assert drawn == [roll]


def test_failed_run_can_keep_walking_after_more_than_walk_distance(ice_game,monkeypatch):
    actor = ice_game['actor_location']
    actor.movement_mode_this_turn = 'run'
    actor.movement_distance_this_turn = 11
    actor.movement_points_current = 8
    db.session.commit()
    drawn = rolls(monkeypatch,[11])
    actor,cost,_ = move(ice_game,6)
    assert cost == pytest.approx(7.75)
    assert actor.movement_distance_this_turn == 16
    actor,cost,_ = move(ice_game,6.1,'walk')
    assert cost == pytest.approx(.2)
    assert actor.pos_x == 6.1 and drawn == [11]


def test_rejected_move_does_not_draw_or_consume_resources(ice_game,monkeypatch):
    actor = ice_game['actor_location']
    actor.movement_points_current = .1
    db.session.commit()
    drawn = rolls(monkeypatch,[])
    before = actor.action_points_current
    with pytest.raises(ValidationError,match='movement points'):
        move(ice_game,6)
    assert drawn == [] and actor.pos_x == 1
    assert actor.action_points_current == before


@pytest.mark.parametrize('embedded', [False,True])
def test_floor_over_ice_removes_cost_and_slip_check(ice_game,monkeypatch,embedded):
    properties = {'level':0,'dimensions':{'width':10,'depth':1}}
    if embedded:
        tiles = deepcopy(ice_game['location'].tiles_data)
        tiles[0][5]['objects'] = [{'type':'floor','properties':properties}]
        ice_game['location'].tiles_data = tiles
    else:
        db.session.add(LocationObject(location_id=ice_game['location'].id,type='floor',tile_x=4.5,tile_y=0,properties=properties))
    db.session.commit()
    drawn = rolls(monkeypatch,[])
    _,cost,_ = move(ice_game,6)
    assert cost == pytest.approx(2.5)
    assert drawn == []


def test_flight_ignores_ice_cost_and_checks(ice_game,monkeypatch):
    monkeypatch.setattr(CombatService,'_mutant_has_trait',staticmethod(lambda data,key:key=='Полёт'))
    drawn = rolls(monkeypatch,[])
    _,cost,_ = move(ice_game,6)
    assert cost == pytest.approx(2.5) and drawn == []


@pytest.mark.parametrize('terrain', ['snow','sand','swamp','metal','water'])
def test_other_surfaces_do_not_trigger_slipping(ice_game,monkeypatch,terrain):
    ice_game['location'].tiles_data = [[{'terrain':terrain,'height':1} for _ in range(10)]]
    db.session.commit()
    drawn = rolls(monkeypatch,[])
    _,cost,_ = move(ice_game,6)
    assert cost == pytest.approx(5 if terrain in {'snow','sand','swamp'} else 2.5)
    assert drawn == []


def test_bush_on_floor_over_ice_does_not_restore_slipperiness(ice_game,monkeypatch):
    db.session.add_all([
        LocationObject(location_id=ice_game['location'].id,type='floor',tile_x=4.5,tile_y=0,
                       properties={'level':0,'dimensions':{'width':10,'depth':1}}),
        LocationObject(location_id=ice_game['location'].id,type='tree',tile_x=4,tile_y=0,
                       properties={'visual_asset':'nature_bush_green','passable':True,'dimensions':{'width':.8,'depth':.8}})])
    db.session.commit()
    drawn = rolls(monkeypatch,[])
    _,cost,_ = move(ice_game,6)
    assert cost == pytest.approx(3)
    assert drawn == []


def test_new_turn_requires_a_new_check_when_starting_on_ice(ice_game,monkeypatch):
    from app.models import LocationCombatState
    drawn = rolls(monkeypatch,[20,12])
    actor,_,_ = move(ice_game,5)
    CombatService._prepare_character_for_turn(actor)
    actor.movement_points_current = 10
    db.session.query(LocationCombatState).filter_by(location_id=ice_game['location'].id).first().round_number = 3
    db.session.commit()
    move(ice_game,6)
    assert drawn == [20,12]


def test_anomaly_beyond_failed_walk_endpoint_is_not_entered(ice_game,monkeypatch):
    actor = ice_game['actor_location']
    actor.movement_points_current = 4.25
    db.session.commit()
    rolls(monkeypatch,[11])
    monkeypatch.setattr(CombatService,'_anomaly_candidates',staticmethod(lambda location:[]))
    monkeypatch.setattr(CombatService,'_anomalies_at_tile',staticmethod(
        lambda location,x,y,*args,**kwargs:[{'key':'test'}] if x == 6 else []))
    monkeypatch.setattr(CombatService,'_enter_anomaly',staticmethod(lambda *a,**k:pytest.fail('До аномалии персонаж не дошёл')))
    actor,_,_ = move(ice_game,6)
    assert actor.pos_x == pytest.approx(4.25)


def test_ice_and_vegetation_use_maximum_surface_multiplier(ice_game,monkeypatch):
    db.session.add(LocationObject(location_id=ice_game['location'].id,type='tree',tile_x=4,tile_y=0,
        properties={'visual_asset':'nature_bush_green','passable':True,'dimensions':{'width':.8,'depth':.8}}))
    db.session.commit()
    rolls(monkeypatch,[20])
    _,cost,_ = move(ice_game,6)
    assert cost == pytest.approx(4.25)


def test_unreached_climb_is_not_paid_after_failed_slip(ice_game,monkeypatch):
    actor = ice_game['actor_location']
    actor.movement_points_current = 5.5
    db.session.add(LocationObject(location_id=ice_game['location'].id,type='barrier',tile_x=7,tile_y=0,
        properties={'dimensions':{'width':.8,'depth':.8,'height':.5}}))
    db.session.commit()
    rolls(monkeypatch,[11])
    actor,cost,_ = CombatService.move_character(ice_game['location'].id,ice_game['actor_user']['id'],
        ice_game['actor_character'].id,8,0,movement_mode='run',climb_payment='action')
    assert cost == pytest.approx(5.5)
    assert actor.pos_x == pytest.approx(4.875)
    assert actor.action_points_current == 3


def test_preview_never_draws_or_saves_execution_segments(ice_game,monkeypatch,client):
    actor = ice_game['actor_location']
    before = deepcopy(actor.character.data)
    drawn = rolls(monkeypatch,[])
    response = client.post(f"/lobbies/{ice_game['lobby'].id}/locations/{ice_game['location'].id}/movement-preview",
        json={'character_id':actor.character_id,'x':6,'y':0,'floor_level':0},
        headers=ice_game['actor_headers'])
    assert response.status_code == 200
    route = response.get_json()['route']
    assert route['slippery'] is True
    assert route['cost'] == pytest.approx(8.5)
    assert 'movement_segments' not in route
    db.session.refresh(actor.character)
    assert actor.character.data == before and drawn == []
