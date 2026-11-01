from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.extensions import db
from app.models import Location, LocationCharacter, LocationObject, LobbyCharacter
from app.services.combat import CombatService
from app.services.exceptions import ValidationError
from app.services.location_navigation import find_path, surface


def test_short_ground_route_stays_straight_and_preserves_weighted_optimal_cost():
    import heapq
    import random
    location = SimpleNamespace(grid_width=24, grid_height=20,
                               tiles_data=[[{'height': 1} for _ in range(24)] for _ in range(20)])
    route = find_path(location, [], (2, 10, 0), (7, 10, 0), lambda level: (set(), {}))
    assert route['path'] == [(x, 10) for x in range(2, 8)]
    for seed in range(12):
        rng = random.Random(seed)
        blocked = {(x, y) for x in range(24) for y in range(20) if rng.random() < .18}
        blocked -= {(2, 10), (21, 10)}
        costs = {(x, y): 5 for x in range(24) for y in range(20) if rng.random() < .08}
        costs.pop((21, 10), None)
        queue, best = [(0, (2, 10))], {(2, 10): 0}
        while queue:
            cost, key = heapq.heappop(queue)
            if cost != best[key]:
                continue
            for dx, dy in [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
                p = (key[0]+dx, key[1]+dy)
                if p in blocked or not (0 <= p[0] < 24 and 0 <= p[1] < 20):
                    continue
                candidate = cost+1+costs.get(p, 0)
                if candidate < best.get(p, float('inf')):
                    best[p] = candidate
                    heapq.heappush(queue, (candidate, p))
        route = find_path(location, [], (2,10,0), (21,10,0), lambda level: (blocked, costs))
        assert (route['cost'] if route else None) == best.get((21,10))
        if route:
            assert all(p not in blocked for p in route['path'])


def test_navigation_keeps_legacy_top_level_floor_numbers():
    location = SimpleNamespace(grid_width=10, grid_height=10,
                               tiles_data=[[{'height': 1} for _ in range(10)] for _ in range(10)])
    floor = {'type': 'floor', 'tile_x': 5, 'tile_y': 5, 'level': 1,
             'properties': {'dimensions': {'width': 6, 'depth': 6, 'height': .12}}}
    route = find_path(location, [floor], (4,4,1), (6,4,1), lambda level: (set(), {}))
    assert route and route['cost'] == 2
    assert all(n['level'] == 1 for n in route['surfaces'])


def obj(kind, x, y, level, width=8, depth=8, height=.12, **extra):
    return SimpleNamespace(id=len(extra), type=kind, name=kind, tile_x=x, tile_y=y, properties={
        'level': level, 'building_id': 'A', 'dimensions': {'width': width, 'depth': depth, 'height': height}, **extra})


@pytest.mark.parametrize('base', [-2, -1, 0, 1])
def test_stairs_connect_adjacent_levels_in_both_directions_without_side_entry(base):
    location = SimpleNamespace(grid_width=12, grid_height=12,
                               tiles_data=[[{'height': 1} for _ in range(12)] for _ in range(12)])
    stairs = obj('stairs', 5, 5, base, 1.2, 3, 2.4)
    objects = [obj('floor', 5, 5, base), obj('floor', 5, 5, base+1), stairs]
    maps = lambda level: (set(), {})
    path = find_path(location, objects, (5, 3, base), (5, 8, base+1), maps)
    assert path
    assert path['surfaces'][0]['level'] == base
    assert path['surfaces'][-1]['level'] == base+1
    heights = [n['height'] for n in path['surfaces']]
    assert heights == sorted(heights)
    assert max(abs(a-b) for a, b in zip(heights, heights[1:])) <= 1.01
    assert find_path(location, objects, (5, 8, base+1), (5, 3, base), maps)
    assert not surface(location, objects, 5, 5, base+1)
    assert not find_path(location, objects[:-1], (5, 3, base), (5, 8, base+1), maps)


def test_coincident_xy_collisions_and_openings_are_isolated_by_signed_floor(app):
    location = SimpleNamespace(id=20, tiles_data=[])
    lower = SimpleNamespace(id=1, character_id=1, pos_x=3, pos_y=3, floor_level=-1)
    upper = SimpleNamespace(id=2, character_id=2, pos_x=3, pos_y=3, floor_level=1)
    wall = obj('wall', 7, 7, -1, 1, .2, 2.4)
    with patch.object(LocationObject, 'query') as objects, patch.object(LocationCharacter, 'query') as characters:
        objects.filter_by.return_value.all.return_value = [wall]
        characters.filter_by.return_value.all.return_value = [lower, upper]
        underground, _ = CombatService._build_movement_map(location, 1, floor_level=-1)
        first, _ = CombatService._build_movement_map(location, 1, floor_level=1)
        assert (3, 3) not in underground
        assert (3, 3) in first
        assert (7, 7) in underground and (7, 7) not in first
    door = obj('door', 7, 7, 1, .9, .18, 2, is_open=True)
    assert not CombatService._door_is_in_wall(door, wall)
    assert not CombatService._is_adjacent(lower, SimpleNamespace(pos_x=4, pos_y=3, floor_level=1))


def test_rotated_and_stacked_stair_flights_do_not_teleport_through_the_slab():
    import math
    location = SimpleNamespace(grid_width=14, grid_height=14,
                               tiles_data=[[{'height': 1} for _ in range(14)] for _ in range(14)])
    objects = [obj('floor', 6, 6, level, 12, 12) for level in [-1, 0, 1]]
    objects += [obj('stairs', 6, 6, level, 1.2, 4.2, 2.4, rotation=math.pi/4) for level in [-1, 0]]
    route = find_path(location, objects, (4, 4, -1), (9, 9, 1), lambda level: (set(), {}))
    assert route
    assert {n['level'] for n in route['surfaces']} == {-1, 0, 1}
    assert len(route['surfaces']) > 8


@pytest.fixture
def storey_game(client, create_user, auth_headers):
    gm = create_user('storey-gm')
    headers = auth_headers(gm)
    lobby_id = client.post('/lobbies/', headers=headers, json={
        'name': 'Storeys', 'map_type': 'empty', 'chunks_width': 1, 'chunks_height': 1}).get_json()['id']
    location = Location(lobby_id=lobby_id, name='storeys', world_tile_x=1, world_tile_z=1,
                        grid_width=12, grid_height=12,
                        tiles_data=[[{'terrain': 'bare_dirt', 'height': 1, 'objects': []} for _ in range(12)] for _ in range(12)])
    db.session.add(location)
    db.session.flush()
    for level in [-1, 0, 1]:
        f = obj('floor', 5, 5, level)
        db.session.add(LocationObject(location_id=location.id, name='floor', type='floor',
                                      tile_x=5, tile_y=5, properties=f.properties))
    for x, level in [(5, 0), (7, -1)]:
        stair = obj('stairs', x, 5, level, 1.2, 3, 2.4)
        db.session.add(LocationObject(location_id=location.id, name='stairs', type='stairs',
                                      tile_x=x, tile_y=5, properties=stair.properties))
    character = LobbyCharacter(lobby_id=lobby_id, owner_id=gm['id'], name='walker', data={})
    db.session.add(character)
    db.session.flush()
    actor = LocationCharacter(location_id=location.id, character_id=character.id, pos_x=5, pos_y=3, controlled_by=gm['id'])
    db.session.add(actor)
    db.session.commit()
    return gm, headers, lobby_id, location, actor


def test_server_moves_up_down_and_into_basement_and_persists_without_changing_terrain(storey_game):
    gm, _, _, location, actor = storey_game
    terrain = deepcopy(location.tiles_data)
    assert actor.floor_level == 0
    moved, cost, _ = CombatService.move_character(location.id, gm['id'], actor.character_id, 5, 8, floor_level=1)
    assert moved.floor_level == 1 and cost > 0
    assert CombatService.physical_position(moved)['height'] == pytest.approx(3.435)
    assert len(moved._movement_surfaces) > 3
    assert {p['level'] for p in moved._movement_surfaces} == {0, 1}
    CombatService.move_character(location.id, gm['id'], actor.character_id, 5, 3, floor_level=0)
    CombatService.move_character(location.id, gm['id'], actor.character_id, 7, 3, floor_level=-1)
    actor_id = actor.id
    db.session.expire_all()
    assert db.session.get(LocationCharacter, actor_id).floor_level == -1
    assert location.tiles_data == terrain
    with pytest.raises(ValidationError, match='blocked'):
        CombatService.move_character(location.id, gm['id'], actor.character_id, 5, 8, floor_level=5)


def test_server_preview_is_read_only_and_cover_above_does_not_block_same_floor(client, storey_game):
    gm, headers, lobby_id, location, actor = storey_game
    url = f'/lobbies/{lobby_id}/locations/{location.id}/movement-preview'
    response = client.post(url, headers=headers, json={'character_id': actor.character_id, 'x': 5, 'y': 8, 'floor_level': 1})
    assert response.status_code == 200
    assert response.get_json()['route']['surfaces'][-1]['level'] == 1
    assert actor.floor_level == 0 and (actor.pos_x, actor.pos_y) == (5, 3)
    assert client.post(url, headers=headers, json={'character_id': actor.character_id, 'x': -1, 'y': 8}).status_code == 400
    actor.pos_x = 2
    target = SimpleNamespace(pos_x=2, pos_y=8, floor_level=0, posture='standing')
    wall = LocationObject(location_id=location.id, name='upper wall', type='wall', tile_x=2, tile_y=5,
                          properties={'level':1,'dimensions':{'width':3,'depth':.2,'height':2.4}})
    db.session.add(wall); db.session.commit()
    assert CombatService._cover_analysis(location.id, actor, target)['grade'] == 'none'
    target.floor_level = 1
    assert CombatService._cover_analysis(location.id, actor, target)['grade'] == 'full'


def test_legacy_sqlite_migration_backfills_zero_and_downgrade_keeps_positions(tmp_path):
    import importlib.util
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    path = Path(__file__).parents[1] / 'migrations/versions/c6a2d4e8f019_character_floor_level.py'
    spec = importlib.util.spec_from_file_location('storey_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f'sqlite:///{tmp_path / "legacy.sqlite"}')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE location_characters (id INTEGER PRIMARY KEY, pos_x INTEGER, pos_y INTEGER)'))
        connection.execute(sa.text('INSERT INTO location_characters VALUES (1, 7, 9)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT pos_x, pos_y, floor_level FROM location_characters')).one() == (7, 9, 0)
            migration.downgrade()
            assert connection.execute(sa.text('SELECT pos_x, pos_y FROM location_characters')).one() == (7, 9)
    engine.dispose()


def test_signed_lamp_levels_preserve_legacy_height_validation():
    from app.services.location_lighting import normalize_location_lighting
    light = {'x': 4, 'z': 4, 'height': -.6, 'level': -1,
             'color': '#ffe2aa', 'intensity': 2, 'distance': 8}
    saved = normalize_location_lighting({'local_lights': [light]}, 12, 12)['local_lights'][0]
    assert saved['height'] == -.6 and saved['level'] == -1
    with pytest.raises(ValidationError):
        normalize_location_lighting({'local_lights': [{k:v for k,v in light.items() if k != 'level'}]}, 12, 12)
    with pytest.raises(ValidationError):
        normalize_location_lighting({'local_lights': [{**light,'level':-1.5}]}, 12, 12)


@pytest.mark.parametrize('base',[-2,-1,0,1])
def test_vertical_ladder_is_a_real_same_xy_route_sealed_by_a_hatch(base):
    location=SimpleNamespace(grid_width=12,grid_height=12,tiles_data=[[{'height':1} for _ in range(12)] for _ in range(12)])
    floors=[obj('floor',5,5,base),obj('floor',5,5,base+1)]
    ladder=obj('ladder',5,5,base,.9,.9,2.4)
    hatch=obj('hatch',5,5,base+1,1,1,.08,is_open=False)
    route=lambda items:find_path(location,items,(5,5,base),(5,5,base+1),lambda level:(set(),{}))
    assert route(floors) is None
    assert route(floors+[ladder,hatch]) is None
    hatch.properties['is_open']=True
    path=route(floors+[ladder,hatch])
    assert path and path['cost']==3
    assert all((n['x'],n['y'])==(5,5) for n in path['surfaces'])
    assert len(path['surfaces'])>=9
    heights=[n['height'] for n in path['surfaces']]
    assert heights==sorted(heights)
    assert find_path(location,floors+[ladder,hatch],(5,5,base+1),(5,5,base),lambda level:(set(),{}))
    assert not find_path(location,floors+[ladder,hatch],(5,5,base),(5,5,base+1),lambda level:({(5,5)} if level==base+1 else set(),{}))
    ladder.properties['dimensions']['height']=1.2
    assert route(floors+[ladder,hatch]) is None


def test_hatch_action_validates_reach_lock_and_controls_and_persists_movement(client,storey_game,create_user,auth_headers):
    from app.models import LobbyParticipant
    gm,headers,lobby_id,location,actor=storey_game
    LocationObject.query.filter_by(location_id=location.id,type='stairs').delete()
    ladder=LocationObject(location_id=location.id,type='ladder',tile_x=5,tile_y=5,properties=obj('ladder',5,5,0,.9,.9,2.4).properties)
    hatch=LocationObject(location_id=location.id,type='hatch',tile_x=5,tile_y=5,properties=obj('hatch',5,5,1,1,1,.12,visual_asset='bunker_hatch',is_open=False).properties)
    db.session.add_all([ladder,hatch]);db.session.commit()
    url=f'/lobbies/{lobby_id}/locations/objects/{hatch.id}'
    payload={'actor_character_id':actor.character_id,'properties':{'is_open':True}}
    assert client.patch(url,headers=headers,json=payload).status_code==400 # two tiles away
    actor.pos_y=5;db.session.commit()
    with pytest.raises(ValidationError,match='blocked'):
        CombatService.move_character(location.id,gm['id'],actor.character_id,5,5,floor_level=1)
    hatch.properties={**hatch.properties,'locked':True};db.session.commit()
    assert client.patch(url,headers=headers,json=payload).status_code==400
    hatch.properties={**hatch.properties,'locked':False};db.session.commit()
    stranger=create_user('hatch-stranger')
    db.session.add(LobbyParticipant(lobby_id=lobby_id,user_id=stranger['id']));db.session.commit()
    assert client.patch(url,headers=auth_headers(stranger),json=payload).status_code==403
    assert client.patch(url,headers=headers,json=payload).status_code==200
    moved,cost,_=CombatService.move_character(location.id,gm['id'],actor.character_id,5,5,floor_level=1)
    assert cost==pytest.approx(2.4) and moved.floor_level==1
    target=SimpleNamespace(pos_x=5,pos_y=5,floor_level=0,posture='standing')
    assert CombatService._cover_analysis(location.id,moved,target)['grade']=='none'
    assert CombatService.physical_position(moved)['height']==pytest.approx(3.435)
    assert client.patch(url,headers=headers,json={**payload,'properties':{'is_open':False}}).status_code==200
    assert CombatService._cover_analysis(location.id,moved,target)['grade']=='full'
    with pytest.raises(ValidationError,match='blocked'):
        CombatService.move_character(location.id,gm['id'],actor.character_id,5,5,floor_level=0)
    assert client.patch(url,headers=headers,json=payload).status_code==200
    CombatService.move_character(location.id,gm['id'],actor.character_id,5,5,floor_level=0)
    db.session.expire_all()
    assert db.session.get(LocationCharacter,actor.id).floor_level==0
    assert db.session.get(LocationObject,hatch.id).properties['is_open'] is True


@pytest.mark.parametrize('upper',[-1,0,1,3])
@pytest.mark.parametrize('kind',['hatch','floor_opening'])
def test_explicit_drop_without_a_ladder_on_signed_levels(upper,kind):
    from app.services.location_navigation import drop_route
    location=SimpleNamespace(grid_width=12,grid_height=12,tiles_data=[[{'height':1} for _ in range(12)] for _ in range(12)])
    portal=obj(kind,5,5,upper,1,1,.08,is_open=True)
    objects=[obj('floor',5,5,upper),obj('floor',5,5,upper-1),portal]
    maps=lambda level:(set(),{})
    start=(5,4,upper)
    assert find_path(location,objects,start,(5,5,upper-1),maps) is None
    assert find_path(location,objects,(5,5,upper-1),(5,4,upper),maps) is None
    plan=drop_route(location,objects,start,portal,maps)
    assert plan and plan['height']==pytest.approx(2.4)
    assert plan['landing']['level']==upper-1
    assert all(n['x']==5 and n['y'] in {4,5} for n in plan['surfaces'])
    assert drop_route(location,objects,(5,3,upper),portal,maps) is None
    assert drop_route(location,objects,start,portal,lambda level:({(5,5)} if level==upper-1 else set(),{})) is None
    closed=obj('hatch',5,5,upper,1,1,.08,is_open=False)
    assert drop_route(location,objects+[closed],start,portal,maps) is None


def test_drop_stops_on_first_support_and_cannot_pass_a_closed_intermediate_lid():
    from app.services.location_navigation import drop_route
    location=SimpleNamespace(grid_width=12,grid_height=12,tiles_data=[[{'height':1} for _ in range(12)] for _ in range(12)])
    portal=obj('floor_opening',5,5,2,1,1,.05)
    objects=[obj('floor',5,5,level) for level in [-1,0,1,2]]
    objects += [portal,obj('floor_opening',5,5,1,1,1,.05),obj('floor_opening',5,5,0,1,1,.05)]
    maps=lambda level:(set(),{})
    plan=drop_route(location,objects,(5,4,2),portal,maps)
    assert plan['landing']['level']==-1 and plan['height']==pytest.approx(7.2)
    closed=obj('hatch',5,5,1,1,1,.08,is_open=False)
    plan=drop_route(location,objects+[closed],(5,4,2),portal,maps)
    assert plan['landing']['level']==1 and plan['height']==pytest.approx(2.4)
    without_floor=[o for o in objects if not (o.type=='floor' and o.properties['level']==1)]
    assert drop_route(location,without_floor+[closed],(5,4,2),portal,maps) is None


def test_drop_applies_existing_fall_rules_and_saves_landing(storey_game):
    gm,_,_,location,actor=storey_game
    LocationObject.query.filter_by(location_id=location.id,type='stairs').delete()
    portal=LocationObject(location_id=location.id,type='hatch',tile_x=5,tile_y=5,
                          properties=obj('hatch',5,5,0,1,1,.08,is_open=True).properties)
    db.session.add(portal);actor.pos_y=4;db.session.commit()
    terrain=deepcopy(location.tiles_data)
    plan=CombatService._object_drop_plan(location,actor,portal)
    assert plan
    x,y=plan['landing']['x'],plan['landing']['y']
    with patch('app.services.combat.random.randint',return_value=1):
        moved,cost,state=CombatService.move_character(location.id,gm['id'],actor.character_id,x,y,special_action='drop_down',object_id=portal.id)
    assert moved.floor_level==-1 and (moved.pos_x,moved.pos_y)==(x,y)
    assert cost==0 and state
    assert moved._movement_fall['height']==pytest.approx(2.4)
    assert moved._movement_fall['success'] is False
    assert [n['damage'] for n in moved._movement_fall['legs']]==[30,30]
    assert CombatService.physical_position(moved)['height']==pytest.approx(-1.365)
    health=deepcopy(moved.character.data['health'])
    db.session.expire_all()
    assert db.session.get(LocationCharacter,moved.id).floor_level==-1
    assert moved.character.data['health']==health
    assert location.tiles_data==terrain


def test_closed_or_occupied_drop_is_rejected_before_damage_and_cost(storey_game):
    from app.models import LocationCombatState
    gm,_,_,location,actor=storey_game
    LocationObject.query.filter_by(location_id=location.id,type='stairs').delete()
    portal=LocationObject(location_id=location.id,type='floor_opening',tile_x=5,tile_y=5,
                          properties=obj('floor_opening',5,5,0,1,1,.05).properties)
    db.session.add(portal);actor.pos_y=4;actor.action_points_current=0
    state=LocationCombatState(location_id=location.id,status='active',round_number=1,current_location_character_id=actor.id)
    db.session.add(state);db.session.commit()
    def drop(**extra):
        return CombatService.move_character(location.id,gm['id'],actor.character_id,5,5,special_action='drop_down',object_id=portal.id,**extra)
    with patch.object(CombatService,'resolve_fall',wraps=CombatService.resolve_fall) as fall:
        with pytest.raises(ValidationError,match='action points'):drop()
        with pytest.raises(ValidationError,match='landing'):drop(floor_level=1)
        assert not fall.called and actor.floor_level==0
        actor.action_points_current=2
        occupied=LobbyCharacter(lobby_id=location.lobby_id,owner_id=gm['id'],name='below',data={})
        db.session.add(occupied);db.session.flush()
        blocker=LocationCharacter(location_id=location.id,character_id=occupied.id,pos_x=5,pos_y=5,floor_level=-1)
        db.session.add(blocker);db.session.commit()
        with pytest.raises(ValidationError,match='landing'):drop()
        assert not fall.called and actor.action_points_current==2
        db.session.delete(blocker);db.session.commit()
        with patch('app.services.combat.random.randint',return_value=20):drop()
        assert fall.call_count==1 and actor.action_points_current==1 and actor.floor_level==-1
