"""Ложный профиль использует отдельные данные, но не создаёт второй экземпляр книги."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.services.pda_accounts import account_action,account_status
from app.services.exceptions import ValidationError
from app.services.pda_access import target_snapshot
from test_pda_device_accounts import account_game,act,status,register
from test_pda_access import access_game,connected,selection,write
from test_grenade_rules import grenade_game


def enable(character):
    data=deepcopy(character.data);item=data['inventory']['pockets'][0]
    item.setdefault('modifications',[]).append({'ruleId':'pda-b15'})
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
    act(pair,'login',1,login=main['login'],password='synthetic-password')
    assert status(pair,1)['account']['notes']=='Настоящие заметки'
    assert len(pair['actor_character'].data['inventory']['pockets'][0]['contents'])==1
    assert not pair['actor_character'].data['inventory']['pockets'][1].get('contents')
    act(pair,'switch_profile',profile='decoy');assert status(pair)['account']['notes']=='Ложные заметки'


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
