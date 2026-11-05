"""Питание ПНВ и стерильность экземпляра через реальные сохранения и действия."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.services.portable_lights import advance,start_battle,preserve
from app.services.equipment_care import infection_multiplier
from app.services.weapon_modules import uses_nvg
from app.services.combat import CombatService
from test_quick_skill_checks import checks


def test_nvg_clock_battle_and_raised_device_share_light_consumption():
    item={'id':'nvg','slotType':'nvg','lightPower':{'uses_left':10,'elapsed_seconds':0}}
    data={'equipment':{'helmet':{'installedModules':[item]}}}
    advance(data,599);assert item['lightPower']['uses_left']==10
    advance(data,1);assert item['lightPower']['uses_left']==9
    advance(data,battle_end=True);assert item['lightPower']['uses_left']==9
    item['raised']=True;advance(data,1200);assert item['lightPower']['uses_left']==9
    item['raised']=False;start_battle(data);advance(data,battle_end=True)
    assert item['lightPower']['uses_left']==8
    advance(data,4800);assert item['lightPower']['uses_left']==0 and not uses_nvg(data)
    assert CombatService._narrative_skill_check(data,'skills.physical.shooting',darkness=True)['equipment_modifier']==-5


def test_legacy_nvg_without_id_does_not_refill_on_sheet_save():
    old={'equipment':{'helmet':{'installedModules':[{'slotType':'nvg','lightPower':{'uses_left':2,'elapsed_seconds':90}}]}}}
    incoming=deepcopy(old);incoming['equipment']['helmet']['installedModules'][0]['lightPower']['uses_left']=10
    preserve(old,incoming)
    assert incoming['equipment']['helmet']['installedModules'][0]['lightPower']['uses_left']==2


def add_gloves(checks):
    data=deepcopy(checks['actor_character'].data)
    data['equipment']['gloves']={'id':'gloves','name':'Медицинские','category':'gloves','attributes':{'sterile_duration_seconds':3600}}
    data['inventory']['pockets']=[{'id':'alcohol','name':'Спирт','category':'consumable','quantity':1,'uses':3,'maxUses':5}]
    checks['actor_character'].data=data;db.session.commit()
    return data


def save(client,checks,auth_headers,data):
    data=deepcopy(data);data['_revision']=checks['actor_character'].revision
    return client.put(f"/lobbies/characters/{checks['actor_character'].id}",headers=auth_headers(checks['actor_user']),json={'data':data})


def sterilize(client,checks,auth_headers,**extra):
    character=checks['actor_character']
    return client.post(f'/lobbies/characters/{character.id}/sterilize-gloves',headers=auth_headers(checks['actor_user']),json={
        'character_revision':character.revision,'source_id':'alcohol','path':['inventory','pockets',0],**extra})


def test_glove_first_wear_unequip_and_reequip_preserve_timer(client,checks,auth_headers):
    data=add_gloves(checks)
    first=save(client,checks,auth_headers,data);assert first.status_code==200,first.json
    data=deepcopy(checks['actor_character'].data)
    assert data['equipment']['gloves']['sterility']['remaining_seconds']==3600
    advance(data,2400);checks['actor_character'].data=deepcopy(data);db.session.commit()
    gloves=data['equipment'].pop('gloves');data['inventory']['pockets'].append(gloves)
    assert save(client,checks,auth_headers,data).status_code==200
    data=deepcopy(checks['actor_character'].data);gloves=data['inventory']['pockets'].pop()
    gloves['sterility']['remaining_seconds']=3600;data['equipment']['gloves']=gloves
    assert save(client,checks,auth_headers,data).status_code==200
    assert checks['actor_character'].data['equipment']['gloves']['sterility']['remaining_seconds']==1200
    assert infection_multiplier(checks['actor_character'].data)==.5


def test_sterilization_spends_one_use_and_three_ap_without_drinking(client,checks,auth_headers):
    data=add_gloves(checks);data['equipment']['gloves']['sterility']={'remaining_seconds':0}
    checks['actor_character'].data=data;db.session.commit()
    assert infection_multiplier(data)==1
    response=sterilize(client,checks,auth_headers)
    assert response.status_code==200,response.json
    data=response.json['data']
    assert data['inventory']['pockets'][0]['uses']==2
    assert checks['actor_location'].action_points_current==2
    assert data['equipment']['gloves']['sterility']['remaining_seconds']==3600
    assert not data['health'].get('intoxication') and infection_multiplier(data)==.5


@pytest.mark.parametrize('problem',['ap','source','revision'])
def test_invalid_sterilization_cannot_spend_resources(client,checks,auth_headers,problem):
    data=add_gloves(checks)
    if problem=='ap':checks['actor_location'].action_points_current=2
    if problem=='source':data['inventory']['pockets'][0]['name']='Вода';checks['actor_character'].data=data
    db.session.commit();before=deepcopy(checks['actor_character'].data)
    response=sterilize(client,checks,auth_headers,**({'character_revision':0} if problem=='revision' else {}))
    assert response.status_code in {400,409},response.json
    after=checks['actor_character'].data
    assert after['inventory']==before['inventory'] and after['equipment']==before['equipment']
    assert after['health']['current']==before['health']['current']
    assert after['health']['combatMeta']['actionJournal'][-1]['status']=='rejected'


def test_nvg_buttons_charge_and_battery_replacement(client,checks,auth_headers):
    character=checks['actor_character'];data=deepcopy(character.data)
    module=data['equipment']['helmet']['installedModules'][0]
    module.update(id='nvg',lightPower={'uses_left':1,'elapsed_seconds':0})
    data['inventory']['pockets']=[{'id':'battery','name':'Батарейки','quantity':2,'category':'device'}]
    character.data=data;db.session.commit()
    url=f'/lobbies/characters/{character.id}/portable-lights'
    listing=client.get(url,headers=auth_headers(checks['actor_user']))
    assert listing.json['lights'][0]['kind']=='nvg'
    response=client.post(url,headers=auth_headers(checks['actor_user']),json={'operation':'battery','path':['equipment','helmet','installedModules',0],
        'source_id':'nvg','battery_path':['inventory','pockets',0],'character_revision':character.revision})
    assert response.status_code==200,response.json
    assert response.json['data']['equipment']['helmet']['installedModules'][0]['lightPower']['uses_left']==10
    assert response.json['data']['inventory']['pockets'][0]['quantity']==1
    assert checks['actor_location'].action_points_current==3
