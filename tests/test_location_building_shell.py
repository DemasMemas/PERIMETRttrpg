"""Exercise actual JS-generated objects through the existing Python movement rules."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import LocationCharacter, LocationObject
from app.services.combat import CombatService
from app.services.location_navigation import find_path, surface
from app.extensions import db, socketio


def shell(shape='rectangle', condition='intact', storeys=2, basements=1, roof_type=None, width=10, depth=10):
    script = """const fs=require('fs');
const roof='data:text/javascript;base64,'+fs.readFileSync('app/static/js/locationRoofSections.js').toString('base64');
const source=fs.readFileSync('app/static/js/locationHouseGenerator.js','utf8').replace("'./locationRoofSections.js'",JSON.stringify(roof));
import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))
.then(m=>console.log(JSON.stringify(m.planBuildingShell(JSON.parse(process.argv[1])).objects)));
"""
    result = subprocess.run(['node', '-e', script, json.dumps(dict(minX=2, minZ=2, width=width,
        depth=depth, storeys=storeys, basements=basements, shape=shape, condition=condition, seed='shell', buildingId='shell',roofType=roof_type))],
        cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True, encoding='utf-8')
    return json.loads(result.stdout)


@pytest.mark.parametrize('size', [6, 8, 12, 16])
def test_flights_connect_small_and_large_houses_and_cellar_hatch_seals_route(app, size):
    objects = [SimpleNamespace(id=i+1, **o) for i,o in enumerate(shell(width=size, depth=size, storeys=3))]
    location = SimpleNamespace(id=1, grid_width=20, grid_height=20,
        tiles_data=[[{'height':1} for _ in range(20)] for _ in range(20)])
    entry = next(o for o in objects if o.type=='door')
    hatch = next(o for o in objects if o.type=='hatch')
    ladder = next(o for o in objects if o.type=='ladder')
    start=(entry.tile_x,entry.tile_y+1,0)
    assert abs(hatch.tile_x-entry.tile_x)+abs(hatch.tile_y-entry.tile_y)>=3
    assert len([o for o in objects if o.type=='stairs'])==2
    with patch.object(LocationObject,'query') as oq,patch.object(LocationCharacter,'query') as cq:
        oq.filter_by.return_value.all.return_value=objects
        cq.filter_by.return_value.all.return_value=[]
        maps={l:CombatService._build_movement_map(location,floor_level=l) for l in [-1,0,1,2]}
        target=(ladder.tile_x,ladder.tile_y,-1)
        assert find_path(location,objects,start,(*start[:2],2),lambda l:maps[l])
        assert not find_path(location,objects,start,target,lambda l:maps[l])
        hatch.properties['is_open']=True
        maps={l:CombatService._build_movement_map(location,floor_level=l) for l in [-1,0,1,2]}
        assert find_path(location,objects,start,target,lambda l:maps[l])
        assert find_path(location,objects,target,start,lambda l:maps[l])


@pytest.mark.parametrize('shape', ['rectangle', 'L', 'U', 'C', 'compound'])
@pytest.mark.parametrize('condition', ['intact', 'ruins'])
def test_generated_entrance_basement_and_upper_connections(app, shape, condition):
    objects = [SimpleNamespace(id=i+1, **o) for i, o in enumerate(shell(shape, condition))]
    hatches = [o for o in objects if o.type == 'hatch']
    assert hatches and all(o.properties['is_open'] is False for o in hatches)
    assert all(o.properties['level'] < 0 for o in objects if o.type == 'ladder')
    for hatch in hatches:
        hatch.properties['is_open'] = True
    location = SimpleNamespace(id=1, grid_width=16, grid_height=16,
        tiles_data=[[{'height':1} for _ in range(16)] for _ in range(16)])
    with patch.object(LocationObject, 'query') as oq, patch.object(LocationCharacter, 'query') as cq:
        oq.filter_by.return_value.all.return_value = objects
        cq.filter_by.return_value.all.return_value = []
        maps = {level:CombatService._build_movement_map(location, floor_level=level) for level in [-1,0,1]}
        route = lambda a,b:find_path(location, objects, a, b, lambda level: maps[level])
        # Entry from outside, then a physical ladder into the basement, both directions.
        assert route((3,1,0), (3,3,0))
        assert route((3,3,0), (4,4,-1))
        assert route((4,4,-1), (3,3,0))
        assert bool(route((3,3,0), (3,3,1))) == (condition != 'ruins')
        if condition == 'ruins':
            for o in objects:
                if o.type == 'floor_opening' and o.properties['dimensions']['width'] == 2 and o.properties['level'] == 1:
                    assert surface(location, objects, o.tile_x, o.tile_y, 1) is None


def test_generated_four_storeys_and_two_basements_keep_separate_landings(app):
    objects = [SimpleNamespace(id=i+1, **o) for i,o in enumerate(shell('U', storeys=4, basements=2))]
    for o in objects:
        if o.type == 'hatch': o.properties['is_open'] = True
    location = SimpleNamespace(id=1, grid_width=16, grid_height=16,
        tiles_data=[[{'height':1} for _ in range(16)] for _ in range(16)])
    with patch.object(LocationObject, 'query') as oq, patch.object(LocationCharacter, 'query') as cq:
        oq.filter_by.return_value.all.return_value = objects
        cq.filter_by.return_value.all.return_value = []
        maps = {level:CombatService._build_movement_map(location, floor_level=level) for level in range(-2,4)}
        for start, end in [((3,3,-2),(3,3,3)), ((3,3,3),(3,3,-2))]:
            route = find_path(location,objects,start,end,lambda level:maps[level])
            assert route
            assert {p['level'] for p in route['surfaces']} == set(range(-2,4))


@pytest.mark.parametrize('kind',['flat','shed','gable'])
def test_volume_roofs_do_not_introduce_phantom_floor_obstacles(app,kind):
    objects = [SimpleNamespace(id=i+1, **o) for i,o in enumerate(shell('T', roof_type=kind))]
    for o in objects:
        if o.type == 'hatch': o.properties['is_open'] = True
    location=SimpleNamespace(id=1,grid_width=16,grid_height=16,tiles_data=[[{'height':1} for _ in range(16)] for _ in range(16)])
    with patch.object(LocationObject,'query') as oq,patch.object(LocationCharacter,'query') as cq:
        oq.filter_by.return_value.all.return_value=objects
        cq.filter_by.return_value.all.return_value=[]
        maps={level:CombatService._build_movement_map(location,floor_level=level) for level in [-1,0,1]}
        assert find_path(location,objects,(3,3,0),(4,4,-1),lambda level:maps[level])
        assert find_path(location,objects,(3,3,0),(3,3,1),lambda level:maps[level])
        for o in objects:
            if o.type=='roof':assert CombatService._object_movement_profile(o)=={'blocked':False,'climb_cost':0}


def test_generated_objects_keep_rest_socket_permissions_and_round_trip(client, app, create_user, auth_headers):
    gm, player = create_user('shell-gm'), create_user('shell-player')
    headers = auth_headers(gm)
    lobby = client.post('/lobbies/', headers=headers, json=dict(name='shell', map_type='empty',
        chunks_width=1, chunks_height=1)).get_json()
    api = f"/lobbies/{lobby['id']}"
    assert client.post(api+'/join', headers=auth_headers(player)).status_code == 200
    location = client.post(api+'/locations', headers=headers, json=dict(name='shell', world_tile_x=1,
        world_tile_z=1, grid_width=16, grid_height=16)).get_json()
    url = f"{api}/locations/{location['id']}"
    from app.sockets.location import handle_join_location
    socketio.on_event('join_location', handle_join_location)
    clients = [socketio.test_client(app), socketio.test_client(app)]
    try:
        for connection, user in zip(clients, [gm, player]):
            connection.emit('join_location', dict(token=user['token'], location_id=location['id']))
            assert any(e['name']=='joined_location' for e in connection.get_received())
        specs = shell('L', 'damaged',roof_type='gable')
        assert client.post(url+'/objects', headers=auth_headers(player), json=specs[0]).status_code == 403
        saved = []
        for spec in specs:
            response = client.post(url+'/objects', headers=headers, json=spec)
            assert response.status_code == 201
            saved.append(response.get_json())
        for connection in clients:
            created = [e['args'][0]['object'] for e in connection.get_received() if e['name']=='location_object_created']
            assert [o['id'] for o in created] == [o['id'] for o in saved]
        target = saved[0]
        assert client.delete(f"{api}/locations/objects/{target['id']}", headers=auth_headers(player)).status_code == 403
        assert client.patch(f"{api}/locations/objects/{target['id']}", headers=headers,
            json={'properties':{'dimensions':{'width':1.7,'depth':2,'height':.12},'color':'#887766'}}).status_code == 200
        db.session.remove()
        reloaded = client.get(url, headers=headers).get_json()['objects']
        assert len(reloaded) == len(specs)
        changed = next(o for o in reloaded if o['id']==target['id'])
        assert changed['properties']['dimensions']['width'] == 1.7
        assert changed['properties']['building_generation'] == specs[0]['properties']['building_generation']
        assert len(set(o['id'] for o in reloaded)) == len(specs)
        assert [o['properties'].get('roof_geometry') for o in reloaded if o['type']=='roof'] == [o['properties']['roof_geometry'] for o in saved if o['type']=='roof']
        for connection in clients:
            updates = [e for e in connection.get_received() if e['name']=='location_object_updated']
            assert len(updates) == 1
    finally:
        for connection in clients:
            connection.disconnect()


def test_cover_settings_accept_custom_hp_and_protection_only_from_gm(client, create_user, auth_headers):
    gm, player = create_user('cover-gm'), create_user('cover-player')
    headers = auth_headers(gm)
    lobby = client.post('/lobbies/', headers=headers, json={
        'name': 'Cover settings', 'map_type': 'empty', 'chunks_width': 1, 'chunks_height': 1,
    }).get_json()
    api = f"/lobbies/{lobby['id']}"
    assert client.post(api + '/join', headers=auth_headers(player)).status_code == 200
    location = client.post(api + '/locations', headers=headers, json={
        'name': 'Cover test', 'world_tile_x': 1, 'world_tile_z': 1,
        'grid_width': 6, 'grid_height': 6,
    }).get_json()
    created = client.post(f"{api}/locations/{location['id']}/objects", headers=headers, json={
        'name': 'Сетчатый забор PBR', 'type': 'fence', 'tile_x': 2, 'tile_y': 2,
        'properties': {'dimensions': {'width': 2, 'depth': 0.2, 'height': 2}},
    }).get_json()
    url = f"{api}/locations/objects/{created['id']}"
    properties = {'cover_class': 'strong', 'cover_max_hp': 8500, 'cover_hp': 7000,
                  'cover_base_physical_protection': 60, 'cover_physical_protection': 60}
    assert client.patch(url, headers=auth_headers(player), json={'properties': properties}).status_code == 403
    assert client.patch(url, headers=headers, json={'properties': {**properties, 'cover_hp': 9000}}).status_code == 400
    response = client.patch(url, headers=headers, json={'properties': properties})
    assert response.status_code == 200, response.get_json()
    profile = CombatService._cover_profile(db.session.get(LocationObject, created['id']))
    assert (profile['hp'], profile['max_hp'], profile['physical_protection']) == (7000, 8500, 60)


def test_atomic_building_edits_isolate_levels_and_clients(client, app, create_user, auth_headers):
    gm, player = create_user('batch-gm'), create_user('batch-player')
    headers = auth_headers(gm)
    lobby = client.post('/lobbies/', headers=headers, json=dict(name='batch', map_type='empty',
        chunks_width=1, chunks_height=1)).get_json()
    api = f"/lobbies/{lobby['id']}"
    client.post(api+'/join', headers=auth_headers(player))
    loc = client.post(api+'/locations', headers=headers, json=dict(name='batch', world_tile_x=1,
        world_tile_z=1, grid_width=16, grid_height=16)).get_json()
    url = f"{api}/locations/{loc['id']}"
    batch = url + '/objects/batch'
    terrain = client.get(url, headers=headers).get_json()['tiles_data']
    from app.sockets.location import handle_join_location
    socketio.on_event('join_location', handle_join_location)
    connections = [socketio.test_client(app), socketio.test_client(app)]
    try:
        for connection, user in zip(connections, [gm, player]):
            connection.emit('join_location', dict(token=user['token'], location_id=loc['id']))
            connection.get_received()
        specs = shell(basements=2)
        assert client.post(batch, headers=auth_headers(player), json={'objects': specs}).status_code == 403
        # Validate the entire batch before adding anything or emitting an event.
        assert client.post(batch, headers=headers, json={'objects': specs + [dict(name='bad', type='wall', tile_x=99, tile_y=0)]}).status_code == 400
        assert not client.get(url, headers=headers).get_json()['objects']
        result = client.post(batch, headers=headers, json={'objects': specs})
        assert result.status_code == 201
        saved = result.get_json()['objects']
        for connection in connections:
            events = connection.get_received()
            assert [e['name'] for e in events] == ['location_objects_changed']
            assert events[0]['args'][0]['objects'] == saved
        # The same building has two storeys and two basements: zero and the
        # intermediate basement cannot be removed, even by a direct API call.
        for level in [0, -1]:
            rejected = client.delete(batch, headers=headers, json={'building_id':'shell', 'level':level})
            assert rejected.status_code == 409
            assert rejected.get_json()['error']
            assert client.get(url, headers=headers).get_json()['objects'] == saved
        # An unrelated building's higher/lower storeys must not define our limits.
        extra_specs = [dict(name='Extra', type='floor', tile_x=14, tile_y=14,
            properties={'building_id':bid,'level':level}) for bid, level in [('neighbor',3),('neighbor',-3),('shell',2)]]
        extra = client.post(batch, headers=headers, json={'objects':extra_specs}).get_json()['objects']
        for connection in connections:
            connection.get_received()
        previous = client.get(url, headers=headers).get_json()['objects']
        assert client.delete(batch, headers=headers, json={'building_id':'shell','level':1}).status_code == 409
        assert client.get(url, headers=headers).get_json()['objects'] == previous
        assert all(not connection.get_received() for connection in connections)
        assert client.delete(batch, headers=headers, json={'building_id':'shell','level':2}).status_code == 200
        # Remove fixture objects through ordinary element editing, then continue
        # testing the basement operation and whole-building deletion below.
        for item in extra:
            if item['properties']['building_id'] == 'neighbor':
                assert client.delete(f"{api}/locations/objects/{item['id']}", headers=headers).status_code == 204
        saved = client.get(url, headers=headers).get_json()['objects']
        for connection in connections:
            connection.get_received()
        lower = next(o for o in saved if o['type'] == 'floor' and o['properties']['level'] == -2)
        upper = next(o for o in saved if o['type'] == 'floor' and o['properties']['level'] == -1)
        # A stale or forged cross-floor eraser operation is rejected atomically.
        assert client.delete(batch, headers=headers, json={'object_ids':[lower['id'], upper['id']], 'level':-2}).status_code == 400
        assert len(client.get(url, headers=headers).get_json()['objects']) == len(saved)
        result = client.delete(batch, headers=headers, json={'object_ids':[lower['id']], 'level':-2})
        assert result.status_code == 200
        assert result.get_json()['deleted_ids'] == [lower['id']]
        assert client.delete(batch, headers=auth_headers(player), json={'building_id':'shell'}).status_code == 403
        for connection in connections:
            assert len(connection.get_received()) == 1
        before = client.get(url, headers=headers).get_json()['objects']
        removed = client.delete(batch, headers=headers, json={'building_id':'shell', 'level':-2}).get_json()['deleted_ids']
        after = client.get(url, headers=headers).get_json()['objects']
        assert {o['id'] for o in after} == {o['id'] for o in before} - set(removed)
        assert {o['id'] for o in before if o['properties']['level'] == -2} <= set(removed)
        # Only the portal belonging to the removed shaft may change on the upper floor.
        assert all(o['type'] in {'floor_opening', 'hatch'} and o['properties']['level'] == -1
                   for o in before if o['id'] in removed and o['properties']['level'] != -2)
        # Keep the neighboring building and terrain, even for an entire-building delete.
        neighbor = client.post(url+'/objects', headers=headers, json=dict(type='floor', name='neighbor',
            tile_x=14, tile_y=14, properties={'building_id':'neighbor'})).get_json()
        actor = client.post(api+'/characters', headers=headers, json={'name':'Resident','data':{}}).get_json()
        client.post(url+'/spawn_character', headers=headers, json={'character_id':actor['id'],'tile_x':3,'tile_y':3})
        assert client.delete(batch, headers=headers, json={'building_id':'shell'}).status_code == 409
        assert len(client.get(url, headers=headers).get_json()['objects']) == len(after)+1
        with app.app_context():
            resident = LocationCharacter.query.filter_by(location_id=loc['id'], character_id=actor['id']).one()
            resident.pos_x = resident.pos_y = 14
            db.session.commit()
        assert client.delete(batch, headers=headers, json={'building_id':'shell'}).status_code == 200
        db.session.remove()
        reloaded = client.get(url, headers=headers).get_json()
        assert [o['id'] for o in reloaded['objects']] == [neighbor['id']]
        assert reloaded['tiles_data'] == terrain
    finally:
        for connection in connections:
            connection.disconnect()
