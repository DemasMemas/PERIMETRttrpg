from copy import deepcopy
import pytest


@pytest.fixture
def editor(client, create_user, auth_headers):
    gm = create_user('history-gm')
    headers = auth_headers(gm)
    lobby = client.post('/lobbies/', headers=headers, json={
        'name': 'History', 'map_type': 'empty', 'chunks_width': 2, 'chunks_height': 2,
    }).get_json()
    base = f"/lobbies/{lobby['id']}/locations"
    location = client.post(base, headers=headers, json={
        'name': 'History scene', 'world_tile_x': 10, 'world_tile_z': 10,
        'grid_width': 5, 'grid_height': 5,
        'tiles_data': [[{'terrain': 'concrete', 'height': 1, 'objects': []} for _ in range(5)] for _ in range(5)],
    }).get_json()
    url = f"{base}/{location['id']}"
    return client, headers, url, lobby['id']


def test_atomic_mixed_group_restore_and_revision_conflict(editor):
    client, headers, url, lobby_id = editor
    spec = {'id': -1, 'revision': 1, 'name': None, 'type': 'box', 'tile_x': 1, 'tile_y': 1,
            'properties': {'rotation': 12.345, 'dimensions': {'width': .375, 'depth': 1, 'height': 1}}}
    decor = {'type': 'rock', 'rotation': 12.345, 'x': .12345, 'scale': .375}
    change = {'objects': [{'id': -1, 'before': None, 'after': spec}],
              'tiles': [{'x': 2, 'z': 2, 'before': [], 'after': [decor]}]}
    response = client.post(url + '/object-edits', headers=headers, json=change)
    assert response.status_code == 200, response.get_json()
    obj = response.get_json()['objects'][0]
    delete = {'objects': [{'id': obj['id'], 'before': obj, 'after': None}],
              'tiles': [{'x': 2, 'z': 2, 'before': [decor], 'after': []}]}
    assert client.post(url + '/object-edits', headers=headers, json=delete).status_code == 200
    restore = {'objects': [{'id': obj['id'], 'before': None, 'after': obj}],
               'tiles': [{'x': 2, 'z': 2, 'before': [], 'after': [decor]}]}
    response = client.post(url + '/object-edits', headers=headers, json=restore)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['objects'][0]['properties'] == obj['properties']
    assert response.get_json()['objects'][0]['revision'] > obj['revision']
    newer = client.patch(f"/lobbies/{lobby_id}/locations/objects/{obj['id']}", headers=headers,
                         json={'properties': {'rotation': 24.69}}).get_json()
    response = client.post(url + '/object-edits', headers=headers, json=delete)
    assert response.status_code == 409
    stored = client.get(url, headers=headers).get_json()
    assert stored['objects'][0] == newer
    assert stored['tiles_data'][2][2]['objects'] == [decor]


def test_decor_conflict_does_not_commit_any_other_changes(editor):
    client, headers, url, _ = editor
    first = {'tiles': [{'x': 2, 'z': 2, 'before': [], 'after': [{'type': 'rock'}]}]}
    assert client.post(url + '/object-edits', headers=headers, json=first).status_code == 200
    stale = {'tiles': [{'x': 1, 'z': 1, 'before': [], 'after': [{'type': 'tree'}]},
                       {'x': 2, 'z': 2, 'before': [], 'after': []}]}
    assert client.post(url + '/object-edits', headers=headers, json=stale).status_code == 409
    assert client.get(url, headers=headers).get_json()['tiles_data'][1][1]['objects'] == []


def test_surface_override_round_trips_without_repainting_and_rejects_non_boolean(editor):
    client, headers, url, _ = editor
    initial = client.get(url, headers=headers).get_json()['tiles_data'][1][1]
    route = url + '/object-edits'
    for setting in (True, False):
        edited = {**initial, 'difficult_surface': setting}
        response = client.post(route, headers=headers, json={'cells': [
            {'x': 1, 'z': 1, 'before': initial, 'after': edited},
        ]})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()['cells'][0]['state'] == edited
        assert client.get(url, headers=headers).get_json()['tiles_data'][1][1] == edited
        undo = client.post(route, headers=headers, json={'cells': [
            {'x': 1, 'z': 1, 'before': edited, 'after': initial},
        ]})
        assert undo.status_code == 200
    rejected = client.post(route, headers=headers, json={'cells': [
        {'x': 1, 'z': 1, 'before': initial, 'after': {**initial, 'difficult_surface': 'false'}},
    ]})
    assert rejected.status_code == 400
    assert client.get(url, headers=headers).get_json()['tiles_data'][1][1] == initial


