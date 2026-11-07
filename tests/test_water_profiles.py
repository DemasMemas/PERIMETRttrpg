from copy import deepcopy
import pytest
from app.services.water_profiles import validate_water_profile, apply_water_profile
from app.services.exceptions import ValidationError


def test_profile_validation_and_no_height_changes():
    for invalid in (None, {}, 12, 'oil'):
        with pytest.raises(ValidationError):
            validate_water_profile(invalid)
    tile = {'terrain': 'water', 'height': 1.7}
    apply_water_profile(tile, {'water_profile': 'peat'})
    assert tile == {'terrain': 'water', 'height': 1.7, 'water_profile': 'peat'}
    apply_water_profile(tile, {'terrain': 'grass'})
    assert 'water_profile' not in tile


def test_world_basin_crosses_chunks_and_batch_validation_is_atomic(client, create_user, auth_headers):
    from app.services.map import MapService
    user = create_user()
    headers = auth_headers(user)
    lobby = client.post('/lobbies/', headers=headers, json={'name':'Profiles','map_type':'empty','chunks_width':2,'chunks_height':1}).get_json()
    root = f"/lobbies/{lobby['id']}"
    for x, z in ((31,8),(32,8),(33,8),(2,2)):
        r = client.patch(f'{root}/chunks/{x//32}/0/tile/{x%32}/{z}',headers=headers,json={'terrain':'water','height':1.4})
        assert r.status_code == 200
    before = deepcopy(MapService.get_tile_data(lobby['id'],31,8))
    result = client.post(root+'/water-profile',headers=headers,json={'x':31,'z':8,'profile':'industrial'})
    assert result.status_code == 200 and len(result.get_json()['updates']) == 3
    for x in (31,32,33):
        t = MapService.get_tile_data(lobby['id'],x,8)
        assert t['water_profile'] == 'industrial' and t['height'] == 1.4
    assert 'water_profile' not in MapService.get_tile_data(lobby['id'],2,2)
    bad = client.post(root+'/chunks/batch',headers=headers,json=[
        {'chunk_x':0,'chunk_y':0,'tile_x':31,'tile_y':8,'updates':{'water_profile':'clean'}},
        {'chunk_x':1,'chunk_y':0,'tile_x':0,'tile_y':8,'updates':{'water_profile':'bad'}}])
    assert bad.status_code == 400
    assert MapService.get_tile_data(lobby['id'],31,8)['water_profile'] == 'industrial'
    client.post(root+'/water-profile',headers=headers,json={'x':31,'z':8,'profile':'auto'})
    assert MapService.get_tile_data(lobby['id'],31,8)['water_profile'] == 'auto'
    assert MapService.get_tile_data(lobby['id'],31,8)['height'] == before['height']
    for x, height in ((31, 1.8), (32, .7), (33, 1.3), (2, 2.2)):
        assert client.patch(f'{root}/chunks/{x//32}/0/tile/{x%32}/{8 if x > 30 else 2}',
                            headers=headers, json={'height': height}).status_code == 200
    leveled = client.post(root+'/water-level', headers=headers,
                          json={'x':31,'z':8,'mode':'minimum'})
    assert leveled.status_code == 200 and len(leveled.get_json()['updates']) == 2
    assert [MapService.get_tile_data(lobby['id'],x,8)['height'] for x in (31,32,33)] == [.7]*3
    assert MapService.get_tile_data(lobby['id'],2,2)['height'] == 2.2
    exact = client.post(root+'/water-level', headers=headers,
                        json={'x':32,'z':8,'mode':'exact','height':1.6})
    assert exact.status_code == 200 and len(exact.get_json()['updates']) == 3
    assert [MapService.get_tile_data(lobby['id'],x,8)['height'] for x in (31,32,33)] == [1.6]*3
    invalid = client.post(root+'/water-level', headers=headers,
                          json={'x':32,'z':8,'mode':'exact','height':4})
    assert invalid.status_code == 400
    assert MapService.get_tile_data(lobby['id'],32,8)['height'] == 1.6


def test_local_socket_full_save_and_repaint_preserve_rules(client, app, create_user, auth_headers):
    from app.extensions import db, socketio
    user = create_user(); headers = auth_headers(user)
    lobby = client.post('/lobbies/',headers=headers,json={'name':'Local profiles','map_type':'empty'}).get_json()
    tiles = [[{'terrain':'grass','height':1,'objects':[]} for _ in range(5)] for _ in range(5)]
    tiles[2][2]['terrain'] = 'water'
    result = client.post(f"/lobbies/{lobby['id']}/locations",headers=headers,json={
        'name':'Pond','grid_width':5,'grid_height':5,'world_tile_x':0,'world_tile_z':0,'tiles_data':tiles})
    assert result.status_code == 201
    loc = result.get_json()['id'];url=f"/lobbies/{lobby['id']}/locations/{loc}"
    from app.sockets.location import handle_update_location_tiles
    socketio.on_event('update_location_tiles', handle_update_location_tiles)
    sock = socketio.test_client(app,flask_test_client=client)
    def update(**fields):
        sock.emit('update_location_tiles',{'token':user['token'],'location_id':loc,'updates':[{'x':2,'z':2,**fields}]})
        db.session.expire_all()
    try:
        update(water_profile='peat')
        saved = client.get(url,headers=headers).get_json()['tiles_data']
        assert saved[2][2]['water_profile'] == 'peat' and saved[2][2]['height'] == 1
        saved[2][2].pop('water_profile')
        assert client.put(url,headers=headers,json={'tiles_data':saved}).status_code == 200
        assert client.get(url,headers=headers).get_json()['tiles_data'][2][2]['water_profile'] == 'peat'
        update(water_profile={})
        assert client.get(url,headers=headers).get_json()['tiles_data'][2][2]['water_profile'] == 'peat'
        update(terrain='grass');update(terrain='water')
        assert 'water_profile' not in client.get(url,headers=headers).get_json()['tiles_data'][2][2]
    finally:
        sock.disconnect()
