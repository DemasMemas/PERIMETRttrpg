"""Каталог, совместимость, серверная оплата и обслуживание протезов."""
from copy import deepcopy

import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCombatState,DeferredCombatAction
from app.services.combat import CombatService
from app.services.prostheses import advance,limb,discharge,missing_templates
from app.services.prosthesis_catalog import PROFILES
from app.services.weapon_grip import functional_arms
from test_grenade_rules import grenade_game


def setup(case,name='Навесная голень со стопой',zone='leftLeg',extent='elbow_or_knee'):
    actor=case['actor_location'];data=deepcopy(actor.character.data)
    data['health']['zones']={zone:{'current':0,'max':100}}
    data['health']['effects']=[{'id':'lost','type':'amputation','area':zone,'active':True,'loss_extent':extent}]
    data['inventory']={'pockets':[{'id':'prosthesis-test','name':name,'quantity':1,
        'attributes':{'prosthesis':deepcopy(PROFILES[name])}}],'backpack':[]}
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit();return actor


def outside(case):
    state=LocationCombatState.query.filter_by(location_id=case['actor_location'].location_id).one();state.status='idle';db.session.commit()


def operation(client,case,headers,**payload):
    actor=case['actor_location']
    return client.post(f'/lobbies/characters/{actor.character_id}/prostheses',headers=headers(case['actor_user']),
        json={'character_revision':actor.character.revision,'zone':'leftLeg','path':['inventory','pockets',0],**payload})


def test_catalog_has_twenty_profiles_and_preserves_existing():
    entries=missing_templates([])
    assert len(entries)==20 and len({item['id'] for item in entries})==20
    assert len(missing_templates(entries))==0
    assert PROFILES['Кисть крюк']['attack']['types']==['Колющий','Вспарывающий']
    assert PROFILES['Кисть клинок']['attack']['damage']==85
    assert all(item['weight'] is None and item['volume'] is None for item in entries)


def test_outside_install_power_replaces_missing_leg_penalty_and_preserves_instance(client,grenade_game,auth_headers):
    actor=setup(grenade_game);outside(grenade_game)
    assert CombatService._disabled_limb_penalties(actor.character.data)['movement']==6
    assert operation(client,grenade_game,auth_headers,operation='install').status_code==200
    assert not actor.character.data['inventory']['pockets']
    assert limb(actor.character.data,'leftLeg') is None
    data=deepcopy(actor.character.data);data['inventory']['pockets']=[{'name':'Батарейка','quantity':2}]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    result=operation(client,grenade_game,auth_headers,operation='power',slot='base')
    assert result.status_code==200,result.json
    assert limb(actor.character.data,'leftLeg')['efficiency']==-3
    assert CombatService._disabled_limb_penalties(actor.character.data)['movement']==3
    assert not CombatService._disabled_limb_penalties(actor.character.data)['sprint_blocked']
    assert operation(client,grenade_game,auth_headers,operation='remove').status_code==200
    assert actor.character.data['inventory']['pockets'][-1]['id']=='prosthesis-test'
    assert CombatService._disabled_limb_penalties(actor.character.data)['movement']==6


@pytest.mark.parametrize('extent',['entire_limb','hand_or_foot',None])
def test_wrong_extent_cannot_install_or_consume(client,grenade_game,auth_headers,extent):
    actor=setup(grenade_game,extent=extent);outside(grenade_game)
    response=operation(client,grenade_game,auth_headers,operation='install')
    assert response.status_code==400
    assert actor.character.data['inventory']['pockets'][0]['id']=='prosthesis-test'


def test_strap_install_combat_cost_and_remove(client,grenade_game,auth_headers):
    actor=setup(grenade_game);before=actor.action_points_current
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'prosthesis',
        equipment_operation='install',equipment_slot='leftLeg',item_path=['inventory','pockets',0])
    assert actor.action_points_current==before-10 and result['equipment_change']['action_points']==10
    CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'prosthesis',equipment_operation='remove',equipment_slot='leftLeg')
    assert actor.action_points_current==before-13


def prosthetic_data(name='Навесная голень со стопой',zone='leftLeg',**changes):
    item={'id':'maintenance','name':name,'durability':PROFILES[name]['durability'],'energy_seconds':86400,**changes}
    return {'health':{'effects':[{'type':'amputation','area':zone,'active':True}],
        'prostheses':{zone:{'base':item}}}}


def test_power_and_missed_lubrications_accumulate_and_never_repair():
    data=prosthetic_data();item=data['health']['prostheses']['leftLeg']['base']
    advance(data,86400)
    assert item['energy_seconds']==0 and limb(data,'leftLeg') is None
    advance(data,6*86400)
    assert item['missed_lubrications']==1 and item['durability']==25
    advance(data,7*86400)
    assert item['missed_lubrications']==2 and item['durability']==0 and item['broken']


def test_clock_rewind_cannot_restore_charge_or_lubrication():
    data=prosthetic_data(energy_seconds=42,lubrication_elapsed=500);before=deepcopy(data)
    assert not advance(data,-3600)
    assert data==before


def test_electric_discharge_preserves_durability_and_respects_shielding():
    data=prosthetic_data();item=data['health']['prostheses']['leftLeg']['base']
    assert discharge(data) and item['energy_seconds']==0 and item['durability']==75
    shielded=prosthetic_data('Интегрированный протез голени')
    assert not discharge(shielded)
    assert shielded['health']['prostheses']['leftLeg']['base']['energy_seconds']==86400


