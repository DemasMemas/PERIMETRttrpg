"""Один ствол: оплата хвата, действующие руки, авторитет сервера."""
from copy import deepcopy
import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCombatState
from app.services.combat import CombatService
from test_grenade_rules import grenade_game


def prepare(case):
    actor=case['actor_location']
    data=deepcopy(actor.character.data)
    data['weapons']=[{'id':'grip-rifle','name':'Винтовка хвата','category':'weapon','ammo':5,
        'attributes':{'damage':50,'range':20,'min_strength':12,'noise':0}}]
    data['activeWeaponIndex']=0
    actor.character.data=data;flag_modified(actor.character,'data')
    actor.drawn_weapon_index=0;db.session.commit()
    return actor


def change(client,case,auth_headers,actor,hands,revision=None,user=None):
    return client.post(f'/lobbies/characters/{actor.character_id}/weapon-grip',
        headers=auth_headers(user or case['actor_user']),json={'hands':hands,'weapon_index':0,
            'character_revision':actor.character.revision if revision is None else revision})


def test_grip_switch_is_paid_once_and_changes_strength_requirement(client,grenade_game,auth_headers):
    actor=prepare(grenade_game);before=actor.action_points_current
    assert not CombatService._has_free_hand(actor)
    original_revision=actor.character.revision
    response=change(client,grenade_game,auth_headers,actor,1)
    assert response.status_code==200,response.json
    assert actor.action_points_current==before-1 and response.json['grip_change']['action_points']==1
    assert CombatService._has_free_hand(actor)
    profile=CombatService._weapon_strength_profile(actor,actor.character.data['weapons'][0])
    assert profile['base_required']==12 and profile['effective_required']==14
    assert profile['module_modifier']==0 and profile['grip_modifier']==2
    assert change(client,grenade_game,auth_headers,actor,1).status_code==200
    assert actor.action_points_current==before-1
    assert change(client,grenade_game,auth_headers,actor,2,original_revision).status_code==409
    assert actor.action_points_current==before-1
    assert change(client,grenade_game,auth_headers,actor,2).status_code==200
    assert actor.action_points_current==before-2
    assert CombatService._weapon_strength_profile(actor,actor.character.data['weapons'][0])['effective_required']==12


@pytest.mark.parametrize('occupied',['water','body','disabled'])
def test_two_hands_requires_free_usable_second_arm_before_payment(client,grenade_game,auth_headers,occupied):
    actor=prepare(grenade_game)
    assert change(client,grenade_game,auth_headers,actor,1).status_code==200
    data=deepcopy(actor.character.data)
    if occupied=='disabled':data['health'].setdefault('zones',{})['leftArm']={'current':0,'max':100}
    else:data['health']['combatMeta']['heldWaterItem' if occupied=='water' else 'bodyTransport']={'id':'occupied'}
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    before=actor.action_points_current
    response=change(client,grenade_game,auth_headers,actor,2)
    assert response.status_code==400,response.json
    assert actor.action_points_current==before
    assert actor.character.data['health']['combatMeta']['weaponGripHands']==1


def test_grip_endpoint_rejects_other_user_and_other_turn(client,grenade_game,auth_headers):
    actor=prepare(grenade_game);before=actor.action_points_current
    denied=change(client,grenade_game,auth_headers,actor,1,user=grenade_game['target_user'])
    assert denied.status_code==403
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    state.current_location_character_id=grenade_game['target_location'].id;db.session.commit()
    assert change(client,grenade_game,auth_headers,actor,1).status_code==403
    assert actor.action_points_current==before


def test_sheet_save_cannot_forge_or_reset_paid_grip(client,grenade_game,auth_headers):
    actor=prepare(grenade_game)
    assert change(client,grenade_game,auth_headers,actor,1).status_code==200
    snapshot=client.get(f'/lobbies/characters/{actor.character_id}',headers=auth_headers(grenade_game['actor_user'])).json['data']
    snapshot['health']['combatMeta']['weaponGripHands']=2
    response=client.put(f'/lobbies/characters/{actor.character_id}',headers=auth_headers(grenade_game['actor_user']),json={'data':snapshot})
    assert response.status_code==200,response.json
    assert response.json['data']['health']['combatMeta']['weaponGripHands']==1


def test_zero_action_points_cannot_switch_grip(client,grenade_game,auth_headers):
    actor=prepare(grenade_game);actor.action_points_current=0;db.session.commit()
    response=change(client,grenade_game,auth_headers,actor,1)
    assert response.status_code==400,response.json
    assert actor.action_points_current==0


def test_attack_with_disabled_second_arm_requires_one_hand_without_losing_resources(client,grenade_game,auth_headers,monkeypatch):
    actor=prepare(grenade_game);target=grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y
    data=deepcopy(actor.character.data);data['health'].setdefault('zones',{})['leftArm']={'current':0,'max':100}
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:min(b,max(a,10)))
    url=f'/lobbies/{grenade_game["lobby"].id}/locations/{actor.location_id}/combat/action'
    payload={'location_character_id':actor.id,'action_key':'attack','weapon_index':0,'fire_mode':'unaimed',
             'shot_count':1,'action_points':2,'target_character_id':target.character_id}
    before=actor.action_points_current
    denied=client.post(url,headers=auth_headers(grenade_game['actor_user']),json=payload)
    assert denied.status_code==400,denied.json
    assert actor.action_points_current==before and actor.character.data['weapons'][0]['ammo']==5
    assert change(client,grenade_game,auth_headers,actor,1).status_code==200
    accepted=client.post(url,headers=auth_headers(grenade_game['actor_user']),json=payload)
    assert accepted.status_code==200,accepted.json
    assert actor.action_points_current==before-3 and actor.character.data['weapons'][0]['ammo']==4


