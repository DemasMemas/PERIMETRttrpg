"""Платная установка модификаций через взаимодействие с НПС."""
from copy import deepcopy

from app.extensions import db
from app.models import LocationCombatState
from test_npc_trade import shop, configure


def request(client, case, auth_headers, **changes):
    payload = {'actor_location_character_id': case['actor_location'].id,
        'specialist_character_id': case['target_character'].id,
        'target_path': ['weapons', 0], 'item_id': 'service-weapon',
        'rule_id': 'weapon-c23', **changes}
    return client.post(f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/professional-modification",
        headers=auth_headers(case['actor_user']), json=payload)


def test_paid_npc_installation_requires_no_player_book_tools_or_roll(client, shop, auth_headers):
    LocationCombatState.query.filter_by(location_id=shop['location'].id).one().status = 'inactive'
    data = deepcopy(shop['actor_character'].data)
    data['inventory']['money'] = 10000
    data['weapons'] = [{'id':'service-weapon','name':'Пистолет','category':'weapon',
        'subcategory':'Пистолеты','itemClass':'1','ergonomics':20,'modifications':[]}]
    data.pop('crafting', None)
    shop['actor_character'].data = data
    db.session.commit()
    configured = configure(client, shop, auth_headers, modification_enabled=True, repair_markup=1.2)
    assert configured.status_code == 200, configured.json
    quoted = request(client, shop, auth_headers)
    assert quoted.status_code == 200, quoted.json
    assert quoted.json['price'] == 6000
    result = request(client, shop, auth_headers, execute=True, operation_id='install-one',
        actor_revision=shop['actor_character'].revision,
        specialist_revision=shop['target_character'].revision)
    assert result.status_code == 200, result.json
    assert shop['actor_character'].data['inventory']['money'] == 4000
    assert shop['target_character'].data['inventory']['money'] == 6500
    assert shop['actor_character'].data['weapons'][0]['modifications'][0]['ruleId'] == 'weapon-c23'
    replay = request(client, shop, auth_headers, execute=True, operation_id='install-one',
        actor_revision=0, specialist_revision=0)
    assert replay.status_code == 200 and replay.json['duplicate']
    assert shop['actor_character'].data['inventory']['money'] == 4000


def test_only_adjacent_mechanic_opens_slot_once_for_exact_price(client, shop, auth_headers):
    LocationCombatState.query.filter_by(location_id=shop['location'].id).one().status = 'inactive'
    data = deepcopy(shop['actor_character'].data)
    data['inventory']['money'] = 5000
    data['weapons'] = [{'id':'service-weapon','name':'Пистолет','category':'weapon',
        'subcategory':'Пистолеты','itemClass':'1','modifications':[]}]
    shop['actor_character'].data = data
    db.session.commit()
    args = {'action':'unlock_slot', 'rule_id':None}
    assert request(client, shop, auth_headers, **args).status_code == 400
    configured = configure(client, shop, auth_headers, repair_enabled=True,
        modification_enabled=False, repair_markup=1.8)
    assert configured.status_code == 200, configured.json
    quoted = request(client, shop, auth_headers, **args)
    assert quoted.status_code == 200 and quoted.json['price'] == 2500
    result = request(client, shop, auth_headers, **args, execute=True, operation_id='open-slot',
        actor_revision=shop['actor_character'].revision,
        specialist_revision=shop['target_character'].revision)
    assert result.status_code == 200, result.json
    assert result.json['slots']['available'] == 2
    assert shop['actor_character'].data['inventory']['money'] == 2500
    assert shop['target_character'].data['inventory']['money'] == 3000
    replay = request(client, shop, auth_headers, **args, execute=True, operation_id='open-slot',
        actor_revision=0, specialist_revision=0)
    assert replay.status_code == 200 and replay.json['duplicate']
    assert shop['actor_character'].data['inventory']['money'] == 2500
    assert request(client, shop, auth_headers, **args, item_id='other').status_code == 409
    data = deepcopy(shop['actor_character'].data)
    data['inventory']['money'] = 2499
    shop['actor_character'].data = data
    db.session.commit()
    assert request(client, shop, auth_headers, **args, execute=True, operation_id='no-money',
        actor_revision=shop['actor_character'].revision,
        specialist_revision=shop['target_character'].revision).status_code == 400
    assert shop['actor_character'].data['inventory']['money'] == 2499
    assert shop['actor_character'].data['weapons'][0]['unlockedModificationSlots'] == 1


