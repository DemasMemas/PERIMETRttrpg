"""Добыча НПС: фильтры, бюджет, повтор запроса и запреты без изменения игры."""
from copy import deepcopy
import random

import pytest

from app.extensions import db
from app.models import LocationCombatState
from app.models.templates import ItemTemplate
from app.services.npc_loot import roll_loot
from app.services.exceptions import ValidationError
from test_character_interactions import _setup_pair


def pool(**changes):
    return {'category':'consumable','subcategory':'Медицина','item_class':'1','min_price':1,'max_price':100,
            'draws':2,'quantity':1,'chance':100, **changes}


def profile(**changes):
    return {'name':'Бандит: медицина','budget':100,'money_min':10,'money_max':10,'pools':[pool()],**changes}


@pytest.fixture
def prepared(client, create_user, auth_headers):
    pair = _setup_pair(client, create_user, auth_headers)
    pair['combat'] = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    target = pair['target_character']
    target.data = {**deepcopy(target.data), 'is_npc':True}
    pair['combat'].status = 'idle'
    templates = [ItemTemplate(name=name,category=category,subcategory=subcategory,item_class=item_class,price=price,attributes={})
                 for name,category,subcategory,item_class,price in [
                     ('Бинт','consumable','Медицина','1',50),('КПК','misc','КПК','2',80),
                     ('Дорогой препарат','consumable','Медицина','1',150),('Сложный препарат','consumable','Медицина','3',50),
                     ('Пища','consumable','Продукты','1',50),('Автомат','weapon','rifles','1',90)]]
    db.session.add_all(templates);db.session.commit()
    pair['templates'] = templates
    return pair


def api(client, pair, auth_headers, *, generate=False, user='actor_user', **changes):
    target=pair['target_character']
    payload={'character_revision':target.revision,**({'operation_id':'loot-once'} if generate else {'profile':profile()}),**changes}
    method=client.post if generate else client.patch
    return method(f"/lobbies/{pair['lobby'].id}/characters/{target.id}/loot-pool",headers=auth_headers(pair[user]),json=payload)


def test_loot_filters_class_type_price_and_stays_within_budget(prepared):
    items, result=roll_loot(profile(),prepared['templates'],rng=random.Random(1))
    assert [item['name'] for item in items]==['Бинт','Бинт']
    assert result['spent']==100 and result['money']==10
    assert len({item['id'] for item in items})==2
    assert all(not item.get('createdByPlayer') for item in items)


def test_retired_sport_gloves_are_not_offered_or_generated(client,prepared,auth_headers):
    from app.services.equipment_catalog import load_catalog
    from app.services.character_starting_purchase import eligible
    from app.services.npc_loot import candidates

    sport=ItemTemplate(name='Спортивные',category='gloves',subcategory='Перчатки',price=3500,attributes={})
    working=ItemTemplate(name='Рабочие',category='gloves',subcategory='Перчатки',price=1500,attributes={})
    db.session.add_all([sport,working]);db.session.commit()
    assert not any(t['category']=='gloves' and t['name']=='Спортивные' for t in load_catalog())
    assert not eligible(sport) and eligible(working)
    available=candidates([sport,working],pool(category='gloves',subcategory='Перчатки',item_class='',max_price=5000))
    assert available==[working]
    response=client.get(f"/lobbies/{prepared['lobby'].id}/templates?category=gloves",
        headers=auth_headers(prepared['actor_user']))
    assert response.status_code==200,response.json
    assert [entry['name'] for entry in response.json['global']]==['Рабочие']


def test_budget_exhaustion_and_zero_chance_are_visible_without_exceeding_cost(prepared):
    items,result=roll_loot(profile(budget=50,pools=[pool(draws=3),pool(category='no-items',chance=0)]),prepared['templates'])
    assert len(items)==1 and result['spent']==50 and result['skipped_pools']==[1]
    assert len(prepared['target_character'].data['inventory']['backpack'])==1


def test_gm_can_save_reuse_and_generate_without_replacing_existing_items(client,prepared,auth_headers):
    result=api(client,prepared,auth_headers)
    assert result.status_code==200,result.json
    target=prepared['target_character'];revision=target.revision
    response=api(client,prepared,auth_headers,generate=True)
    assert response.status_code==200,response.json
    assert len(target.data['inventory']['backpack'])==3
    assert target.data['inventory']['backpack'][0]['id']=='target-water'
    assert target.data['inventory']['money']==10
    assert target.data['npcLoot']['profile']==profile()
    duplicate=api(client,prepared,auth_headers,generate=True,character_revision=revision)
    assert duplicate.status_code==200 and duplicate.json['receipt']==response.json['receipt']
    assert len(target.data['inventory']['backpack'])==3 and target.data['inventory']['money']==10


@pytest.mark.parametrize('fault',['not_gm','stale','not_npc','active_combat','missing_profile'])
def test_forbidden_generation_preserves_inventory_money_and_ap(client,prepared,auth_headers,fault):
    target=prepared['target_character']
    if fault!='missing_profile':assert api(client,prepared,auth_headers).status_code==200
    if fault=='not_npc':target.data={**deepcopy(target.data),'is_npc':False}
    if fault=='active_combat':prepared['combat'].status='active'
    db.session.commit();before=deepcopy(target.data)
    result=api(client,prepared,auth_headers,generate=True,user='target_user' if fault=='not_gm' else 'actor_user',
               **({'character_revision':target.revision-1} if fault=='stale' else {}))
    assert result.status_code in {400,403,409},result.json
    assert target.data==before
    assert prepared['actor_location'].action_points_current==5


@pytest.mark.parametrize('bad',[profile(budget=-1),profile(money_min=20,money_max=10),profile(pools=[pool(chance=True)]),
                                  profile(pools=[pool(quantity=0)]),profile(pools=[pool(item_class='Не существует')])])
def test_invalid_profile_does_not_overwrite_saved_settings(client,prepared,auth_headers,bad):
    assert api(client,prepared,auth_headers).status_code==200
    before=deepcopy(prepared['target_character'].data)
    response=api(client,prepared,auth_headers,profile=bad)
    assert response.status_code==400,response.json
    assert prepared['target_character'].data==before


def test_weapons_are_independent_and_pda_is_a_real_inventory_item(client,prepared,auth_headers):
    p=profile(budget=300,pools=[pool(category='weapon',subcategory='rifles',quantity=2,draws=1),
                               pool(category='misc',subcategory='КПК',item_class='2',draws=1)])
    assert api(client,prepared,auth_headers,profile=p).status_code==200
    assert api(client,prepared,auth_headers,generate=True).status_code==200
    data=prepared['target_character'].data
    assert len(data['weapons'])==2 and data['weapons'][0]['id']!=data['weapons'][1]['id']
    assert data['weapons'][0]['ammo']==0
    assert data['inventory']['backpack'][-1]['name']=='КПК'


def test_saved_pool_and_receipts_cannot_be_forged_by_ordinary_sheet_save(client,prepared,auth_headers):
    assert api(client,prepared,auth_headers).status_code==200
    from app.services.npc_loot import preserve
    incoming={'npcLoot':{'profile':profile(budget=9000000),'receipts':[]}}
    preserve(prepared['target_character'].data,incoming)
    assert incoming['npcLoot']==prepared['target_character'].data['npcLoot']
    preserve({},incoming)
    assert 'npcLoot' not in incoming
