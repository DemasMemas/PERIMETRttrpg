"""Оплаченный выстрел и клин: основные границы NOT_REACHED; коррекция NOT_RUN.

Дальний обычный выстрел не запрещён текущим контрактом: отдельный
NEEDS_RULE описан в reports/D-ATTACK-JAM.md, тестовый запрет не выдуман.
"""
from collections import deque
from copy import deepcopy
import sys
from types import SimpleNamespace

import pytest

import app.services.combat as combat_module
from app.extensions import db
from app.models import (ChatMessage, DeferredCombatAction, LobbyCharacter,
                        LocationCharacter, LocationCombatState, LocationObject)
from app.services.combat import CombatService
from app.services.health import apply_health_maximums
from test_character_interactions import _setup_pair


def rifle(identity):
    return {
        'id': identity, 'name': 'Проверочная винтовка', 'category': 'weapon',
        'weight': 1, 'ergonomics': 0, 'accuracy': 0, 'range': 20,
        'durability': 100, 'maxDurability': 100, 'ammo': 3,
        'fireModes': {'single_shot_options': [1]},
        'installedMagazine': {'id': identity + '-mag', 'ammo': [{
            'id': identity + '-rounds', 'name': '5.56x45', 'quantity': 3,
            'category': 'ammo', 'attributes': {
                'caliber': '5.56x45', 'damage': 10, 'armor_piercing': 0,
            },
        }]},
    }


@pytest.fixture
def paid_case(client, create_user, auth_headers):
    pair = _setup_pair(client, create_user, auth_headers)
    data = deepcopy(pair['actor_character'].data)
    data['weapons'] = [rifle('paid-original'), rifle('paid-other')]
    data['activeWeaponIndex'] = 0
    data['skills']['physical']['shooting'] = {'base': 10, 'bonus': 0}
    data['health'] = {'current': 700, 'max': 700, 'effects': []}
    apply_health_maximums(data)
    pair['actor_character'].data = data
    target = deepcopy(pair['target_character'].data)
    target['health'] = {'current': 700, 'max': 700, 'effects': []}
    apply_health_maximums(target)
    pair['target_character'].data = target
    actor = pair['actor_location']
    actor.pos_x, actor.pos_y = 1, 1
    actor.facing_x, actor.facing_y = 1, 0
    actor.drawn_weapon_index = 0
    actor.action_points_current = 2
    pair['target_location'].pos_x, pair['target_location'].pos_y = 5, 1
    pair['location'].grid_width = pair['location'].grid_height = 12
    other = LobbyCharacter(lobby_id=pair['lobby'].id,
        owner_id=pair['target_user']['id'], name='Подставная цель', data=deepcopy(target))
    db.session.add(other)
    db.session.flush()
    db.session.add(LocationCharacter(location_id=pair['location'].id,
        character_id=other.id, controlled_by=pair['target_user']['id'],
        pos_x=5, pos_y=3))
    # Препятствие сначала вне луча, позже переносится в него без mock видимости.
    wall = LocationObject(location_id=pair['location'].id, type='wall',
        name='Проверочная стена', tile_x=8, tile_y=8,
        properties={'dimensions': {'width': 1, 'depth': 1, 'height': 3},
                    'floor_level': 0, 'cover_class': 'heavy'})
    db.session.add(wall)
    db.session.commit()
    return {**pair, 'replacement_id': other.id, 'wall_id': wall.id}


def url(case):
    return f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat"


def action(client, case, auth_headers, **payload):
    return client.post(url(case) + '/action', headers=auth_headers(case['actor_user']),
        json={'location_character_id': case['actor_location'].id, **payload})


def no_draws(monkeypatch):
    def forbidden(low, high):
        pytest.fail(f'Неожиданный бросок до проверки условий/при оплате: {low}, {high}')
    # Не менять общий random других модулей и не подменять игровые результаты.
    monkeypatch.setattr(combat_module, 'random', SimpleNamespace(randint=forbidden))


