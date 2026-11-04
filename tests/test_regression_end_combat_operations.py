"""Окончание боя во время зарядки/лечения: paying и ready; все случаи NOT_RUN."""
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from helpers.journal_assertions import assert_same_game_state
import sys
from types import SimpleNamespace

import pytest

import app.services.combat as combat_module
import app.services.deferred_consumable as deferred_module
import app.services.medical_procedure as medical_module
from app.extensions import db
from app.models import (CharacterInteractionRequest, ChatMessage, ConsumableUse,
                        DeferredCombatAction, LobbyCharacter, LocationCharacter,
                        LocationCombatState)
from app.services.character_interaction import CharacterInteractionService
from app.services.combat import CombatService
from app.services.health import apply_health_maximums
from test_ammo_loading import loading_case, request as loading_request
from test_magazine_installation import case
from test_consumable_use import pair
from test_medical_procedure import install_item, selected_bleeding, payload, apply


def combat_url(pair):
    return f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat"


def forbid_rng(monkeypatch):
    def forbidden(low, high):
        pytest.fail(f'Неожиданный RNG вне разрешённого этапа: {low}, {high}')
    for module in (combat_module, deferred_module, medical_module):
        monkeypatch.setattr(module, 'random', SimpleNamespace(randint=forbidden))


@contextmanager
def rng_stage(monkeypatch, module, expected):
    sequence = deque(expected)
    def draw(low, high):
        assert sequence, (module.__name__, low, high)
        caller, expected_low, expected_high, value = sequence.popleft()
        assert sys._getframe(1).f_code.co_name == caller
        assert sys._getframe(1).f_globals['__name__'] == module.__name__
        assert (low, high) == (expected_low, expected_high)
        return value
    with monkeypatch.context() as controlled:
        controlled.setattr(module, 'random', SimpleNamespace(randint=draw))
        yield
        assert not sequence


def canonical_fixture(pair):
    # Только до измеряемого start. Нет pending/ready row, эффектов результата или выдачи оплаты.
    for role in ('actor', 'target'):
        sheet = pair[role + '_character']
        data = deepcopy(sheet.data)
        apply_health_maximums(data)
        sheet.data = data
        CombatService._prepare_character_for_turn(pair[role + '_location'])
        CombatService._sync_location_effects_from_character(pair[role + '_location'])
    pair['actor_location'].action_points_current = 2
    db.session.commit()


def record(model):
    return {column.name: deepcopy(getattr(model, column.name))
            for column in model.__table__.columns}


def full_snapshot(pair):
    db.session.expire_all()
    state = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    return {
        'clock': (pair['lobby'].game_day, pair['lobby'].game_time_minutes),
        'sheets': [record(row) for row in LobbyCharacter.query
                   .filter_by(lobby_id=pair['lobby'].id).order_by(LobbyCharacter.id)],
        'location_characters': [record(row) for row in LocationCharacter.query
                   .filter_by(location_id=pair['location'].id).order_by(LocationCharacter.id)],
        'state': record(state),
        'actions': [record(row) for row in DeferredCombatAction.query.order_by(DeferredCombatAction.id)],
        'receipts': [record(row) for row in ConsumableUse.query.order_by(ConsumableUse.id)],
        'consents': [record(row) for row in CharacterInteractionRequest.query.order_by(CharacterInteractionRequest.id)],
        'messages': [record(row) for row in ChatMessage.query
                     .filter_by(lobby_id=pair['lobby'].id).order_by(ChatMessage.id)],
    }


def post_action(client, pair, auth_headers, **body):
    return client.post(combat_url(pair) + '/action', headers=auth_headers(pair['actor_user']),
        json={'location_character_id': pair['actor_location'].id, **body})


def row_state(pair, identity, phase):
    db.session.expire_all()
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == phase
    assert row.remaining_action_points == (4 if phase == 'paying' else 0)
    assert pair['actor_location'].action_points_current == (0 if phase == 'paying' else 1)
    return row


def pay(client, pair, auth_headers):
    response = client.post(combat_url(pair) + '/end_turn',
        headers=auth_headers(pair['target_user']),
        json={'location_character_id': pair['target_location'].id})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['current_location_character_id'] == pair['actor_location'].id


def body_error(response, status):
    assert response.status_code == status, response.get_json()
    error = response.get_json()['error']
    assert error['code'] == status
    assert isinstance(error['message'], str) and error['message']


