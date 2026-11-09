import pytest
from types import SimpleNamespace

from app.extensions import db
from app.lobbies import _world_group_speed
from app.services import addictions
from app.services.character_events import emit_character_update
from app.services.combat import CombatService
from app.models import (
    ChatMessage,
    Lobby,
    LobbyCharacter,
    LobbyParticipant,
    MapChunk,
    LocationCombatState,
    Location,
    LocationCharacter,
    LocationObject,
    User,
    WorldGroup,
    WorldMapEvent,
    WorldTravelEvent,
)
from app.models.templates import ItemTemplate
from app.services.map import MapService
from app.services.world_travel_events import travel_scenario
from app.services.world_rules import mutant_catalog, mutant_character_data, mutant_profile


def create_lobby(client, user, auth_headers, name="Rookie camp"):
    response = client.post(
        "/lobbies/",
        headers=auth_headers(user),
        json={
            "name": name,
            "map_type": "empty",
            "chunks_width": 4,
            "chunks_height": 3,
        },
    )
    assert response.status_code == 201
    return response.get_json()


def join_lobby(client, lobby, user, auth_headers):
    response = client.post(
        f"/lobbies/{lobby['id']}/join",
        headers=auth_headers(user),
    )
    assert response.status_code == 200


def create_character(client, lobby, user, auth_headers, data=None):
    response = client.post(
        f"/lobbies/{lobby['id']}/characters",
        headers=auth_headers(user),
        json={"name": "Test character", "data": data or {}},
    )
    assert response.status_code == 201
    return response.get_json()


def test_gm_full_heal_restores_body_and_rejects_player(client, create_user, auth_headers):
    gm = create_user('full-heal-gm')
    player = create_user('full-heal-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, gm, auth_headers)
    stored = db.session.get(LobbyCharacter, character['id'])
    stored.data = {
        **stored.data,
        'health': {
            'current': 0, 'max': 700, 'stress': 3, 'painLevel': 7,
            'blood': 'severe',
            'zones': {
                'head': {'current': 0, 'max': 50, 'destructionDamage': 300},
                'leftArm': {'current': 0, 'max': 90, 'destructionDamage': 500},
            },
            'organs': {'skull': {'current': 0, 'max': 50}},
            'effects': [{'type': 'death'}, {'type': 'fracture', 'area': 'leftArm'}],
        },
    }
    db.session.commit()
    endpoint = f"/lobbies/{lobby['id']}/characters/{character['id']}/heal"
    assert client.post(endpoint, headers=auth_headers(player)).status_code == 403
    result = client.post(endpoint, headers=auth_headers(gm))
    assert result.status_code == 200, result.json
    health = result.json['data']['health']
    assert health['current'] == health['max'] == 700
    assert health['zones']['head']['current'] == health['zones']['head']['max'] == 50
    assert health['zones']['leftArm']['current'] == health['zones']['leftArm']['max'] == 90
    assert health['zones']['leftArm']['destructionDamage'] == 0
    assert health['organs']['skull']['current'] == health['organs']['skull']['max'] == 50
    assert health['effects'] == []
    assert health['bleeding']['effects'] == []
    assert health['blood'] == 'normal'
    assert health['painLevel'] == 0
    assert health['stress'] == 3


def test_mutant_health_update_reaches_only_allowed_list_viewers(
    client, create_user, auth_headers, monkeypatch,
):
    gm = create_user('mutant-health-event-gm')
    viewer = create_user('mutant-health-event-viewer')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, viewer, auth_headers)
    mutant = create_character(
        client, lobby, gm, auth_headers,
        mutant_character_data(mutant_profile('Собака')),
    )
    human = create_character(client, lobby, gm, auth_headers)
    stored = db.session.get(LobbyCharacter, mutant['id'])
    stored.data = {
        **stored.data,
        'health': {**stored.data['health'], 'current': 70},
    }
    db.session.commit()
    events = []
    monkeypatch.setattr(
        'app.services.character_events.socketio.emit',
        lambda event, payload, **kwargs: events.append((event, payload, kwargs)),
    )

    emit_character_update({
        'character_id': stored.id,
        'updates': {'data': stored.data_snapshot()},
        'source': 'combat',
    })
    health_events = [event for event in events if event[0] == 'mutant_health_updated']
    assert len(health_events) == 1
    assert health_events[0][1]['health'] == {'current': 70, 'max': stored.data['health']['max']}
    assert health_events[0][2]['room'] == f"user_{gm['id']}"

    stored.visible_to = [viewer['id']]
    db.session.commit()
    events.clear()
    emit_character_update({
        'character_id': stored.id,
        'updates': {'data': stored.data_snapshot()},
        'source': 'combat',
    })
    assert {
        event[2]['room'] for event in events if event[0] == 'mutant_health_updated'
    } == {f"user_{gm['id']}", f"user_{viewer['id']}"}

    events.clear()
    person = db.session.get(LobbyCharacter, human['id'])
    emit_character_update({'character_id': person.id, 'updates': {'data': person.data_snapshot()}})
    assert not any(event[0] == 'mutant_health_updated' for event in events)


def test_replacing_character_outside_combat_clears_old_anomaly(
    client, create_user, auth_headers,
):
    gm = create_user('reposition-anomaly-gm')
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers)
    tiles = [[{'terrain': 'grass', 'height': 1, 'objects': []} for _ in range(5)] for _ in range(5)]
    tiles[2][2]['objects'] = [{'type': 'anomaly', 'anomalyKey': 'otboynik'}]
    location = Location(
        lobby_id=lobby['id'], name='Reposition anomaly', world_tile_x=0, world_tile_z=0,
        grid_width=5, grid_height=5, tiles_data=tiles,
    )
    db.session.add(location)
    db.session.flush()
    placed = LocationCharacter(
        location_id=location.id, character_id=character['id'], pos_x=2, pos_y=2,
    )
    db.session.add(placed)
    db.session.flush()
    CombatService._enter_anomaly(
        placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1,
    )
    db.session.commit()

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/spawn_character",
        headers=auth_headers(gm),
        json={'character_id': character['id'], 'tile_x': 3, 'tile_y': 2},
    )

    assert response.status_code == 200
    assert (placed.pos_x, placed.pos_y) == (3, 2)
    assert 'activeAnomaly' not in db.session.get(LobbyCharacter, character['id']).data['health']['combatMeta']


def test_gm_can_hide_mutant_and_npc_models_but_not_player_character(
    client, create_user, auth_headers,
):
    gm = create_user('hidden-model-gm')
    player = create_user('hidden-model-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    location = Location(
        lobby_id=lobby['id'], name='Hidden models', world_tile_x=0, world_tile_z=0,
        grid_width=3, grid_height=3,
        tiles_data=[[{'terrain': 'grass', 'height': 1, 'objects': []} for _ in range(3)] for _ in range(3)],
    )
    db.session.add(location)
    db.session.flush()
    characters = [
        create_character(client, lobby, gm, auth_headers, data=data)
        for data in ({'is_mutant': True}, {'is_npc': True}, {})
    ]
    placed = [
        LocationCharacter(location_id=location.id, character_id=character['id'], pos_x=index, pos_y=1)
        for index, character in enumerate(characters)
    ]
    db.session.add_all(placed)
    db.session.commit()
    endpoint = lambda character: (
        f"/lobbies/{lobby['id']}/locations/{location.id}/characters/{character['id']}/gm-hidden"
    )

    assert client.patch(endpoint(characters[0]), headers=auth_headers(player),
                        json={'gm_hidden': True}).status_code == 403
    assert client.patch(endpoint(characters[2]), headers=auth_headers(gm),
                        json={'gm_hidden': True}).status_code == 400
    assert client.patch(endpoint(characters[0]), headers=auth_headers(gm),
                        json={'gm_hidden': 'true'}).status_code == 400
    for character in characters[:2]:
        assert client.patch(endpoint(character), headers=auth_headers(gm),
                            json={'gm_hidden': True}).status_code == 200
    assert all(model.gm_hidden for model in placed[:2])
    assert not placed[2].gm_hidden
    assert CombatService.get_state(location.id, gm['id'])['characters'][0]['gm_hidden'] is True
    assert client.patch(endpoint(characters[0]), headers=auth_headers(gm),
                        json={'gm_hidden': False}).status_code == 200
    assert not placed[0].gm_hidden


def test_mutant_camouflage_visibility_follows_combat_round_without_changing_gm_hide(app):
    gm = User(username='camouflage-gm', email='camouflage-gm@example.com', password_hash='x')
    db.session.add(gm)
    db.session.flush()
    lobby = Lobby(name='Camouflage', gm_id=gm.id, invite_code='CAMOUFLAGE')
    db.session.add(lobby)
    db.session.flush()
    location = Location(
        lobby_id=lobby.id, name='Clearing', world_tile_x=0, world_tile_z=0,
        grid_width=2, grid_height=2,
        tiles_data=[[{'terrain': 'grass', 'height': 1, 'objects': []} for _ in range(2)] for _ in range(2)],
    )
    mutant = LobbyCharacter(
        lobby_id=lobby.id, owner_id=gm.id, name='Bloodsucker',
        data={'is_mutant': True, 'health': {'combatMeta': {
            'mutantCamouflageActive': True, 'mutantCamouflageUntilRound': 2,
        }}},
    )
    db.session.add_all([location, mutant])
    db.session.flush()
    placed = LocationCharacter(
        location_id=location.id, character_id=mutant.id, pos_x=0, pos_y=0, gm_hidden=True,
    )
    state = LocationCombatState(
        location_id=location.id, status='active', round_number=1,
        turn_index=0, turn_order=[],
    )
    db.session.add_all([placed, state])
    db.session.commit()

    snapshot = CombatService._serialize_character(placed, combat_state=state)
    assert snapshot['camouflaged'] is True
    assert snapshot['gm_hidden'] is True
    state.round_number = 3
    snapshot = CombatService._serialize_character(placed, combat_state=state)
    assert snapshot['camouflaged'] is False
    assert snapshot['gm_hidden'] is True


def test_addiction_exposure_and_global_time_start_withdrawal(
    client,
    create_user,
    auth_headers,
    monkeypatch,
):
    gm = create_user("addiction-gm")
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers, data={"health": {}})
    stored_character = db.session.get(LobbyCharacter, character['id'])
    stored_character.time_active = True
    db.session.commit()
    monkeypatch.setattr(addictions.random, 'random', lambda: 0)

    exposed = client.post(
        f"/lobbies/characters/{character['id']}/addictions/exposure",
        headers=auth_headers(gm),
        json={"item_name": "Борщевик", "price": 0},
    )
    assert exposed.status_code == 200
    assert exposed.get_json()['result']['acquired'] is True

    advanced = client.patch(
        f"/lobbies/{lobby['id']}/time",
        headers=auth_headers(gm),
        json={"game_day": 3, "game_time_minutes": 480},
    )
    assert advanced.status_code == 200
    stored_character = db.session.get(LobbyCharacter, character['id'])
    health = stored_character.data['health']
    record = health['addictions']['records']['borshevik']
    assert record['withdrawal_stage'] == 1
    assert any(effect['type'] == 'addiction_withdrawal' for effect in health['effects'])

    monkeypatch.setattr(addictions.random, 'randint', lambda _low, _high: 20)
    checked = client.post(
        f"/lobbies/characters/{character['id']}/addictions/borshevik/check",
        headers=auth_headers(gm),
    )
    assert checked.status_code == 200
    assert checked.get_json()['result']['success'] is True


