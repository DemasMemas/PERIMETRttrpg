from copy import deepcopy

import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import ConsumableUse, DeferredCombatAction
from app.services.character_interaction import CharacterInteractionService
from app.services.combat import CombatService
from app.services.exceptions import ValidationError
from test_consumable_use import pair, apply_self
from test_atomic_inventory import treatment_payload, treat


def selection(pair, *, patient=False, consent=None):
    return {
        'source': {'path': ['inventory', 'backpack', 0],
                   'snapshot': deepcopy(pair['actor_character'].data['inventory']['backpack'][0])},
        'application': {'kind': 'self', 'actionPoints': 4},
        'target_character_id': pair['target_character' if patient else 'actor_character'].id,
        'treatment_request_id': consent.id if consent else None,
    }


def base_url(pair):
    return f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat"


def start(client, pair, auth_headers, chosen=None, *, cost=6, user=None):
    pair['actor_location'].action_points_current = 2
    db.session.commit()
    return client.post(base_url(pair) + '/spend', headers=auth_headers(user or pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_points': cost,
        'allow_deferred': True, 'pending_action_id': 'medical-test', 'pending_action_label': 'Bandage',
        'consumable_selection': chosen or selection(pair),
    })


def pay(client, pair, auth_headers):
    response = client.post(base_url(pair) + '/end_turn', headers=auth_headers(pair['target_user']),
                           json={'location_character_id': pair['target_location'].id})
    assert response.status_code == 200, response.json
    return response.json


def prepare(client, pair, auth_headers, user=None):
    return client.get(base_url(pair) + '/deferred-consumable/medical-test', headers=auth_headers(user or pair['actor_user']))


def completion_payload(pair):
    data = pair['actor_character'].data_snapshot()
    data['inventory']['backpack'][0]['quantity'] -= 1
    data['health']['current'] = 90
    row = db.session.get(DeferredCombatAction, 'medical-test')
    return {'actor_updates': {'data': data}, 'consumable_use': {
        'id': row.id, 'deferred_action_id': row.id, 'source': deepcopy(row.payload['selection']['source']),
        'combat_location_id': pair['location'].id, 'side_effects': {},
    }}


def test_reconnect_recovers_procedure_and_atomic_completion_once(client, pair, auth_headers, monkeypatch):
    monkeypatch.setattr('app.services.deferred_consumable.random.randint', lambda *_: 17)
    chosen = selection(pair)
    assert start(client, pair, auth_headers, chosen).status_code == 200
    assert prepare(client, pair, auth_headers).status_code == 409
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2
    assert 'medicine_roll' not in db.session.get(DeferredCombatAction, 'medical-test').payload
    state = pay(client, pair, auth_headers)
    summary = state['current_character']['deferred_action']
    assert summary['client_executable'] is True
    assert summary['server_executable'] is False
    assert 'selection' not in summary  # No medical details in the public combat summary.
    db.session.expire_all()
    response = prepare(client, pair, auth_headers)
    assert response.status_code == 200, response.json
    assert response.json['selection'] == chosen
    assert response.json['medicine_roll'] == 17
    monkeypatch.setattr('app.services.deferred_consumable.random.randint', lambda *_: 1)
    assert prepare(client, pair, auth_headers).json['medicine_roll'] == 17
    wrong_endpoint = client.post(base_url(pair) + '/action', headers=auth_headers(pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_key': 'attack',
        'resume_pending_action_id': 'medical-test',
    })
    assert wrong_endpoint.status_code == 400
    assert db.session.get(DeferredCombatAction, 'medical-test').status == 'ready'
    assert response.json['target_data']['_revision'] == pair['actor_character'].revision
    payload = completion_payload(pair)
    response = apply_self(client, pair, auth_headers, payload)
    assert response.status_code == 200, response.json
    assert response.json['data']['health']['current'] == 90
    assert pair['actor_location'].action_points_current == 1
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 1
    assert 'completedPendingActionId' not in pair['actor_character'].data['health']['combatMeta']
    assert db.session.get(DeferredCombatAction, 'medical-test').status == 'completed'
    assert ConsumableUse.query.count() == 1
    assert apply_self(client, pair, auth_headers, payload).status_code == 409