def test_lamp_history_keeps_other_settings_and_detects_foreign_lamp(editor):
    client, headers, url, _ = editor
    initial = client.get(url, headers=headers).get_json()['lighting']
    lamp = {'x': 2.5, 'z': 2.5, 'height': 2.345, 'color': '#ffffff', 'kind': 'spot',
            'azimuth': 12.345, 'intensity': .375, 'distance': 6,
            'dimensions': {'width': .375, 'depth': .375, 'height': .375}}
    r = client.post(url + '/object-edits', headers=headers,
                    json={'lights': {'before': initial.get('local_lights', []), 'after': [lamp]}})
    assert r.status_code == 200, r.get_json()
    saved = r.get_json()['lighting']
    foreign = deepcopy(saved); foreign['ambient_intensity'] = .25
    assert client.put(url, headers=headers, json={'lighting': foreign}).status_code == 200
    undo = {'lights': {'before': saved['local_lights'], 'after': []}}
    r = client.post(url + '/object-edits', headers=headers, json=undo)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['lighting']['ambient_intensity'] == .25
    assert client.post(url + '/object-edits', headers=headers, json=undo).status_code == 409


def test_history_endpoint_is_gm_only_and_rejects_duplicate_ids(editor, create_user, auth_headers):
    client, headers, url, lobby_id = editor
    player = create_user('history-player')
    client.post(f'/lobbies/{lobby_id}/join', headers=auth_headers(player))
    assert client.post(url + '/object-edits', headers=auth_headers(player), json={}).status_code == 403
    assert client.post(url + '/object-edits', headers=headers,
                       json={'objects': [{'id': 1}, {'id': 1}]}).status_code == 400


def test_mixed_cell_patch_restores_absent_fields_and_precise_state(editor):
    client, headers, url, _ = editor
    old = client.get(url, headers=headers).get_json()['tiles_data'][1][1]
    new = {**old, 'terrain': 'water', 'height': 1.234567, 'radiation': .375,
           'water_profile': 'auto', 'water_level_offset': -.2, 'custom': {'seed': 17},
           'objects': [{'type': 'rock', 'rotation': 12.345}]}
    change = {'cells': [{'x': 1, 'z': 1, 'before': old, 'after': new}]}
    r = client.post(url + '/object-edits', headers=headers, json=change)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['cells'][0]['state'] == new
    assert client.get(url, headers=headers).get_json()['tiles_data'][1][1] == new
    inverse = {'cells': [{'x': 1, 'z': 1, 'before': new, 'after': old}]}
    assert client.post(url + '/object-edits', headers=headers, json=inverse).status_code == 200
    assert client.get(url, headers=headers).get_json()['tiles_data'][1][1] == old
    assert client.post(url + '/object-edits', headers=headers, json=change).status_code == 200


def test_overlapping_cell_conflict_rolls_back_objects_darkness_and_other_cells(editor):
    client, headers, url, _ = editor
    data = client.get(url, headers=headers).get_json()
    before = data['tiles_data'][1][1]
    foreign = {**before, 'radiation': 3.5}
    assert client.post(url + '/object-edits', headers=headers, json={
        'cells': [{'x': 1, 'z': 1, 'before': before, 'after': foreign}],
    }).status_code == 200
    r = client.post(url + '/object-edits', headers=headers, json={
        'cells': [{'x': 0, 'z': 0, 'before': before, 'after': {**before, 'height': 2}},
                  {'x': 1, 'z': 1, 'before': before, 'after': {**before, 'height': 2}}],
        'darkness': [{'x': 3, 'z': 3, 'before': False, 'after': True}],
        'objects': [{'id': -1, 'before': None, 'after': {'name': 'box', 'type': 'box',
                     'tile_x': 0, 'tile_y': 0, 'properties': {}}}],
    })
    assert r.status_code == 409
    stored = client.get(url, headers=headers).get_json()
    assert stored['tiles_data'][0][0] == before
    assert stored['tiles_data'][1][1] == foreign
    assert stored['objects'] == []
    assert stored['lighting']['dark_tiles'] == []


def test_darkness_cells_preserve_disjoint_foreign_paint_and_conflict_atomically(editor):
    client, headers, url, _ = editor
    def paint(x, before, after):
        return {'darkness': [{'x': x, 'z': 2, 'before': before, 'after': after}]}
    for x in (1, 2):
        assert client.post(url + '/object-edits', headers=headers, json=paint(x, False, True)).status_code == 200
    assert client.post(url + '/object-edits', headers=headers, json=paint(1, True, False)).status_code == 200
    assert client.get(url, headers=headers).get_json()['lighting']['dark_tiles'] == [{'x': 2, 'z': 2}]
    r = client.post(url + '/object-edits', headers=headers, json={'darkness': [
        {'x': 1, 'z': 2, 'before': False, 'after': True}, {'x': 2, 'z': 2, 'before': False, 'after': True}]})
    assert r.status_code == 409
    assert client.get(url, headers=headers).get_json()['lighting']['dark_tiles'] == [{'x': 2, 'z': 2}]