def test_gm_can_assign_location_teams_and_players_cannot_edit_them(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("teams-gm")
    player = create_user("teams-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, player, auth_headers)
    location = Location(lobby_id=lobby["id"], name="Team test", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    location_character = LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
        controlled_by=player["id"],
    )
    db.session.add(location_character)
    db.session.commit()

    url = f"/lobbies/{lobby['id']}/locations/{location.id}/teams"
    denied = client.put(
        url,
        headers=auth_headers(player),
        json={"assignments": [{"location_character_id": location_character.id, "team_name": "North", "team_color": "#457b9d"}]},
    )
    assert denied.status_code == 403

    updated = client.put(
        url,
        headers=auth_headers(gm),
        json={"assignments": [{"location_character_id": location_character.id, "team_name": "North", "team_color": "#457b9d"}]},
    )
    assert updated.status_code == 200
    assert updated.get_json()["characters"][0]["team_name"] == "North"
    assert updated.get_json()["characters"][0]["team_color"] == "#457b9d"

    invalid_color = client.put(
        url,
        headers=auth_headers(gm),
        json={"assignments": [{"location_character_id": location_character.id, "team_name": "North", "team_color": "red; background:black"}]},
    )
    assert invalid_color.status_code == 200
    assert invalid_color.get_json()["characters"][0]["team_color"] is None


def test_combat_facing_change_up_to_45_degrees_is_free_and_limited_per_round(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("facing-gm")
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers)
    location = Location(lobby_id=lobby["id"], name="Facing test", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    location_character = LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
        action_points_current=5,
        free_actions_current=1,
        movement_points_current=6,
        facing_x=0,
        facing_y=1,
    )
    db.session.add(location_character)
    db.session.flush()
    combat = LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_order=[location_character.id],
        current_location_character_id=location_character.id,
    )
    db.session.add(combat)
    db.session.commit()

    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action"
    turned = client.post(
        url,
        headers=auth_headers(gm),
        json={
            "location_character_id": location_character.id,
            "action_key": "change_facing",
            "facing_x": 1,
            "facing_y": 1,
            "payment": "free_turn",
        },
    )
    assert turned.status_code == 200
    db.session.refresh(location_character)
    assert (location_character.facing_x, location_character.facing_y) == (1, 1)
    assert location_character.free_actions_current == 1
    assert location_character.facing_changed_round == 1

    repeated = client.post(
        url,
        headers=auth_headers(gm),
        json={
            "location_character_id": location_character.id,
            "action_key": "change_facing",
            "facing_x": 1,
            "facing_y": 0,
            "payment": "action",
        },
    )
    assert repeated.status_code == 400


def test_exact_facing_vector_is_saved_outside_combat(client, create_user, auth_headers):
    gm = create_user("exact-facing-gm")
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers)
    location = Location(lobby_id=lobby["id"], name="Facing vector", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    placed = LocationCharacter(location_id=location.id, character_id=character["id"])
    db.session.add(placed)
    db.session.commit()

    response = client.patch(
        f"/lobbies/{lobby['id']}/locations/{location.id}/characters/{character['id']}/facing",
        headers=auth_headers(gm),
        json={"facing_x": 2, "facing_y": 7},
    )

    assert response.status_code == 200
    db.session.refresh(placed)
    assert (placed.facing_x, placed.facing_y) == (2, 7)


@pytest.mark.parametrize('has_backpack', [False, True])
def test_dead_mutant_can_be_butchered_only_once(
    client, create_user, auth_headers, monkeypatch, has_backpack,
):
    gm = create_user("butchering-gm")
    lobby = create_lobby(client, gm, auth_headers)
    actor_data = {
        "health": {},
        "skills": {"other": {"survival": {"base": 20, "bonus": 0}}},
        "weapons": [{
            "name": "Охотничий нож",
            "category": "melee_weapon",
            "attributes": {
                "armor_piercing": 100,
                "allowed_attacks": ["Колющий"],
                "melee_damage_type": "Колющий",
            },
        }],
        "inventory": {"pockets": [], "backpack": []},
        "equipment": {"backpack": {"name": "Рюкзак"}} if has_backpack else {},
    }
    actor = create_character(client, lobby, gm, auth_headers, actor_data)
    dog_data = mutant_character_data(mutant_profile("Собака"))
    dog_data["health"]["effects"] = [{"type": "death", "name": "Смерть", "active": True}]
    mutant = create_character(client, lobby, gm, auth_headers, dog_data)
    location = Location(lobby_id=lobby["id"], name="Butchering", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    actor_placed = LocationCharacter(
        location_id=location.id, character_id=actor["id"], pos_x=1, pos_y=1,
        drawn_weapon_index=0,
    )
    mutant_placed = LocationCharacter(
        location_id=location.id, character_id=mutant["id"], pos_x=2, pos_y=1,
    )
    db.session.add_all([actor_placed, mutant_placed])
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda _a, _b: 20)
    url = f"/lobbies/{lobby['id']}/locations/{location.id}/characters/{mutant['id']}/butchering"

    rolled = client.post(
        url,
        headers=auth_headers(gm),
        json={"actor_location_character_id": actor_placed.id},
    )
    assert rolled.status_code == 200
    assert rolled.get_json()["roll"]["total"] == 17

    completed = client.post(
        url,
        headers=auth_headers(gm),
        json={
            "actor_location_character_id": actor_placed.id,
            "confirm": True,
            "allocation": {"meat": 2, "hide": 1, "limb": 1},
        },
    )
    assert completed.status_code == 200
    assert completed.get_json()["spent"] == 12
    stored_actor = db.session.get(LobbyCharacter, actor["id"])
    destination = "backpack" if has_backpack else "pockets"
    other = "pockets" if has_backpack else "backpack"
    assert len(stored_actor.data["inventory"][destination]) == 3
    assert stored_actor.data["inventory"][other] == []

    repeated = client.post(
        url,
        headers=auth_headers(gm),
        json={"actor_location_character_id": actor_placed.id},
    )
    assert repeated.status_code == 400

    healed = client.post(
        f"/lobbies/{lobby['id']}/characters/{mutant['id']}/heal",
        headers=auth_headers(gm),
    )
    assert healed.status_code == 200
    healed_meta = healed.get_json()['data']['health'].get('combatMeta', {})
    assert not any(key in healed_meta for key in ('butchered', 'butcheringResult', 'butcheringRoll'))
    revived = db.session.get(LobbyCharacter, mutant['id'])
    revived.data = {
        **revived.data,
        'health': {
            **revived.data['health'],
            'effects': [{'type': 'death', 'name': 'Смерть', 'active': True}],
        },
    }
    db.session.commit()
    new_body = client.post(
        url,
        headers=auth_headers(gm),
        json={"actor_location_character_id": actor_placed.id},
    )
    assert new_body.status_code == 200
    assert new_body.get_json()['roll']['total'] == 17


def test_reaction_reserve_interrupts_and_returns_to_the_original_turn(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("reaction-gm")
    player = create_user("reaction-player")
    other = create_user("reaction-other")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    join_lobby(client, lobby, other, auth_headers)
    player_character = create_character(client, lobby, player, auth_headers)
    other_character = create_character(client, lobby, other, auth_headers)
    location = Location(lobby_id=lobby["id"], name="Reaction test", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    reactor = LocationCharacter(
        location_id=location.id,
        character_id=player_character["id"],
        controlled_by=player["id"],
        action_points_current=4,
        free_actions_current=1,
        movement_points_current=5,
    )
    active = LocationCharacter(
        location_id=location.id,
        character_id=other_character["id"],
        controlled_by=other["id"],
        action_points_current=5,
        free_actions_current=1,
        movement_points_current=0,
    )
    db.session.add_all([reactor, active])
    db.session.flush()
    state = LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_order=[reactor.id, active.id],
        current_location_character_id=reactor.id,
    )
    db.session.add(state)
    db.session.commit()

    base_url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/reaction"
    reserved = client.post(
        f"{base_url}/reserve",
        headers=auth_headers(player),
        json={
            "location_character_id": reactor.id,
            "action_points": 2,
            "free_actions": 1,
            "movement_points": 3,
            "trigger": "Enemy leaves cover",
        },
    )
    assert reserved.status_code == 200
    db.session.refresh(reactor)
    assert (reactor.action_points_current, reactor.free_actions_current, reactor.movement_points_current) == (2, 0, 2)

    state.current_location_character_id = active.id
    db.session.commit()
    requested = client.post(
        f"{base_url}/request",
        headers=auth_headers(player),
        json={"location_character_id": reactor.id},
    )
    assert requested.status_code == 200
    assert requested.get_json()["reaction"]["pending_location_character_id"] == reactor.id

    approved = client.post(
        f"{base_url}/resolve",
        headers=auth_headers(gm),
        json={"approve": True},
    )
    assert approved.status_code == 200
    assert approved.get_json()["current_location_character_id"] == reactor.id
    db.session.refresh(reactor)
    assert (reactor.action_points_current, reactor.free_actions_current, reactor.movement_points_current) == (2, 1, 3)

    returned = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(player),
        json={},
    )
    assert returned.status_code == 200
    assert returned.get_json()["current_location_character_id"] == active.id


def test_help_reaction_grants_advantage_without_switching_turn(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("help-gm")
    player = create_user("help-player")
    other = create_user("help-other")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    join_lobby(client, lobby, other, auth_headers)
    helper_character = create_character(client, lobby, player, auth_headers)
    target_character = create_character(client, lobby, other, auth_headers)
    location = Location(lobby_id=lobby["id"], name="Help test", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    helper = LocationCharacter(
        location_id=location.id,
        character_id=helper_character["id"],
        controlled_by=player["id"],
        action_points_current=4,
        free_actions_current=1,
        movement_points_current=0,
    )
    target = LocationCharacter(
        location_id=location.id,
        character_id=target_character["id"],
        controlled_by=other["id"],
        action_points_current=5,
        free_actions_current=1,
        movement_points_current=0,
    )
    db.session.add_all([helper, target])
    db.session.flush()
    state = LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_order=[helper.id, target.id],
        current_location_character_id=helper.id,
    )
    db.session.add(state)
    db.session.commit()

    base_url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/reaction"
    reserved = client.post(
        f"{base_url}/reserve",
        headers=auth_headers(player),
        json={
            "location_character_id": helper.id,
            "action_points": 6,
            "kind": "help",
            "help_target_character_id": target.character_id,
            "help_action_label": "Hold the wound closed",
            "help_skill_path": "skills.physical.melee",
        },
    )
    assert reserved.status_code == 200
    db.session.refresh(helper)
    assert helper.action_points_current == 0

    state.current_location_character_id = target.id
    db.session.commit()
    unpaid = client.post(
        f"{base_url}/request",
        headers=auth_headers(player),
        json={"location_character_id": helper.id},
    )
    assert unpaid.status_code == 400

    paid_turn = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(other),
        json={},
    )
    assert paid_turn.status_code == 200
    assert paid_turn.get_json()["current_location_character_id"] == helper.id
    db.session.refresh(helper)
    assert helper.action_points_current == 3

    state.current_location_character_id = target.id
    db.session.commit()
    requested = client.post(
        f"{base_url}/request",
        headers=auth_headers(player),
        json={"location_character_id": helper.id},
    )
    assert requested.status_code == 200
    approved = client.post(
        f"{base_url}/resolve",
        headers=auth_headers(gm),
        json={"approve": True},
    )
    assert approved.status_code == 200
    assert approved.get_json()["current_location_character_id"] == target.id
    db.session.refresh(target.character)
    help_bonus = target.character.data["health"]["combatMeta"]["helpAdvantage"]
    assert help_bonus["source_character_id"] == helper.character_id
    assert help_bonus["skill_path"] == "skills.physical.melee"


def test_gm_location_events_apply_stress_and_fall(client, create_user, auth_headers):
    gm = create_user("events-gm")
    player = create_user("events-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    first = create_character(client, lobby, player, auth_headers, data={"health": {"stress": 2}})
    second = create_character(client, lobby, player, auth_headers, data={"health": {"stress": 0}})
    location = Location(lobby_id=lobby["id"], name="Events test", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    first_location_character = LocationCharacter(
        location_id=location.id, character_id=first["id"], controlled_by=player["id"], posture="standing"
    )
    second_location_character = LocationCharacter(
        location_id=location.id, character_id=second["id"], controlled_by=player["id"], posture="standing"
    )
    db.session.add_all([first_location_character, second_location_character])
    db.session.commit()
    url = f"/lobbies/{lobby['id']}/locations/{location.id}/gm-events"

    denied = client.post(
        url,
        headers=auth_headers(player),
        json={"type": "stress", "amount": 1, "location_character_ids": [first_location_character.id]},
    )
    assert denied.status_code == 403

    stressed = client.post(
        url,
        headers=auth_headers(gm),
        json={
            "type": "stress",
            "amount": 3,
            "location_character_ids": [first_location_character.id, second_location_character.id],
            "note": "The blowout begins",
        },
    )
    assert stressed.status_code == 200
    db.session.refresh(first_location_character.character)
    db.session.refresh(second_location_character.character)
    assert first_location_character.character.data["health"]["stress"] == 5
    assert second_location_character.character.data["health"]["stress"] == 3

    fallen = client.post(
        url,
        headers=auth_headers(gm),
        json={
            "type": "fall",
            "height_meters": 1,
            "location_character_ids": [first_location_character.id],
        },
    )
    assert fallen.status_code == 200
    db.session.refresh(first_location_character)
    assert first_location_character.posture in {"standing", "prone"}
    assert ChatMessage.query.filter_by(lobby_id=lobby["id"], username="ГМ").count() == 2


def test_only_gm_can_use_quick_stress_buttons_and_decrease_expires_effects(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("quick-stress-gm")
    player = create_user("quick-stress-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, player, auth_headers, data={
        "skills": {"physical": {"will": {"base": 20, "bonus": 0}}},
        "health": {
            "stress": 2,
            "effects": [{
                "id": "until-stress-drops",
                "type": "stress_effect",
                "name": "До снижения стресса",
                "active": True,
                "expires_on_stress_decrease": True,
            }],
        },
    })
    location = Location(lobby_id=lobby["id"], name="Quick stress", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    loc_char = LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
        controlled_by=player["id"],
    )
    db.session.add(loc_char)
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)
    url = f"/lobbies/{lobby['id']}/locations/{location.id}/characters/{character['id']}/stress"

    denied = client.post(url, headers=auth_headers(player), json={"amount": 1})
    assert denied.status_code == 403

    increased = client.post(url, headers=auth_headers(gm), json={"amount": 1})
    assert increased.status_code == 200
    assert increased.get_json()["stress"]["after"] == 3

    decreased = client.post(url, headers=auth_headers(gm), json={"amount": -1})
    assert decreased.status_code == 200
    assert decreased.get_json()["stress"]["after"] == 2
    db.session.refresh(loc_char.character)
    stored_effect = loc_char.character.data["health"]["effects"][0]
    assert stored_effect["active"] is False


@pytest.mark.parametrize(
    ("penalty", "distance", "label"),
    [
        (0, 3, "Без изменений"),
        (3, 3, "Без изменений"),
        (4, 2, "На треть медленнее"),
        (5, 2, "На треть медленнее"),
        (6, 2, "На треть медленнее"),
        (7, 1, "Вдвое медленнее"),
        (8, 1, "Вдвое медленнее"),
        (9, 1, "Вдвое медленнее"),
        (10, 0, "Группа не может идти"),
    ],
)
def test_world_group_speed_thresholds(penalty, distance, label):
    assert _world_group_speed(penalty) == (distance, label)


def test_lobby_creation_assigns_gm_and_membership(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("game-master")

    lobby = create_lobby(client, gm, auth_headers)

    assert lobby["gm_id"] == gm["id"]
    assert lobby["chunks_width"] == 4
    assert lobby["chunks_height"] == 3
    assert len(lobby["invite_code"]) == 6
    participant = db.session.get(
        LobbyParticipant,
        {"lobby_id": lobby["id"], "user_id": gm["id"]},
    )
    assert participant is not None


def test_lobby_creation_requires_authentication(client):
    response = client.post(
        "/lobbies/",
        json={"name": "No auth", "map_type": "empty"},
    )

    assert response.status_code == 401


def test_player_joins_by_code_and_can_open_lobby(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("join-gm")
    player = create_user("join-player")
    lobby = create_lobby(client, gm, auth_headers)

    joined = client.post(
        "/lobbies/join_by_code",
        headers=auth_headers(player),
        json={"code": lobby["invite_code"]},
    )
    details = client.get(
        f"/lobbies/{lobby['id']}",
        headers=auth_headers(player),
    )

    assert joined.status_code == 200
    assert joined.get_json()["lobby_id"] == lobby["id"]
    assert details.status_code == 200
    assert details.get_json()["name"] == "Rookie camp"


def test_joining_twice_is_idempotent(client, create_user, auth_headers):
    gm = create_user("idempotent-gm")
    player = create_user("idempotent-player")
    lobby = create_lobby(client, gm, auth_headers)
    endpoint = f"/lobbies/{lobby['id']}/join"

    first = client.post(endpoint, headers=auth_headers(player))
    second = client.post(endpoint, headers=auth_headers(player))

    assert first.status_code == 200
    assert second.status_code == 200


def test_lobby_detail_restores_participant_color(client, create_user, auth_headers):
    gm = create_user("color-gm")
    lobby = create_lobby(client, gm, auth_headers)

    changed = client.patch(
        "/auth/color",
        json={"color": "#4a7b52"},
        headers=auth_headers(gm),
    )
    detail = client.get(f"/lobbies/{lobby['id']}", headers=auth_headers(gm))

    assert changed.status_code == 200
    assert detail.status_code == 200
    participant = detail.get_json()["participants"][0]
    assert participant["color"] == "#4a7b52"


def test_only_gm_can_update_persisted_lobby_time(
    client, create_user, auth_headers
):
    gm = create_user("time-gm")
    player = create_user("time-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    endpoint = f"/lobbies/{lobby['id']}/time"

    forbidden = client.patch(
        endpoint,
        json={"game_day": 3, "game_time_minutes": 19 * 60 + 25},
        headers=auth_headers(player),
    )
    updated = client.patch(
        endpoint,
        json={"game_day": 3, "game_time_minutes": 19 * 60 + 25},
        headers=auth_headers(gm),
    )
    detail = client.get(f"/lobbies/{lobby['id']}", headers=auth_headers(player))

    assert forbidden.status_code == 403
    assert updated.status_code == 200
    assert detail.get_json()["game_day"] == 3
    assert detail.get_json()["game_time_minutes"] == 19 * 60 + 25
    count = LobbyParticipant.query.filter_by(
        lobby_id=lobby["id"],
        user_id=player["id"],
    ).count()
    assert count == 1


def test_world_group_creation_is_gm_only_and_validates_map_bounds(
    client, create_user, auth_headers
):
    gm = create_user("world-group-gm")
    player = create_user("world-group-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    endpoint = f"/lobbies/{lobby['id']}/world-groups"

    forbidden = client.post(
        endpoint,
        headers=auth_headers(player),
        json={"name": "Player party", "tile_x": 2, "tile_y": 3},
    )
    outside = client.post(
        endpoint,
        headers=auth_headers(gm),
        json={"name": "Outside", "tile_x": 128, "tile_y": 0},
    )
    created = client.post(
        endpoint,
        headers=auth_headers(gm),
        json={"name": "Rookies", "tile_x": 2, "tile_y": 3},
    )

    assert forbidden.status_code == 403
    assert outside.status_code == 400
    assert created.status_code == 201
    assert created.get_json()["name"] == "Rookies"
    assert WorldGroup.query.filter_by(lobby_id=lobby["id"]).count() == 1


def test_world_anomaly_field_can_be_searched_and_looted(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user('field-gm')
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers, data={
        'health': {'current': 700},
        'skills': {'other': {'survival': 5}, 'physical': {'agility': 5}},
        'inventory': {'pockets': []},
    })
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={'name': 'Field party', 'tile_x': 2, 'tile_y': 2},
    ).get_json()
    client.patch(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/members",
        headers=auth_headers(gm),
        json={'character_ids': [character['id']]},
    )
    MapService.update_tile(
        lobby['id'], gm['id'], 0, 0, 2, 2,
        {'anomaly_field': {
            'name': 'Батутный комплекс', 'field_type': 'Гравитационное',
            'hazard': 'Повышенное давление', 'rank': 1,
        }},
    )
    template = ItemTemplate(
        name='Камень проверки', category='artifact', subcategory='Гравитационное',
        item_class='trash', attributes={'artifact_class': 'trash'},
    )
    db.session.add(template)
    db.session.commit()
    artifact = {
        'name': template.name, 'artifact_class': 'trash',
        'anomaly_type': 'Гравитационное',
    }
    monkeypatch.setattr('app.lobbies.random_artifact', lambda *_args, **_kwargs: artifact)

    def fixed_randint(low, high):
        if high == 100:
            return 1
        if high == 4:
            return 2
        if high == 10:
            return 10
        return low

    monkeypatch.setattr('app.lobbies.random.randint', fixed_randint)
    endpoint = f"/lobbies/{lobby['id']}/world-groups/{group['id']}/anomaly-field"
    inspected = client.post(
        endpoint, headers=auth_headers(gm),
        json={'action': 'inspect', 'character_id': character['id']},
    )
    recovered = client.post(
        endpoint, headers=auth_headers(gm),
        json={
            'action': 'recover', 'character_id': character['id'],
            'artifact_index': 0, 'extra_dice': 0,
        },
    )

    assert inspected.status_code == 200
    assert inspected.get_json()['field']['untouched'] is True
    assert len(inspected.get_json()['field']['artifacts']) == 1
    assert recovered.status_code == 200
    assert recovered.get_json()['recovery']['success'] is True
    stored = db.session.get(LobbyCharacter, character['id'])
    assert stored.data['inventory']['pockets'][0]['name'] == 'Камень проверки'


def test_gm_can_create_mutant_from_world_rule_catalog(
    client, create_user, auth_headers
):
    gm = create_user('mutant-gm')
    player = create_user('mutant-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    endpoint = f"/lobbies/{lobby['id']}/mutants"
    catalogs = client.get(
        f"/lobbies/{lobby['id']}/world-rules",
        headers=auth_headers(gm),
    )

    forbidden = client.post(
        endpoint, headers=auth_headers(player),
        json={'mutant_type': 'Собака'},
    )
    created = client.post(
        endpoint, headers=auth_headers(gm),
        json={'mutant_type': 'Собака', 'variant': 'Матерый пёс', 'name': 'Клык'},
    )

    assert forbidden.status_code == 403
    assert catalogs.status_code == 200
    assert len(catalogs.get_json()['anomalies']) == 48
    assert created.status_code == 201
    stored = db.session.get(LobbyCharacter, created.get_json()['id'])
    assert stored.name == 'Клык'
    assert stored.data['basic']['mutant_variant'] == 'Матерый пёс'
    assert stored.data['health']['max'] == 200


def test_world_movement_takes_ten_minutes_and_respects_group_distance(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("world-move-gm")
    player = create_user("world-move-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    created = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Road party", "tile_x": 10, "tile_y": 10},
    ).get_json()
    endpoint = f"/lobbies/{lobby['id']}/world-groups/{created['id']}/move"
    monkeypatch.setattr("app.lobbies.random.random", lambda: 1.0)

    too_far = client.post(
        endpoint,
        headers=auth_headers(player),
        json={"tile_x": 14, "tile_y": 10},
    )
    moved = client.post(
        endpoint,
        headers=auth_headers(player),
        json={"tile_x": 13, "tile_y": 13},
    )

    assert too_far.status_code == 400
    assert moved.status_code == 200
    assert moved.get_json()["event_pending"] is False
    assert moved.get_json()["time"] == {"game_day": 1, "game_time_minutes": 490}
    group = db.session.get(WorldGroup, created["id"])
    assert (group.tile_x, group.tile_y) == (13, 13)


def test_world_movement_spends_and_auto_replaces_equipped_gas_mask_filter(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("world-filter-gm")
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers, data={
        "equipment": {"gasMask": {
            "name": "Противогаз",
            "autoReplaceFilters": True,
            "installedModules": [{
                "name": "Старый фильтр",
                "category": "gas_mask_module",
                "subcategory": "filter",
                "slotType": "filter",
                "durability": 1,
                "maxDurability": 10,
                "attributes": {"slot_type": "filter", "durability": 1, "max_durability": 10},
            }],
        }},
        "inventory": {"backpack": [{
            "name": "Запасной фильтр",
            "category": "gas_mask_module",
            "subcategory": "filter",
            "quantity": 2,
            "durability": 10,
            "maxDurability": 10,
            "attributes": {"slot_type": "filter", "durability": 10, "max_durability": 10},
        }]},
    })
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Filtered party", "tile_x": 2, "tile_y": 2},
    ).get_json()
    members = client.patch(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/members",
        headers=auth_headers(gm),
        json={"character_ids": [character["id"]]},
    )
    monkeypatch.setattr("app.lobbies.random.random", lambda: 1.0)

    moved = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/move",
        headers=auth_headers(gm),
        json={"tile_x": 3, "tile_y": 2},
    )

    assert members.status_code == 200
    assert moved.status_code == 200
    assert moved.get_json()["filter_updates"] == [{
        "character_id": character["id"],
        "changed": True,
        "consumed": 1,
        "removed": 1,
        "replaced": 1,
        "empty": 1,
    }]
    stored = db.session.get(LobbyCharacter, character["id"]).data
    installed = stored["equipment"]["gasMask"]["installedModules"][0]
    assert installed["name"] == "Запасной фильтр"
    assert installed["durability"] == 10
    assert stored["inventory"]["backpack"][0]["quantity"] == 1


def test_world_movement_applies_binary_radiation_per_group_member(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("world-radiation-gm")
    lobby = create_lobby(client, gm, auth_headers)
    protected = create_character(client, lobby, gm, auth_headers, data={
        "health": {"radiation": 1},
        "equipment": {
            "armor": {"protection": {"radiation": 0.3}},
            "helmet": {"protection": {"radiation": 0.2}},
        },
    })
    exposed = create_character(client, lobby, gm, auth_headers, data={
        "health": {"radiation": 1},
        "equipment": {"armor": {"protection": {"radiation": 0.49}}},
    })
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Radiation party", "tile_x": 2, "tile_y": 2},
    ).get_json()
    client.patch(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/members",
        headers=auth_headers(gm),
        json={"character_ids": [protected["id"], exposed["id"]]},
    )
    client.get(
        f"/lobbies/{lobby['id']}/chunks",
        headers=auth_headers(gm),
        query_string={
            "min_chunk_x": 0,
            "max_chunk_x": 0,
            "min_chunk_y": 0,
            "max_chunk_y": 0,
        },
    )
    client.patch(
        f"/lobbies/{lobby['id']}/chunks/0/0/tile/3/2",
        headers=auth_headers(gm),
        json={"radiation": 5},
    )
    monkeypatch.setattr("app.lobbies.random.random", lambda: 1.0)

    moved = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/move",
        headers=auth_headers(gm),
        json={"tile_x": 3, "tile_y": 2},
    )

    assert moved.status_code == 200
    updates = {
        item["character_id"]: item
        for item in moved.get_json()["radiation_updates"]
    }
    assert updates[protected["id"]]["protection"] == 50
    assert updates[protected["id"]]["received"] == 0
    assert updates[exposed["id"]]["protection"] == 49
    assert updates[exposed["id"]]["received"] == 5
    assert db.session.get(LobbyCharacter, protected["id"]).data["health"]["radiation"] == 1
    assert db.session.get(LobbyCharacter, exposed["id"]).data["health"]["radiation"] == 6


@pytest.mark.parametrize(
    (
        "radiation", "damage", "bleeding_stage", "bleeding_count",
        "critical", "death",
    ),
    [
        (20, 0, None, 0, False, False),
        (21, 20, "light", 1, False, False),
        (31, 50, "light", 2, False, False),
        (51, 100, "medium", 1, False, False),
        (61, 200, "medium", 2, False, False),
        (76, 200, "severe", 1, True, False),
        (100, 0, None, 0, False, True),
    ],
)
def test_world_radiation_consequence_bands(
    radiation, damage, bleeding_stage, bleeding_count, critical, death
):
    data = {
        "health": {
            "radiation": radiation,
            "current": 700,
            "max": 700,
            "effects": [],
        },
    }

    result = CombatService._apply_world_radiation_consequences(data)

    assert result["damage"] == damage
    assert result["health_after"] == 700 - damage
    assert len(result["bleedings"]) == bleeding_count
    assert {item["stage"] for item in result["bleedings"]} == (
        {bleeding_stage} if bleeding_stage else set()
    )
    assert result["critical"] is critical
    assert result["death"] is death


def test_radist_and_tarpaulin_reduce_world_incoming_radiation_and_spend_capacity():
    radist_data = {"health": {"radiation": 10, "effects": [{
        "type": "radiation_filter",
        "name": "Радист-Л",
        "value": 100,
        "capacity": 50,
        "remaining_capacity": 50,
        "remaining": 24,
        "time_unit": "hour",
        "tick": "time_elapsed",
    }]}}
    tarpaulin_data = {"health": {"radiation": 10, "effects": [{
        "type": "radiation_filter",
        "name": "Брезент-ПБ",
        "value": 50,
        "capacity": 100,
        "remaining_capacity": 100,
        "remaining": 24,
        "time_unit": "hour",
        "tick": "time_elapsed",
    }]}}

    radist = CombatService._apply_incoming_radiation(radist_data, 5, binary=True)
    tarpaulin = CombatService._apply_incoming_radiation(tarpaulin_data, 5, binary=True)

    assert radist["received"] == 0
    assert radist["filtered"] == 5
    assert radist_data["health"]["radiation"] == 10
    assert radist_data["health"]["effects"][0]["remaining_capacity"] == 45
    assert tarpaulin["received"] == 2.5
    assert tarpaulin["filtered"] == 2.5
    assert tarpaulin_data["health"]["radiation"] == 12.5
    assert tarpaulin_data["health"]["effects"][0]["remaining_capacity"] == 97.5


@pytest.mark.parametrize(
    ("starting_radiation", "incoming", "effect_type"),
    [
        (75, 1, "critical_condition"),
        (99, 1, "death"),
    ],
)
def test_incoming_radiation_immediately_applies_terminal_thresholds(
    starting_radiation, incoming, effect_type
):
    data = {"health": {"radiation": starting_radiation, "effects": []}}

    result = CombatService._apply_incoming_radiation(data, incoming, binary=False)

    assert result["after"] == starting_radiation + incoming
    assert any(
        effect.get("type") == effect_type
        and effect.get("source") == "radiation_sickness"
        for effect in data["health"]["effects"]
    )


def test_world_radiation_damage_uses_starting_dose_before_new_exposure(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("world-radiation-order-gm")
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers, data={
        "health": {
            "radiation": 25,
            "current": 100,
            "max": 700,
            "painLevel": 7,
            "effects": [],
        },
    })
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Irradiated party", "tile_x": 2, "tile_y": 2},
    ).get_json()
    client.patch(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/members",
        headers=auth_headers(gm),
        json={"character_ids": [character["id"]]},
    )
    client.get(
        f"/lobbies/{lobby['id']}/chunks",
        headers=auth_headers(gm),
        query_string={
            "min_chunk_x": 0,
            "max_chunk_x": 0,
            "min_chunk_y": 0,
            "max_chunk_y": 0,
        },
    )
    client.patch(
        f"/lobbies/{lobby['id']}/chunks/0/0/tile/3/2",
        headers=auth_headers(gm),
        json={"radiation": 5},
    )
    monkeypatch.setattr("app.lobbies.random.random", lambda: 1.0)

    moved = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/move",
        headers=auth_headers(gm),
        json={"tile_x": 3, "tile_y": 2},
    )

    assert moved.status_code == 200
    assert moved.get_json()["radiation_consequences"][0]["damage"] == 20
    stored_health = db.session.get(LobbyCharacter, character["id"]).data["health"]
    assert stored_health["current"] == 80
    assert stored_health["radiation"] == 30
    assert stored_health["painLevel"] == 0
    radiation_bleedings = [
        effect for effect in stored_health["effects"]
        if effect.get("source") == "radiation_sickness"
        and str(effect.get("type", "")).startswith("bleeding_")
    ]
    assert len(radiation_bleedings) == 1


def test_location_end_turn_subtracts_radiation_protection(
    client, create_user, auth_headers
):
    gm = create_user("location-radiation-gm")
    lobby = create_lobby(client, gm, auth_headers)
    exposed = create_character(client, lobby, gm, auth_headers, data={
        "health": {"radiation": 74},
        "equipment": {
            "armor": {"protection": {"radiation": 0.2}},
            "helmet": {"protection": {"radiation": 0.1}},
        },
    })
    next_character = create_character(client, lobby, gm, auth_headers)
    location = Location(
        lobby_id=lobby["id"],
        name="Radiation arena",
        world_tile_x=0,
        world_tile_z=0,
        grid_width=2,
        grid_height=2,
        tiles_data=[
            [{"radiation": 5}, {"radiation": 0}],
            [{"radiation": 0}, {"radiation": 0}],
        ],
    )
    db.session.add(location)
    db.session.flush()
    exposed_loc = LocationCharacter(
        location_id=location.id,
        character_id=exposed["id"],
        controlled_by=gm["id"],
        pos_x=0,
        pos_y=0,
    )
    next_loc = LocationCharacter(
        location_id=location.id,
        character_id=next_character["id"],
        controlled_by=gm["id"],
        pos_x=1,
        pos_y=1,
    )
    db.session.add_all([exposed_loc, next_loc])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[exposed_loc.id, next_loc.id],
        current_location_character_id=exposed_loc.id,
    ))
    db.session.commit()

    ended = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(gm),
        json={},
    )

    assert ended.status_code == 200
    radiation_result = ended.get_json()["radiation"]
    assert radiation_result["incoming"] == 5
    assert radiation_result["protection"] == 30
    assert radiation_result["required_protection"] == 50
    assert radiation_result["received"] == 2
    assert radiation_result["before"] == 74
    assert radiation_result["after"] == 76
    assert radiation_result["critical"] is True
    stored = db.session.get(LobbyCharacter, exposed["id"])
    assert stored.data["health"]["radiation"] == 76
    assert any(
        effect.get("type") == "critical_condition"
        and effect.get("source") == "radiation_sickness"
        for effect in stored.data["health"]["effects"]
    )
    assert db.session.get(LocationCharacter, exposed_loc.id).posture == "prone"




