"""D+: поздний resume после отмены, удаление строки и потеря/подмена оплаченной цели."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import (ChatMessage, DeferredCombatAction, LobbyCharacter,
                        LocationCharacter, LocationCombatState)
from app.services.combat import CombatService
from test_character_interactions import _setup_pair


@pytest.fixture
def deferred_case(client, create_user, auth_headers):
    pair = _setup_pair(client, create_user, auth_headers)
    data = deepcopy(pair['actor_character'].data)
    data['weapons'] = [{'id': 'ad-weapon', 'name': 'Тестовое оружие', 'ergonomics': 0}]
    pair['actor_character'].data = data
    pair['actor_location'].action_points_current = 2
    pair['actor_location'].drawn_weapon_index = None
    pair['actor_location'].facing_x = 1
    pair['actor_location'].facing_y = 0
    db.session.commit()
    return {
        'lobby_id': pair['lobby'].id, 'location_id': pair['location'].id,
        'actor_id': pair['actor_location'].id, 'other_id': pair['target_location'].id,
        'sheet_id': pair['actor_character'].id, 'target_sheet_id': pair['target_character'].id,
        'user': pair['actor_user'], 'other_user': pair['target_user'],
    }


def combat_url(case):
    return f"/lobbies/{case['lobby_id']}/locations/{case['location_id']}/combat"


def action(client, case, auth_headers, payload):
    return client.post(combat_url(case) + '/action', headers=auth_headers(case['user']),
                       json={'location_character_id': case['actor_id'], **payload})


def pay_next_turn(client, case, auth_headers):
    response = client.post(combat_url(case) + '/end_turn',
        headers=auth_headers(case['other_user']),
        json={'location_character_id': case['other_id']})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def invariant_snapshot(case, action_id):
    db.session.expire_all()
    actor = db.session.get(LocationCharacter, case['actor_id'])
    sheet = db.session.get(LobbyCharacter, case['sheet_id'])
    row = db.session.get(DeferredCombatAction, action_id)
    return {
        'ap': actor.action_points_current, 'mp': actor.movement_points_current,
        'free': actor.free_actions_current, 'drawn': actor.drawn_weapon_index,
        'aim_target': actor.aimed_target_character_id,
        'aim_weapon': actor.aimed_weapon_index, 'aim_bonus': actor.aim_accuracy_bonus,
        'data': deepcopy(sheet.data), 'sheet_revision': sheet.revision,
        'row_status': row.status, 'row_remaining': row.remaining_action_points,
        'row_payload': deepcopy(row.payload), 'row_revision': row.revision,
        'messages': [item[0] for item in db.session.query(ChatMessage.id).order_by(ChatMessage.id).all()],
    }


def start_draw(client, case, auth_headers):
    action_id = 'ad-draw'
    response = action(client, case, auth_headers, {
        'action_key': 'draw_weapon', 'weapon_index': 0, 'pending_action_id': action_id,
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['pending_action'] is True
    row = db.session.get(DeferredCombatAction, action_id)
    assert row.status == 'paying'
    assert row.remaining_action_points == 2
    assert row.payload['action_key'] == 'draw_weapon'
    assert row.payload['weapon_index'] == 0
    actor = db.session.get(LocationCharacter, case['actor_id'])
    assert actor.action_points_current == 0
    assert actor.drawn_weapon_index is None
    meta = actor.character.data['health']['combatMeta']['pendingAction']
    assert meta['total_action_points'] == 4
    return action_id


@pytest.mark.parametrize('phase', ['paying', 'ready', 'completed'])
def test_gm_removes_deferred_row_without_refund_or_replaying_effect(client, deferred_case, auth_headers, phase):
    case = deferred_case
    action_id = start_draw(client, case, auth_headers)
    if phase != 'paying':
        pay_next_turn(client, case, auth_headers)
    if phase == 'completed':
        response = action(client, case, auth_headers, {
            'action_key': 'draw_weapon', 'resume_pending_action_id': action_id,
        })
        assert response.status_code == 200, response.get_json()
    before = invariant_snapshot(case, action_id)
    for _ in range(2):
        response = client.post(combat_url(case) + '/deferred-action/cancel',
            headers=auth_headers(case['user']),
            json={'location_character_id': case['actor_id'], 'action_id': action_id})
        assert response.status_code == 200, response.get_json()
        actor = next(item for item in response.get_json()['characters']
                     if item['location_character_id'] == case['actor_id'])
        assert not actor.get('deferred_action')
        after = invariant_snapshot(case, action_id)
        assert after['ap'] == before['ap']
        assert after['drawn'] == before['drawn']
        assert after['row_status'] == ('completed' if phase == 'completed' else 'cancelled')
        meta = after['data']['health']['combatMeta']
        assert 'pendingAction' not in meta and 'completedPendingActionId' not in meta
    response = action(client, case, auth_headers, {
        'action_key': 'draw_weapon', 'resume_pending_action_id': action_id,
    })
    assert response.status_code == 409, response.get_json()


@pytest.mark.parametrize('finish', ['end', 'remove'])
@pytest.mark.parametrize('phase', ['paying', 'ready'])
def test_late_resume_after_real_invalidation_cannot_charge_or_draw(client, deferred_case, auth_headers, finish, phase):
    case = deferred_case
    original = deepcopy(db.session.get(LobbyCharacter, case['sheet_id']).data)
    action_id = start_draw(client, case, auth_headers)
    if phase == 'ready':
        state = pay_next_turn(client, case, auth_headers)
        assert state['current_location_character_id'] == case['actor_id']
        assert state['current_character']['deferred_action']['status'] == 'ready'
        assert db.session.get(LocationCharacter, case['actor_id']).action_points_current == 3
        assert db.session.get(DeferredCombatAction, action_id).remaining_action_points == 0
    paid = invariant_snapshot(case, action_id)
    assert paid['row_status'] == phase
    assert paid['data']['inventory'] == original['inventory']
    assert paid['drawn'] is None

    if finish == 'end':
        CombatService.end_combat(case['location_id'], case['user']['id'])
        assert LocationCombatState.query.filter_by(location_id=case['location_id']).one().status == 'idle'
    else:
        CombatService.remove_combat_participant(case['location_id'], case['user']['id'], case['actor_id'])
        assert case['actor_id'] not in LocationCombatState.query.filter_by(location_id=case['location_id']).one().turn_order
    cancelled = invariant_snapshot(case, action_id)
    assert cancelled['row_status'] == 'cancelled'
    assert cancelled['row_remaining'] == paid['row_remaining']
    assert cancelled['row_payload'] == paid['row_payload']
    assert cancelled['data']['inventory'] == original['inventory']
    assert cancelled['data']['weapons'] == original['weapons']
    assert cancelled['drawn'] is None
    assert cancelled['data'].get('activeWeaponIndex') is None
    meta = cancelled['data']['health']['combatMeta']
    assert 'pendingAction' not in meta
    assert 'completedPendingActionId' not in meta

    for _ in range(2):
        response = action(client, case, auth_headers, {
            'action_key': 'draw_weapon', 'weapon_index': 0,
            'resume_pending_action_id': action_id,
        })
        assert response.status_code == 409, response.get_json()
        assert invariant_snapshot(case, action_id) == cancelled
    assert DeferredCombatAction.query.filter_by(id=action_id, status='completed').count() == 0


def start_paid_aim(client, case, auth_headers):
    actor = db.session.get(LocationCharacter, case['actor_id'])
    actor.action_points_current = 0  # Только исходное условие до начала оплаты.
    actor.drawn_weapon_index = 0
    db.session.commit()
    action_id = 'ad-aim'
    response = action(client, case, auth_headers, {
        'action_key': 'aim', 'weapon_index': 0,
        'target_character_id': case['target_sheet_id'], 'pending_action_id': action_id,
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['pending_action'] is True
    row = db.session.get(DeferredCombatAction, action_id)
    assert row.status == 'paying'
    assert row.remaining_action_points == 1
    assert row.payload['target_character_id'] == case['target_sheet_id']
    state = pay_next_turn(client, case, auth_headers)
    assert state['current_location_character_id'] == case['actor_id']
    db.session.expire_all()
    row = db.session.get(DeferredCombatAction, action_id)
    actor = db.session.get(LocationCharacter, case['actor_id'])
    assert row.status == 'ready'
    assert row.remaining_action_points == 0
    assert actor.action_points_current == 4
    assert actor.aimed_target_character_id is None
    return action_id


@pytest.mark.parametrize('already_ready',[False,True],ids=['paying','ready'])
def test_turn_start_cancels_hidden_target_before_payment_and_cannot_restore_action(client,deferred_case,auth_headers,already_ready):
    from app.models import LocationObject
    case = deferred_case
    actor = db.session.get(LocationCharacter,case['actor_id'])
    actor.action_points_current = 0
    actor.drawn_weapon_index = 0
    db.session.commit()
    action_id = 'corner-aim'
    response = action(client,case,auth_headers,{
        'action_key':'aim','weapon_index':0,'target_character_id':case['target_sheet_id'],
        'pending_action_id':action_id,
    })
    assert response.status_code == 200,response.get_json()
    if already_ready:
        pay_next_turn(client,case,auth_headers)
        row = db.session.get(DeferredCombatAction,action_id)
        assert row.status == 'ready'
        actor = db.session.get(LocationCharacter,case['actor_id'])
        assert actor.action_points_current == 4
        response = client.post(combat_url(case)+'/end_turn',headers=auth_headers(case['user']),
            json={'location_character_id':case['actor_id']})
        assert response.status_code == 200,response.get_json()
    # Противник ушёл за угол до начала следующего хода действующего персонажа.
    target = db.session.get(LocationCharacter,case['other_id'])
    target.pos_x,target.pos_y = 3,2
    wall = LocationObject(location_id=case['location_id'],type='wall',name='Угол',tile_x=2,tile_y=1,
        properties={'rotation':1.5707963267948966,'dimensions':{'width':3,'depth':.2,'height':3}})
    db.session.add(wall)
    db.session.commit()
    actor = db.session.get(LocationCharacter,case['actor_id'])
    assert CombatService._cover_analysis(case['location_id'],actor,target)['grade'] == 'full'
    pay_next_turn(client,case,auth_headers)
    db.session.expire_all()
    actor = db.session.get(LocationCharacter,case['actor_id'])
    row = db.session.get(DeferredCombatAction,action_id)
    assert row.status == 'cancelled'
    assert actor.action_points_current == 5  # Новая оплата не списана; старая не возвращена.
    meta = actor.character.data['health']['combatMeta']
    assert 'pendingAction' not in meta and 'completedPendingActionId' not in meta
    # Даже восстановив условия в этот же ход, старое действие завершить нельзя.
    target = db.session.get(LocationCharacter,case['other_id'])
    target.pos_x,target.pos_y = 2,1
    db.session.delete(wall)
    db.session.commit()
    response = action(client,case,auth_headers,{'action_key':'aim','resume_pending_action_id':action_id})
    assert response.status_code == 409,response.get_json()
    db.session.expire_all()
    assert db.session.get(DeferredCombatAction,action_id).status == 'cancelled'
    assert db.session.get(LocationCharacter,case['actor_id']).action_points_current == 5


def add_replacement(case):
    replacement = LobbyCharacter(lobby_id=case['lobby_id'], owner_id=case['user']['id'],
        name='Другая цель', data={'health': {'current': 700, 'max': 700, 'effects': []}})
    db.session.add(replacement)
    db.session.flush()
    db.session.add(LocationCharacter(location_id=case['location_id'], character_id=replacement.id,
        controlled_by=case['user']['id'], pos_x=3, pos_y=1))
    db.session.commit()
    return replacement.id


@pytest.mark.parametrize('redirect', [False, True], ids=['original-request', 'tampered-target'])
def test_missing_saved_target_rejects_repeated_resume_without_spending_or_retargeting(client, deferred_case, auth_headers, redirect):
    case = deferred_case
    replacement_id = add_replacement(case)
    action_id = start_paid_aim(client, case, auth_headers)
    removed = client.delete(
        f"/lobbies/{case['lobby_id']}/locations/{case['location_id']}/characters/{case['target_sheet_id']}",
        headers=auth_headers(case['user']))
    assert removed.status_code == 200, removed.get_json()
    assert db.session.get(LocationCharacter, case['other_id']) is None
    before = invariant_snapshot(case, action_id)
    replacement_before = deepcopy(db.session.get(LobbyCharacter, replacement_id).data)
    assert before['row_payload']['target_character_id'] == case['target_sheet_id']
    assert before['row_status'] == 'ready'
    for attempt in range(2):
        payload = {'action_key': 'aim', 'weapon_index': 0,
                   'resume_pending_action_id': action_id}
        if redirect:
            payload['target_character_id'] = replacement_id
        response = action(client, case, auth_headers, payload)
        assert response.status_code == (400 if attempt == 0 else 409), response.get_json()
        after = invariant_snapshot(case, action_id)
        assert after['row_status'] == 'cancelled'
        assert after['ap'] == before['ap']
        assert after['aim_target'] is None and after['aim_bonus'] == before['aim_bonus']
        assert after['data']['inventory'] == before['data']['inventory']
        assert after['row_payload'] == before['row_payload']
        meta = after['data']['health']['combatMeta']
        assert 'pendingAction' not in meta and 'completedPendingActionId' not in meta
        assert db.session.get(LobbyCharacter, replacement_id).data == replacement_before
    assert before['aim_target'] is None
    assert DeferredCombatAction.query.filter_by(id=action_id, status='completed').count() == 0


def test_saved_target_wins_over_redirect_and_paid_aim_completes_only_once(client, deferred_case, auth_headers):
    case = deferred_case
    replacement_id = add_replacement(case)
    action_id = start_paid_aim(client, case, auth_headers)
    before = invariant_snapshot(case, action_id)
    response = action(client, case, auth_headers, {
        'action_key': 'aim', 'weapon_index': 0, 'target_character_id': replacement_id,
        'resume_pending_action_id': action_id,
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['action'] == 'aim'
    assert response.get_json()['aim']['target_character_id'] == case['target_sheet_id']
    completed = invariant_snapshot(case, action_id)
    assert completed['row_status'] == 'completed'
    assert completed['row_payload'] == before['row_payload']
    assert completed['ap'] == before['ap'] == 4
    assert completed['aim_target'] == case['target_sheet_id'] != replacement_id
    assert completed['aim_bonus'] == 1
    assert completed['data']['inventory'] == before['data']['inventory']
    assert completed['data']['weapons'] == before['data']['weapons']
    for _ in range(2):
        repeated = action(client, case, auth_headers, {
            'action_key': 'aim', 'resume_pending_action_id': action_id,
            'target_character_id': replacement_id,
        })
        assert repeated.status_code == 409, repeated.get_json()
        assert invariant_snapshot(case, action_id) == completed
    assert DeferredCombatAction.query.filter_by(id=action_id, status='completed').count() == 1
