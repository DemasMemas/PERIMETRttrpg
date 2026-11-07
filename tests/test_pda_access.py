"""Взлом требует дешифратор и прекращается при потере настоящего соединения."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState,PdaAccessSession
from app.services import pda_devices
from app.services.pda_access import operate,advance,snapshot
from app.services.exceptions import ValidationError,PermissionDenied,ConflictError,NotFoundError
from app.services.container_transfer import ContainerTransferService
from test_grenade_rules import grenade_game


@pytest.fixture
def access_game(grenade_game):
    pair=grenade_game;LocationCombatState.query.filter_by(location_id=pair['location'].id).one().status='idle'
    for side,mods in [('actor',['pda-b18','pda-b25','pda-b11']),('target',[])]:
        character=pair[side+'_character'];data=deepcopy(character.data)
        data['inventory']={'pockets':[{'id':side+'-pda','name':'КПК','category':'misc','quantity':1,
            'pdaState':{'on':True,'charge_percent':100},'modifications':[{'ruleId':rule} for rule in mods]}],'backpack':[]}
        data['health']={'current':700,'max':700,'effects':[]};character.data=data
    db.session.commit();return pair


def selection(pair,side='actor',**extra):return {'item_path':['inventory','pockets',0],'item_id':side+'-pda',**extra}
def target_token(pair,identity='target-pda'):return next(record['token'] for record in pda_devices.registry(pair['lobby'].id) if record['id']==identity)
def write(pair,side='actor',**extra):
    character=pair[side+'_character']
    return operate(character.id,pair[side+'_user']['id'],selection(pair,side,character_revision=character.revision,**extra))
def start(pair,mode='remote',**extra):return write(pair,operation='start',operation_id='access-one',mode=mode,target_token=target_token(pair),**extra)


@pytest.mark.parametrize('rule,seconds',[('pda-b11',7200),('pda-b19',3600),('pda-b26',1800)])
def test_decipher_order_sets_duration_even_for_unencrypted_target(access_game,rule,seconds):
    pair=access_game;actor=pair['actor_character'];data=deepcopy(actor.data)
    data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b25'},{'ruleId':rule}];actor.data=data;db.session.commit()
    _,result=start(pair);row=db.session.get(PdaAccessSession,result['session']['id'])
    assert row.remaining_seconds==seconds and row.status=='working'
    advance(pair['lobby'].id,seconds-1);db.session.commit();assert row.status=='working'
    advance(pair['lobby'].id,1);db.session.commit();assert row.status=='active'
    _,replayed=start(pair);assert replayed['replayed'] and PdaAccessSession.query.count()==1


def test_no_decipher_cannot_hack_even_without_cipher(access_game):
    pair=access_game;data=deepcopy(pair['actor_character'].data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b25'}];pair['actor_character'].data=data;db.session.commit()
    with pytest.raises(ValidationError,match='дешифратор'):start(pair)
    assert PdaAccessSession.query.count()==0


def test_stronger_cipher_requires_adequate_decipher(access_game):
    pair=access_game;data=deepcopy(pair['target_character'].data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b12'}];pair['target_character'].data=data;db.session.commit()
    with pytest.raises(ValidationError,match='дешифратор'):start(pair)
    data=deepcopy(pair['actor_character'].data);data['inventory']['pockets'][0]['modifications'].append({'ruleId':'pda-b26'});pair['actor_character'].data=data;db.session.commit()
    _,result=start(pair);assert result['session']['remaining_seconds']==1800


def test_leaving_radio_range_closes_session_before_return_even_without_ui_poll(access_game):
    pair=access_game;start(pair);row=PdaAccessSession.query.one();target=pair['target_location'];original=target.pos_x
    target.pos_x=pair['actor_location'].pos_x+400;db.session.commit()
    assert row.status=='closed' and 'радиосвязь' in row.data['closed_reason']
    target.pos_x=original;db.session.commit();advance(pair['lobby'].id,10000);db.session.commit()
    assert row.status=='closed'


@pytest.mark.parametrize('change',['off','broken','mask','remove_decipher'])
def test_power_mask_and_tools_are_rechecked_on_server_state_change(access_game,change):
    pair=access_game;start(pair);row=PdaAccessSession.query.one();target=pair['target_character']
    if change=='remove_decipher':
        target=pair['actor_character'];data=deepcopy(target.data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b25'}]
    else:
        data=deepcopy(target.data);item=data['inventory']['pockets'][0]
        if change=='off':item['pdaState']['on']=False
        if change=='broken':item['pdaState'].update(on=False,broken=True)
        if change=='mask':item['modifications'].append({'ruleId':'pda-b9'})
    target.data=data;db.session.commit();assert row.status=='closed'


def test_physical_connection_requires_access_and_breaks_when_phone_is_moved_to_box(access_game):
    pair=access_game;actor=pair['actor_character'];data=deepcopy(actor.data)
    data['inventory']['pockets'].append({'id':'own-target','name':'КПК','category':'misc','quantity':1});actor.data=data;db.session.commit()
    _,result=write(pair,operation='start',operation_id='own-wire',mode='physical',target_token=target_token(pair,'own-target'))
    row=db.session.get(PdaAccessSession,result['session']['id']);assert row.status=='working'
    item=deepcopy(actor.data['inventory']['pockets'][1])
    ContainerTransferService.drop(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],{
        'character_id':actor.id,'character_revision':actor.revision,'item_path':['inventory','pockets',1],'expected_item':item,'amount':1})
    assert row.status=='closed' and 'Кабель' in row.data['closed_reason']
    with pytest.raises(ValidationError,match='физического доступа'):start(pair,mode='physical')


@pytest.mark.parametrize('detection,notified',[(75,True),(76,False)])
def test_defender_notice_has_seventy_five_percent_boundary(access_game,monkeypatch,detection,notified):
    pair=access_game;data=deepcopy(pair['target_character'].data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b24'}];pair['target_character'].data=data;db.session.commit()
    start(pair);monkeypatch.setattr('app.services.pda_access.random.randint',lambda a,b:detection)
    advance(pair['lobby'].id,7200);db.session.commit();assert PdaAccessSession.query.one().notified is notified


def test_counterattack_burns_attacker_and_repeat_does_not_reroll(access_game,monkeypatch):
    pair=access_game;data=deepcopy(pair['target_character'].data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b24'}];pair['target_character'].data=data;db.session.commit()
    start(pair);monkeypatch.setattr('app.services.pda_access.random.randint',lambda a,b:25)
    advance(pair['lobby'].id,7200);db.session.commit()
    _,result=write(pair,'target',operation='counter',operation_id='counter-once',session_id='access-one')
    assert result['success'] and pair['actor_character'].data['inventory']['pockets'][0]['pdaState']['broken']
    assert PdaAccessSession.query.one().status=='closed'
    monkeypatch.setattr('app.services.pda_access.random.randint',lambda a,b:pytest.fail('Повторный бросок контратаки'))
    _,again=write(pair,'target',operation='counter',operation_id='counter-once',session_id='access-one')
    assert again==result


def test_access_snapshot_does_not_grant_another_characters_sheet(access_game):
    pair=access_game;actor=pair['actor_character']
    with pytest.raises(PermissionDenied):snapshot(actor.id,pair['target_user']['id'],selection(pair))


def connected(pair):
    start(pair);advance(pair['lobby'].id,7200);db.session.commit()
    return {'session_id':'access-one'}


def software(pair,operation,identity=None,**extra):
    from app.services.pda_access import target_action
    return target_action(pair['actor_character'].id,pair['actor_user']['id'],selection(pair,
        session_id='access-one',operation=operation,operation_id=identity or operation,**extra))


def test_full_access_uses_target_account_without_leaking_carrier_or_password(access_game):
    from app.services.pda_accounts import account_action
    from app.services.pda_access import target_snapshot
    pair=access_game;target=pair['target_character'];secret=deepcopy(target.data)
    secret['private_gm_note']='Не входит в КПК';secret['inventory']['pockets'][0]['contents']=[{
        'id':'book','name':'Книга','attributes':{'book_kind':'skill','book_subject':'Сила'},'quantity':1}];target.data=secret;db.session.commit()
    account_action(target.id,pair['target_user']['id'],'register',selection(pair,'target',login='Target',password='target-secret'))
    account_action(target.id,pair['target_user']['id'],'save_notes',selection(pair,'target',notes='На устройстве'))
    connected(pair)
    view=target_snapshot(pair['actor_character'].id,pair['actor_user']['id'],selection(pair,session_id='access-one'))
    assert view['account']['notes']=='На устройстве' and view['files'][0]['id']=='book'
    assert 'private_gm_note' not in str(view) and 'target-secret' not in str(view) and 'password_hash' not in str(view)
    software(pair,'save_notes',notes='Изменены по соединению')
    assert target_snapshot(pair['actor_character'].id,pair['actor_user']['id'],selection(pair,session_id='access-one'))['account']['notes']=='Изменены по соединению'
    with pytest.raises(PermissionDenied):target_snapshot(pair['actor_character'].id,pair['target_user']['id'],selection(pair,session_id='access-one'))
    with pytest.raises(NotFoundError):target_snapshot(target.id,pair['target_user']['id'],selection(pair,'target',session_id='access-one'))
    software(pair,'logout')
    assert target.data['inventory']['pockets'][0]['pdaNetwork']['account_id'] is None
    software(pair,'login',login='Target',password='target-secret')
    assert target.data['inventory']['pockets'][0]['contents'][0]['id']=='book'
    pair['target_location'].pos_x=500;db.session.commit()
    with pytest.raises(ConflictError):software(pair,'save_notes','after-disconnect',notes='Нельзя')


def test_messages_from_hacked_pda_are_sent_as_target_not_attacker(access_game):
    from app.services.pda_accounts import account_action
    from app.services.pda_network import snapshot as network_snapshot
    pair=access_game
    for side in ('actor','target'):
        account_action(pair[side+'_character'].id,pair[side+'_user']['id'],'register',selection(pair,side,login=side,password='test-secret'))
    connected(pair);software(pair,'send',text='От имени владельца КПК')
    messages=network_snapshot(pair['actor_character'].id,pair['actor_user']['id'],selection(pair,location_id=pair['location'].id))['messages']
    assert len(messages)==1 and messages[0]['sender']=='target' and not messages[0]['outgoing']
    software(pair,'send',text='От имени владельца КПК')
    from app.models import PdaTransmission
    assert PdaTransmission.query.count()==1


def test_remote_power_off_closes_access_and_replayed_operation_does_not_turn_it_back_on(access_game):
    pair=access_game;connected(pair)
    _,result=write(pair,operation='power',operation_id='power-off',session_id='access-one',enabled=False)
    assert result['action_points']==1 and PdaAccessSession.query.one().status=='closed'
    assert not pair['target_character'].data['inventory']['pockets'][0]['pdaState']['on']
    _,replayed=write(pair,operation='power',operation_id='power-off',session_id='access-one',enabled=False)
    assert replayed==result


def test_combat_start_and_target_power_each_use_normal_ap_payment(access_game):
    from app.services.combat import CombatService
    pair=access_game;actor=pair['actor_location'];state=LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    state.status='active';db.session.commit();before=actor.action_points_current
    result=CombatService.perform_action(pair['location'].id,pair['actor_user']['id'],actor.id,'pda_access',
        attribute_choice=selection(pair,operation='start',operation_id='combat-access',mode='remote',target_token=target_token(pair)))
    assert actor.action_points_current==before-3 and result['narrative_action']['session']['status']=='working'
    advance(pair['lobby'].id,7200);db.session.commit();before=actor.action_points_current
    CombatService.perform_action(pair['location'].id,pair['actor_user']['id'],actor.id,'pda_access',
        attribute_choice=selection(pair,operation='power',operation_id='combat-off',session_id='combat-access',enabled=False))
    assert actor.action_points_current==before-1 and PdaAccessSession.query.one().status=='closed'


def test_hacking_http_routes_require_completed_session_and_publish_only_allowed_character_data(access_game,client,auth_headers):
    pair=access_game;actor=pair['actor_character'];headers=auth_headers(pair['actor_user'])
    path=f'/lobbies/characters/{actor.id}/pda-access'
    response=client.post(path,headers=headers,json=selection(pair,operation='start',operation_id='http',mode='remote',
        target_token=target_token(pair),character_revision=actor.revision))
    assert response.status_code==200,response.json
    import json
    query={'item_path':json.dumps(selection(pair)['item_path']),'item_id':'actor-pda','session_id':'http'}
    assert client.get(path,headers=headers,query_string=query).status_code==409
    advance(pair['lobby'].id,7200);db.session.commit()
    response=client.get(path,headers=headers,query_string=query)
    assert response.status_code==200 and set(response.json)=={'session','device','account','network','files','charges'}


def test_hacked_charge_uses_target_transmitter_and_existing_explosion_engine_once(access_game):
    from app.services.pda_access import target_snapshot
    pair=access_game;actor=pair['actor_character'];target=pair['target_character']
    pair['target_location'].pos_x=280;pair['target_location'].pos_y=5.2
    data=deepcopy(actor.data);data['health'].setdefault('combatMeta',{})['heldPda']={'id':'actor-pda'};actor.data=data
    data=deepcopy(target.data);data['inventory']['pockets'].append({'id':'charge-item','name':'Граната РГД-5','category':'grenade','quantity':1,
        'controllerState':{'id':'attached-charge','kind':'remote_detonator','paired_pda_id':'target-pda','armed':True,
            'label':'Заряд цели','source_character_id':target.id}});target.data=data;db.session.commit()
    connected(pair)
    view=target_snapshot(actor.id,pair['actor_user']['id'],selection(pair,session_id='access-one'))
    assert view['charges']==[{'id':'attached-charge','label':'Заряд цели','armed':True,'reachable':True}]
    before=target.data['health']['current']
    _,result=write(pair,operation='detonate',operation_id='fire-once',session_id='access-one',attachment_id='attached-charge')
    assert result['action_points']==1 and target.data['health']['current']<before
    assert not any(item.get('id')=='charge-item' for item in target.data['inventory']['pockets'])
    health=deepcopy(target.data['health']);_,again=write(pair,operation='detonate',operation_id='fire-once',session_id='access-one',attachment_id='attached-charge')
    assert again==result and target.data['health']==health


def test_physical_cable_is_lost_during_detour_even_if_endpoint_returns_near_target(access_game):
    from app.services.pda_access import check_route
    pair=access_game;target=pair['target_location'];actor=pair['actor_location'];target.pos_x,target.pos_y=3.1,5.2;db.session.commit()
    # ГМ управляет обоими персонажами: физический доступ разрешён, кабель 1 м.
    start(pair,mode='physical');row=PdaAccessSession.query.one()
    check_route(actor,{'path':[(2.1,5.2),(0,5.2),(2.1,5.2)]});db.session.commit()
    assert row.status=='closed' and 'Кабель' in row.data['closed_reason']


def test_session_schema_migration_works_in_isolated_database(access_game,monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    PdaAccessSession.__table__.drop(db.session.connection())
    path=Path(__file__).resolve().parents[1]/'migrations/versions/da072c5384ce_add_pda_access_sessions.py'
    spec=importlib.util.spec_from_file_location('pda_access_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(db.session.connection())))
    migration.upgrade()
    assert 'source_device_id' in {column['name'] for column in inspect(db.session.connection()).get_columns('pda_access_sessions')}
    start(access_game);assert PdaAccessSession.query.count()==1


def test_hacking_can_continue_next_turn_without_charge_drain_invalidating_bound_device(access_game):
    from app.services.combat import CombatService
    pair=access_game;actor=pair['actor_location'];state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    state.status='active';actor.action_points_current=1;db.session.commit()
    payload=selection(pair,operation='start',operation_id='deferred-access',mode='remote',target_token=target_token(pair))
    result=CombatService.perform_action(actor.location_id,pair['actor_user']['id'],actor.id,'pda_access',attribute_choice=payload,pending_action_id='deferred-hacking-payment')
    assert result['pending_action'] and PdaAccessSession.query.count()==0
    CombatService.end_turn(actor.location_id,pair['target_user']['id'],pair['target_location'].id)
    result=CombatService.perform_action(actor.location_id,pair['actor_user']['id'],actor.id,'pda_access',resume_pending_action_id='deferred-hacking-payment')
    assert result['narrative_action']['session']['status']=='working' and PdaAccessSession.query.count()==1


def test_changing_location_id_closes_connection_even_with_old_relationship_loaded(access_game):
    from app.models import Location
    pair=access_game;connected(pair);target=pair['target_location'];assert target.location.id==pair['location'].id
    distant=Location(lobby_id=pair['lobby'].id,name='Дальняя сцена',world_tile_x=10,world_tile_z=0)
    db.session.add(distant);db.session.flush();target.location_id=distant.id;db.session.commit()
    assert PdaAccessSession.query.one().status=='closed'


def test_gm_placement_selects_current_scene_without_reopening_closed_session(access_game,client,auth_headers):
    from app.models import Location
    pair=access_game;connected(pair);target=pair['target_character'];headers=auth_headers(pair['actor_user'])
    distant=Location(lobby_id=pair['lobby'].id,name='Дальняя копия',world_tile_x=10,world_tile_z=0)
    db.session.add(distant);db.session.commit();prefix=f"/lobbies/{pair['lobby'].id}/locations/"
    response=client.post(prefix+str(distant.id)+'/spawn_character',headers=headers,json={'character_id':target.id,'tile_x':5,'tile_y':5})
    assert response.status_code==200,response.json
    assert PdaAccessSession.query.one().status=='closed'
    response=client.post(prefix+str(pair['location'].id)+'/spawn_character',headers=headers,json={'character_id':target.id,'tile_x':25,'tile_y':25})
    assert response.status_code==200,response.json
    view=snapshot(pair['actor_character'].id,pair['actor_user']['id'],selection(pair))
    assert any(entry['token']==target_token(pair) for entry in view['targets'])
    assert view['sessions'][0]['status']=='closed'


def reading(pair,identity='read-remote'):
    from app.models.templates import ItemTemplate
    from app.services.character_starting_purchase import item_from_template
    from app.services.crafting import operate as craft
    template=ItemTemplate(name='Физическая электронная книга. Раздел: Сила',category='misc',price=100,weight=0,volume=0,
        attributes={'book_kind':'skill','book_subject':'Физические','book_skill_path':'physical.strength','reading_seconds':14400})
    db.session.add(template);db.session.flush();book=item_from_template(template);book['id']='single-book'
    target=pair['target_character'];data=deepcopy(target.data);data['inventory']['pockets'][0]['contents']=[book];target.data=data;db.session.commit()
    connected(pair);actor=pair['actor_character']
    return craft(actor.id,pair['actor_user']['id'],{'operation':'read_book','operation_id':identity,'character_revision':actor.revision,
        'remote_source':{'session_id':'access-one','source_id':'actor-pda'},'book_id':'single-book','skill_path':'physical.strength'})


def test_remote_book_uses_existing_four_hour_reading_without_copying_the_file(access_game):
    from app.services.crafting import complete_ready_book,now
    from app.services.portable_lights import walk
    pair=access_game;_,job=reading(pair);actor=pair['actor_character'];target=pair['target_character']
    assert job['ready_at']-job['started_at']==14400
    assert sum(item.get('id')=='single-book' for person in (actor,target) for _,item in walk(person.data,True))==1
    assert not complete_ready_book(actor)
    pair['lobby'].game_time_minutes+=240;db.session.commit()
    assert now(actor)>=job['ready_at'] and complete_ready_book(actor);db.session.commit()
    assert actor.data['crafting']['library'][0]['skill_path']=='physical.strength'
    assert target.data['inventory']['pockets'][0]['contents'][0]['id']=='single-book'
    assert 'current' not in actor.data['crafting'] and not complete_ready_book(actor)


@pytest.mark.parametrize('loss',['connection','file','source_phone'])
def test_remote_reading_is_cancelled_on_connection_file_or_source_loss(access_game,loss):
    from app.services.crafting import complete_ready_book
    pair=access_game;reading(pair);actor=pair['actor_character'];target=pair['target_character']
    if loss=='connection':pair['target_location'].pos_x=500
    else:
        holder=target if loss=='file' else actor;data=deepcopy(holder.data)
        if loss=='file':data['inventory']['pockets'][0]['contents']=[]
        else:data['inventory']['pockets']=[]
        holder.data=data
    db.session.commit()
    assert 'current' not in actor.data['crafting'] and actor.data['crafting']['history'][-1]['cancelled']
    pair['lobby'].game_time_minutes+=240;db.session.commit()
    assert not complete_ready_book(actor) and not actor.data['crafting'].get('library')
