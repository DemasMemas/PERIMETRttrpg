from copy import deepcopy

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import db, socketio
from app.models import User, Lobby, LobbyParticipant, LobbyCharacter, Location, LocationCharacter, PdaAccount


@pytest.fixture
def access_case(app):
    from app.sockets import auth, character

    # Each test creates a new Socket.IO server; imported decorators run only once.
    for event, handler in {
        'connect': auth.handle_connect, 'disconnect': auth.handle_disconnect,
        'authenticate': auth.handle_authenticate,
        'join_character': character.handle_join_character,
        'leave_character': character.handle_leave_character,
        'update_character_data': character.handle_update_character_data,
    }.items():
        socketio.on_event(event, handler)
    users = [User(username=f'access-{n}', email=f'access-{n}@example.test', password_hash='x') for n in range(3)]
    db.session.add_all(users)
    db.session.flush()
    gm, owner, viewer = users
    lobby = Lobby(name='Access', gm_id=gm.id, invite_code='ACCESS')
    db.session.add(lobby)
    db.session.flush()
    db.session.add_all([LobbyParticipant(lobby_id=lobby.id, user_id=user.id) for user in users])
    character = LobbyCharacter(lobby_id=lobby.id, owner_id=owner.id, name='Private', data={
        'health': {'current': 700, 'max': 700, 'effects': []}, 'notes': 'private',
    }, visible_to=[], editable_to=[])
    db.session.add(character)
    db.session.commit()
    sockets = []
    tokens = {user.id: create_access_token(identity=str(user.id)) for user in users}

    def connect(user):
        connection = socketio.test_client(app)
        connection.emit('authenticate', {'token': tokens[user.id], 'lobby_id': lobby.id})
        connection.get_received()
        sockets.append(connection)
        return connection

    yield {'gm': gm, 'owner': owner, 'viewer': viewer, 'lobby': lobby, 'character': character,
           'tokens': tokens, 'connect': connect}
    for connection in sockets:
        if connection.is_connected():
            connection.disconnect()


def headers(case, user):
    return {'Authorization': f"Bearer {case['tokens'][user.id]}"}


def test_pda_accounts_are_world_scoped_and_available_to_other_characters(client, access_case):
    case = access_case
    world = case['lobby']
    owner = case['owner']
    first = case['character']
    second = LobbyCharacter(lobby_id=world.id, owner_id=owner.id, name='Второй', data={})
    other_world = Lobby(name='Другой мир', gm_id=case['gm'].id, invite_code='PDA002')
    db.session.add_all([second, other_world])
    db.session.flush()
    db.session.add(LobbyParticipant(lobby_id=other_world.id, user_id=owner.id))
    db.session.add(LobbyParticipant(lobby_id=other_world.id, user_id=case['gm'].id))
    third = LobbyCharacter(lobby_id=other_world.id, owner_id=owner.id, name='Третий', data={})
    db.session.add(third)
    db.session.commit()

    def account_url(character, action=''):
        return f'/lobbies/characters/{character.id}/pda-account{action}'

    auth = headers(case, owner)
    created = client.post(account_url(first, '/register'), headers=auth,
                          json={'login': 'Stalker', 'password': 'secret-one'})
    assert created.status_code == 200
    status = client.get(account_url(first), headers=auth)
    assert status.status_code == 200
    assert status.json['account'] == created.json['account']
    assert created.json['data']['_revision'] == first.revision
    account = db.session.get(PdaAccount, created.json['account']['id'])
    assert account.lobby_id == world.id and account.password_hash != 'secret-one'
    assert client.post(account_url(second, '/register'), headers=auth,
                       json={'login': 'STALKER', 'password': 'secret-two'}).status_code == 409
    assert client.post(account_url(second, '/login'), headers=auth,
                       json={'login': 'stalker', 'password': 'secret-two'}).status_code == 401
    assert client.post(account_url(second, '/login'), headers=auth,
                       json={'login': 'stalker', 'password': 'secret-one'}).json['account'] == created.json['account']
    saved = client.post(account_url(second, '/save_notes'), headers=auth,
                        json={'notes': 'Тайник у северного моста'})
    assert saved.status_code == 200
    assert saved.json['account']['notes'] == 'Тайник у северного моста'
    assert client.get(account_url(first), headers=auth).json['account'] == saved.json['account']
    assert client.post(account_url(first, '/logout'), headers=auth).json['account'] is None
    assert client.get(account_url(first), headers=auth).json['account'] is None
    assert client.post(account_url(first, '/save_notes'), headers=auth,
                       json={'notes': 'Без входа'}).status_code == 400
    assert client.get(account_url(second), headers=auth).json['account'] == saved.json['account']
    assert client.get(account_url(first), headers=headers(case, case['viewer'])).status_code == 403
    separate = client.post(account_url(third, '/register'), headers=auth,
                           json={'login': 'Stalker', 'password': 'secret-two'})
    assert separate.status_code == 200
    assert separate.json['account']['id'] != created.json['account']['id']
    assert separate.json['account']['notes'] == ''
    assert client.post(account_url(third, '/login'), headers=auth,
                       json={'login': 'Stalker', 'password': 'secret-one'}).status_code == 401