def test_parallel_world_groups_advance_time_only_after_every_active_group_acts(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("parallel-world-gm")
    player = create_user("parallel-world-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    first = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "First", "tile_x": 2, "tile_y": 2},
    ).get_json()
    second = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Second", "tile_x": 8, "tile_y": 8},
    ).get_json()
    monkeypatch.setattr("app.lobbies.random.random", lambda: 1.0)

    first_move = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{first['id']}/move",
        headers=auth_headers(player),
        json={"tile_x": 3, "tile_y": 2},
    )
    repeated = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{first['id']}/move",
        headers=auth_headers(player),
        json={"tile_x": 4, "tile_y": 2},
    )
    second_wait = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{second['id']}/wait",
        headers=auth_headers(player),
    )

    assert first_move.status_code == 200
    assert first_move.get_json()["time_advanced"] is False
    assert first_move.get_json()["time"] is None
    assert repeated.status_code == 409
    assert second_wait.status_code == 200
    assert second_wait.get_json()["time_advanced"] is True
    assert second_wait.get_json()["time"] == {
        "game_day": 1,
        "game_time_minutes": 490,
    }


def test_gm_can_exclude_world_group_from_required_turns(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("inactive-world-gm")
    lobby = create_lobby(client, gm, auth_headers)
    first = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Travellers", "tile_x": 2, "tile_y": 2},
    ).get_json()
    second = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Camp", "tile_x": 8, "tile_y": 8},
    ).get_json()
    disabled = client.patch(
        f"/lobbies/{lobby['id']}/world-groups/{second['id']}/turn-active",
        headers=auth_headers(gm),
        json={"active": False},
    )
    monkeypatch.setattr("app.lobbies.random.random", lambda: 1.0)

    moved = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{first['id']}/move",
        headers=auth_headers(gm),
        json={"tile_x": 3, "tile_y": 2},
    )

    assert disabled.status_code == 200
    assert disabled.get_json()["group"]["turn_active"] is False
    assert moved.status_code == 200
    assert moved.get_json()["time_advanced"] is True


def test_gm_configures_persisted_world_group_members(
    client, create_user, auth_headers
):
    gm = create_user("world-members-gm")
    player = create_user("world-members-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    gm_character = create_character(
        client,
        lobby,
        gm,
        auth_headers,
        data={"equipment": {"armor": {"movementPenalty": 5}}},
    )
    player_character = create_character(client, lobby, player, auth_headers)
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Mixed party", "tile_x": 3, "tile_y": 3},
    ).get_json()
    endpoint = f"/lobbies/{lobby['id']}/world-groups/{group['id']}/members"

    forbidden = client.patch(
        endpoint,
        headers=auth_headers(player),
        json={"character_ids": [player_character["id"]]},
    )
    invalid = client.patch(
        endpoint,
        headers=auth_headers(gm),
        json={"character_ids": [999999]},
    )
    updated = client.patch(
        endpoint,
        headers=auth_headers(gm),
        json={"character_ids": [gm_character["id"], player_character["id"]]},
    )
    player_view = client.get(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(player),
    ).get_json()

    assert forbidden.status_code == 403
    assert invalid.status_code == 400
    assert updated.status_code == 200
    assert [member["id"] for member in updated.get_json()["members"]] == [
        gm_character["id"],
        player_character["id"],
    ]
    assert updated.get_json()["movement_penalty"] == 5
    assert updated.get_json()["movement_distance"] == 2
    assert updated.get_json()["movement_speed_label"] == "На треть медленнее"
    assert player_view["available_characters"] == []
    assert len(player_view["groups"][0]["members"]) == 2
    assert db.session.get(WorldGroup, group["id"]).member_character_ids == [
        gm_character["id"],
        player_character["id"],
    ]


def test_mutants_and_npcs_can_be_activated_for_world_time(
    client, create_user, auth_headers
):
    gm = create_user("world-roles-gm")
    lobby = create_lobby(client, gm, auth_headers)
    human = create_character(client, lobby, gm, auth_headers, data={"health": {}})
    def timed_health():
        return {"stress": 5, "effects": [{
            "type": "delayed_adjustment", "remaining": 10,
            "remaining_seconds": 600, "time_unit": "minute",
            "tick": "time_elapsed",
            "adjustments": [{"field": "stress", "delta": -2, "min": 0}],
        }]}

    mutant = create_character(client, lobby, gm, auth_headers, data={
        "is_mutant": True, "basic": {"is_mutant": True},
        "health": timed_health(),
    })
    npc = create_character(client, lobby, gm, auth_headers, data={
        "basic": {"is_npc": True}, "health": timed_health(),
    })
    joining_npc = create_character(client, lobby, gm, auth_headers, data={
        "basic": {"is_npc": True, "can_join_group": True},
        "health": timed_health(),
    })
    for character in (mutant, npc, joining_npc):
        assert db.session.get(LobbyCharacter, character["id"]).time_active is False

    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Eligible party", "tile_x": 3, "tile_y": 3},
    ).get_json()
    group_url = f"/lobbies/{lobby['id']}/world-groups/{group['id']}/members"
    available = client.get(
        f"/lobbies/{lobby['id']}/world-groups", headers=auth_headers(gm),
    ).get_json()["available_characters"]
    assert {entry["id"] for entry in available} == {human["id"], joining_npc["id"]}
    for forbidden in (mutant, npc):
        assert client.patch(
            group_url, headers=auth_headers(gm),
            json={"character_ids": [human["id"], forbidden["id"]]},
        ).status_code == 400
    assert client.patch(
        group_url, headers=auth_headers(gm),
        json={"character_ids": [human["id"], joining_npc["id"]]},
    ).status_code == 200

    # Участие в группе и ход часов настраиваются отдельно.
    stored_group = db.session.get(WorldGroup, group["id"])
    stored_group.member_character_ids = [
        human["id"], mutant["id"], npc["id"], joining_npc["id"],
    ]
    for character in (mutant, npc, joining_npc):
        db.session.get(LobbyCharacter, character["id"]).time_active = True
    db.session.commit()
    visible_group = client.get(
        f"/lobbies/{lobby['id']}/world-groups", headers=auth_headers(gm),
    ).get_json()["groups"][0]
    assert {member["id"] for member in visible_group["members"]} == {
        human["id"], joining_npc["id"],
    }

    activity_url = f"/lobbies/{lobby['id']}/characters/time-active"
    rest_url = f"/lobbies/{lobby['id']}/rest"
    assert client.patch(
        activity_url, headers=auth_headers(gm),
        json={"character_ids": [human["id"], mutant["id"], npc["id"], joining_npc["id"]]},
    ).status_code == 200
    for character in (mutant, npc, joining_npc):
        assert db.session.get(LobbyCharacter, character["id"]).time_active is True
    assert client.patch(
        f"/lobbies/{lobby['id']}/time", headers=auth_headers(gm),
        json={"game_day": 1, "game_time_minutes": 490},
    ).status_code == 200
    moved = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/move",
        headers=auth_headers(gm), json={"tile_x": 4, "tile_y": 3},
    )
    assert moved.status_code == 200
    for character in (mutant, npc, joining_npc):
        health = db.session.get(LobbyCharacter, character["id"]).data["health"]
        assert health["stress"] == 3
        assert health["effects"] == []
    assert client.post(
        rest_url, headers=auth_headers(gm),
        json={"type": "rest", "character_ids": [human["id"], joining_npc["id"]]},
    ).status_code == 200
    for character in (mutant, npc, joining_npc):
        health = db.session.get(LobbyCharacter, character["id"]).data["health"]
        assert health["stress"] == 3
        assert health["effects"] == []
    assert client.patch(
        activity_url, headers=auth_headers(gm),
        json={"character_ids": [human["id"]]},
    ).status_code == 200
    for character in (mutant, npc, joining_npc):
        assert db.session.get(LobbyCharacter, character["id"]).time_active is False


def test_world_group_carries_incapacitated_member_and_uses_available_rope(
    client, create_user, auth_headers
):
    gm = create_user("world-carry-gm")
    lobby = create_lobby(client, gm, auth_headers)
    carrier = create_character(client, lobby, gm, auth_headers)
    body = create_character(
        client,
        lobby,
        gm,
        auth_headers,
        data={
            "health": {
                "effects": [{"type": "critical_condition", "active": True}],
            },
            "inventory": {
                "backpack": [{"name": "Heavy load", "weight": 25, "quantity": 1}],
                "pockets": [],
            },
        },
    )
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Rescue party", "tile_x": 3, "tile_y": 3},
    ).get_json()
    endpoint = f"/lobbies/{lobby['id']}/world-groups/{group['id']}/members"

    without_rope = client.patch(
        endpoint,
        headers=auth_headers(gm),
        json={"character_ids": [carrier["id"], body["id"]]},
    ).get_json()

    carried = next(member for member in without_rope["members"] if member["id"] == body["id"])
    assert carried["requires_carry"] is True
    assert carried["uses_carry_rope"] is False
    assert carried["carry_penalty"] == 7
    assert without_rope["movement_penalty"] == 7
    assert without_rope["movement_distance"] == 1

    stored_carrier = db.session.get(LobbyCharacter, carrier["id"])
    stored_carrier.data = {
        **(stored_carrier.data or {}),
        "inventory": {
            "backpack": [],
            "pockets": [{
                "name": "Канат для переноски",
                "category": "tool",
                "weight": 0.5,
                "volume": 4,
                "quantity": 1,
            }],
        },
    }
    db.session.commit()

    with_rope = client.get(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
    ).get_json()["groups"][0]
    carried = next(member for member in with_rope["members"] if member["id"] == body["id"])
    assert carried["uses_carry_rope"] is True
    assert carried["carry_penalty"] == 3.5
    assert with_rope["carry_rope_count"] == 1
    assert with_rope["movement_penalty"] == 3.5
    assert with_rope["movement_distance"] == 2


