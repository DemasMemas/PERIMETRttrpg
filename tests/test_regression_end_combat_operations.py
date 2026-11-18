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
                        DeferredCombatAction, LobbyCharacter, Location, LocationCharacter,
                        LocationCombatState)
from app.services.character_interaction import CharacterInteractionService
from app.services.combat import CombatService
from app.services.health import apply_health_maximums
from test_ammo_loading import loading_case, request as loading_request
from test_magazine_installation import case
from test_consumable_use import pair
from test_grenade_rules import grenade_game
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
    current = CombatService.get_state(pair['location'].id, pair['actor_user']['id'])
    if current['current_location_character_id'] == pair['actor_location'].id:
        ended = client.post(combat_url(pair) + '/end_turn', headers=auth_headers(pair['actor_user']),
                            json={'location_character_id': pair['actor_location'].id})
        assert ended.status_code == 200, ended.get_json()
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


def second_location_with_same_character(game):
    actor = game['actor_location']
    other = Location(lobby_id=game['lobby'].id, name='Другая сцена', world_tile_x=1, world_tile_z=0)
    participant = LobbyCharacter(lobby_id=game['lobby'].id, owner_id=game['actor_user']['id'], name='Местный')
    db.session.add_all([other, participant])
    db.session.flush()
    copy = LocationCharacter(location_id=other.id, character_id=actor.character_id, pos_x=3, pos_y=4)
    local = LocationCharacter(location_id=other.id, character_id=participant.id, pos_x=5, pos_y=4)
    db.session.add_all([copy, local])
    db.session.flush()
    return other, copy, local


def test_explosion_releases_grenadier_only_in_the_participating_battle(client, grenade_game, auth_headers):
    from app.services.character_rules import explosion_occurred, meta

    game = grenade_game
    actor = game['actor_location']
    other, _, local = second_location_with_same_character(game)
    second = f"/lobbies/{game['lobby'].id}/locations/{other.id}/combat"
    response = client.post(second + '/start', headers=auth_headers(game['actor_user']),
                           json={'location_character_ids': [local.id]})
    assert response.status_code == 200, response.get_json()
    meta(actor)['grenadirMustExplode'] = True

    explosion_occurred(other.id)
    assert meta(local)['combatExplosionOccurred'] is True
    assert 'combatExplosionOccurred' not in meta(actor)
    assert meta(actor)['grenadirMustExplode'] is True

    explosion_occurred(actor.location_id)
    assert meta(actor)['combatExplosionOccurred'] is True
    assert 'grenadirMustExplode' not in meta(actor)


