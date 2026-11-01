"""Адресные границы подготовки кубика и finalize. Все случаи NOT_RUN.

Второй сценарий пересекает запросы после commit победителя, до публикации.
Это не проверка двух незавершённых write-транзакций общего StaticPool.
"""
from copy import deepcopy
from types import SimpleNamespace
import sys

import pytest

import app.services.combat as combat_module
import app.services.deferred_consumable as deferred_module
import app.services.medical_procedure as medical_module
from app.extensions import db, socketio
from app.models import ConsumableUse, DeferredCombatAction, LobbyCharacter, LocationCharacter, LocationCombatState
from app.models.lobby_templates import LobbyItemTemplate
from test_consumable_use import pair
from test_medical_procedure import install_item, selected_bleeding, payload, apply


def combat_url(pair):
    return f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat"


def data(pair):
    db.session.expire_all()
    return deepcopy(db.session.get(LobbyCharacter, pair['actor_character'].id).data)


def payment_snapshot(pair, operation):
    actor_data = data(pair)
    actor = db.session.get(LocationCharacter, pair['actor_location'].id)
    other = db.session.get(LocationCharacter, pair['target_location'].id)
    state = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    row = db.session.get(DeferredCombatAction, operation)
    return (actor_data, actor.action_points_current, other.action_points_current,
            state.round_number, state.current_location_character_id,
            row.status, row.remaining_action_points, row.revision, deepcopy(row.payload))


