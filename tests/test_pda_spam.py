"""Спам идёт по игровым часам одному физическому аппарату, без повторной оплаты."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState
from app.services.combat import CombatService
from app.services.exceptions import ConflictError,ValidationError,PermissionDenied
from app.services.portable_lights import advance_lobby
from app.services.pda_network import snapshot
from test_pda_network import network_game,selection,write,read
from test_grenade_rules import grenade_game
from test_pda_access import access_game


@pytest.fixture
def spam_game(network_game):
    case=network_game;case['target_group'].tile_x=1
    data=deepcopy(case['actor_character'].data)
    data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b17'})
    case['actor_character'].data=data;db.session.commit()
    return case


def start(case,**extra):
    target=read(case)['signals'][0]['signal_id']
    return write(case,operation='spam',operation_id='spam-proof',target_signal_id=target,text='Тестовый сигнал',
        character_revision=case['actor_character'].revision,**extra)


def inbox(case,index=0):
    return (case['target_character'].data['inventory']['pockets'][index].get('pdaNetwork') or {}).get('spam_messages') or []


def tick(case,seconds):
    advance_lobby(case['lobby'],seconds);db.session.commit()


def test_five_six_second_signals_and_no_extra_from_snapshot_or_repeated_request(spam_game):
    case=spam_game;character,result=start(case)
    assert result['remaining']==5 and result['action_points']==0
    assert start(case)[1]['replayed'] is True
    tick(case,5);assert inbox(case)==[]
    read(case);read(case,'target');assert inbox(case)==[]
    tick(case,1);assert len(inbox(case))==1
    tick(case,24);assert len(inbox(case))==5
    assert len({m['id'] for m in inbox(case)})==5 and all(m['noise']==10 and m['spam'] for m in inbox(case))
    assert start(case)[1]['replayed'] is True
    tick(case,60);assert len(inbox(case))==5
    assert not read(case)['source']['spam_remaining']


def test_same_operation_id_with_another_text_is_rejected(spam_game):
    case=spam_game;start(case);target=read(case)['signals'][0]['signal_id']
    with pytest.raises(ConflictError):
        write(case,operation='spam',operation_id='spam-proof',target_signal_id=target,text='Другой текст',character_revision=case['actor_character'].revision)
    tick(case,30);assert len(inbox(case))==5


def test_normal_messages_keep_real_send_order_when_interleaved_with_spam(spam_game):
    case=spam_game
    write(case,operation='send',operation_id='z-first',text='До спама')
    start(case);tick(case,6)
    write(case,operation='send',operation_id='a-last',text='После сигнала')
    assert [m['text'] for m in read(case,'target')['messages']]==['До спама','Тестовый сигнал','После сигнала']


def test_target_power_off_loses_attempts_instead_of_delivering_a_late_burst(spam_game):
    case=spam_game;start(case);tick(case,6)
    data=deepcopy(case['target_character'].data);data['inventory']['pockets'][0]['pdaState']['on']=False
    case['target_character'].data=data;db.session.commit();tick(case,24)
    assert len(inbox(case))==1
    data=deepcopy(case['target_character'].data);data['inventory']['pockets'][0]['pdaState']['on']=True
    case['target_character'].data=data;db.session.commit();tick(case,60)
    assert len(inbox(case))==1


def test_two_devices_of_one_account_receive_spam_on_only_the_selected_device(spam_game):
    case=spam_game;data=deepcopy(case['target_character'].data)
    second=deepcopy(data['inventory']['pockets'][0]);second['id']='target-second';data['inventory']['pockets'].append(second)
    case['target_character'].data=data;db.session.commit();start(case);tick(case,30)
    assert len(inbox(case,0))==5 and inbox(case,1)==[]
    other=snapshot(case['target_character'].id,case['target_user']['id'],{
        'item_path':['inventory','pockets',1],'item_id':'target-second'})
    assert other['messages']==[]


def test_no_modification_or_stale_revision_cannot_start_job(spam_game):
    case=spam_game;target=read(case)['signals'][0]['signal_id']
    payload={'operation_id':'bad','target_signal_id':target,'text':'Текст','character_revision':0}
    with pytest.raises(ConflictError):write(case,operation='spam',**payload)
    assert not (case['actor_character'].data['inventory']['pockets'][0].get('pdaNetwork') or {}).get('spam_job')
    data=deepcopy(case['actor_character'].data);data['inventory']['pockets'][0]['modifications']=[]
    case['actor_character'].data=data;db.session.commit();payload['character_revision']=case['actor_character'].revision
    with pytest.raises(ValidationError,match='Спам'):write(case,operation='spam',**payload)


def test_real_rounds_cost_three_ap_once_and_require_the_selected_pda_in_hand(spam_game):
    case=spam_game;actor=case['actor_location'];other=case['target_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='active'
    state.current_location_character_id=actor.id;state.turn_order=[actor.id,other.id];state.turn_index=0
    actor.action_points_current=20;db.session.commit()
    target=read(case,location_id=actor.location_id)['signals'][0]['signal_id']
    def launch():return write(case,operation='spam',operation_id='combat-spam',target_signal_id=target,text='Боевой сигнал',
        character_revision=case['actor_character'].revision,location_id=actor.location_id)
    with pytest.raises(ValidationError,match='руку'):launch()
    data=deepcopy(actor.character.data);data['health'].setdefault('combatMeta',{})['heldPda']={'id':'actor-pda'}
    actor.character.data=data;db.session.commit()
    assert launch()[1]['action_points']==3 and actor.action_points_current==17
    assert launch()[1]['replayed'] and actor.action_points_current==17
    for count in range(1,6):
        CombatService.end_turn(actor.location_id,case['actor_user']['id'],actor.id)
        assert len(inbox(case))==count-1
        CombatService.end_turn(actor.location_id,case['actor_user']['id'],other.id)
        assert len(inbox(case))==count


def test_sound_notifications_are_published_only_after_commit_and_cleared_on_rollback(spam_game,monkeypatch):
    case=spam_game;start(case);sent=[]
    monkeypatch.setattr('app.services.pda_spam.socketio.emit',lambda event,payload,**kw:sent.append((event,payload,kw)))
    advance_lobby(case['lobby'],6)
    assert not any(event=='pda_spam_received' for event,_,_ in sent)
    db.session.rollback();db.session.commit()
    assert not any(event=='pda_spam_received' for event,_,_ in sent)
    tick(case,6)
    packets=[(payload,kw) for event,payload,kw in sent if event=='pda_spam_received']
    assert len(packets)==2 and {kw['room'] for _,kw in packets}=={
        f"user_{case['lobby'].gm_id}",f"user_{case['target_user']['id']}"}
    assert all(payload['noise']==10 for payload,_ in packets)


@pytest.mark.parametrize('off_turn,points',[(True,20),(False,2)])
def test_off_turn_and_insufficient_ap_reject_without_job_or_payment(spam_game,off_turn,points):
    case=spam_game;actor=case['actor_location'];other=case['target_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='active'
    state.current_location_character_id=other.id if off_turn else actor.id
    actor.action_points_current=points;data=deepcopy(actor.character.data)
    data['health'].setdefault('combatMeta',{})['heldPda']={'id':'actor-pda'};actor.character.data=data;db.session.commit()
    with pytest.raises(PermissionDenied if off_turn else ValidationError):start(case)
    assert actor.action_points_current==points
    assert not (actor.character.data['inventory']['pockets'][0].get('pdaNetwork') or {}).get('spam_job')


def test_sheet_save_cannot_accelerate_or_rewrite_existing_spam_job(spam_game,client,auth_headers):
    case=spam_game;start(case);tick(case,3)
    character=case['actor_character'];url=f'/lobbies/characters/{character.id}';headers=auth_headers(case['actor_user'])
    incoming=client.get(url,headers=headers).json['data'];job=incoming['inventory']['pockets'][0]['pdaNetwork']['spam_job']
    job.update(elapsed_seconds=5.99,remaining=100,text='Подмена')
    response=client.put(url,headers=headers,json={'data':incoming});assert response.status_code==200,response.json
    tick(case,2);assert inbox(case)==[]
    tick(case,1);assert len(inbox(case))==1 and inbox(case)[0]['text']=='Тестовый сигнал'
    tick(case,24);assert len(inbox(case))==5


def test_real_noise_ten_is_applied_once_per_message_on_local_map(spam_game,monkeypatch):
    from app.services import noise
    case=spam_game;case['target_group'].tile_x=0;db.session.commit();seen=[];original=noise.apply
    def record(*args,**kwargs):seen.append(args[4]);return original(*args,**kwargs)
    monkeypatch.setattr(noise,'apply',record)
    start(case,location_id=case['location'].id);tick(case,30)
    assert seen==[10]*5
    deafness=next(e for e in case['target_character'].data['health']['effects'] if e.get('source')=='noise')
    assert deafness['value']==25


def test_job_on_ground_pda_targets_one_physical_device_and_finishes(spam_game):
    from app.models import LocationObject
    from app.services.pda_devices import registry
    from app.services.pda_access import _radio_sources
    from app.services.pda_spam import start as start_job
    case=spam_game;case['target_group'].tile_x=0
    ground=deepcopy(case['actor_character'].data['inventory']['pockets'][0]);ground['id']='ground-spammer'
    ground['pdaNetwork']={'account_id':case['actor_account'].id}
    box=LocationObject(location_id=case['location'].id,type='container',name='Ящик с КПК',tile_x=10,tile_y=10,properties={'contents':[ground]})
    db.session.add(box);db.session.commit();radios=_radio_sources(registry(case['lobby'].id))
    source=next(r for r in radios if r['item']['id']=='ground-spammer')
    from app.services.pda_network import snapshot_source
    assert snapshot_source(source,radios)['source']['has_spam'] is True
    target=next(r for r in radios if r['item']['id']=='target-pda')
    result=start_job(source,radios,{'operation_id':'ground-spam','target_signal_id':target['signal_id'],'text':'Из ящика'})
    db.session.commit();assert result['remaining']==5
    tick(case,30);assert len(inbox(case))==5 and all(m['text']=='Из ящика' for m in inbox(case))
    assert 'spam_job' not in box.properties['contents'][0]['pdaNetwork']


@pytest.mark.parametrize('text',[None,'','x'*2001])
def test_invalid_message_never_creates_job(spam_game,text):
    case=spam_game;target=read(case)['signals'][0]['signal_id']
    with pytest.raises(ValidationError):
        write(case,operation='spam',operation_id='invalid-text',target_signal_id=target,text=text,character_revision=case['actor_character'].revision)
    assert not (case['actor_character'].data['inventory']['pockets'][0].get('pdaNetwork') or {}).get('spam_job')


def test_target_that_left_visible_radio_range_cannot_be_selected_by_old_signal_id(spam_game):
    case=spam_game;target=read(case)['signals'][0]['signal_id'];case['target_group'].tile_x=15;db.session.commit()
    with pytest.raises(ValidationError,match='недоступен'):
        write(case,operation='spam',operation_id='stale-target',target_signal_id=target,text='Текст',character_revision=case['actor_character'].revision)
    assert not (case['actor_character'].data['inventory']['pockets'][0].get('pdaNetwork') or {}).get('spam_job')


def test_successful_hacker_can_use_target_spam_module_without_taking_its_inventory(access_game):
    from app.services.pda_accounts import account_action
    from app.services.pda_access import target_snapshot
    from test_pda_access import connected,software,selection as access_selection
    case=access_game;target=case['target_character'];data=deepcopy(target.data)
    data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b17'});target.data=data;db.session.commit()
    account_action(target.id,case['target_user']['id'],'register',access_selection(case,'target',login='Spam-target',password='synthetic-spam-password'))
    connected(case)
    view=target_snapshot(case['actor_character'].id,case['actor_user']['id'],access_selection(case,session_id='access-one'))
    assert view['network']['source']['has_spam'] is True
    chosen=view['network']['signals'][0]['signal_id'];software(case,'spam',target_signal_id=chosen,text='Через подключение')
    tick(case,6)
    received=(case['actor_character'].data['inventory']['pockets'][0].get('pdaNetwork') or {}).get('spam_messages') or []
    assert len(received)==1 and received[0]['text']=='Через подключение' and received[0]['sender']=='Spam-target'
