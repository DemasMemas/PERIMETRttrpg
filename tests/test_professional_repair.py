"""Платный ремонт у НПС: расчёт книги, деньги, повтор и отказ."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import LocationCombatState
from app.models.templates import ItemTemplate
from test_npc_trade import shop,configure


@pytest.fixture
def mechanic(client,shop,auth_headers):
    state=LocationCombatState.query.filter_by(location_id=shop['location'].id).one()
    state.status='inactive'
    template=ItemTemplate(name='Пистолет для ремонта',category='weapon',price=1000,attributes={})
    db.session.add(template);db.session.flush()
    data=deepcopy(shop['actor_character'].data)
    data['weapons']=[{'id':'repair-weapon','templateId':template.id,'category':'weapon','name':template.name,
        'durability':50,'maxDurability':100,
        'modifications':[{'ruleId':'weapon-b33','rules':{'repair_discount':.5}}]}]
    shop['actor_character'].data=data
    db.session.commit()
    response=configure(client,shop,auth_headers,repair_enabled=True,repair_markup=1.2)
    assert response.status_code==200,response.json
    shop['repair_template']=template
    return shop


def request(client,case,auth_headers,**extra):
    payload={'actor_location_character_id':case['actor_location'].id,
        'mechanic_character_id':case['target_character'].id,'item_path':['weapons',0],
        'item_id':'repair-weapon','mode':'weapon',**extra}
    return client.post(f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/professional-repair",
        headers=auth_headers(case['actor_user']),json=payload)


def test_mechanic_quote_and_repair_use_catalog_price_and_weapon_discount(client,mechanic,auth_headers):
    quote=request(client,mechanic,auth_headers)
    assert quote.status_code==200,quote.json
    assert quote.json['price']==300  # 50% поломки × 1000 ₽ × 1,2 × скидка 50%.
    result=request(client,mechanic,auth_headers,execute=True,operation_id='repair-one',
        actor_revision=mechanic['actor_character'].revision,
        mechanic_revision=mechanic['target_character'].revision)
    assert result.status_code==200,result.json
    assert result.json['duplicate'] is False
    assert mechanic['actor_character'].data['inventory']['money']==700
    assert mechanic['target_character'].data['inventory']['money']==800
    weapon=mechanic['actor_character'].data['weapons'][0]
    assert (weapon['durability'],weapon['maxDurability'])==(95,95)
    assert mechanic['actor_character'].data['health']['combatMeta']['actionJournal'][-1]['kind']=='professional_repair'
    repeated=request(client,mechanic,auth_headers,execute=True,operation_id='repair-one',
        actor_revision=0,mechanic_revision=0)
    assert repeated.status_code==200 and repeated.json['duplicate'] is True
    assert mechanic['actor_character'].data['inventory']['money']==700


@pytest.mark.parametrize('change',[
    {'item_id':'another-item'},
    {'mode':'armor_stage'},
    {'actor_revision':-1},
])
def test_invalid_repair_does_not_spend_money(client,mechanic,auth_headers,change):
    before=deepcopy(mechanic['actor_character'].data)
    payload={'execute':True,'operation_id':'bad',
        'actor_revision':mechanic['actor_character'].revision,
        'mechanic_revision':mechanic['target_character'].revision,**change}
    response=request(client,mechanic,auth_headers,**payload)
    assert response.status_code in {400,409},response.json
    assert mechanic['actor_character'].data==before


def test_repair_requires_nearby_mechanic_and_valid_markup(client,mechanic,auth_headers):
    mechanic['target_location'].pos_x=10;db.session.commit()
    response=request(client,mechanic,auth_headers)
    assert response.status_code==400 and 'Подойдите' in response.json['error']['message']
    mechanic['target_location'].pos_x=2;db.session.commit()
    response=configure(client,mechanic,auth_headers,repair_enabled=True,repair_markup=0)
    assert response.status_code==400


def test_discounted_mechanic_multiplier_below_one(client,mechanic,auth_headers):
    response=configure(client,mechanic,auth_headers,repair_enabled=True,repair_markup=.5)
    assert response.status_code==200,response.json
    quote=request(client,mechanic,auth_headers)
    assert quote.status_code==200 and quote.json['price']==125
    paid=request(client,mechanic,auth_headers,execute=True,operation_id='half-price-repair',
        actor_revision=mechanic['actor_character'].revision,
        mechanic_revision=mechanic['target_character'].revision)
    assert paid.status_code==200 and paid.json['price']==125
    assert mechanic['actor_character'].data['inventory']['money']==875


def test_armor_stage_repair_uses_ten_percent_of_catalog_price(client,mechanic,auth_headers):
    armor_template=ItemTemplate(name='Броня для ремонта',category='armor',price=2000,
        attributes={'max_durability':20})
    db.session.add(armor_template);db.session.flush()
    data=deepcopy(mechanic['actor_character'].data)
    data.setdefault('equipment',{})['armor']={'id':'repair-armor','templateId':armor_template.id,
        'category':'armor','name':armor_template.name,'stage':3,'durability':20,
        'currentStageDurability':1,'attributes':{'max_durability':20}}
    mechanic['actor_character'].data=data;db.session.commit()
    response=request(client,mechanic,auth_headers,item_path=['equipment','armor'],item_id='repair-armor',mode='armor_stage')
    assert response.status_code==200,response.json
    assert response.json['price']==240  # 10% × 2000 ₽ × 1,2.
    result=request(client,mechanic,auth_headers,item_path=['equipment','armor'],item_id='repair-armor',mode='armor_stage',
        execute=True,operation_id='repair-armor',actor_revision=mechanic['actor_character'].revision,
        mechanic_revision=mechanic['target_character'].revision)
    assert result.status_code==200,result.json
    assert mechanic['actor_character'].data['equipment']['armor']['stage']==2
    assert mechanic['actor_character'].data['equipment']['armor']['durability']==17


def test_non_gm_owner_can_repair_but_disabled_service_is_rejected(client,mechanic,auth_headers):
    mechanic['actor_character'].owner_id=mechanic['target_user']['id']
    mechanic['actor_location'].controlled_by=mechanic['target_user']['id']
    db.session.commit()
    payload={'actor_location_character_id':mechanic['actor_location'].id,
        'mechanic_character_id':mechanic['target_character'].id,'item_path':['weapons',0],
        'item_id':'repair-weapon','mode':'weapon'}
    url=f"/lobbies/{mechanic['lobby'].id}/locations/{mechanic['location'].id}/professional-repair"
    response=client.post(url,headers=auth_headers(mechanic['target_user']),json=payload)
    assert response.status_code==200,response.json
    response=configure(client,mechanic,auth_headers,repair_enabled=False)
    assert response.status_code==200,response.json
    response=client.post(url,headers=auth_headers(mechanic['target_user']),json=payload)
    assert response.status_code==400,response.json
