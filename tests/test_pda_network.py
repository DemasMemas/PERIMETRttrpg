"""Направленная связь КПК, маска телеметрии и подавление радиосигнала."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import PdaAccount,PdaAccountLogin,PdaContact,PdaTransmission,WorldGroup,LocationCombatState,Lobby
from app.services.pda_network import snapshot,operate,registry,blocked
from app.services.exceptions import ValidationError,ConflictError,PermissionDenied,NotFoundError
from test_grenade_rules import grenade_game


@pytest.fixture
def network_game(grenade_game):
    pair=grenade_game
    LocationCombatState.query.filter_by(location_id=pair['location'].id).one().status='idle'
    for side,x in [('actor',0),('target',7)]:
        character=pair[side+'_character'];data=deepcopy(character.data)
        data['inventory']['pockets']=[{'id':side+'-pda','name':'КПК','category':'misc','quantity':1,
            'pdaState':{'on':True,'charge_percent':100},'modifications':[{'ruleId':'pda-b10'}] if side=='actor' else []}]
        character.data=data
        group=WorldGroup(lobby_id=pair['lobby'].id,name=side,tile_x=x,tile_y=0,member_character_ids=[character.id],created_by=character.owner_id)
        account=PdaAccount(lobby_id=character.lobby_id,login=side,login_key=side,password_hash='synthetic')
        db.session.add_all([account,group]);db.session.flush();db.session.add(PdaAccountLogin(character_id=character.id,account_id=account.id))
        pair[side+'_account']=account;pair[side+'_group']=group
    db.session.commit();return pair


def selection(pair,side='actor',**extra):return {'item_path':['inventory','pockets',0],'item_id':side+'-pda',**extra}


def read(pair,side='actor',**extra):return snapshot(pair[side+'_character'].id,pair[side+'_user']['id'],selection(pair,side,**extra))


def write(pair,side='actor',**extra):return operate(pair[side+'_character'].id,pair[side+'_user']['id'],selection(pair,side,**extra))


def test_enhanced_antenna_is_directional_and_messages_arrive_only_inside_sender_range(network_game):
    pair=network_game
    ordinary=read(pair,'target');assert len(ordinary['signals'])==1 and ordinary['signals'][0]['can_send'] is False
    assert read(pair)['signals']==[]
    _,sent=write(pair,operation='send',operation_id='long-outgoing',target_account_id=pair['target_account'].id,text='Дальний сигнал')
    assert sent['replayed'] is False
    inbox=read(pair,'target')['messages'];assert inbox[0]['text']=='Дальний сигнал' and not inbox[0]['outgoing']
    write(pair,'target',operation='send',operation_id='short-answer',target_account_id=pair['actor_account'].id,text='Ответ')
    assert len(read(pair)['messages'])==1
    pair['target_group'].tile_x=3;db.session.commit()
    write(pair,'target',operation='send',operation_id='three-cells',target_account_id=pair['actor_account'].id,text='На границе')
    assert len(read(pair)['messages'])==2


def test_mask_hides_signal_and_coordinates_even_from_triangulation(network_game):
    pair=network_game
    actor=pair['actor_character'];target=pair['target_character']
    data=deepcopy(actor.data);data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b9'});actor.data=data
    data=deepcopy(target.data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b2'}];target.data=data
    db.session.commit()
    assert read(pair,'target')['signals']==[]
    write(pair,operation='send',operation_id='masked-send',text='Сообщение без координат')
    assert read(pair,'target')['messages'][0]['text']=='Сообщение без координат'


def test_jammer_has_25_meter_radius_shielding_and_60_second_battery(network_game):
    from app.services.portable_lights import advance
    pair=network_game;first=pair['actor_location'];last=pair['target_location']
    first.pos_x,first.pos_y=0,0;last.pos_x,last.pos_y=25,0
    data=deepcopy(first.character.data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b16'}];first.character.data=data
    db.session.commit()
    write(pair,operation='jammer',enabled=True,character_revision=first.character.revision,location_id=first.location_id)
    assert read(pair,'target',location_id=last.location_id)['source']['blocked']=='Сигнал подавлен глушилкой'
    last.pos_x=25.01;db.session.commit();assert not read(pair,'target',location_id=last.location_id)['source']['blocked']
    last.pos_x=10;data=deepcopy(last.character.data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b5'}];last.character.data=data;db.session.commit()
    assert not read(pair,'target',location_id=last.location_id)['source']['blocked']
    data=deepcopy(first.character.data);advance(data,60)
    assert data['inventory']['pockets'][0]['pdaState']['charge_percent']==0
    assert data['inventory']['pockets'][0]['pdaNetwork']['jammer_on'] is False


def test_retry_and_changed_message_do_not_duplicate_or_reuse_receipt(network_game):
    pair=network_game;request={'operation':'send','operation_id':'same-message','text':'Один раз'}
    write(pair,**request);_,replayed=write(pair,**request);assert replayed['replayed'] and PdaTransmission.query.count()==1
    with pytest.raises(ConflictError):write(pair,**{**request,'text':'Подмена'})


def test_network_privacy_and_contacts_do_not_cross_worlds(network_game):
    pair=network_game
    from app.services.pda_accounts import account_status
    with pytest.raises(PermissionDenied):account_status(pair['actor_character'].id,pair['target_user']['id'])
    with pytest.raises(PermissionDenied):snapshot(pair['actor_character'].id,pair['target_user']['id'],selection(pair))
    write(pair,operation='contact',login='target',friendly=True)
    assert read(pair)['contacts'][0]['friendly'] and read(pair,'target')['contacts']==[]
    other=Lobby(name='Другой мир',gm_id=pair['lobby'].gm_id,invite_code='OTHER-PDA');db.session.add(other);db.session.flush()
    account=PdaAccount(lobby_id=other.id,login='other-world',login_key='other-world',password_hash='synthetic');db.session.add(account);db.session.commit()
    with pytest.raises(NotFoundError):write(pair,operation='contact',login='other-world')
    with pytest.raises(NotFoundError):write(pair,operation='send',operation_id='cross-world',text='Нет',target_account_id=account.id)
    write(pair,operation='remove_contact',target_account_id=pair['target_account'].id);assert not read(pair)['contacts']


def test_offline_device_does_not_receive_new_messages_but_old_delivery_is_kept(network_game):
    pair=network_game;write(pair,operation='send',operation_id='before-off',text='До выключения')
    target=pair['target_character'];data=deepcopy(target.data);data['inventory']['pockets'][0]['pdaState']['on']=False;target.data=data;db.session.commit()
    write(pair,operation='send',operation_id='while-off',text='Во время выключения')
    data=deepcopy(target.data);data['inventory']['pockets'][0]['pdaState']['on']=True;target.data=data;db.session.commit()
    assert [m['text'] for m in read(pair,'target')['messages']]==['До выключения']


def test_triangulation_shows_only_received_signal_position(network_game):
    pair=network_game;target=pair['target_character'];data=deepcopy(target.data)
    data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b2'}];target.data=data;db.session.commit()
    signal=read(pair,'target')['signals'][0]
    assert signal['position']=={'tile_x':0,'tile_y':0}
    assert 'character_id' not in signal


def test_network_state_cannot_be_changed_by_saving_character(network_game):
    from app.services.portable_lights import preserve
    pair=network_game;current=pair['actor_character'].data;incoming=deepcopy(current)
    incoming['inventory']['pockets'][0]['pdaNetwork']={'jammer_on':True};preserve(current,incoming)
    assert 'pdaNetwork' not in incoming['inventory']['pockets'][0]


@pytest.mark.parametrize('enhanced,cells,delivered',[(False,3,True),(False,4,False),(True,10,True),(True,11,False)])
def test_sender_range_boundaries_in_world_cells(network_game,enhanced,cells,delivered):
    pair=network_game;actor=pair['actor_character'];data=deepcopy(actor.data)
    data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b10'}] if enhanced else []
    actor.data=data;pair['target_group'].tile_x=cells;db.session.commit()
    write(pair,operation='send',operation_id='boundary',text='Граница')
    assert bool(read(pair,'target')['messages'])==delivered


def test_shielding_ignores_storm_and_jammer_costs_one_ap_in_combat(network_game):
    from app.services.combat import CombatService
    pair=network_game;actor=pair['actor_location'];data=deepcopy(actor.character.data)
    data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b16'},{'ruleId':'pda-b5'}];actor.character.data=data
    pair['lobby'].weather_settings={'rain':{'enabled':True,'intensity':.7}};db.session.commit()
    assert read(pair)['source']['blocked'] is None
    assert read(pair,'target')['source']['blocked']=='Сигнал подавлен погодой'
    CombatService.start_combat(actor.location_id,pair['lobby'].gm_id,[actor.id],actor.id)
    before=actor.action_points_current
    write(pair,operation='jammer',enabled=True,character_revision=actor.character.revision,location_id=actor.location_id)
    assert actor.action_points_current==before-1
    write(pair,operation='jammer',enabled=True,character_revision=actor.character.revision,location_id=actor.location_id)
    assert actor.action_points_current==before-1


def test_network_http_and_schema_migration(network_game,client,auth_headers):
    import json,importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.models import PdaDelivery
    pair=network_game;url=f"/lobbies/characters/{pair['actor_character'].id}/pda-network"
    response=client.get(url,headers=auth_headers(pair['actor_user']),query_string={
        'item_path':json.dumps(['inventory','pockets',0]),'item_id':'actor-pda'})
    assert response.status_code==200 and response.json['source']['transmit_range']==1000
    identity=pair['actor_account'].id
    for table in (PdaDelivery.__table__,PdaTransmission.__table__,PdaContact.__table__):table.drop(db.session.connection())
    path=Path(__file__).resolve().parents[1]/'migrations/versions/a7d49e2f510b_add_pda_network.py'
    spec=importlib.util.spec_from_file_location('network_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    migration.op=Operations(MigrationContext.configure(db.session.connection()));migration.upgrade()
    assert db.session.get(PdaAccount,identity).login=='actor'
    assert PdaTransmission.query.count()==0 and PdaContact.query.count()==0


def test_message_order_follows_transmission_time_instead_of_random_uuid(network_game):
    pair=network_game
    for identity,text in [('z-message','Первое'),('a-message','Второе'),('b-message','Третье')]:
        write(pair,operation='send',operation_id=identity,text=text)
    assert [message['text'] for message in read(pair,'target')['messages']]==['Первое','Второе','Третье']


def test_proximity_requires_mod_and_ignores_friends_masked_and_unreachable(network_game):
    from app.services.pda_network import proximity
    pair=network_game;actor=pair['actor_character'];user=pair['actor_user']['id']
    assert proximity(actor.id,user)['devices']==[]
    data=deepcopy(actor.data);data['inventory']['pockets'][0]['modifications'].extend([{'ruleId':'pda-b8'},{'ruleId':'pda-b2'}]);actor.data=data
    pair['target_group'].tile_x=2;db.session.commit()
    result=proximity(actor.id,user)['devices'][0]
    assert result['sound'] and len(result['signals'])==1 and set(result['signals'][0])=={'id','login'}
    write(pair,operation='contact',login='target',friendly=True)
    assert proximity(actor.id,user)['devices'][0]['signals']==[]
    write(pair,operation='contact',login='target',friendly=False)
    assert len(proximity(actor.id,user)['devices'][0]['signals'])==1
    data=deepcopy(pair['target_character'].data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b9'}];pair['target_character'].data=data;db.session.commit()
    assert proximity(actor.id,user)['devices'][0]['signals']==[]
    data=deepcopy(pair['target_character'].data);data['inventory']['pockets'][0]['modifications']=[];pair['target_character'].data=data;pair['target_group'].tile_x=4;db.session.commit()
    assert proximity(actor.id,user)['devices'][0]['signals']==[]


def test_proximity_sound_is_physical_setting_and_public_sheet_cannot_override(network_game):
    from app.services.pda_network import proximity
    from app.services.portable_lights import preserve
    pair=network_game;actor=pair['actor_character'];data=deepcopy(actor.data)
    data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b8'});actor.data=data;pair['target_group'].tile_x=2;db.session.commit()
    write(pair,operation='proximity_sound',enabled=False,character_revision=actor.revision)
    assert not proximity(actor.id,pair['actor_user']['id'])['devices'][0]['sound']
    assert read(pair)['source']['has_proximity'] and not read(pair)['source']['proximity_sound']
    incoming=deepcopy(actor.data);incoming['inventory']['pockets'][0]['pdaNetwork']['proximity_sound']=True
    preserve(actor.data,incoming)
    assert not incoming['inventory']['pockets'][0]['pdaNetwork']['proximity_sound']
    assert proximity(actor.id,pair['actor_user']['id'])['devices'][0]['signals']


def test_proximity_is_private_world_scoped_and_stops_when_power_is_off(network_game,client,auth_headers):
    from app.services.pda_network import proximity
    pair=network_game;actor=pair['actor_character'];data=deepcopy(actor.data)
    data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b8'});actor.data=data;db.session.commit()
    with pytest.raises(PermissionDenied):proximity(actor.id,pair['target_user']['id'])
    with pytest.raises(NotFoundError):proximity(actor.id,pair['actor_user']['id'],lobby_id=actor.lobby_id+100)
    data=deepcopy(actor.data);data['inventory']['pockets'][0]['pdaState']['on']=False;actor.data=data;db.session.commit()
    assert proximity(actor.id,pair['actor_user']['id'])['devices']==[]
    response=client.get(f'/lobbies/characters/{actor.id}/pda-proximity',headers=auth_headers(pair['actor_user']))
    assert response.status_code==200 and response.json=={'devices':[]}
