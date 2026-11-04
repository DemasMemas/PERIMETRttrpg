from copy import deepcopy

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import db, socketio
from app.models import Lobby, LobbyCharacter, LobbyParticipant, Location, LocationCharacter, LocationCombatState, LocationObject, User
from app.services.anomaly_area import anomaly_covers_tile
from app.services.anomaly_profiles import ANOMALY_PROFILES, anomaly_profile
from app.services.combat import CombatService


def _health():
    return {"current": 700, "max": 700, "effects": [], "combatMeta": {}}


def _combat_with_anomaly(key="otboynik"):
    user = User(username="anomaly", email="anomaly@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()
    lobby = Lobby(name="Anomalies", gm_id=user.id, invite_code="ANOMALY")
    db.session.add(lobby)
    db.session.flush()
    tiles = [[{"terrain": "grass", "height": 1, "objects": []} for _ in range(6)] for _ in range(6)]
    tiles[2][2]["objects"] = [{"type": "anomaly", "anomalyKey": key}]
    location = Location(
        lobby_id=lobby.id, name="Field", grid_width=6, grid_height=6,
        world_tile_x=0, world_tile_z=0, tiles_data=tiles,
    )
    db.session.add(location)
    db.session.flush()
    actor = LobbyCharacter(
        lobby_id=lobby.id, owner_id=user.id, name="Walker",
        data={
            "skills": {"physical": {
                "agility": {"base": 10, "bonus": 0},
                "will": {"base": 10, "bonus": 0},
            }},
            "health": _health(),
        },
    )
    other = LobbyCharacter(lobby_id=lobby.id, owner_id=user.id, name="Other", data={"health": _health()})
    db.session.add_all([actor, other])
    db.session.flush()
    placed = LocationCharacter(
        location_id=location.id, character_id=actor.id, controlled_by=user.id,
        pos_x=1, pos_y=2, action_points_current=5, action_points_max=5,
        movement_points_current=20, movement_points_max=20,
    )
    placed_other = LocationCharacter(
        location_id=location.id, character_id=other.id, controlled_by=user.id,
        pos_x=5, pos_y=5, action_points_current=5, action_points_max=5,
    )
    db.session.add_all([placed, placed_other])
    db.session.flush()
    state = LocationCombatState(
        location_id=location.id, status="active", round_number=1, turn_index=0,
        turn_order=[placed.id, placed_other.id], current_location_character_id=placed.id,
    )
    db.session.add(state)
    db.session.commit()
    return user, location, actor, placed, placed_other


def test_anomaly_catalog_has_all_rulebook_entries():
    assert len(ANOMALY_PROFILES) == 48
    assert anomaly_profile("batut")["name"] == "Батут"
    assert anomaly_profile("mozgotrobilka")["category"] == "psi"


@pytest.mark.parametrize('intensity,expected',[(.5,{'electric','gravity'}),(.6,{'electric'})])
def test_weather_reveals_gm_hidden_outdoor_anomalies_through_api(app,client,intensity,expected):
    from sqlalchemy.orm.attributes import flag_modified
    user,location,actor,placed,_=_combat_with_anomaly('batut')
    db.session.add(LobbyParticipant(lobby_id=location.lobby_id,user_id=user.id))
    location.tiles_data[2][2]['objects'][0]['gm_hidden']=True
    flag_modified(location,'tiles_data')
    rows=[]
    for key,x,y in [('vspishka',1,1),('batut',4,4),('banya',3,3),('vspishka',0,0)]:
        obj=LocationObject(location_id=location.id,type='anomaly',tile_x=x,tile_y=y,
            properties={'anomaly_key':key,'gm_hidden':True})
        db.session.add(obj);rows.append(obj)
    db.session.add(LocationObject(location_id=location.id,type='floor',tile_x=0,tile_y=0,
        properties={'building_id':'weather-room','dimensions':{'width':1,'depth':1},'level':0}))
    db.session.commit()
    token=create_access_token(identity=str(user.id))
    response=client.patch(f'/lobbies/{location.lobby_id}/weather',headers={'Authorization':f'Bearer {token}'},
        json={'rain':{'enabled':True,'intensity':intensity}})
    assert response.status_code==200,response.json
    for obj in rows[:3]:
        family=anomaly_profile(obj.properties['anomaly_key'])['category']
        assert obj.properties['gm_hidden']==(family not in expected)
    assert rows[3].properties['gm_hidden'] is True  # Электрическая в помещении.
    assert location.tiles_data[2][2]['objects'][0]['gm_hidden']==('gravity' not in expected)


@pytest.mark.parametrize('intensity,accuracy,visual,storm,radiation',[
    (.5,-4,-3,False,1.5),(.6,-4,-4,True,2),
])
def test_weather_thresholds_and_indoor_exemption(app,intensity,accuracy,visual,storm,radiation):
    from app.services.weather_rules import at_character
    _,location,_,placed,_=_combat_with_anomaly()
    location.lobby.weather_settings={
        'sun':{'enabled':True,'intensity':intensity},
        'rain':{'enabled':True,'intensity':intensity},
        'fog':{'enabled':True,'intensity':intensity},
    }
    weather=at_character(location,placed)
    assert weather['accuracy']==accuracy
    assert weather['visual_awareness']==visual
    assert weather['accuracy_disadvantage']==storm
    assert weather['radiation_multiplier']==radiation
    data=deepcopy(placed.character.data)
    check=CombatService._narrative_skill_check(deepcopy(data),'skills.physical.awareness.visual',weather=weather)
    assert check['weather_modifier']==visual
    assert check['weather_disadvantage']==storm
    db.session.add(LocationObject(location_id=location.id,type='floor',tile_x=placed.pos_x,tile_y=placed.pos_y,
        properties={'building_id':'weather-room','dimensions':{'width':2,'depth':2}}))
    db.session.flush()
    sheltered=at_character(location,placed)
    assert sheltered['accuracy']==0 and sheltered['visual_awareness']==0
    assert sheltered['radiation_multiplier']==1 and not sheltered['accuracy_disadvantage']


def test_rain_only_amplifies_external_radiation(app):
    from app.services.weather_rules import profile
    _,location,_,placed,_=_combat_with_anomaly()
    location.lobby.weather_settings={'rain':{'enabled':True,'intensity':.5}}
    data={'health':{'radiation':0},'equipment':{}}
    result=CombatService._apply_incoming_radiation(data,10,target=placed)
    assert result['received']==15 and result['weather_multiplier']==1.5
    # Внутреннее облучение артефактами проходит по прежнему отдельному пути.
    data={'health':{'radiation':0},'equipment':{}}
    assert CombatService._apply_incoming_radiation(data,10)['received']==10
    assert profile({'sun':{'enabled':False,'intensity':0}})['visual_awareness']==0


@pytest.mark.parametrize('indoors',[False,True])
def test_character_sheet_skill_check_applies_weather_and_explains_it(app,client,indoors):
    user,location,actor,placed,_=_combat_with_anomaly()
    db.session.add(LobbyParticipant(lobby_id=location.lobby_id,user_id=user.id))
    location.lobby.weather_settings={'sun':{'enabled':True,'intensity':.5},
        'rain':{'enabled':True,'intensity':.6},'fog':{'enabled':True,'intensity':.6}}
    if indoors:
        db.session.add(LocationObject(location_id=location.id,type='floor',tile_x=placed.pos_x,tile_y=placed.pos_y,
            properties={'building_id':'sheet-weather-room','dimensions':{'width':2,'depth':2}}))
    db.session.commit()
    token=create_access_token(identity=str(user.id))
    response=client.post(f'/lobbies/characters/{actor.id}/skill-check',headers={'Authorization':f'Bearer {token}'},
        json={'skill_path':'skills.physical.awareness.visual','difficulty':12,'operation_id':'sheet-weather'})
    assert response.status_code==200,response.json
    check=response.json['check']
    assert check['weather_modifier']==(0 if indoors else -5)
    assert check['weather_disadvantage']==(not indoors)
    assert ('Ливень: Помеха' in check['rules'])==(not indoors)


def test_anomaly_is_passable_and_stops_route_on_first_anomaly(app):
    user, location, actor, placed, _ = _combat_with_anomaly()
    assert CombatService._object_movement_profile({"type": "anomaly"})["blocked"] is False

    moved, _, _ = CombatService.move_character(
        location.id, user.id, actor.id, 4, 2, movement_mode="walk",
    )

    assert (moved.pos_x, moved.pos_y) == (2, 2)
    active = actor.data["health"]["combatMeta"]["activeAnomaly"]
    assert active["key"] == "otboynik"


def test_anomaly_trigger_uses_at_least_three_quarters_of_cell():
    anomaly = {'type': 'anomaly', 'dimensions': {'width': 2.5, 'depth': 1}}
    assert anomaly_covers_tile(anomaly, 2, 2, 3, 2)
    assert not anomaly_covers_tile(anomaly, 2, 2, 4, 2)
    anomaly['dimensions']['width'] = 2.48
    assert not anomaly_covers_tile(anomaly, 2, 2, 3, 2)
    anomaly['dimensions']['width'] = 2.5
    anomaly['rotation'] = 90
    assert anomaly_covers_tile(anomaly, 2, 2, 2, 3)
    assert not anomaly_covers_tile(anomaly, 2, 2, 3, 2)
    anomaly['rotation'] = 0
    anomaly['x'] = -0.25
    assert not anomaly_covers_tile(anomaly, 2, 2, 3, 2)


def test_legacy_anomaly_scale_expands_trigger():
    anomaly = {'type': 'anomaly', 'scale': 2.5}
    assert anomaly_covers_tile(anomaly, 2, 2, 3, 2)
    assert not anomaly_covers_tile(anomaly, 2, 2, 4, 2)


def test_resized_anomaly_stops_route_before_original_tile(app):
    user, location, actor, placed, _ = _combat_with_anomaly()
    tiles = deepcopy(location.tiles_data)
    tiles[2][2]['objects'][0]['dimensions'] = {'width': 2.5, 'depth': 1, 'height': 1}
    location.tiles_data = tiles
    placed.pos_x = 4
    db.session.commit()

    moved, _, _ = CombatService.move_character(
        location.id, user.id, actor.id, 2, 2, movement_mode='walk',
    )

    assert (moved.pos_x, moved.pos_y) == (3, 2)
    assert actor.data['health']['combatMeta']['activeAnomaly']['key'] == 'otboynik'


def test_triggered_hidden_tile_anomaly_reveals_itself(app):
    user, location, actor, _, _ = _combat_with_anomaly()
    tiles = deepcopy(location.tiles_data)
    tiles[2][2]['objects'][0]['gm_hidden'] = True
    location.tiles_data = tiles
    db.session.commit()

    moved, _, _ = CombatService.move_character(
        location.id, user.id, actor.id, 2, 2, movement_mode='walk',
    )

    assert location.tiles_data[2][2]['objects'][0]['gm_hidden'] is False
    assert moved._revealed_anomaly == {'object_id': None, 'tile_x': 2, 'tile_y': 2}


def test_triggered_hidden_database_anomaly_reveals_itself(app):
    user, location, actor, _, _ = _combat_with_anomaly()
    tiles = deepcopy(location.tiles_data)
    tiles[2][2]['objects'] = []
    location.tiles_data = tiles
    anomaly = LocationObject(
        location_id=location.id, type='anomaly', tile_x=2, tile_y=2,
        properties={'anomaly_key': 'otboynik', 'gm_hidden': True},
    )
    db.session.add(anomaly)
    db.session.commit()

    moved, _, _ = CombatService.move_character(
        location.id, user.id, actor.id, 2, 2, movement_mode='walk',
    )

    assert anomaly.properties['gm_hidden'] is False
    assert moved._revealed_anomaly['object_id'] == anomaly.id


def test_triggered_hidden_anomaly_broadcasts_tile_update(app):
    from app.sockets.location import handle_join_location, handle_move_in_location

    socketio.on_event('join_location', handle_join_location)
    socketio.on_event('move_in_location', handle_move_in_location)
    user, location, actor, _, _ = _combat_with_anomaly()
    db.session.add(LobbyParticipant(lobby_id=location.lobby_id, user_id=user.id))
    tiles = deepcopy(location.tiles_data)
    tiles[2][2]['objects'][0]['gm_hidden'] = True
    location.tiles_data = tiles
    db.session.commit()
    token = create_access_token(identity=str(user.id))
    connection = socketio.test_client(app)
    try:
        connection.emit('join_location', {
            'token': token, 'location_id': location.id, 'character_id': actor.id,
        })
        connection.get_received()
        connection.emit('move_in_location', {
            'token': token, 'location_id': location.id, 'character_id': actor.id,
            'x': 2, 'y': 2, 'movement_mode': 'walk',
        })
        updates = [event for event in connection.get_received()
                   if event['name'] == 'location_tiles_updated']
        assert len(updates) == 1
        assert updates[0]['args'][0]['updates'][0]['objects'][0]['gm_hidden'] is False
    finally:
        connection.disconnect()


def test_mutant_camouflage_action_hides_model_in_combat_snapshot(app):
    user, location, actor, _, _ = _combat_with_anomaly()
    data = deepcopy(actor.data)
    data['is_mutant'] = True
    data['mutant'] = {'traits': ['Маскировка']}
    actor.data = data
    db.session.commit()

    result = CombatService.perform_action(
        location.id, user.id,
        location_character_id=LocationCharacter.query.filter_by(
            location_id=location.id, character_id=actor.id,
        ).first().id,
        action_key='mutant_camouflage',
    )

    snapshot = next(char for char in result['state']['characters'] if char['character_id'] == actor.id)
    assert snapshot['camouflaged'] is True


def test_database_anomaly_uses_dimensions_and_offset(app):
    _, location, _, _, _ = _combat_with_anomaly()
    anomaly = LocationObject(
        location_id=location.id, type='anomaly', tile_x=1, tile_y=3,
        properties={
            'anomaly_key': 'otboynik',
            'dimensions': {'width': 2.5, 'depth': 1, 'height': 1},
            'offset_x': 0.25,
        },
    )
    db.session.add(anomaly)
    db.session.commit()

    assert CombatService._anomalies_at_tile(location, 2, 3)[0]['object_id'] == anomaly.id
    assert not CombatService._anomalies_at_tile(location, 3, 3)


def test_anomaly_on_target_tile_takes_priority_over_expanded_neighbor(app):
    _, location, _, _, _ = _combat_with_anomaly()
    tiles = deepcopy(location.tiles_data)
    tiles[2][1]['objects'] = [{
        'type': 'anomaly', 'anomalyKey': 'banya',
        'dimensions': {'width': 2.5, 'depth': 1},
    }]
    location.tiles_data = tiles
    db.session.commit()

    anomalies = CombatService._anomalies_at_tile(location, 2, 2)
    assert [anomaly['key'] for anomaly in anomalies] == ['otboynik', 'banya']


def test_relocation_within_expanded_anomaly_keeps_active_exposure(app):
    _, location, actor, placed, _ = _combat_with_anomaly()
    tiles = deepcopy(location.tiles_data)
    tiles[2][2]['objects'][0]['dimensions'] = {'width': 2.5, 'depth': 1}
    location.tiles_data = tiles
    db.session.commit()
    active = CombatService._enter_anomaly(
        placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1,
    )
    active['rounds'] = 2
    placed.pos_x = 3

    assert not CombatService._sync_anomaly_after_relocation(location, placed, (2, 2))
    assert actor.data['health']['combatMeta']['activeAnomaly']['rounds'] == 2
    placed.pos_x = 4
    assert CombatService._sync_anomaly_after_relocation(location, placed, (3, 2))
    assert 'activeAnomaly' not in actor.data['health']['combatMeta']


@pytest.mark.parametrize('has_idle_state', [True, False])
def test_gm_relocation_outside_combat_exits_anomaly_without_exposure(app, has_idle_state):
    user, location, actor, placed, _ = _combat_with_anomaly()
    state = LocationCombatState.query.filter_by(location_id=location.id).one()
    if has_idle_state:
        state.status = 'idle'
        state.turn_order = []
        state.current_location_character_id = None
    else:
        db.session.delete(state)
    placed.pos_x = placed.pos_y = 2
    active = CombatService._enter_anomaly(
        placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1,
    )
    actor.data['health']['effects'] = [
        {'type': 'blindness', 'source': active['name'], 'value': 90},
        {'type': 'blindness', 'source': 'other', 'value': 10},
    ]
    db.session.commit()

    moved, _, snapshot = CombatService.move_character(
        location.id, user.id, actor.id, 3, 2,
    )

    assert (moved.pos_x, moved.pos_y) == (3, 2)
    assert 'activeAnomaly' not in actor.data['health']['combatMeta']
    assert actor.data['health']['current'] == 700
    assert all(effect.get('source') != active['name'] for effect in actor.data['health']['effects'])
    assert any(effect.get('source') == 'other' for effect in actor.data['health']['effects'])
    assert snapshot['characters'][0]['active_anomaly'] is None


def test_successful_escape_costs_three_ap_and_moves_without_damage(app, monkeypatch):
    user, location, actor, placed, _ = _combat_with_anomaly()
    placed.pos_x = placed.pos_y = 2
    CombatService._enter_anomaly(placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1)
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)

    result = CombatService.perform_action(
        location.id, user.id, placed.id, "escape_anomaly", target_x=1, target_y=2,
    )

    assert result["anomaly"]["outcome"] == "success"
    assert result["anomaly"]["exits"] is True
    assert (placed.pos_x, placed.pos_y) == (1, 2)
    assert placed.action_points_current == 2
    assert actor.data["health"]["current"] == 700
    assert "activeAnomaly" not in actor.data["health"]["combatMeta"]


