from copy import deepcopy

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import CharacterInteractionRequest, LocationCombatState
from app.models.location_object import LocationObject
from app.services.exceptions import ValidationError
from test_character_interactions import _setup_pair


@pytest.fixture
def pair(client, create_user, auth_headers):
    return _setup_pair(client, create_user, auth_headers)


def _money_request(client, pair, auth_headers, payload):
    return client.post(f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/character-interactions",
        headers=auth_headers(pair['actor_user']), json={'kind': 'trade',
            'actor_location_character_id': pair['actor_location'].id,
            'target_character_id': pair['target_character'].id, 'payload': payload})


def _respond_money(client, pair, auth_headers, request_id, decision='accept'):
    return client.post(f"/lobbies/{pair['lobby'].id}/character-interactions/{request_id}/response",
                      headers=auth_headers(pair['target_user']), json={'decision': decision})


def _set_money(pair):
    for key, value in [('actor_character', 1000), ('target_character', 500)]:
        data = deepcopy(pair[key].data)
        data['inventory']['money'] = value
        pair[key].data = data
    db.session.commit()


def test_money_only_exchange_requires_consent_and_cannot_repeat(client, pair, auth_headers):
    _set_money(pair)
    pending = _money_request(client, pair, auth_headers, {'actor_money': 250})
    assert pending.status_code == 201, pending.json
    assert pair['actor_character'].data['inventory']['money'] == 1000
    response = _respond_money(client, pair, auth_headers, pending.json['id'])
    assert response.status_code == 200, response.json
    assert response.json['result']['actor_to_target_money'] == 250
    assert pair['actor_character'].data['inventory']['money'] == 750
    assert pair['target_character'].data['inventory']['money'] == 750
    assert pair['actor_location'].action_points_current == 3
    again = _respond_money(client, pair, auth_headers, pending.json['id'])
    assert again.status_code == 404
    assert pair['actor_character'].data['inventory']['money'] == 750


def test_trade_combines_goods_and_money_and_uses_actual_item_names(client, pair, auth_headers):
    _set_money(pair)
    pending = _money_request(client, pair, auth_headers, {'actor_money': 100, 'target_money': 25,
        'target_offer': [{'item_id': 'target-water', 'path': ['inventory', 'backpack', 0], 'amount': 1,
                          'name': 'Неверное имя из клиента'}]})
    assert pending.status_code == 201, pending.json
    assert pending.json['payload']['target_offer'][0]['name'] == 'Вода'
    result = _respond_money(client, pair, auth_headers, pending.json['id'])
    assert result.status_code == 200, result.json
    assert pair['actor_character'].data['inventory']['money'] == 925
    assert pair['target_character'].data['inventory']['money'] == 575
    assert pair['actor_character'].data['inventory']['backpack'][-1]['id'] == 'target-water'


@pytest.mark.parametrize('amount', [-1, True, '50', .5, 1001])
def test_money_offer_rejects_invalid_amount_without_spending_anything(client, pair, auth_headers, amount):
    _set_money(pair)
    result = _money_request(client, pair, auth_headers, {'actor_money': amount})
    assert result.status_code == 400
    assert pair['actor_character'].data['inventory']['money'] == 1000
    assert pair['actor_location'].action_points_current == 5
    assert CharacterInteractionRequest.query.count() == 0


def test_trade_stale_money_or_items_does_not_change_either_character(client, pair, auth_headers):
    _set_money(pair)
    pending = _money_request(client, pair, auth_headers, {'actor_money': 100})
    data = deepcopy(pair['actor_character'].data)
    data['inventory']['money'] = 50
    pair['actor_character'].data = data
    db.session.commit()
    result = _respond_money(client, pair, auth_headers, pending.json['id'])
    assert result.status_code == 409, result.json
    assert pair['actor_character'].data['inventory']['money'] == 50
    assert pair['target_character'].data['inventory']['money'] == 500
    assert pair['actor_location'].action_points_current == 5
    assert db.session.get(CharacterInteractionRequest, pending.json['id']).status == 'pending'