def test_patient_lock_is_saved_before_turn_changes_and_released_on_completion(client, pair, auth_headers):
    consent, _ = treatment_payload(pair)
    chosen = selection(pair, patient=True, consent=consent)
    assert start(client, pair, auth_headers, chosen).status_code == 200
    assert consent.status == 'in_progress'
    assert CharacterInteractionService.movement_locked(pair['target_location'].id)
    pay(client, pair, auth_headers)
    assert CharacterInteractionService.movement_locked(pair['target_location'].id)
    assert prepare(client, pair, auth_headers).status_code == 200
    payload = completion_payload(pair)
    payload.update({'actor_location_character_id': pair['actor_location'].id,
                    'interaction_request_id': consent.id,
                    'target_revision': pair['target_character'].revision,
                    'health': deepcopy(pair['target_character'].data['health'])})
    payload['health']['current'] = 90
    response = treat(client, pair, auth_headers(pair['actor_user']), payload)
    assert response.status_code == 200, response.json
    assert consent.status == 'completed'
    assert not CharacterInteractionService.movement_locked(pair['target_location'].id)


@pytest.mark.parametrize('failure', ['commit', 'version', 'destination', 'source', 'operation_id'])
def test_failure_preserves_paid_action_and_inventory(client, pair, auth_headers, monkeypatch, failure):
    assert start(client, pair, auth_headers).status_code == 200
    pay(client, pair, auth_headers)
    payload = completion_payload(pair)
    before = deepcopy(pair['actor_character'].data)
    if failure == 'commit':
        def fail():
            db.session.flush()
            raise ValidationError('Injected commit failure')
        monkeypatch.setattr(db.session, 'commit', fail)
    elif failure == 'version':
        payload['actor_updates']['data']['_revision'] = 0
    elif failure == 'destination':
        payload['consumable_use']['combat_location_id'] = 999999
    elif failure == 'source':
        payload['consumable_use']['source']['snapshot']['quantity'] = 1
    else:
        payload['consumable_use']['id'] = 'another-use'
    response = apply_self(client, pair, auth_headers, payload)
    assert response.status_code in {400, 409}, response.json
    assert pair['actor_character'].data == before
    assert db.session.get(DeferredCombatAction, 'medical-test').status == 'ready'
    assert ConsumableUse.query.count() == 0


@pytest.mark.parametrize('change', ['item', 'injury_removed', 'injury_changed', 'target_moved', 'consent'])
def test_recovery_revalidates_selection_and_consent(client, pair, auth_headers, change):
    consent, _ = treatment_payload(pair)
    target = pair['target_character']
    data = deepcopy(target.data)
    effect = {'id': 'wound-1', 'type': 'untreated_wound', 'area': 'leftArm', 'source': 'injury'}
    data['health']['effects'] = [effect]
    target.data = data
    db.session.commit()
    chosen = selection(pair, patient=True, consent=consent)
    chosen['application'] = {'kind': 'wound', 'effect': deepcopy(effect), 'actionPoints': 4}
    assert start(client, pair, auth_headers, chosen).status_code == 200
    pay(client, pair, auth_headers)
    if change == 'item':
        pair['actor_character'].data['inventory']['backpack'][0]['id'] = 'replacement'
        flag_modified(pair['actor_character'], 'data')
    elif change.startswith('injury'):
        target.data['health']['effects'] = [] if change == 'injury_removed' else [{**effect, 'type': 'fracture_fixed'}]
        flag_modified(target, 'data')
    elif change == 'target_moved':
        pair['target_location'].pos_x = 20
    else:
        consent.status = 'cancelled'
    db.session.commit()
    paid_ap = pair['actor_location'].action_points_current
    assert prepare(client, pair, auth_headers).status_code in {400, 403, 409}
    assert db.session.get(DeferredCombatAction, 'medical-test').status == 'cancelled'
    assert pair['actor_location'].action_points_current == paid_ap
    meta = pair['actor_character'].data['health']['combatMeta']
    assert 'pendingAction' not in meta and 'completedPendingActionId' not in meta
    assert consent.status == 'cancelled'
    assert not CharacterInteractionService.movement_locked(pair['target_location'].id)
    assert prepare(client,pair,auth_headers).status_code == 409
    assert ConsumableUse.query.count() == 0


@pytest.mark.parametrize('finish', ['cancel', 'end', 'remove'])
def test_cancellation_releases_patient_without_consuming(client, pair, auth_headers, finish):
    consent, _ = treatment_payload(pair)
    assert start(client, pair, auth_headers, selection(pair, patient=True, consent=consent)).status_code == 200
    if finish == 'cancel':
        response = client.post(base_url(pair) + '/deferred-action/cancel', headers=auth_headers(pair['actor_user']),
                               json={'location_character_id': pair['actor_location'].id, 'action_id': 'medical-test'})
        assert response.status_code == 200, response.json
    elif finish == 'end':
        CombatService.end_combat(pair['location'].id, pair['actor_user']['id'])
    else:
        CombatService.remove_combat_participant(pair['location'].id, pair['actor_user']['id'], pair['actor_location'].id)
    assert db.session.get(DeferredCombatAction, 'medical-test').status == 'cancelled'
    assert consent.status == 'cancelled'
    assert not CharacterInteractionService.movement_locked(pair['target_location'].id)
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2


