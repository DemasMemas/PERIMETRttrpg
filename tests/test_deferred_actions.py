from copy import deepcopy

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import DeferredCombatAction, LobbyCharacter, LocationCharacter, LocationCombatState, ChatMessage
from app.services.combat import CombatService
from app.services.exceptions import ValidationError
from test_character_interactions import _setup_pair


@pytest.fixture
def case(client, create_user, auth_headers):
    pair = _setup_pair(client, create_user, auth_headers)
    pair['actor_location'].action_points_current = 2
    db.session.commit()
    return {
        'lobby': pair['lobby'].id, 'location': pair['location'].id,
        'actor': pair['actor_location'].id, 'other': pair['target_location'].id,
        'actor_sheet': pair['actor_character'].id,
        'user': pair['actor_user'], 'other_user': pair['target_user'],
    }


def action(client, case, auth_headers, payload, *, other=False):
    return client.post(f"/lobbies/{case['lobby']}/locations/{case['location']}/combat/action",
                       headers=auth_headers(case['other_user'] if other else case['user']), json=payload)


def start(client, case, auth_headers, cost=6):
    response = action(client, case, auth_headers, {
        'location_character_id': case['actor'], 'action_key': 'narrative_action',
        'action_points': cost, 'narrative_action_name': 'Repair the door',
        'narrative_roll_required': False, 'pending_action_id': 'durable-test',
    })
    assert response.status_code == 200, response.json
    assert response.json['pending_action'] is True
    return response.json


def pay(client, case, auth_headers):
    response = client.post(f"/lobbies/{case['lobby']}/locations/{case['location']}/combat/end_turn",
                           headers=auth_headers(case['other_user']), json={'location_character_id': case['other']})
    assert response.status_code == 200, response.json
    return response.json


def resume(client, case, auth_headers, *, other=False, **extra):
    return action(client, case, auth_headers, {
        'location_character_id': case['actor'], 'action_key': 'narrative_action',
        'resume_pending_action_id': 'durable-test', **extra,
    }, other=other)


def test_reconnect_resumes_saved_arguments_once_without_repaying(client, case, auth_headers):
    start(client, case, auth_headers)
    assert ChatMessage.query.filter_by(username='Действие').count() == 0
    assert db.session.get(DeferredCombatAction, 'durable-test').remaining_action_points == 4
    state = pay(client, case, auth_headers)
    assert state['current_character']['deferred_action']['status'] == 'ready'
    assert state['current_character']['deferred_action']['server_executable'] is True
    db.session.remove()
    state = CombatService.get_state(case['location'], case['user']['id'])
    assert state['current_character']['deferred_action']['id'] == 'durable-test'
    response = resume(client, case, auth_headers,
                      action_key='attack', narrative_action_name='Tampered', action_points=0,
                      target_character_id=999999)
    assert response.status_code == 200, response.json
    assert response.json['action'] == 'narrative_action'
    assert response.json['narrative_action']['name'] == 'Repair the door'
    assert response.json['narrative_action']['action_points'] == 6
    assert response.json['character']['action_points_current'] == 1
    assert response.json['character']['deferred_action'] is None
    assert db.session.get(DeferredCombatAction, 'durable-test').status == 'completed'
    assert ChatMessage.query.filter_by(username='Действие').count() == 1
    assert resume(client, case, auth_headers).status_code == 409
    assert ChatMessage.query.filter_by(username='Действие').count() == 1


def test_drawing_weapon_waits_for_full_payment(client, case, auth_headers):
    character = db.session.get(LobbyCharacter, case['actor_sheet'])
    character.data = {**character.data, 'weapons': [{'id': 'test-gun', 'name': 'Test gun', 'ergonomics': 0}]}
    actor = db.session.get(LocationCharacter, case['actor'])
    actor.action_points_current = 0
    actor.drawn_weapon_index = None
    db.session.commit()

    started = action(client, case, auth_headers, {
        'location_character_id': case['actor'], 'action_key': 'draw_weapon',
        'weapon_index': 0, 'pending_action_id': 'draw-test',
    })
    assert started.status_code == 200, started.json
    assert started.json['pending_action'] is True
    assert db.session.get(LocationCharacter, case['actor']).drawn_weapon_index is None
    assert character.data.get('activeWeaponIndex') is None

    state = pay(client, case, auth_headers)
    assert state['current_character']['deferred_action']['status'] == 'ready'
    completed = action(client, case, auth_headers, {
        'location_character_id': case['actor'], 'action_key': 'draw_weapon',
        'resume_pending_action_id': 'draw-test',
    })
    assert completed.status_code == 200, completed.json
    assert completed.json['draw_weapon']['weapon_index'] == 0
    assert db.session.get(LocationCharacter, case['actor']).drawn_weapon_index == 0
    assert character.data['activeWeaponIndex'] == 0
    assert db.session.get(DeferredCombatAction, 'draw-test').status == 'completed'


def test_large_cost_spans_several_turns_before_becoming_ready(client, case, auth_headers):
    start(client, case, auth_headers, cost=13)
    for remaining in (6, 1):
        state = pay(client, case, auth_headers)
        assert state['current_location_character_id'] == case['other']
        row = db.session.get(DeferredCombatAction, 'durable-test')
        assert row.status == 'paying'
        assert row.remaining_action_points == remaining
        assert resume(client, case, auth_headers).status_code == 409
    state = pay(client, case, auth_headers)
    assert state['current_location_character_id'] == case['actor']
    assert state['current_character']['action_points_current'] == 4
    assert resume(client, case, auth_headers).status_code == 200


