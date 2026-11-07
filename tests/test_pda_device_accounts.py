"""Учётная запись мира входит на конкретный переносимый КПК."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest
from app.extensions import db
from app.models import PdaAccount,PdaAccountLogin,Lobby,LocationCombatState
from app.services.pda_accounts import account_action,account_status
from app.services.pda_network import registry
from app.services.container_transfer import ContainerTransferService
from app.services.character import CharacterService
from app.services.exceptions import ValidationError,PermissionDenied
from test_grenade_rules import grenade_game


@pytest.fixture
def account_game(grenade_game):
    pair=grenade_game;LocationCombatState.query.filter_by(location_id=pair['location'].id).one().status='idle'
    data=deepcopy(pair['actor_character'].data)
    data['inventory']={'pockets':[{'id':name,'name':'КПК','category':'misc','quantity':1} for name in ('first-pda','second-pda')],'backpack':[]}
    data['inventory']['pockets'][0]['contents']=[{'id':'book-one','name':'Книга','quantity':1,'volume':0}]
    pair['actor_character'].data=data;db.session.commit();return pair


def selection(index=0):return {'item_path':['inventory','pockets',index],'item_id':['first-pda','second-pda'][index]}
def act(pair,operation,index=0,**payload):return account_action(pair['actor_character'].id,pair['actor_user']['id'],operation,{**selection(index),**payload})
def status(pair,index=0):return account_status(pair['actor_character'].id,pair['actor_user']['id'],selection(index))
def register(pair,index=0,login='first-account'):return act(pair,'register',index,login=login,password='synthetic-password')


def test_two_physical_pdas_keep_separate_logins_and_logout(account_game):
    pair=account_game;first=register(pair);assert status(pair,1)=={'account':None}
    second=register(pair,1,'second-account')
    assert status(pair)['account']['id']==first['account']['id'] and status(pair,1)['account']['id']==second['account']['id']
    act(pair,'save_notes',notes='Запись первого аккаунта');assert status(pair,1)['account']['notes']==''
    act(pair,'logout');assert status(pair)=={'account':None} and status(pair,1)['account']['id']==second['account']['id']
    assert pair['actor_character'].data['inventory']['pockets'][0]['contents'][0]['id']=='book-one'


def test_same_account_notes_can_be_opened_on_another_pda_without_moving_books(account_game):
    pair=account_game;register(pair);act(pair,'save_notes',notes='Общая запись аккаунта')
    act(pair,'login',1,login='first-account',password='synthetic-password')
    assert status(pair,1)['account']['notes']=='Общая запись аккаунта'
    assert pair['actor_character'].data['inventory']['pockets'][0]['contents']
    assert not pair['actor_character'].data['inventory']['pockets'][1].get('contents')


def test_authenticated_device_keeps_account_after_drop_and_pickup_by_another_carrier(account_game):
    pair=account_game;first=register(pair);register(pair,1,'second-account');actor=pair['actor_character'];target=pair['target_character']
    target.data={'inventory':{'pockets':[{'id':'third-pda','name':'КПК','category':'misc','quantity':1}],'backpack':[]}}
    db.session.commit()
    account_action(target.id,pair['target_user']['id'],'register',{'item_path':['inventory','pockets',0],'item_id':'third-pda','login':'third-account','password':'synthetic-password'})
    item=deepcopy(actor.data['inventory']['pockets'][0])
    _,obj,_=ContainerTransferService.drop(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],{
        'character_id':actor.id,'character_revision':actor.revision,'item_path':['inventory','pockets',0],'expected_item':item,'amount':1})
    pair['target_location'].pos_x=pair['actor_location'].pos_x;pair['target_location'].pos_y=pair['actor_location'].pos_y;db.session.commit()
    ContainerTransferService.transfer(pair['lobby'].id,obj.id,pair['target_user']['id'],{'character_id':target.id,
        'character_revision':target.revision,'object_revision':obj.revision,'direction':'to_character','item_path':['contents',0],'expected_item':item,'amount':1})
    picked=account_status(target.id,pair['target_user']['id'],{'item_path':['inventory','backpack',0],'item_id':'first-pda'})
    assert picked['account']['id']==first['account']['id']
    assert target.data['inventory']['backpack'][0]['contents'][0]['id']=='book-one'
    entries=[entry for entry in registry(pair['lobby'].id,pair['location'].id) if entry['character'].id==target.id]
    assert {entry['account'].login for entry in entries}=={'third-account','first-account'}


def test_physical_login_cannot_be_forged_or_cross_world_boundary(account_game):
    pair=account_game;register(pair);actor=pair['actor_character'];other=PdaAccount(lobby_id=actor.lobby_id,login='private',login_key='private',password_hash='synthetic',notes='Чужие заметки')
    db.session.add(other);db.session.commit()
    data=deepcopy(actor.data);data['inventory']['pockets'][0]['pdaNetwork']['account_id']=other.id
    from app.services.portable_lights import preserve
    preserve(actor.data,data)
    assert data['inventory']['pockets'][0]['pdaNetwork']['account_id']!=other.id
    created=CharacterService.create_character(actor.lobby_id,pair['actor_user']['id'],'Поддельный вход',{'inventory':{'pockets':[
        {'id':'forged-pda','name':'КПК','category':'misc','quantity':1,'pdaNetwork':{'account_id':other.id}}]}})
    assert account_status(created.id,pair['actor_user']['id'],{'item_path':['inventory','pockets',0],'item_id':'forged-pda'})=={'account':None}
    with pytest.raises(PermissionDenied):account_status(actor.id,pair['target_user']['id'],selection())
    another=Lobby(name='Другой мир',gm_id=pair['lobby'].gm_id,invite_code='PDA-OTHER-WORLD');db.session.add(another);db.session.flush()
    foreign=PdaAccount(lobby_id=another.id,login='foreign',login_key='foreign',password_hash='synthetic');db.session.add(foreign);db.session.flush()
    data=deepcopy(actor.data);data['inventory']['pockets'][0]['pdaNetwork']['account_id']=foreign.id;actor.data=data;db.session.commit()
    assert status(pair)=={'account':None}


def test_legacy_login_materializes_on_old_pdas_before_selective_logout(account_game):
    pair=account_game;actor=pair['actor_character'];account=PdaAccount(lobby_id=actor.lobby_id,login='legacy',login_key='legacy',password_hash='synthetic')
    db.session.add(account);db.session.flush();db.session.add(PdaAccountLogin(character_id=actor.id,account_id=account.id));db.session.commit()
    assert status(pair)['account']['login']=='legacy' and status(pair,1)['account']['login']=='legacy'
    act(pair,'logout')
    assert status(pair)=={'account':None} and status(pair,1)['account']['login']=='legacy'
    assert db.session.get(PdaAccountLogin,actor.id) is None


def test_migration_pins_legacy_login_without_moving_books(account_game,monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    pair=account_game;actor=pair['actor_character'];account=PdaAccount(lobby_id=actor.lobby_id,login='legacy',login_key='legacy',password_hash='synthetic')
    db.session.add(account);db.session.flush();db.session.add(PdaAccountLogin(character_id=actor.id,account_id=account.id));db.session.commit()
    original=deepcopy(actor.data)
    old=deepcopy(actor.data);old['inventory']['pockets'][0].update(id=None,pdaNetwork=None);actor.data=old;db.session.commit()
    path=Path(__file__).resolve().parents[1]/'migrations/versions/c9f61b4273bd_pin_pda_accounts_to_devices.py'
    spec=importlib.util.spec_from_file_location('pda_login_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(db.session.connection())))
    migration.upgrade();db.session.expire_all()
    assert actor.data['inventory']['pockets'][0]['contents']==original['inventory']['pockets'][0]['contents']
    first=account_status(actor.id,pair['actor_user']['id'],{'item_path':['inventory','pockets',0]})
    assert first['account']['id']==account.id and status(pair,1)['account']['id']==account.id
    identity=actor.data['inventory']['pockets'][0]['id'];assert isinstance(identity,str) and identity
    assert db.session.get(PdaAccountLogin,actor.id) is None
    migration.upgrade();db.session.expire_all();assert actor.data['inventory']['pockets'][0]['id']==identity
