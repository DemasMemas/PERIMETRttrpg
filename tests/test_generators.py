"""Заправка, смена батареи и питание станков через игровые маршруты."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import LocationCombatState, LocationObject
from app.services import power_cells
from app.services.crafting import quote
from app.services.liquid_containers import GLASS_BOTTLE, PLASTIC_BOTTLE
from test_grenade_rules import grenade_game


def test_generator_catalog_and_full_canister_runtime():
    from app.services.equipment_catalog import load_catalog
    from app.services.generators import SECONDS_PER_LITER

    template = next(entry for entry in load_catalog() if entry['name'] == power_cells.NAME)
    assert (template['price'], template['weight'], template['volume']) == (750, 0, .2)
    assert not template['attributes'].get('not_for_sale')
    assert 5 * SECONDS_PER_LITER == 24 * 3600
    assert 15 * SECONDS_PER_LITER == 3 * 24 * 3600


@pytest.mark.parametrize('bottle_name', [GLASS_BOTTLE, PLASTIC_BOTTLE])
def test_generator_charges_cells_and_powers_connected_station(client, grenade_game, auth_headers, bottle_name):
    pair = grenade_game
    actor = pair['target_location']
    character = actor.character
    actor.pos_x = actor.pos_y = 4
    LocationCombatState.query.filter_by(location_id=actor.location_id).one().status = 'idle'
    lobby_id = pair['lobby'].id
    location_id = actor.location_id
    generator = LocationObject(location_id=location_id, name='Электрогенератор', type='household_generator',
        tile_x=5, tile_y=4, properties={})
    station = LocationObject(location_id=location_id, name='Станок Электрика', type='household_workbench',
        tile_x=4, tile_y=5, properties={'craft_workbench': 'Станок Электрика'})
    db.session.add_all([generator, station])
    data = deepcopy(character.data)
    data.setdefault('skills', {}).setdefault('other', {})['engineering'] = {'base': 20}
    data['inventory']['pockets'] = [
        {'id': 'bottle', 'name': bottle_name, 'contents': [{'id': 'fuel', 'name': 'Топливо(1 Литр)', 'quantity': 1}]},
        {'id': 'cell', 'name': power_cells.NAME, 'quantity': 1, 'attributes': {'charge_percent': 0}},
        {'id': 'exo', 'name': 'Аккумулятор экзоскелета', 'slotType': 'exoskeleton_battery',
         'attributes': {'charge_days': 10, 'remaining_days': 1}},
        {'id': 'single-use', 'name': 'Батарейки'},
    ]
    character.data = data
    db.session.commit()
    api = f'/lobbies/{lobby_id}'
    url = f'{api}/locations/{location_id}/generators/{generator.id}'
    headers = auth_headers(pair['target_user'])
    forbidden = client.get(f"{url}?character_id={pair['actor_character'].id}", headers=headers)
    assert forbidden.status_code == 403

    def get():
        response = client.get(f'{url}?character_id={character.id}', headers=headers)
        assert response.status_code == 200, response.json
        return response.json

    def post(operation, **payload):
        before = get()
        return client.post(url, headers=headers, json={'operation': operation, 'character_id': character.id,
            'revision': before['revision'], 'character_revision': before['character_revision'], **payload})

    def apply(operation, **payload):
        response = post(operation, **payload)
        assert response.status_code == 200, response.json
        return response.json

    assert post('toggle').status_code == 400
    assert {entry['id'] for entry in get()['inputs']} == {'fuel', 'cell', 'exo'}
    assert post('load', path=['inventory', 'pockets', 3], item_id='single-use').status_code >= 400
    for amount, duration in ((.5, 8640), (.5, 17280)):
        snapshot = apply('refuel', path=['inventory', 'pockets', 0, 'contents', 0], item_id='fuel', liters=amount)
        assert snapshot['fuel_seconds'] == duration
    bottle = character.data['inventory']['pockets'][0]
    assert bottle['contents'] == [] and bottle['fuelUsed']
    apply('load', path=['inventory', 'pockets', 1], item_id='cell')
    apply('load', path=['inventory', 'pockets', 1], item_id='exo')
    snapshot = apply('connect', station_id=station.id, station_revision=station.revision)
    assert snapshot['stations'][0]['linked'] and not snapshot['stations'][0]['powered']
    from app.services.workbenches import NAMES
    extra = [LocationObject(location_id=location_id, name=name, type='household_workbench',
        tile_x=5, tile_y=5, properties={'craft_workbench': name}) for name in (*NAMES[2:5], NAMES[2])]
    db.session.add_all(extra)
    db.session.commit()
    for other in extra[:3]:
        snapshot = apply('connect', station_id=other.id, station_revision=other.revision)
    assert snapshot['connected_stations'] == snapshot['connection_limit'] == 4
    assert post('connect', station_id=extra[3].id, station_revision=extra[3].revision).status_code == 400
    apply('disconnect', station_id=extra[2].id, station_revision=extra[2].revision)
    assert apply('connect', station_id=extra[3].id, station_revision=extra[3].revision)['connected_stations'] == 4
    recipe = {'recipe_id': 'recipe-f7'}
    before = quote(character, character.data, recipe)
    assert before['generator_difficulty_bonus'] == 0
    assert apply('toggle')['stations'][0]['powered']
    powered = quote(character, character.data, recipe)
    assert powered['difficulty'] == before['difficulty'] - 10
    assert powered['generator_difficulty_bonus'] == 10
    gm_headers = auth_headers(pair['actor_user'])
    lobby = pair['lobby']
    absolute_minutes = (lobby.game_day or 1) * 1440 + (lobby.game_time_minutes or 0)
    for delta, expected_fuel, cell_charge, exo_charge in ((30, 15480, 50, 22.5),
            (60, 13680, 100, 35), (240, 2880, 100, 100), (288, 0, 100, 100)):
        day, minute = divmod(absolute_minutes + delta, 1440)
        response = client.patch(f'{api}/time', headers=gm_headers, json={'game_day': day, 'game_time_minutes': minute})
        assert response.status_code == 200, response.json
        snapshot = get()
        assert snapshot['fuel_seconds'] == expected_fuel
        assert snapshot['batteries'][0]['charge'] == cell_charge
        assert snapshot['batteries'][1]['charge'] == exo_charge
    assert not snapshot['on'] and not snapshot['stations'][0]['powered']
    assert quote(character, character.data, recipe)['difficulty'] == before['difficulty']
    apply('unload', item_id='cell')
    apply('unload', item_id='exo')
    returned = {item['id']: item for item in character.data['inventory']['pockets']}
    assert set(returned) == {'bottle', 'cell', 'exo', 'single-use'}
    assert returned['cell']['attributes']['charge_percent'] == 100
    assert returned['exo']['attributes']['remaining_days'] == 10


def test_rechargeable_cell_returns_to_inventory_after_dosimeter_drains():
    from app.services.electronics import DOSIMETER_SECONDS
    from app.services.portable_lights import advance

    dosimeter = {'id': 'dose', 'name': 'Дозиметр'}
    cell = {'id': 'cell', 'name': power_cells.NAME, 'attributes': {'charge_percent': 20}}
    data = {'inventory': {'pockets': [dosimeter, cell]}}
    charge = power_cells.swap(data, dosimeter, ['inventory', 'pockets', 1], 0)
    assert charge == 20 and dosimeter['rechargeableBattery']['id'] == 'cell'
    dosimeter['deviceState'] = {'on': True, 'remaining_seconds': charge * DOSIMETER_SECONDS / 100}
    assert advance(data, DOSIMETER_SECONDS)
    assert dosimeter['deviceState'] == {'on': False, 'remaining_seconds': 0}
    assert 'rechargeableBattery' not in dosimeter
    assert data['inventory']['pockets'][1]['id'] == 'cell'
    assert power_cells.percentage(data['inventory']['pockets'][1]) == 0