def test_unauthorized_player_cannot_spend_or_read_procedure(client, pair, auth_headers):
    assert start(client, pair, auth_headers, user=pair['target_user']).status_code == 403
    assert pair['actor_location'].action_points_current == 2
    assert DeferredCombatAction.query.count() == 0
    assert start(client, pair, auth_headers).status_code == 200
    pay(client, pair, auth_headers)
    assert prepare(client, pair, auth_headers, user=pair['target_user']).status_code == 403


def test_long_payment_never_applies_treatment_early(client, pair, auth_headers):
    assert start(client, pair, auth_headers, cost=13).status_code == 200
    for remaining in (6, 1):
        pay(client, pair, auth_headers)
        assert db.session.get(DeferredCombatAction, 'medical-test').remaining_action_points == remaining
        assert prepare(client, pair, auth_headers).status_code == 409
        assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2
    pay(client, pair, auth_headers)
    assert prepare(client, pair, auth_headers).status_code == 200


def test_gm_can_finish_procedure_begun_by_player(client, pair, auth_headers):
    pair['lobby'].gm_id = pair['target_user']['id']
    db.session.commit()
    consent, _ = treatment_payload(pair)
    assert start(client, pair, auth_headers, selection(pair, patient=True, consent=consent)).status_code == 200
    pay(client, pair, auth_headers)
    assert prepare(client, pair, auth_headers, user=pair['target_user']).status_code == 200
    payload = completion_payload(pair)
    payload.update({'actor_location_character_id': pair['actor_location'].id,
                    'interaction_request_id': consent.id,
                    'target_revision': pair['target_character'].revision,
                    'health': deepcopy(pair['target_character'].data['health'])})
    response = treat(client, pair, auth_headers(pair['target_user']), payload)
    assert response.status_code == 200, response.json
    assert consent.status == 'completed'


def test_failed_initial_commit_does_not_leave_debt_or_patient_lock(client, pair, auth_headers, monkeypatch):
    consent, _ = treatment_payload(pair)
    pair['actor_location'].action_points_current = 2
    db.session.commit()
    before = deepcopy(pair['actor_character'].data)
    def fail():
        db.session.flush()
        raise ValidationError('Injected payment failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    response = client.post(base_url(pair) + '/spend', headers=auth_headers(pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_points': 6,
        'allow_deferred': True, 'pending_action_id': 'medical-test',
        'consumable_selection': selection(pair, patient=True, consent=consent),
    })
    assert response.status_code == 400
    assert pair['actor_location'].action_points_current == 2
    assert consent.status == 'accepted'
    assert pair['actor_character'].data == before
    assert DeferredCombatAction.query.count() == 0


def test_blood_packet_is_bound_as_secondary_item(client, pair, auth_headers):
    actor = pair['actor_character']
    actor.data['inventory']['backpack'].append({'id': 'packet', 'name': 'Пакет крови', 'quantity': 1})
    flag_modified(actor, 'data')
    db.session.commit()
    chosen = selection(pair)
    chosen['application'] = {'kind': 'blood_type_test', 'target': 'packet', 'entry': {
        'path': ['inventory', 'backpack', 1], 'item': deepcopy(actor.data['inventory']['backpack'][1]),
    }}
    assert start(client, pair, auth_headers, chosen).status_code == 200
    pay(client, pair, auth_headers)
    assert prepare(client, pair, auth_headers).status_code == 200
    actor.data['inventory']['backpack'][1]['id'] = 'other-packet'
    flag_modified(actor, 'data')
    db.session.commit()
    assert prepare(client, pair, auth_headers).status_code == 409


def test_ready_limb_restoration_rejects_already_healed_limb(client, pair, auth_headers):
    actor = pair['actor_character']
    actor.data['health']['zones']['leftArm']['current'] = 0
    flag_modified(actor, 'data')
    db.session.commit()
    chosen = selection(pair)
    chosen['application'] = {'kind': 'injury', 'treatmentMode': 'restore_limb',
                             'effect': {'type': 'damaged_zone', 'area': 'leftArm'}}
    assert start(client, pair, auth_headers, chosen).status_code == 200
    pay(client, pair, auth_headers)
    actor.data['health']['zones']['leftArm']['current'] = 1
    flag_modified(actor, 'data')
    db.session.commit()
    assert prepare(client, pair, auth_headers).status_code == 409