def test_slot_service_rejects_distant_or_unqualified_npc(client, shop, auth_headers):
    LocationCombatState.query.filter_by(location_id=shop['location'].id).one().status = 'inactive'
    data = deepcopy(shop['actor_character'].data)
    data['weapons'] = [{'id':'service-weapon','name':'Пистолет','category':'weapon',
        'subcategory':'Пистолеты','itemClass':'1','modifications':[]}]
    shop['actor_character'].data = data
    db.session.commit()
    args = {'action':'unlock_slot', 'rule_id':None}
    assert configure(client, shop, auth_headers, repair_enabled=True).status_code == 200
    shop['target_location'].pos_x = 10
    db.session.commit()
    assert request(client, shop, auth_headers, **args).status_code == 400


def test_paid_installation_rejects_distant_specialist(client, shop, auth_headers):
    LocationCombatState.query.filter_by(location_id=shop['location'].id).one().status = 'inactive'
    shop['target_location'].pos_x = 10
    db.session.commit()
    response = request(client, shop, auth_headers)
    assert response.status_code == 400


def test_paid_installation_requires_service_and_funds(client, shop, auth_headers):
    LocationCombatState.query.filter_by(location_id=shop['location'].id).one().status = 'inactive'
    data = deepcopy(shop['actor_character'].data)
    data['inventory']['money'] = 1000
    data['weapons'] = [{'id':'service-weapon','name':'Пистолет','category':'weapon',
        'subcategory':'Пистолеты','itemClass':'1','ergonomics':20,'modifications':[]}]
    shop['actor_character'].data = data
    db.session.commit()
    assert request(client, shop, auth_headers).status_code == 400
    assert configure(client, shop, auth_headers, modification_enabled=True, repair_markup=1.2).status_code == 200
    result = request(client, shop, auth_headers, execute=True, operation_id='no-funds',
        actor_revision=shop['actor_character'].revision,
        specialist_revision=shop['target_character'].revision)
    assert result.status_code == 400
    assert shop['actor_character'].data['inventory']['money'] == 1000
    assert shop['target_character'].data['inventory']['money'] == 500


def test_paid_installation_rounds_price_down_and_rejects_stale_sheet(client, shop, auth_headers):
    LocationCombatState.query.filter_by(location_id=shop['location'].id).one().status = 'inactive'
    data = deepcopy(shop['actor_character'].data)
    data['inventory']['money'] = 10000
    data['weapons'] = [{'id':'service-weapon','name':'Пистолет','category':'weapon',
        'subcategory':'Пистолеты','itemClass':'1','ergonomics':20,'modifications':[]}]
    shop['actor_character'].data = data
    db.session.commit()
    assert configure(client, shop, auth_headers, modification_enabled=True, repair_markup=.3333).status_code == 200
    quote = request(client, shop, auth_headers)
    assert quote.status_code == 200 and quote.json['price'] == 1666
    result = request(client, shop, auth_headers, execute=True, operation_id='stale-sheet',
        actor_revision=0, specialist_revision=shop['target_character'].revision)
    assert result.status_code == 409
    assert shop['actor_character'].data['inventory']['money'] == 10000


def test_scientist_physical_exoskeleton_price_and_service_role(client, shop, auth_headers):
    LocationCombatState.query.filter_by(location_id=shop['location'].id).one().status = 'inactive'
    data = deepcopy(shop['actor_character'].data)
    data['inventory']['money'] = 50000
    data['equipment'] = {'armor': {'id':'service-exo','name':'Экзоскелет','category':'armor',
        'itemClass':'6','attributes':{'modification_category':'Экзоскелет'},'modifications':[]}}
    shop['actor_character'].data = data
    db.session.commit()
    configured = configure(client, shop, auth_headers, scientist_enabled=True,
                           modification_enabled=False, repair_markup=.5)
    assert configured.status_code == 200, configured.json
    args = {'target_path':['equipment','armor'], 'item_id':'service-exo', 'rule_id':'armor-d18'}
    quote = request(client, shop, auth_headers, **args)
    assert quote.status_code == 200, quote.json
    assert quote.json['base_price'] == 45000 and quote.json['price'] == 22500
    done = request(client, shop, auth_headers, **args, execute=True, operation_id='exo-service',
        actor_revision=shop['actor_character'].revision,
        specialist_revision=shop['target_character'].revision)
    assert done.status_code == 200, done.json
    assert shop['actor_character'].data['inventory']['money'] == 27500
    assert shop['actor_character'].data['equipment']['armor']['modifications'][0]['ruleId'] == 'armor-d18'
    ordinary = request(client, shop, auth_headers, target_path=['equipment','armor'],
        item_id='service-exo', rule_id='armor-d24')
    assert ordinary.status_code == 400