def test_late_missing_bandage_never_creates_medical_roll_or_applies_paid_procedure(client, pair, auth_headers, monkeypatch):
    bleed = {'id': 'completion-bleed', 'type': 'bleeding_external_medium', 'area': 'rightLeg', 'source': 'shot'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.',
                                     quantity=2, medicine=10, effects=[bleed])
    pair['actor_location'].action_points_current = 2
    db.session.commit()
    medical_draws, bleeding_draws = [], []

    def medical_draw(low, high):
        # Счётчик-сентинел: проверка отсутствия ниже не позволит принять этот вызов.
        assert (low, high) == (1, 20)
        assert medical_draws == []
        medical_draws.append(20)
        return 20

    def bleeding_draw(low, high):
        assert sys._getframe(1).f_code is combat_module.CombatService._resolve_bleeding_check.__code__
        assert (low, high) == (1, 20)
        assert bleeding_draws == []
        bleeding_draws.append(20)
        return 20

    medical_rng = SimpleNamespace(randint=medical_draw)
    monkeypatch.setattr(medical_module, 'random', medical_rng)
    monkeypatch.setattr(deferred_module, 'random', medical_rng)
    monkeypatch.setattr(combat_module, 'random', SimpleNamespace(randint=bleeding_draw))
    application = selected_bleeding(profile, bleed, 1)
    selection = {'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
                 'application': deepcopy(application), 'target_character_id': pair['actor_character'].id,
                 'treatment_request_id': None, 'server_authoritative': True}
    operation = 'completion-late'
    started = client.post(combat_url(pair) + '/spend', headers=auth_headers(pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_points': 6, 'allow_deferred': True,
        'pending_action_id': operation, 'pending_action_label': 'Два бинта', 'consumable_selection': selection})
    assert started.status_code == 200, started.get_json()
    row = db.session.get(DeferredCombatAction, operation)
    assert row.status == 'paying' and row.remaining_action_points == 4
    assert row.payload['selection']['source']['snapshot']['quantity'] == 2
    assert 'medicine_roll' not in row.payload and medical_draws == []
    assert bleeding_draws == [20]
    assert pair['actor_location'].action_points_current == 0

    # Отдельный настоящий save меняет текущий предмет, а не сохранённую selection.
    changed = pair['actor_character'].data_snapshot()
    changed['inventory']['backpack'][0]['quantity'] = 1
    changed_response = client.put(f"/lobbies/characters/{pair['actor_character'].id}",
                                  headers=auth_headers(pair['actor_user']), json={'data': changed})
    assert changed_response.status_code == 200, changed_response.get_json()
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 1
    assert db.session.get(DeferredCombatAction, operation).payload['selection'] == selection
    ended = client.post(combat_url(pair) + '/end_turn', headers=auth_headers(pair['target_user']),
                        json={'location_character_id': pair['target_location'].id})
    assert ended.status_code == 200, ended.get_json()
    row = db.session.get(DeferredCombatAction, operation)
    assert row.status == 'cancelled' and row.remaining_action_points == 4
    assert pair['actor_location'].action_points_current == 5
    assert 'medicine_roll' not in row.payload and medical_draws == []
    assert bleeding_draws == [20]
    assert row.payload['selection'] == selection
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 1
    assert any(e.get('id') == bleed['id'] and e['type'] == bleed['type'] for e in data(pair)['health']['effects'])
    assert not any(e['type'] == 'untreated_wound' for e in data(pair)['health']['effects'])
    assert ConsumableUse.query.count() == 0

    # Stale snapshot должен отклоняться настоящими recovery/completion, без обхода привязки.
    recovered = client.get(combat_url(pair) + '/deferred-consumable/' + operation,
                           headers=auth_headers(pair['actor_user']))
    assert recovered.status_code == 409, recovered.get_json()
    body = payload(pair, item, application, operation=operation, combat=True)
    body['deferred_action_id'] = operation
    before_completion = payment_snapshot(pair, operation)
    rejected = apply(client, pair, auth_headers, body)
    # Ход уже начался, но отменённую процедуру завершить нельзя.
    assert rejected.status_code == 409, rejected.get_json()
    assert payment_snapshot(pair, operation) == before_completion
    assert ConsumableUse.query.count() == 0
    assert medical_draws == []
    assert 'medicine_roll' not in db.session.get(DeferredCombatAction, operation).payload

    # Восстановление предмета не возрождает отменённую процедуру.
    restored = pair['actor_character'].data_snapshot()
    restored['inventory']['backpack'][0] = deepcopy(item)
    restored_response = client.put(f"/lobbies/characters/{pair['actor_character'].id}",
                                   headers=auth_headers(pair['actor_user']), json={'data': restored})
    assert restored_response.status_code == 200, restored_response.get_json()
    assert data(pair)['inventory']['backpack'][0] == item
    row = db.session.get(DeferredCombatAction, operation)
    assert row.status == 'cancelled' and row.remaining_action_points == 4
    assert pair['actor_location'].action_points_current == 5
    assert medical_draws == []
    prepared = client.get(combat_url(pair) + '/deferred-consumable/' + operation,
                          headers=auth_headers(pair['actor_user']))
    assert prepared.status_code == 409, prepared.get_json()
    assert apply(client, pair, auth_headers, body).status_code == 409
    assert 'medicine_roll' not in db.session.get(DeferredCombatAction, operation).payload
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 2
    assert ConsumableUse.query.count() == 0
    assert pair['actor_location'].action_points_current == 5


@pytest.fixture
def owner_observer(app, pair):
    from app.sockets import auth, character
    for event, handler in {'connect': auth.handle_connect, 'disconnect': auth.handle_disconnect,
                           'authenticate': auth.handle_authenticate,
                           'join_character': character.handle_join_character}.items():
        socketio.on_event(event, handler)
    connection = socketio.test_client(app)
    try:
        connection.emit('authenticate', {'token': pair['actor_user']['token'], 'lobby_id': pair['lobby'].id})
        connection.get_received()
        joined = connection.emit('join_character', {'token': pair['actor_user']['token'],
                                                    'character_id': pair['actor_character'].id}, callback=True)
        assert joined['ok'] is True
        connection.get_received()
        yield connection
    finally:
        if connection.is_connected():
            connection.disconnect()


def updates(connection, character_id):
    return [event['args'][0] for event in connection.get_received()
            if event['name'] == 'character_data_updated' and event['args'][0]['character_id'] == character_id]