def test_environment_history_preserves_lamps_and_rejects_stale_preview(editor):
    client, headers, url, _ = editor
    initial = client.get(url, headers=headers).get_json()['lighting']
    env = {k: v for k, v in initial.items() if k not in {'dark_tiles', 'local_lights'}}
    changed = {**env, 'ambient_intensity': .375, 'directional_azimuth': 12.345}
    change = {'environment': {'before': env, 'after': changed}}
    r = client.post(url + '/object-edits', headers=headers, json=change)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['lighting']['ambient_intensity'] == .375
    assert client.post(url + '/object-edits', headers=headers, json=change).status_code == 409
    r = client.post(url + '/object-edits', headers=headers, json={'environment': {'before': changed, 'after': env}})
    assert r.status_code == 200
    assert r.get_json()['lighting'] == initial


@pytest.mark.parametrize('cell', [None, {'terrain': 'grass', 'height': float('inf')},
                                     {'terrain': 'grass', 'height': 1, 'radiation': ''}])
def test_invalid_cell_patch_is_not_committed(editor, cell):
    client, headers, url, _ = editor
    before = client.get(url, headers=headers).get_json()['tiles_data'][1][1]
    r = client.post(url + '/object-edits', headers=headers,
                    json={'cells': [{'x': 1, 'z': 1, 'before': before, 'after': cell}]})
    assert r.status_code == 400
    assert client.get(url, headers=headers).get_json()['tiles_data'][1][1] == before


def test_paste_dependencies_survive_undo_redo_and_reject_changed_support(editor):
    client, headers, url, lobby_id = editor
    floor = client.post(url + '/objects', headers=headers, json={
        'name': 'floor', 'type': 'floor', 'tile_x': 1, 'tile_y': 1,
        'properties': {'building_id': 'target', 'level': 1, 'dimensions': {'width': 4, 'depth': 4, 'height': .12}},
    }).get_json()
    checks = {'objects': [[floor['id'], floor['revision']]], 'ground': [{'x': 1, 'z': 1, 'height': 1}]}
    spec = {'name': 'copy', 'type': 'box', 'tile_x': 1, 'tile_y': 1,
            'properties': {'building_id': 'target', 'level': 1, 'rotation': 12.345}}
    change = {'objects': [{'id': -1, 'before': None, 'after': spec}], 'checks': checks}
    r = client.post(url + '/object-edits', headers=headers, json=change)
    assert r.status_code == 200, r.get_json()
    obj = r.get_json()['objects'][0]
    assert obj['id'] != floor['id']
    inverse = {'objects': [{'id': obj['id'], 'before': obj, 'after': None}], 'checks': checks}
    assert client.post(url + '/object-edits', headers=headers, json=inverse).status_code == 200
    redo = {'objects': [{'id': obj['id'], 'before': None, 'after': obj}], 'checks': checks}
    assert client.post(url + '/object-edits', headers=headers, json=redo).status_code == 200
    assert client.patch(f"/lobbies/{lobby_id}/locations/objects/{floor['id']}", headers=headers,
                        json={'properties': {'elevation': .375}}).status_code == 200
    assert client.post(url + '/object-edits', headers=headers, json=change).status_code == 409
    assert len(client.get(url, headers=headers).get_json()['objects']) == 2


def test_paste_ground_conflict_has_no_partial_objects_or_lamps(editor):
    client, headers, url, _ = editor
    before = client.get(url, headers=headers).get_json()['tiles_data'][1][1]
    assert client.post(url + '/object-edits', headers=headers, json={
        'cells': [{'x': 1, 'z': 1, 'before': before, 'after': {**before, 'height': 2}}],
    }).status_code == 200
    change = {'checks': {'ground': [{'x': 1, 'z': 1, 'height': 1}]},
              'objects': [{'id': -1, 'before': None, 'after': {'name': 'copy', 'type': 'box',
                           'tile_x': 1, 'tile_y': 1, 'properties': {}}}],
              'lights': {'before': [], 'after': [{'x': 2, 'z': 2, 'height': 2, 'intensity': 1,
                                                'distance': 6, 'color': '#ffffff'}]}}
    assert client.post(url + '/object-edits', headers=headers, json=change).status_code == 409
    stored = client.get(url, headers=headers).get_json()
    assert stored['objects'] == []
    assert stored['lighting']['local_lights'] == []