def test_world_group_with_only_incapacitated_members_cannot_move(
    client, create_user, auth_headers
):
    gm = create_user("world-no-carrier-gm")
    lobby = create_lobby(client, gm, auth_headers)
    body = create_character(
        client,
        lobby,
        gm,
        auth_headers,
        data={
            "health": {
                "effects": [{"type": "death", "active": True}],
            },
        },
    )
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "No carriers", "tile_x": 3, "tile_y": 3},
    ).get_json()

    updated = client.patch(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/members",
        headers=auth_headers(gm),
        json={"character_ids": [body["id"]]},
    ).get_json()

    assert updated["carried_member_count"] == 1
    assert updated["movement_penalty"] == 10
    assert updated["movement_distance"] == 0


def test_random_world_event_blocks_travel_until_gm_resolves_it(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("world-event-gm")
    player = create_user("world-event-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    created = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Event party", "tile_x": 5, "tile_y": 5},
    ).get_json()
    move_endpoint = f"/lobbies/{lobby['id']}/world-groups/{created['id']}/move"
    monkeypatch.setattr(
        "app.lobbies.roll_travel_event",
        lambda _scenario: SimpleNamespace(name="Test world encounter", prompt="GM prompt"),
    )

    moved = client.post(
        move_endpoint,
        headers=auth_headers(player),
        json={"tile_x": 6, "tile_y": 5},
    )
    blocked = client.post(
        move_endpoint,
        headers=auth_headers(player),
        json={"tile_x": 7, "tile_y": 5},
    )
    event = WorldTravelEvent.query.filter_by(group_id=created["id"]).one()
    hidden_list = client.get(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(player),
    ).get_json()
    visible_list = client.get(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
    ).get_json()
    forbidden = client.patch(
        f"/lobbies/{lobby['id']}/world-events/{event.id}",
        headers=auth_headers(player),
        json={"decision": "approve"},
    )
    approved = client.patch(
        f"/lobbies/{lobby['id']}/world-events/{event.id}",
        headers=auth_headers(gm),
        json={"decision": "approve"},
    )

    assert moved.status_code == 200
    assert moved.get_json()["event_pending"] is True
    assert blocked.status_code == 409
    assert hidden_list["pending_events"][0]["description"] is None
    assert visible_list["pending_events"][0]["description"] == "Test world encounter: GM prompt"
    assert forbidden.status_code == 403
    assert approved.status_code == 200
    assert approved.get_json()["status"] == "approved"
    assert ChatMessage.query.filter_by(
        lobby_id=lobby["id"],
        username="Событие",
    ).one().message == "Event party: Test world encounter: GM prompt"


def test_world_travel_anomaly_uses_synchronized_detector_bonus(client,create_user,auth_headers,monkeypatch):
    from app.services.equipment_modifications import make_record
    from app.services.world_travel_events import OUTCOMES

    gm = create_user('synchronized-detector-gm')
    lobby = create_lobby(client, gm, auth_headers)
    headers = auth_headers(gm)
    api = f"/lobbies/{lobby['id']}/world-groups"
    character = db.session.get(LobbyCharacter, create_character(client,lobby,gm,auth_headers)['id'])
    character.data = {**character.data, 'equipment': {
        'armor': {'modifications':[make_record('armor-a43')]},
        'detector': {'category':'detector','subcategory':'Аномалии','bonus':2,'attributes':{'avoidance_chance':25}},
    }}
    db.session.commit()
    group = client.post(api, headers=headers, json={'name':'Группа','tile_x':5,'tile_y':5}).json
    assert client.patch(f"{api}/{group['id']}/members",headers=headers,json={'character_ids':[character.id]}).status_code==200
    anomaly = next(outcome for outcome in OUTCOMES if outcome.key == 'anomaly')
    monkeypatch.setattr('app.lobbies.roll_travel_event',lambda _scenario:anomaly)
    moved = client.post(f"{api}/{group['id']}/move",headers=headers,json={'tile_x':6,'tile_y':5})
    assert moved.status_code==200,moved.json
    event = client.get(api, headers=headers).json['pending_events'][0]
    assert f'{character.name} · СЛ 15 − бонус Внимательности.' in event['description']


def test_world_travel_scenario_uses_only_explicit_active_bases(
    client, create_user, auth_headers
):
    gm = create_user("world-base-gm")
    lobby = create_lobby(client, gm, auth_headers)
    tiles = [[{'terrain': 'grass', 'objects': []} for _ in range(32)] for _ in range(32)]
    tiles[10][10]['objects'] = [{'type': 'base'}]
    tiles[10][13]['objects'] = [{'type': 'road'}]
    tiles[10][14]['objects'] = [{'type': 'road'}]
    tiles[20][20]['objects'] = [{'type': 'forest'}]
    chunk = MapChunk(lobby_id=lobby['id'], chunk_x=0, chunk_y=0, data=tiles)
    db.session.add(chunk)
    db.session.commit()

    assert travel_scenario(lobby['id'], 14, 10) == 'road'
    tiles[10][10]['objects'][0].update(is_active=True)
    chunk.data = tiles
    db.session.commit()
    assert travel_scenario(lobby['id'], 13, 10) == 'near_base'
    assert travel_scenario(lobby['id'], 14, 10) == 'base_perimeter'
    assert travel_scenario(lobby['id'], 20, 20) == 'forest'
    assert travel_scenario(lobby['id'], 31, 31) == 'wild'


