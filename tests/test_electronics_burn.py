"""Сгорание приборов не превращается в разряд, снимаемый заменой батарейки."""
from copy import deepcopy
import pytest
from app.services.combat import CombatService as C
from app.services.electronics import burned,electric_protection,preserve,switch
from app.services.artifact_effects import device_block_reason
from app.services.exceptions import ValidationError
from test_grenade_rules import grenade_game


def devices():
    return [{'id':'lamp','name':'Фонарик'}, {'id':'head','name':'Налобный фонарик'},
        {'id':'dose','name':'Дозиметр','deviceState':{'on':True,'remaining_seconds':600}},
        {'id':'detector','name':'ДА-2','category':'detector','attributes':{'isActive':True}},
        {'id':'nvg','name':'ПНВ','slotType':'nvg','active':True,'enabled':True},
        {'id':'radio','name':'Рация','attributes':{'isActive':True,'type':'radio'}}]


@pytest.mark.parametrize('damage,expected',[(0,False),(200,False),(200.01,True),(500,True)])
def test_electric_threshold_for_every_listed_device_and_nested_inventory(grenade_game,damage,expected):
    physical=devices();data={'inventory':{'pockets':[{'id':'bag','name':'Сумка','contents':physical}]}}
    result=C._apply_anomaly_equipment_damage(data,damage,{'category':'electric'})
    assert all(burned(item)==expected for item in physical)
    assert len(result)==(6 if expected else 0)
    if expected:
        assert not physical[2]['deviceState']['on'] and not physical[3]['attributes']['isActive']
        assert not physical[4]['active'] and not physical[4]['enabled']
        assert not physical[5]['attributes']['isActive']
        assert C._apply_anomaly_equipment_damage(data,500,{'category':'electric'})==[]


def test_non_electric_damage_does_not_burn_devices(grenade_game):
    data={'inventory':{'pockets':devices()}}
    C._apply_anomaly_equipment_damage(data,1000,{'category':'thermal'})
    assert not any(burned(item) for item in data['inventory']['pockets'])


def test_improved_lamp_and_explicit_anomaly_immunity_survive(grenade_game):
    items=[{'id':'immune-light','name':'Улучшенный фонарик'},
        {'id':'immune-detector','name':'ДА','category':'detector','attributes':{'anomaly_immunities':['electrical','chemical']}}]
    data={'inventory':{'pockets':items}}
    assert all(electric_protection(data,item) for item in items)
    assert C._apply_anomaly_equipment_damage(data,1000,{'category':'electric'})==[]


def test_worn_insulation_prevents_burn_but_carried_or_disabled_does_not(grenade_game):
    armor={'category':'armor','modifications':[{'ruleId':'armor-d14'}]}
    data={'equipment':{'armor':armor},'inventory':{'pockets':devices()}}
    assert C._apply_anomaly_equipment_damage(data,1000,{'category':'electric'})==[]
    armor['protectionDisabled']=True
    assert len(C._apply_anomaly_equipment_damage(data,1000,{'category':'electric'}))==6
    data={'inventory':{'pockets':[deepcopy(armor),*devices()]}}
    C._apply_anomaly_equipment_damage(data,1000,{'category':'electric'})
    assert all(burned(item) for item in data['inventory']['pockets'][1:])


def test_burn_persists_after_sheet_forgery_and_cannot_be_attached_to_new_item(grenade_game):
    data={'inventory':{'pockets':devices()}}
    C._apply_anomaly_equipment_damage(data,500,{'category':'electric'})
    incoming=deepcopy(data)
    for item in incoming['inventory']['pockets']:item.pop('electronicsState',None)
    incoming['inventory']['pockets'][3]['attributes']['isActive']=True
    incoming['inventory']['pockets'].append({'id':'forged','name':'Фонарик','electronicsState':{'burned':True}})
    preserve(data,incoming)
    assert all(burned(item) for item in incoming['inventory']['pockets'][:-1])
    assert not incoming['inventory']['pockets'][3]['attributes']['isActive']
    assert not burned(incoming['inventory']['pockets'][-1])


