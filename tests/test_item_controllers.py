"""Контроллер следует за физическим зарядом и не допускает повторного взрыва."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState,DetonationEvent,WorldGroup,LocationObject
from app.models.templates import ItemTemplate
from app.services.electronics import controller_templates
from app.services.item_controllers import operate,snapshot,advance,fire,preserve
from app.services.portable_lights import walk
from app.services.world_detonations import pending,add_target
from app.services.exceptions import ValidationError,ConflictError,PermissionDenied
from test_grenade_rules import grenade_game


@pytest.fixture
def controller_game(grenade_game):
    pair=grenade_game
    LocationCombatState.query.filter_by(location_id=pair['location'].id).one().status='idle'
    templates=[]
    for row in controller_templates():
        template=ItemTemplate(**row);db.session.add(template);db.session.flush();templates.append(template)
    data=deepcopy(pair['actor_character'].data)
    data['inventory']['pockets']=[{'id':'pda','name':'КПК','category':'misc','quantity':1},
        {'id':'remote','name':templates[0].name,'templateId':templates[0].id,'category':'device','quantity':2},
        {'id':'charge','name':'Граната РГД-5','category':'grenade','quantity':2},
        {'id':'timer','name':templates[1].name,'templateId':templates[1].id,'category':'device','quantity':1}]
    data['health'].setdefault('combatMeta',{})['heldPda']={'id':'pda'}
    pair['actor_character'].data=data;db.session.commit();return pair


def write(pair,operation,**kwargs):
    actor=pair['actor_character']
    return operate(actor.id,pair['actor_user']['id'],{'operation':operation,'operation_id':kwargs.pop('operation_id',operation),
        'character_revision':actor.revision,**kwargs})


def locate(pair,identity):
    from app.services.portable_lights import walk
    return next((path,item) for path,item in walk(pair['actor_character'].data,True) if item.get('id')==identity)


def install(pair,kind='remote',**kwargs):
    if kind=='remote':
        write(pair,'bind',item_path=locate(pair,'remote')[0],item_id='remote',pda_path=locate(pair,'pda')[0],pda_id='pda')
    return write(pair,'install',item_path=locate(pair,'charge')[0],item_id='charge',
        controller_path=locate(pair,kind)[0],controller_id=kind,**kwargs)[1]


def test_binding_is_free_install_splits_one_and_preserves_other_rounds(controller_game):
    pair=controller_game;pair['actor_character'].data['health']['combatMeta'].pop('heldPda',None)
    result=install(pair)
    items=pair['actor_character'].data['inventory']['pockets']
    assert sum(item.get('quantity',1) for item in items if item['name']=='Граната РГД-5')==2
    live=[item for item in items if item.get('controllerState',{}).get('id')==result['attachment_id']]
    assert len(live)==1 and live[0]['quantity']==1
    assert locate(pair,'remote')[1]['quantity']==1
    assert live[0]['controllerState']['paired_pda_id']=='pda'
    assert result['action_points']>=5


def test_remote_requires_held_live_pda_and_250m_and_consumes_once(controller_game):
    pair=controller_game;pair['actor_character'].data['health']['combatMeta'].pop('heldPda',None)
    identity=install(pair)['attachment_id']
    with pytest.raises(ValidationError,match='в руку'):write(pair,'detonate',attachment_id=identity)
    data=deepcopy(pair['actor_character'].data);data['health']['combatMeta']['heldPda']={'id':'pda'};pair['actor_character'].data=data;db.session.commit()
    original=pair['actor_character'].data['health']['current']
    _,result=write(pair,'detonate',attachment_id=identity,operation_id='detonation')
    db.session.commit()
    assert result['fired'] and DetonationEvent.query.count()==1
    after=pair['actor_character'].data['health']['current'];assert after<original
    _,again=write(pair,'detonate',attachment_id=identity,operation_id='detonation')
    assert again==result and pair['actor_character'].data['health']['current']==after
    assert fire(pair['lobby'].id,identity)['replayed']


def test_timer_ticks_inside_nested_box_and_does_not_fire_twice(controller_game):
    pair=controller_game;pair['actor_character'].data['health']['combatMeta'].pop('heldPda',None)
    identity=install(pair,'timer',seconds=12)['attachment_id']
    data=deepcopy(pair['actor_character'].data)
    charge=next(item for item in data['inventory']['pockets'] if item.get('controllerState',{}).get('id')==identity)
    data['inventory']['pockets'].remove(charge);pair['actor_character'].data=data
    box=LocationObject(location_id=pair['location'].id,type='container',name='Ящик',tile_x=3,tile_y=5,
        properties={'contents':[{'name':'Сумка','contents':[charge]}]})
    db.session.add(box);db.session.commit()
    assert advance(pair['lobby'].id,6)==[]
    assert box.properties['contents'][0]['contents'][0]['controllerState']['remaining_seconds']==6
    result=advance(pair['lobby'].id,6);db.session.commit()
    assert result[0]['fired'] and box.properties['contents'][0]['contents']==[]
    assert advance(pair['lobby'].id,60)==[] and DetonationEvent.query.count()==1


def test_global_carrier_is_automatic_gm_adds_target_once_and_can_close(controller_game):
    pair=controller_game;pair['actor_character'].data['health']['combatMeta'].pop('heldPda',None)
    install(pair,'timer',seconds=2)
    group=WorldGroup(lobby_id=pair['lobby'].id,name='В пути',tile_x=200,tile_y=200,
        member_character_ids=[pair['actor_character'].id],created_by=pair['actor_user']['id'])
    db.session.add(group);db.session.commit()
    before=pair['actor_character'].data['health']['current'];advance(pair['lobby'].id,2);db.session.commit()
    assert pair['actor_character'].data['health']['current']<before
    event=DetonationEvent.query.one();assert event.location_id is None and not event.closed
    assert event.affected_character_ids==[pair['actor_character'].id]
    with pytest.raises(PermissionDenied):pending(pair['lobby'].id,pair['target_user']['id'])
    target=pair['target_character'];before=target.data['health']['current']
    payload={'operation':'add_target','character_id':target.id,'distance':1,'cover_percent':0,'revision':event.revision}
    result=add_target(pair['lobby'].id,pair['lobby'].gm_id,event.id,payload)
    assert target.data['health']['current']<before
    health=deepcopy(target.data['health']);add_target(pair['lobby'].id,pair['lobby'].gm_id,event.id,payload)
    assert target.data['health']==health
    add_target(pair['lobby'].id,pair['lobby'].gm_id,event.id,{'operation':'close','revision':result['revision']})
    assert pending(pair['lobby'].id,pair['lobby'].gm_id)==[]


def test_disarm_is_automatic_three_ap_and_sheet_cannot_forge_controller(controller_game):
    pair=controller_game;pair['actor_character'].data['health']['combatMeta'].pop('heldPda',None)
    identity=install(pair,'timer',seconds=1)['attachment_id']
    path,item=next((path,item) for path,item in walk(pair['actor_character'].data,True)
        if item.get('controllerState',{}).get('id')==identity)
    _,result=write(pair,'disarm',item_path=path,item_id=item['id'])
    assert result['action_points']==3 and not result['roll_required']
    assert advance(pair['lobby'].id,100)==[]
    old=pair['actor_character'].data;incoming=deepcopy(old)
    incoming['inventory']['pockets'][0]['controllerState']={'id':'forged','kind':'timer','armed':True}
    preserve(old,incoming)
    assert 'controllerState' not in incoming['inventory']['pockets'][0]


def test_install_uses_common_multiturn_combat_payment(controller_game):
    from app.services.combat import CombatService
    pair=controller_game;actor=pair['actor_location']
    data=deepcopy(actor.character.data);data['health']['combatMeta'].pop('heldPda',None);actor.character.data=data
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='active';actor.action_points_current=2;db.session.commit()
    payload={'operation':'install','item_path':locate(pair,'charge')[0],'item_id':'charge','controller_path':locate(pair,'timer')[0],'controller_id':'timer','seconds':20}
    result=CombatService.perform_action(actor.location_id,pair['actor_user']['id'],actor.id,'controller',attribute_choice=payload,pending_action_id='test-controller-payment')
    assert result['pending_action']
    assert not any(item.get('controllerState',{}).get('kind')=='timer' for item in actor.character.data['inventory']['pockets'])
    CombatService.end_turn(actor.location_id,pair['target_user']['id'],pair['target_location'].id)
    result=CombatService.perform_action(actor.location_id,pair['actor_user']['id'],actor.id,'controller',resume_pending_action_id='test-controller-payment')
    assert result['narrative_action']['attachment_id']
    assert sum(bool(item.get('controllerState',{}).get('kind')=='timer') for item in actor.character.data['inventory']['pockets'])==1


def move_charge_to_box(pair,identity,x=10):
    data=deepcopy(pair['actor_character'].data)
    charge=next(item for _,item in walk(data,True) if item.get('controllerState',{}).get('id')==identity)
    data['inventory']['pockets'].remove(charge);pair['actor_character'].data=data
    box=LocationObject(location_id=pair['location'].id,type='box',name='Ящик',tile_x=x,tile_y=5.2,properties={'contents':[charge]})
    db.session.add(box);db.session.commit();return box


def test_remote_rechecks_distance_and_combat_command_costs_one_ap(controller_game):
    from app.services.combat import CombatService
    pair=controller_game;actor=pair['actor_location'];actor.character.data['health']['combatMeta'].pop('heldPda',None)
    identity=install(pair)['attachment_id'];box=move_charge_to_box(pair,identity,x=actor.pos_x+250)
    selection={'item_path':locate(pair,'pda')[0],'item_id':'pda','location_id':actor.location_id}
    assert snapshot(actor.character,pair['actor_user']['id'],selection)['charges'][0]['reachable']
    box.tile_x+=.001;db.session.commit()
    assert not snapshot(actor.character,pair['actor_user']['id'],selection)['charges'][0]['reachable']
    box.tile_x=10;data=deepcopy(actor.character.data);data['health']['combatMeta']['heldPda']={'id':'pda'};actor.character.data=data
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='active';db.session.commit()
    before=actor.action_points_current
    result=CombatService.perform_action(actor.location_id,pair['actor_user']['id'],actor.id,'controller',attribute_choice={'operation':'detonate','attachment_id':identity})
    assert result['narrative_action']['fired'] and actor.action_points_current==before-1
    assert box.properties['contents']==[]


def test_wire_can_be_disconnected_in_box_but_not_from_far_away(controller_game):
    pair=controller_game;actor=pair['actor_location'];actor.character.data['health']['combatMeta'].pop('heldPda',None)
    identity=install(pair,'timer',seconds=1)['attachment_id'];box=move_charge_to_box(pair,identity,x=10)
    with pytest.raises(ValidationError,match='Подойдите'):write(pair,'disarm',attachment_id=identity)
    box.tile_x=actor.pos_x+.5;db.session.commit()
    write(pair,'disarm',attachment_id=identity)
    assert not box.properties['contents'][0]['controllerState']['armed'] and advance(pair['lobby'].id,10)==[]


def test_controlled_grenade_lands_instead_of_starting_normal_fuse(controller_game,monkeypatch):
    from app.services.combat import CombatService
    pair=controller_game;actor=pair['actor_location'];actor.character.data['health']['combatMeta'].pop('heldPda',None)
    identity=install(pair,'timer',seconds=18)['attachment_id']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='active';db.session.commit()
    path,item=next((path,item) for path,item in walk(actor.character.data,True) if item.get('controllerState',{}).get('id')==identity)
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:b)
    result=CombatService.perform_action(actor.location_id,pair['actor_user']['id'],actor.id,'explosive_attack',explosive_source='hand',
        item_path=path,target_x=8,target_y=5,target_floor_level=0)
    explosive=result['explosive'];assert not explosive['detonated'] and not state.pending_explosives
    obj=db.session.get(LocationObject,explosive['ground_object_id'])
    assert obj.properties['contents'][0]['controllerState']['id']==identity
    assert obj.properties['contents'][0]['controllerState']['remaining_seconds']==18


def test_timer_advances_once_per_full_round_not_per_actor(controller_game):
    from app.services.combat import CombatService
    pair=controller_game;actor=pair['actor_location'];actor.character.data['health']['combatMeta'].pop('heldPda',None)
    identity=install(pair,'timer',seconds=12)['attachment_id'];box=move_charge_to_box(pair,identity)
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='active';db.session.commit()
    CombatService.end_turn(actor.location_id,pair['actor_user']['id'],actor.id)
    assert box.properties['contents'][0]['controllerState']['remaining_seconds']==12
    CombatService.end_turn(actor.location_id,pair['target_user']['id'],pair['target_location'].id)
    assert box.properties['contents'][0]['controllerState']['remaining_seconds']==6


def test_gm_notification_waits_for_commit_and_rollback_discards_it(controller_game,monkeypatch):
    pair=controller_game;pair['actor_character'].data['health']['combatMeta'].pop('heldPda',None)
    install(pair,'timer',seconds=1)
    calls=[];monkeypatch.setattr('app.services.item_controllers.socketio.emit',lambda name,*args,**kwargs:calls.append(name))
    advance(pair['lobby'].id,1)
    assert 'detonation_pending' not in calls
    db.session.rollback()
    assert 'detonation_pending' not in calls and DetonationEvent.query.count()==0
    advance(pair['lobby'].id,1);db.session.commit()
    assert calls.count('detonation_pending')==1


def test_controller_migration_creates_table_and_preserves_existing_custom_template(app,monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from sqlalchemy import inspect
    custom=ItemTemplate(name='Таймер',category='device',price=123,attributes={'custom':True});db.session.add(custom);db.session.commit()
    connection=db.session.connection();DetonationEvent.__table__.drop(connection)
    path=Path(__file__).resolve().parents[1]/'migrations/versions/b8e50f3162ac_add_controlled_devices.py'
    spec=importlib.util.spec_from_file_location('controlled_devices_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(connection)))
    migration.upgrade();db.session.flush();db.session.expire_all()
    assert inspect(connection).has_table('detonation_events')
    assert custom.price==123 and custom.attributes=={'custom':True}
    assert ItemTemplate.query.filter_by(name='Дистанционный взрыватель',category='device').one().price==1000


@pytest.mark.parametrize('device',['pda','dosimeter','light','unprepared_light','nvg'])
def test_timer_controls_real_device_power_and_only_elapsed_active_time(controller_game,device):
    from app.services.portable_lights import advance_lobby
    pair=controller_game;character=pair['actor_character'];data=deepcopy(character.data);meta=data['health']['combatMeta'];meta.pop('heldPda',None)
    if device=='pda':
        target=data['inventory']['pockets'][0];target['pdaState']={'on':False,'charge_percent':50}
    else:
        target={'id':'scheduled-device','name':'Дозиметр' if device=='dosimeter' else 'Фонарик','category':'device','quantity':1}
        if device=='dosimeter':target.update(attributes={'device_kind':'dosimeter'},deviceState={'on':False,'remaining_seconds':10000})
        if device in {'light','unprepared_light'}:
            target['lightPower']={'uses_left':10,'elapsed_seconds':0}
            if device=='light':meta['portableLights']=[{'id':target['id'],'mount':'hand','on':False}]
        if device=='nvg':
            target.update(name='ПНВ',slotType='nvg',active=False,enabled=False,lightPower={'uses_left':10,'elapsed_seconds':0})
            data.setdefault('equipment',{})['helmet']={'installedModules':[target]}
        else:data['inventory']['pockets'].append(target)
    character.data=data;db.session.commit()
    path=next(path for path,item in walk(character.data,True) if item.get('id')==target['id'])
    write(pair,'install',item_path=path,item_id=target['id'],controller_path=locate(pair,'timer')[0],controller_id='timer',seconds=60,mode='on')
    advance_lobby(pair['lobby'],120);db.session.commit()
    live=next(item for _,item in walk(character.data,True) if item.get('id')==target['id'])
    assert live['controllerState']['armed'] is False and DetonationEvent.query.count()==0
    if device=='pda':assert live['pdaState']['on'] and live['pdaState']['charge_percent']==pytest.approx(50-.5*60/3600)
    elif device=='dosimeter':assert live['deviceState']=={'on':True,'remaining_seconds':9940}
    else:assert live['lightPower']['elapsed_seconds']==60 and live['lightPower']['uses_left']==10


def test_timer_can_switch_pda_off_or_press_button_without_destroying_device(controller_game):
    from app.services.portable_lights import advance_lobby
    pair=controller_game;character=pair['actor_character'];data=deepcopy(character.data);data['health']['combatMeta'].pop('heldPda',None)
    data['inventory']['pockets'][0]['pdaState']={'on':True,'charge_percent':50};character.data=data;db.session.commit()
    write(pair,'install',item_path=locate(pair,'pda')[0],item_id='pda',controller_path=locate(pair,'timer')[0],controller_id='timer',seconds=60,mode='off')
    advance_lobby(pair['lobby'],120);db.session.commit()
    assert locate(pair,'pda')[1]['pdaState']['charge_percent']==pytest.approx(50-.5*60/3600)
    assert not locate(pair,'pda')[1]['pdaState']['on']
    write(pair,'timer',item_path=locate(pair,'pda')[0],item_id='pda',seconds=3600,mode='on')
    write(pair,'activate',item_path=locate(pair,'pda')[0],item_id='pda',mode='on')
    assert locate(pair,'pda')[1]['pdaState']['on'] and DetonationEvent.query.count()==0
    write(pair,'activate',operation_id='physical-off',item_path=locate(pair,'pda')[0],item_id='pda',mode='off')
    assert not locate(pair,'pda')[1]['pdaState']['on']


def test_empty_device_timer_reports_failure_without_blocking_world_clock(controller_game):
    from app.services.portable_lights import advance_lobby
    pair=controller_game;character=pair['actor_character'];data=deepcopy(character.data);data['health']['combatMeta'].pop('heldPda',None)
    data['inventory']['pockets'][0]['pdaState']={'on':False,'charge_percent':0};character.data=data;db.session.commit()
    write(pair,'install',item_path=locate(pair,'pda')[0],item_id='pda',controller_path=locate(pair,'timer')[0],controller_id='timer',seconds=60,mode='on')
    advance_lobby(pair['lobby'],120);db.session.commit()
    state=locate(pair,'pda')[1]['controllerState']
    assert not state['armed'] and 'разряжен' in state['error']


def test_detonated_ground_charge_leaves_no_empty_ground_object(controller_game):
    pair=controller_game;pair['actor_character'].data['health']['combatMeta'].pop('heldPda',None)
    identity=install(pair,'timer',seconds=1)['attachment_id'];box=move_charge_to_box(pair,identity)
    box.type='ground_item';db.session.commit();object_id=box.id
    result=advance(pair['lobby'].id,1);db.session.commit()
    assert result[0]['ground_object_event']=='deleted' and db.session.get(LocationObject,object_id) is None