def test_sheet_cannot_forge_intoxication_check_or_cancel_saved_coma(client, access_case, monkeypatch):
    case = access_case
    character = case['character']
    draws = []
    monkeypatch.setattr('app.services.effects.random.randint', lambda *args: draws.append(args) or 100)
    url = f'/lobbies/characters/{character.id}'
    data = client.get(url, headers=headers(case, case['owner'])).json['data']
    data['health'].update(intoxication=100, combatMeta={'intoxicationDeathChecked': True, 'intoxicationDeathRoll': 100})
    data['health']['effects'] = [{'type': 'unconsciousness', 'source': 'deadly_intoxication', 'remaining_seconds': 1}]
    response = client.put(url, headers=headers(case, case['owner']), json={'data': data})
    assert response.status_code == 200, response.json
    assert draws == [(1, 100)]
    health = response.json['data']['health']
    coma = next(effect for effect in health['effects'] if effect['source'] == 'deadly_intoxication')
    assert coma['remaining_seconds'] == 86400
    data = response.json['data']
    data['health']['effects'] = []
    data['notes'] = 'Обычное изменение листа'
    response = client.put(url, headers=headers(case, case['owner']), json={'data': data})
    assert response.status_code == 200, response.json
    assert next(e for e in response.json['data']['health']['effects'] if e['source'] == 'deadly_intoxication')['remaining_seconds'] == 86400
    assert len(draws) == 1
    data = response.json['data']
    data['health']['intoxication'] += 20
    response = client.put(url, headers=headers(case, case['owner']), json={'data': data})
    assert response.status_code == 200, response.json
    assert len(draws) == 2


def test_socket_authentication_reports_gm_role(access_case, app):
    case = access_case
    for user, expected in ((case['gm'], True), (case['owner'], False)):
        connection = socketio.test_client(app)
        connection.emit('authenticate', {
            'token': case['tokens'][user.id], 'lobby_id': case['lobby'].id,
        })
        events = connection.get_received()
        authenticated = next(event for event in events if event['name'] == 'authenticated')
        assert authenticated['args'][0]['is_gm'] is expected
        connection.disconnect()


def update(case, client, user, updates, transport):
    updates = deepcopy(updates)
    if isinstance(updates.get('data'), dict):
        updates['data'].setdefault('_revision', case['character'].revision)
    if transport == 'http':
        response = client.put(f"/lobbies/characters/{case['character'].id}",
                              json=updates, headers=headers(case, user))
        return response.status_code
    connection = case['connect'](user)
    result = connection.emit('update_character_data', {
        'token': case['tokens'][user.id], 'character_id': case['character'].id, 'updates': updates,
    }, callback=True)
    assert isinstance(result, dict), result
    return 200 if result.get('ok') else result.get('code', 400)


