"""Реальное выполнение обычных прыжков №98–100 через игровой API."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import DeferredCombatAction, LocationObject
from app.services.combat import CombatService
from app.services.exceptions import ValidationError
from test_character_interactions import _setup_pair


@pytest.fixture
def jumping(client,create_user,auth_headers):
    p=_setup_pair(client,create_user,auth_headers)
    location=p['location']
    location.grid_width=location.grid_height=10
    location.tiles_data=[[{'height':1,'terrain':'grass','objects':[]} for _ in range(10)] for _ in range(10)]
    actor=p['actor_location']
    actor.pos_x,actor.pos_y=2,2
    actor.facing_x,actor.facing_y=1,0
    actor.action_points_current=5
    actor.movement_points_current=10
    data=deepcopy(actor.character.data)
    data['skills']={'physical':{'strength':{'base':10,'bonus':0},'agility':{'base':10,'bonus':0}}}
    data['health']={'current':700,'max':700,'effects':[]}
    actor.character.data=data
    p['target_location'].pos_x=p['target_location'].pos_y=8
    db.session.commit()
    return p


def jump(client,p,headers,**payload):
    return client.post(f"/lobbies/{p['lobby'].id}/locations/{p['location'].id}/combat/action",
        headers=headers(p['actor_user']),json={'location_character_id':p['actor_location'].id,
        'action_key':'jump','target_x':4,'target_y':2,**payload})


@pytest.mark.parametrize('roll,outcome,x',[(10,'success',4),(9,'unstable_edge',4),(7,'unstable_edge',4),(6,'midpoint',3)])
def test_jump_uses_real_roll_cost_and_exact_landing(client,jumping,auth_headers,monkeypatch,roll,outcome,x):
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:roll)
    r=jump(client,jumping,auth_headers)
    assert r.status_code==200,r.json
    assert r.json['jump']['outcome']==outcome
    actor=jumping['actor_location']
    assert (actor.pos_x,actor.pos_y)==(x,2)
    assert actor.action_points_current==3 and actor.movement_points_current==10
    assert (actor.facing_x,actor.facing_y)==(1,0)
    assert bool(r.json['character']['unstable_jump'])==(outcome=='unstable_edge')


@pytest.mark.parametrize('mode',['run','sprint'])
@pytest.mark.parametrize('roll,x,outcome',[(6,5.5,'success'),(5,3.75,'midpoint'),(1,3.75,'midpoint')])
def test_running_jump_needs_no_metres_and_any_failure_uses_exact_midpoint(client,jumping,auth_headers,monkeypatch,mode,roll,x,outcome):
    actor=jumping['actor_location'];actor.movement_mode_this_turn=mode
    actor.movement_distance_this_turn=0
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:roll)
    r=jump(client,jumping,auth_headers,target_x=5.5)
    assert r.status_code==200,r.json
    assert actor.pos_x==x and r.json['jump']['outcome']==outcome
    assert r.json['jump']['check']['difficulty']==6
    assert actor.action_points_current==3
    assert not r.json['character']['unstable_jump']


@pytest.mark.parametrize('success',[True,False])
def test_unstable_edge_blocks_move_and_turn_and_free_balance_check_can_stumble(client,jumping,auth_headers,monkeypatch,success):
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:9)
    assert jump(client,jumping,auth_headers).status_code==200
    actor=jumping['actor_location']
    with pytest.raises(ValidationError,match='равновесие'):
        CombatService.move_character(jumping['location'].id,jumping['actor_user']['id'],actor.character_id,4.5,2)
    denied=jump(client,jumping,auth_headers,action_key='change_facing',facing_x=0,facing_y=1)
    assert denied.status_code==400,denied.json
    # Отказ от проверки и чтение состояния ничего не меняют и не бросают кубик.
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:pytest.fail('чтение не бросает кубик'))
    state=CombatService.get_state(jumping['location'].id,jumping['actor_user']['id'])
    assert state['current_character']['unstable_jump']
    assert actor.action_points_current==3
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:12 if success else 11)
    r=jump(client,jumping,auth_headers,action_key='recover_jump_balance')
    assert r.status_code==200,r.json
    assert actor.pos_x==(4 if success else 3.5)
    assert not r.json['character']['unstable_jump']
    assert actor.action_points_current==3 and actor.free_actions_current==1


@pytest.mark.parametrize('rise,allowed',[(1,True),(1.01,False),(-5,True)])
def test_jump_rise_is_one_metre_inclusive_and_descent_uses_fall_rules(client,jumping,auth_headers,monkeypatch,rise,allowed):
    tiles=deepcopy(jumping['location'].tiles_data);tiles[2][4]['height']=1+rise
    jumping['location'].tiles_data=tiles;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:20 if allowed else pytest.fail('ошибка до броска'))
    r=jump(client,jumping,auth_headers)
    assert r.status_code==(200 if allowed else 400),r.json
    assert jumping['actor_location'].action_points_current==(3 if allowed else 5)
    if rise<0:
        assert r.json['jump']['fall'] is not None
        assert r.json['jump']['landing']['height']==-4


def test_natural_one_is_not_automatic_failure_of_agility_check(client,jumping,auth_headers,monkeypatch):
    data=deepcopy(jumping['actor_character'].data);data['skills']['physical']['agility']['base']=30
    jumping['actor_character'].data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:1)
    r=jump(client,jumping,auth_headers)
    assert r.status_code==200,r.json
    assert r.json['jump']['outcome']=='success' and r.json['jump']['check']['total']==11


def test_fully_paid_jump_rolls_only_on_completion_and_preserves_running_origin(client,jumping,auth_headers,monkeypatch):
    p=jumping;actor=p['actor_location'];actor.action_points_current=1;actor.movement_mode_this_turn='run'
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:pytest.fail('до оплаты броска нет'))
    r=jump(client,p,auth_headers,target_x=5.5,pending_action_id='paid-jump')
    assert r.status_code==200,r.json
    assert r.json['pending_action'] and actor.pos_x==2
    row=db.session.get(DeferredCombatAction,'paid-jump')
    assert row.remaining_action_points==1
    url=f"/lobbies/{p['lobby'].id}/locations/{p['location'].id}/combat/end_turn"
    assert client.post(url,headers=auth_headers(p['actor_user']),json={'location_character_id':p['target_location'].id}).status_code==200
    assert row.status=='ready' and actor.action_points_current==4
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:6)
    r=jump(client,p,auth_headers,resume_pending_action_id='paid-jump')
    assert r.status_code==200,r.json
    assert actor.pos_x==5.5 and actor.action_points_current==4
    assert row.status=='completed'
    again=jump(client,p,auth_headers,resume_pending_action_id='paid-jump')
    assert again.status_code==409


def test_jump_preview_is_read_only_and_rejects_a_wall_before_charging(client,jumping,auth_headers,monkeypatch):
    p=jumping
    wall=LocationObject(location_id=p['location'].id,type='wall',tile_x=3,tile_y=2,
        properties={'dimensions':{'width':.2,'depth':2,'height':3}})
    db.session.add(wall);db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:pytest.fail('предпросмотр не бросает'))
    url=f"/lobbies/{p['lobby'].id}/locations/{p['location'].id}/movement-preview"
    r=client.post(url,headers=auth_headers(p['actor_user']),json={'character_id':p['actor_character'].id,'special_action':'jump','x':4,'y':2})
    assert r.status_code==400,r.json
    assert jump(client,p,auth_headers).status_code==400
    assert p['actor_location'].action_points_current==5 and p['actor_location'].pos_x==2


@pytest.mark.parametrize('mode,roll',[('walk',6),('run',5)])
def test_failed_jump_lands_at_exact_hole_midpoint_and_enters_anomaly_below(client,jumping,auth_headers,monkeypatch,mode,roll):
    p=jumping;actor=p['actor_location'];actor.floor_level=1;actor.movement_mode_this_turn=mode
    db.session.add_all([
        LocationObject(location_id=p['location'].id,type='floor',tile_x=3,tile_y=2,
            properties={'level':1,'dimensions':{'width':5,'depth':3,'height':.1}}),
        LocationObject(location_id=p['location'].id,type='floor_opening',tile_x=3,tile_y=2,
            properties={'level':1,'dimensions':{'width':1,'depth':1,'height':.1}}),
        LocationObject(location_id=p['location'].id,type='anomaly',tile_x=3,tile_y=2,
            properties={'level':0,'anomaly_key':'morozilnik'}),
    ])
    db.session.commit()
    rolls=iter([roll,20,20,20,20])
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:next(rolls))
    r=jump(client,p,auth_headers)
    assert r.status_code==200,r.json
    assert r.json['jump']['outcome']=='midpoint'
    assert (actor.pos_x,actor.pos_y,actor.floor_level)==(3,2,0)
    assert r.json['jump']['fall'] is not None
    assert r.json['character']['active_anomaly']['key']=='morozilnik'


def test_jump_onto_one_metre_object_preserves_support_and_requires_paid_descent(client,jumping,auth_headers,monkeypatch):
    p=jumping;actor=p['actor_location']
    box=LocationObject(location_id=p['location'].id,type='crate',tile_x=4,tile_y=2,
        properties={'dimensions':{'width':.8,'depth':.8,'height':1}})
    db.session.add(box);db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:20)
    r=jump(client,p,auth_headers)
    assert r.status_code==200,r.json
    assert r.json['character']['height']==2
    db.session.expire_all()
    assert CombatService.get_state(p['location'].id,p['actor_user']['id'])['current_character']['height']==2
    assert CombatService._find_movement_path(p['location'],4,2,5,2,actor.character_id) is None
    plan=CombatService._vertical_movement_plan(p['location'],actor,5,2,0)
    assert plan['special_action']=='descend'
    moved,_,_=CombatService.move_character(p['location'].id,p['actor_user']['id'],actor.character_id,5,2,special_action='descend',floor_level=0)
    assert moved.action_points_current==2
    assert CombatService.physical_position(moved)['height']==1


@pytest.mark.parametrize('x',[True,float('nan'),float('inf'),-1,11])
def test_invalid_jump_point_never_rolls_or_charges(client,jumping,auth_headers,monkeypatch,x):
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:pytest.fail('невалидная точка не бросает кубик'))
    r=jump(client,jumping,auth_headers,target_x=x)
    assert r.status_code==400,r.json
    assert jumping['actor_location'].action_points_current==5


def test_out_of_combat_jump_and_free_balance_recovery_work_without_starting_fight(client,jumping,auth_headers,monkeypatch):
    p=jumping
    CombatService.end_combat(p['location'].id,p['actor_user']['id'])
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:9)
    r=jump(client,p,auth_headers)
    assert r.status_code==200,r.json
    assert r.json['state']['status']=='idle'
    assert r.json['character']['unstable_jump']
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:12)
    r=jump(client,p,auth_headers,action_key='recover_jump_balance')
    assert r.status_code==200,r.json
    assert not r.json['character']['unstable_jump']


@pytest.mark.parametrize('mode,cost',[('run',2),('sprint',4)])
def test_mode_can_be_selected_without_running_metres_and_is_not_paid_twice(client,jumping,auth_headers,monkeypatch,mode,cost):
    p=jumping;actor=p['actor_location']
    moved,mp,state=CombatService.move_character(p['location'].id,p['actor_user']['id'],actor.character_id,2,2,movement_mode=mode)
    assert (moved.pos_x,moved.pos_y)==(2,2)
    assert mp==0 and actor.movement_distance_this_turn==0
    assert actor.movement_mode_this_turn==mode and actor.action_points_current==5-cost
    CombatService.move_character(p['location'].id,p['actor_user']['id'],actor.character_id,2,2,movement_mode=mode)
    assert actor.action_points_current==5-cost
    assert state['current_character']['jump_preview']['max_distance']==4


def test_sheet_save_cannot_remove_edge_or_forge_support_height(client,jumping,auth_headers,monkeypatch):
    p=jumping
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:9)
    assert jump(client,p,auth_headers).status_code==200
    url=f"/lobbies/characters/{p['actor_character'].id}"
    fresh=client.get(url,headers=auth_headers(p['actor_user'])).json['data']
    fresh['health']['combatMeta'].pop('unstableJump')
    fresh['health']['combatMeta']['jumpSupport']={'height':999,'position':[4,2,0]}
    r=client.put(url,headers=auth_headers(p['actor_user']),json={'data':fresh})
    assert r.status_code==200,r.json
    meta=p['actor_character'].data['health']['combatMeta']
    assert meta['unstableJump'] and 'jumpSupport' not in meta
    assert CombatService.physical_position(p['actor_location'])['height']==1


def test_gm_relocation_clears_only_obsolete_jump_state_and_does_not_resurrect_it(client,jumping,auth_headers,monkeypatch):
    p=jumping
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:9)
    assert jump(client,p,auth_headers).status_code==200
    for x in (5,4):
        r=client.post(f"/lobbies/{p['lobby'].id}/locations/{p['location'].id}/spawn_character",
            headers=auth_headers(p['actor_user']),json={'character_id':p['actor_character'].id,
            'tile_x':x,'tile_y':2,'force_relocate':True})
        assert r.status_code==200,r.json
        assert not CombatService.get_state(p['location'].id,p['actor_user']['id'])['current_character']['unstable_jump']
    assert p['actor_location'].action_points_current==3


def test_deferred_jump_is_cancelled_before_payment_if_takeoff_position_changes(client,jumping,auth_headers,monkeypatch):
    p=jumping;p['actor_location'].action_points_current=1;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:pytest.fail('отменённый прыжок не бросает кубик'))
    assert jump(client,p,auth_headers,pending_action_id='moved-jump').status_code==200
    p['actor_location'].pos_x=2.5;db.session.commit()
    r=client.post(f"/lobbies/{p['lobby'].id}/locations/{p['location'].id}/combat/end_turn",
        headers=auth_headers(p['actor_user']),json={'location_character_id':p['target_location'].id})
    assert r.status_code==200,r.json
    assert db.session.get(DeferredCombatAction,'moved-jump').status=='cancelled'
    assert p['actor_location'].action_points_current==5


def test_melee_action_cannot_turn_character_while_unstable(client,jumping,auth_headers,monkeypatch):
    p=jumping
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:9)
    assert jump(client,p,auth_headers).status_code==200
    p['target_location'].pos_x,p['target_location'].pos_y=4,3
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:20)
    r=jump(client,p,auth_headers,action_key='melee_shove',target_character_id=p['target_character'].id)
    assert r.status_code==200,r.json
    assert (p['actor_location'].facing_x,p['actor_location'].facing_y)==(1,0)


@pytest.mark.parametrize('success',[True,False])
def test_balance_check_does_not_restart_anomaly_and_stumble_can_leave_it(client,jumping,auth_headers,monkeypatch,success):
    p=jumping
    db.session.add(LocationObject(location_id=p['location'].id,type='anomaly',tile_x=4,tile_y=2,
        properties={'anomaly_key':'morozilnik','level':0}))
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:9)
    assert jump(client,p,auth_headers).status_code==200
    data=deepcopy(p['actor_character'].data)
    data['health']['combatMeta']['activeAnomaly']['rounds']=2
    p['actor_character'].data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:12 if success else 11)
    r=jump(client,p,auth_headers,action_key='recover_jump_balance')
    assert r.status_code==200,r.json
    active=r.json['character']['active_anomaly']
    if success:
        assert active['key']=='morozilnik'
        assert p['actor_character'].data['health']['combatMeta']['activeAnomaly']['rounds']==2
    else:
        assert not active


def test_jump_on_upper_floor_is_not_blocked_by_person_underneath(client,jumping,auth_headers,monkeypatch):
    p=jumping;p['actor_location'].floor_level=1
    p['target_location'].pos_x,p['target_location'].pos_y=3,2
    db.session.add(LocationObject(location_id=p['location'].id,type='floor',tile_x=3,tile_y=2,
        properties={'level':1,'dimensions':{'width':6,'depth':4,'height':.1}}))
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:10)
    r=jump(client,p,auth_headers)
    assert r.status_code==200,r.json
    assert p['actor_location'].pos_x==4 and p['actor_location'].floor_level==1


def test_another_player_cannot_jump_as_the_actor(client,jumping,auth_headers,monkeypatch):
    p=jumping
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:pytest.fail('чужой запрос не бросает кубик'))
    r=client.post(f"/lobbies/{p['lobby'].id}/locations/{p['location'].id}/combat/action",
        headers=auth_headers(p['target_user']),json={'location_character_id':p['actor_location'].id,
        'action_key':'jump','target_x':4,'target_y':2})
    assert r.status_code==403,r.json
    assert p['actor_location'].action_points_current==5 and p['actor_location'].pos_x==2