def reject_unchanged(pair, operation, status):
    before = full_snapshot(pair)
    response = operation()
    body_error(response, status)
    assert_same_game_state(full_snapshot(pair), before)


def end_battle(client, pair, auth_headers, identity):
    actor = pair['actor_location']
    before = full_snapshot(pair)
    ap, free = actor.action_points_current, actor.free_actions_current
    original = {role: deepcopy(pair[role + '_character'].data) for role in ('actor', 'target')}
    row = db.session.get(DeferredCombatAction, identity)
    saved_payload = deepcopy(row.payload)
    debt = row.remaining_action_points
    response = client.post(combat_url(pair) + '/end', headers=auth_headers(pair['actor_user']), json={})
    assert response.status_code == 200, response.get_json()
    state = response.get_json()
    assert state['status'] == 'idle'
    assert state['current_location_character_id'] is None
    assert state['turn_order'] == []
    db.session.expire_all()
    assert (pair['lobby'].game_day, pair['lobby'].game_time_minutes) == before['clock']
    assert actor.action_points_current == ap
    assert actor.free_actions_current == free
    # Нулевые ОП здесь штатное завершение боя, не требование оставить боевой запас.
    assert actor.movement_points_current == 0
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'cancelled'
    assert row.remaining_action_points == debt
    assert row.payload == saved_payload
    assert db.session.get(ConsumableUse, identity) is None
    for role in ('actor', 'target'):
        data = pair[role + '_character'].data
        assert data['inventory'] == original[role]['inventory']
        assert data.get('weapons') == original[role].get('weapons')
        assert data['health']['current'] == original[role]['health']['current']
        assert data['health']['zones'] == original[role]['health']['zones']
        assert data['health']['effects'] == original[role]['health']['effects']
        meta = data['health']['combatMeta']
        assert 'pendingAction' not in meta
        assert 'completedPendingActionId' not in meta
        assert not CharacterInteractionService.movement_locked(pair[role + '_location'].id)
    return saved_payload


def restart_battle(client, pair, auth_headers, monkeypatch, identity):
    clock = (pair['lobby'].game_day, pair['lobby'].game_time_minutes)
    # Две реальные инициативы; initiator_first гарантирует ход врача/заряжающего.
    with rng_stage(monkeypatch, combat_module,
                   [('start_combat', 1, 20, 10), ('start_combat', 1, 20, 10)]):
        response = client.post(combat_url(pair) + '/start',
            headers=auth_headers(pair['actor_user']), json={
                'location_character_ids': [pair['actor_location'].id, pair['target_location'].id],
                'initiator_location_character_id': pair['actor_location'].id, 'initiator_first': True})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['status'] == 'active'
    assert response.get_json()['current_location_character_id'] == pair['actor_location'].id
    assert pair['actor_location'].action_points_current == 5
    assert (pair['lobby'].game_day, pair['lobby'].game_time_minutes) == clock
    assert db.session.get(DeferredCombatAction, identity).status == 'cancelled'
    assert db.session.get(ConsumableUse, identity) is None


def request_consent(client, pair, auth_headers):
    response = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/character-interactions",
        headers=auth_headers(pair['actor_user']), json={
            'actor_location_character_id': pair['actor_location'].id,
            'target_character_id': pair['target_character'].id, 'kind': 'treatment'})
    assert response.status_code == 201, response.get_json()
    identity = response.get_json()['id']
    assert response.get_json()['status'] == 'pending'
    accepted = client.post(f"/lobbies/{pair['lobby'].id}/character-interactions/{identity}/response",
        headers=auth_headers(pair['target_user']), json={'decision': 'accept'})
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted.get_json()['status'] == 'accepted'
    return db.session.get(CharacterInteractionRequest, identity)


def spend_medical(client, pair, auth_headers, identity, selection):
    return client.post(combat_url(pair) + '/spend', headers=auth_headers(pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_points': 6,
        'allow_deferred': True, 'pending_action_id': identity,
        'pending_action_label': 'Два бинта пациенту', 'consumable_selection': selection})



