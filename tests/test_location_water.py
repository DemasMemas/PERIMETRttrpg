from copy import deepcopy
from app.services.location_water import freeze_water_offsets


def pond():
    tiles = [[{'terrain': 'sand', 'height': 1, 'objects': []} for _ in range(5)] for _ in range(5)]
    tiles[1][1]['terrain'] = 'water'
    return tiles


def test_legacy_offset_is_frozen_once_without_changing_heights():
    tiles = pond()
    heights = [[t['height'] for t in row] for row in tiles]
    assert freeze_water_offsets(tiles)
    assert tiles[1][1]['water_level_offset'] == -.2
    for row in tiles:
        for t in row:
            if t['terrain'] == 'sand':
                t['terrain'] = 'grass'
    assert not freeze_water_offsets(tiles)
    assert tiles[1][1]['water_level_offset'] == -.2
    assert heights == [[t['height'] for t in row] for row in tiles]


def test_explicit_new_water_level_and_old_mixed_pond_are_preserved():
    tiles = pond()
    tiles[1][1]['water_level_offset'] = 0
    snapshot = deepcopy(tiles)
    assert not freeze_water_offsets(tiles)
    assert tiles == snapshot
    del tiles[1][1]['water_level_offset']
    tiles[0][1]['terrain'] = tiles[1][0]['terrain'] = 'grass'
    freeze_water_offsets(tiles)
    assert tiles[1][1]['water_level_offset'] == 0


def test_get_migrates_legacy_map_and_socket_preserves_offset(client, app, create_user, auth_headers):
    from app.extensions import db, socketio
    from app.models.location import Location
    user = create_user()
    headers = auth_headers(user)
    lobby = client.post('/lobbies/', headers=headers, json={'name': 'Water compatibility', 'map_type': 'empty'}).get_json()
    created = client.post(f"/lobbies/{lobby['id']}/locations", headers=headers,
                          json={'name': 'Legacy pond', 'grid_width': 5, 'grid_height': 5,
                                'world_tile_x': 0, 'world_tile_z': 0, 'tiles_data': pond()})
    assert created.status_code == 201
    loc_id = created.get_json()['id']
    # Simulate an existing DB save predating the optional field.
    loc = db.session.get(Location, loc_id)
    loc.tiles_data = pond()
    db.session.commit()
    url = f"/lobbies/{lobby['id']}/locations/{loc_id}"
    first = client.get(url, headers=headers).get_json()
    assert first['tiles_data'][1][1]['water_level_offset'] == -.2
    assert first['tiles_data'][1][1]['height'] == 1
    socket = socketio.test_client(app, flask_test_client=client)
    try:
        socket.emit('update_location_tiles', {'token': user['token'], 'location_id': loc_id,
                                            'updates': [{'x': 1, 'z': 0, 'terrain': 'grass'}, {'x': 0, 'z': 1, 'terrain': 'grass'}]})
        db.session.expire_all()
        after = client.get(url, headers=headers).get_json()
        assert after['tiles_data'][0][1]['terrain'] == 'grass'
        assert after['tiles_data'][1][0]['terrain'] == 'grass'
        assert after['tiles_data'][1][1] == first['tiles_data'][1][1]
        old_editor = deepcopy(after['tiles_data'])
        old_editor[1][1].pop('water_level_offset')
        assert client.put(url, headers=headers, json={'tiles_data': old_editor}).status_code == 200
        assert client.get(url, headers=headers).get_json()['tiles_data'][1][1]['water_level_offset'] == -.2
        socket.emit('update_location_tiles', {'token': user['token'], 'location_id': loc_id,
                                            'updates': [{'x': 0, 'z': 0, 'terrain': 'water', 'height': .7, 'water_level_offset': 0}]})
        db.session.expire_all()
        new = client.get(url, headers=headers).get_json()['tiles_data'][0][0]
        assert new['water_level_offset'] == 0 and new['height'] == .7
    finally:
        socket.disconnect()
