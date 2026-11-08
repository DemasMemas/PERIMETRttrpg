"""Мини-осциллограф не требует рук и не выдаёт чужие приборы или скрытые объекты."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.services.exceptions import ValidationError,PermissionDenied
from app.services.pda_network import snapshot
from test_pda_network import network_game,selection,read
from test_grenade_rules import grenade_game
from test_pda_access import access_game,connected,selection as access_selection


@pytest.fixture
def instruments_game(network_game):
    case=network_game;data=deepcopy(case['actor_character'].data)
    data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b6'})
    data['inventory']['pockets'].append({'id':'bag','name':'Сумка','contents':[
        {'id':'dose','name':'Дозиметр','deviceState':{'on':True,'remaining_seconds':60}},
        {'id':'detector','name':'Детектор ДА','category':'detector',
            'attributes':{'isActive':True,'avoidance_chance':25},'bonus':3}]})
    case['actor_character'].data=data;db.session.commit();return case


def test_active_own_nested_devices_no_hands_no_network_account(instruments_game,monkeypatch):
    case=instruments_game
    from app.services.map import MapService
    calls=[]
    monkeypatch.setattr(MapService,'get_tile_data',lambda *args:(calls.append(args) or {'radiation':7,'anomaly_field':{'secret':True}}))
    data=deepcopy(case['actor_character'].data);data['inventory']['pockets'][0]['pdaNetwork']={'account_id':None}
    case['actor_character'].data=data;db.session.commit()
    result=read(case)
    assert result['account'] is None
    assert result['instruments']==[{'name':'Дозиметр','kind':'dosimeter','radiation':7},
        {'name':'Детектор ДА','kind':'anomaly','avoidance_chance':25,'bonus':3}]
    assert calls==[(case['lobby'].id,0,0)]
    assert not (case['actor_character'].data.get('health') or {}).get('combatMeta',{}).get('heldPda')


def test_local_value_uses_actual_character_tile(instruments_game):
    case=instruments_game;actor=case['actor_location'];scene=case['location']
    tiles=[[{} for _ in range(scene.grid_width)] for _ in range(scene.grid_height)]
    tiles[int(actor.pos_y)][int(actor.pos_x)]={'radiation':13,'anomaly_field':{'hidden':True}}
    scene.tiles_data=tiles;db.session.commit()
    assert read(case,location_id=scene.id)['instruments'][0]['radiation']==13


@pytest.mark.parametrize('change',['missing_mod','off_dose','empty_dose','off_detector','artifact_block'])
def test_only_available_active_devices(instruments_game,change):
    case=instruments_game;data=deepcopy(case['actor_character'].data)
    pda=data['inventory']['pockets'][0];dose,detector=data['inventory']['pockets'][1]['contents']
    if change=='missing_mod':pda['modifications']=[]
    elif change=='off_dose':dose['deviceState']['on']=False
    elif change=='empty_dose':dose['deviceState']['remaining_seconds']=0
    elif change=='off_detector':detector['attributes']['isActive']=False
    else:data.setdefault('equipment',{})['armor']={'containers':[{'item':{'name':'Запрет электроники','category':'artifact',
        'attributes':{'negative_effect':'Не позволяет использовать электроприборы'}}}]}
    case['actor_character'].data=data;db.session.commit()
    result=read(case)['instruments']
    if change=='missing_mod':assert result is None
    elif change=='artifact_block':assert result==[]
    else:assert [row['kind'] for row in result]==(['dosimeter'] if change=='off_detector' else ['anomaly'])


def test_reading_does_not_consume_power_or_use_other_persons_devices(instruments_game):
    case=instruments_game;before=deepcopy(case['actor_character'].data)
    for _ in range(3):assert len(read(case)['instruments'])==2
    assert case['actor_character'].data==before
    assert read(case,'target')['instruments'] is None
    with pytest.raises(PermissionDenied):snapshot(case['actor_character'].id,case['target_user']['id'],selection(case))


@pytest.mark.parametrize('state',[{'on':False,'charge_percent':100},{'on':True,'charge_percent':0},{'broken':True,'charge_percent':100}])
def test_pda_power_required(instruments_game,state):
    case=instruments_game;data=deepcopy(case['actor_character'].data)
    data['inventory']['pockets'][0]['pdaState']=state;case['actor_character'].data=data;db.session.commit()
    with pytest.raises(ValidationError):read(case)


def test_radio_jamming_does_not_disable_local_readings(instruments_game):
    case=instruments_game;case['lobby'].weather_settings={'emission':{'enabled':True}}
    db.session.commit();view=read(case)
    assert view['source']['blocked'] and len(view['instruments'])==2


def test_hacked_pda_reads_target_instruments_not_hacker_inventory(access_game):
    from app.services.pda_access import target_snapshot
    case=access_game
    for side,name,chance in [('actor','Прибор взломщика',10),('target','Прибор цели',45)]:
        character=case[side+'_character'];data=deepcopy(character.data)
        data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b6'})
        data['inventory']['pockets'].append({'name':name,'category':'detector',
            'attributes':{'isActive':True,'avoidance_chance':chance}})
        character.data=data
    db.session.commit();connected(case)
    view=target_snapshot(case['actor_character'].id,case['actor_user']['id'],access_selection(case,session_id='access-one'))
    assert view['network']['instruments']==[{'name':'Прибор цели','kind':'anomaly','avoidance_chance':45}]