@pytest.mark.parametrize('phase', ['paying', 'ready'])
def test_end_combat_cancels_actual_ammo_loading_without_transfer_or_old_resume(
        client, loading_case, auth_headers, monkeypatch, phase):
    pair = loading_case
    identity = 'end-ammo-' + phase
    forbid_rng(monkeypatch)
    # Точный тариф6AP/1СД: рюкзак2+футляр2, подготовка ammo/зарядка2AP/1СД.
    initial = deepcopy(pair['actor_character'].data)
    magazine = initial['equipment']['belt']['pouches'][0]['contents'].pop(0)
    initial['inventory']['backpack'] = [{
        'id': 'end-ammo-box', 'name': 'Футляр', 'category': 'container',
        'attributes': {'access_action_points': 2}, 'contents': [magazine]}]
    pair['path'] = ['inventory', 'backpack', 0, 'contents', 0]
    pair['actor_character'].data = initial
    canonical_fixture(pair)
    original = deepcopy(pair['actor_character'].data)
    loading = loading_request(pair)
    started = post_action(client, pair, auth_headers, action_key='load_ammunition',
                          loading=loading, pending_action_id=identity)
    assert started.status_code == 200, started.get_json()
    assert started.get_json()['pending_action'] is True
    assert started.get_json()['reload_weapon'] is None
    row_state(pair, identity, 'paying')
    assert pair['actor_location'].free_actions_current == 0
    assert pair['actor_character'].data['inventory'] == original['inventory']
    assert pair['actor_character'].data['equipment'] == original['equipment']
    assert pair['actor_character'].data['weapons'] == original['weapons']
    if phase == 'ready':
        pay(client, pair, auth_headers)
    row = row_state(pair, identity, phase)
    assert row.payload['action_key'] == 'load_ammunition'
    assert row.payload['loading'] == loading
    assert pair['actor_character'].data['inventory'] == original['inventory']
    assert pair['actor_character'].data['equipment'] == original['equipment']
    saved = end_battle(client, pair, auth_headers, identity)
    assert pair['actor_character'].data['equipment'] == original['equipment']
    for _ in range(2):
        reject_unchanged(pair, lambda: post_action(client, pair, auth_headers,
            action_key='load_ammunition', resume_pending_action_id=identity,
            loading={'quantity': 999}), 409)
    restart_battle(client, pair, auth_headers, monkeypatch, identity)
    reject_unchanged(pair, lambda: post_action(client, pair, auth_headers,
        action_key='load_ammunition', resume_pending_action_id=identity), 409)
    assert db.session.get(DeferredCombatAction, identity).payload == saved
    assert pair['actor_character'].data['inventory'] == original['inventory']
    assert pair['actor_character'].data['equipment'] == original['equipment']
    # Новый бой законно даёт5AP/1СД; новая операция6AP допускается с новымID.
    new_id = identity + '-new'
    fresh = post_action(client, pair, auth_headers, action_key='load_ammunition',
                        loading=loading_request(pair), pending_action_id=new_id)
    assert fresh.status_code == 200, fresh.get_json()
    new = db.session.get(DeferredCombatAction, new_id)
    assert new.status == 'paying'
    assert new.remaining_action_points == 1
    assert pair['actor_location'].action_points_current == 0
    assert pair['actor_location'].free_actions_current == 0
    assert db.session.get(DeferredCombatAction, identity).status == 'cancelled'
    assert pair['actor_character'].data['inventory'] == original['inventory']
    assert pair['actor_character'].data['equipment'] == original['equipment']
    assert ConsumableUse.query.count() == 0