def test_natural_one_deals_125_percent_and_keeps_character_trapped(app, monkeypatch):
    user, location, actor, placed, _ = _combat_with_anomaly()
    placed.pos_x = placed.pos_y = 2
    CombatService._enter_anomaly(placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1)
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 1)

    result = CombatService.perform_action(
        location.id, user.id, placed.id, "escape_anomaly", target_x=1, target_y=2,
    )

    assert result["anomaly"]["outcome"] == "critical_failure"
    assert result["anomaly"]["exposure"]["damage"] == 250
    assert (placed.pos_x, placed.pos_y) == (2, 2)
    assert actor.data["health"]["combatMeta"]["activeAnomaly"]["rounds"] == 1


def test_psi_anomaly_uses_will_for_escape(app, monkeypatch):
    user, location, actor, placed, _ = _combat_with_anomaly("ekho")
    placed.pos_x = placed.pos_y = 2
    CombatService._enter_anomaly(placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1)
    db.session.commit()
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)

    result = CombatService.perform_action(
        location.id, user.id, placed.id, "escape_anomaly", target_x=1, target_y=2,
    )

    assert result["anomaly"]["category"] == "psi"
    assert result["anomaly"]["check"]["success"] is True


def test_remaining_in_anomaly_applies_full_exposure_at_turn_end(app):
    _, location, actor, placed, _ = _combat_with_anomaly()
    placed.pos_x = placed.pos_y = 2
    CombatService._enter_anomaly(placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1)

    result = CombatService._resolve_anomaly_end_turn(placed, 1)

    assert result["fraction"] == 1.0
    assert result["damage"] == 200
    assert actor.data["health"]["current"] == 500
    assert actor.data["health"]["combatMeta"]["activeAnomaly"]["rounds"] == 1