def test_declined_money_transfer_and_failure_after_flush_are_atomic(client, pair, auth_headers, monkeypatch):
    _set_money(pair)
    pending = _money_request(client, pair, auth_headers, {'actor_money': 100})
    result = _respond_money(client, pair, auth_headers, pending.json['id'], 'decline')
    assert result.json['status'] == 'rejected'
    pending = _money_request(client, pair, auth_headers, {'actor_money': 100})
    monkeypatch.setattr(db.session, 'commit', fail_after_flush)
    result = _respond_money(client, pair, auth_headers, pending.json['id'])
    assert result.status_code == 400
    assert pair['actor_character'].data['inventory']['money'] == 1000
    assert pair['target_character'].data['inventory']['money'] == 500
    assert pair['actor_location'].action_points_current == 5


def container(pair, *, ground=False):
    obj = LocationObject(location_id=pair['location'].id, name='Box',
                         type='ground_item' if ground else 'container', tile_x=1, tile_y=2,
                         properties={'contents': [{'id': 'box-item', 'name': 'Water', 'quantity': 3}]})
    db.session.add(obj)
    db.session.commit()
    return obj


def transfer_payload(pair, obj, *, take=False):
    actor = pair['actor_character']
    return {
        'character_id': actor.id, 'character_revision': actor.revision,
        'object_revision': obj.revision, 'direction': 'to_character' if take else 'to_container',
        'item_path': ['contents', 0] if take else ['inventory', 'backpack', 0],
        'expected_item': deepcopy(obj.properties['contents'][0] if take else actor.data['inventory']['backpack'][0]),
        'amount': 1,
    }


def transfer(client, pair, obj, headers, payload):
    return client.post(f"/lobbies/{pair['lobby'].id}/locations/objects/{obj.id}/transfer",
                       headers=headers, json=payload)


@pytest.mark.parametrize('object_type', [
    'household_nightstand', 'household_wardrobe', 'household_dresser',
    'household_wall_shelf', 'household_storage_rack', 'household_refrigerator',
    'household_cooker', 'household_kitchen_cabinet', 'household_sink',
    'household_wood_stove', 'household_wood_crate', 'household_cardboard_box',
])
def test_household_storage_without_legacy_contents_supports_round_trip(
    client, pair, auth_headers, object_type,
):
    obj = container(pair)
    obj.type = object_type
    obj.properties = {}  # Already placed furniture does not have contents yet.
    db.session.commit()
    headers = auth_headers(pair['actor_user'])
    deposited = transfer(client, pair, obj, headers, transfer_payload(pair, obj))
    assert deposited.status_code == 200, deposited.json
    assert obj.properties['contents'][0]['quantity'] == 1
    taken = transfer(client, pair, obj, headers, transfer_payload(pair, obj, take=True))
    assert taken.status_code == 200, taken.json
    assert obj.properties['contents'] == []
    assert db.session.get(LocationObject, obj.id).type == object_type
    assert sum(item['quantity'] for item in pair['actor_character'].data['inventory']['backpack']) == 2


@pytest.mark.parametrize('take', [False, True])
def test_transfer_splits_stack_and_rejects_replay(client, pair, auth_headers, take):
    obj = container(pair)
    payload = transfer_payload(pair, obj, take=take)
    headers = auth_headers(pair['actor_user'])
    response = transfer(client, pair, obj, headers, payload)
    assert response.status_code == 200, response.json
    character_items = pair['actor_character'].data['inventory']['backpack']
    box_items = obj.properties['contents']
    source, destination = (box_items, character_items) if take else (character_items, box_items)
    assert source[0]['quantity'] == (2 if take else 1)
    assert destination[-1]['quantity'] == 1
    assert destination[-1]['id'] != payload['expected_item']['id']
    assert response.json['data']['_revision'] > payload['character_revision']
    assert response.json['object']['revision'] > payload['object_revision']
    assert transfer(client, pair, obj, headers, payload).status_code == 409
    assert source[0]['quantity'] == (2 if take else 1)