def test_contender_finalize_after_winner_commit_before_publish_cannot_undo_or_emit_twice(app, client, pair, auth_headers, owner_observer, monkeypatch):
    # Ограничение существующей memory fixture проверяется, engine не создаётся.
    assert type(db.engine.pool).__name__ == 'StaticPool'
    assert db.engine.url.database in (None, '', ':memory:')
    profile = {'editor_version': 1, 'direct': {'action_points_cost': 1, 'radiation_delta': 3},
               'modifiers': [], 'effects': []}
    template = LobbyItemTemplate(lobby_id=pair['lobby'].id, created_by=pair['actor_user']['id'],
                                name='Состав завершения', category='consumable',
                                attributes={'uses': 2, 'consumable': profile})
    db.session.add(template)
    db.session.flush()
    item = {'id': 'completion-item', 'templateId': template.id + 1_000_000, 'name': 'Состав',
            'category': 'consumable', 'quantity': 1, 'uses': 2, 'maxUses': 2}
    value = data(pair)
    value['inventory']['backpack'] = [item]
    value['health']['radiation'] = 0
    pair['actor_character'].data = value
    db.session.commit()
    operation = 'completion-interleave'
    selection = {'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
                 'application': {'kind': 'self', 'actionPoints': 0},
                 'target_character_id': pair['actor_character'].id, 'treatment_request_id': None,
                 'server_authoritative': True, 'server_operation': 'general'}
    started = client.post(combat_url(pair) + '/spend', headers=auth_headers(pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_points': 3, 'allow_deferred': True,
        'pending_action_id': operation, 'pending_action_label': 'Одна операция', 'consumable_selection': selection})
    assert started.status_code == 200, started.get_json()
    assert db.session.get(DeferredCombatAction, operation).status == 'ready'
    assert pair['actor_location'].action_points_current == 2
    assert data(pair)['inventory']['backpack'][0]['uses'] == 2
    assert data(pair)['health']['radiation'] == 0
    assert ConsumableUse.query.count() == 0
    character_id = pair['actor_character'].id
    location_id = pair['location'].id
    actor_id = pair['actor_location'].id
    request_headers = auth_headers(pair['actor_user'])
    finalize_url = f'/lobbies/characters/{character_id}/general-consumable'
    body = {'operation_id': operation, 'deferred_action_id': operation,
            'source': deepcopy(selection['source']), 'application': deepcopy(selection['application']),
            'combat_location_id': location_id, 'actor_location_character_id': actor_id}
    # spend публикует combat-состояние, не приватное character_data_updated.
    assert updates(owner_observer, character_id) == []
    real_commit = db.session.commit
    winner_session = db.session()
    trace = []
    armed = True

    def commit_with_contender():
        nonlocal armed
        real_commit()  # Настоящие claim, эффект, расход и receipt уже сохранены.
        if not armed:
            return
        armed = False
        trace.append('winner_committed')
        assert updates(owner_observer, character_id) == []
        # Только после commit: две write-транзакции одного StaticPool не имитируются.
        with app.app_context():
            assert db.session() is not winner_session
            trace.append('contender_entered')
            contender = app.test_client()
            lost = contender.post(finalize_url, headers=request_headers, json=deepcopy(body))
            assert lost.status_code == 409, lost.get_json()
            trace.append('contender_409')
        assert updates(owner_observer, character_id) == []

    with monkeypatch.context() as controlled:
        controlled.setattr(db.session, 'commit', commit_with_contender)
        won = client.post(finalize_url, headers=request_headers, json=body)
    assert won.status_code == 200, won.get_json()
    trace.append('winner_returned')
    assert trace == ['winner_committed', 'contender_entered', 'contender_409', 'winner_returned']
    db.session.expire_all()
    actor = db.session.get(LobbyCharacter, character_id)
    expected = actor.data_snapshot()
    assert expected['health']['radiation'] == 3
    assert expected['inventory']['backpack'][0]['uses'] == 1
    assert expected['inventory']['backpack'][0]['id'] == 'completion-item'
    assert db.session.get(LocationCharacter, actor_id).action_points_current == 2
    assert db.session.get(DeferredCombatAction, operation).status == 'completed'
    receipts = ConsumableUse.query.all()
    assert len(receipts) == 1
    assert receipts[0].id == operation
    assert receipts[0].actor_character_id == receipts[0].target_character_id == character_id
    events = updates(owner_observer, character_id)
    assert len(events) == 1
    assert events[0]['source'] == 'general_consumable'
    assert events[0]['updates']['data'] == expected