def test_fall_anomaly_uses_existing_fall_rules(app, monkeypatch):
    _, location, actor, placed, _ = _combat_with_anomaly("batut")
    placed.pos_x = placed.pos_y = 2
    active = CombatService._enter_anomaly(
        placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1,
    )
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)

    result = CombatService._apply_anomaly_exposure(placed, active, 1, round_number=1)

    assert result["fall"]["height"] == 9
    assert result["fall"]["success"] is True
    assert result["damage"] == 140
    assert actor.data["health"]["current"] == 560


def test_throwing_anomaly_ejects_character_in_entry_direction(app, monkeypatch):
    _, location, actor, placed, _ = _combat_with_anomaly("batut")
    placed.pos_x = placed.pos_y = 2
    active = CombatService._enter_anomaly(
        placed,
        CombatService._anomalies_at_tile(location, 2, 2)[0],
        1,
        previous_position=(1, 2),
    )
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)

    result = CombatService._resolve_anomaly_end_turn(placed, 1)

    assert result["ejected_to"] == {"x": 3, "y": 2}
    assert (placed.pos_x, placed.pos_y) == (3, 2)
    assert "activeAnomaly" not in actor.data["health"]["combatMeta"]


def test_escalating_electric_anomaly_uses_third_round_damage(app):
    _, location, _, placed, _ = _combat_with_anomaly("katushka")
    placed.pos_x = placed.pos_y = 2
    active = CombatService._enter_anomaly(
        placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1,
    )
    active["rounds"] = 2

    result = CombatService._apply_anomaly_exposure(placed, active, 1, round_number=3)

    assert result["damage"] == 200


