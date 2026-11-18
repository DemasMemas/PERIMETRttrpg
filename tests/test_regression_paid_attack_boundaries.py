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
    actor.free_actions_current = 0
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


@pytest.mark.parametrize(('worn', 'assisting', 'cost'), [(False, False, 12), (True, False, 24), (False, True, 12)])
def test_exoskeleton_battery_replacement_applies_only_after_all_turns_are_paid(
        client, paid_case, auth_headers, worn, assisting, cost):
    case = paid_case
    identity = 'paid-exoskeleton-battery'
    actor = case['actor_character']
    wearer = case['target_character'] if assisting else actor
    data = deepcopy(actor.data)
    battery = {'id': 'new-cell', 'name': 'Аккумуляторы Экзоскелета', 'category': 'exoskeleton_module',
               'subcategory': 'battery', 'quantity': 2,
               'attributes': {'slot_type': 'exoskeleton_battery', 'charge_days': 1, 'remaining_days': 1}}
    old_cell = deepcopy(battery)
    old_cell.update(id='old-cell', quantity=1, slotType='exoskeleton_battery')
    old_cell['attributes']['remaining_days'] = 0
    exoskeleton = {'id': 'suit', 'name': 'Экзоскелет', 'category': 'armor', 'installedModules': [old_cell]}
    data['inventory']['backpack'] = [battery]
    if assisting:
        case['target_location'].pos_x = 2
        wearer_data = deepcopy(wearer.data)
        wearer_data.setdefault('equipment', {})['armor'] = exoskeleton
        wearer.data = wearer_data
    elif worn:
        data.setdefault('equipment', {})['armor'] = exoskeleton
    else:
        data['inventory']['backpack'].append(exoskeleton)
    target_path = ['equipment', 'armor'] if worn or assisting else ['inventory', 'backpack', 1]
    actor.data = data
    db.session.commit()
    payload = {'action_key': 'change_equipment_module', 'module_operation': 'install',
               'module_slot_type': 'exoskeleton_battery', 'module_target_path': target_path,
               'item_path': ['inventory', 'backpack', 0], 'pending_action_id': identity,
               'module_selection': {'target': {'id': 'suit'}, 'source': {'id': 'new-cell'}}}
    if assisting:
        payload['target_character_id'] = wearer.id

    rejected = action(client, case, auth_headers, **payload)
    assert rejected.status_code == 400, rejected.get_json()
    assert case['actor_location'].action_points_current == 2
    data = deepcopy(actor.data)
    data['inventory']['backpack'].append({'id': 'driver', 'name': 'Отвертка', 'category': 'misc'})
    actor.data = data
    db.session.commit()

    started = action(client, case, auth_headers, **payload)
    assert started.status_code == 200, started.get_json()
    assert started.get_json()['pending_action'] is True
    row = db.session.get(DeferredCombatAction, identity)
    assert row.remaining_action_points == cost - 2
    assert case['actor_location'].action_points_current == 0
    for _ in range(35):
        pay_turn(client, case, auth_headers)
        backpack = actor.data['inventory']['backpack']
        armor = wearer.data['equipment']['armor'] if worn or assisting else backpack[1]
        assert armor['installedModules'] == [old_cell]
        assert backpack[0]['quantity'] == 2
        if row.status == 'ready':
            break
        passed = client.post(url(case) + '/end_turn', headers=auth_headers(case['actor_user']),
                             json={'location_character_id': case['actor_location'].id})
        assert passed.status_code == 200, passed.get_json()
    assert row.status == 'ready'

    completed = action(client, case, auth_headers, action_key='change_equipment_module',
                       resume_pending_action_id=identity)
    assert completed.status_code == 200, completed.get_json()
    assert completed.get_json()['module_change']['action_points'] == cost
    assert row.status == 'completed'
    backpack = actor.data['inventory']['backpack']
    armor = wearer.data['equipment']['armor'] if worn or assisting else backpack[1]
    assert backpack[0]['quantity'] == 1
    assert wearer.data['inventory']['backpack'][-1] == old_cell
    assert armor['installedModules'][0]['id'] != battery['id']
    assert armor['installedModules'][0]['quantity'] == 1
    assert armor['powered'] is True
    replay_unchanged(client, case, auth_headers, identity, 'change_equipment_module')


def test_new_combat_clears_weapon_shot_count_from_reused_round(
        client, paid_case, auth_headers):
    case = paid_case
    location_id = case['location'].id
    gm_id = case['actor_user']['id']
    actor_id = case['actor_location'].id
    CombatService.end_combat(location_id, gm_id)

    # Сохранение прежней версии со счётчиком второго раунда завершённого боя.
    data = deepcopy(case['actor_character'].data)
    data['weapons'][0]['fireRate'] = 1
    data['health']['combatMeta']['weaponShots'] = {'round': 2, 'shots': {'0': 1}}
    case['actor_character'].data = data
    db.session.commit()

    started = client.post(url(case) + '/start', headers=auth_headers(case['actor_user']), json={
        'location_character_ids': [actor_id, case['target_location'].id],
        'initiator_location_character_id': actor_id,
    })
    assert started.status_code == 200, started.get_json()
    db.session.expire_all()
    data = deepcopy(case['actor_character'].data)
    meta = data['health']['combatMeta']
    assert CombatService._validate_weapon_fire_rate(meta, 2, 0, data['weapons'][0], 1)['fired'] == 0

    CombatService._record_weapon_shots(meta, 2, 0, 1)
    case['actor_character'].data = data
    db.session.commit()
    CombatService.end_combat(location_id, gm_id)
    assert 'weaponShots' not in case['actor_character'].data['health']['combatMeta']


