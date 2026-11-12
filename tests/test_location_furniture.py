import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.extensions import db, socketio
from app.models import LocationObject, LocationCharacter
from app.services.combat import CombatService
from app.services.location_navigation import find_path


def furniture(**options):
    result = subprocess.run(['node', 'tests/location_furniture_fixture.cjs'], input=json.dumps(options),
                            cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True, encoding='utf-8')
    return json.loads(result.stdout)


def room_cell_is_free(target, maps):
    x, y, level = target
    blocked, climb_costs = maps[level]
    return (x, y) not in blocked and not climb_costs.get((x, y))


@pytest.mark.parametrize('shape', ['rectangle', 'L', 'U', 'C', 'T', 'cross', 'compound'])
@pytest.mark.parametrize('condition', ['intact', 'damaged', 'ruins'])
def test_furniture_keeps_room_routes_and_basement_ladders(app, shape, condition):
    data = furniture(shellOptions={'shape': shape, 'condition': condition, 'wealth': 'rich'})
    original = [SimpleNamespace(**o) for o in data['source']]
    for o in original:
        if o.type == 'hatch': o.properties['is_open'] = True
    additions = [SimpleNamespace(id=10000+i, **o) for i, o in enumerate(data['plan']['objects'])]
    location = SimpleNamespace(id=1, grid_width=18, grid_height=18,
                               tiles_data=[[{'height': 1} for _ in range(18)] for _ in range(18)])
    with patch.object(LocationObject, 'query') as oq, patch.object(LocationCharacter, 'query') as cq:
        cq.filter_by.return_value.all.return_value = []
        oq.filter_by.return_value.all.return_value = original
        old_maps = {level: CombatService._build_movement_map(location, floor_level=level) for level in [-1, 0, 1]}
        oq.filter_by.return_value.all.return_value = original+additions
        new_maps = {level: CombatService._build_movement_map(location, floor_level=level) for level in [-1, 0, 1]}
        entry = next(o for o in original if o.type == 'door' and o.properties.get('building_generation'))
        anchor = (entry.tile_x, entry.tile_y+1)
        for room in data['rooms']:
            level = room['level']
            cx = sum(c['x'] for c in room['cells'])/len(room['cells'])
            cz = sum(c['z'] for c in room['cells'])/len(room['cells'])
            start = (*anchor, level)
            candidates = sorted(
                ((c['x'], c['z'], level) for c in room['cells']),
                key=lambda cell: (cell[0] - cx)**2 + (cell[1] - cz)**2,
            )
            reachable_before = (
                target for target in candidates
                if room_cell_is_free(target, old_maps)
                and find_path(location, original, start, target, old_maps.__getitem__)
            )
            first_target = next(reachable_before, None)
            if first_target is None:
                continue
            # Мебель может занять ближайшую к центру клетку. Проверяем проход
            # к ближайшей из тех, куда можно было дойти и где ещё можно встать.
            target = first_target if room_cell_is_free(first_target, new_maps) else next(
                (cell for cell in reachable_before if room_cell_is_free(cell, new_maps)), None,
            )
            assert target is not None, (shape, condition, room['id'], 'В комнате не осталось свободной цели')
            assert find_path(location, original + additions, start, target, new_maps.__getitem__), (
                shape, condition, room['id'], start, target,
            )
        for stair in (o for o in original if o.type == 'ladder'):
            start = (*anchor, stair.properties['level'])
            end = (*anchor, stair.properties['level']+1)
            if find_path(location, original, start, end, lambda l: old_maps[l]):
                assert find_path(location, original+additions, start, end, lambda l: new_maps[l])


