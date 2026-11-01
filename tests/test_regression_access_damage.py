"""A+: настоящий урон и отзыв доступа подписанного readonly viewer; NOT_RUN."""
from collections import deque
from copy import deepcopy

import app.services.combat as combat_module
from app.extensions import db
from app.models import LobbyCharacter, Location, LocationCharacter, LocationCombatState
from test_character_access import access_case, headers


def damage_events(connection, character_id):
    return [event['args'][0] for event in connection.get_received()
            if event['name'] == 'character_data_updated'
            and event['args'][0]['character_id'] == character_id]


def deny_edit(client, case, connection):
    character = case['character']
    response = client.put(f'/lobbies/characters/{character.id}',
                          headers=headers(case, case['viewer']), json={'name': 'Запрещено'})
    assert response.status_code == 403
    reply = connection.emit('update_character_data', {
        'token': case['tokens'][case['viewer'].id], 'character_id': character.id,
        'updates': {'name': 'Запрещено'},
    }, callback=True)
    assert reply['ok'] is False
    assert reply['code'] == 403
    db.session.refresh(character)
    assert character.name == 'Private'


def setup_attack(case):
    location = Location(lobby_id=case['lobby'].id, name='AD damage',
                        grid_width=6, grid_height=6, world_tile_x=0, world_tile_z=0)
    attacker = LobbyCharacter(lobby_id=case['lobby'].id, owner_id=case['gm'].id,
                              name='Атакующий', data={
        'health': {'current': 700, 'max': 700, 'effects': []},
        'skills': {'physical': {'strength': {'base': 10, 'bonus': 0},
                                'melee': {'base': 10, 'bonus': 0}}},
    })
    db.session.add_all([location, attacker])
    db.session.flush()
    actor = LocationCharacter(location_id=location.id, character_id=attacker.id,
                               controlled_by=case['gm'].id, pos_x=1, pos_y=2,
                               facing_x=1, facing_y=0, action_points_current=5)
    victim = LocationCharacter(location_id=location.id, character_id=case['character'].id,
                                controlled_by=case['owner'].id, pos_x=2, pos_y=2,
                                facing_x=-1, facing_y=0, posture='standing')
    db.session.add_all([actor, victim])
    db.session.flush()
    db.session.add(LocationCombatState(location_id=location.id, status='active',
        round_number=1, turn_index=0, turn_order=[actor.id, victim.id],
        current_location_character_id=actor.id))
    db.session.commit()
    assert victim.controlled_by != case['viewer'].id
    assert case['viewer'].id not in (case['character'].editable_to or [])
    return location.id, actor.id


def hit(client, case, location_id, actor_id, monkeypatch, *, first):
    # Обычный удар10: попадание19, левая рука14, первое проявление стресса20.
    sequence = deque([(1, 20, 19), (1, 20, 14)] + ([(1, 20, 20)] if first else []))

    def scripted(low, high):
        assert sequence, (low, high)
        expected_low, expected_high, value = sequence.popleft()
        assert (low, high) == (expected_low, expected_high)
        return value

    with monkeypatch.context() as controlled:
        controlled.setattr(combat_module.random, 'randint', scripted)
        response = client.post(f"/lobbies/{case['lobby'].id}/locations/{location_id}/combat/action",
            headers=headers(case, case['gm']), json={
                'location_character_id': actor_id, 'action_key': 'attack',
                'attack_type': 'unarmed', 'target_character_id': case['character'].id,
            })
    assert response.status_code == 200, response.get_json()
    assert not sequence
    result = response.get_json()
    assert result['attack']['damage_total'] == 10
    assert len(result['attack']['results']) == 1
    attack = result['attack']['results'][0]
    assert attack['hit'] is True
    assert attack['rolls'] == [19]
    assert attack['zone'] == 'left_arm'
    assert attack['damage'] == 10
    return result


def test_readonly_viewer_receives_real_damage_then_revocation_blocks_next_damage(access_case, client, monkeypatch):
    case = access_case
    character_id = case['character'].id
    location_id, actor_id = setup_attack(case)
    response = client.put(f'/lobbies/characters/{character_id}/visibility',
        headers=headers(case, case['gm']),
        json={'visible_to': [case['viewer'].id], 'editable_to': []})
    assert response.status_code == 200
    viewer = case['connect'](case['viewer'])
    owner = case['connect'](case['owner'])
    for connection, user in [(viewer, case['viewer']), (owner, case['owner'])]:
        joined = connection.emit('join_character', {
            'token': case['tokens'][user.id], 'character_id': character_id,
        }, callback=True)
        assert joined['ok'] is True
        connection.get_received()
    assert client.get(f'/lobbies/characters/{character_id}',
                       headers=headers(case, case['viewer'])).status_code == 200
    deny_edit(client, case, viewer)
    viewer.get_received()
    owner.get_received()
    revision_before = case['character'].revision

    hit(client, case, location_id, actor_id, monkeypatch, first=True)
    db.session.expire_all()
    saved = db.session.get(LobbyCharacter, character_id)
    first_snapshot = deepcopy(saved.data_snapshot())
    first_revision = saved.revision
    assert first_revision > revision_before
    assert first_snapshot['health']['current'] == 690
    assert first_snapshot['_revision'] == first_revision
    for connection in (viewer, owner):
        events = damage_events(connection, character_id)
        assert len(events) == 1
        assert events[0]['source'] == 'combat'
        assert events[0]['updates']['data'] == first_snapshot
    readable = client.get(f'/lobbies/characters/{character_id}', headers=headers(case, case['viewer']))
    assert readable.status_code == 200
    assert readable.get_json()['data']['health']['current'] == 690
    deny_edit(client, case, viewer)
    assert case['character'].revision == first_revision

    revoked = client.put(f'/lobbies/characters/{character_id}/visibility',
        headers=headers(case, case['gm']), json={'visible_to': [], 'editable_to': []})
    assert revoked.status_code == 200
    viewer.get_received()
    owner.get_received()
    revision_after_revoke = case['character'].revision
    hit(client, case, location_id, actor_id, monkeypatch, first=False)
    db.session.expire_all()
    saved = db.session.get(LobbyCharacter, character_id)
    second_snapshot = saved.data_snapshot()
    assert saved.revision > revision_after_revoke >= first_revision
    assert second_snapshot['_revision'] == saved.revision
    assert second_snapshot['health']['current'] == 680
    assert damage_events(viewer, character_id) == []
    owner_events = damage_events(owner, character_id)
    assert len(owner_events) == 1
    assert owner_events[0]['updates']['data'] == second_snapshot
    assert client.get(f'/lobbies/characters/{character_id}',
                       headers=headers(case, case['viewer'])).status_code == 403
    deny_edit(client, case, viewer)
    assert damage_events(viewer, character_id) == []
    assert db.session.get(LocationCharacter, actor_id).action_points_current == 1
    # Все соединения зарегистрированы access_case и disconnect в её teardown.