def test_web_and_tesla_damage_depends_on_escape_failure_margin(app):
    _, location, _, placed, _ = _combat_with_anomaly("pautina")
    placed.pos_x = placed.pos_y = 2
    active = CombatService._enter_anomaly(
        placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1,
    )

    result = CombatService._apply_anomaly_exposure(
        placed, active, 0.5, round_number=1, escape_margin=3,
    )

    assert result["hit_count"] == 3
    assert result["damage"] == 75


def test_trap_uses_second_discharge_after_first_round(app):
    _, location, _, placed, _ = _combat_with_anomaly("kapkan")
    placed.pos_x = placed.pos_y = 2
    active = CombatService._enter_anomaly(
        placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1,
    )
    active["rounds"] = 1

    result = CombatService._apply_anomaly_exposure(
        placed, active, 1, round_number=2,
    )

    assert result["damage"] == 300


def test_ionic_fog_uses_walking_damage_during_escape_attempt(app):
    _, location, _, placed, _ = _combat_with_anomaly("ionny_tuman")
    placed.pos_x = placed.pos_y = 2
    active = CombatService._enter_anomaly(
        placed, CombatService._anomalies_at_tile(location, 2, 2)[0], 1,
    )

    result = CombatService._apply_anomaly_exposure(
        placed, active, 0.5, round_number=1, escape_margin=4,
    )

    assert result["damage"] == 100