def test_unauthorized_resume_and_cancel_cannot_claim_paid_action(client, case, auth_headers):
    start(client, case, auth_headers)
    pay(client, case, auth_headers)
    assert resume(client, case, auth_headers, other=True).status_code == 403
    response = client.post(f"/lobbies/{case['lobby']}/locations/{case['location']}/combat/deferred-action/cancel",
                           headers=auth_headers(case['other_user']),
                           json={'location_character_id': case['actor'], 'action_id': 'durable-test'})
    assert response.status_code == 403
    assert db.session.get(DeferredCombatAction, 'durable-test').status == 'ready'


def test_cancel_during_payment_does_not_refund_or_charge_next_turn(client, case, auth_headers):
    start(client, case, auth_headers)
    response = client.post(f"/lobbies/{case['lobby']}/locations/{case['location']}/combat/deferred-action/cancel",
                           headers=auth_headers(case['user']),
                           json={'location_character_id': case['actor'], 'action_id': 'durable-test'})
    assert response.status_code == 200, response.json
    assert db.session.get(LocationCharacter, case['actor']).action_points_current == 0
    assert db.session.get(DeferredCombatAction, 'durable-test').status == 'cancelled'
    assert pay(client, case, auth_headers)['current_character']['action_points_current'] == 5
    assert resume(client, case, auth_headers).status_code == 409


def test_resume_failure_rolls_back_claim_and_result(client, case, auth_headers, monkeypatch):
    start(client, case, auth_headers)
    pay(client, case, auth_headers)
    before = deepcopy(db.session.get(LobbyCharacter, case['actor_sheet']).data)
    events = []
    monkeypatch.setattr('app.lobbies.socketio.emit', lambda *args, **kwargs: events.append(args))
    def fail():
        db.session.flush()
        raise ValidationError('Injected failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    response = resume(client, case, auth_headers)
    assert response.status_code == 400
    assert db.session.get(DeferredCombatAction, 'durable-test').status == 'ready'
    assert db.session.get(LobbyCharacter, case['actor_sheet']).data == before
    assert ChatMessage.query.filter_by(username='Действие').count() == 0
    assert events == []


@pytest.mark.parametrize('finish', ['end', 'remove'])
def test_ending_combat_or_removing_actor_cancels_old_action(client, case, auth_headers, finish):
    start(client, case, auth_headers)
    if finish == 'end':
        CombatService.end_combat(case['location'], case['user']['id'])
    else:
        CombatService.remove_combat_participant(case['location'], case['user']['id'], case['actor'])
    assert db.session.get(DeferredCombatAction, 'durable-test').status == 'cancelled'
    meta = db.session.get(LobbyCharacter, case['actor_sheet']).data['health']['combatMeta']
    assert 'pendingAction' not in meta
    assert 'completedPendingActionId' not in meta


def test_sheet_save_cannot_change_pending_payment(client, case, auth_headers):
    start(client, case, auth_headers)
    character = db.session.get(LobbyCharacter, case['actor_sheet'])
    snapshot = character.data_snapshot()
    snapshot['health']['combatMeta']['pendingAction']['remaining_action_points'] = 0
    snapshot['health']['combatMeta']['completedPendingActionId'] = 'durable-test'
    response = client.put(f"/lobbies/characters/{character.id}", headers=auth_headers(case['user']), json={'data': snapshot})
    assert response.status_code == 200, response.json
    assert character.data['health']['combatMeta']['pendingAction']['remaining_action_points'] == 4
    assert 'completedPendingActionId' not in character.data['health']['combatMeta']


def test_duplicate_start_and_old_unsaved_resume_rejected(client, case, auth_headers):
    start(client, case, auth_headers)
    response = action(client, case, auth_headers, {
        'location_character_id': case['actor'], 'action_key': 'narrative_action',
        'pending_action_id': 'durable-test',
    })
    assert response.status_code == 409
    assert resume(client, case, auth_headers, resume_pending_action_id='legacy-unknown').status_code == 400
    assert db.session.get(DeferredCombatAction, 'durable-test').remaining_action_points == 4


def test_competing_database_claims_have_one_winner(client, case, auth_headers):
    start(client, case, auth_headers)
    pay(client, case, auth_headers)
    with Session(db.engine) as first, Session(db.engine) as second:
        a = first.get(DeferredCombatAction, 'durable-test')
        b = second.get(DeferredCombatAction, 'durable-test')
        a.status = 'completed'
        first.commit()
        b.status = 'completed'
        with pytest.raises(StaleDataError):
            second.flush()
        second.rollback()


def test_weapon_replacement_rejects_resume_without_consuming_paid_action(client, case, auth_headers):
    start(client, case, auth_headers)
    pay(client, case, auth_headers)
    row = db.session.get(DeferredCombatAction, 'durable-test')
    row.payload = {**row.payload, 'weapon_index': 0, '_bindings': {'weapon': {'id': 'old'}}}
    character = db.session.get(LobbyCharacter, case['actor_sheet'])
    character.data = {**character.data, 'weapons': [{'id': 'new'}]}
    db.session.commit()
    response = resume(client, case, auth_headers)
    assert response.status_code == 400, response.json
    assert row.status == 'cancelled'


def test_deferred_table_migration_round_trip():
    from importlib import import_module
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text
    migration = import_module('migrations.versions.c0d1e2f3a4b5_deferred_combat_actions')
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE location_characters (id INTEGER PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert 'deferred_combat_actions' in inspect(connection).get_table_names()
            assert len(inspect(connection).get_foreign_keys('deferred_combat_actions')) == 1
            migration.downgrade()
            assert inspect(connection).get_table_names() == ['location_characters']
    engine.dispose()