@pytest.mark.parametrize('fault', ['character_revision', 'object_revision', 'expected_item', 'locked', 'turn', 'distance'])
def test_invalid_transfer_changes_neither_side(client, pair, auth_headers, fault):
    obj = container(pair)
    payload = transfer_payload(pair, obj)
    if fault in {'character_revision', 'object_revision'}:
        payload[fault] -= 1
    elif fault == 'expected_item':
        payload[fault]['name'] = 'Different item'
    elif fault == 'locked':
        obj.properties = {**obj.properties, 'locked': True}
    elif fault == 'turn':
        LocationCombatState.query.one().current_location_character_id = pair['target_location'].id
    else:
        obj.tile_x = 20
    db.session.commit()
    before = deepcopy(pair['actor_character'].data), deepcopy(obj.properties)
    response = transfer(client, pair, obj, auth_headers(pair['actor_user']), payload)
    assert response.status_code in {400, 403, 409}, response.json
    assert (pair['actor_character'].data, obj.properties) == before


def test_transfer_cannot_use_other_players_inventory(client, pair, auth_headers):
    obj = container(pair)
    response = transfer(client, pair, obj, auth_headers(pair['target_user']), transfer_payload(pair, obj))
    assert response.status_code == 403
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2


def test_pickup_last_ground_item_by_player_deletes_model(client, pair, auth_headers):
    obj = container(pair, ground=True)
    object_id = obj.id
    LocationCombatState.query.one().status = 'idle'
    db.session.commit()
    payload = transfer_payload(pair, obj, take=True)
    payload.update(character_id=pair['target_character'].id,
                   character_revision=pair['target_character'].revision, amount=3)
    response = transfer(client, pair, obj, auth_headers(pair['target_user']), payload)
    assert response.status_code == 200, response.json
    assert response.json['deleted'] is True
    assert response.json['object'] is None
    assert db.session.get(LocationObject, object_id) is None
    assert pair['target_character'].data['inventory']['backpack'][-1]['id'] == 'box-item'


def test_transfer_filled_nested_container_preserves_contents(client, pair, auth_headers):
    obj = container(pair)
    nested = {'id': 'bag', 'name': 'Bag', 'quantity': 1, 'contents': [{'id': 'inside', 'quantity': 4}]}
    obj.properties = {'contents': [nested]}
    db.session.commit()
    response = transfer(client, pair, obj, auth_headers(pair['actor_user']), transfer_payload(pair, obj, take=True))
    assert response.status_code == 200, response.json
    assert pair['actor_character'].data['inventory']['backpack'][-1] == nested
    assert obj.properties['contents'] == []


def fail_after_flush():
    db.session.flush()
    raise ValidationError('Injected failure after both writes')


def test_transfer_rollback_after_flush_sends_no_events(client, pair, auth_headers, monkeypatch):
    obj = container(pair)
    payload = transfer_payload(pair, obj)
    before = deepcopy(pair['actor_character'].data), deepcopy(obj.properties)
    events = []
    monkeypatch.setattr('app.lobbies.socketio.emit', lambda *args, **kwargs: events.append(args))
    monkeypatch.setattr(db.session, 'commit', fail_after_flush)
    response = transfer(client, pair, obj, auth_headers(pair['actor_user']), payload)
    assert response.status_code == 400
    assert (pair['actor_character'].data, obj.properties) == before
    assert obj.revision == payload['object_revision']
    assert pair['actor_character'].revision == payload['character_revision']
    assert events == []


@pytest.mark.parametrize('fail', [False, True])
def test_drop_weapon_is_atomic_with_creation_and_wear(client, pair, auth_headers, monkeypatch, fail):
    actor = pair['actor_character']
    weapon = {'id': 'gun', 'name': 'Gun', 'category': 'weapon', 'quantity': 1, 'durability': 90}
    actor.data = {**actor.data, 'inventory': {'backpack': [weapon]}}
    db.session.commit()
    revision = actor.revision
    if fail:
        monkeypatch.setattr(db.session, 'commit', fail_after_flush)
    response = client.post(f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/drop-item",
                           headers=auth_headers(pair['actor_user']), json={
                               'character_id': actor.id, 'character_revision': revision,
                               'item_path': ['inventory', 'backpack', 0], 'expected_item': weapon, 'amount': 1,
                           })
    assert response.status_code == (400 if fail else 200), response.json
    if fail:
        assert LocationObject.query.count() == 0
        assert actor.data['inventory']['backpack'] == [weapon]
        assert actor.revision == revision
    else:
        assert actor.data['inventory']['backpack'] == []
        assert LocationObject.query.one().properties['contents'][0]['durability'] == 87


