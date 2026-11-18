"""Торговля по прайсу ГМа: реальные запросы, склад и деньги обеих сторон."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import CharacterInteractionRequest
from app.models.templates import ItemTemplate
from test_character_interactions import _setup_pair


@pytest.fixture
def shop(client, create_user, auth_headers):
    pair = _setup_pair(client, create_user, auth_headers)
    water = ItemTemplate(name='Вода', category='consumable', price=75, attributes={})
    bandage = ItemTemplate(name='Бинт', category='consumable', price=50, attributes={})
    db.session.add_all([water, bandage])
    db.session.flush()
    for key, balance, template in [('actor_character', 1000, bandage), ('target_character', 500, water)]:
        data = deepcopy(pair[key].data)
        data['inventory']['money'] = balance
        data['inventory']['backpack'][0]['templateId'] = template.id
        if key == 'target_character':
            data['is_npc'] = True
        pair[key].data = data
    pair['actor_location'].pos_x, pair['actor_location'].pos_y = 1, 1
    pair['target_location'].pos_x, pair['target_location'].pos_y = 2, 1
    db.session.commit()
    pair.update(water=water, bandage=bandage)
    response = configure(client, pair, auth_headers)
    assert response.status_code == 200, response.json
    return pair


def configure(client, pair, auth_headers, **changes):
    body = {'character_revision':pair['target_character'].revision, 'enabled':True,
        'prices':{str(pair['water'].id):{'buy':40,'sell':75}, str(pair['bandage'].id):{'buy':25,'sell':50}},
        'workbenches':['Станок Оружейника','Станок Бронника','Станок Электрика']}
    body.update(changes)
    return client.patch(f"/lobbies/{pair['lobby'].id}/characters/{pair['target_character'].id}/merchant",
                        headers=auth_headers(pair['actor_user']), json=body)


def test_craft_only_flamethrower_cannot_enter_merchant_price_list(client,shop,auth_headers):
    from app.services.npc_trade import price_for
    from app.services.character_starting_purchase import eligible
    from app.services.exceptions import ValidationError
    template=ItemTemplate(name='Реактивный огнемёт',category='weapon',price=0,
        attributes={'craft_only':True,'unspecified_fields':['price','item_class']})
    db.session.add(template);db.session.commit()
    response=configure(client,shop,auth_headers,prices={str(template.id):{'buy':0,'sell':0}})
    assert response.status_code==400,response.json
    assert not eligible(template)
    with pytest.raises(ValidationError,match='только через создание'):
        price_for(shop['target_character'].data,{'templateId':template.id,'attributes':template.attributes},'sell')


@pytest.mark.parametrize('name', ['Болотная вода', 'Дождевая вода', 'Радиоактивная вода'])
def test_contaminated_water_is_excluded_from_starting_purchase_and_merchant_trade(client, shop, auth_headers, name):
    from app.services.character_starting_purchase import apply_purchase, catalog, item_from_template
    from app.services.equipment_catalog import load_catalog
    from app.services.exceptions import ValidationError
    from app.services.npc_trade import price_for

    template = ItemTemplate(**next(entry for entry in load_catalog() if entry['name'] == name))
    clean_water = ItemTemplate(**next(entry for entry in load_catalog() if entry['name'] == 'Вода'))
    db.session.add_all([template, clean_water])
    db.session.commit()
    starting_names = {entry['name'] for entry in catalog()}
    assert 'Вода' in starting_names
    assert name not in starting_names
    with pytest.raises(ValidationError, match='Предмет недоступен при стартовом закупе'):
        apply_purchase(deepcopy(shop['actor_character'].data), [{'templateId': template.id, 'quantity': 1}], 1000)
    prices = {str(template.id): {'buy': 40, 'sell': 75}}
    response = configure(client, shop, auth_headers, prices=prices)
    assert response.status_code == 400, response.json
    assert 'нельзя включить в торговый прайс' in response.json['error']['message']
    with pytest.raises(ValidationError, match='нельзя продать или купить'):
        price_for(shop['target_character'].data, item_from_template(template), 'buy')


def test_partly_used_tool_prorates_catalog_and_both_merchant_prices():
    from app.services.npc_trade import price_for, tool_price
    from app.services.exceptions import ValidationError

    item = {'category': 'tool', 'templateId': 42, 'uses': 7, 'maxUses': 12}
    merchant = {'merchant': {'prices': {'42': {'buy': 101, 'sell': 211}}}}
    assert tool_price(item, 1001) == 583
    assert price_for(merchant, item, 'buy') == 58
    assert price_for(merchant, item, 'sell') == 123
    item['uses'] = 0
    assert price_for(merchant, item, 'buy') == 0
    item['uses'] = 13
    with pytest.raises(ValidationError, match='зарядов'):
        price_for(merchant, item, 'sell')


def test_shop_trades_partly_used_tools_at_prorated_prices(client, shop, auth_headers):
    template = ItemTemplate(name='Инструмент для торговли', category='tool', price=1001,
                            attributes={'uses': 12})
    db.session.add(template)
    db.session.flush()
    for key, uses in [('actor_character', 7), ('target_character', 3)]:
        data = deepcopy(shop[key].data)
        item = data['inventory']['backpack'][0]
        item.update(name=template.name, category='tool', templateId=template.id,
                    price=1001, uses=uses, maxUses=12, attributes={'uses': 12})
        shop[key].data = data
    db.session.commit()
    configured = configure(client, shop, auth_headers,
        prices={str(template.id): {'buy': 101, 'sell': 211}})
    assert configured.status_code == 200, configured.json
    result = propose(client, shop, auth_headers, sell=1, buy=1)
    assert result.status_code == 201, result.json
    assert result.json['payload']['sale_total'] == 58
    assert result.json['payload']['purchase_total'] == 52
    assert shop['actor_character'].data['inventory']['money'] == 1006
    assert shop['target_character'].data['inventory']['money'] == 494


@pytest.mark.parametrize('background,category,current,maximum,expected_price', [
    ({}, 'weapon', 40, 100, 400),
    ({'name': 'Торгашня'}, 'weapon', 40, 100, 700),
    ({'ruleId': 'background-p'}, 'armor', 40, None, 700),
    ({'ruleId': 'background-p'}, 'helmet', 0, 100, 500),
    ({'name': 'Торгашня'}, 'weapon', 40.1, 100, 700),
    ({'name': 'Торгашня'}, 'weapon', 100, 100, 1000),
])
def test_merchant_quotes_and_pays_for_physical_wear_with_torgashnya(
        client, shop, auth_headers, background, category, current, maximum, expected_price):
    actor, merchant = shop['actor_character'], shop['target_character']
    player = shop['target_user']
    template = ItemTemplate(name='Предмет с прочностью', category=category, price=1000,
                            attributes={'max_durability': 100})
    db.session.add(template)
    db.session.flush()
    data = deepcopy(actor.data)
    data.setdefault('basic', {})['background'] = background
    item = data['inventory']['backpack'][0]
    item.update(name=template.name, templateId=template.id, category=category, durability=current, quantity=1)
    if maximum is not None:
        item['maxDurability'] = maximum
    actor.data = data
    actor.owner_id = player['id']
    shop['actor_location'].controlled_by = player['id']
    db.session.commit()
    configured = configure(client, shop, auth_headers, money=2000,
                           prices={str(template.id): {'buy': 1000, 'sell': None}})
    assert configured.status_code == 200, configured.json

    url = (f"/lobbies/{shop['lobby'].id}/locations/{shop['location'].id}"
           f"/characters/{merchant.id}/interaction?actor_location_character_id={shop['actor_location'].id}")
    snapshot = client.get(url, headers=auth_headers(player))
    assert snapshot.status_code == 200, snapshot.json
    assert snapshot.json['merchant_sale_prices'] == {'["inventory","backpack",0]': expected_price}
    assert snapshot.json['target_data']['merchant']['prices'][str(template.id)]['buy'] == 1000

    result = propose(client, shop, auth_headers, sell=1, as_user='target_user')
    assert result.status_code == 201, result.json
    assert result.json['payload']['sale_total'] == expected_price
    assert actor.data['inventory']['money'] == 1000 + expected_price
    assert merchant.data['inventory']['money'] == 2000 - expected_price
    received = next(item for item in merchant.data['inventory']['backpack'] if item.get('templateId') == template.id)
    assert received['durability'] == current
    assert received.get('maxDurability') == maximum


def propose(client, shop, auth_headers, *, sell=0, buy=0, as_user='actor_user', **extra):
    payload = {'merchant_mode':True, 'actor_money':0, 'target_money':0,
        'actor_revision':shop['actor_character'].revision, 'target_revision':shop['target_character'].revision}
    if sell:
        payload['actor_offer'] = [{'item_id':'actor-bandage', 'path':['inventory','backpack',0], 'amount':sell}]
    if buy:
        payload['target_offer'] = [{'item_id':'target-water', 'path':['inventory','backpack',0], 'amount':buy}]
    payload.update(extra)
    return client.post(f"/lobbies/{shop['lobby'].id}/locations/{shop['location'].id}/character-interactions",
        headers=auth_headers(shop[as_user]), json={'kind':'trade',
            'actor_location_character_id':shop['actor_location'].id, 'target_character_id':shop['target_character'].id,
            'payload':payload})


def haggle(client, shop, auth_headers, expected_round, *, buy=0, sell=0, skill='barter'):
    offer = {'merchant_mode': True, 'actor_revision': shop['actor_character'].revision,
             'target_revision': shop['target_character'].revision}
    if buy:
        offer['target_offer'] = [{'item_id': 'target-water', 'path': ['inventory', 'backpack', 0], 'amount': buy}]
    if sell:
        offer['actor_offer'] = [{'item_id': 'actor-bandage', 'path': ['inventory', 'backpack', 0], 'amount': sell}]
    url = f"/lobbies/{shop['lobby'].id}/locations/{shop['location'].id}/merchant-haggle"
    return client.post(url, headers=auth_headers(shop['actor_user']), json={
        'actor_location_character_id': shop['actor_location'].id, 'target_character_id': shop['target_character'].id,
        'offer': offer, 'skill': skill, 'expected_round': expected_round,
    })


def test_haggle_price_is_bound_to_basket_and_round_and_moves_ten_percent_of_base(client, shop, auth_headers, monkeypatch):
    from app.services import merchant_haggle
    actor, seller = shop['actor_character'], shop['target_character']
    actor.data = {**actor.data, 'skills': {'social': {'charisma': {'base': 14}, 'persuasion': {'base': 12}}}}
    seller.data = {**seller.data, 'skills': {'physical': {'will': {'base': 14}}}}
    db.session.commit()
    rolls = iter([16, 8, 14, 9, 10, 11])
    monkeypatch.setattr(merchant_haggle, 'randint', lambda *_: next(rolls))
    prices = []
    for round_number in range(3):
        response = haggle(client, shop, auth_headers, round_number, buy=2, sell=1, skill='persuasion')
        assert response.status_code == 200, response.json
        prices.append(response.json['actor_money'])
        assert len(response.json['rounds']) == round_number + 1
    quote = response.json
    assert prices == [112, 100, 100]
    assert quote['sale_total'] == 25
    assert quote['purchase_total'] == 150
    assert quote['rounds'][0]['actor']['bonuses'] == {'charisma': 2, 'persuasion': 1}
    assert quote['rounds'][0]['target']['bonuses']['will'] == 2
    assert [entry['change'] for entry in quote['rounds']] == [1, 1, 0]
    assert haggle(client, shop, auth_headers, 0, buy=2, sell=1).json == quote
    assert haggle(client, shop, auth_headers, 3, buy=2, sell=1).status_code == 400
    assert actor.data['inventory']['money'] == 1000
    assert propose(client, shop, auth_headers, buy=2, sell=1).status_code == 409
    assert propose(client, shop, auth_headers, buy=2, sell=1, haggle_id=quote['id'], haggle_round=2).status_code == 409
    assert propose(client, shop, auth_headers, buy=1, sell=1, haggle_id=quote['id'], haggle_round=3).status_code == 409
    result = propose(client, shop, auth_headers, buy=2, sell=1, haggle_id=quote['id'], haggle_round=3, actor_money=1)
    assert result.status_code == 201, result.json
    assert result.json['result']['actor_to_target_money'] == 100
    assert actor.data['inventory']['money'] == 900
    assert seller.data['inventory']['money'] == 600
    assert shop['actor_location'].action_points_current == 3


def test_seller_uses_will_when_player_haggles_to_sell(client, shop, auth_headers, monkeypatch):
    from app.services import merchant_haggle
    actor = shop['actor_character']
    actor.data = {**actor.data, 'skills': {'physical': {'will': {'base': 14}}}}
    db.session.commit()
    rolls = iter([7, 12])
    monkeypatch.setattr(merchant_haggle, 'randint', lambda *_: next(rolls))
    response = haggle(client, shop, auth_headers, 0, sell=2)
    assert response.status_code == 200, response.json
    quote = response.json
    assert quote['rounds'][0]['actor']['bonuses']['will'] == 2
    assert 'will' not in quote['rounds'][0]['target']['bonuses']
    assert quote['sale_total'] == 50
    result = propose(client, shop, auth_headers, sell=2, haggle_id=quote['id'], haggle_round=1)
    assert result.status_code == 201, result.json
    assert result.json['result']['target_to_actor_money'] == 45


@pytest.mark.parametrize('buy,sell,ammo_side,skill,advantage', [
    (3, 0, None, 'barter', True),
    (2, 1, None, 'barter', True),
    (2, 0, None, 'barter', False),
    (3, 0, 'target', 'barter', False),
    (3, 1, 'actor', 'barter', True),
    (2, 2, 'actor', 'barter', False),
    (3, 0, None, 'persuasion', False),
])
def test_bulk_barter_counts_item_units_on_both_sides_except_ammo(
        client, shop, auth_headers, monkeypatch, buy, sell, ammo_side, skill, advantage):
    from app.services import merchant_haggle
    actor = shop['actor_character']
    actor.data = {**actor.data, 'features': {'specialTraits': [{'name': 'Оптовик'}]}}
    if ammo_side == 'actor':
        shop['bandage'].category = 'ammo'
    elif ammo_side == 'target':
        shop['water'].category = 'ammo'
    db.session.commit()
    rolls = iter([4, 18, 11] if advantage else [4, 11])
    monkeypatch.setattr(merchant_haggle, 'randint', lambda *_: next(rolls))
    response = haggle(client, shop, auth_headers, 0, buy=buy, sell=sell, skill=skill)
    assert response.status_code == 200, response.json
    round_result = response.json['rounds'][0]
    assert round_result['actor']['advantage'] is advantage
    assert round_result['actor']['rolls'] == ([4, 18] if advantage else [4])
    assert round_result['actor']['roll'] == (18 if advantage else 4)
    assert round_result['target']['rolls'] == [11]
    assert round_result['change'] == (1 if advantage else -1)


def test_bulk_haggles_over_net_payout_with_player_as_seller(client, shop, auth_headers, monkeypatch):
    from app.services import merchant_haggle
    actor = shop['actor_character']
    actor.data = {**actor.data, 'features': {'specialTraits': [{'name': 'Оптовик'}]},
                  'skills': {'physical': {'will': {'base': 14}}}}
    db.session.commit()
    configured = configure(client, shop, auth_headers, money=2000, prices={
        str(shop['bandage'].id): {'buy': 2500, 'sell': None},
        str(shop['water'].id): {'buy': None, 'sell': 4000},
    })
    assert configured.status_code == 200, configured.json
    rolls = iter([4, 18, 12])
    monkeypatch.setattr(merchant_haggle, 'randint', lambda *_: next(rolls))
    response = haggle(client, shop, auth_headers, 0, buy=1, sell=2)
    assert response.status_code == 200, response.json
    quote = response.json
    assert (quote['sale_total'], quote['purchase_total'], quote['target_money']) == (5000, 4000, 1100)
    assert quote['rounds'][0]['actor']['bonuses']['will'] == 2
    assert 'will' not in quote['rounds'][0]['target']['bonuses']
    result = propose(client, shop, auth_headers, sell=2, buy=1, haggle_id=quote['id'], haggle_round=1)
    assert result.status_code == 201, result.json
    assert result.json['result']['target_to_actor_money'] == 1100
    assert actor.data['inventory']['money'] == 2100
    assert shop['target_character'].data['inventory']['money'] == 900


def test_equal_value_merchant_basket_can_be_exchanged_at_zero(client, shop, auth_headers):
    actor = shop['actor_character']
    data = deepcopy(actor.data)
    data['inventory']['backpack'][0]['quantity'] = 3
    actor.data = data
    db.session.commit()
    equal = haggle(client, shop, auth_headers, 0, buy=1, sell=3)
    assert equal.status_code == 400 and 'нулю' in equal.json['error']['message']
    result = propose(client, shop, auth_headers, buy=1, sell=3)
    assert result.status_code == 201, result.json
    assert result.json['result']['target_to_actor_money'] == 0


@pytest.mark.parametrize('sell,buy,actor_balance,target_balance', [(0,2,850,650),(1,0,1025,475),(1,2,875,625)])
def test_merchant_automatically_exchanges_goods_for_server_calculated_money(client, shop, auth_headers, sell, buy, actor_balance, target_balance):
    response = propose(client, shop, auth_headers, sell=sell, buy=buy, actor_money=0, target_money=999999)
    assert response.status_code == 201, response.json
    assert response.json['status'] == 'completed'
    assert shop['actor_character'].data['inventory']['money'] == actor_balance
    assert shop['target_character'].data['inventory']['money'] == target_balance
    assert actor_balance + target_balance == 1500
    assert shop['actor_location'].action_points_current == 3
    assert response.json['payload']['purchase_total'] == buy * 75
    assert response.json['payload']['sale_total'] == sell * 25
    entry = shop['actor_character'].data['health']['combatMeta']['actionJournal'][-1]
    assert entry['kind'] == 'trade' and entry['label'].startswith('Торговля')
    assert entry['money_given'] - entry['money_received'] == 1000 - actor_balance


@pytest.mark.parametrize('background,template_name,display_name,attributes,unit_price', [
    ({'ruleId': 'background-m'}, 'Банка пива', 'Напиток', {'is_alcohol': True}, 56),
    ({'name': 'Прогульщик'}, 'Сигареты Петр Второй', 'Пачка', {}, 56),
    ({'name': 'Прогульщик'}, 'Вода', 'Банка пива', {}, 75),
    ({}, 'Банка пива', 'Банка пива', {'is_alcohol': True}, 75),
])
def test_merchant_quotes_and_charges_customer_discount_only_on_eligible_purchases(
        client, shop, auth_headers, background, template_name, display_name, attributes, unit_price):
    actor = shop['actor_character']
    target = shop['target_character']
    buyer = shop['target_user']
    actor_data = deepcopy(actor.data)
    actor_data.setdefault('basic', {})['background'] = background
    actor.data = actor_data
    actor.owner_id = buyer['id']
    shop['actor_location'].controlled_by = buyer['id']
    shop['water'].name = template_name
    shop['water'].attributes = attributes
    target_data = deepcopy(target.data)
    target_data['inventory']['backpack'][0]['name'] = display_name
    target.data = target_data
    db.session.commit()

    url = (f"/lobbies/{shop['lobby'].id}/locations/{shop['location'].id}"
           f"/characters/{target.id}/interaction?actor_location_character_id={shop['actor_location'].id}")
    preview = client.get(url, headers=auth_headers(buyer))
    assert preview.status_code == 200, preview.json
    template_key = str(shop['water'].id)
    assert preview.json['merchant_purchase_prices'] == ({template_key: unit_price} if unit_price < 75 else {})
    assert preview.json['target_data']['merchant']['prices'][template_key]['sell'] == 75

    result = propose(client, shop, auth_headers, sell=1, buy=2, as_user='target_user')
    assert result.status_code == 201, result.json
    assert result.json['payload']['purchase_total'] == 2 * unit_price
    assert result.json['payload']['sale_total'] == 25
    assert actor.data['inventory']['money'] == 1000 - 2 * unit_price + 25
    assert target.data['inventory']['money'] == 500 + 2 * unit_price - 25
    assert target.data['merchant']['prices'][template_key]['sell'] == 75


@pytest.mark.parametrize('fault', ['empty','too_many','customer_money','merchant_money','too_far','unpriced','disabled','created_manually'])
def test_invalid_shop_operation_does_not_change_goods_money_or_ap(client, shop, auth_headers, fault):
    sell, buy = 0, 1
    if fault == 'empty':
        buy = 0
    elif fault == 'too_many':
        buy = 100
    elif fault in {'customer_money', 'merchant_money'}:
        key = 'actor_character' if fault == 'customer_money' else 'target_character'
        data = deepcopy(shop[key].data)
        data['inventory']['money'] = 0
        shop[key].data = data
        if fault == 'merchant_money':
            sell, buy = 1, 0
    elif fault == 'too_far':
        shop['target_location'].pos_x = 10
    elif fault == 'unpriced':
        configure(client, shop, auth_headers, prices={})
    elif fault == 'disabled':
        configure(client, shop, auth_headers, enabled=False)
    elif fault == 'created_manually':
        data = deepcopy(shop['actor_character'].data)
        data['inventory']['backpack'][0]['createdByPlayer'] = True
        shop['actor_character'].data = data
        sell, buy = 1, 0
    db.session.commit()
    before = deepcopy(shop['actor_character'].data), deepcopy(shop['target_character'].data)
    response = propose(client, shop, auth_headers, sell=sell, buy=buy)
    assert response.status_code in {400,409}, response.json
    assert (shop['actor_character'].data, shop['target_character'].data) == before
    assert shop['actor_location'].action_points_current == 5
    assert CharacterInteractionRequest.query.count() == 0


def test_merchant_configuration_is_gm_only_and_cannot_be_replaced_by_sheet_save(client, shop, auth_headers):
    response = client.patch(f"/lobbies/{shop['lobby'].id}/characters/{shop['target_character'].id}/merchant",
        headers=auth_headers(shop['target_user']), json={})
    assert response.status_code == 403
    from app.services.npc_trade import preserve
    data = {'merchant':{'enabled':False,'prices':{}}}
    preserve(shop['target_character'].data, data)
    assert data['merchant'] == shop['target_character'].data['merchant']


@pytest.mark.parametrize('price', [-1,True,.5,'75'])
def test_bad_prices_do_not_replace_saved_merchant_profile(client, shop, auth_headers, price):
    before = deepcopy(shop['target_character'].data['merchant'])
    result = configure(client, shop, auth_headers, prices={str(shop['water'].id):{'buy':40,'sell':price}})
    assert result.status_code == 400
    assert shop['target_character'].data['merchant'] == before


def test_merchant_refuses_stale_configuration_revision(client, shop, auth_headers):
    result = configure(client, shop, auth_headers, character_revision=shop['target_character'].revision-1)
    assert result.status_code == 409


def test_customer_without_gm_permissions_can_buy_from_configured_gm_npc(client, shop, auth_headers):
    shop['actor_character'].owner_id = shop['target_user']['id']
    shop['actor_location'].controlled_by = shop['target_user']['id']
    shop['target_character'].owner_id = shop['actor_user']['id']
    shop['target_location'].controlled_by = shop['actor_user']['id']
    db.session.commit()
    result = propose(client, shop, auth_headers, buy=2, as_user='target_user')
    assert result.status_code == 201, result.json
    assert result.json['status'] == 'completed'
    assert shop['actor_character'].data['inventory']['money'] == 850
