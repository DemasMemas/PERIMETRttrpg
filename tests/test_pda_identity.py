"""Имя отправителя можно подменить, права чужого аккаунта получить нельзя."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import inspect
from app.extensions import db
from app.models import LocationCombatState,PdaTransmission,PdaTransmissionIdentity,PdaContact,PdaContactLabel
from app.services.exceptions import ValidationError,ConflictError,PermissionDenied
from app.services.pda_accounts import account_status,account_action
from app.services.pda_network import snapshot
from test_grenade_rules import grenade_game
from test_pda_network import network_game,selection,read,write
from test_pda_access import access_game,connected,software,selection as access_selection,write as access_write


@pytest.fixture
def identity_game(network_game):
    case=network_game;case['target_group'].tile_x=1
    data=deepcopy(case['actor_character'].data);data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b21'})
    case['actor_character'].data=data;db.session.commit();return case


def change(case,name,identity='alias-one',**extra):
    return write(case,operation='sender_name',sender_name=name,operation_id=identity,
        character_revision=case['actor_character'].revision,**extra)


def send(case,identity):return write(case,operation='send',operation_id=identity,text=identity,target_account_id=case['target_account'].id)


def test_existing_login_can_be_used_without_password_but_does_not_grant_account(identity_game):
    case=identity_game;case['target_account'].notes='Чужие заметки';db.session.commit()
    change(case,case['target_account'].login);send(case,'masked-message')
    assert read(case,'target')['messages'][-1]['sender']=='target'
    assert read(case,'target')['signals'][0]['login']=='target'
    assert PdaTransmission.query.one().sender_account_id==case['actor_account'].id
    own=account_status(case['actor_character'].id,case['actor_user']['id'],selection(case))
    assert own['account']['id']==case['actor_account'].id and own['account']['notes']!='Чужие заметки'
    assert case['actor_account'].login=='actor' and case['target_account'].login=='target'


def test_history_keeps_name_at_send_time_after_change_reset_and_retry(identity_game):
    case=identity_game
    send(case,'real-before');change(case,'Первое имя');send(case,'first-name')
    change(case,'Второе имя','alias-two');send(case,'second-name');send(case,'first-name')
    change(case,'','alias-reset');send(case,'real-after')
    assert [(r['text'],r['sender']) for r in read(case,'target')['messages']]==[
        ('real-before','actor'),('first-name','Первое имя'),('second-name','Второе имя'),('real-after','actor')]
    assert PdaTransmissionIdentity.query.count()==2


def test_alias_retry_and_noop_have_no_extra_cost_or_different_payload(identity_game):
    case=identity_game;_,first=change(case,'Новое имя')
    assert not first['replayed'] and first['changed'] and first['action_points']==0
    _,retry=change(case,'Новое имя');assert retry['replayed'] and retry['action_points']==0
    _,noop=change(case,'Новое имя','same-value');assert not noop['changed'] and noop['action_points']==0
    with pytest.raises(ConflictError):change(case,'Другое имя')
    assert read(case)['source']['sender_name']=='Новое имя'


@pytest.mark.parametrize('name',[None,42,'a'*101,'Имя\nДругое','\nИмя','Имя\x00'])
def test_invalid_names_leave_no_state(identity_game,name):
    case=identity_game;before=deepcopy(case['actor_character'].data)
    with pytest.raises(ValidationError):change(case,name)
    assert case['actor_character'].data==before


def test_unicode_name_and_html_are_literal_text(identity_game):
    case=identity_game;change(case,'  <b>Сталкер 🐾</b>  ');send(case,'unicode-alias')
    assert read(case,'target')['messages'][-1]['sender']=='<b>Сталкер 🐾</b>'


def test_missing_mod_foreign_access_and_stale_revision(identity_game):
    case=identity_game;character=case['actor_character']
    with pytest.raises(ConflictError):write(case,operation='sender_name',sender_name='Нет',operation_id='stale',character_revision=character.revision-1)
    assert not (character.data['inventory']['pockets'][0].get('pdaNetwork') or {}).get('sender_name')
    with pytest.raises(PermissionDenied):snapshot(character.id,case['target_user']['id'],selection(case))
    with pytest.raises(ValidationError):write(case,'target',operation='sender_name',sender_name='Нет',operation_id='no-mod',character_revision=case['target_character'].revision)


@pytest.mark.parametrize('mode',['success','no_hand','insufficient','off_turn'])
def test_combat_cost_once_and_rejected_changes_do_not_mutate(identity_game,mode):
    case=identity_game;character=case['actor_character'];actor=case['actor_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='active'
    state.current_location_character_id=case['target_location'].id if mode=='off_turn' else actor.id
    data=deepcopy(character.data);data.setdefault('health',{}).setdefault('combatMeta',{})['heldPda']={'id':'actor-pda' if mode!='no_hand' else 'other-pda'}
    character.data=data;actor.action_points_current=2 if mode=='insufficient' else 5;db.session.commit();before=actor.action_points_current
    if mode!='success':
        with pytest.raises(PermissionDenied if mode=='off_turn' else ValidationError):change(case,'Боевое имя',location_id=actor.location_id)
        assert actor.action_points_current==before
        assert not (character.data['inventory']['pockets'][0].get('pdaNetwork') or {}).get('sender_name')
    else:
        assert change(case,'Боевое имя',location_id=actor.location_id)[1]['action_points']==3
        assert actor.action_points_current==2
        assert change(case,'Боевое имя',location_id=actor.location_id)[1]['action_points']==0
        assert change(case,'Боевое имя','no-change',location_id=actor.location_id)[1]['action_points']==0
        assert actor.action_points_current==2


def test_alias_setting_is_physical_and_sheet_cannot_rewrite_it(identity_game):
    from app.services.portable_lights import preserve
    case=identity_game;change(case,'Первый аппарат')
    character=case['actor_character'];data=deepcopy(character.data);second=deepcopy(data['inventory']['pockets'][0])
    second['id']='second-pda';second.pop('pdaNetwork',None);data['inventory']['pockets'].append(second);character.data=data;db.session.commit()
    result=snapshot(character.id,case['actor_user']['id'],{'item_path':['inventory','pockets',1],'item_id':'second-pda'})
    assert result['source']['sender_name']==''
    incoming=deepcopy(character.data);incoming['inventory']['pockets'][0]['pdaNetwork']={'sender_name':'Подделка'}
    preserve(character.data,incoming)
    assert incoming['inventory']['pockets'][0]['pdaNetwork']['sender_name']=='Первый аппарат'


def test_old_schema_keeps_normal_chat_and_migration_enables_alias(identity_game):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    case=identity_game;connection=db.session.connection();PdaContactLabel.__table__.drop(connection);PdaTransmissionIdentity.__table__.drop(connection)
    assert not read(case)['source']['alias_ready']
    with pytest.raises(ValidationError,match='обновить'):change(case,'Нет таблицы')
    send(case,'old-schema');assert read(case,'target')['messages'][-1]['sender']=='actor'
    module_path=Path(__file__).resolve().parents[1]/'migrations/versions/e14a39b7062c_add_pda_transmission_identities.py'
    spec=importlib.util.spec_from_file_location('identity_migration',module_path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db.session.connection())):module.upgrade()
    assert inspect(db.session.connection()).has_table('pda_transmission_identities')
    change(case,'После миграции');send(case,'new-schema')
    assert read(case,'target')['messages'][-1]['sender']=='После миграции'


def test_successful_hack_sees_masked_name_in_both_views_and_can_change_it(access_game):
    from app.services.pda_access import target_snapshot
    case=access_game;target=case['target_character'];data=deepcopy(target.data)
    data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b21'});target.data=data;db.session.commit()
    account_action(target.id,case['target_user']['id'],'register',access_selection(case,'target',login='TargetReal',password='synthetic-secret'))
    account_action(case['actor_character'].id,case['actor_user']['id'],'register',access_selection(case,login='HackerReal',password='synthetic-hacker'))
    connected(case);software(case,'sender_name',sender_name='Ложное имя')
    write(case,operation='send',operation_id='incoming-for-masked-account',text='Входящее',
        target_account_id=target.data['inventory']['pockets'][0]['pdaNetwork']['account_id'])
    view=target_snapshot(case['actor_character'].id,case['actor_user']['id'],access_selection(case,session_id='access-one'))
    assert view['account']['login']==view['network']['account']['login']=='Ложное имя'
    assert view['network']['messages'][-1]['recipient']=='Ложное имя'
    assert read(case)['messages'][-1]['recipient']=='Ложное имя'
    assert software(case,'sender_name',sender_name='Ложное имя')[1]['replayed'] is True
    access_write(case,operation='disconnect',operation_id='stop',session_id='access-one')
    with pytest.raises(ConflictError):software(case,'sender_name',identity='after-close',sender_name='Нет доступа')


def test_spam_captures_sender_name_and_wipe_removes_alias(identity_game):
    from app.services.portable_lights import advance_lobby
    from test_pda_wipe import wipe
    case=identity_game;character=case['actor_character'];data=deepcopy(character.data)
    data['inventory']['pockets'][0]['modifications'].extend([{'ruleId':'pda-b17'},{'ruleId':'pda-b14'}]);character.data=data;db.session.commit()
    change(case,'Автор сигнала');send(case,'to-erase')
    target=read(case)['signals'][0]['signal_id']
    write(case,operation='spam',operation_id='masked-spam',target_signal_id=target,text='Сигнал',character_revision=character.revision)
    signal=read(case,'target')['signals'][0]
    write(case,'target',operation='contact',target_account_id=signal['account_id'],target_signal_id=signal['signal_id'],friendly=True)
    change(case,'','reset-before-tick');advance_lobby(case['lobby'],6);db.session.commit()
    assert read(case,'target')['messages'][-1]['sender']=='Автор сигнала'
    wipe(case);assert not character.data['inventory']['pockets'][0]['pdaNetwork'].get('sender_name')
    assert PdaTransmissionIdentity.query.count()==0
    assert PdaContactLabel.query.count()==0


@pytest.mark.parametrize('name',['target','Несуществующий аккаунт'])
def test_signal_contact_uses_actual_account_id_and_keeps_observed_name(identity_game,name):
    case=identity_game;change(case,name)
    signal=read(case,'target')['signals'][0]
    write(case,'target',operation='contact',target_account_id=signal['account_id'],target_signal_id=signal['signal_id'],friendly=True)
    contact=read(case,'target')['contacts'][0]
    assert contact['account_id']==case['actor_account'].id and contact['login']==name
    assert PdaContact.query.one().target_account_id==case['actor_account'].id
    write(case,'target',operation='contact',target_account_id=contact['account_id'],friendly=False)
    assert not read(case,'target')['contacts'][0]['friendly']
    assert read(case,'target')['contacts'][0]['login']==name
    case['actor_group'].tile_x=20;db.session.commit()
    assert read(case,'target')['signals']==[] and read(case,'target')['contacts'][0]['login']==name
    write(case,'target',operation='remove_contact',target_account_id=case['actor_account'].id)
    assert PdaContactLabel.query.count()==0


def test_id_contact_cannot_guess_unavailable_accounts_and_labels_follow_account(identity_game):
    case=identity_game;change(case,'Имя сигнала');signal=read(case,'target')['signals'][0]
    case['actor_group'].tile_x=20;db.session.commit()
    with pytest.raises(ValidationError):write(case,'target',operation='contact',target_account_id=signal['account_id'],friendly=True)
    case['actor_group'].tile_x=0;db.session.commit()
    write(case,'target',operation='contact',target_account_id=signal['account_id'],target_signal_id=signal['signal_id'],friendly=True)
    character=case['target_character'];data=deepcopy(character.data);other=deepcopy(data['inventory']['pockets'][0]);other['id']='target-second'
    data['inventory']['pockets'].append(other);character.data=data;db.session.commit()
    view=snapshot(character.id,case['target_user']['id'],{'item_path':['inventory','pockets',1],'item_id':'target-second'})
    assert view['contacts'][0]['login']=='Имя сигнала'