def test_gm_edits_and_moves_existing_world_object_without_duplicates(
    client, create_user, auth_headers
):
    gm = create_user('world-object-edit-gm')
    player = create_user('world-object-edit-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    MapService.get_chunks(lobby['id'], gm['id'], (0, 1, 0, 0))
    source_objects = [
        {'type': 'base', 'name': 'Old base', 'x': 0.1, 'z': -0.2,
         'scale': 1, 'rotation': 0, 'color': '#6c765d'},
        {'type': 'tree', 'x': 0.2, 'z': 0.1, 'color': '#2d5a27'},
    ]
    source_url = f"/lobbies/{lobby['id']}/chunks/0/0/tile/31/10"
    created = client.patch(source_url, headers=auth_headers(gm), json={'objects': source_objects})
    assert created.status_code == 200
    endpoint = f"/lobbies/{lobby['id']}/world-objects/edit"
    move = {
        'source': {'tile_x': 31, 'tile_y': 10, 'index': 0},
        'target': {'tile_x': 32, 'tile_y': 10},
        'expected': source_objects[0],
        'changes': {'name': 'Active base', 'x': -0.1, 'is_active': True},
    }
    assert client.post(endpoint, headers=auth_headers(player), json=move).status_code == 403
    moved = client.post(endpoint, headers=auth_headers(gm), json=move)
    assert moved.status_code == 200, moved.json
    assert len(moved.json['updates']) == 2
    db.session.expire_all()
    source = MapService.get_tile_data(lobby['id'], 31, 10)['objects']
    destination = MapService.get_tile_data(lobby['id'], 32, 10)['objects']
    assert source == [source_objects[1]]
    assert len(destination) == 1
    assert destination[0]['name'] == 'Active base'
    assert destination[0]['x'] == -0.1
    assert destination[0]['z'] == -0.2
    assert 'is_major' not in destination[0]
    assert destination[0]['is_active'] is True
    assert travel_scenario(lobby['id'], 34, 10) == 'near_base'
    assert travel_scenario(lobby['id'], 35, 10) == 'near_base'
    assert travel_scenario(lobby['id'], 36, 10) == 'base_perimeter'
    reloaded = client.get(
        f"/lobbies/{lobby['id']}/chunks?min_chunk_x=0&max_chunk_x=1&min_chunk_y=0&max_chunk_y=0",
        headers=auth_headers(gm),
    )
    assert reloaded.status_code == 200
    chunks = {item['chunk_x']: item['data'] for item in reloaded.get_json()}
    assert chunks[0][10][31]['objects'] == [source_objects[1]]
    assert chunks[1][10][0]['objects'] == destination

    stale = client.post(endpoint, headers=auth_headers(gm), json=move)
    assert stale.status_code == 409
    assert MapService.get_tile_data(lobby['id'], 32, 10)['objects'] == destination
    turn_off = client.post(endpoint, headers=auth_headers(gm), json={
        'source': {'tile_x': 32, 'tile_y': 10, 'index': 0},
        'target': {'tile_x': 32, 'tile_y': 10},
        'expected': destination[0],
        'changes': {'is_active': False},
    })
    assert turn_off.status_code == 200
    db.session.expire_all()
    assert MapService.get_tile_data(lobby['id'], 32, 10)['objects'][0]['is_active'] is False
    assert travel_scenario(lobby['id'], 34, 10) == 'wild'


def test_direct_world_object_delete_checks_permissions_and_snapshot(client, create_user, auth_headers):
    gm = create_user('direct-delete-gm')
    player = create_user('direct-delete-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    MapService.get_chunks(lobby['id'], gm['id'], (0, 0, 0, 0))
    objects = [{'type': 'factory', 'is_active': True}, {'type': 'tree', 'scale': 0.7}]
    MapService.update_tile(lobby['id'], gm['id'], 0, 0, 10, 10, {'objects': objects})
    endpoint = f"/lobbies/{lobby['id']}/world-objects/edit"
    payload = {
        'source': {'tile_x': 10, 'tile_y': 10, 'index': 0},
        'target': {'tile_x': 10, 'tile_y': 10},
        'expected': objects[0], 'changes': {}, 'delete': True,
    }
    assert client.post(endpoint, headers=auth_headers(player), json=payload).status_code == 403
    stale = {**payload, 'expected': {'type': 'factory'}}
    assert client.post(endpoint, headers=auth_headers(gm), json=stale).status_code == 409
    assert client.post(endpoint, headers=auth_headers(gm), json={**payload, 'delete': 'yes'}).status_code == 400
    result = client.post(endpoint, headers=auth_headers(gm), json=payload)
    assert result.status_code == 200
    assert result.json['index'] is None
    assert result.json['updates'][0]['updates']['objects'] == objects[1:]
    db.session.expire_all()
    assert MapService.get_tile_data(lobby['id'], 10, 10)['objects'] == objects[1:]
    assert client.post(endpoint, headers=auth_headers(gm), json=payload).status_code == 409


def test_world_buildings_require_active_flag_and_other_types_cannot_claim_zone(
    client, create_user, auth_headers
):
    gm = create_user('world-object-base-flags-gm')
    lobby = create_lobby(client, gm, auth_headers)
    MapService.get_chunks(lobby['id'], gm['id'], (0, 0, 0, 0))
    url = f"/lobbies/{lobby['id']}/chunks/0/0/tile/10/10"
    endpoint = f"/lobbies/{lobby['id']}/world-objects/edit"
    base = {'type': 'base', 'color': '#6c765d'}
    assert client.patch(url, headers=auth_headers(gm), json={'objects': [base]}).status_code == 200
    assert travel_scenario(lobby['id'], 12, 10) == 'wild'
    def edit(expected, changes):
        return client.post(endpoint, headers=auth_headers(gm), json={
            'source': {'tile_x': 10, 'tile_y': 10, 'index': 0},
            'target': {'tile_x': 10, 'tile_y': 10},
            'expected': expected, 'changes': changes,
        })
    legacy = {**base, 'is_major': True}
    assert client.patch(url, headers=auth_headers(gm), json={'objects': [legacy]}).status_code == 200
    assert travel_scenario(lobby['id'], 12, 10) == 'wild'
    legacy_active = {**base, 'is_major': False, 'is_active': True}
    assert client.patch(url, headers=auth_headers(gm), json={'objects': [legacy_active]}).status_code == 200
    assert travel_scenario(lobby['id'], 12, 10) == 'near_base'
    assert client.patch(url, headers=auth_headers(gm), json={'objects': [legacy]}).status_code == 200
    assert edit(legacy, {'is_active': True}).status_code == 200
    active = MapService.get_tile_data(lobby['id'], 10, 10)['objects'][0]
    assert 'is_major' not in active
    assert travel_scenario(lobby['id'], 12, 10) == 'near_base'
    assert edit(active, {'is_major': False}).status_code == 400
    assert edit(active, {'is_active': False}).status_code == 200
    inactive = MapService.get_tile_data(lobby['id'], 10, 10)['objects'][0]
    assert travel_scenario(lobby['id'], 12, 10) == 'wild'
    assert edit(inactive, {'type': 'tree', 'is_active': True}).status_code == 400
    assert edit(inactive, {'type': 'factory', 'is_active': True}).status_code == 200
    factory = MapService.get_tile_data(lobby['id'], 10, 10)['objects'][0]
    assert travel_scenario(lobby['id'], 12, 10) == 'near_base'
    assert edit(factory, {'type': 'road', 'is_active': True}).status_code == 400
    assert edit(factory, {'type': 'road'}).status_code == 200
    road = MapService.get_tile_data(lobby['id'], 10, 10)['objects'][0]
    assert 'is_active' not in road
    assert travel_scenario(lobby['id'], 12, 10) == 'wild'
    for kind in ('hamlet', 'village', 'factory', 'base', 'camp', 'house', 'fence'):
        protected = {'type': kind, 'is_active': True}
        assert client.patch(url, headers=auth_headers(gm), json={'objects': [protected]}).status_code == 200
        assert travel_scenario(lobby['id'], 12, 10) == 'near_base'
    for kind in ('tree', 'forest', 'road'):
        unprotected = {'type': kind, 'is_active': True}
        assert client.patch(url, headers=auth_headers(gm), json={'objects': [unprotected]}).status_code == 200
        assert travel_scenario(lobby['id'], 12, 10) == 'wild'


def test_empty_world_step_does_not_pause_and_gm_can_skip_random_event(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user('world-skip-gm')
    lobby = create_lobby(client, gm, auth_headers)
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={'name': 'Quiet party', 'tile_x': 5, 'tile_y': 5},
    ).get_json()
    endpoint = f"/lobbies/{lobby['id']}/world-groups/{group['id']}/move"
    monkeypatch.setattr('app.lobbies.roll_travel_event', lambda _scenario: None)
    quiet = client.post(endpoint, headers=auth_headers(gm), json={'tile_x': 6, 'tile_y': 5})
    assert quiet.status_code == 200
    assert quiet.get_json()['event_pending'] is False
    assert quiet.get_json()['time'] == {'game_day': 1, 'game_time_minutes': 490}
    assert WorldTravelEvent.query.filter_by(group_id=group['id']).count() == 0

    monkeypatch.setattr(
        'app.lobbies.roll_travel_event',
        lambda _scenario: SimpleNamespace(name='Мутанты', prompt='Угроза впереди.'),
    )
    encounter = client.post(endpoint, headers=auth_headers(gm), json={'tile_x': 7, 'tile_y': 5})
    assert encounter.status_code == 200
    assert encounter.get_json()['event_pending'] is True
    event = WorldTravelEvent.query.filter_by(group_id=group['id'], status='pending').one()
    skipped = client.patch(
        f"/lobbies/{lobby['id']}/world-events/{event.id}",
        headers=auth_headers(gm), json={'decision': 'reject'},
    )
    assert skipped.status_code == 200
    assert skipped.get_json()['status'] == 'rejected'
    assert ChatMessage.query.filter_by(lobby_id=lobby['id'], username='Событие').count() == 0


def test_placed_world_event_is_hidden_and_stops_group_on_route(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("placed-event-gm")
    player = create_user("placed-event-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Route party", "tile_x": 10, "tile_y": 10},
    ).get_json()
    event_endpoint = f"/lobbies/{lobby['id']}/world-map-events"

    forbidden = client.post(
        event_endpoint,
        headers=auth_headers(player),
        json={
            "name": "Ambush",
            "description": "Bandits open fire",
            "tile_x": 12,
            "tile_y": 10,
        },
    )
    created = client.post(
        event_endpoint,
        headers=auth_headers(gm),
        json={
            "name": "Ambush",
            "description": "Bandits open fire",
            "tile_x": 12,
            "tile_y": 10,
            "repeatable": False,
        },
    )
    player_view = client.get(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(player),
    ).get_json()
    monkeypatch.setattr("app.lobbies.random.random", lambda: 1.0)
    moved = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/move",
        headers=auth_headers(player),
        json={"tile_x": 13, "tile_y": 10},
    )

    assert forbidden.status_code == 403
    assert created.status_code == 201
    assert player_view["map_events"] == []
    assert moved.status_code == 200
    assert moved.get_json()["placed_event_triggered"] is True
    assert moved.get_json()["event_pending"] is True
    assert (moved.get_json()["group"]["tile_x"], moved.get_json()["group"]["tile_y"]) == (12, 10)
    map_event = db.session.get(WorldMapEvent, created.get_json()["id"])
    assert map_event.is_active is False
    pending = WorldTravelEvent.query.filter_by(group_id=group["id"], status="pending").one()
    assert pending.world_map_event_id == map_event.id
    assert pending.description == "Ambush: Bandits open fire"


def test_repeatable_world_event_remains_on_map_after_trigger(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("repeat-event-gm")
    lobby = create_lobby(client, gm, auth_headers)
    group = client.post(
        f"/lobbies/{lobby['id']}/world-groups",
        headers=auth_headers(gm),
        json={"name": "Repeat party", "tile_x": 4, "tile_y": 4},
    ).get_json()
    event = client.post(
        f"/lobbies/{lobby['id']}/world-map-events",
        headers=auth_headers(gm),
        json={
            "name": "Radiation field",
            "description": "The dosimeter crackles",
            "tile_x": 5,
            "tile_y": 5,
            "repeatable": True,
        },
    ).get_json()
    monkeypatch.setattr("app.lobbies.random.random", lambda: 1.0)

    moved = client.post(
        f"/lobbies/{lobby['id']}/world-groups/{group['id']}/move",
        headers=auth_headers(gm),
        json={"tile_x": 6, "tile_y": 6},
    )

    assert moved.status_code == 200
    assert moved.get_json()["group"]["tile_x"] == 5
    assert db.session.get(WorldMapEvent, event["id"]).is_active is True


def test_gm_rest_event_advances_lobby_time_for_selected_characters(
    client, create_user, auth_headers
):
    gm = create_user("rest-event-gm")
    player = create_user("rest-event-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    sleeper = create_character(
        client,
        lobby,
        player,
        auth_headers,
        data={
            "health": {
                "current": 100,
                "max": 700,
                "zones": {},
                "effects": [],
                "exhaustion": 1,
                "intoxication": 90,
                "combatMeta": {"sleepMode": "bed"},
                "needs": {
                    "day": 1,
                    "mealsToday": 1,
                    "drinksToday": 3,
                    "sleptToday": False,
                },
            },
        },
    )
    observer = create_character(
        client,
        lobby,
        gm,
        auth_headers,
        data={
            "health": {
                "current": 200,
                "max": 700,
                "zones": {},
                "stress": 5,
                "effects": [{
                    "type": "delayed_adjustment",
                    "remaining": 10,
                    "remaining_seconds": 600,
                    "time_unit": "minute",
                    "tick": "time_elapsed",
                    "adjustments": [{"field": "stress", "delta": -2, "min": 0}],
                }],
                "needs": {"day": 1, "mealsToday": 0, "drinksToday": 0},
            },
        },
    )
    inactive_npc = create_character(
        client,
        lobby,
        gm,
        auth_headers,
        data={
            "health": {
                "current": 300,
                "max": 700,
                "zones": {},
                "stress": 5,
                "effects": [{
                    "type": "delayed_adjustment",
                    "remaining": 10,
                    "remaining_seconds": 600,
                    "time_unit": "minute",
                    "tick": "time_elapsed",
                    "adjustments": [{"field": "stress", "delta": -2, "min": 0}],
                }],
                "needs": {"day": 1, "mealsToday": 0, "drinksToday": 0},
            },
        },
    )
    endpoint = f"/lobbies/{lobby['id']}/rest"

    activity = client.patch(
        f"/lobbies/{lobby['id']}/characters/time-active",
        headers=auth_headers(gm),
        json={"character_ids": [sleeper["id"], observer["id"]]},
    )

    forbidden = client.post(
        endpoint,
        headers=auth_headers(player),
        json={"type": "sleep", "character_ids": [sleeper["id"]]},
    )
    completed = client.post(
        endpoint,
        headers=auth_headers(gm),
        json={"type": "sleep", "character_ids": [sleeper["id"]]},
    )

    assert activity.status_code == 200
    assert forbidden.status_code == 403
    assert completed.status_code == 200
    assert completed.get_json()["game_day"] == 1
    assert completed.get_json()["game_time_minutes"] == 16 * 60

    sleeper_health = db.session.get(LobbyCharacter, sleeper["id"]).data["health"]
    observer_health = db.session.get(LobbyCharacter, observer["id"]).data["health"]
    inactive_health = db.session.get(LobbyCharacter, inactive_npc["id"]).data["health"]
    assert sleeper_health["current"] == 450
    assert sleeper_health["intoxication"] == 15
    assert sleeper_health["exhaustion"] == 2
    assert sleeper_health["needs"]["day"] == 2
    assert sleeper_health["needs"]["lastDay"]["missed"] == ["еда"]
    assert observer_health["current"] == 200
    assert observer_health["needs"]["day"] == 1
    assert observer_health["stress"] == 3
    assert observer_health["effects"] == []
    assert inactive_health["stress"] == 5
    assert inactive_health["effects"][0]["remaining_seconds"] == 600
    assert db.session.get(LobbyCharacter, inactive_npc["id"]).time_active is False


def test_banned_user_is_kept_only_in_banned_list(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("ban-list-gm")
    player = create_user("ban-list-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)

    response = client.post(
        f"/lobbies/{lobby['id']}/ban/{player['id']}",
        headers=auth_headers(gm),
    )
    details = client.get(
        f"/lobbies/{lobby['id']}",
        headers=auth_headers(gm),
    )
    participants = client.get(
        f"/lobbies/{lobby['id']}/participants_characters",
        headers=auth_headers(gm),
    )
    banned = client.get(
        f"/lobbies/{lobby['id']}/banned",
        headers=auth_headers(gm),
    )

    assert response.status_code == 200
    assert [item["user_id"] for item in details.get_json()["participants"]] == [gm["id"]]
    assert [item["user_id"] for item in participants.get_json()] == [gm["id"]]
    assert banned.get_json() == [{
        "user_id": player["id"],
        "username": player["username"],
    }]
    stored = db.session.get(
        LobbyParticipant,
        {"lobby_id": lobby["id"], "user_id": player["id"]},
    )
    assert stored.is_banned is True


def test_non_participant_cannot_open_private_lobby(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("private-gm")
    outsider = create_user("outsider")
    lobby = create_lobby(client, gm, auth_headers)

    response = client.get(
        f"/lobbies/{lobby['id']}",
        headers=auth_headers(outsider),
    )

    assert response.status_code == 403


def test_only_gm_can_delete_lobby(client, create_user, auth_headers):
    gm = create_user("delete-gm")
    player = create_user("delete-player")
    lobby = create_lobby(client, gm, auth_headers)
    client.post(
        f"/lobbies/{lobby['id']}/join",
        headers=auth_headers(player),
    )

    forbidden = client.delete(
        f"/lobbies/{lobby['id']}",
        headers=auth_headers(player),
    )
    deleted = client.delete(
        f"/lobbies/{lobby['id']}",
        headers=auth_headers(gm),
    )

    assert forbidden.status_code == 403
    assert deleted.status_code == 200
    db.session.expire_all()
    assert db.session.get(Lobby, lobby["id"]).is_active is False


def test_my_lobbies_validates_pagination(client, create_user, auth_headers):
    gm = create_user("pagination-gm")

    bad_limit = client.get("/lobbies/my?limit=0", headers=auth_headers(gm))
    bad_offset = client.get("/lobbies/my?offset=-1", headers=auth_headers(gm))

    assert bad_limit.status_code == 400
    assert bad_offset.status_code == 400


def test_lobby_schema_rejects_invalid_map_dimensions(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("validation-gm")

    response = client.post(
        "/lobbies/",
        headers=auth_headers(gm),
        json={
            "name": "Broken map",
            "map_type": "empty",
            "chunks_width": 0,
            "chunks_height": 3,
        },
    )

    assert response.status_code == 400


def test_gm_can_delete_character_owned_by_player(
    client,
    create_user,
    auth_headers,
    monkeypatch,
):
    gm = create_user("character-delete-gm")
    player = create_user("character-delete-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, player, auth_headers)
    location = Location(
        lobby_id=lobby["id"],
        name="Delete test",
        world_tile_x=0,
        world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    db.session.add(LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
    ))
    db.session.commit()
    emitted = []
    monkeypatch.setattr(
        'app.lobbies.socketio.emit',
        lambda event, payload, **kwargs: emitted.append((event, payload, kwargs)),
    )

    response = client.delete(
        f"/lobbies/characters/{character['id']}",
        headers=auth_headers(gm),
    )

    assert response.status_code == 200
    assert db.session.get(LobbyCharacter, character["id"]) is None
    assert LocationCharacter.query.filter_by(character_id=character["id"]).count() == 0
    removed = [payload for event, payload, _ in emitted if event == 'location_character_removed']
    assert removed == [{
        'location_id': location.id,
        'character_id': character['id'],
    }]


def test_only_gm_can_remove_character_model_from_location(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("despawn-gm")
    player = create_user("despawn-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, player, auth_headers)
    location = Location(
        lobby_id=lobby["id"],
        name="Despawn test",
        world_tile_x=0,
        world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    db.session.add(LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
    ))
    db.session.commit()
    endpoint = (
        f"/lobbies/{lobby['id']}/locations/{location.id}"
        f"/characters/{character['id']}"
    )

    forbidden = client.delete(endpoint, headers=auth_headers(player))
    removed = client.delete(endpoint, headers=auth_headers(gm))

    assert forbidden.status_code == 403
    assert removed.status_code == 200
    assert db.session.get(LobbyCharacter, character["id"]) is not None
    assert LocationCharacter.query.filter_by(
        location_id=location.id,
        character_id=character["id"],
    ).count() == 0


def test_location_edit_can_delete_structures_without_deleting_ground_items(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("clear-structures-gm")
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers)
    location = Location(
        lobby_id=lobby["id"],
        name="Rebuild test",
        world_tile_x=0,
        world_tile_z=0,
        tiles_data=[],
    )
    db.session.add(location)
    db.session.flush()
    wall = LocationObject(
        location_id=location.id,
        name="Wall",
        type="wall",
        tile_x=2,
        tile_y=2,
        properties={"cover_enabled": True},
    )
    ground_item = LocationObject(
        location_id=location.id,
        name="Пол",
        type="ground_item",
        tile_x=3,
        tile_y=3,
        properties={"is_ground_item": True, "contents": [{"name": "Бинт"}]},
    )
    db.session.add_all([wall, ground_item])
    db.session.flush()
    location_character = LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
        cover_object_id=wall.id,
        weapon_braced=True,
        braced_weapon_index=0,
    )
    db.session.add(location_character)
    db.session.commit()

    response = client.put(
        f"/lobbies/{lobby['id']}/locations/{location.id}",
        headers=auth_headers(gm),
        json={"delete_structures": True},
    )

    assert response.status_code == 200
    assert response.get_json()["deleted_structure_count"] == 1
    assert db.session.get(LocationObject, wall.id) is None
    assert db.session.get(LocationObject, ground_item.id) is not None
    db.session.refresh(location_character)
    assert location_character.cover_object_id is None
    assert location_character.weapon_braced is False
    assert location_character.braced_weapon_index is None


def test_location_join_lookup_does_not_spawn_selected_character(
    client,
    create_user,
    auth_headers,
):
    from app.sockets.location import _find_existing_location_character

    gm = create_user("location-join-gm")
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers)
    location = Location(
        lobby_id=lobby["id"],
        name="Empty location",
        world_tile_x=0,
        world_tile_z=0,
    )
    db.session.add(location)
    db.session.commit()

    result = _find_existing_location_character(location.id, character["id"])

    assert result is None
    assert LocationCharacter.query.filter_by(
        location_id=location.id,
        character_id=character["id"],
    ).count() == 0


def test_controller_can_change_posture_outside_combat_for_free(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("posture-gm")
    player = create_user("posture-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, player, auth_headers)
    location = Location(
        lobby_id=lobby["id"],
        name="Posture test",
        world_tile_x=0,
        world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    location_character = LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
        controlled_by=player["id"],
        posture="standing",
    )
    db.session.add(location_character)
    db.session.commit()

    response = client.patch(
        (
            f"/lobbies/{lobby['id']}/locations/{location.id}"
            f"/characters/{character['id']}/posture"
        ),
        headers=auth_headers(player),
        json={"posture": "prone"},
    )

    assert response.status_code == 200
    db.session.refresh(location_character)
    assert location_character.posture == "prone"
    assert location_character.action_points_current == 5
    assert location_character.movement_points_current == 0


def test_prone_visual_variant_is_persisted_without_changing_combat_posture(
    app,
    client,
    create_user,
    auth_headers,
):
    gm = create_user('visual-pose-gm')
    player = create_user('visual-pose-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, player, auth_headers)
    location = Location(lobby_id=lobby['id'], name='Visual pose', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    placed = LocationCharacter(
        location_id=location.id,
        character_id=character['id'],
        controlled_by=player['id'],
        posture='standing',
    )
    db.session.add(placed)
    db.session.commit()
    assert placed.prone_on_back is False

    url = f"/lobbies/{lobby['id']}/locations/{location.id}/characters/{character['id']}"
    assert client.patch(f'{url}/visual-pose', headers=auth_headers(player),
                        json={'prone_on_back': True}).status_code == 409
    assert client.patch(f'{url}/posture', headers=auth_headers(player),
                        json={'posture': 'prone'}).status_code == 200
    assert client.patch(f'{url}/visual-pose', headers=auth_headers(player),
                        json={'prone_on_back': 'yes'}).status_code == 400
    response = client.patch(f'{url}/visual-pose', headers=auth_headers(player),
                            json={'prone_on_back': True})
    assert response.status_code == 200
    db.session.expire_all()
    saved = db.session.get(LocationCharacter, placed.id)
    assert saved.prone_on_back is True
    assert saved.posture == 'prone'
    assert saved.action_points_current == 5
    assert saved.movement_points_current == 0

    from flask_jwt_extended import create_access_token
    from app.extensions import socketio
    from app.sockets.location import handle_join_location

    socketio.on_event('join_location', handle_join_location)
    connection = socketio.test_client(app)
    try:
        connection.emit('join_location', {
            'token': create_access_token(identity=str(player['id'])),
            'location_id': location.id,
            'character_id': character['id'],
        })
        states = [event['args'][0] for event in connection.get_received()
                  if event['name'] == 'location_state']
        assert states
        restored = next(item for item in states[-1]
                        if item['character_id'] == character['id'])
        assert restored['posture'] == 'prone'
        assert restored['prone_on_back'] is True
    finally:
        connection.disconnect()


def test_combat_start_rolls_tactics_initiative_only_for_selected_characters(
    client,
    create_user,
    auth_headers,
    monkeypatch,
):
    gm = create_user("initiative-gm")
    lobby = create_lobby(client, gm, auth_headers)
    first = create_character(
        client,
        lobby,
        gm,
        auth_headers,
        data={
            "skills": {
                "other": {
                    "tactics": {
                        "base": 16,
                        "bonus": 2,
                    }
                }
            }
        },
    )
    second = create_character(
        client,
        lobby,
        gm,
        auth_headers,
    )
    location = Location(
        lobby_id=lobby["id"],
        name="Initiative test",
        world_tile_x=0,
        world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    selected = LocationCharacter(
        location_id=location.id,
        character_id=first["id"],
    )
    excluded = LocationCharacter(
        location_id=location.id,
        character_id=second["id"],
    )
    db.session.add_all([selected, excluded])
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda start, end: 10)

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start",
        headers=auth_headers(gm),
        json={"location_character_ids": [selected.id]},
    )

    assert response.status_code == 200
    state = response.get_json()
    assert state["turn_order"] == [selected.id]
    selected_state = next(
        item for item in state["characters"]
        if item["location_character_id"] == selected.id
    )
    excluded_state = next(
        item for item in state["characters"]
        if item["location_character_id"] == excluded.id
    )
    assert selected_state["initiative_roll"] == 10
    assert selected_state["initiative_bonus"] == 4
    assert selected_state["initiative_total"] == 14
    assert excluded_state["initiative_roll"] is None
    chat_message = ChatMessage.query.filter_by(
        lobby_id=lobby["id"],
        username="Бой",
    ).one()
    assert "Инициатива:" in chat_message.message
    assert "Test character: d20 10 +4 = 14" in chat_message.message
    assert excluded_state["initiative_total"] is None


@pytest.mark.parametrize('kind,health_change', [
    ('mutant', {'effects': [{'type': 'death', 'active': True}]}),
    ('human', {'effects': [{'type': 'death', 'active': True}]}),
    ('human', {'bloodStage': 'fatal'}),
    ('human', {'organs': {'brain': {'current': 0}}}),
    ('human', {'organs': {'skull': {'current': 0}}}),
])
@pytest.mark.parametrize('explicit_selection', [True, False])
def test_dead_character_cannot_join_initiative_at_combat_start(
    client, create_user, auth_headers, kind, health_change, explicit_selection,
):
    gm = create_user(f'dead-initiative-{kind}')
    lobby = create_lobby(client, gm, auth_headers)
    alive = create_character(client, lobby, gm, auth_headers)
    dead_data = mutant_character_data(mutant_profile('Собака')) if kind == 'mutant' else {}
    dead_data['health'] = {**dead_data.get('health', {}), **health_change}
    dead = create_character(client, lobby, gm, auth_headers, dead_data)
    location = Location(lobby_id=lobby['id'], name='Dead initiative', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    living_model = LocationCharacter(location_id=location.id, character_id=alive['id'])
    dead_model = LocationCharacter(location_id=location.id, character_id=dead['id'])
    db.session.add_all([living_model, dead_model])
    db.session.commit()

    payload = {'location_character_ids': [living_model.id, dead_model.id]} if explicit_selection else {}
    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start",
        headers=auth_headers(gm), json=payload,
    )

    if explicit_selection:
        assert response.status_code == 400
        assert not LocationCombatState.query.filter_by(location_id=location.id, status='active').first()
        assert db.session.get(LocationCharacter, dead_model.id).initiative_roll is None
    else:
        assert response.status_code == 200
        state = response.get_json()
        assert state['turn_order'] == [living_model.id]
        assert db.session.get(LocationCharacter, dead_model.id).initiative_roll is None


def test_combat_cannot_start_with_only_dead_characters(client, create_user, auth_headers):
    gm = create_user('only-dead-initiative-gm')
    lobby = create_lobby(client, gm, auth_headers)
    dead = create_character(
        client, lobby, gm, auth_headers,
        {'health': {'effects': [{'type': 'death', 'active': True}]}},
    )
    location = Location(lobby_id=lobby['id'], name='Only dead', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    db.session.add(LocationCharacter(location_id=location.id, character_id=dead['id']))
    db.session.commit()

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start",
        headers=auth_headers(gm), json={},
    )

    assert response.status_code == 400
    assert not LocationCombatState.query.filter_by(location_id=location.id, status='active').first()


def test_attack_start_request_requires_gm_approval_and_preserves_ammo(
    client, create_user, auth_headers,
):
    gm = create_user('combat-request-gm')
    player = create_user('combat-request-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    attacker = create_character(
        client, lobby, player, auth_headers,
        data={'weapons': [{'name': 'Тестовое оружие', 'ammo': 5}]},
    )
    defender = create_character(client, lobby, gm, auth_headers)
    location = Location(lobby_id=lobby['id'], name='Attack request', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    actor = LocationCharacter(location_id=location.id, character_id=attacker['id'])
    target = LocationCharacter(location_id=location.id, character_id=defender['id'])
    db.session.add_all([actor, target])
    db.session.commit()
    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start-requests"

    created = client.post(
        url, headers=auth_headers(player),
        json={'actor_location_character_id': actor.id},
    )
    assert created.status_code == 201
    request_id = created.get_json()['id']
    repeated = client.post(
        url, headers=auth_headers(player),
        json={'actor_location_character_id': actor.id},
    )
    assert repeated.status_code == 200
    assert repeated.get_json()['id'] == request_id
    assert db.session.get(LobbyCharacter, attacker['id']).data['weapons'][0]['ammo'] == 5
    assert client.get(url, headers=auth_headers(player)).status_code == 403
    assert len(client.get(url, headers=auth_headers(gm)).get_json()) == 1

    response_url = f'{url}/{request_id}/response'
    assert client.post(
        response_url, headers=auth_headers(player), json={'decision': 'approve'},
    ).status_code == 403
    rejected = client.post(
        response_url, headers=auth_headers(gm), json={'decision': 'reject'},
    )
    assert rejected.status_code == 200
    assert rejected.get_json()['status'] == 'rejected'
    assert client.get(url, headers=auth_headers(gm)).get_json() == []

    created_again = client.post(
        url, headers=auth_headers(player),
        json={'actor_location_character_id': actor.id},
    )
    approved = client.post(
        f"{url}/{created_again.get_json()['id']}/response",
        headers=auth_headers(gm),
        json={'decision': 'approve', 'location_character_ids': [actor.id, target.id]},
    )
    assert approved.status_code == 200
    state = approved.get_json()
    assert state['status'] == 'active'
    assert state['turn_order'][0] == actor.id
    assert state['current_character']['character_id'] == attacker['id']
    assert db.session.get(LobbyCharacter, attacker['id']).data['weapons'][0]['ammo'] == 5
    assert client.get(url, headers=auth_headers(gm)).get_json() == []


def test_attack_start_request_can_use_normal_initiative_when_gm_unchecks_first(
    client, create_user, auth_headers, monkeypatch,
):
    gm = create_user('combat-roll-gm')
    player = create_user('combat-roll-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    attacker = create_character(client, lobby, player, auth_headers)
    defender = create_character(
        client, lobby, gm, auth_headers,
        data={'skills': {'other': {'tactics': {'base': 16, 'bonus': 2}}}},
    )
    location = Location(lobby_id=lobby['id'], name='Rolled initiative', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    actor = LocationCharacter(location_id=location.id, character_id=attacker['id'])
    target = LocationCharacter(location_id=location.id, character_id=defender['id'])
    db.session.add_all([actor, target])
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint', lambda _low, _high: 10)

    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start-requests"
    created = client.post(
        url, headers=auth_headers(player),
        json={'actor_location_character_id': actor.id},
    )
    assert created.status_code == 201
    response_url = f"{url}/{created.get_json()['id']}/response"
    excluded = client.post(
        response_url, headers=auth_headers(gm),
        json={
            'decision': 'approve', 'location_character_ids': [target.id],
            'initiator_first': False,
        },
    )
    assert excluded.status_code == 400

    approved = client.post(
        response_url, headers=auth_headers(gm),
        json={
            'decision': 'approve', 'location_character_ids': [actor.id, target.id],
            'initiator_first': False,
        },
    )
    assert approved.status_code == 200
    state = approved.get_json()
    assert state['turn_order'] == [target.id, actor.id]
    initiatives = {
        item['location_character_id']: item['initiative_total']
        for item in state['characters']
    }
    assert initiatives[target.id] > initiatives[actor.id]


@pytest.mark.parametrize('initiator_first', [True, False])
@pytest.mark.parametrize('actor_kind,health_change', [
    ('mutant', {'effects': [{'type': 'death', 'active': True}]}),
    ('mutant', {'current': 0}),
    ('human', {'effects': [{'type': 'death', 'active': True}]}),
    ('human', {'effects': [{'type': 'critical_condition', 'active': True}]}),
    ('human', {'effects': [{'type': 'unconsciousness', 'active': True}]}),
    ('human', {'effects': [{'type': 'shock', 'active': True}]}),
])
def test_incapacitated_character_cannot_start_combat_by_action(
    client, create_user, auth_headers, actor_kind, health_change, initiator_first,
):
    gm = create_user(f'invalid-combat-initiator-{actor_kind}')
    lobby = create_lobby(client, gm, auth_headers)
    actor_data = mutant_character_data(mutant_profile('Собака')) if actor_kind == 'mutant' else {}
    actor_data['health'] = {**actor_data.get('health', {}), **health_change}
    initiator = create_character(client, lobby, gm, auth_headers, actor_data)
    location = Location(lobby_id=lobby['id'], name='Invalid initiator', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    placed = LocationCharacter(location_id=location.id, character_id=initiator['id'])
    db.session.add(placed)
    db.session.commit()

    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start"
    response = client.post(
        url, headers=auth_headers(gm),
        json={
            'location_character_ids': [placed.id],
            'initiator_location_character_id': placed.id,
            'initiator_first': initiator_first,
        },
    )

    assert response.status_code == 400
    assert not LocationCombatState.query.filter_by(location_id=location.id, status='active').first()


@pytest.mark.parametrize('health_change', [
    {'effects': [{'type': 'death', 'active': True}]},
    {'effects': [{'type': 'critical_condition', 'active': True}]},
    {'effects': [{'type': 'shock', 'active': True}]},
])
def test_incapacitated_character_cannot_request_combat_start(
    client, create_user, auth_headers, health_change,
):
    gm = create_user('invalid-combat-request-gm')
    player = create_user('invalid-combat-request-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    attacker = create_character(client, lobby, player, auth_headers, {'health': health_change})
    location = Location(lobby_id=lobby['id'], name='Invalid request', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    actor = LocationCharacter(location_id=location.id, character_id=attacker['id'])
    db.session.add(actor)
    db.session.commit()

    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start-requests"
    response = client.post(
        url, headers=auth_headers(player),
        json={'actor_location_character_id': actor.id},
    )

    assert response.status_code == 400
    assert client.get(url, headers=auth_headers(gm)).get_json() == []


def test_gm_action_start_with_normal_initiative_keeps_living_initiator(
    client, create_user, auth_headers, monkeypatch,
):
    gm = create_user('direct-combat-normal-initiative-gm')
    lobby = create_lobby(client, gm, auth_headers)
    attacker = create_character(client, lobby, gm, auth_headers)
    defender = create_character(
        client, lobby, gm, auth_headers,
        {'skills': {'other': {'tactics': {'base': 16, 'bonus': 2}}}},
    )
    location = Location(lobby_id=lobby['id'], name='Normal initiative', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    actor = LocationCharacter(location_id=location.id, character_id=attacker['id'])
    target = LocationCharacter(location_id=location.id, character_id=defender['id'])
    db.session.add_all([actor, target])
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint', lambda _low, _high: 10)

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start",
        headers=auth_headers(gm),
        json={
            'location_character_ids': [actor.id, target.id],
            'initiator_location_character_id': actor.id,
            'initiator_first': False,
        },
    )

    assert response.status_code == 200
    assert response.get_json()['turn_order'] == [target.id, actor.id]


@pytest.mark.parametrize('initiator_first', [True, False])
def test_combat_start_request_cannot_be_approved_after_mutant_dies(
    client, create_user, auth_headers, initiator_first,
):
    gm = create_user('late-mutant-combat-gm')
    player = create_user('late-mutant-combat-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    mutant = create_character(
        client, lobby, player, auth_headers,
        mutant_character_data(mutant_profile('Собака')),
    )
    location = Location(lobby_id=lobby['id'], name='Late mutant death', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    actor = LocationCharacter(location_id=location.id, character_id=mutant['id'])
    db.session.add(actor)
    db.session.commit()

    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/start-requests"
    created = client.post(
        url, headers=auth_headers(player),
        json={'actor_location_character_id': actor.id},
    )
    assert created.status_code == 201
    stored = db.session.get(LobbyCharacter, mutant['id'])
    stored.data = {
        **stored.data,
        'health': {
            **stored.data['health'],
            'effects': [{'type': 'death', 'active': True}],
        },
    }
    db.session.commit()

    approved = client.post(
        f"{url}/{created.get_json()['id']}/response",
        headers=auth_headers(gm),
        json={
            'decision': 'approve',
            'location_character_ids': [actor.id],
            'initiator_first': initiator_first,
        },
    )

    assert approved.status_code == 400
    assert not LocationCombatState.query.filter_by(location_id=location.id, status='active').first()


def test_gm_can_adjust_stress_without_placing_character_on_location(
    client, create_user, auth_headers,
):
    gm = create_user('stress-outside-gm')
    player = create_user('stress-outside-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, player, auth_headers)
    url = f"/lobbies/{lobby['id']}/characters/{character['id']}/stress"

    assert client.post(url, headers=auth_headers(player), json={'amount': 1}).status_code == 403
    increased = client.post(url, headers=auth_headers(gm), json={'amount': 1})
    assert increased.status_code == 200
    assert increased.get_json()['data']['health']['stress'] == 1
    reduced = client.post(url, headers=auth_headers(gm), json={'amount': -1})
    assert reduced.status_code == 200
    assert reduced.get_json()['data']['health']['stress'] == 0


def test_new_world_chunks_have_no_standalone_anomaly_objects(
    client, create_user, auth_headers,
):
    gm = create_user('world-no-anomalies-gm')
    lobby = create_lobby(client, gm, auth_headers)
    chunk = MapService.get_chunks(lobby['id'], gm['id'], (0, 0, 0, 0))[0]

    assert all(
        obj.get('type') != 'anomaly'
        for row in chunk['data'] for tile in row for obj in tile.get('objects', [])
    )


def test_only_gm_can_change_character_visibility(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("visibility-gm")
    owner = create_user("visibility-owner")
    viewer = create_user("visibility-viewer")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, owner, auth_headers)
    join_lobby(client, lobby, viewer, auth_headers)
    character = create_character(client, lobby, owner, auth_headers)
    endpoint = f"/lobbies/characters/{character['id']}/visibility"

    owner_response = client.put(
        endpoint,
        headers=auth_headers(owner),
        json={"visible_to": [viewer["id"]]},
    )
    gm_response = client.put(
        endpoint,
        headers=auth_headers(gm),
        json={"visible_to": [viewer["id"]]},
    )

    assert owner_response.status_code == 403
    assert gm_response.status_code == 200
    assert db.session.get(LobbyCharacter, character["id"]).visible_to == [viewer["id"]]


def test_edit_permission_implies_visibility_and_allows_sheet_update(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("edit-rights-gm")
    owner = create_user("edit-rights-owner")
    editor = create_user("edit-rights-editor")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, owner, auth_headers)
    join_lobby(client, lobby, editor, auth_headers)
    character = create_character(client, lobby, owner, auth_headers)

    access = client.put(
        f"/lobbies/characters/{character['id']}/visibility",
        headers=auth_headers(gm),
        json={"visible_to": [], "editable_to": [editor["id"]]},
    )
    opened = client.get(
        f"/lobbies/characters/{character['id']}",
        headers=auth_headers(editor),
    )
    updated = client.put(
        f"/lobbies/characters/{character['id']}",
        headers=auth_headers(editor),
        json={"data": {"notes": "Shared edit", "_revision": opened.get_json()['data']['_revision']}},
    )

    stored = db.session.get(LobbyCharacter, character["id"])
    assert access.status_code == 200
    assert opened.status_code == 200
    assert opened.get_json()["can_edit"] is True
    assert updated.status_code == 200
    assert stored.visible_to == [editor["id"]]
    assert stored.editable_to == [editor["id"]]
    assert stored.data["notes"] == "Shared edit"


def test_only_gm_or_controller_can_end_combat_turn(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("combat-end-gm")
    controller = create_user("combat-end-controller")
    spectator = create_user("combat-end-spectator")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, controller, auth_headers)
    join_lobby(client, lobby, spectator, auth_headers)
    controller_character = create_character(client, lobby, controller, auth_headers)
    spectator_character = create_character(client, lobby, spectator, auth_headers)
    location = Location(
        lobby_id=lobby["id"],
        name="Combat arena",
        world_tile_x=0,
        world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    controller_loc_char = LocationCharacter(
        location_id=location.id,
        character_id=controller_character["id"],
        controlled_by=controller["id"],
    )
    spectator_loc_char = LocationCharacter(
        location_id=location.id,
        character_id=spectator_character["id"],
        controlled_by=spectator["id"],
    )
    db.session.add_all([controller_loc_char, spectator_loc_char])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[spectator_loc_char.id, controller_loc_char.id],
        current_location_character_id=spectator_loc_char.id,
    ))
    db.session.commit()

    forbidden = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(controller),
        json={},
    )
    allowed_for_controller = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(spectator),
        json={},
    )
    allowed_for_gm = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(gm),
        json={},
    )

    assert forbidden.status_code == 403
    assert allowed_for_controller.status_code == 200
    assert allowed_for_gm.status_code == 200


def test_only_gm_can_remove_current_character_from_initiative(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("initiative-remove-gm")
    player = create_user("initiative-remove-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    first_character = create_character(client, lobby, gm, auth_headers)
    second_character = create_character(client, lobby, player, auth_headers)
    location = Location(
        lobby_id=lobby["id"], name="Initiative removal", world_tile_x=0, world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    first = LocationCharacter(location_id=location.id, character_id=first_character["id"])
    second = LocationCharacter(location_id=location.id, character_id=second_character["id"])
    db.session.add_all([first, second])
    db.session.flush()
    first.initiative_roll = 20
    first.initiative_total = 20
    second.initiative_roll = 10
    second.initiative_total = 10
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[first.id, second.id],
        current_location_character_id=first.id,
    ))
    db.session.commit()
    endpoint = (
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/participants/{first.id}"
    )

    forbidden = client.delete(endpoint, headers=auth_headers(player))
    allowed = client.delete(endpoint, headers=auth_headers(gm))

    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    state = allowed.get_json()
    assert state["turn_order"] == [second.id]
    assert state["current_location_character_id"] == second.id
    assert state["removed_location_character_id"] == first.id
    assert db.session.get(LocationCharacter, first.id) is not None
    assert db.session.get(LocationCharacter, first.id).initiative_roll is None


def test_gm_adds_combat_participant_for_next_round_only(client, create_user, auth_headers):
    gm = create_user('initiative-add-gm')
    player = create_user('initiative-add-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    characters = [create_character(client, lobby, gm, auth_headers) for _ in range(3)]
    location = Location(lobby_id=lobby['id'], name='Reinforcements', world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    placed = [LocationCharacter(location_id=location.id, character_id=character['id']) for character in characters]
    db.session.add_all(placed)
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id, status='active', round_number=1, turn_index=0,
        turn_order=[placed[0].id, placed[1].id],
        current_location_character_id=placed[0].id,
    ))
    db.session.commit()
    endpoint = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/participants/{placed[2].id}"

    assert client.post(endpoint, headers=auth_headers(player)).status_code == 403
    response = client.post(endpoint, headers=auth_headers(gm))
    assert response.status_code == 200
    state = response.get_json()
    assert state['turn_order'] == [placed[2].id, placed[0].id, placed[1].id]
    assert state['current_location_character_id'] == placed[0].id
    assert state['round_number'] == 1
    assert client.post(endpoint, headers=auth_headers(gm)).status_code == 400

    next_turn = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(gm), json={},
    )
    assert next_turn.status_code == 200
    assert next_turn.get_json()['current_location_character_id'] == placed[1].id
    following_turn = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(gm), json={},
    )
    assert following_turn.status_code == 200
    assert following_turn.get_json()['current_location_character_id'] == placed[2].id
    assert following_turn.get_json()['round_number'] == 2


def test_only_gm_removes_character_addiction(client, create_user, auth_headers):
    gm = create_user('remove-addiction-gm')
    player = create_user('remove-addiction-player')
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    character = create_character(client, lobby, gm, auth_headers)
    stored = db.session.get(LobbyCharacter, character['id'])
    data = dict(stored.data)
    data['health'] = {
        'addictions': {
            'records': {'caffeine': {'key': 'caffeine', 'label': 'Кофеин', 'active': True}},
            'exposures': {'caffeine': {'dose': 3}},
        },
        'effects': [{'id': 'addiction-withdrawal-caffeine', 'type': 'addiction_withdrawal'}],
    }
    stored.data = data
    db.session.commit()
    endpoint = f"/lobbies/{lobby['id']}/characters/{character['id']}/addictions/caffeine"

    assert client.delete(endpoint, headers=auth_headers(player)).status_code == 403
    response = client.delete(endpoint, headers=auth_headers(gm))
    assert response.status_code == 200
    health = response.get_json()['data']['health']
    assert not health['addictions']['records']
    assert not health['addictions']['exposures']
    assert not any(effect.get('type') == 'addiction_withdrawal' for effect in health['effects'])


def test_gm_heal_restores_mutant_specific_maximums(client, create_user, auth_headers):
    gm = create_user('mutant-heal-gm')
    lobby = create_lobby(client, gm, auth_headers)
    profile = mutant_profile(mutant_catalog()[0]['name'])
    mutant = create_character(client, lobby, gm, auth_headers, mutant_character_data(profile))
    stored = db.session.get(LobbyCharacter, mutant['id'])
    data = dict(stored.data)
    health = dict(data['health'])
    health['current'] = 1
    health['painLevel'] = 7
    health['bloodStage'] = 'severe'
    health['zones'] = {key: dict(zone) for key, zone in health['zones'].items()}
    health['zones']['head']['current'] = 0
    data['health'] = health
    stored.data = data
    db.session.commit()

    response = client.post(
        f"/lobbies/{lobby['id']}/characters/{mutant['id']}/heal",
        headers=auth_headers(gm),
    )
    assert response.status_code == 200
    healed = response.get_json()['data']['health']
    assert healed['current'] == healed['max'] == profile['health']
    assert healed['zones']['head']['current'] == healed['zones']['head']['max']
    assert healed['painLevel'] == 0
    assert healed['bloodStage'] == 'normal'


def test_end_turn_automatically_skips_dead_character(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("initiative-auto-skip-gm")
    lobby = create_lobby(client, gm, auth_headers)
    active_character = create_character(client, lobby, gm, auth_headers)
    dead_character = create_character(client, lobby, gm, auth_headers, data={
        "health": {"effects": [{"type": "death", "active": True}]},
    })
    next_character = create_character(client, lobby, gm, auth_headers)
    location = Location(
        lobby_id=lobby["id"], name="Initiative auto skip", world_tile_x=0, world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    active = LocationCharacter(location_id=location.id, character_id=active_character["id"])
    dead = LocationCharacter(location_id=location.id, character_id=dead_character["id"])
    next_active = LocationCharacter(location_id=location.id, character_id=next_character["id"])
    db.session.add_all([active, dead, next_active])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[active.id, dead.id, next_active.id],
        current_location_character_id=active.id,
    ))
    db.session.commit()

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(gm),
        json={"location_character_id": active.id},
    )

    assert response.status_code == 200
    state = response.get_json()
    assert state["current_location_character_id"] == next_active.id
    assert [item["location_character_id"] for item in state["auto_skipped"]] == [dead.id]
    assert state["auto_skipped"][0]["condition"]["state"] == "dead"


def test_end_turn_keeps_recoverable_pain_shock_in_initiative(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("initiative-shock-gm")
    lobby = create_lobby(client, gm, auth_headers)
    active_character = create_character(client, lobby, gm, auth_headers)
    shocked_character = create_character(client, lobby, gm, auth_headers, data={
        "health": {
            "painLevel": 5,
            "effects": [{"type": "shock", "active": True}],
        },
    })
    location = Location(
        lobby_id=lobby["id"], name="Initiative shock", world_tile_x=0, world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    active = LocationCharacter(location_id=location.id, character_id=active_character["id"])
    shocked = LocationCharacter(location_id=location.id, character_id=shocked_character["id"])
    db.session.add_all([active, shocked])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[active.id, shocked.id],
        current_location_character_id=active.id,
    ))
    db.session.commit()

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(gm),
        json={"location_character_id": active.id},
    )

    assert response.status_code == 200
    state = response.get_json()
    assert state["current_location_character_id"] == shocked.id
    assert "auto_skipped" not in state
    assert state["current_character"]["condition"]["state"] == "pain_shock"


def test_end_turn_skips_pain_shock_that_cannot_recover(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("initiative-blocked-shock-gm")
    lobby = create_lobby(client, gm, auth_headers)
    active_character = create_character(client, lobby, gm, auth_headers)
    shocked_character = create_character(client, lobby, gm, auth_headers, data={
        "health": {
            "painLevel": 10,
            "effects": [{"type": "shock", "active": True}],
        },
    })
    next_character = create_character(client, lobby, gm, auth_headers)
    location = Location(
        lobby_id=lobby["id"], name="Blocked pain shock", world_tile_x=0, world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    active = LocationCharacter(location_id=location.id, character_id=active_character["id"])
    shocked = LocationCharacter(location_id=location.id, character_id=shocked_character["id"])
    next_active = LocationCharacter(location_id=location.id, character_id=next_character["id"])
    db.session.add_all([active, shocked, next_active])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[active.id, shocked.id, next_active.id],
        current_location_character_id=active.id,
    ))
    db.session.commit()

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(gm),
        json={"location_character_id": active.id},
    )

    assert response.status_code == 200
    state = response.get_json()
    assert state["current_location_character_id"] == next_active.id
    assert [item["location_character_id"] for item in state["auto_skipped"]] == [shocked.id]
    assert state["auto_skipped"][0]["condition"]["state"] == "pain_shock"
    assert state["auto_skipped"][0]["condition"]["can_recover"] is False


def test_reload_can_be_paid_across_combat_turns(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("reload-pending-gm")
    actor = create_user("reload-pending-actor")
    other = create_user("reload-pending-other")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, actor, auth_headers)
    join_lobby(client, lobby, other, auth_headers)
    actor_character = create_character(client, lobby, actor, auth_headers, data={
        "weapons": [{"name": "Test rifle", "ergonomics": 60}],
        "health": {"effects": []},
    })
    other_character = create_character(client, lobby, other, auth_headers)
    location = Location(
        lobby_id=lobby["id"],
        name="Reload arena",
        world_tile_x=0,
        world_tile_z=0,
    )
    magazine = ItemTemplate(
        name="Test magazine",
        category="magazine",
        attributes={"reload_time_od": 6, "ergonomics": 0},
    )
    db.session.add_all([location, magazine])
    db.session.flush()
    reload_character = db.session.get(LobbyCharacter, actor_character['id'])
    reload_character.data = {**reload_character.data, 'inventory': {'backpack': [{
        'id': 'reload-magazine', 'templateId': magazine.id, 'name': magazine.name,
        'category': 'magazine', 'quantity': 1, 'ammo': [],
    }]}}
    actor_loc_char = LocationCharacter(
        location_id=location.id,
        character_id=actor_character["id"],
        controlled_by=actor["id"],
        action_points_current=2,
        action_points_max=5,
    )
    other_loc_char = LocationCharacter(
        location_id=location.id,
        character_id=other_character["id"],
        controlled_by=other["id"],
        action_points_current=5,
        action_points_max=5,
    )
    db.session.add_all([actor_loc_char, other_loc_char])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[actor_loc_char.id, other_loc_char.id],
        current_location_character_id=actor_loc_char.id,
    ))
    db.session.commit()

    started = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(actor),
        json={
            "location_character_id": actor_loc_char.id,
            "action_key": "reload_weapon",
            "weapon_index": 0,
            "magazine_template_id": magazine.id,
            "pending_action_id": "reload-test-1",
            "item_path": ["inventory", "backpack", 0],
        },
    )

    assert started.status_code == 200
    assert started.get_json()["pending_action"] is True
    db.session.refresh(actor_loc_char)
    assert actor_loc_char.action_points_current == 0
    pending = actor_loc_char.character.data["health"]["combatMeta"]["pendingAction"]
    # Reloading costs 6 AP plus 2 AP to retrieve the magazine from the backpack.
    assert pending["remaining_action_points"] == 6

    next_turn = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(other),
        json={},
    )
    assert next_turn.status_code == 200
    db.session.refresh(actor_loc_char)
    assert actor_loc_char.action_points_current == 0
    assert actor_loc_char.character.data["health"]["combatMeta"][
        "pendingAction"
    ]["remaining_action_points"] == 1

    other_ends = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/end_turn",
        headers=auth_headers(other),
        json={},
    )
    assert other_ends.status_code == 200
    db.session.refresh(actor_loc_char)
    assert actor_loc_char.action_points_current == 4
    assert actor_loc_char.character.data["health"]["combatMeta"][
        "completedPendingActionId"
    ] == "reload-test-1"

    completed = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(actor),
        json={
            "location_character_id": actor_loc_char.id,
            "action_key": "reload_weapon",
            "weapon_index": 0,
            "magazine_template_id": magazine.id,
            "resume_pending_action_id": "reload-test-1",
        },
    )

    assert completed.status_code == 200
    assert completed.get_json()["reload_weapon"]["action_points"] == 8
    db.session.refresh(actor_loc_char)
    assert actor_loc_char.action_points_current == 4
    assert "completedPendingActionId" not in actor_loc_char.character.data["health"]["combatMeta"]
    assert actor_loc_char.character.data['weapons'][0]['installedMagazine']['id'] == 'reload-magazine'
    assert actor_loc_char.character.data['inventory']['backpack'] == []


def test_narrative_combat_action_spends_ap_rolls_and_writes_chat(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("narrative-action-gm")
    actor = create_user("narrative-action-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, actor, auth_headers)
    character = create_character(client, lobby, actor, auth_headers, data={
        "skills": {"other": {"engineering": {"base": 12, "bonus": 0}}},
        "health": {"effects": [], "painLevel": 0, "exhaustion": 0},
    })
    location = Location(
        lobby_id=lobby["id"],
        name="Narrative arena",
        world_tile_x=0,
        world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    actor_loc_char = LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
        controlled_by=actor["id"],
        action_points_current=5,
        action_points_max=5,
    )
    db.session.add(actor_loc_char)
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[actor_loc_char.id],
        current_location_character_id=actor_loc_char.id,
    ))
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 15)

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(actor),
        json={
            "location_character_id": actor_loc_char.id,
            "action_key": "narrative_action",
            "action_points": 2,
            "narrative_action_name": "Перенаправляю питание терминала",
            "narrative_roll_required": True,
            "narrative_skill_path": "skills.other.engineering",
            "narrative_difficulty": 20,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()["narrative_action"]
    assert payload["name"] == "Перенаправляю питание терминала"
    assert payload["check"]["roll"] == 15
    assert payload["check"]["total"] == 16
    assert payload["check"]["success"] is False
    db.session.refresh(actor_loc_char)
    assert actor_loc_char.action_points_current == 3
    assert actor_loc_char.character.data["health"]["combatMeta"]["mustDoRetry"]["difficulty"] == 20
    message = ChatMessage.query.filter_by(
        lobby_id=lobby["id"], username="Действие"
    ).one()
    assert "Затрачено ОД: 2" in message.message
    assert "Инженерия" in message.message

    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)
    retry = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(actor),
        json={
            "location_character_id": actor_loc_char.id,
            "action_key": "must_do_it",
        },
    )
    assert retry.status_code == 200
    retry_payload = retry.get_json()["must_do_it"]
    assert retry_payload["check"]["success"] is True
    db.session.refresh(actor_loc_char.character)
    assert actor_loc_char.character.data["health"]["stress"] == 1
    assert "mustDoRetry" not in actor_loc_char.character.data["health"]["combatMeta"]

    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 15)
    failed_again = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(actor),
        json={
            "location_character_id": actor_loc_char.id,
            "action_key": "narrative_action",
            "action_points": 0,
            "narrative_action_name": "Повторная важная проверка",
            "narrative_roll_required": True,
            "narrative_skill_path": "skills.other.engineering",
            "narrative_difficulty": 20,
        },
    )
    assert failed_again.status_code == 200

    exhausted_retry = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(actor),
        json={
            "location_character_id": actor_loc_char.id,
            "action_key": "must_do_it",
        },
    )
    assert exhausted_retry.status_code == 400
    assert "ten-minute interval" in exhausted_retry.get_json()["error"]["message"]