@pytest.mark.parametrize('phase', ['paying', 'ready'])
def test_end_combat_cancels_server_treatment_unlocks_patient_and_rejects_old_completion(
        client, pair, auth_headers, monkeypatch, phase):
    identity = 'end-medical-' + phase
    forbid_rng(monkeypatch)
    bleed = {'id': 'end-bleed', 'type': 'bleeding_external_medium',
             'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.',
                                    quantity=2, medicine=10)
    target = deepcopy(pair['target_character'].data)
    target['health']['current'] = 700
    target['health']['effects'] = [bleed]
    pair['target_character'].data = target
    actor = deepcopy(pair['actor_character'].data)
    actor['health']['current'] = 700
    pair['actor_character'].data = actor
    canonical_fixture(pair)
    consent = request_consent(client, pair, auth_headers)
    application = selected_bleeding(profile, bleed, 1)
    selection = {
        'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
        'application': deepcopy(application), 'target_character_id': pair['target_character'].id,
        'treatment_request_id': consent.id, 'server_authoritative': True,
    }
    original_inventory = deepcopy(pair['actor_character'].data['inventory'])
    original_target = deepcopy(pair['target_character'].data['health'])
    started = spend_medical(client, pair, auth_headers, identity, selection)
    assert started.status_code == 200, started.get_json()
    row = row_state(pair, identity, 'paying')
    assert row.payload['required_action_points'] == 6
    assert row.payload['action_key'] == 'consumable_use'
    assert row.payload['selection'] == selection
    assert 'medicine_roll' not in row.payload
    assert db.session.get(CharacterInteractionRequest, consent.id).status == 'in_progress'
    assert CharacterInteractionService.movement_locked(pair['target_location'].id)
    assert not CharacterInteractionService.movement_locked(pair['actor_location'].id)
    assert pair['actor_character'].data['inventory'] == original_inventory
    assert pair['target_character'].data['health']['effects'] == original_target['effects']
    if phase == 'ready':
        # Платёж завершает ход пациента с законной проверкой кровопотери,
        # затем единожды создаёт медицинский кубик. Это ещё не лечение.
        with rng_stage(monkeypatch, combat_module, [('_resolve_bleeding_check', 1, 20, 20)]), \
             rng_stage(monkeypatch, deferred_module, [('prepare_medicine_roll', 1, 20, 20)]):
            pay(client, pair, auth_headers)
    row = row_state(pair, identity, phase)
    if phase == 'ready':
        assert row.payload['medicine_roll'] == 20
    else:
        assert 'medicine_roll' not in row.payload
    assert CharacterInteractionService.movement_locked(pair['target_location'].id)
    assert pair['actor_character'].data['inventory'] == original_inventory
    assert pair['target_character'].data['health']['effects'] == original_target['effects']
    assert pair['target_character'].data['health']['current'] == 700
    assert pair['target_character'].data['health']['zones'] == original_target['zones']
    assert ConsumableUse.query.count() == 0
    completion = payload(pair, item, application, operation=identity,
                         target=pair['target_character'], combat=True, consent=consent)
    completion['deferred_action_id'] = identity
    saved = end_battle(client, pair, auth_headers, identity)
    assert db.session.get(CharacterInteractionRequest, consent.id).status == 'cancelled'
    assert pair['actor_character'].data['inventory'] == original_inventory
    assert pair['target_character'].data['health']['effects'] == original_target['effects']
    # Пока бой idle, сначала отказывает реальный medical combat context.
    for _ in range(2):
        reject_unchanged(pair, lambda: apply(client, pair, auth_headers, completion), 409)
        reject_unchanged(pair, lambda: client.get(
            combat_url(pair) + '/deferred-consumable/' + identity,
            headers=auth_headers(pair['actor_user'])), 409)
    restart_battle(client, pair, auth_headers, monkeypatch, identity)
    # В новом бою врач уже ходит, но старое согласие отменено:403, не новый roll.
    reject_unchanged(pair, lambda: apply(client, pair, auth_headers, completion), 403)
    reject_unchanged(pair, lambda: client.get(
        combat_url(pair) + '/deferred-consumable/' + identity,
        headers=auth_headers(pair['actor_user'])), 409)
    assert db.session.get(DeferredCombatAction, identity).payload == saved
    assert not CharacterInteractionService.movement_locked(pair['target_location'].id)
    # Новое согласие и новыйID разрешены; старые marker/receipt не оживают.
    new_consent = request_consent(client, pair, auth_headers)
    new_selection = {**deepcopy(selection), 'treatment_request_id': new_consent.id}
    new_id = identity + '-new'
    fresh = spend_medical(client, pair, auth_headers, new_id, new_selection)
    assert fresh.status_code == 200, fresh.get_json()
    new_row = db.session.get(DeferredCombatAction, new_id)
    assert new_row.status == 'paying'
    assert new_row.remaining_action_points == 1
    assert 'medicine_roll' not in new_row.payload
    assert pair['actor_location'].action_points_current == 0
    assert db.session.get(DeferredCombatAction, identity).status == 'cancelled'
    assert db.session.get(CharacterInteractionRequest, consent.id).status == 'cancelled'
    assert db.session.get(CharacterInteractionRequest, new_consent.id).status == 'in_progress'
    assert CharacterInteractionService.movement_locked(pair['target_location'].id)
    assert pair['actor_character'].data['inventory'] == original_inventory
    assert pair['target_character'].data['health']['effects'] == original_target['effects']
    assert ConsumableUse.query.count() == 0