def test_switch_outside_combat_persists_without_charging_combat_resource(client,grenade_game,auth_headers):
    actor=prepare(grenade_game)
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='finished';db.session.commit()
    before=actor.action_points_current
    response=change(client,grenade_game,auth_headers,actor,1)
    assert response.status_code==200,response.json
    assert response.json['grip_change']['action_points']==0
    assert actor.action_points_current==before


def make_pair(client,case,headers,*,melee=False,weight=.5):
    actor=prepare(case);data=deepcopy(actor.character.data)
    data['skills']['physical']['strength']={'base':20}
    data['skills']['physical']['melee']={'base':20}
    if melee:
        data['weapons']=[{'id':'knife-a','name':'Первый нож','category':'melee_weapon','weight':.5,'volume':3,
            'attributes':{'damage':100,'weight_class':'Легкое','allowed_attacks':['Колющий'],'accuracy':100}},
            {'id':'knife-b','name':'Второй нож','category':'melee_weapon','weight':weight,'volume':6,
            'attributes':{'damage':100,'weight_class':'Легкое','allowed_attacks':['Колющий'],'accuracy':100}}]
    else:
        data['weapons'][0]['attributes'].update(accuracy=100,noise=0)
        data['weapons'][0]['subcategory']='Пистолеты'
        data['weapons'].append({'id':'grip-pistol-b','name':'Второй пистолет','category':'weapon','subcategory':'Пистолеты',
            'ammo':5,'weight':.5,'attributes':{'damage':10,'range':20,'min_strength':8,'accuracy':100,'noise':0}})
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    assert change(client,case,headers,actor,1).status_code==200
    before=actor.action_points_current
    result=CombatService.perform_action(actor.location_id,case['lobby'].gm_id,actor.id,'draw_weapon',
        weapon_index=1,attribute_choice='second_hand')
    assert actor.action_points_current==before-result['draw_weapon']['action_points']
    return actor


def test_pair_strength_and_each_weapon_can_fire(client,grenade_game,auth_headers,monkeypatch):
    from app.services.weapon_grip import secondary_index
    actor=make_pair(client,grenade_game,auth_headers);target=grenade_game['target_location']
    assert actor.drawn_weapon_index==0 and secondary_index(actor)==1
    profile=CombatService._weapon_strength_profile(actor,actor.character.data['weapons'][0])
    assert profile['effective_required']==18  # 12 + 8/2 + 2, без повторного штрафа одной руки.
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:min(b,max(a,10)))
    for index in (0,1):
        before=actor.action_points_current
        shot=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',
            weapon_index=index,fire_mode='unaimed',shot_count=1,action_points=1,target_character_id=target.character_id)
        assert shot['attack']['paired_weapon_accuracy_penalty']==2
        assert actor.action_points_current==before-1
    assert [weapon['ammo'] for weapon in actor.character.data['weapons']]==[4,4]


def test_pair_rejects_reload_cycle_and_free_grip_change_without_payment(client,grenade_game,auth_headers):
    from app.services.exceptions import ValidationError
    actor=make_pair(client,grenade_game,auth_headers);before=actor.action_points_current
    for key in ('reload_weapon','reload_underbarrel','clear_weapon_jam','load_ammunition'):
        with pytest.raises(ValidationError,match='двух оружиях'):
            CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,key,weapon_index=0)
    assert change(client,grenade_game,auth_headers,actor,2).status_code==400
    assert actor.action_points_current==before
    snapshot=client.get(f'/lobbies/characters/{actor.character_id}',headers=auth_headers(grenade_game['actor_user'])).json['data']
    untouched=client.put(f'/lobbies/characters/{actor.character_id}',headers=auth_headers(grenade_game['actor_user']),json={'data':snapshot})
    assert untouched.status_code==200,untouched.json
    snapshot=untouched.json['data']
    snapshot['weapons'][0]['ammo']=20
    assert client.put(f'/lobbies/characters/{actor.character_id}',headers=auth_headers(grenade_game['actor_user']),json={'data':snapshot}).status_code==400
    assert actor.character.data['weapons'][0]['ammo']==5


def test_two_knives_use_five_percent_strength_and_sum_block_sizes(client,grenade_game,auth_headers,monkeypatch):
    actor=make_pair(client,grenade_game,auth_headers,melee=True);target=grenade_game['target_location']
    target_data=deepcopy(target.character.data);target_data['health'].update(current=700,max=700)
    target.character.data=target_data;flag_modified(target.character,'data')
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:min(b,max(a,10)))
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',
        weapon_index=1,attack_type='Колющий',target_character_id=target.character_id)
    assert result['attack']['results'][0]['damage']==156  # Колющий 125, затем +25% за Силу при парном хвате.
    block=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'melee_block',action_points=1)
    assert block['character']['melee_block_effectiveness']==10  # 2 за ОД, 5 за Силу, 1+2 за размеры.


def test_second_melee_weapon_over_one_kilogram_is_rejected_before_payment(client,grenade_game,auth_headers):
    from app.services.exceptions import ValidationError
    actor=prepare(grenade_game);data=deepcopy(actor.character.data)
    data['weapons']=[{'id':'a','name':'Нож','category':'melee_weapon','weight':.5},
        {'id':'b','name':'Тяжёлый нож','category':'melee_weapon','weight':1.01}]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    assert change(client,grenade_game,auth_headers,actor,1).status_code==200
    before=actor.action_points_current
    with pytest.raises(ValidationError,match='не более 1 кг'):
        CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'draw_weapon',weapon_index=1,attribute_choice='second_hand')
    assert actor.action_points_current==before