def snapshot(case, identity):
    db.session.expire_all()
    actor = db.session.get(LocationCharacter, case['actor_location'].id)
    row = db.session.get(DeferredCombatAction, identity)
    state = LocationCombatState.query.filter_by(location_id=case['location'].id).one()
    return {
        'resources': (actor.action_points_current, actor.free_actions_current,
                      actor.movement_points_current, actor.drawn_weapon_index),
        'aim': (actor.aimed_target_character_id, actor.aimed_weapon_index, actor.aim_accuracy_bonus),
        'sheets': [(s.id, s.revision, deepcopy(s.data)) for s in
                   LobbyCharacter.query.filter_by(lobby_id=case['lobby'].id).order_by(LobbyCharacter.id)],
        'positions': [(c.id, c.pos_x, c.pos_y, c.floor_level, c.posture,
                       c.facing_x, c.facing_y, c.action_points_current,
                       c.free_actions_current, c.movement_points_current,
                       c.drawn_weapon_index, c.last_action,
                       deepcopy(c.effects), deepcopy(c.hp_zones))
                      for c in LocationCharacter.query.filter_by(location_id=case['location'].id)
                      .order_by(LocationCharacter.id)],
        'row': (row.status, row.remaining_action_points, row.revision, deepcopy(row.payload)),
        'state': (state.status, state.round_number, state.turn_index,
                  state.current_location_character_id, deepcopy(state.turn_order)),
        'messages': [(m.id, m.username, m.message) for m in
                     ChatMessage.query.filter_by(lobby_id=case['lobby'].id).order_by(ChatMessage.id)],
    }


def recover(client, case, auth_headers, identity, *, status, remaining, key):
    db.session.expire_all()
    response = client.get(url(case), headers=auth_headers(case['actor_user']))
    assert response.status_code == 200, response.get_json()
    state = response.get_json()
    summary = next(c for c in state['characters'] if c['location_character_id'] == case['actor_location'].id)['deferred_action']
    assert summary['id'] == identity
    assert summary['action_key'] == key
    assert summary['status'] == status
    assert summary['remaining_action_points'] == remaining
    assert summary['server_executable'] is True
    row = db.session.get(DeferredCombatAction, identity)
    assert row.payload['weapon_index'] == 0
    assert row.payload['_bindings']['weapon']['id'] == 'paid-original'
    return row


def pay_turn(client, case, auth_headers):
    response = client.post(url(case) + '/end_turn',
        headers=auth_headers(case['target_user']),
        json={'location_character_id': case['target_location'].id})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def replay_unchanged(client, case, auth_headers, identity, key):
    before = snapshot(case, identity)
    response = action(client, case, auth_headers, action_key=key,
                      resume_pending_action_id=identity)
    assert response.status_code == 409, response.get_json()
    assert snapshot(case, identity) == before