@pytest.mark.parametrize('deferred', [False, True], ids=['immediate', 'deferred'])
def test_aimed_shot_pays_for_automatic_aim_only_once(client, paid_case, auth_headers, deferred):
    case = paid_case
    actor = case['actor_location']
    target_id = case['target_character'].id
    CombatService._prepare_character_for_turn(case['target_location'])
    CombatService._sync_location_effects_from_character(case['target_location'])
    actor.action_points_current = 2 if deferred else 14
    db.session.commit()

    shot = {'action_key': 'attack', 'weapon_index': 0, 'target_character_id': target_id,
            'target_zone': 'chest', 'fire_mode': 'aimed', 'shot_count': 1, 'action_points': 6}
    identity = 'aim-on-attack'
    started = action(client, case, auth_headers, **shot, pending_action_id=identity)
    assert started.status_code == 200, started.get_json()
    result = started.get_json()
    if deferred:
        assert result['pending_action_total_points'] == 7
        assert actor.aimed_target_character_id is None
        recover(client, case, auth_headers, identity, status='paying', remaining=5, key='attack')
        pay_turn(client, case, auth_headers)
        recover(client, case, auth_headers, identity, status='ready', remaining=0, key='attack')
        resumed = action(client, case, auth_headers, action_key='attack', resume_pending_action_id=identity)
        assert resumed.status_code == 200, resumed.get_json()
        result = resumed.get_json()

    assert result['attack']['action_points'] == 7
    assert result['attack']['aim_accuracy_bonus'] == 1
    assert result['character']['aimed_target_character_id'] == target_id
    assert result['character']['aimed_weapon_index'] == 0
    assert result['character']['action_points_current'] == (0 if deferred else 7)
    if not deferred:
        next_shot = action(client, case, auth_headers, **shot, pending_action_id='reuse-aim')
        assert next_shot.status_code == 200, next_shot.get_json()
        assert next_shot.get_json()['attack']['action_points'] == 6


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
    data = deepcopy(case['actor_character'].data)
    data['weapons'][0]['installedMagazine']['ammo'][0]['attributes']['damage'] = 12
    case['actor_character'].data = data
    db.session.commit()
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
    # Попадание, выбор зоны (даже при aimed), сильное кровотечение, успешная Воля.
    draws = deque([('_resolve_attack', 1, 20, 19), ('_resolve_attack', 1, 20, 10),
                   ('_roll_firearm_bleeding', 1, 6, 6), ('_apply_attack_damage', 1, 100, 100),
                   ('_apply_attack_damage', 1, 20, 13), ('apply_stress_trigger', 1, 20, 20)])
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
    assert attack['damage_total'] == 12
    assert len(attack['results']) == 1
    assert attack['results'][0]['hit'] is True
    assert attack['results'][0]['rolls'] == [19]
    assert attack['results'][0]['zone'] == 'chest'
    assert any(bleeding['stage'] == 'severe' for bleeding in attack['results'][0]['bleedings'])
    assert attack['results'][0]['additional_traumas'][0]['roll'] == 13
    db.session.expire_all()
    assert case['actor_location'].action_points_current == 1
    weapons = case['actor_character'].data['weapons']
    assert weapons[0]['installedMagazine']['ammo'][0]['quantity'] == 2
    assert weapons[0]['ammo'] == 2
    assert weapons[1] == original_ammo[1]
    assert case['target_character'].data['health']['current'] == 688
    assert any(effect['type'] == 'bleeding_external_severe'
               for effect in case['target_character'].data['health']['effects'])
    assert any(effect['type'] == 'bleeding_internal_medium'
               for effect in case['target_character'].data['health']['effects'])
    assert db.session.get(LobbyCharacter, case['replacement_id']).data == replacement_before
    assert db.session.get(DeferredCombatAction, identity).status == 'completed'
    messages = [m.message for m in ChatMessage.query.filter_by(lobby_id=case['lobby'].id)
                if m.id not in message_ids]
    assert len(messages) == 1
    assert 'Кровотечение:' in messages[0] and 'сильное' in messages[0]
    assert 'Доп. травма: d20 13' in messages[0]
    journal = case['actor_character'].data['health']['combatMeta']['actionJournal']
    entries = [entry for entry in journal if entry.get('deferred_id') == identity]
    assert len(entries) == 1
    assert entries[0]['status'] == 'completed'
    assert 'Доп. травма: d20 13' in entries[0]['outcome']
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
    # После оплаты 5 ОД у персонажа остался 1 ОД долга и ход продолжается за остаток СД или ОП.
    assert first_turn['current_location_character_id'] == case['actor_location'].id
    recover(client, case, auth_headers, identity,
            status='paying', remaining=1, key='clear_weapon_jam')
    assert case['actor_location'].action_points_current == 0
    assert case['actor_character'].data['weapons'] == original_weapons
    before = snapshot(case, identity)
    early = action(client, case, auth_headers, action_key='clear_weapon_jam',
                   resume_pending_action_id=identity)
    assert early.status_code == 409, early.get_json()
    assert snapshot(case, identity) == before
    passed = client.post(url(case) + '/end_turn', headers=auth_headers(case['actor_user']),
                         json={'location_character_id': case['actor_location'].id})
    assert passed.status_code == 200, passed.get_json()
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