def test_base_and_hand_efficiency_and_functional_arm():
    data=prosthetic_data('Навесная база предплечья','leftArm')
    parts=data['health']['prostheses']['leftArm']
    assert limb(data,'leftArm') is None and functional_arms(data)==1
    parts['hand']={'name':'Кисть крюк','durability':25}
    assert limb(data,'leftArm')['efficiency']==-9 and functional_arms(data)==2
    assert CombatService._health_roll_modifier(data,'skills.other.medicine')==-9


def test_sheet_cannot_forge_installed_prostheses(client,grenade_game,auth_headers):
    actor=setup(grenade_game);outside(grenade_game)
    assert operation(client,grenade_game,auth_headers,operation='install').status_code==200
    url=f'/lobbies/characters/{actor.character_id}'
    snapshot=client.get(url,headers=auth_headers(grenade_game['actor_user'])).json['data']
    snapshot['health']['prostheses']={}
    result=client.put(url,headers=auth_headers(grenade_game['actor_user']),json={'data':snapshot})
    assert result.status_code==200,result.json
    assert actor.character.data['health']['prostheses']['leftLeg']['base']['id']=='prosthesis-test'


def test_ten_ap_strap_installation_waits_for_full_payment(client,grenade_game,auth_headers):
    actor=setup(grenade_game);actor.action_points_current=1;db.session.commit()
    gm=grenade_game['lobby'].gm_id
    result=CombatService.perform_action(actor.location_id,gm,actor.id,'prosthesis',equipment_operation='install',
        equipment_slot='leftLeg',item_path=['inventory','pockets',0],pending_action_id='prosthesis-debt')
    assert result['pending_action'] and not actor.character.data['health'].get('prostheses')
    for _ in range(6):
        row=db.session.get(DeferredCombatAction,'prosthesis-debt')
        if row.status=='ready':break
        state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
        CombatService.end_turn(actor.location_id,gm,state.current_location_character_id)
    assert row.status=='ready' and not actor.character.data['health'].get('prostheses')
    CombatService.perform_action(actor.location_id,gm,actor.id,'prosthesis',resume_pending_action_id=row.id)
    assert actor.character.data['health']['prostheses']['leftLeg']['base']['id']=='prosthesis-test'
    assert row.status=='completed'


def test_lubrication_takes_five_minutes_and_does_not_repair_missed_cycles(client,grenade_game,auth_headers):
    actor=setup(grenade_game);outside(grenade_game)
    assert operation(client,grenade_game,auth_headers,operation='install').status_code==200
    data=deepcopy(actor.character.data)
    item=data['health']['prostheses']['leftLeg']['base'];item['missed_lubrications']=1;item['durability']=25
    item['lubrication_elapsed']=600
    data['inventory']['pockets']=[{'name':'Набор смазочных приспособлений','quantity':1}]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    result=operation(client,grenade_game,auth_headers,operation='lubricate',slot='base')
    assert result.status_code==200,result.json
    item=actor.character.data['health']['prostheses']['leftLeg']['base']
    assert item['lubrication_remaining']==300 and not actor.character.data['inventory']['pockets']
    advance(actor.character.data,299)
    assert item['lubrication_remaining']==1 and item['lubrication_elapsed']==899
    advance(actor.character.data,1)
    assert item['lubrication_elapsed']==0 and item['durability']==25 and item['missed_lubrications']==1


def test_hook_attack_uses_own_accuracy_and_never_scales_from_strength():
    from app.services.prostheses import sync_weapons
    data=prosthetic_data('Навесная база предплечья','leftArm')
    data['health']['prostheses']['leftArm']['hand']={'id':'hook','name':'Кисть крюк','durability':25}
    sync_weapons(data)
    weapon=data['weapons'][0];profile=CombatService._weapon_damage_profile(weapon,'Колющий')
    assert profile['accuracy']==-2 and profile['skip_strength_scaling']
    assert weapon['attributes']['prosthesis_efficiency']==-5
    data['health']['prostheses']['leftArm']['base']['energy_seconds']=0
    sync_weapons(data)
    assert not data['weapons']


def test_real_hook_attack_replaces_hand_penalty_and_ignores_strength(grenade_game,monkeypatch):
    from app.services.prostheses import sync_weapons
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    data=prosthetic_data('Навесная база предплечья','leftArm')
    data['health'].update(current=700,max=700,painLevel=0,stress=0,exhaustion=0)
    data['health']['prostheses']['leftArm']['hand']={'id':'combat-hook','name':'Кисть крюк','durability':25}
    data['skills']={'physical':{'strength':{'base':40},'melee':{'base':20}}}
    sync_weapons(data);actor.character.data=data;actor.action_points_current=20
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y;flag_modified(actor.character,'data');db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:min(b,max(a,15)))
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',
        weapon_index=0,attack_type='Колющий',target_character_id=target.character_id)
    assert result['attack']['results'][0]['hit']
    assert result['attack']['results'][0]['damage']==62
    assert result['attack']['hit_difficulty']==13  # 12 − (5 − 4 основы − 5 кисти + 5 замены) − (−2 точности).