@pytest.mark.parametrize('blocked', [False, True], ids=['complete-once', 'cancel-hidden'])
def test_paid_aimed_shot_completes_once_or_cancels_permanently_when_hidden(
        client, paid_case, auth_headers, monkeypatch, blocked):
    case = paid_case
    identity = 'paid-shot-hidden'
    no_draws(monkeypatch)
    # Каноническая фикстура до aim/start: те же prepare/sync, что у штатного хода.
    CombatService._prepare_character_for_turn(case['target_location'])
    CombatService._sync_location_effects_from_character(case['target_location'])
    db.session.commit()
    # Прицеливание настоящее; его1ОД оплачивается отдельно от выстрела6ОД.
    case['actor_location'].action_points_current = 3
    db.session.commit()
    aimed = action(client, case, auth_headers, action_key='aim', weapon_index=0,
                   target_character_id=case['target_character'].id)
    assert aimed.status_code == 200, aimed.get_json()
    assert case['actor_location'].action_points_current == 2
    original_ammo = deepcopy(case['actor_character'].data['weapons'])
    original_target = deepcopy(case['target_character'].data)
    started = action(client, case, auth_headers, action_key='attack', weapon_index=0,
        target_character_id=case['target_character'].id, target_zone='chest',
        fire_mode='aimed', shot_count=1, volley_count=1, action_points=6,
        pending_action_id=identity)
    assert started.status_code == 200, started.get_json()
    assert started.get_json()['pending_action'] is True
    assert started.get_json()['attack'] is None
    row = recover(client, case, auth_headers, identity,
                  status='paying', remaining=4, key='attack')
    saved = deepcopy(row.payload)
    assert saved['target_character_id'] == case['target_character'].id
    assert saved['target_zone'] == 'chest'
    assert case['actor_location'].action_points_current == 0
    assert case['actor_character'].data['weapons'] == original_ammo
    assert case['target_character'].data == original_target
    pay_turn(client, case, auth_headers)
    recover(client, case, auth_headers, identity, status='ready', remaining=0, key='attack')
    assert case['actor_location'].action_points_current == 1
    assert case['actor_character'].data['weapons'] == original_ammo
    assert case['target_character'].data['health']['current'] == 700
    assert case['target_character'].data['health']['zones'] == original_target['health']['zones']
    assert case['target_character'].data['health']['effects'] == original_target['health']['effects']
    wall = db.session.get(LocationObject, case['wall_id'])
    wall.tile_x, wall.tile_y = (3, 1) if blocked else (8, 8)
    db.session.commit()
    cover = CombatService._cover_analysis(case['location'].id,
                                         case['actor_location'], case['target_location'])
    assert cover['targetable'] is not blocked
    if blocked:
        assert 'chest' in cover['blocked_zones']
        before = snapshot(case, identity)
        target_before = deepcopy(case['target_character'].data)
        for attempt,extra in enumerate(({}, {'weapon_index': 1, 'target_character_id': case['replacement_id'],
                           'fire_mode': 'unaimed', 'target_zone': 'left_arm', 'action_points': 0})):
            denied = action(client, case, auth_headers, action_key='attack',
                            resume_pending_action_id=identity, **extra)
            assert denied.status_code == (400 if attempt == 0 else 409), denied.get_json()
            assert case['actor_location'].action_points_current == 1
            assert case['actor_character'].data['weapons'] == original_ammo
            assert case['target_character'].data == target_before
            assert db.session.get(DeferredCombatAction, identity).status == 'cancelled'
        assert db.session.get(DeferredCombatAction, identity).payload == saved
        wall = db.session.get(LocationObject, case['wall_id'])
        wall.tile_x, wall.tile_y = 8, 8
        db.session.commit()
        denied = action(client,case,auth_headers,action_key='attack',resume_pending_action_id=identity)
        assert denied.status_code == 409,denied.get_json()
        assert case['actor_character'].data['weapons'] == original_ammo
        return
    assert CombatService._cover_analysis(case['location'].id,
        case['actor_location'], case['target_location'])['targetable'] is True
    replacement_before = deepcopy(db.session.get(LobbyCharacter, case['replacement_id']).data)
    message_ids = {m.id for m in ChatMessage.query.filter_by(lobby_id=case['lobby'].id)}
    # Попадание, выбор зоны (даже при aimed), отсутствие кровотечения, успешная Воля.
    draws = deque([('_resolve_attack', 1, 20, 19), ('_resolve_attack', 1, 20, 10),
                   ('_roll_firearm_bleeding', 1, 6, 1), ('apply_stress_trigger', 1, 20, 20)])
    def draw(low, high):
        assert draws, (low, high)
        caller, expected_low, expected_high, result = draws.popleft()
        assert sys._getframe(1).f_code.co_name == caller
        assert (low, high) == (expected_low, expected_high)
        return result
    monkeypatch.setattr(combat_module, 'random', SimpleNamespace(randint=draw))
    completed = action(client, case, auth_headers, action_key='attack',
        resume_pending_action_id=identity, weapon_index=1,
        target_character_id=case['replacement_id'], fire_mode='unaimed', action_points=0)
    assert completed.status_code == 200, completed.get_json()
    assert not draws
    attack = completed.get_json()['attack']
    assert attack['weapon_index'] == 0
    assert attack['target_character_id'] == case['target_character'].id
    assert attack['fire_mode'] == 'aimed'
    assert attack['action_points'] == 6
    assert attack['shot_count'] == 1
    assert attack['damage_total'] == 10
    assert len(attack['results']) == 1
    assert attack['results'][0]['hit'] is True
    assert attack['results'][0]['rolls'] == [19]
    assert attack['results'][0]['zone'] == 'chest'
    db.session.expire_all()
    assert case['actor_location'].action_points_current == 1
    weapons = case['actor_character'].data['weapons']
    assert weapons[0]['installedMagazine']['ammo'][0]['quantity'] == 2
    assert weapons[0]['ammo'] == 2
    assert weapons[1] == original_ammo[1]
    assert case['target_character'].data['health']['current'] == 690
    assert db.session.get(LobbyCharacter, case['replacement_id']).data == replacement_before
    assert db.session.get(DeferredCombatAction, identity).status == 'completed'
    assert len([m for m in ChatMessage.query.filter_by(lobby_id=case['lobby'].id)
                if m.id not in message_ids]) == 1
    no_draws(monkeypatch)
    replay_unchanged(client, case, auth_headers, identity, 'attack')