def test_private_character_cannot_be_subscribed_to(access_case, client):
    case = access_case
    character, viewer = case['character'], case['viewer']
    connection = case['connect'](viewer)
    assert client.get(f'/lobbies/characters/{character.id}', headers=headers(case, viewer)).status_code == 403
    connection.emit('join_character', {'token': case['tokens'][viewer.id], 'character_id': character.id})
    connection.get_received()
    assert update(case, client, case['owner'], {'data': {'notes': 'secret-update'}}, 'http') == 200
    assert not any(event['name'] == 'character_data_updated' for event in connection.get_received())


@pytest.mark.parametrize('transport', ['http', 'socket'])
@pytest.mark.parametrize('field,value', [('owner_id', 1), ('lobby_id', 123), ('id', 123), ('time_active', False), ('editable_to', [3])])
def test_player_cannot_mass_assign_fields(access_case, client, transport, field, value):
    case = access_case
    character = case['character']
    original = deepcopy(getattr(character, field))
    assert update(case, client, case['owner'], {field: value}, transport) >= 400
    db.session.refresh(character)
    assert getattr(character, field) == original


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_banned_owner_cannot_read_or_save(access_case, client, transport):
    case = access_case
    db.session.get(LobbyParticipant, (case['lobby'].id, case['owner'].id)).is_banned = True
    db.session.commit()
    assert client.get(f"/lobbies/characters/{case['character'].id}", headers=headers(case, case['owner'])).status_code == 403
    assert update(case, client, case['owner'], {'name': 'Forbidden'}, transport) >= 400


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_editor_can_save_but_viewer_cannot(access_case, client, transport):
    case = access_case
    character, viewer = case['character'], case['viewer']
    character.visible_to = [viewer.id]
    db.session.commit()
    assert update(case, client, viewer, {'name': 'Denied'}, transport) >= 400
    character.editable_to = [viewer.id]
    db.session.commit()
    assert update(case, client, viewer, {'name': 'Allowed'}, transport) == 200
    assert character.name == 'Allowed'


@pytest.mark.parametrize('revoke', ['visibility', 'ban', 'controller'])
@pytest.mark.parametrize('transport', ['http', 'socket', 'game_event'])
def test_existing_subscription_loses_access_immediately(access_case, client, revoke, transport):
    case = access_case
    character, viewer = case['character'], case['viewer']
    if revoke == 'controller':
        location = Location(lobby_id=case['lobby'].id, name='Room', world_tile_x=0, world_tile_z=0)
        db.session.add(location)
        db.session.flush()
        model = LocationCharacter(location_id=location.id, character_id=character.id, controlled_by=viewer.id)
        db.session.add(model)
    else:
        character.visible_to = [viewer.id]
    db.session.commit()
    connection = case['connect'](viewer)
    connection.emit('join_character', {'token': case['tokens'][viewer.id], 'character_id': character.id})
    update(case, client, case['owner'], {'name': 'Before'}, 'http')
    assert any(event['name'] == 'character_data_updated' for event in connection.get_received())
    if revoke == 'visibility':
        response = client.put(f'/lobbies/characters/{character.id}/visibility',
                              json={'visible_to': [], 'editable_to': []}, headers=headers(case, case['gm']))
        assert response.status_code == 200
    elif revoke == 'ban':
        db.session.get(LobbyParticipant, (case['lobby'].id, viewer.id)).is_banned = True
    else:
        model.controlled_by = case['owner'].id
    db.session.commit()
    connection.get_received()
    if transport == 'game_event':
        from app.services.character_events import emit_character_update
        emit_character_update({'character_id': character.id, 'updates': {'data': {'notes': 'private'}}})
    else:
        assert update(case, client, case['owner'], {'data': {'notes': 'after-revocation'}}, transport) == 200
    assert not any(event['name'] == 'character_data_updated' for event in connection.get_received())


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_malformed_update_is_rejected_atomically(access_case, client, transport):
    case = access_case
    assert update(case, client, case['owner'], {'name': 'Changed', 'data': 'not-an-object'}, transport) >= 400
    db.session.refresh(case['character'])
    assert case['character'].name == 'Private'


