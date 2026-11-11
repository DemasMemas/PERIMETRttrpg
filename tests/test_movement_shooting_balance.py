from itertools import product

import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import Lobby, LobbyCharacter, Location, LocationCharacter, LocationCombatState, LocationObject, User
from app.services.combat import CombatService
from app.services.exceptions import ValidationError


@pytest.mark.parametrize('shooter,target,evading', product([None, 'walk', 'run', 'sprint'], [None, 'walk', 'run', 'sprint'], [False, True]))
def test_movement_accuracy_matrix(shooter, target, evading):
    expected = {None: 0, 'walk': 1, 'run': 2, 'sprint': 3}[shooter]
    expected += {None: 0, 'walk': 0, 'run': 1, 'sprint': 2}[target]
    expected += 2 if evading and target else 0
    assert CombatService._shooting_movement_modifiers(shooter, target, evading) == {
        'difficulty_penalty': expected, 'disadvantage': False,
    }


@pytest.mark.parametrize('blocked', range(8))
def test_cover_never_imposes_accuracy_penalty_or_disadvantage(blocked):
    _, penalty, disadvantage, targetable = CombatService._cover_grade(blocked)
    assert penalty == 0
    assert disadvantage is False
    assert targetable is (blocked < 7)


@pytest.fixture
def arena(app):
    user = User(username='balance', email='balance@example.com', password_hash='x')
    db.session.add(user)
    db.session.flush()
    lobby = Lobby(name='Balance', gm_id=user.id, invite_code='BALANCE')
    db.session.add(lobby)
    db.session.flush()
    location = Location(lobby_id=lobby.id, name='Arena', grid_width=40, grid_height=5,
                        world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    placed = []
    for name, x in [('Shooter', 0), ('Target', 9)]:
        character = LobbyCharacter(lobby_id=lobby.id, owner_id=user.id, name=name, data={
            'health': {'current': 700, 'max': 700, 'effects': [], 'combatMeta': {}},
            'skills': {'physical': {'shooting': {'base': 10}, 'strength': {'base': 10}}},
            'weapons': [{'name': 'Test rifle', 'durability': 100, 'maxDurability': 100,
                         'range': 30, 'ergonomics': 50, 'ammo': 20, 'damage': 10}],
        })
        db.session.add(character)
        db.session.flush()
        model = LocationCharacter(location_id=location.id, character_id=character.id,
                                  controlled_by=user.id, pos_x=x, pos_y=1, facing_x=1, facing_y=0,
                                  drawn_weapon_index=0, action_points_current=20,
                                  free_actions_current=1, movement_points_current=50)
        db.session.add(model)
        placed.append(model)
    db.session.flush()
    state = LocationCombatState(location_id=location.id, status='active', round_number=1,
                                turn_index=0, turn_order=[c.id for c in placed],
                                current_location_character_id=placed[0].id)
    db.session.add(state)
    db.session.commit()
    return user, location, *placed


@pytest.mark.parametrize('mode,maximum,ap_cost', [('walk', 7, 0), ('run', 15, 2), ('sprint', 23, 4)])
def test_evasion_distance_persists_and_mode_is_paid_only_once(arena, mode, maximum, ap_cost):
    user, location, actor, _ = arena
    actor.pos_y = 3
    for x in [1, maximum]:
        _, _, state = CombatService.move_character(location.id, user.id, actor.character_id,
                                                   x, 3, movement_mode=mode, evasion=True)
    assert state['current_character']['movement_evasion'] is True
    assert actor.action_points_current == 20 - ap_cost
    assert actor.movement_distance_this_turn == maximum
    with pytest.raises(ValidationError, match='distance is limited'):
        CombatService.move_character(location.id, user.id, actor.character_id,
                                     maximum + 1, 3, movement_mode=mode)
    with pytest.raises(ValidationError, match='Уклонение нельзя менять'):
        CombatService.move_character(location.id, user.id, actor.character_id,
                                     maximum + 1, 3, movement_mode=mode, evasion=False)
    CombatService._prepare_character_for_turn(actor)
    assert CombatService._movement_evasion_active(actor) is False


def shoot(arena, **kwargs):
    user, location, actor, _ = arena
    return CombatService.perform_action(location.id, user.id, actor.id, 'attack',
                                        weapon_index=0, shot_count=1, volley_count=1,
                                        **kwargs)


def cover(arena, height):
    item = LocationObject(location_id=arena[1].id, type='cover', name='Wall', tile_x=5, tile_y=1,
                          properties={'height': height, 'hp': 100, 'max_hp': 100})
    db.session.add(item)
    db.session.commit()
    return item


def test_aimed_shot_at_covered_zone_is_rejected_before_spending_resources(arena):
    _, _, actor, target = arena
    cover(arena, 1.6)
    actor.aimed_target_character_id = target.character_id
    actor.aimed_weapon_index = 0
    cost = CombatService._weapon_ergonomics_profile(actor, actor.character.data['weapons'][0], 0)['aimed_shot_action_points']
    with pytest.raises(ValidationError, match='закрыта укрытием'):
        shoot(arena, fire_mode='aimed', target_character_id=target.character_id,
              target_zone='left_leg', action_points=cost)
    assert actor.action_points_current == 20
    assert actor.character.data['weapons'][0]['ammo'] == 20


@pytest.mark.parametrize('height', [1.1, 1.6])
def test_unaimed_shot_at_partially_covered_target_has_no_disadvantage(arena, monkeypatch, height):
    _, _, _, target = arena
    cover(arena, height)
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 10)
    result = shoot(arena, fire_mode='unaimed', target_character_id=target.character_id, action_points=2)
    assert result['attack']['cover']['accuracy_penalty'] == 0
    assert result['attack']['shooting_disadvantage'] is False
    assert len(result['attack']['results'][0]['rolls']) == 1