def test_real_switch_and_repair_refuse_burned_device_without_spending():
    from app.services.equipment_repair import repair_equipment
    data={'inventory':{'pockets':[{'name':'Инструмент','uses':5,'attributes':{'repair_profile':{'kind':'restore_tool','restore_fraction':.5}}},
        {'id':'radio','name':'Рация','category':'tool','uses':0,'maxUses':10,'electronicsState':{'burned':True}}]}}
    before=deepcopy(data);target=data['inventory']['pockets'][1]
    assert 'сгорел' in device_block_reason(data,target)
    with pytest.raises(ValidationError):switch(target,data,True)
    with pytest.raises(ValueError,match='не подлежит ремонту'):repair_equipment(data,['inventory','pockets',0],['inventory','pockets',1])
    assert data==before


def test_belt_stored_helmet_nvg_burn_and_preserve(grenade_game):
    module={'id':'belt-nvg','name':'ПНВ','slotType':'nvg','active':True}
    data={'equipment':{'belt':{'storedItem':{'savedAttributes':{'id':'helmet','installedModules':[module]}}}}}
    result=C._apply_anomaly_equipment_damage(data,201,{'category':'electric'})
    assert len(result)==1 and burned(module) and module['active'] is False
    incoming=deepcopy(data);nvg=incoming['equipment']['belt']['storedItem']['savedAttributes']['installedModules'][0]
    nvg.pop('electronicsState');preserve(data,incoming);assert burned(nvg)


def test_prostheses_only_discharge_and_mechanical_items_survive(grenade_game):
    prosthesis={'name':'Навесная база предплечья','energy_seconds':600,'durability':50}
    data={'health':{'prostheses':{'leftArm':{'base':prosthesis}}},'inventory':{'pockets':[
        {'name':'Компас','attributes':{'device_kind':'compass'}},{'name':'ХИС'},{'name':'Лопата'}]}}
    C._apply_anomaly_equipment_damage(data,250,{'category':'electric'})
    assert prosthesis['energy_seconds']==0 and prosthesis['durability']==50 and not burned(prosthesis)
    assert not any(burned(item) for item in data['inventory']['pockets'])


@pytest.mark.parametrize('index,operation',[(0,'battery'),(2,'dosimeter_battery')])
def test_replacing_battery_cannot_revive_burned_device_or_spend_ap(client,grenade_game,auth_headers,index,operation):
    from app.extensions import db
    case=grenade_game;actor=case['actor_location'];data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[*devices(),{'id':'battery','name':'Батарейки','quantity':2}]
    C._apply_anomaly_equipment_damage(data,250,{'category':'electric'})
    actor.character.data=data;db.session.commit();before=deepcopy(actor.character.data);points=actor.action_points_current
    response=client.post(f'/lobbies/characters/{actor.character_id}/portable-lights',headers=auth_headers(case['actor_user']),json={
        'operation':operation,'path':['inventory','pockets',index],'source_id':data['inventory']['pockets'][index]['id'],
        'battery_path':['inventory','pockets',6],'character_revision':actor.character.revision})
    assert response.status_code==400 and 'сгорел' in response.json['error']['message']
    assert actor.character.data['inventory']==before['inventory'] and actor.action_points_current==points
    assert actor.character.data['health']['combatMeta']['actionJournal'][-1]['action_points']==0


@pytest.mark.parametrize('fraction,protection,expected_damage,expected_burn',[(.8,0,200,False),(.5,0,125,False),(1,50,125,True)])
def test_threshold_uses_actual_exposure_before_body_protection(app,fraction,protection,expected_damage,expected_burn):
    from test_combat_anomalies import _combat_with_anomaly
    _,scene,character,actor,_=_combat_with_anomaly('akkumulyator')
    character.data={**character.data,'inventory':{'pockets':[{'id':'lamp','name':'Фонарик'}]},
        'equipment':{'armor':{'category':'armor','protection':{'electric':protection}}}}
    active=C._enter_anomaly(actor,C._anomalies_at_tile(scene,2,2)[0],1)
    result=C._apply_anomaly_exposure(actor,active,fraction,round_number=1)
    assert result['damage']==expected_damage
    assert burned(character.data['inventory']['pockets'][0])==expected_burn
