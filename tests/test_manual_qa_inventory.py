"""Дополнительные интеграционные проверки критериев ручного плейтеста торговли."""
from copy import deepcopy

from app.extensions import db
from app.models import LocationCombatState
from test_npc_trade import configure, propose, shop


def test_merchant_empty_side_is_disabled_and_zero_side_is_free(client, shop, auth_headers):
    water_id = str(shop['water'].id)
    bandage_id = str(shop['bandage'].id)
    configured = configure(client, shop, auth_headers, prices={
        water_id: {'buy': None, 'sell': 0},
        bandage_id: {'buy': None, 'sell': None},
    })
    assert configured.status_code == 200, configured.json
    prices = shop['target_character'].data['merchant']['prices']
    assert prices[water_id] == {'buy': None, 'sell': 0}
    assert bandage_id not in prices

    actor_before = deepcopy(shop['actor_character'].data)
    target_before = deepcopy(shop['target_character'].data)
    free_purchase = propose(client, shop, auth_headers, buy=1)
    assert free_purchase.status_code == 201, free_purchase.json
    assert free_purchase.json['status'] == 'completed'
    assert free_purchase.json['payload']['purchase_total'] == 0
    assert shop['actor_character'].data['inventory']['money'] == actor_before['inventory']['money']
    assert shop['target_character'].data['inventory']['money'] == target_before['inventory']['money']
    assert shop['actor_character'].data['inventory']['backpack'][-1]['id'] == 'target-water'

    actor_before = deepcopy(shop['actor_character'].data)
    target_before = deepcopy(shop['target_character'].data)
    ap_before = shop['actor_location'].action_points_current
    unavailable_sale = propose(client, shop, auth_headers, sell=1)
    assert unavailable_sale.status_code == 400, unavailable_sale.json
    assert shop['actor_character'].data == actor_before
    assert shop['target_character'].data == target_before
    assert shop['actor_location'].action_points_current == ap_before

def test_merchant_trade_rejects_target_more_than_one_point_five_metres_away(client, shop, auth_headers):
    combat = LocationCombatState.query.filter_by(location_id=shop['location'].id).one()
    combat.status = 'idle'
    shop['target_location'].pos_x = shop['actor_location'].pos_x + 2
    shop['target_location'].pos_y = shop['actor_location'].pos_y
    db.session.commit()
    actor_before = deepcopy(shop['actor_character'].data)
    target_before = deepcopy(shop['target_character'].data)
    ap_before = shop['actor_location'].action_points_current

    result = propose(client, shop, auth_headers, buy=1)

    assert result.status_code in {400, 409}, result.json
    assert shop['actor_character'].data == actor_before
    assert shop['target_character'].data == target_before
    assert shop['actor_location'].action_points_current == ap_before