@pytest.mark.parametrize('mode', ['unaimed', 'area'])
def test_fully_hidden_character_cannot_be_selected_for_fire(arena, mode):
    user, location, actor, target = arena
    wall = cover(arena, 2)
    weapon = actor.character.data['weapons'][0]
    weapon['fireModes'] = {'supports_burst': True, 'burst_size': 3}
    flag_modified(actor.character, 'data')
    db.session.commit()
    if mode == 'area':
        selection = dict(target_character_ids=[target.character_id], area_center_x=9, area_center_y=1,
                         shot_count=6, volley_count=2, action_points=5)
    else:
        selection = dict(target_character_id=target.character_id, shot_count=1, action_points=2)

    with pytest.raises(ValidationError, match='не видна стрелку'):
        CombatService.perform_action(location.id, user.id, actor.id, 'attack', weapon_index=0,
                                     fire_mode=mode, **selection)
    assert actor.action_points_current == 20
    assert actor.character.data['weapons'][0]['ammo'] == 20
    assert CombatService._cover_profile(wall)['hp'] == 100


def test_remembered_tile_cannot_be_used_as_firearm_target(arena):
    _, _, actor, target = arena
    with pytest.raises(ValidationError, match='выберите укрытие'):
        shoot(arena, fire_mode='unaimed', target_x=9, target_y=1, action_points=2)
    assert actor.character.data['weapons'][0]['ammo'] == 20
    assert actor.action_points_current == 20


@pytest.mark.parametrize('distance,protection,should_hit', [
    (1, 0, True), (2, 0, True), (3, 0, True), (4, 0, False), (1, 100, False),
])
def test_cover_shot_reaches_only_nearby_targets_when_penetrated(arena, monkeypatch, distance, protection, should_hit):
    _, _, actor, target = arena
    wall = cover(arena, 2)
    wall.properties = {**wall.properties, 'cover_base_physical_protection': protection}
    before = CombatService._cover_profile(wall)['hp']
    target.pos_x = wall.tile_x + distance
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 1)
    result = shoot(arena, fire_mode='unaimed', target_object_id=wall.id, action_points=2)
    shot = result['attack']['results'][0]
    assert shot['automatic_cover_hit'] is True
    assert bool(shot.get('target_behind_cover_result')) is should_hit
    if should_hit:
        assert shot['target_behind_cover_result']['hit'] is True
        assert shot['target_behind_cover_result']['automatic_hit'] is True
        assert shot['target_behind_cover_result']['zone'] in {'head', 'chest', 'abdomen', 'left_arm', 'right_arm', 'left_leg', 'right_leg'}
    assert CombatService._cover_profile(wall)['hp'] < before
    assert actor.character.data['weapons'][0]['ammo'] == 19
    assert actor.action_points_current == 18


def test_cover_shot_does_not_hit_second_character_on_same_line(arena, monkeypatch):
    user, location, _, nearest = arena
    wall = cover(arena, 2)
    wall.properties = {**wall.properties, 'cover_base_physical_protection': 0}
    nearest.pos_x = 6
    other = LobbyCharacter(lobby_id=location.lobby_id, owner_id=user.id, name='Further',
                           data={'health': {'current': 700, 'max': 700}})
    db.session.add(other)
    db.session.flush()
    db.session.add(LocationCharacter(location_id=location.id, character_id=other.id, pos_x=7, pos_y=1))
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 10)
    result = shoot(arena, fire_mode='unaimed', target_object_id=wall.id, action_points=2)
    assert result['attack']['results'][0]['target_behind_cover_id'] == nearest.character_id
    assert other.data['health']['current'] == 700


def test_aimed_shot_at_exposed_head_is_allowed(arena, monkeypatch):
    _, _, actor, target = arena
    cover(arena, 1.6)
    actor.aimed_target_character_id = target.character_id
    actor.aimed_weapon_index = 0
    cost = CombatService._weapon_ergonomics_profile(actor, actor.character.data['weapons'][0], 0)['aimed_shot_action_points']
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 10)
    result = shoot(arena, fire_mode='aimed', target_character_id=target.character_id,
                   target_zone='head', action_points=cost)
    assert result['attack']['target_zone'] == 'head'
    assert 'head' not in result['attack']['cover']['blocked_zones']


def test_area_fire_check_includes_primary_target_evasion(arena, monkeypatch):
    _, _, actor, target = arena
    weapon = actor.character.data['weapons'][0]
    weapon['fireModes'] = {'supports_burst': True, 'burst_size': 3}
    flag_modified(actor.character, 'data')
    target.movement_mode_this_turn = 'sprint'
    target.character.data['health']['combatMeta']['movementEvasion'] = True
    flag_modified(target.character, 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 10)
    result = CombatService.perform_action(arena[1].id, arena[0].id, actor.id, 'attack',
                                         weapon_index=0, fire_mode='area', shot_count=6,
                                         volley_count=2, action_points=5,
                                         target_character_ids=[target.character_id],
                                         area_center_x=9, area_center_y=1)
    assert result['attack']['results'][0]['difficulty'] == result['attack']['hit_difficulty'] + 4


def test_evasion_is_applied_to_shooting_difficulty(arena, monkeypatch):
    _, _, actor, target = arena
    actor.movement_mode_this_turn = 'walk'
    target.movement_mode_this_turn = 'run'
    target.character.data['health']['combatMeta']['movementEvasion'] = True
    flag_modified(target.character, 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 10)
    result = shoot(arena, fire_mode='unaimed', target_character_id=target.character_id, action_points=2)
    assert result['attack']['movement_accuracy_penalty'] == 4
    assert result['attack']['shooting_disadvantage'] is False
