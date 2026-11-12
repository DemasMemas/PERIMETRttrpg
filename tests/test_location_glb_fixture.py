import json
import struct
from pathlib import Path

from scripts.generate_test_crate_glb import build_glb
from app.extensions import db
from app.models import Location


ROOT = Path(__file__).resolve().parents[1]


def test_crate_glb_is_deterministic_and_valid_container():
    data = (ROOT / 'app/static/models/test-crate.glb').read_bytes()
    assert data == build_glb()
    magic, version, total = struct.unpack_from('<4sII', data)
    assert (magic, version, total) == (b'glTF', 2, len(data))
    json_size, json_type = struct.unpack_from('<I4s', data, 12)
    assert json_type == b'JSON'
    description = json.loads(data[20:20 + json_size])
    binary_offset = 20 + json_size
    binary_size, binary_type = struct.unpack_from('<I4s', data, binary_offset)
    assert binary_type == b'BIN\x00'
    assert binary_offset + 8 + binary_size == total
    assert description['buffers'][0]['byteLength'] == binary_size
    assert len(description['meshes'][0]['primitives']) == 2
    assert len(description['materials']) == 2
    triangles = sum(description['accessors'][primitive['indices']]['count'] // 3
                    for primitive in description['meshes'][0]['primitives'])
    assert triangles == 192
    for view in description['bufferViews']:
        assert view['byteOffset'] + view['byteLength'] <= binary_size


def test_box_catalog_references_test_glb():
    catalog = (ROOT / 'app/static/js/locationAssetCatalog.js').read_text(encoding='utf-8')
    assert "id: 'box'" in catalog
    assert "modelPath: '/static/models/test-crate.glb'" in catalog


def test_crate_glb_is_served_by_existing_static_route(client):
    response = client.get('/static/models/test-crate.glb')
    assert response.status_code == 200
    assert response.data == build_glb()


def test_swamp_bank_preset_saves_editable_assets_and_atmosphere(client, create_user, auth_headers):
    headers = auth_headers(create_user('swamp-bank-gm'))
    lobby = client.post('/lobbies/', headers=headers, json={
        'name': 'Болотный берег', 'map_type': 'empty', 'chunks_width': 2, 'chunks_height': 2,
    }).get_json()
    url = f"/lobbies/{lobby['id']}/locations"
    payload = {'name': 'Болотный берег', 'preset': 'swamp_bank', 'world_tile_x': 12, 'world_tile_z': 7}
    created = client.post(url, headers=headers, json=payload)
    assert created.status_code == 201
    scene = client.get(f"{url}/{created.get_json()['id']}", headers=headers).get_json()

    assert (scene['grid_width'], scene['grid_height']) == (42, 42)
    assert scene['lighting']['water_reflections'] is True
    assert scene['lighting']['fog_density'] > 0
    assert scene['tiles_data'][20][26]['water_profile'] == 'peat'
    assert scene['lighting']['natural_detail'] is False
    assert any(tile['terrain'] == 'shallow_water' for row in scene['tiles_data'] for tile in row)
    assert all(scene['tiles_data'][point['y']][point['x']]['terrain'] != 'water'
               for point in scene['spawn_points'])
    assert any(obj['type'].startswith('swamp_shrub_') and obj['properties']['passable']
               for obj in scene['objects'])
    assert any(obj['type'].startswith('swamp_dead_tree_')
               and obj['properties']['traversal_category'] == 'blocked' for obj in scene['objects'])
    assert client.post(url, headers=headers, json=payload).status_code == 409
    assert len(client.get(url, headers=headers).get_json()) == 1


def test_existing_box_save_edit_reload_and_delete(client, create_user, auth_headers):
    gm = create_user('glb-fixture-gm')
    headers = auth_headers(gm)
    lobby = client.post('/lobbies/', headers=headers, json={
        'name': 'GLB fixture', 'map_type': 'empty', 'chunks_width': 2, 'chunks_height': 2,
    })
    assert lobby.status_code == 201
    lobby_id = lobby.get_json()['id']
    location = Location(lobby_id=lobby_id, name='Asset test', grid_width=5, grid_height=5,
                        world_tile_x=0, world_tile_z=0,
                        tiles_data=[[{'terrain': 'grass', 'height': 1, 'objects': []}
                                     for _ in range(5)] for _ in range(5)])
    db.session.add(location)
    db.session.commit()
    detail_url = f'/lobbies/{lobby_id}/locations/{location.id}'

    created = client.post(f'{detail_url}/objects', headers=headers, json={
        'name': 'Crate', 'type': 'box', 'tile_x': 1, 'tile_y': 2,
        'properties': {'dimensions': {'width': 0.8, 'depth': 0.8, 'height': 0.7},
                       'rotation': 0, 'gm_hidden': True},
    })
    assert created.status_code == 201
    object_id = created.get_json()['id']
    assert 'modelPath' not in str(created.get_json())
    edited = client.patch(f'/lobbies/{lobby_id}/locations/objects/{object_id}',
                          headers=headers, json={'tile_x': 3,
                                                 'properties': {'rotation': 1.2}})
    assert edited.status_code == 200
    detail = client.get(detail_url, headers=headers)
    assert detail.status_code == 200
    stored = next(item for item in detail.get_json()['objects'] if item['id'] == object_id)
    assert stored['type'] == 'box'
    assert stored['tile_x'] == 3
    assert stored['properties']['rotation'] == 1.2
    assert stored['properties']['gm_hidden'] is True

    artistic = client.post(f'{detail_url}/objects', headers=headers, json={
        'name': 'Art crate', 'type': 'box', 'tile_x': 2, 'tile_y': 1,
        'properties': {'dimensions': {'width': 0.8, 'depth': 0.8, 'height': 0.7},
                       'visual_asset': 'art_wooden_crate', 'contents': []},
    })
    assert artistic.status_code == 201
    art_id = artistic.get_json()['id']
    detail = client.get(detail_url, headers=headers).get_json()
    saved_art = next(item for item in detail['objects'] if item['id'] == art_id)
    assert (saved_art['type'], saved_art['properties']['visual_asset']) == (
        'box', 'art_wooden_crate')

    deleted = client.delete(f'/lobbies/{lobby_id}/locations/objects/{object_id}', headers=headers)
    assert deleted.status_code == 204
    remaining = client.get(detail_url, headers=headers).get_json()['objects']
    assert [item['id'] for item in remaining] == [art_id]
    assert client.delete(f'/lobbies/{lobby_id}/locations/objects/{art_id}', headers=headers).status_code == 204
    assert not client.get(detail_url, headers=headers).get_json()['objects']
