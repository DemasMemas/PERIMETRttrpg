"""Ложный профиль использует отдельные данные, но не создаёт второй экземпляр книги."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.services.pda_accounts import account_action,account_status
from app.services.exceptions import PermissionDenied, ValidationError
from app.services.pda_access import target_snapshot
from test_pda_device_accounts import account_game,act,status,register
from test_pda_access import access_game,connected,selection,write
from test_grenade_rules import grenade_game


def enable(character, *, legacy=False):
    data=deepcopy(character.data);item=data['inventory']['pockets'][0]
    modification={'ruleId':'pda-b15'}
    if not legacy:
        modification['profileOwnerId']=character.id
    item.setdefault('modifications',[]).append(modification)
    item['contents']=[{'id':'single-book','name':'Книга','attributes':{'book_kind':'skill'},'quantity':1,'volume':0}]
    character.data=data;db.session.commit()


def test_second_profile_has_own_account_notes_and_file_visibility_without_copy(account_game):
    pair=account_game;enable(pair['actor_character']);main=register(pair)['account']
    act(pair,'save_notes',notes='Настоящие заметки')
    switched=act(pair,'switch_profile',profile='decoy')
    assert switched['account'] is None and switched['profile']=='decoy'
    decoy=register(pair,login='decoy-account')['account'];assert decoy['id']!=main['id']
    act(pair,'save_notes',notes='Ложные заметки')
    act(pair,'move_file',file_id='single-book',profile='decoy')
    assert status(pair)['file_profiles']=={'single-book':'decoy'}
    from app.services.pda_network import registry
    assert next(row for row in registry(pair['lobby'].id) if row['item']['id']=='first-pda')['account'].id==decoy['id']
    act(pair,'switch_profile',profile='main');assert status(pair)['account']['notes']=='Настоящие заметки'
    from app.services.crafting import readable_book
    assert not readable_book(pair['actor_character'],pair['actor_character'].data,{'book_id':'single-book'})
    act(pair,'login',1,login=main['login'],password='synthetic-password')
    assert status(pair,1)['account']['notes']=='Настоящие заметки'
    assert len(pair['actor_character'].data['inventory']['pockets'][0]['contents'])==1
    assert not pair['actor_character'].data['inventory']['pockets'][1].get('contents')
    act(pair,'switch_profile',profile='decoy');assert status(pair)['account']['notes']=='Ложные заметки'
    assert readable_book(pair['actor_character'],pair['actor_character'].data,{'book_id':'single-book'})


def test_new_holder_cannot_switch_second_profile_or_move_files(account_game):
    from app.services.container_transfer import ContainerTransferService
    from app.services.portable_lights import operate

    pair=account_game;actor=pair['actor_character'];target=pair['target_character'];enable(actor)
    act(pair,'switch_profile',profile='decoy')
    decoy=register(pair,login='transfer-decoy')['account']
    target.data={'inventory':{'pockets':[],'backpack':[]}}
    item=deepcopy(actor.data['inventory']['pockets'][0])
    db.session.commit()
    _,obj,_=ContainerTransferService.drop(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],{
        'character_id':actor.id,'character_revision':actor.revision,
        'item_path':['inventory','pockets',0],'expected_item':item,'amount':1})
    pair['target_location'].pos_x=pair['actor_location'].pos_x
    pair['target_location'].pos_y=pair['actor_location'].pos_y
    db.session.commit()
    user=pair['target_user']['id']
    ContainerTransferService.transfer(pair['lobby'].id,obj.id,user,{'character_id':target.id,
        'character_revision':target.revision,'object_revision':obj.revision,'direction':'to_character',
        'item_path':['contents',0],'expected_item':item,'amount':1})
    path=['inventory','backpack',0]
    selection={'item_path':path,'item_id':'first-pda'}
    snapshot=account_status(target.id,user,selection)
    assert snapshot['profile']=='decoy' and snapshot['account']['id']==decoy['id']
    assert 'profiles' not in snapshot and 'can_bind_profile_owner' not in snapshot
    for operation,payload in [('switch_profile',{'profile':'main'}),
                              ('move_file',{'file_id':'single-book','profile':'decoy'}),
                              ('bind_profile_owner',{})]:
        with pytest.raises(PermissionDenied):
            account_action(target.id,user,operation,{**selection,**payload})
    with pytest.raises(PermissionDenied):
        operate(target.id,user,{'operation':'pda_profile','path':path,'source_id':'first-pda',
            'character_revision':target.revision,'profile':'main'})
    assert account_status(target.id,user,selection)==snapshot


def test_legacy_profile_can_be_assigned_once_by_gm(account_game):
    from test_pda_device_accounts import selection as device_selection

    pair=account_game;character=pair['actor_character'];enable(character,legacy=True)
    gm=pair['lobby'].gm_id
    assert 'profiles' not in status(pair)
    assert account_status(character.id,gm,device_selection())['can_bind_profile_owner']
    with pytest.raises(PermissionDenied):
        act(pair,'switch_profile',profile='decoy')
    account_action(character.id,gm,'bind_profile_owner',device_selection())
    assert act(pair,'switch_profile',profile='decoy')['profile']=='decoy'
    with pytest.raises(ValidationError,match='уже назначен'):
        account_action(character.id,gm,'bind_profile_owner',device_selection())


def test_missing_modification_or_file_does_not_change_profiles(account_game):
    pair=account_game
    with pytest.raises(ValidationError,match='Второго профиля'):act(pair,'switch_profile',profile='decoy')
    enable(pair['actor_character'])
    with pytest.raises(ValidationError,match='файл книги'):act(pair,'move_file',file_id='foreign-file',profile='decoy')
    with pytest.raises(ValidationError,match='основной или ложный'):act(pair,'switch_profile',profile='fake')
    assert status(pair)['profile']=='main'


def test_hacker_can_access_both_profiles_and_both_file_lists(access_game):
    pair=access_game;target=pair['target_character'];user=pair['target_user']['id'];enable(target)
    def change(operation,**payload):return account_action(target.id,user,operation,selection(pair,'target',**payload))
    change('register',login='real-profile',password='synthetic-password');change('save_notes',notes='Настоящие данные')
    change('switch_profile',profile='decoy');change('register',login='decoy-profile',password='synthetic-password')
    change('save_notes',notes='Ложные данные');change('move_file',file_id='single-book',profile='decoy')
    connected(pair)
    def view():return target_snapshot(pair['actor_character'].id,pair['actor_user']['id'],selection(pair,session_id='access-one'))
    assert view()['account']['notes']=='Ложные данные' and view()['files'][0]['profile']=='decoy'
    assert {profile['id'] for profile in view()['profiles']}=={'main','decoy'}
    assert write(pair,operation='switch_profile',operation_id='main-profile',session_id='access-one',profile='main')[1]['action_points']==3
    assert view()['account']['notes']=='Настоящие данные'
    assert len(view()['files'])==1 and view()['files'][0]['id']=='single-book'
    write(pair,operation='switch_profile',operation_id='back-to-decoy',session_id='access-one',profile='decoy')
    assert view()['account']['notes']=='Ложные данные'


def test_switch_in_combat_costs_three_ap_and_cannot_use_free_account_route(account_game):
    from app.models import LocationCombatState
    from app.services.portable_lights import operate
    pair=account_game;character=pair['actor_character'];enable(character);register(pair)
    state=LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    state.status='active';state.current_location_character_id=pair['actor_location'].id
    pair['actor_location'].action_points_current=5;db.session.commit()
    with pytest.raises(ValidationError,match='3 ОД'):act(pair,'switch_profile',profile='decoy')
    def switch(profile):return operate(character.id,pair['actor_user']['id'],{'operation':'pda_profile',
        'path':['inventory','pockets',0],'source_id':'first-pda','character_revision':character.revision,'profile':profile})
    assert switch('decoy')[2]['action_points']==3 and pair['actor_location'].action_points_current==2
    with pytest.raises(ValidationError,match='Недостаточно ОД'):switch('main')
    assert status(pair)['profile']=='decoy'
    assert switch('decoy')[2]['action_points']==0 and pair['actor_location'].action_points_current==2
    assert any(entry['label']=='Переключение профиля КПК' and entry['action_points']==3
        for entry in character.data['health']['combatMeta']['actionJournal'])