def test_must_do_it_allows_gm_approved_manual_check_without_stored_failure(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("manual-must-do-gm")
    actor = create_user("manual-must-do-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, actor, auth_headers)
    character = create_character(client, lobby, actor, auth_headers, data={
        "skills": {
            "physical": {"will": {"base": 12, "bonus": 0}},
            "other": {"engineering": {"base": 12, "bonus": 0}},
        },
        "health": {"effects": [], "stress": 0},
    })
    location = Location(
        lobby_id=lobby["id"], name="Manual must do", world_tile_x=0, world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    actor_loc_char = LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
        controlled_by=actor["id"],
        action_points_current=5,
        action_points_max=5,
    )
    db.session.add(actor_loc_char)
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[actor_loc_char.id],
        current_location_character_id=actor_loc_char.id,
    ))
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(actor),
        json={
            "location_character_id": actor_loc_char.id,
            "action_key": "must_do_it",
            "narrative_action_name": "Запускаю аварийный генератор",
            "narrative_skill_path": "skills.other.engineering",
            "narrative_difficulty": 18,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["must_do_it"]["kind"] == "manual"
    assert payload["must_do_it"]["check"]["success"] is True
    assert payload["must_do_it"]["uses_remaining"] == 0
    assert payload["state"]["current_character"]["must_do_retry"] is None
    assert payload["state"]["current_character"]["must_do_usage"] == {
        "will_bonus": 1,
        "limit": 1,
        "used": 1,
        "remaining": 0,
    }
    db.session.refresh(actor_loc_char.character)
    assert actor_loc_char.character.data["health"]["stress"] == 1


def test_mutant_cannot_use_must_do_it(
    client, create_user, auth_headers,
):
    gm = create_user("mutant-must-do-gm")
    lobby = create_lobby(client, gm, auth_headers)
    character = create_character(client, lobby, gm, auth_headers, data={
        "is_mutant": True,
        "basic": {"species": "Мутант", "is_mutant": True},
        "health": {"effects": [], "stress": 0},
    })
    location = Location(
        lobby_id=lobby["id"], name="Mutant must do", world_tile_x=0, world_tile_z=0,
    )
    db.session.add(location)
    db.session.flush()
    actor = LocationCharacter(
        location_id=location.id,
        character_id=character["id"],
        controlled_by=gm["id"],
        action_points_current=5,
        action_points_max=5,
    )
    db.session.add(actor)
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[actor.id],
        current_location_character_id=actor.id,
    ))
    db.session.commit()

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(gm),
        json={
            "location_character_id": actor.id,
            "action_key": "must_do_it",
            "narrative_action_name": "Невозможный повтор",
            "narrative_skill_path": "skills.physical.will",
            "narrative_difficulty": 10,
        },
    )

    assert response.status_code == 400
    assert "Мутанты не могут" in response.get_json()["error"]["message"]

    state = client.get(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat",
        headers=auth_headers(gm),
    ).get_json()
    serialized = next(item for item in state["characters"] if item["character_id"] == character["id"])
    assert serialized["is_mutant"] is True
    assert serialized["must_do_usage"] is None
    assert serialized["must_do_retry"] is None


