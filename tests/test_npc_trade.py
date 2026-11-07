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
