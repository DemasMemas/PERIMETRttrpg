"""Броски обычных вещей используют проверку гранат и переносят экземпляр на клетку."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.services.combat import CombatService
from app.services.exceptions import ValidationError
from app.services.thrown_items import prepare, weight_band
from test_grenade_rules import grenade_game


@pytest.mark.parametrize('weight,damage,limit,modifier', [
    (.1, 1, None, -5), (.5, 15, None, 0), (2, 30, 10, 3),
    (5, 45, 3, 6), (15, 80, 1, 8),
])
def test_weight_bands(weight, damage, limit, modifier):
    result = weight_band(weight)
    assert result == {'maximum_distance': limit, 'difficulty': modifier, 'damage': damage}


def test_weight_over_limit_rejected():
    with pytest.raises(ValidationError): weight_band(15.1)


def test_throw_item_moves_one_instance_and_spends_server_ap(grenade_game, monkeypatch):
    actor=grenade_game['actor_location'];location=grenade_game['location']
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'id':'throw-stack','name':'Камень','category':'misc',
        'weight':.4,'quantity':2}]
    actor.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.brigadier_order.boost_roll',lambda data,rolls,**kwargs:([20],20,0))
    plan=prepare(location,actor,actor.character.data,['inventory','pockets',0],
        'throw-stack',4,5)
    assert plan['difficulty']>=1
    before=actor.action_points_current
    result=CombatService.perform_action(location.id,grenade_game['lobby'].gm_id,
        actor.id,'throw_item',item_path=['inventory','pockets',0],item_id='throw-stack',
        target_x=4,target_y=5)
    assert result['thrown_item']['success']
    assert actor.action_points_current==before-plan['action_points']
    assert actor.character.data['inventory']['pockets'][0]['quantity']==1
    assert result['thrown_item']['ground_object_id']


def test_heavy_item_cannot_exceed_range(grenade_game):
    actor=grenade_game['actor_location'];location=grenade_game['location']
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'id':'heavy','name':'Кирпич','category':'misc',
        'weight':5,'quantity':1}]
    actor.character.data=data;db.session.commit()
    with pytest.raises(ValidationError):
        prepare(location,actor,actor.character.data,['inventory','pockets',0],'heavy',8,5)


def test_throw_hit_uses_random_d6_zone_and_armor_damage(grenade_game, monkeypatch):
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    target.pos_x,target.pos_y=4,5
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'id':'throw-rock','name':'Камень','category':'misc',
        'weight':2,'quantity':1}]
    actor.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.brigadier_order.boost_roll',lambda data,rolls,**kwargs:([20],20,0))
    monkeypatch.setattr('app.services.thrown_items.random.randint',lambda low,high:5)
    result=CombatService.perform_action(grenade_game['location'].id,grenade_game['lobby'].gm_id,
        actor.id,'throw_item',item_path=['inventory','pockets',0],item_id='throw-rock',
        target_x=4,target_y=5)
    hit=result['thrown_item']['damage']
    assert hit and hit['hit'] and hit['zone']=='chest'
    assert hit['damage']>=0


def test_only_allowed_non_crushing_melee_attack_can_be_thrown(grenade_game, monkeypatch):
    actor=grenade_game['actor_location'];location=grenade_game['location']
    data=deepcopy(actor.character.data)
    data['weapons']=[{'id':'throw-knife','name':'Нож','category':'melee_weapon',
        'weight':.4,'quantity':1,'attributes':{'allowed_attacks':['Колющий','Дробящий','Круговой']}}]
    actor.character.data=data;actor.drawn_weapon_index=0;db.session.commit()
    for attack in ('Дробящий','Круговой'):
        with pytest.raises(ValidationError):
            prepare(location,actor,actor.character.data,['weapons',0],'throw-knife',4,5,attack_type=attack)
    monkeypatch.setattr('app.services.brigadier_order.boost_roll',lambda data,rolls,**kwargs:([20],20,0))
    result=CombatService.perform_action(location.id,grenade_game['lobby'].gm_id,
        actor.id,'throw_item',item_path=['weapons',0],item_id='throw-knife',
        target_x=4,target_y=5,attack_type='Колющий')
    assert result['thrown_item']['success']
    assert actor.drawn_weapon_index is None
    assert actor.character.data['weapons']==[]


def test_head_hit_adds_five_turn_concussion(grenade_game, monkeypatch):
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    target.pos_x,target.pos_y=4,5
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'name':'Камень','category':'misc','weight':2,'quantity':1}]
    actor.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.brigadier_order.boost_roll',lambda data,rolls,**kwargs:([20],20,0))
    monkeypatch.setattr('app.services.thrown_items.random.randint',lambda low,high:6)
    result=CombatService.perform_action(grenade_game['location'].id,grenade_game['lobby'].gm_id,
        actor.id,'throw_item',item_path=['inventory','pockets',0],item_id=None,target_x=4,target_y=5)
    assert result['thrown_item']['damage']['zone']=='head'
    assert any(effect['type']=='concussion' and effect['remaining']==5
        for effect in target.character.data['health']['effects'])
    ground=result['thrown_item']['ground_object_id']
    from app.models import LocationObject
    assert LocationObject.query.get(ground).properties['contents'][0]['id']