def test_consolation_costs_three_ap_reduces_stress_and_is_limited_per_game_hour(
    client, create_user, auth_headers, monkeypatch
):
    gm = create_user("consolation-gm")
    player = create_user("consolation-player")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    helper = create_character(client, lobby, player, auth_headers, data={
        "skills": {"social": {"charisma": {"base": 5, "bonus": 0}}},
        "health": {"effects": []},
    })
    target = create_character(client, lobby, player, auth_headers, data={
        "skills": {"physical": {"will": {"base": 20, "bonus": 0}}},
        "health": {"stress": 3, "effects": []},
    })
    location = Location(lobby_id=lobby["id"], name="Consolation", world_tile_x=0, world_tile_z=0)
    db.session.add(location)
    db.session.flush()
    helper_loc = LocationCharacter(
        location_id=location.id, character_id=helper["id"], controlled_by=player["id"],
        pos_x=1, pos_y=1, action_points_current=5, action_points_max=5,
    )
    target_loc = LocationCharacter(
        location_id=location.id, character_id=target["id"], controlled_by=player["id"],
        pos_x=2, pos_y=1,
    )
    db.session.add_all([helper_loc, target_loc])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id, status="active", round_number=1, turn_index=0,
        turn_order=[helper_loc.id, target_loc.id], current_location_character_id=helper_loc.id,
    ))
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)
    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action"
    payload = {
        "location_character_id": helper_loc.id,
        "action_key": "console_ally",
        "target_character_id": target["id"],
    }

    response = client.post(url, headers=auth_headers(player), json=payload)

    assert response.status_code == 200
    assert response.get_json()["consolation"]["check"]["success"] is True
    db.session.refresh(helper_loc)
    db.session.refresh(target_loc.character)
    assert helper_loc.action_points_current == 2
    assert target_loc.character.data["health"]["stress"] == 2

    helper_loc.action_points_current = 5
    db.session.commit()
    repeated = client.post(url, headers=auth_headers(player), json=payload)
    assert repeated.status_code == 400
    assert "last hour" in repeated.get_json()["error"]["message"]


def test_player_may_move_and_add_marked_player_item(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("inventory-gm")
    owner = create_user("inventory-owner")
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, owner, auth_headers)
    existing_item = {
        "id": "item-existing",
        "templateId": 10,
        "name": "Bandage",
        "category": "consumable",
        "quantity": 1,
    }
    character = create_character(
        client,
        lobby,
        owner,
        auth_headers,
        data={"inventory": {"pockets": [existing_item], "backpack": []}},
    )
    endpoint = f"/lobbies/characters/{character['id']}"
    moved_data = {
        '_revision': character['data']['_revision'],
        "inventory": {"pockets": [], "backpack": [existing_item]},
    }

    moved = client.put(
        endpoint,
        headers=auth_headers(owner),
        json={"data": moved_data},
    )
    player_add = client.put(
        endpoint,
        headers=auth_headers(owner),
        json={
            "data": {
                '_revision': moved.get_json()['data']['_revision'],
                "inventory": {
                    "pockets": [],
                    "backpack": [
                        existing_item,
                        {
                            "id": "item-added",
                            "templateId": 11,
                            "name": "Medkit",
                            "category": "consumable",
                            "quantity": 1,
                        },
                    ],
                },
            },
        },
    )
    db.session.expire_all()
    saved_character = db.session.get(LobbyCharacter, character["id"])
    player_item = next(
        item
        for item in saved_character.data["inventory"]["backpack"]
        if item["id"] == "item-added"
    )
    gm_add = client.put(
        endpoint,
        headers=auth_headers(gm),
        json={
            "data": {
                '_revision': player_add.get_json()['data']['_revision'],
                "inventory": {
                    "pockets": [],
                    "backpack": [
                        existing_item,
                        {
                            "id": "item-added",
                            "templateId": 11,
                            "name": "Medkit",
                            "category": "consumable",
                            "quantity": 1,
                        },
                    ],
                },
            },
        },
    )

    assert moved.status_code == 200
    assert player_add.status_code == 200
    assert player_item["createdByPlayer"] is True
    assert gm_add.status_code == 200


def test_weapon_fire_rate_rejects_excess_shots_without_spending_ammo_or_ap(
    client,
    create_user,
    auth_headers,
):
    gm = create_user("fire-rate-gm")
    lobby = create_lobby(client, gm, auth_headers)
    actor = create_character(client, lobby, gm, auth_headers, data={
        "weapons": [{
            "name": "Test pistol",
            "subcategory": "Пистолеты",
            "fireRate": 2,
            "fireModes": {"single_shot_options": [1], "supports_burst": False},
            "accuracy": 0,
            "range": 20,
            "durability": 100,
            "installedMagazine": {
                "ammo": [{
                    "name": "9x19",
                    "category": "ammo",
                    "quantity": 5,
                    "attributes": {"damage": 0, "armor_piercing": 0},
                }],
            },
        }],
        "health": {"effects": []},
    })
    target = create_character(client, lobby, gm, auth_headers, data={
        "health": {"effects": []},
    })
    location = Location(
        lobby_id=lobby["id"],
        name="Fire rate arena",
        world_tile_x=0,
        world_tile_z=0,
        grid_width=10,
        grid_height=10,
    )
    db.session.add(location)
    db.session.flush()
    actor_location = LocationCharacter(
        location_id=location.id,
        character_id=actor["id"],
        pos_x=1,
        pos_y=1,
        facing_x=0,
        facing_y=1,
        drawn_weapon_index=0,
        action_points_current=5,
        action_points_max=5,
    )
    target_location = LocationCharacter(
        location_id=location.id,
        character_id=target["id"],
        pos_x=1,
        pos_y=3,
    )
    db.session.add_all([actor_location, target_location])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id,
        status="active",
        round_number=1,
        turn_index=0,
        turn_order=[actor_location.id, target_location.id],
        current_location_character_id=actor_location.id,
    ))
    db.session.commit()

    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action"
    payload = {
        "location_character_id": actor_location.id,
        "action_key": "attack",
        "weapon_index": 0,
        "fire_mode": "unaimed",
        "shot_count": 1,
        "volley_count": 1,
        "action_points": 1,
        "target_character_id": target["id"],
    }

    assert client.post(url, headers=auth_headers(gm), json=payload).status_code == 200
    assert client.post(url, headers=auth_headers(gm), json=payload).status_code == 200
    rejected = client.post(url, headers=auth_headers(gm), json=payload)

    assert rejected.status_code == 400
    assert "скорострельность" in rejected.get_json()["error"]["message"]
    db.session.refresh(actor_location)
    stored_data = actor_location.character.data
    assert actor_location.action_points_current == 3
    assert stored_data["weapons"][0]["ammo"] == 3
    assert stored_data["health"]["combatMeta"]["weaponShots"] == {
        "round": 1,
        "shots": {"0": 2},
    }


