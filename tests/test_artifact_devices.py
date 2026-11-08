"""Надетые артефакты запрещают работу объявленных приборов, а не хранение."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from app.services.artifact_effects import device_block_reason,artifact_passive_profile
from app.services.world_rules import artifact_catalog
from app.services.electronics import switch,active,preserve
from app.services.exceptions import ValidationError
from app.services.weapon_modules import uses_nvg
from app.services.pda_network import blocked
from app.services.pda_devices import powered
from app.services.portable_lights import walk
from app.extensions import db
from test_grenade_rules import grenade_game
from test_item_controllers import controller_game


def equipped(name):
    rule=next(row for row in artifact_catalog() if row['name']==name)
    return {'health':{},'equipment':{'armor':{'containers':[{'item':{'id':'device-artifact',
        'category':'artifact','name':name,'attributes':deepcopy(rule)}}]}}}


def instrument(name,kind=None,category='device'):
    return {'id':'checked-device','name':name,'category':category,'attributes':{'type':kind} if kind else {}}


@pytest.mark.parametrize('artifact,compass,radio,detector',[
    ('Металлическая стружка',True,False,False),('Полярный осколок',True,True,False),('Ферромагнит',True,True,True),
])
def test_exact_declared_instrument_restrictions_and_unequip(artifact,compass,radio,detector):
    data=equipped(artifact)
    for item,expected in [(instrument('Компас'),compass),(instrument('Рация','radio'),radio),
                          (instrument('Детектор аномалий','anomaly','detector'),detector)]:
        assert bool(device_block_reason(data,item)) is expected
    assert not device_block_reason(data,instrument('КПК')),'Запрет обычной рации не объявлен запретом КПК'
    data['inventory']={'pockets':[data['equipment']['armor']['containers'][0]['item']]};data['equipment']={}
    assert not artifact_passive_profile(data)['blocked_devices']
    assert not device_block_reason(data,instrument('Компас'))


@pytest.mark.parametrize('artifact',['Громоотвод','Железное сердце','Проводник бури','Полюс'])
def test_electrical_instruments_blocked_but_chemical_light_and_oxygen_remain_usable(artifact):
    data=equipped(artifact)
    for name in ['Фонарик','Улучшенный фонарик','Налобный фонарик','КПК','Дозиметр']:
        assert device_block_reason(data,instrument(name))
    for name in ['Компас','Кислородный баллон','Светящаяся палочка','ХИС']:
        assert not device_block_reason(data,instrument(name))


def test_generic_toggle_rejected_before_power_consumption_and_switch_off_allowed():
    data=equipped('Ферромагнит');item=instrument('Детектор аномалий','anomaly','detector')
    item['installedModules']=[{'slotType':'battery','attributes':{'power':75}}];before=deepcopy(item)
    with pytest.raises(ValidationError,match='Ферромагнит'):switch(item,data,True)
    assert item==before
    item['attributes']['isActive']=True;switch(item,data,False)
    assert item['attributes']['isActive'] is False
    assert item['installedModules'][0]['attributes']['power']==75


def test_ordinary_sheet_save_cannot_keep_forbidden_detector_active():
    data=equipped('Ферромагнит');item=instrument('Детектор','anomaly','detector');item['attributes']['isActive']=True
    data['inventory']={'pockets':[item]};preserve({},data)
    assert data['inventory']['pockets'][0]['attributes']['isActive'] is False


def test_existing_powered_dosimeter_and_nvg_stop_providing_effect_then_resume_on_unequip():
    data=equipped('Громоотвод');dose=instrument('Дозиметр');dose['deviceState']={'on':True,'remaining_seconds':100}
    data['inventory']={'pockets':[dose]};data['equipment']['helmet']={'installedModules':[{
        'name':'ПНВ','slotType':'nvg','active':True,'enabled':True,'lightPower':{'uses_left':10}}]}
    assert not active(data) and not uses_nvg(data)
    data['equipment']['armor']['containers']=[]
    assert active(data) and uses_nvg(data)


def test_pda_transmission_and_hacking_power_cannot_bypass_own_artifact_even_with_radio_immunity():
    data=equipped('Громоотвод');pda=instrument('КПК');pda['pdaState']={'on':True,'charge_percent':100}
    character=SimpleNamespace(data=data)
    device={'character':character,'item':pda,'mods':{'pda-b5'}}
    assert 'Громоотвод' in blocked(device,[])
    assert not powered(device)
    data['equipment']={}
    assert blocked(device,[]) is None and powered(device)


def test_light_http_refusal_does_not_spend_ap_or_charge_and_can_still_be_switched_off(client,grenade_game,auth_headers):
    from test_portable_lights import install,operate
    from app.services.portable_lights import ready_sources
    case=grenade_game;actor=install(case['actor_location'],on=True)
    data=deepcopy(actor.character.data);data['equipment']=equipped('Громоотвод')['equipment'];actor.character.data=data;db.session.commit()
    before=actor.action_points_current
    assert not ready_sources(actor)
    assert operate(client,case,auth_headers,'toggle').status_code==200
    stopped=deepcopy(actor.character.data);points=actor.action_points_current
    assert points==before-1
    response=operate(client,case,auth_headers,'toggle')
    assert response.status_code==400 and 'Громоотвод' in response.json['error']['message']
    assert actor.action_points_current==points
    actual=deepcopy(actor.character.data);expected=deepcopy(stopped)
    journal=actual['health']['combatMeta'].pop('actionJournal')
    previous_journal=expected['health']['combatMeta'].pop('actionJournal')
    assert len(journal)==len(previous_journal)+1 and journal[-1]['action_points']==0
    assert actual==expected
    data=deepcopy(stopped);data['equipment']['armor']['containers']=[];actor.character.data=data;db.session.commit()
    assert operate(client,case,auth_headers,'toggle').status_code==200
    assert ready_sources(actor)


def test_electronic_weapon_laser_does_not_grant_accuracy_when_blocked():
    from app.services.weapon_modules import conditional_accuracy
    data=equipped('Громоотвод');weapon={'installedModules':[{'name':'ЛЦУ','attributes':{'laser':True}}]}
    kwargs={'fire_mode':'unaimed','distance':5,'posture':'standing'}
    assert conditional_accuracy(weapon,**kwargs)==1
    assert conditional_accuracy(weapon,character_data=data,**kwargs)==0


def test_artifact_interrupts_existing_dynamo_charge_without_restoring_charge():
    from app.services.portable_lights import advance_pda
    data=equipped('Громоотвод');item=instrument('КПК');item['pdaState']={'on':False,'charge_percent':50}
    item['modifications']=[{'ruleId':'pda-b7'}]
    data['inventory']={'pockets':[item]};data['health']['combatMeta']={'pdaDynamo':{'id':item['id'],'elapsed_seconds':30}}
    advance_pda(data,60)
    assert item['pdaState']['charge_percent']==50
    assert 'pdaDynamo' not in data['health']['combatMeta']


def test_player_http_loses_dosimeter_access_and_cannot_register_until_artefact_removed(client,grenade_game,auth_headers):
    from app.models import PdaAccount
    case=grenade_game;target=case['target_location'];data=equipped('Громоотвод')
    pda=instrument('КПК');pda['pdaState']={'on':True,'charge_percent':100}
    dose=instrument('Дозиметр');dose['id']='active-dose';dose['deviceState']={'on':True,'remaining_seconds':100}
    data['inventory']={'pockets':[pda,dose]};target.character.data=data;db.session.commit()
    headers=auth_headers(case['target_user'])
    dose_url=f"/lobbies/{case['lobby'].id}/radiation-access?character_id={target.character_id}&location_id={target.location_id}"
    assert client.get(dose_url,headers=headers).json['can_read'] is False
    account_url=f'/lobbies/characters/{target.character_id}/pda-account/register'
    payload={'item_path':['inventory','pockets',0],'item_id':pda['id'],'login':'artifact-user','password':'synthetic-device-password'}
    response=client.post(account_url,headers=headers,json=payload)
    assert response.status_code==400 and 'Громоотвод' in response.json['error']['message']
    assert PdaAccount.query.count()==0
    data=deepcopy(target.character.data);data['equipment']={};target.character.data=data;db.session.commit()
    assert client.get(dose_url,headers=headers).json['can_read'] is True
    response=client.post(account_url,headers=headers,json=payload)
    assert response.status_code==200,response.json
    assert PdaAccount.query.count()==1


def test_forbidden_timer_cannot_be_reconfigured_but_wire_disarm_remains_available(controller_game):
    from test_item_controllers import install,locate,write
    case=controller_game;identity=install(case,'timer',seconds=60)['attachment_id']
    character=case['actor_character'];data=deepcopy(character.data);data['equipment']=equipped('Громоотвод')['equipment']
    character.data=data;db.session.commit()
    path,item=next((path,item) for path,item in walk(data,True)
        if item.get('controllerState',{}).get('id')==identity)
    original=deepcopy(character.data['inventory'])
    with pytest.raises(ValidationError,match='Громоотвод'):
        write(case,'timer',item_path=path,item_id=item['id'],seconds=30)
    assert character.data['inventory']==original
    write(case,'disarm',item_path=path,item_id=item['id'])
    assert locate(case,item['id'])[1]['controllerState']['disconnected'] is True


def test_ground_pda_radio_does_not_require_character_data():
    from app.services.pda_devices import radio_device
    item=instrument('КПК');item['pdaState']={'on':True,'charge_percent':100}
    record={'character':None,'host':'object:1','lobby_id':1,
        'lobby':SimpleNamespace(weather_settings={}), 'item':item,'mods':set()}
    assert powered(record)
    assert blocked(radio_device(record),[]) is None