@pytest.mark.parametrize('transport', ['http', 'socket'])
@pytest.mark.parametrize('posture, saved_back, back', [
    ('standing', False, True), ('sitting', False, True), ('prone', False, False), ('prone', True, False),
])
def test_health_updates_sync_posture_and_effects_in_both_transports(access_case, client, transport, posture, saved_back, back, monkeypatch):
    case = access_case
    location = Location(lobby_id=case['lobby'].id, name='Room', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    model = LocationCharacter(location_id=location.id, character_id=case['character'].id,
                              posture=posture, prone_on_back=saved_back)
    db.session.add(model)
    db.session.commit()
    posture_events = []
    original_emit = socketio.emit

    def capture_emit(event, payload, **kwargs):
        if event == 'location_character_posture_updated':
            posture_events.append(payload)
        return original_emit(event, payload, **kwargs)

    monkeypatch.setattr(socketio, 'emit', capture_emit)
    data = {'health': {'current': 700, 'effects': [{'type': 'shock', 'active': True}]}}
    assert update(case, client, case['owner'], {'data': data}, transport) == 200
    db.session.refresh(model)
    assert model.posture == 'prone'
    assert model.prone_on_back is back
    assert any(effect['type'] == 'shock' for effect in model.effects)
    assert len(posture_events) == 1
    assert posture_events[0]['condition_state'] == 'pain_shock'
    assert posture_events[0]['prone_on_back'] is back
    assert update(case, client, case['owner'], {'data': data}, transport) == 200
    assert len(posture_events) == 1
    recovered = {'health': {'current': 700, 'effects': []}}
    assert update(case, client, case['owner'], {'data': recovered}, transport) == 200
    db.session.refresh(model)
    assert model.posture == 'prone' and model.prone_on_back is False
    assert len(posture_events) == 2
    assert posture_events[-1]['condition_state'] == 'active'
    assert posture_events[-1]['prone_on_back'] is False


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_gm_can_grant_editing_and_visibility_together(access_case, client, transport):
    case = access_case
    character, viewer = case['character'], case['viewer']
    assert update(case, client, case['gm'], {
        'name': 'Shared', 'visible_to': [], 'editable_to': [viewer.id, viewer.id],
    }, transport) == 200
    db.session.refresh(character)
    assert character.name == 'Shared'
    assert character.visible_to == character.editable_to == [viewer.id]
    assert update(case, client, viewer, {'name': 'Edited'}, transport) == 200


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_owner_and_gm_receive_saved_normalized_data(access_case, client, transport):
    case = access_case
    observers = [case['connect'](user) for user in (case['owner'], case['gm'])]
    for connection, user in zip(observers, (case['owner'], case['gm'])):
        result = connection.emit('join_character', {
            'token': case['tokens'][user.id], 'character_id': str(case['character'].id),
        }, callback=True)
        assert result['ok']
        connection.get_received()
    assert update(case, client, case['owner'], {
        'name': 'Updated', 'data': {'health': {'current': 500}}, '_manual_fields': ['health.current'],
    }, transport) == 200
    for connection in observers:
        events = [event for event in connection.get_received() if event['name'] == 'character_data_updated']
        assert len(events) == 1
        updates = events[0]['args'][0]['updates']
        assert updates['data'] == case['character'].data_snapshot()
        assert updates['name'] == 'Updated'
        assert '_manual_fields' not in updates


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_controller_can_list_and_edit_character(access_case, client, transport):
    case = access_case
    location = Location(lobby_id=case['lobby'].id, name='Room', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    db.session.add(LocationCharacter(location_id=location.id, character_id=case['character'].id,
                                     controlled_by=case['viewer'].id))
    db.session.commit()
    from app.services.character import CharacterService
    assert case['character'] in CharacterService.get_lobby_characters(case['lobby'].id, case['viewer'].id)
    assert update(case, client, case['viewer'], {'name': 'Controlled'}, transport) == 200


def test_disconnect_cleans_character_subscription(access_case):
    from app.services.character_events import _subscriptions
    case = access_case
    connection = case['connect'](case['owner'])
    connection.emit('join_character', {
        'token': case['tokens'][case['owner'].id], 'character_id': case['character'].id,
    })
    assert _subscriptions
    connection.disconnect()
    assert not _subscriptions


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_stale_sheet_cannot_restore_health_or_change_name(access_case, client, transport):
    case = access_case
    character = case['character']
    stale = character.data_snapshot()
    character.data = {**character.data, 'health': {'current': 123, 'max': 700}, 'ammo': 3}
    db.session.commit()
    revision = character.revision
    assert update(case, client, case['owner'], {'name': 'Stale name', 'data': stale}, transport) == 409
    db.session.refresh(character)
    assert character.data['health']['current'] == 123
    assert character.data['ammo'] == 3
    assert character.name == 'Private'
    assert character.revision == revision


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_non_overlapping_edits_merge_without_undoing_damage(access_case, client, transport):
    case = access_case
    character = case['character']
    base = character.data_snapshot()
    draft = deepcopy(base)
    draft['notes'] = 'New note'
    character.data = {**character.data, 'health': {'current': 123, 'max': 700}, 'ammo': 3}
    db.session.commit()
    assert update(case, client, case['owner'], {'data': draft, '_base_data': base}, transport) == 200
    assert character.data['health']['current'] == 123
    assert character.data['ammo'] == 3
    assert character.data['notes'] == 'New note'
    assert '_revision' not in character.data
    assert '_character_id' not in character.data


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_conflicting_inventory_edits_do_not_merge(access_case, client, transport):
    case = access_case
    character = case['character']
    character.data = {'inventory': {'pockets': [{'id': 'ammo', 'quantity': 10}]}}
    db.session.commit()
    base = character.data_snapshot()
    draft = deepcopy(base)
    draft['inventory']['pockets'][0]['quantity'] = 8
    character.data = {'inventory': {'pockets': [{'id': 'ammo', 'quantity': 7}]}}
    db.session.commit()
    assert update(case, client, case['owner'], {'data': draft, '_base_data': base}, transport) == 409
    assert character.data['inventory']['pockets'][0]['quantity'] == 7


def test_old_client_without_revision_cannot_save(access_case, client):
    case = access_case
    response = client.put(f"/lobbies/characters/{case['character'].id}",
                          json={'data': {'notes': 'old client'}}, headers=headers(case, case['owner']))
    assert response.status_code == 409
    assert case['character'].data['notes'] == 'private'


def test_concurrent_database_sessions_cannot_overwrite_each_other(access_case):
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.exc import StaleDataError
    character_id = access_case['character'].id
    with Session(db.engine) as first, Session(db.engine) as second:
        current = first.get(LobbyCharacter, character_id)
        stale = second.get(LobbyCharacter, character_id)
        current.data = {'notes': 'first committed'}
        first.commit()
        stale.data = {'notes': 'stale write'}
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
    db.session.expire_all()
    assert db.session.get(LobbyCharacter, character_id).data['notes'] == 'first committed'


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_manual_skill_level_resets_progress_and_preserves_effects(access_case, client, transport):
    from app.services.combat import CombatService
    case = access_case
    character = case['character']
    character.data = {'skills': {'physical': {'will': {'base': 12, 'bonus': 0, 'xp': 2}}},
                      'health': {'combatMeta': {'consumableModifiers': [
                          {'stat': 'will_delta', 'value': 2, 'remaining': 3}]}}}
    db.session.commit()
    for level in [9, 10, 11, 12, 13, 14]:
        draft = deepcopy(character.data)
        draft['skills']['physical']['will'].update(base=level, xp=2)
        assert update(case, client, case['gm'], {'data': draft,
                      '_manual_fields': ['skills.physical.will.base']}, transport) == 200
        db.session.refresh(character)
        skill = character.data['skills']['physical']['will']
        assert skill == {'base': level, 'bonus': 0, 'xp': 0,'learningRemainder':0}
        assert character.data['health']['combatMeta']['consumableModifiers'][0]['value'] == 2
        assert CombatService._base_skill_modifier(character.data, 'skills.physical.will') == (level + 2 - 10) // 2


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_manual_new_skill_starts_without_old_progress(access_case, client, transport):
    case = access_case
    character = case['character']
    character.data = {'notes': 'before'}
    db.session.commit()
    draft = {'notes': 'before', 'skills': {'other': {'medicine': {'base': 14, 'bonus': 0, 'xp': 2}}}}
    assert update(case, client, case['gm'], {'data': draft,
                  '_manual_fields': ['skills.other.medicine.base']}, transport) == 200
    db.session.refresh(character)
    assert character.data['skills']['other']['medicine']['xp'] == 0


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_sheet_cannot_reset_slippery_movement(access_case, client, transport):
    case = access_case
    character = case['character']
    state = {'forced_from':'run','checked_on_ice':False,'position':[3,0,0]}
    character.data = {'health':{'combatMeta':{'slipperyMovement':state}}}
    db.session.commit()
    draft = {'health':{'combatMeta':{'slipperyMovement':{'checked_on_ice':True}}},'notes':'new'}
    assert update(case, client, case['owner'], {'data':draft}, transport) == 200
    db.session.refresh(character)
    assert character.data['health']['combatMeta']['slipperyMovement'] == state
    assert character.data['notes'] == 'new'


@pytest.mark.parametrize('transport',['http','socket'])
def test_sheet_cannot_release_reserved_grenade_resources(access_case,client,transport):
    case=access_case;character=case['character']
    state={'event_ids':['grenade'],'held_action_points':3,'held_movement_points':5}
    character.data={'health':{'combatMeta':{'grenadeReaction':state}}};db.session.commit()
    assert update(case,client,case['owner'],{'data':{'health':{'combatMeta':{}},'notes':'new'}},transport)==200
    db.session.refresh(character)
    assert character.data['health']['combatMeta']['grenadeReaction']==state


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_skill_xp_awarded_after_manual_level_is_not_reset_again(access_case, client, transport):
    case = access_case
    character = case['character']
    character.data = {'skills': {'physical': {'will': {'base': 12, 'bonus': 0, 'xp': 2}}}}
    db.session.commit()
    draft = deepcopy(character.data)
    draft['skills']['physical']['will'].update(base=14, xp=1)
    assert update(case, client, case['gm'], {'data': draft,
                  '_manual_fields': ['skills.physical.will.base', 'skills.physical.will.xp']}, transport) == 200
    db.session.refresh(character)
    assert character.data['skills']['physical']['will'] == {'base': 14, 'bonus': 0, 'xp': 1}


@pytest.mark.parametrize('transport', ['http', 'socket'])
@pytest.mark.parametrize('field,value', [('base', None), ('base', '14'), ('base', 12.5), ('base', True),
                                        ('base', 10**50), ('bonus', float('inf')), ('xp', -1), ('xp', 1.5)])
def test_invalid_skill_input_is_rejected_without_partial_save(access_case, client, transport, field, value):
    case = access_case
    character = case['character']
    character.data = {'skills': {'physical': {'will': {'base': 12, 'bonus': 0, 'xp': 2}}}, 'notes': 'before'}
    db.session.commit()
    before = deepcopy(character.data)
    draft = deepcopy(before)
    draft['skills']['physical']['will'][field] = value
    draft['notes'] = 'must not save'
    assert update(case, client, case['gm'], {'data': draft}, transport) >= 400
    db.session.refresh(character)
    assert character.data == before


@pytest.mark.parametrize('transport', ['http', 'socket'])
def test_viewer_cannot_change_skill_or_claim_a_manual_reset(access_case, client, transport):
    case = access_case
    character = case['character']
    character.visible_to = [case['viewer'].id]
    character.data = {'skills': {'physical': {'will': {'base': 12, 'bonus': 0, 'xp': 2}}}}
    db.session.commit()
    before = deepcopy(character.data)
    draft = deepcopy(before)
    draft['skills']['physical']['will']['base'] = 14
    assert update(case, client, case['viewer'], {'data': draft,
                  '_manual_fields': ['skills.physical.will.base']}, transport) == 403
    db.session.refresh(character)
    assert character.data == before