def test_paid_clear_jam_recovers_selected_weapon_and_finishes_once(
        client, paid_case, auth_headers, monkeypatch):
    case = paid_case
    identity = 'paid-clear-jam'
    no_draws(monkeypatch)
    data = deepcopy(case['actor_character'].data)
    # Штатный результат10:8ОД; Стрельба10 не даёт скидки15/20.
    jam = {'id': 'paid-jam', 'result': 10,
           **deepcopy(CombatService.WEAPON_JAM_RESULTS[10])}
    other_jam = {'id': 'other-jam', 'result': 1,
                 **deepcopy(CombatService.WEAPON_JAM_RESULTS[1])}
    data['weapons'][0]['jam'] = deepcopy(jam)
    data['weapons'][0]['jams'] = [deepcopy(jam)]
    data['weapons'][1]['jam'] = deepcopy(other_jam)
    data['weapons'][1]['jams'] = [deepcopy(other_jam)]
    case['actor_character'].data = data
    db.session.commit()
    original_weapons = deepcopy(data['weapons'])
    started = action(client, case, auth_headers, action_key='clear_weapon_jam',
                     weapon_index=0, pending_action_id=identity)
    assert started.status_code == 200, started.get_json()
    assert started.get_json()['pending_action'] is True
    assert started.get_json()['clear_weapon_jam'] is None
    row = recover(client, case, auth_headers, identity,
                  status='paying', remaining=6, key='clear_weapon_jam')
    saved = deepcopy(row.payload)
    assert case['actor_location'].action_points_current == 0
    assert case['actor_character'].data['weapons'] == original_weapons
    before = snapshot(case, identity)
    early = action(client, case, auth_headers, action_key='clear_weapon_jam',
                   resume_pending_action_id=identity, weapon_index=1)
    assert early.status_code == 409, early.get_json()
    assert snapshot(case, identity) == before
    first_turn = pay_turn(client, case, auth_headers)
    # Оплата5ОД заканчивает первый ход автоматически, остаётся1ОД долга.
    assert first_turn['current_location_character_id'] == case['target_location'].id
    recover(client, case, auth_headers, identity,
            status='paying', remaining=1, key='clear_weapon_jam')
    assert case['actor_location'].action_points_current == 0
    assert case['actor_character'].data['weapons'] == original_weapons
    before = snapshot(case, identity)
    early = action(client, case, auth_headers, action_key='clear_weapon_jam',
                   resume_pending_action_id=identity)
    assert early.status_code == 409, early.get_json()
    assert snapshot(case, identity) == before
    second_turn = pay_turn(client, case, auth_headers)
    assert second_turn['current_location_character_id'] == case['actor_location'].id
    row = recover(client, case, auth_headers, identity,
                  status='ready', remaining=0, key='clear_weapon_jam')
    assert row.payload == saved
    assert case['actor_location'].action_points_current == 4
    assert case['actor_character'].data['weapons'] == original_weapons
    message_ids = {m.id for m in ChatMessage.query.filter_by(lobby_id=case['lobby'].id)}
    # Клиент пытается заменить индекс и действие: durable wrapper берёт сохранённое.
    finished = action(client, case, auth_headers, action_key='attack', weapon_index=1,
        target_character_id=case['replacement_id'], action_points=0,
        resume_pending_action_id=identity)
    assert finished.status_code == 200, finished.get_json()
    result = finished.get_json()
    assert result['action'] == 'clear_weapon_jam'
    assert result['attack'] is None
    assert result['clear_weapon_jam'] == {
        'weapon_index': 0, 'result': 10, 'label': jam['label'],
        'base_action_points': 8, 'skill_reduction': 0, 'action_points': 8,
    }
    db.session.expire_all()
    assert case['actor_location'].action_points_current == 4
    actual = case['actor_character'].data['weapons']
    expected = deepcopy(original_weapons[0])
    expected.pop('jam')
    expected.pop('jams')
    assert actual[0] == expected
    assert actual[1] == original_weapons[1]
    assert db.session.get(DeferredCombatAction, identity).status == 'completed'
    # В существующем API для clear_weapon_jam нет отдельного chat formatter.
    # Проверяется один результат ответа и отсутствие выдуманного/дублированного chat.
    assert {m.id for m in ChatMessage.query.filter_by(lobby_id=case['lobby'].id)} == message_ids
    replay_unchanged(client, case, auth_headers, identity, 'clear_weapon_jam')