def test_furniture_scopes_manual_edits_atomicity_permissions_and_socket_roundtrip(client, app, create_user, auth_headers):
    gm, player = create_user('furniture-gm'), create_user('furniture-player')
    headers = auth_headers(gm)
    lobby = client.post('/lobbies/', headers=headers, json=dict(name='furniture', map_type='empty', chunks_width=1, chunks_height=1)).get_json()
    api = f"/lobbies/{lobby['id']}"
    client.post(api+'/join', headers=auth_headers(player))
    loc = client.post(api+'/locations', headers=headers, json=dict(name='furniture', world_tile_x=1, world_tile_z=1, grid_width=18, grid_height=18)).get_json()
    url = f"{api}/locations/{loc['id']}"
    source = furniture()['source']
    specs = [{k: v for k, v in o.items() if k not in {'id', 'revision'}} for o in source]
    architecture = client.post(url+'/objects/batch', headers=headers, json={'objects': specs}).get_json()['objects']
    current = lambda: client.get(url, headers=headers).get_json()['objects']
    plan = lambda opts=None: furniture(source=current(), options=opts or {})['plan']
    payload = lambda p: {'objects': p['objects'], 'replace_furniture': p['replacement']}
    initial = plan()
    assert initial['objects']
    assert client.post(url+'/objects/batch', headers=auth_headers(player), json=payload(initial)).status_code == 403
    invalid = json.loads(json.dumps(payload(initial)))
    invalid['objects'][0]['type'] = 'wall'
    assert client.post(url+'/objects/batch', headers=headers, json=invalid).status_code == 400
    target = initial['objects'][0]
    cell = next(iter(CombatService._object_footprint_tiles(target)))
    with patch.object(LocationCharacter, 'query') as actors:
        actors.filter_by.return_value.all.return_value = [SimpleNamespace(pos_x=cell[0], pos_y=cell[1], floor_level=target['properties']['level'])]
        assert client.post(url+'/objects/batch', headers=headers, json=payload(initial)).status_code == 409
    from app.sockets.location import handle_join_location
    socketio.on_event('join_location', handle_join_location)
    peers = [socketio.test_client(app), socketio.test_client(app)]
    try:
        for peer, user in zip(peers, [gm, player]):
            peer.emit('join_location', dict(token=user['token'], location_id=loc['id']))
            peer.get_received()
        response = client.post(url+'/objects/batch', headers=headers, json=payload(initial))
        assert response.status_code == 201
        generated = response.get_json()['objects']
        for peer in peers:
            events = [e for e in peer.get_received() if e['name'] == 'location_objects_changed']
            assert len(events) == 1
            assert events[0]['args'][0]['objects'] == generated
        assert client.post(url+'/objects/batch', headers=headers, json=payload(initial)).status_code == 409
        moved = generated[0]
        assert client.patch(f"{api}/locations/objects/{moved['id']}", headers=headers,
                            json={'properties': {'offset_x': moved['properties']['offset_x']+.15}}).status_code == 200
        filled = next(o for o in generated if 'contents' in o['properties'])
        assert client.patch(f"{api}/locations/objects/{filled['id']}", headers=headers,
                            json={'revision': filled['revision'], 'properties': {'contents': [{'name': 'Фонарь', 'quantity': 1, 'instance_id': 'furniture-keepsake'}]}}).status_code == 200
        manual = json.loads(json.dumps(generated[1]))
        for k in ('id', 'revision'):
            manual.pop(k)
        manual['properties'].pop('furniture_generation')
        manual = client.post(url+'/objects', headers=headers, json=manual).get_json()
        for scope in ['room', 'floor', 'building']:
            floor_item = next(o for o in generated if o['properties']['level'] == 0)
            opts = {'scope': scope, 'level': 0, 'roomId': floor_item['properties']['furniture_generation']['room_id'], 'seed': scope}
            proposed = plan(opts)
            assert not {moved['id'], filled['id'], manual['id']} & set(proposed['replacedIds'])
            before = current()
            # Manual changes after a preview cause a clean conflict, with no deletion.
            if scope == 'room':
                assert client.patch(f"{api}/locations/objects/{manual['id']}", headers=headers, json={'properties': {'color': '#746554'}}).status_code == 200
                assert client.post(url+'/objects/batch', headers=headers, json=payload(proposed)).status_code == 409
                proposed = plan(opts)
                before = current()
            result = client.post(url+'/objects/batch', headers=headers, json=payload(proposed))
            assert result.status_code == 201, result.get_json()
            deleted = set(result.get_json()['deleted_ids'])
            after = current()
            assert {o['id'] for o in after} == {o['id'] for o in before if o['id'] not in deleted} | {o['id'] for o in result.get_json()['objects']}
            assert [o for o in after if o['id'] in {a['id'] for a in architecture}] == architecture
            for peer in peers:
                events = [e for e in peer.get_received() if e['name'] == 'location_objects_changed']
                assert len(events) == 1
                assert set(events[0]['args'][0]['deleted_ids']) == deleted
        cleared = plan({'scope': 'building', 'empty': True})
        assert not cleared['objects']
        assert client.post(url+'/objects/batch', headers=headers, json=payload(cleared)).status_code == 201
        db.session.remove()
        saved = current()
        assert {o['id'] for o in saved} == {o['id'] for o in architecture} | {moved['id'], filled['id'], manual['id']}
        assert next(o for o in saved if o['id'] == filled['id'])['properties']['contents'][0]['name'] == 'Фонарь'
    finally:
        for peer in peers:
            peer.disconnect()


@pytest.mark.parametrize('shape', ['rectangle', 'U', 'cross'])
@pytest.mark.parametrize('wealth,condition', [('rich', 'damaged'), ('poor', 'ruins'), ('poor', 'intact')])
def test_large_six_level_buildings_keep_real_routes_after_furnishing(app, shape, wealth, condition):
    data = furniture(shellOptions={'shape': shape, 'wealth': wealth, 'condition': condition,
                                   'width': 16, 'depth': 16, 'storeys': 4, 'basements': 2})
    original = [SimpleNamespace(**o) for o in data['source']]
    for o in original:
        if o.type == 'hatch': o.properties['is_open'] = True
    complete = original+[SimpleNamespace(id=10000+i, **o) for i, o in enumerate(data['plan']['objects'])]
    location = SimpleNamespace(id=1, grid_width=24, grid_height=24,
                               tiles_data=[[{'height': 1} for _ in range(24)] for _ in range(24)])
    entry = next(o for o in original if o.type == 'door' and o.properties.get('building_generation'))
    anchor = (entry.tile_x, entry.tile_y+1)
    with patch.object(LocationObject, 'query') as oq, patch.object(LocationCharacter, 'query') as cq:
        cq.filter_by.return_value.all.return_value = []
        oq.filter_by.return_value.all.return_value = original
        before = {level: CombatService._build_movement_map(location, floor_level=level) for level in range(-2, 4)}
        oq.filter_by.return_value.all.return_value = complete
        after = {level: CombatService._build_movement_map(location, floor_level=level) for level in range(-2, 4)}
        assert find_path(location, complete, (*anchor, -2), (*anchor, 0), lambda l: after[l])
        for level in range(-2, 3):
            a, b = (*anchor, level), (*anchor, level+1)
            expected = bool(find_path(location, original, a, b, lambda l: before[l]))
            assert bool(find_path(location, complete, a, b, lambda l: after[l])) == expected