@pytest.mark.parametrize('phase', ['paying', 'ready'])
def test_battles_in_two_locations_keep_placements_and_restore_saved_action(client, grenade_game, auth_headers, phase):
    game = grenade_game
    actor = game['actor_location']
    sheet = actor.character
    gm_headers = auth_headers(game['actor_user'])
    other, copy, local = second_location_with_same_character(game)
    actor.action_points_current = 2
    db.session.commit()

    identity = 'shared-sheet-action'
    started = post_action(client, game, auth_headers, action_key='narrative_action', action_points=6,
        narrative_action_name='Осмотреть механизм', narrative_roll_required=False, pending_action_id=identity)
    assert started.status_code == 200, started.get_json()
    assert started.get_json()['pending_action'] is True
    if phase == 'ready':
        pay(client, game, auth_headers)
    # Состояние старого сохранения после второго боя. Запись оплаты сохранилась.
    data = deepcopy(sheet.data)
    data['health']['combatMeta'].pop('pendingAction', None)
    data['health']['combatMeta'].pop('completedPendingActionId', None)
    sheet.data = data
    db.session.commit()

    second = f"/lobbies/{game['lobby'].id}/locations/{other.id}/combat"
    refused = client.post(second + '/start', headers=gm_headers, json={'location_character_ids': [copy.id, local.id]})
    body_error(refused, 400)
    assert game['location'].name in refused.json['error']['message']
    started = client.post(second + '/start', headers=gm_headers, json={'location_character_ids': [local.id]})
    assert started.status_code == 200, started.get_json()
    body_error(client.post(second + f'/participants/{copy.id}', headers=gm_headers, json={}), 400)
    equipment = f'/lobbies/characters/{sheet.id}/equipment-action'
    assert client.get(equipment, headers=gm_headers).json['combat']['location_id'] == game['location'].id
    snapshot = client.get(combat_url(game), headers=gm_headers).json
    actual = next(person for person in snapshot['characters'] if person['location_character_id'] == actor.id)
    assert actual['deferred_action']['status'] == phase
    assert actual['deferred_action']['id'] == identity
    if phase == 'ready':
        blocked = post_action(client, game, auth_headers, action_key='narrative_action', action_points=6,
            narrative_action_name='Другое действие', narrative_roll_required=False, pending_action_id='other-action')
        body_error(blocked, 400)
        reason = blocked.json['error']['message']
        assert all(value in reason for value in (game['location'].name, 'раунд 2', 'Осмотреть механизм'))
    ended = client.post(second + '/end', headers=gm_headers, json={})
    assert ended.json['status'] == 'idle'
    assert {person['location_character_id'] for person in ended.json['characters']} == {copy.id, local.id}
    assert db.session.get(DeferredCombatAction, identity).status == phase

    if phase == 'paying':
        pay(client, game, auth_headers)
    resumed = post_action(client, game, auth_headers, action_key='narrative_action', resume_pending_action_id=identity)
    assert resumed.status_code == 200, resumed.get_json()
    assert resumed.json['narrative_action']['action_points'] == 6
    assert resumed.json['character']['action_points_current'] == 1
    assert db.session.get(DeferredCombatAction, identity).status == 'completed'
    assert client.post(combat_url(game) + '/end', headers=gm_headers, json={}).json['turn_order'] == []
    assert client.get(equipment, headers=gm_headers).json == {'combat': None}
    assert (copy.pos_x, copy.pos_y) == (3, 4)
    assert (actor.pos_x, actor.pos_y) == (2.1, 5.2)
    assert client.post(second + '/start', headers=gm_headers, json={'location_character_ids': [copy.id]}).status_code == 200


@pytest.mark.parametrize('legacy', [False, True])
def test_sniper_choice_and_cover_obligation_belong_to_their_battle(grenade_game, legacy):
    from app.services.character_rules import meta
    from app.services.exceptions import ConflictError
    from app.services.sniper_trauma import choices, decide, enqueue

    game = grenade_game
    actor, target = game['actor_location'], game['target_location']
    gm = game['lobby'].gm_id
    other, copy, local = second_location_with_same_character(game)
    enqueue(actor, target, {'area': 'chest', 'roll': 10, 'chance_roll': 100}, 20, 'chest', True, True)
    meta(actor)['sniperOldCover'] = {'id': None, 'location_id': actor.location_id, 'pending': True, 'expires_after_round': 2}
    if legacy:
        meta(actor)['sniperTraumaChoices'][0].pop('location_id')
        meta(actor)['sniperOldCover'].pop('location_id')
    db.session.commit()

    CombatService.start_combat(other.id, gm, [local.id])
    snapshot = CombatService.get_state(other.id, gm)
    observer = next(entry for entry in snapshot['characters'] if entry['location_character_id'] == copy.id)
    assert observer['sniper_trauma_choices'] == [] and observer['sniper_position'] is None
    choice = choices(actor.character.data)[0]
    with pytest.raises(ConflictError, match=actor.location.name):
        decide(copy, gm, {'id': choice['id'], 'index': 1})
    assert CombatService.end_combat(other.id, gm)['status'] == 'idle'
    assert meta(actor)['sniperOldCover']['pending']
    assert choices(actor.character.data)[0]['id'] == choice['id']
    with pytest.raises(ConflictError, match=actor.location.name):
        CombatService.end_combat(actor.location_id, gm)
    decide(actor, gm, {'id': choice['id'], 'index': 1})
    assert CombatService.end_combat(actor.location_id, gm)['status'] == 'idle'
    assert 'sniperOldCover' not in meta(actor)


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
