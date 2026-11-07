"""Очистка физического КПК удаляет его аккаунты, но не соседние устройства и миры."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import PdaAccount,PdaContact,PdaTransmission,PdaDelivery,LocationObject,LocationCombatState,PdaAccessSession,Lobby
from app.services.portable_lights import operate
from app.services.exceptions import ValidationError,ConflictError
from test_grenade_rules import grenade_game
from test_pda_device_accounts import account_game,act,register,status
from test_pda_access import access_game,connected,write


def install(character):
    data=deepcopy(character.data);pda=data['inventory']['pockets'][0]
    pda.setdefault('modifications',[]).extend([{'ruleId':'pda-b14'},{'ruleId':'pda-b15'}])
    pda['pdaState']={'on':True,'charge_percent':37}
    pda['contents']=[{'id':'erase-book','name':'Книга','quantity':1,'attributes':{'book_kind':'skill'}}]
    character.data=data;db.session.commit()


def wipe(pair,revision=None):
    character=pair['actor_character']
    return operate(character.id,pair['actor_user']['id'],{'operation':'pda_wipe','path':['inventory','pockets',0],
        'source_id':character.data['inventory']['pockets'][0]['id'],
        'character_revision':character.revision if revision is None else revision})


def test_wipe_removes_both_accounts_books_and_bindings_but_preserves_other_data(account_game):
    pair=account_game;character=pair['actor_character'];install(character)
    main=register(pair)['account'];act(pair,'save_notes',notes='Секретные заметки')
    act(pair,'switch_profile',profile='decoy');decoy=register(pair,login='decoy-account')['account']
    act(pair,'login',1,login=main['login'],password='synthetic-password')
    other=PdaAccount(lobby_id=character.lobby_id,login='surviving-account',login_key='surviving-account',password_hash='synthetic',notes='Сохранить')
    db.session.add(other);db.session.flush()
    db.session.add_all([PdaContact(account_id=main['id'],target_account_id=other.id),PdaContact(account_id=other.id,target_account_id=decoy['id'])])
    for identity,sender,recipients in [('erase-message',main['id'],[other.id]),('keep-message',other.id,[decoy['id']])]:
        db.session.add(PdaTransmission(id=identity,lobby_id=character.lobby_id,sender_account_id=sender,
            source_signal_id='synthetic',recipient_account_ids=recipients,text='Сообщение',game_time=1,signature='synthetic'))
    db.session.flush()
    db.session.add_all([PdaDelivery(account_id=other.id,transmission_id='erase-message'),PdaDelivery(account_id=decoy['id'],transmission_id='keep-message')])
    obj=LocationObject(location_id=pair['location'].id,type='container',name='Ящик',tile_x=4,tile_y=4,
        properties={'contents':[{'id':'box-pda','name':'КПК','pdaNetwork':{'account_id':main['id']},'contents':[{'id':'keep-book','name':'Сохранённая книга'}]},
            {'id':'charge-box','name':'Заряд','controllerState':{'id':'controller-box','kind':'remote_detonator','paired_pda_id':'first-pda','armed':True}}]})
    db.session.add(obj)
    data=deepcopy(character.data);data['inventory']['pockets'].append({'id':'charge-self','name':'Заряд','controllerState':{
        'id':'controller-self','kind':'remote_detonator','paired_pda_id':'first-pda','armed':True}})
    data['crafting']={'current':{'id':'reading-job','kind':'book','book_id':'erase-book','name':'Книга'},'library':[{'name':'Уже прочитанная книга'}]}
    character.data=data;db.session.commit();old_revision=character.revision
    _,_,result=wipe(pair)
    assert result['deleted_accounts']==2 and result['deleted_files']==1 and result['unpaired_controllers']==2
    assert db.session.get(PdaAccount,main['id']) is None and db.session.get(PdaAccount,decoy['id']) is None
    assert db.session.get(PdaAccount,other.id).notes=='Сохранить'
    assert PdaContact.query.count()==PdaDelivery.query.count()==0
    assert PdaTransmission.query.one().id=='keep-message' and PdaTransmission.query.one().recipient_account_ids==[]
    item=character.data['inventory']['pockets'][0]
    assert item['id']=='first-pda' and item['name']=='КПК' and item['pdaState']['charge_percent']==37
    assert {mod['ruleId'] for mod in item['modifications']}=={'pda-b14','pda-b15'} and item['contents']==[]
    assert status(pair)['account'] is None and status(pair,1)['account'] is None
    assert obj.properties['contents'][0]['pdaNetwork']['account_id'] is None
    assert obj.properties['contents'][0]['contents'][0]['id']=='keep-book'
    assert all('paired_pda_id' not in entry['controllerState'] for entry in [character.data['inventory']['pockets'][2],obj.properties['contents'][1]])
    assert obj.properties['contents'][1]['controllerState']['armed'] is True
    assert 'current' not in character.data['crafting'] and character.data['crafting']['library']
    assert character.data['crafting']['history'][0]['cancelled']
    register(pair,login='new-after-wipe')
    with pytest.raises(ConflictError):wipe(pair,old_revision)
    assert status(pair)['account']['login']=='new-after-wipe'


@pytest.mark.parametrize('failure',['missing_mod','off','insufficient_ap'])
def test_wipe_validation_never_removes_account_or_book(account_game,failure):
    pair=account_game;character=pair['actor_character'];install(character);account=register(pair)['account']
    data=deepcopy(character.data)
    if failure=='missing_mod':data['inventory']['pockets'][0]['modifications']=[]
    elif failure=='off':data['inventory']['pockets'][0]['pdaState']['on']=False
    else:
        state=LocationCombatState.query.filter_by(location_id=pair['location'].id).one();state.status='active'
        state.current_location_character_id=pair['actor_location'].id;pair['actor_location'].action_points_current=2
    character.data=data;db.session.commit()
    with pytest.raises(ValidationError):wipe(pair)
    assert db.session.get(PdaAccount,account['id']) and character.data['inventory']['pockets'][0]['contents']


def test_hacker_can_wipe_target_once_and_loses_connection(access_game):
    pair=access_game;install(pair['target_character'])
    from app.services.pda_accounts import account_action
    account=account_action(pair['target_character'].id,pair['target_user']['id'],'register',{
        'item_path':['inventory','pockets',0],'item_id':'target-pda','login':'erased-account','password':'synthetic-password'})['account']
    connected(pair)
    _,result=write(pair,operation='wipe',operation_id='erase-once',session_id='access-one')
    assert result['action_points']==3 and result['deleted_accounts']==1
    assert db.session.get(PdaAccessSession,'access-one').status=='closed'
    assert db.session.get(PdaAccount,account['id']) is None
    assert pair['actor_character'].data['inventory']['pockets'][0]['id']=='actor-pda'
    assert write(pair,operation='wipe',operation_id='erase-once',session_id='access-one')[1]==result


def test_corrupt_profile_reference_never_deletes_account_in_other_world(account_game):
    pair=account_game;character=pair['actor_character'];install(character)
    world=Lobby(name='Другой мир',gm_id=pair['actor_user']['id'],invite_code='other-wipe');db.session.add(world);db.session.flush()
    account=PdaAccount(lobby_id=world.id,login='foreign',login_key='foreign',password_hash='synthetic',notes='Чужой мир')
    db.session.add(account);db.session.flush()
    data=deepcopy(character.data);data['inventory']['pockets'][0]['pdaNetwork']={'profile_accounts':{'decoy':account.id}}
    character.data=data;db.session.commit()
    assert wipe(pair)[2]['deleted_accounts']==0
    assert db.session.get(PdaAccount,account.id).notes=='Чужой мир'


def test_http_wipe_publishes_updated_box_after_commit_without_private_service_ids(account_game,client,auth_headers,monkeypatch):
    pair=account_game;character=pair['actor_character'];install(character);account=register(pair)['account']
    obj=LocationObject(location_id=pair['location'].id,type='container',name='Ящик',tile_x=4,tile_y=4,
        properties={'contents':[{'id':'box-pda','name':'КПК','pdaNetwork':{'account_id':account['id']}}]})
    db.session.add(obj);db.session.commit();events=[]
    monkeypatch.setattr('app.services.pda_wipe.socketio.emit',lambda name,payload,**kwargs:events.append((name,payload)))
    response=client.post(f'/lobbies/characters/{character.id}/portable-lights',headers=auth_headers(pair['actor_user']),json={
        'operation':'pda_wipe','path':['inventory','pockets',0],'source_id':'first-pda','character_revision':character.revision})
    assert response.status_code==200
    assert not {'changed_character_ids','changed_object_ids','_pda_updates'}.intersection(response.get_json())
    update=next(payload['object'] for name,payload in events if name=='location_object_updated')
    assert update['id']==obj.id and update['properties']['contents'][0]['pdaNetwork']['account_id'] is None
    assert update['revision']==obj.revision
    from app.services.pda_wipe import publish
    count=len(events);publish(character.lobby_id);assert len(events)==count