def test_object_optimistic_version_rejects_competing_database_session(pair):
    obj = container(pair)
    with Session(db.engine) as first, Session(db.engine) as second:
        a = first.get(LocationObject, obj.id)
        b = second.get(LocationObject, obj.id)
        a.properties = {'contents': []}
        first.commit()
        b.properties = {'contents': [{'id': 'duplicate'}]}
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()


def treatment_payload(pair, status='accepted'):
    request = CharacterInteractionRequest(
        location_id=pair['location'].id, kind='treatment', status=status,
        actor_location_character_id=pair['actor_location'].id,
        target_location_character_id=pair['target_location'].id,
        actor_user_id=pair['actor_user']['id'], target_user_id=pair['target_user']['id'], payload={},
    )
    db.session.add(request)
    db.session.commit()
    actor_data = pair['actor_character'].data_snapshot()
    actor_data['inventory']['backpack'][0]['quantity'] -= 1
    health = deepcopy(pair['target_character'].data['health'])
    health['current'] = 90
    return request, {
        'actor_location_character_id': pair['actor_location'].id,
        'actor_updates': {'data': actor_data}, 'health': health,
        'target_revision': pair['target_character'].revision, 'interaction_request_id': request.id,
    }


def treat(client, pair, headers, payload):
    return client.patch(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/characters/{pair['target_character'].id}/treatment",
        headers=headers, json=payload,
    )


@pytest.mark.parametrize('status', ['accepted', 'forced', 'in_progress'])
def test_treatment_commits_expense_health_and_consent_together(client, pair, auth_headers, status):
    request, payload = treatment_payload(pair, status)
    response = treat(client, pair, auth_headers(pair['actor_user']), payload)
    assert response.status_code == 200, response.json
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 1
    assert pair['target_character'].data['health']['current'] == 90
    assert request.status == 'completed'
    assert response.json['actor_data']['_revision'] == pair['actor_character'].revision
    assert response.json['target_data']['_revision'] == pair['target_character'].revision
    assert treat(client, pair, auth_headers(pair['actor_user']), payload).status_code in {403, 409}


@pytest.mark.parametrize('fault', ['actor_revision', 'target_revision', 'consent', 'flush'])
def test_treatment_failure_preserves_expense_patient_and_request(client, pair, auth_headers, monkeypatch, fault):
    request, payload = treatment_payload(pair)
    before_actor = deepcopy(pair['actor_character'].data)
    before_target = deepcopy(pair['target_character'].data)
    if fault == 'actor_revision':
        payload['actor_updates']['data']['_revision'] -= 1
    elif fault == 'target_revision':
        payload['target_revision'] -= 1
    elif fault == 'consent':
        request.status = 'pending'
        db.session.commit()
    else:
        monkeypatch.setattr(db.session, 'commit', fail_after_flush)
    events = []
    monkeypatch.setattr('app.lobbies.socketio.emit', lambda *args, **kwargs: events.append(args))
    response = treat(client, pair, auth_headers(pair['actor_user']), payload)
    assert response.status_code in {400, 403, 409}, response.json
    assert pair['actor_character'].data == before_actor
    assert pair['target_character'].data == before_target
    assert request.status == ('pending' if fault == 'consent' else 'accepted')
    assert pair['actor_location'].action_points_current == 5
    assert events == []


def test_treatment_ignores_distance_outside_combat(client, pair, auth_headers):
    request, payload = treatment_payload(pair)
    LocationCombatState.query.one().status = 'idle'
    pair['target_location'].pos_x = 50
    db.session.commit()
    assert treat(client, pair, auth_headers(pair['actor_user']), payload).status_code == 200


def test_sheet_treatment_cannot_spend_other_users_item(client, pair, auth_headers):
    actor_data = pair['actor_character'].data_snapshot()
    actor_data['inventory']['backpack'] = []
    response = client.post(f"/lobbies/characters/{pair['target_character'].id}/treatment",
                           headers=auth_headers(pair['target_user']), json={
                               'actor_character_id': pair['actor_character'].id,
                               'actor_updates': {'data': actor_data},
                               'target_updates': {'data': pair['target_character'].data_snapshot()},
                           })
    assert response.status_code == 403
    assert len(pair['actor_character'].data['inventory']['backpack']) == 1