def test_leaving_melee_reach_offers_one_opportunity_attack_per_round(
    client, create_user, auth_headers, monkeypatch,
):
    gm = create_user("opportunity-attack-gm")
    lobby = create_lobby(client, gm, auth_headers)
    mover = create_character(client, lobby, gm, auth_headers, data={
        "health": {"effects": []},
    })
    defender = create_character(client, lobby, gm, auth_headers, data={
        "weapons": [{
            "name": "Test sword",
            "category": "melee_weapon",
            "attributes": {
                "damage": 20,
                "armor_piercing": 0,
                "allowed_attacks": ["slashing"],
                "weight_class": "light",
            },
        }],
        "health": {"effects": []},
    })
    location = Location(
        lobby_id=lobby["id"], name="Opportunity arena",
        world_tile_x=0, world_tile_z=0, grid_width=10, grid_height=10,
    )
    db.session.add(location)
    db.session.flush()
    mover_location = LocationCharacter(
        location_id=location.id, character_id=mover["id"],
        pos_x=2, pos_y=1, movement_points_current=10,
        movement_points_max=10, team_name="Blue",
    )
    defender_location = LocationCharacter(
        location_id=location.id, character_id=defender["id"],
        pos_x=1, pos_y=1, facing_x=1, facing_y=0,
        drawn_weapon_index=0, team_name="Red",
    )
    db.session.add_all([mover_location, defender_location])
    db.session.flush()
    state = LocationCombatState(
        location_id=location.id, status="active", round_number=1,
        turn_index=0, turn_order=[mover_location.id, defender_location.id],
        current_location_character_id=mover_location.id,
    )
    db.session.add(state)
    db.session.commit()

    CombatService.move_character(
        location.id, gm["id"], mover["id"], 4, 1, movement_mode="walk",
    )
    db.session.refresh(defender_location.character)
    pending = defender_location.character.data["health"]["combatMeta"]["opportunityAttacks"]
    assert len(pending) == 1
    assert pending[0]["target_character_id"] == mover["id"]

    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)
    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/opportunity-attack",
        headers=auth_headers(gm),
        json={
            "location_character_id": defender_location.id,
            "opportunity_id": pending[0]["id"],
            "accept": True,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["attack"]["results"][0]["opportunity_attack"] is True
    db.session.refresh(defender_location.character)
    meta = defender_location.character.data["health"]["combatMeta"]
    assert meta["opportunityAttackUsedRound"] == 1
    assert meta["opportunityAttacks"] == []


def test_area_fire_uses_two_bursts_and_applies_weapon_class_penalty(
    client, create_user, auth_headers, monkeypatch,
):
    gm = create_user("area-fire-rules-gm")
    lobby = create_lobby(client, gm, auth_headers)
    actor = create_character(client, lobby, gm, auth_headers, data={
        "weapons": [{
            "name": "Test rifle",
            "subcategory": "Штурмовые винтовки и карабины",
            "fireRate": 20,
            "fireModes": {
                "burst_size": 4,
                "supports_burst": True,
                "supports_area_fire": True,
            },
            "accuracy": 0,
            "range": 20,
            "durability": 100,
            "installedMagazine": {
                "ammo": [{
                    "name": "5.56x45",
                    "category": "ammo",
                    "quantity": 20,
                    "attributes": {
                        "caliber": "5.56x45", "damage": 0,
                        "armor_piercing": 0,
                    },
                }],
            },
        }],
        "health": {"effects": []},
    })
    target = create_character(client, lobby, gm, auth_headers, data={
        "health": {"effects": []},
    })
    unselected_target = create_character(client, lobby, gm, auth_headers, data={
        "health": {"effects": []},
    })
    location = Location(
        lobby_id=lobby["id"], name="Area fire arena",
        world_tile_x=0, world_tile_z=0, grid_width=10, grid_height=10,
    )
    db.session.add(location)
    db.session.flush()
    actor_location = LocationCharacter(
        location_id=location.id, character_id=actor["id"],
        pos_x=1, pos_y=1, facing_x=0, facing_y=1,
        drawn_weapon_index=0, action_points_current=5, action_points_max=5,
        team_name="Blue",
    )
    target_location = LocationCharacter(
        location_id=location.id, character_id=target["id"], pos_x=1, pos_y=3,
        team_name="Red",
    )
    unselected_location = LocationCharacter(
        location_id=location.id, character_id=unselected_target["id"],
        pos_x=2, pos_y=3, team_name="Red",
    )
    db.session.add_all([actor_location, target_location, unselected_location])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id, status="active", round_number=1,
        turn_index=0,
        turn_order=[actor_location.id, target_location.id, unselected_location.id],
        current_location_character_id=actor_location.id,
    ))
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 10)

    response = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(gm),
        json={
            "location_character_id": actor_location.id,
            "action_key": "attack",
            "weapon_index": 0,
            "fire_mode": "area",
            "shot_count": 8,
            "volley_count": 2,
            "action_points": 5,
            "target_character_ids": [target["id"]],
            "area_center_x": 1,
            "area_center_y": 3,
        },
    )

    assert response.status_code == 200
    attack = response.get_json()["attack"]
    assert attack["shot_count"] == 8
    assert attack["volley_count"] == 2
    assert attack["area_fire_accuracy_penalty"] == 4
    assert attack["hit_difficulty"] == 16
    assert {
        entry["character_id"] for entry in attack["area_stressed_characters"]
    } == {target["id"], unselected_target["id"]}
    assert response.get_json()["character"]["must_do_retry"]["kind"] == "attack"
    db.session.refresh(actor_location.character)
    assert actor_location.character.data["weapons"][0]["ammo"] == 12

    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)
    retry = client.post(
        f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action",
        headers=auth_headers(gm),
        json={
            "location_character_id": actor_location.id,
            "action_key": "must_do_it",
        },
    )

    assert retry.status_code == 200
    retry_payload = retry.get_json()
    assert retry_payload["must_do_it"]["check"]["success"] is True
    assert retry_payload["must_do_it"]["check"]["total"] == 20
    assert retry_payload["attack"]["hits"] >= 1
    db.session.refresh(actor_location.character)
    assert actor_location.character.data["weapons"][0]["ammo"] == 12
    assert actor_location.character.data["health"]["stress"] == 1


def test_new_burst_replaces_stale_must_do_target(
    client, create_user, auth_headers, monkeypatch,
):
    gm = create_user("must-do-burst-target-gm")
    lobby = create_lobby(client, gm, auth_headers)
    actor = create_character(client, lobby, gm, auth_headers, data={
        "weapons": [{
            "name": "Test rifle",
            "subcategory": "Штурмовые винтовки и карабины",
            "fireRate": 20,
            "fireModes": {
                "single_shot_options": [1],
                "burst_size": 2,
                "supports_burst": True,
            },
            "accuracy": 0,
            "range": 20,
            "durability": 100,
            "installedMagazine": {
                "ammo": [{
                    "name": "5.45x39",
                    "category": "ammo",
                    "quantity": 20,
                    "attributes": {
                        "caliber": "5.45x39", "damage": 0,
                        "armor_piercing": 0,
                    },
                }],
            },
        }],
        "health": {"effects": [], "stress": 0},
    })
    player_target = create_character(client, lobby, gm, auth_headers, data={
        "health": {"effects": []},
    })
    mutant_target = create_character(client, lobby, gm, auth_headers, data={
        "is_mutant": True,
        "basic": {"species": "Мутант", "is_mutant": True},
        "health": {"effects": []},
    })
    location = Location(
        lobby_id=lobby["id"], name="Must do burst targets",
        world_tile_x=0, world_tile_z=0, grid_width=10, grid_height=10,
    )
    db.session.add(location)
    db.session.flush()
    actor_location = LocationCharacter(
        location_id=location.id, character_id=actor["id"],
        pos_x=1, pos_y=1, facing_x=0, facing_y=1,
        drawn_weapon_index=0, action_points_current=10, action_points_max=10,
    )
    player_location = LocationCharacter(
        location_id=location.id, character_id=player_target["id"], pos_x=1, pos_y=3,
    )
    mutant_location = LocationCharacter(
        location_id=location.id, character_id=mutant_target["id"], pos_x=2, pos_y=3,
    )
    db.session.add_all([actor_location, player_location, mutant_location])
    db.session.flush()
    db.session.add(LocationCombatState(
        location_id=location.id, status="active", round_number=1,
        turn_index=0,
        turn_order=[actor_location.id, player_location.id, mutant_location.id],
        current_location_character_id=actor_location.id,
    ))
    db.session.commit()
    url = f"/lobbies/{lobby['id']}/locations/{location.id}/combat/action"

    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 1)
    first = client.post(url, headers=auth_headers(gm), json={
        "location_character_id": actor_location.id,
        "action_key": "attack",
        "weapon_index": 0,
        "fire_mode": "unaimed",
        "shot_count": 1,
        "volley_count": 1,
        "action_points": 2,
        "target_character_id": player_target["id"],
    })
    assert first.status_code == 200
    assert first.get_json()["character"]["must_do_retry"]["name"].endswith(player_target["name"])

    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)
    successful = client.post(url, headers=auth_headers(gm), json={
        "location_character_id": actor_location.id,
        "action_key": "attack",
        "weapon_index": 0,
        "fire_mode": "unaimed",
        "shot_count": 1,
        "volley_count": 1,
        "action_points": 2,
        "target_character_id": mutant_target["id"],
    })
    assert successful.status_code == 200
    assert successful.get_json()["character"]["must_do_retry"] is None

    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 1)
    stale = client.post(url, headers=auth_headers(gm), json={
        "location_character_id": actor_location.id,
        "action_key": "attack",
        "weapon_index": 0,
        "fire_mode": "unaimed",
        "shot_count": 1,
        "volley_count": 1,
        "action_points": 2,
        "target_character_id": player_target["id"],
    })
    assert stale.status_code == 200
    assert stale.get_json()["character"]["must_do_retry"]["name"].endswith(player_target["name"])

    burst = client.post(url, headers=auth_headers(gm), json={
        "location_character_id": actor_location.id,
        "action_key": "attack",
        "weapon_index": 0,
        "fire_mode": "burst",
        "shot_count": 2,
        "volley_count": 1,
        "action_points": 3,
        "target_character_id": mutant_target["id"],
    })
    assert burst.status_code == 200
    retry = burst.get_json()["character"]["must_do_retry"]
    assert retry["name"].endswith(mutant_target["name"])

    db.session.refresh(actor_location.character)
    stored_retry = actor_location.character.data["health"]["combatMeta"]["mustDoRetry"]
    assert stored_retry["attack_details"]["target_character_id"] == mutant_target["id"]

    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)
    repeated = client.post(url, headers=auth_headers(gm), json={
        "location_character_id": actor_location.id,
        "action_key": "must_do_it",
    })
    assert repeated.status_code == 200
    assert {
        result["target_character_id"]
        for result in repeated.get_json()["attack"]["results"]
    } == {mutant_target["id"]}


def test_gm_connects_adjacent_world_roads_across_chunks(client, create_user, auth_headers):
    gm = create_user()
    player = create_user()
    lobby = create_lobby(client, gm, auth_headers)
    join_lobby(client, lobby, player, auth_headers)
    lobby_id = lobby["id"]
    road = {"type": "road", "connections": ["N", "S"], "color": "#756f61"}
    MapService.update_tile(lobby_id, gm["id"], 0, 0, 31, 4, {"objects": [road]})
    MapService.update_tile(lobby_id, gm["id"], 1, 0, 0, 4, {"objects": [road]})
    url = f"/lobbies/{lobby_id}/world-roads/connect"

    forbidden = client.post(url, headers=auth_headers(player), json={"first": [31, 4], "second": [32, 4]})
    assert forbidden.status_code == 403
    assert client.post(url, headers=auth_headers(gm), json=[31, 4, 32, 4]).status_code == 400
    for first, second in ((None, [32, 4]), ([31, 4], [33, 4])):
        response = client.post(url, headers=auth_headers(gm), json={"first": first, "second": second})
        assert response.status_code == 400
    assert MapService.get_tile_data(lobby_id, 31, 4)["objects"][0].get("manual_connections") is None

    response = client.post(url, headers=auth_headers(gm), json={"first": [31, 4], "second": [32, 4]})
    assert response.status_code == 200
    assert len(response.get_json()["updates"]) == 2
    db.session.expire_all()
    first_road = MapService.get_tile_data(lobby_id, 31, 4)["objects"][0]
    second_road = MapService.get_tile_data(lobby_id, 32, 4)["objects"][0]
    assert first_road["manual_connections"] == ["E"]
    assert second_road["manual_connections"] == ["W"]
    assert first_road["connections"] == ["N", "S"]
    assert MapChunk.query.filter_by(lobby_id=lobby_id).count() == 2

    duplicate = client.post(url, headers=auth_headers(gm), json={"first": [31, 4], "second": [32, 4]})
    assert duplicate.status_code == 200
    assert MapService.get_tile_data(lobby_id, 31, 4)["objects"][0]["manual_connections"] == ["E"]


def test_world_road_connector_requires_roads_on_both_tiles(client, create_user, auth_headers):
    gm = create_user()
    lobby = create_lobby(client, gm, auth_headers)
    lobby_id = lobby["id"]
    MapService.update_tile(lobby_id, gm["id"], 0, 0, 3, 3, {"objects": [{"type": "road"}]})
    response = client.post(f"/lobbies/{lobby_id}/world-roads/connect", headers=auth_headers(gm),
                           json={"first": [3, 3], "second": [4, 3]})
    assert response.status_code == 400
    assert MapService.get_tile_data(lobby_id, 3, 3)["objects"][0].get("manual_connections") is None

    MapService.update_tile(lobby_id, gm["id"], 0, 0, 4, 4, {"objects": [{"type": "road"}]})
    diagonal = client.post(f"/lobbies/{lobby_id}/world-roads/connect", headers=auth_headers(gm),
                           json={"first": [3, 3], "second": [4, 4]})
    assert diagonal.status_code == 200
    assert MapService.get_tile_data(lobby_id, 3, 3)["objects"][0]["manual_connections"] == ["SE"]
    assert MapService.get_tile_data(lobby_id, 4, 4)["objects"][0]["manual_connections"] == ["NW"]


@pytest.mark.parametrize('conflict', [False, True])
def test_world_object_group_edit_is_atomic_and_tracks_shifted_indices(client, create_user, auth_headers, conflict):
    gm = create_user('group-object-gm')
    lobby = create_lobby(client, gm, auth_headers)
    MapService.get_chunks(lobby['id'], gm['id'], (0, 1, 0, 0))
    objects = [{'type': 'house', 'name': name, 'scale': 1, 'rotation': 0, 'custom': {'name': name}} for name in ['A', 'B', 'C']]
    MapService.update_tile(lobby['id'], gm['id'], 0, 0, 31, 10, {'objects': objects})
    edits = [{'source': {'tile_x': 31, 'tile_y': 10, 'index': index},
              'target': {'tile_x': 32, 'tile_y': 10}, 'expected': obj,
              'changes': {'rotation': 359.5, 'scale': 1.2}} for index, obj in enumerate(objects)]
    if conflict:
        edits[-1]['changes']['scale'] = 99
    response = client.post(f"/lobbies/{lobby['id']}/world-objects/edit", headers=auth_headers(gm), json={'edits': edits})
    assert response.status_code == (400 if conflict else 200), response.json
    db.session.expire_all()
    if conflict:
        assert MapService.get_tile_data(lobby['id'], 31, 10)['objects'] == objects
        assert not MapService.get_tile_data(lobby['id'], 32, 10).get('objects')
    else:
        assert not MapService.get_tile_data(lobby['id'], 31, 10)['objects']
        moved = MapService.get_tile_data(lobby['id'], 32, 10)['objects']
        assert [obj['name'] for obj in moved] == ['A', 'B', 'C']
        assert [entry['index'] for entry in response.json['objects']] == [0, 1, 2]
        assert len(response.json['updates']) == 2
        assert all(obj['scale'] == 1.2 and obj['rotation'] == 359.5 and obj['custom']['name'] == obj['name'] for obj in moved)


def test_world_object_group_swap_rejects_stale_snapshots_and_duplicate_members(client, create_user, auth_headers):
    gm = create_user('group-swap-gm')
    lobby = create_lobby(client, gm, auth_headers)
    MapService.get_chunks(lobby['id'], gm['id'], (0, 0, 0, 0))
    a, b = {'type': 'house', 'name': 'A'}, {'type': 'tree', 'name': 'B'}
    for x, obj in [(10,a),(11,b)]:
        MapService.update_tile(lobby['id'], gm['id'], 0, 0, x, 10, {'objects': [obj]})
    edits = [{'source': {'tile_x': x, 'tile_y': 10, 'index': 0}, 'target': {'tile_x': y, 'tile_y': 10}, 'expected': obj, 'changes': {}}
             for x,y,obj in [(10,11,a),(11,10,b)]]
    endpoint = f"/lobbies/{lobby['id']}/world-objects/edit"
    assert client.post(endpoint, headers=auth_headers(gm), json={'edits': [edits[0], edits[0]]}).status_code == 400
    stale = [{**edits[0]}, {**edits[1], 'expected': {'type': 'tree'}}]
    assert client.post(endpoint, headers=auth_headers(gm), json={'edits': stale}).status_code == 409
    assert MapService.get_tile_data(lobby['id'], 10, 10)['objects'] == [a]
    response = client.post(endpoint, headers=auth_headers(gm), json={'edits': edits})
    assert response.status_code == 200, response.json
    assert all(entry['index'] == 0 for entry in response.json['objects'])
    assert MapService.get_tile_data(lobby['id'], 10, 10)['objects'] == [b]
    assert MapService.get_tile_data(lobby['id'], 11, 10)['objects'] == [a]
