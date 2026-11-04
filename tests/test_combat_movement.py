import math
import pytest
from types import SimpleNamespace

from app.models import LobbyCharacter, LocationCharacter
from app.services.combat import COVER_CLASSES, CombatService
from app.services.exceptions import ValidationError


def movement_path(distance, cost=None, climb_cost=0):
    return {
        "path": [(step, 0) for step in range(distance + 1)],
        "cost": distance if cost is None else cost,
        "climb_cost": climb_cost,
    }


@pytest.mark.parametrize(
    ('intoxication', 'movement_penalty', 'accuracy_modifier', 'agility_modifier', 'charisma_modifier'),
    [
        (15, 0, 0, 0, 0),
        (16, 0, -1, 0, 1),
        (31, 0, -1, -3, 3),
        (46, 3, -3, -5, -2),
        (81, 5, -5, -8, -5),
    ],
)
def test_intoxication_stages_affect_movement_and_rolls(
    intoxication, movement_penalty, accuracy_modifier, agility_modifier,
    charisma_modifier,
):
    data = {'health': {'intoxication': intoxication}}

    assert CombatService._movement_penalty_breakdown(data)['intoxication'] == movement_penalty
    assert CombatService._health_roll_modifier(
        data, 'skills.physical.shooting', include_pain=False,
        include_blood=False, include_psy=False,
    ) == accuracy_modifier
    assert CombatService._health_roll_modifier(
        data, 'skills.physical.agility', include_pain=False,
        include_blood=False, include_psy=False,
    ) == agility_modifier
    assert CombatService._health_roll_modifier(
        data, 'skills.social.charisma', include_pain=False,
        include_blood=False, include_psy=False,
    ) == charisma_modifier


def test_withdrawal_support_forces_extreme_intoxication_stage():
    data = {'health': {'intoxication': 0, 'effects': [{
        'type': 'withdrawal_support', 'active': True, 'remaining': 60,
        'forced_intoxication_stage': 'extreme',
    }]}}

    profile = CombatService._intoxication_profile(data)

    assert profile['stage'] == 'extreme'
    assert profile['movement_penalty'] == 5


def test_near_death_intoxication_blocks_actions():
    condition = CombatService._character_condition({
        'health': {
            'current': 700, 'intoxication': 96,
            'zones': {'head': {'current': 50}, 'chest': {'current': 150}},
            'organs': {'brain': {'current': 1}, 'skull': {'current': 1}},
        },
    })

    assert condition['state'] == 'critical'


@pytest.mark.parametrize(
    ("mode", "distance", "expected_cost"),
    [
        ("walk", 5, 5),
        ("correction", 3, 0),
        ("backward_sideways", 5, 10),
        ("run", 5, 2.5),
        ("sprint", 5, 5/3),
    ],
)
def test_movement_mode_costs_preserve_fractional_total(mode, distance, expected_cost):
    result = CombatService._movement_route_cost(movement_path(distance), mode)

    assert result["distance"] == distance
    assert result["movement_points"] == expected_cost


def test_climb_cost_is_not_reduced_by_running():
    result = CombatService._movement_route_cost(
        movement_path(5, cost=10, climb_cost=5),
        "run",
    )

    assert result["movement_points"] == 7.5
    assert result["climb_cost"] == 5


def test_unknown_movement_mode_is_rejected():
    with pytest.raises(ValidationError, match="Unknown movement mode"):
        CombatService._movement_route_cost(movement_path(1), "teleport")


@pytest.fixture
def vertical_game(client,create_user,auth_headers):
    from app.extensions import db
    from test_character_interactions import _setup_pair
    pair = _setup_pair(client,create_user,auth_headers)
    actor=pair['actor_location']
    actor.pos_x,actor.pos_y=2,3
    actor.movement_points_current=100
    actor.free_actions_current=1
    actor.facing_x,actor.facing_y=1,0
    pair['target_location'].pos_x,pair['target_location'].pos_y=9,9
    pair['location'].grid_width=pair['location'].grid_height=10
    db.session.commit()
    return pair


@pytest.mark.parametrize('mode,cost,penalty', [('drag',2,2), ('rope',1,1), ('shoulder',2,0)])
def test_body_transport_follows_real_route_and_preserves_body(vertical_game, mode, cost, penalty):
    from app.extensions import db
    from app.services.body_transport import change, state
    pair = vertical_game; actor = pair['actor_location']; target = pair['target_location']
    target.pos_x, target.pos_y = 1, 3
    target.character.data = {**target.character.data, 'health': {'current':80,'max':100,
        'effects':[{'type':'unconsciousness','tick':'manual','source':'test'}]}}
    actor.character.data = {**actor.character.data, 'features': {'specialTraits':[{'name':'Санитар'}]},
        'inventory': {'pockets':[{'name':'Канат для переноски','quantity':1,'attributes':{'body_carry_rope':True}}]}}
    db.session.commit()
    change(pair['location'].id, pair['actor_user']['id'], actor.id, target.character_id, mode)
    assert actor.action_points_current == 5 - cost
    assert CombatService._movement_penalty_breakdown(actor.character.data,actor)['body_transport'] == penalty
    # Повтор того же запроса не списывает цену ещё раз и не создаёт копии тела.
    change(pair['location'].id, pair['actor_user']['id'], actor.id, target.character_id, mode)
    assert actor.action_points_current == 5 - cost
    CombatService.move_character(pair['location'].id,pair['actor_user']['id'],actor.character_id,4,3,movement_mode='walk')
    assert target.pos_x == (4 if mode == 'shoulder' else 3)
    assert target.pos_y == 3
    assert target.character.data['health']['current'] == 80
    assert target.controlled_by == pair['target_user']['id']
    assert state(actor)['target_character_id'] == target.character_id
    before = actor.action_points_current
    change(pair['location'].id,pair['actor_user']['id'],actor.id,target.character_id,'stop')
    assert actor.action_points_current == before - (1 if mode == 'rope' else 0)
    assert not state(actor)
    assert not target.character.data['health']['combatMeta'].get('transportedBy')
    assert (target.pos_x,target.pos_y) != (actor.pos_x,actor.pos_y)


def test_body_transport_rejects_conscious_target_and_forged_save(vertical_game):
    from app.services.body_transport import change, preserve
    pair=vertical_game;pair['target_location'].pos_x,pair['target_location'].pos_y=3,3
    with pytest.raises(ValidationError,match='сознательного'):
        change(pair['location'].id,pair['actor_user']['id'],pair['actor_location'].id,pair['target_character'].id,'drag')
    forged={'health':{'combatMeta':{'bodyTransport':{'mode':'shoulder'},'transportedBy':123}}}
    preserve({},forged)
    assert not forged['health']['combatMeta']


def test_transport_api_refuses_other_controller_and_sheet_cannot_forge_links(vertical_game,client,auth_headers):
    from copy import deepcopy
    from app.services.body_transport import state
    pair=vertical_game;actor=pair['actor_location'];target=pair['target_location']
    before=actor.action_points_current
    response=client.post(f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/characters/{target.character_id}/body-transport",
        headers=auth_headers(pair['target_user']),json={'actor_location_character_id':actor.id,'mode':'drag'})
    assert response.status_code==403,response.json
    assert actor.action_points_current==before and not state(actor)
    forged=deepcopy(actor.character.data_snapshot())
    forged.setdefault('health',{}).setdefault('combatMeta',{}).update(
        bodyTransport={'mode':'shoulder','target_character_id':target.character_id},transportedBy=target.character_id)
    response=client.put(f'/lobbies/characters/{actor.character_id}',headers=auth_headers(pair['actor_user']),json={'data':forged})
    assert response.status_code==200,response.json
    assert not state(actor)
    assert not actor.character.data['health']['combatMeta'].get('transportedBy')


def test_awakened_target_is_released_before_carriers_next_move(vertical_game,client,auth_headers):
    from app.extensions import db
    from app.services.body_transport import change,state
    pair=vertical_game;actor=pair['actor_location'];target=pair['target_location']
    target.pos_x,target.pos_y=1,3
    target.character.data={'health':{'current':700,'max':700,'effects':[{'type':'unconsciousness','tick':'manual'}]}}
    db.session.commit()
    change(pair['location'].id,pair['actor_user']['id'],actor.id,target.character_id,'drag')
    awake=target.character.data_snapshot();awake['health']['effects']=[]
    response=client.put(f'/lobbies/characters/{target.character_id}',headers=auth_headers(pair['target_user']),json={'data':awake})
    assert response.status_code==200,response.json
    CombatService.move_character(pair['location'].id,pair['actor_user']['id'],actor.character_id,4,3,movement_mode='walk')
    assert not state(actor)
    assert not target.character.data['health']['combatMeta'].get('transportedBy')
    assert (target.pos_x,target.pos_y)==(1,3)


@pytest.mark.parametrize('action',['draw_weapon','aim','reload_weapon','reload_underbarrel','clear_weapon_jam','brace_weapon','place_gunpoint'])
def test_shoulder_transport_blocks_firearm_actions_before_payment(vertical_game,client,auth_headers,action):
    from app.extensions import db
    from app.services.body_transport import change
    pair=vertical_game;actor=pair['actor_location'];target=pair['target_location']
    target.pos_x,target.pos_y=1,3
    target.character.data={'health':{'current':80,'max':700,'effects':[{'type':'unconsciousness','tick':'manual'}]}}
    actor.character.data={**actor.character.data,'features':{'specialTraits':[{'name':'Санитар'}]},
        'weapons':[{'id':'carrier-rifle','category':'weapon','name':'Ружьё','ammo':10}]}
    actor.drawn_weapon_index=0;db.session.commit()
    change(pair['location'].id,pair['actor_user']['id'],actor.id,target.character_id,'shoulder')
    before=actor.action_points_current
    response=client.post(f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/action",
        headers=auth_headers(pair['actor_user']),json={'location_character_id':actor.id,'action_key':action,
            'weapon_index':0,'target_character_id':target.character_id})
    assert response.status_code==400,response.json
    assert 'на плече' in response.json['error']['message']
    assert actor.action_points_current==before
    assert actor.character.data['weapons'][0]['ammo']==10


@pytest.mark.parametrize('height,blocked,cost',[(0,False,0),(.49,False,0),(.5,False,5),(.99,False,5),(1,False,12),(1.99,False,12),(2,True,0)])
def test_obstacle_category_follows_real_height(height,blocked,cost):
    for kind in ('chair','fence','wall'):
        assert CombatService._object_movement_profile({'type':kind,'properties':{'dimensions':{'height':height}}}) == {'blocked':blocked,'climb_cost':cost}


@pytest.mark.parametrize('category,blocked,cost',[('passable',False,0),('low',False,5),('high',False,12),('blocked',True,0)])
def test_gm_category_override_does_not_change_physical_height(category,blocked,cost):
    obj={'type':'wall','properties':{'dimensions':{'height':2.5},'traversal_category':category}}
    assert CombatService._object_movement_profile(obj)=={'blocked':blocked,'climb_cost':cost}
    assert CombatService._object_height(obj)==2.5


@pytest.mark.parametrize('height,base,ap',[(.5,5,1),(.99,5,1),(1,12,3),(1.99,12,3)])
@pytest.mark.parametrize('bonus',[-2,2,10])
@pytest.mark.parametrize('payment',['movement','action'])
def test_explicit_climb_honors_selected_payment_and_agility(vertical_game,height,base,ap,bonus,payment):
    from copy import deepcopy
    from app.extensions import db
    from app.models import LocationObject
    pair=vertical_game;actor=pair['actor_location']
    value=deepcopy(actor.character.data)
    value.setdefault('skills',{}).setdefault('physical',{})['agility']={'base':10+bonus*2,'bonus':0}
    actor.character.data=value
    obj=LocationObject(location_id=pair['location'].id,type='barrier',tile_x=3,tile_y=3,
        properties={'dimensions':{'width':.8,'depth':.8,'height':height}})
    db.session.add(obj);db.session.commit()
    plan=CombatService._object_climb_plan(pair['location'],actor,obj)
    assert plan
    moved,cost,_=CombatService.move_character(pair['location'].id,pair['actor_user']['id'],actor.character_id,
        *plan['path'][-1],special_action='climb',object_id=obj.id,climb_payment=payment)
    expected=max(0,base-bonus) if payment=='movement' else 0
    assert cost==expected and moved.movement_points_current==100-expected
    assert moved.action_points_current==5-(ap if payment=='action' else 0)
    assert moved.free_actions_current==1
    assert moved.movement_distance_this_turn==pytest.approx(plan['distance'])
    assert moved._movement_surfaces[1]['height']>=1+height
    assert (moved.facing_x,moved.facing_y)==(1,0)


def test_climb_requires_explicit_choice_and_cannot_silently_fallback_to_ap(vertical_game):
    from app.extensions import db
    from app.models import LocationObject
    pair=vertical_game;actor=pair['actor_location']
    actor.movement_points_current=0
    obj=LocationObject(location_id=pair['location'].id,type='barrier',tile_x=3,tile_y=3,
        properties={'dimensions':{'width':.8,'depth':.8,'height':.8}})
    db.session.add(obj);db.session.commit()
    plan=CombatService._object_climb_plan(pair['location'],actor,obj)
    args=(pair['location'].id,pair['actor_user']['id'],actor.character_id,*plan['path'][-1])
    for payment in (None,'movement','invalid',[],{}):
        with pytest.raises(ValidationError):
            CombatService.move_character(*args,special_action='climb',object_id=obj.id,climb_payment=payment)
        assert (actor.pos_x,actor.pos_y,actor.action_points_current,actor.movement_points_current)==(2,3,5,0)


@pytest.fixture
def broken_partition(vertical_game):
    from app.extensions import db
    from app.models import LocationObject
    wall=LocationObject(location_id=vertical_game['location'].id,type='wall',tile_x=3,tile_y=5,
        name='Сломанная внутренняя перегородка',
        properties={'visual_asset':'inner_wall','rotation':-math.pi/2,'dimensions':{'width':8,'depth':.14,'height':1.2}})
    db.session.add(wall);db.session.commit()
    return vertical_game,wall


def test_climb_long_partition_prefers_cell_in_facing_not_far_end(broken_partition):
    case,wall=broken_partition;actor=case['actor_location']
    assert CombatService._find_climb_landing(case['location'],wall,actor)==(4,3)
    plan=CombatService._object_climb_plan(case['location'],actor,wall)
    assert plan['path']==[(2,3),(4,3)] and plan['distance']==2


def test_climb_uses_only_two_adjacent_alternatives_if_front_is_occupied(broken_partition):
    from app.extensions import db
    from app.models import LocationObject
    case,wall=broken_partition;actor=case['actor_location'];other=case['target_location']
    other.pos_x,other.pos_y=4,3;db.session.commit()
    assert CombatService._find_climb_landing(case['location'],wall,actor)==(4,2)
    obstruction=LocationObject(location_id=case['location'].id,type='box',tile_x=4,tile_y=2,
        properties={'dimensions':{'width':.7,'depth':.7,'height':2.5}})
    db.session.add(obstruction);db.session.commit()
    assert CombatService._find_climb_landing(case['location'],wall,actor)==(4,4)
    db.session.add(LocationObject(location_id=case['location'].id,type='box',tile_x=4,tile_y=4,properties=obstruction.properties))
    db.session.commit()
    before=(actor.pos_x,actor.pos_y,actor.action_points_current,actor.movement_points_current)
    assert CombatService._find_climb_landing(case['location'],wall,actor) is None
    with pytest.raises(ValidationError,match='места приземления'):
        CombatService.move_character(case['location'].id,case['actor_user']['id'],actor.character_id,4,3,
            special_action='climb',object_id=wall.id,climb_payment='action')
    assert (actor.pos_x,actor.pos_y,actor.action_points_current,actor.movement_points_current)==before


@pytest.mark.parametrize('unsafe',['hole','height','anomaly','low_obstacle'])
def test_climb_skips_unsafe_front_cell_without_searching_along_partition(broken_partition,unsafe):
    from copy import deepcopy
    from app.extensions import db
    from app.models import LocationObject
    case,wall=broken_partition;actor=case['actor_location'];location=case['location']
    if unsafe=='hole':
        actor.floor_level=1
        wall.properties={**wall.properties,'level':1}
        db.session.add(LocationObject(location_id=location.id,type='floor',tile_x=4,tile_y=4,
            properties={'level':1,'dimensions':{'width':10,'depth':10}}))
        db.session.add(LocationObject(location_id=location.id,type='floor_opening',tile_x=4,tile_y=3,
            properties={'level':1,'dimensions':{'width':.8,'depth':.8}}))
    elif unsafe=='height':
        tiles=[[{'height':1} for _ in range(10)] for _ in range(10)]
        tiles[3][4]['height']=3;location.tiles_data=tiles
    elif unsafe=='anomaly':
        db.session.add(LocationObject(location_id=location.id,type='anomaly',tile_x=4,tile_y=3,
            properties={'anomaly_key':'otboynik','dimensions':{'width':1,'depth':1}}))
    else:
        db.session.add(LocationObject(location_id=location.id,type='box',tile_x=4,tile_y=3,
            properties={'dimensions':{'width':.8,'depth':.8,'height':.8}}))
    db.session.commit()
    assert CombatService._find_climb_landing(location,wall,actor)==(4,2)


@pytest.mark.parametrize('direction',[(1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1)])
def test_climb_front_cell_follows_all_eight_facings(vertical_game,direction):
    from app.extensions import db
    from app.models import LocationObject
    actor=vertical_game['actor_location'];actor.pos_x=actor.pos_y=4
    actor.facing_x,actor.facing_y=direction;dx,dy=direction
    wall=LocationObject(location_id=actor.location_id,type='wall',tile_x=4+dx,tile_y=4+dy,
        properties={'visual_asset':'inner_wall','dimensions':{'width':.8,'depth':.8,'height':.8}})
    db.session.add(wall);db.session.commit()
    assert CombatService._find_climb_landing(vertical_game['location'],wall,actor)==(4+2*dx,4+2*dy)


@pytest.mark.parametrize('facing',[(-1,0),(0,1),(0,-1)])
def test_climb_cannot_choose_a_partition_outside_forward_direction(broken_partition,facing):
    case,wall=broken_partition;actor=case['actor_location'];actor.facing_x,actor.facing_y=facing
    assert CombatService._find_climb_landing(case['location'],wall,actor) is None


def test_pripyat_inner_partition_lands_directly_across_from_actor(vertical_game):
    import json
    from pathlib import Path
    from app.extensions import db
    from app.models import LocationObject
    source=json.loads((Path(__file__).parents[1]/'tmp/pripyat-regression.json').read_text(encoding='utf-8'))
    location=vertical_game['location'];actor=vertical_game['actor_location']
    location.grid_width,location.grid_height=source['grid_width'],source['grid_height'];location.tiles_data=source['tiles_data']
    wall=None
    for item in source['objects']:
        obj=LocationObject(location_id=location.id,type=item['type'],name=item.get('name'),tile_x=item['tile_x'],tile_y=item['tile_y'],properties=item.get('properties') or {})
        db.session.add(obj)
        if obj.type=='wall' and obj.tile_x==57 and obj.tile_y==10 and (obj.properties.get('visual_asset')=='inner_wall'):wall=obj
    actor.pos_x,actor.pos_y=57,10;actor.facing_x,actor.facing_y=-1,0
    db.session.commit()
    assert wall is not None
    assert CombatService._find_climb_landing(location,wall,actor)==(56,10)


@pytest.mark.parametrize('delta,payment,ap,op',[(1,'movement',0,12),(1.99,'action',3,0),(-1,'action',1,0),(-2,'action',1,0)])
def test_terrain_step_up_or_controlled_descent_uses_explicit_action_without_fall(vertical_game,monkeypatch,delta,payment,ap,op):
    from app.extensions import db
    pair=vertical_game;actor=pair['actor_location'];location=pair['location']
    location.tiles_data=[[{'height':3} for _ in range(10)] for _ in range(10)]
    location.tiles_data[3][3]['height']=3+delta
    db.session.commit()
    plan=CombatService._vertical_movement_plan(location,actor,3,3,0)
    assert plan
    monkeypatch.setattr(CombatService,'resolve_fall',staticmethod(lambda *args,**kwargs:pytest.fail('Управляемый переход не вызывает падение')))
    moved,cost,_=CombatService.move_character(location.id,pair['actor_user']['id'],actor.character_id,3,3,
        special_action=plan['special_action'],climb_payment=payment)
    assert cost==op and moved.movement_points_current==100-op
    assert moved.action_points_current==5-ap and moved.free_actions_current==1
    assert CombatService.physical_position(moved)['height']==pytest.approx(3+delta)
    assert moved._movement_fall is None
    assert moved.movement_distance_this_turn==1


@pytest.mark.parametrize('delta',[2,2.01,3])
def test_terrain_rise_at_least_two_meters_cannot_be_climbed(vertical_game,delta):
    from app.extensions import db
    pair=vertical_game;actor=pair['actor_location'];location=pair['location']
    location.tiles_data=[[{'height':1} for _ in range(10)] for _ in range(10)]
    location.tiles_data[3][3]['height']=1+delta
    db.session.commit()
    assert CombatService._find_movement_path(location,2,3,3,3,actor.character_id) is None
    assert CombatService._vertical_movement_plan(location,actor,3,3,0) is None
    with pytest.raises(ValidationError):
        CombatService.move_character(location.id,pair['actor_user']['id'],actor.character_id,3,3,special_action='climb_up',climb_payment='action')
    assert (actor.pos_x,actor.pos_y,actor.action_points_current)==(2,3,5)


def test_controlled_drop_through_opening_below_two_meters_has_no_fall_roll(vertical_game,monkeypatch):
    from app.extensions import db
    from app.models import LocationObject
    pair=vertical_game;actor=pair['actor_location'];location=pair['location']
    actor.pos_x,actor.pos_y,actor.floor_level=3,2,1
    floor=LocationObject(location_id=location.id,type='floor',tile_x=3,tile_y=3,
        properties={'level':1,'elevation':-.5,'dimensions':{'width':6,'depth':6,'height':.1}})
    portal=LocationObject(location_id=location.id,type='hatch',tile_x=3,tile_y=3,
        properties={'level':1,'is_open':True,'dimensions':{'width':.9,'depth':.9,'height':.1}})
    db.session.add_all([floor,portal]);db.session.commit()
    plan=CombatService._object_drop_plan(location,actor,portal)
    assert plan and plan['height']<=2
    monkeypatch.setattr(CombatService,'resolve_fall',staticmethod(lambda *args,**kwargs:pytest.fail('Управляемый спуск не вызывает падение')))
    moved,cost,_=CombatService.move_character(location.id,pair['actor_user']['id'],actor.character_id,plan['landing']['x'],plan['landing']['y'],
        special_action='drop_down',object_id=portal.id,floor_level=0)
    assert moved.floor_level==0 and cost==0
    assert moved.action_points_current==4 and moved.movement_points_current==100
    assert moved._movement_fall is None
    assert moved.movement_distance_this_turn==pytest.approx(plan['distance']+plan['height'])


@pytest.mark.parametrize('payment,ap,op',[('movement',0,12),('action',2,6)])
def test_route_with_two_obstacles_applies_agility_to_each_one(vertical_game,payment,ap,op):
    from copy import deepcopy
    from app.extensions import db
    from app.models import LocationObject
    pair=vertical_game;actor=pair['actor_location'];location=pair['location']
    actor.pos_x=1
    value=deepcopy(actor.character.data)
    value.setdefault('skills',{}).setdefault('physical',{})['agility']={'base':14,'bonus':0}
    actor.character.data=value
    for x in (3,5):
        db.session.add(LocationObject(location_id=location.id,type='fence',tile_x=x,tile_y=3,
            properties={'dimensions':{'width':.2,'depth':12,'height':.8}}))
    db.session.commit()
    route=CombatService._find_movement_path(location,1,3,7,3,actor.character_id)
    assert route and len(route['climbs'])==2
    assert max(point['height'] for point in route['surfaces'])>=1.8
    assert route['surfaces'][-1]['height']==1
    moved,cost,_=CombatService.move_character(location.id,pair['actor_user']['id'],actor.character_id,7,3,
        movement_mode='walk',climb_payment=payment)
    assert cost==pytest.approx(op) and moved.movement_points_current==pytest.approx(100-op)
    assert moved.action_points_current==5-ap and moved.free_actions_current==1


@pytest.mark.parametrize('penalty,limits',[(3,[7,14,21,3,3,3]),(-2,[12,24,36,6,4,3]),(10,[0,0,0,0,0,3])])
def test_movement_distance_limits_include_penalties_and_negative_bonuses(penalty,limits):
    modes = [('walk','standing'),('run','standing'),('sprint','standing'),
             ('backward_sideways','standing'),('walk','prone'),('correction','sitting')]
    assert [CombatService._movement_distance_limit(mode,posture,movement_penalty=penalty)
            for mode,posture in modes] == limits
    assert CombatService._movement_distance_limit('walk','sitting',movement_penalty=penalty) == limits[3]


def test_correction_counts_toward_penalized_limit_and_extra_op_cannot_bypass_it(client,create_user,auth_headers):
    from app.extensions import db
    from test_character_interactions import _setup_pair
    case = _setup_pair(client,create_user,auth_headers)
    actor = case['actor_location']
    actor.character.data = {**actor.character.data,'equipment':{'armor':{'movementPenalty':3}}}
    actor.movement_points_current = 100
    actor.free_actions_current = 1
    actor.facing_x,actor.facing_y = 0,1
    db.session.commit()
    args = (case['location'].id,case['actor_user']['id'],actor.character_id)
    CombatService.move_character(*args,1,2,movement_mode='correction')
    CombatService.move_character(*args,1,2.5,movement_mode='correction')
    assert actor.free_actions_current == 0
    assert actor.movement_points_current == 100
    assert actor.movement_mode_this_turn is None
    assert actor.correction_distance_this_turn == 1.5
    assert actor.movement_distance_this_turn == 1.5
    CombatService.move_character(*args,1,8,movement_mode='walk')
    assert actor.movement_points_current == 94.5
    assert actor.movement_distance_this_turn == 7
    CombatService.perform_action(case['location'].id,case['actor_user']['id'],actor.id,'convert_free_action_to_movement')
    assert actor.movement_points_current == 101.5
    with pytest.raises(ValidationError,match='limited to 7'):
        CombatService.move_character(*args,1,8.25,movement_mode='walk')
    assert actor.pos_y == 8
    assert actor.movement_points_current == 101.5


def test_negative_movement_penalty_is_preserved():
    actor = SimpleNamespace(character=SimpleNamespace(data={'equipment':{'armor':{'movementPenalty':-2}}}),movement_points_max=0)
    assert CombatService._movement_penalty(actor) == -2
    assert CombatService._combat_profile(actor)['movement_gain'] == 12


def test_backward_sideways_path_rejects_forward_steps():
    character = SimpleNamespace(facing_x=0, facing_y=1)

    CombatService._validate_backward_sideways_path(
        character,
        {"path": [(2, 2), (1, 2), (1, 1)]},
    )

    with pytest.raises(ValidationError, match="нельзя идти вперёд"):
        CombatService._validate_backward_sideways_path(
            character,
            {"path": [(2, 2), (2, 3)]},
        )


@pytest.mark.parametrize('destination,expected_mode,expected_cost',[
    ((1,0),'backward_sideways',2),((0,1),'backward_sideways',2),((1,2),'walk',1),
    ((0,2),'walk',math.sqrt(2)),((2,2),'walk',math.sqrt(2)),
])
def test_walk_preserves_facing_and_charges_backward_steps(client,create_user,auth_headers,destination,expected_mode,expected_cost):
    from app.extensions import db
    from test_character_interactions import _setup_pair
    case = _setup_pair(client,create_user,auth_headers)
    actor = case['actor_location']
    actor.movement_points_current = 10
    actor.facing_x, actor.facing_y = 0,1
    db.session.commit()
    moved,cost,_ = CombatService.move_character(case['location'].id,case['actor_user']['id'],
        case['actor_character'].id,*destination,movement_mode='walk')
    db.session.expire_all()
    moved = db.session.get(LocationCharacter,actor.id)
    assert cost == pytest.approx(expected_cost)
    assert moved.movement_points_current == pytest.approx(10-expected_cost)
    assert moved.movement_mode_this_turn == expected_mode
    assert (moved.facing_x,moved.facing_y) == (0,1)
    assert moved.action_points_current == 5


def test_running_behind_requires_an_explicit_turn_without_spending_points():
    actor = SimpleNamespace(facing_x=0,facing_y=1)
    with pytest.raises(ValidationError,match='направление взгляда'):
        CombatService._effective_movement_mode(actor,{'path':[(1,1),(1,0)]},'run')


@pytest.mark.parametrize('facing',[(0,1),(1,0),(1,1),(-1,1),(-1,-1),(1,-1),(0,0)])
@pytest.mark.parametrize('angle',[0,44.999,45,-45,45.001,-45.001,60,90,180])
def test_movement_forward_sector_includes_45_degrees(facing,angle):
    actor = SimpleNamespace(facing_x=facing[0],facing_y=facing[1])
    fx,fy = facing if any(facing) else (0,1)
    radians = math.radians(angle)
    # Нулевая длина и повторный отрезок не меняют классификацию направления.
    point = (fx*math.cos(radians)-fy*math.sin(radians),fx*math.sin(radians)+fy*math.cos(radians))
    route = {'path':[(0,0),(0,0),point]}
    forward = abs(angle)<=45
    assert CombatService._movement_direction_profile(actor,route) == (forward,not forward)
    assert CombatService._effective_movement_mode(actor,route,'walk') == ('walk' if forward else 'backward_sideways')
    if forward:
        for mode in ('run','sprint'):
            assert CombatService._effective_movement_mode(actor,route,mode) == mode
        with pytest.raises(ValidationError,match='нельзя идти вперёд'):
            CombatService._validate_backward_sideways_path(actor,route)
    else:
        CombatService._validate_backward_sideways_path(actor,route)
        for mode in ('run','sprint'):
            with pytest.raises(ValidationError,match='направление взгляда'):
                CombatService._effective_movement_mode(actor,route,mode)


def test_diagonal_target_does_not_hide_sideways_detour():
    actor = SimpleNamespace(facing_x=0,facing_y=1)
    route = {'path':[(0,0),(0,1),(1,1)]}
    assert CombatService._movement_direction_profile(actor,route) == (True,True)
    with pytest.raises(ValidationError,match='смешивает'):
        CombatService._effective_movement_mode(actor,route,'walk')


@pytest.mark.parametrize('dx',[-2.1,-2.0001,-2,-1.9999,1.9999,2,2.0001,2.1])
def test_fractional_heading_route_across_45_degree_boundary(client,create_user,auth_headers,dx):
    from app.extensions import db
    from test_character_interactions import _setup_pair
    case=_setup_pair(client,create_user,auth_headers)
    location,actor=case['location'],case['actor_location']
    location.grid_width,location.grid_height=10,10
    location.tiles_data=[[{'terrain':'ice','height':1} for _ in range(10)] for _ in range(10)]
    actor.pos_x,actor.pos_y=4.1,4.1
    actor.facing_x,actor.facing_y=0,1
    actor.movement_points_current=20
    db.session.commit()
    route=CombatService._find_movement_path(location,4.1,4.1,4.1+dx,6.1,actor.character_id,movement_mode='walk')
    assert route,'Открытая поверхность не должна блокироваться из-за угла дробных координат'
    forward=abs(dx)<=2
    assert CombatService._movement_direction_profile(actor,route)==(forward,not forward)
    moved,cost,_=CombatService.move_character(location.id,case['actor_user']['id'],actor.character_id,4.1+dx,6.1,movement_mode='walk')
    assert moved.movement_mode_this_turn==('walk' if forward else 'backward_sideways')
    assert route['distance']==pytest.approx(math.hypot(dx,2))
    assert cost==pytest.approx(math.hypot(dx,2)*(2 if forward else 4))
    assert (moved.facing_x,moved.facing_y)==(0,1)


def test_direction_constrained_planner_on_weighted_terrain(client,create_user,auth_headers):
    from app.extensions import db
    from test_character_interactions import _setup_pair
    case=_setup_pair(client,create_user,auth_headers)
    location,actor=case['location'],case['actor_location']
    location.grid_width,location.grid_height=8,10
    location.tiles_data=[[{'terrain':'ice' if 2<=x<=4 else 'grass','height':1} for x in range(8)] for _ in range(10)]
    actor.pos_x,actor.pos_y=3.1,1.1
    actor.facing_x,actor.facing_y=0,1
    db.session.commit()
    old=CombatService._find_movement_path(location,3.1,1.1,3.1,8.1,actor.character_id)
    assert CombatService._movement_direction_profile(actor,old)==(True,True)
    for mode in ('walk','run','sprint'):
        route=CombatService._find_movement_path(location,3.1,1.1,3.1,8.1,actor.character_id,movement_mode=mode)
        assert route and CombatService._movement_direction_profile(actor,route)==(True,False)
    back=CombatService._find_movement_path(location,3.1,1.1,3.1,.1,actor.character_id,movement_mode='walk')
    assert back and CombatService._movement_direction_profile(actor,back)==(False,True)
    actor.movement_mode_this_turn='walk'
    assert CombatService._find_movement_path(location,3.1,1.1,3.1,.1,actor.character_id,movement_mode='walk') is None


def test_pripyat_ice_planner_avoids_mixed_direction_detour(client,create_user,auth_headers):
    import json
    from pathlib import Path
    from app.extensions import db
    from app.models import LocationObject
    from test_character_interactions import _setup_pair
    source_path=Path(__file__).parents[1]/'tmp/pripyat-regression.json'
    if not source_path.exists():
        pytest.skip('Нужна локальная копия Припяти')
    source=json.loads(source_path.read_text(encoding='utf-8'))
    case=_setup_pair(client,create_user,auth_headers)
    location,actor=case['location'],case['actor_location']
    location.grid_width,location.grid_height=source['grid_width'],source['grid_height']
    location.tiles_data=source['tiles_data']
    for y in range(47,58):
        for x in (44,45,46):
            location.tiles_data[y][x]['terrain']='ice'
    db.session.add_all(LocationObject(location_id=location.id,**item) for item in source['objects'])
    actor.facing_x,actor.facing_y=0,1
    actor.pos_x,actor.pos_y=45.1,48.1
    actor.movement_points_current=20
    db.session.commit()
    old=CombatService._find_movement_path(location,45.1,48.1,45.1,55.1,actor.character_id)
    route=CombatService._find_movement_path(location,45.1,48.1,45.1,55.1,actor.character_id,movement_mode='walk')
    assert old and CombatService._movement_direction_profile(actor,old)==(True,True),old
    assert route and CombatService._movement_direction_profile(actor,route)==(True,False),route
    base=f'/lobbies/{case["lobby"].id}/locations/{location.id}'
    preview=client.post(base+'/movement-preview',headers=auth_headers(case['actor_user']),
        json={'character_id':actor.character_id,'x':45.1,'y':55.1,'movement_mode':'walk'}).get_json()['route']
    assert preview['path']==[list(point) for point in route['path']]
    moved,cost,_=CombatService.move_character(location.id,case['actor_user']['id'],actor.character_id,45.1,55.1,movement_mode='walk')
    assert cost==pytest.approx(preview['cost'])
    assert moved.movement_mode_this_turn=='walk'
    assert (moved.facing_x,moved.facing_y)==(0,1)
    for mode in ('walk','run','sprint'):
        blocked=client.post(base+'/movement-preview',headers=auth_headers(case['actor_user']),
            json={'character_id':actor.character_id,'x':45.1,'y':48.1,'movement_mode':mode})
        assert blocked.status_code==200 and blocked.get_json()['route'] is None
        assert 'направление взгляда' in blocked.get_json()['reason']
    invalid=client.post(base+'/movement-preview',headers=auth_headers(case['actor_user']),
        json={'character_id':actor.character_id,'x':45.1,'y':48.1,'movement_mode':['run']})
    assert invalid.status_code==400
    assert moved.movement_points_current==pytest.approx(20-cost)


def test_grapple_group_path_keeps_companion_offset(monkeypatch):
    location = SimpleNamespace(grid_width=6, grid_height=6)
    monkeypatch.setattr(
        CombatService,
        "_build_movement_map",
        staticmethod(lambda location, moving_character_id=None, ignored_character_ids=None, **kwargs: (set(), {})),
    )

    result = CombatService._find_movement_path(
        location,
        1,
        1,
        3,
        1,
        moving_character_id=10,
        ignored_character_ids=[10, 11],
        companion_offset=(0, 1),
    )

    assert result["path"][0] == (1, 1)
    assert result["path"][-1] == (3, 1)


def test_grapple_group_path_rejects_blocked_companion_destination(monkeypatch):
    location = SimpleNamespace(grid_width=6, grid_height=6)
    monkeypatch.setattr(
        CombatService,
        "_build_movement_map",
        staticmethod(
            lambda location, moving_character_id=None, ignored_character_ids=None, **kwargs: (
                {(3, 2)},
                {},
            )
        ),
    )

    result = CombatService._find_movement_path(
        location,
        1,
        1,
        3,
        1,
        moving_character_id=10,
        ignored_character_ids=[10, 11],
        companion_offset=(0, 1),
    )

    assert result is None


def test_diagonal_path_can_pass_single_blocked_corner(monkeypatch):
    location = SimpleNamespace(grid_width=3, grid_height=3)
    monkeypatch.setattr(
        CombatService,
        "_build_movement_map",
        staticmethod(lambda *args, **kwargs: ({(1, 0)}, {})),
    )

    result = CombatService._find_movement_path(location, 0, 0, 1, 1, 10)

    assert result is not None
    assert result["path"] == [(0, 0), (1, 1)]


def test_diagonal_path_only_requires_destination_to_be_free(monkeypatch):
    location = SimpleNamespace(grid_width=3, grid_height=3)
    monkeypatch.setattr(
        CombatService,
        "_build_movement_map",
        staticmethod(lambda *args, **kwargs: ({(1, 0), (0, 1)}, {})),
    )

    result = CombatService._find_movement_path(location, 0, 0, 1, 1, 10)

    assert result is not None
    assert result["path"] == [(0, 0), (1, 1)]


@pytest.mark.parametrize(
    ("blocked_zones", "expected"),
    [(0, "none"), (1, "half"), (3, "half"), (4, "three_quarters"), (6, "three_quarters"), (7, "full")],
)
def test_cover_grade_depends_on_blocked_body_zones(blocked_zones, expected):
    grade, _, _, _ = CombatService._cover_grade(blocked_zones)

    assert grade == expected


def test_new_turn_resets_distance_but_keeps_run_and_sprint_exhaustion():
    character = LocationCharacter(
        initiative_bonus=0,
        action_points_max=5,
        free_actions_max=1,
        movement_points_max=0,
        movement_mode_this_turn="walk",
        movement_distance_this_turn=8,
        correction_distance_this_turn=2,
        strenuous_movement_blocked_until_round=5,
    )

    CombatService._prepare_character_for_turn(character)

    assert character.movement_mode_this_turn is None
    assert character.movement_distance_this_turn == 0
    assert character.correction_distance_this_turn == 0
    assert character.strenuous_movement_blocked_until_round == 5


def test_breathlessness_does_not_interrupt_selected_run_in_same_turn():
    character = LocationCharacter(
        movement_mode_this_turn="run",
        strenuous_movement_blocked_until_round=4,
    )

    assert CombatService._strenuous_movement_is_blocked(character, "run", 3) is False
    assert CombatService._strenuous_movement_is_blocked(character, "sprint", 3) is True


def test_breathlessness_blocks_new_run_on_later_turn():
    character = LocationCharacter(
        movement_mode_this_turn=None,
        strenuous_movement_blocked_until_round=4,
    )

    assert CombatService._strenuous_movement_is_blocked(character, "run", 3) is True
    assert CombatService._strenuous_movement_is_blocked(character, "run", 5) is False


def test_pending_action_spends_future_turn_points_until_complete():
    character = LocationCharacter(
        initiative_bonus=0,
        action_points_max=5,
        free_actions_max=1,
    )
    character.character = LobbyCharacter(data={
        "health": {
            "combatMeta": {
                "pendingAction": {
                    "id": "splint-1",
                    "label": "Splint",
                    "total_action_points": 12,
                    "remaining_action_points": 7,
                },
            },
        },
    })

    CombatService._prepare_character_for_turn(character)
    pending = character.character.data["health"]["combatMeta"]["pendingAction"]
    assert character.action_points_current == 0
    assert pending["remaining_action_points"] == 2

    CombatService._prepare_character_for_turn(character)
    meta = character.character.data["health"]["combatMeta"]
    assert character.action_points_current == 3
    assert "pendingAction" not in meta
    assert meta["completedPendingActionId"] == "splint-1"


def test_initiative_bonus_uses_tactics_bonus_and_explicit_modifier():
    character = LocationCharacter(initiative_bonus=99)
    character.character = LobbyCharacter(data={
        "skills": {
            "other": {
                "tactics": {
                    "base": 16,
                    "bonus": 2,
                }
            }
        },
        "initiative_bonus": 3,
    })

    profile = CombatService._combat_profile(character)

    assert profile["initiative_bonus"] == 7


def test_movement_penalty_combines_weight_armor_and_temporary_modifier():
    character_data = {
        "inventory": {
            "pockets": [{"category": "consumable", "weight": 6, "quantity": 1}],
            "backpack": [{"category": "container", "weight": 4, "quantity": 1}],
        },
        "equipment": {"armor": {"movementPenalty": 2}},
        "health": {
            "combatMeta": {
                "consumableModifiers": [
                    {"stat": "movement_points", "value": -1, "remaining": 2},
                ],
            },
        },
    }
    location_character = LocationCharacter()
    location_character.character = LobbyCharacter(data=character_data)

    assert CombatService._movement_penalty(location_character) == 3


def test_powered_exoskeleton_sets_base_penalty_and_ignores_overload():
    character_data = {
        "inventory": {
            "pockets": [{"category": "item", "weight": 100, "quantity": 1}],
        },
        "equipment": {
            "armor": {
                "name": "Экзоскелет",
                "movementPenalty": 5,
                "installedModules": [{
                    "slotType": "exoskeleton_battery",
                    "attributes": {"remaining_days": 1},
                }],
            },
        },
        "health": {
            "zones": {
                "leftLeg": {"current": 0},
                "rightLeg": {"current": 100},
            },
        },
    }
    location_character = LocationCharacter()
    location_character.character = LobbyCharacter(data=character_data)

    assert CombatService._movement_penalty(location_character) == 8


def test_exoskeleton_blocks_run_and_sprint_even_when_powered():
    data = {
        "equipment": {
            "armor": {
                "name": "Экзоскелет",
                "installedModules": [{
                    "slotType": "exoskeleton_battery",
                    "attributes": {"remaining_days": 1},
                }],
            },
        },
    }

    CombatService._validate_equipment_movement(data, "walk")
    CombatService._validate_equipment_movement(data, "correction")
    with pytest.raises(ValidationError, match="unavailable in an exoskeleton"):
        CombatService._validate_equipment_movement(data, "run")
    with pytest.raises(ValidationError, match="unavailable in an exoskeleton"):
        CombatService._validate_equipment_movement(data, "sprint")


def test_powered_exoskeleton_adds_strength_level_and_roll_bonuses():
    data = {
        "skills": {"physical": {"strength": {"base": 10, "bonus": 0}}},
        "equipment": {
            "armor": {
                "name": "Экзоскелет",
                "installedModules": [{
                    "slotType": "exoskeleton_battery",
                    "attributes": {"remaining_days": 1},
                }],
            },
        },
    }
    character = LocationCharacter()
    character.character = LobbyCharacter(data=data)

    profile = CombatService._weapon_strength_profile(
        character,
        {"minStrength": 18},
    )

    assert profile["strength"] == 18
    assert profile["accuracy_penalty"] == 0
    assert CombatService._skill_modifier(data, "skills.physical.strength") == 4
    weight_details = CombatService._inventory_weight_details(data)
    assert weight_details["effective_strength"] == 18
    assert weight_details["weight_per_penalty"] == pytest.approx(7)


@pytest.mark.parametrize(
    "skill_path",
    [
        "skills.physical.strength",
        "skills.social.charisma",
        "skills.other.tactics",
    ],
)
def test_skill_bonus_is_added_to_value_before_roll_modifier(skill_path):
    category, skill = skill_path.split(".")[1:]
    data = {
        "skills": {
            category: {
                skill: {"base": 5, "bonus": 8},
            },
        },
    }

    assert CombatService._skill_value(data, skill_path) == 13
    assert CombatService._skill_modifier(data, skill_path) == 1


def test_exoskeleton_without_charged_battery_keeps_weight_penalty():
    character_data = {
        "inventory": {
            "pockets": [{"category": "item", "weight": 5, "quantity": 1}],
        },
        "equipment": {
            "armor": {
                "name": "Экзоскелет",
                "movementPenalty": 5,
                "installedModules": [],
            },
        },
    }
    location_character = LocationCharacter()
    location_character.character = LobbyCharacter(data=character_data)

    details = CombatService._movement_penalty_breakdown(character_data)

    assert details["powered_exoskeleton"] is False
    assert details["weight"] == 1
    assert CombatService._movement_penalty(location_character) == 6


def test_helmet_movement_penalty_is_added_but_integrated_helmet_is_not():
    data = {
        "equipment": {
            "armor": {"movementPenalty": 2},
            "helmet": {"movementPenalty": 1},
        },
    }

    details = CombatService._movement_penalty_breakdown(data)

    assert details["armor"] == 2
    assert details["helmet"] == 1
    assert details["total"] == 3

    data["equipment"]["helmet"]["integratedWithArmor"] = True
    integrated = CombatService._movement_penalty_breakdown(data)

    assert integrated["helmet"] == 0
    assert integrated["total"] == 2


def test_carrying_capacity_uses_strength_bonus_without_health_roll_penalties():
    character_data = {
        "skills": {
            "physical": {
                "strength": {"base": 5, "bonus": 0},
            },
        },
        "inventory": {
            "pockets": [{"category": "item", "weight": 3.5, "quantity": 1}],
        },
        "health": {
            "painLevel": 4,
            "exhaustion": 3,
            "blood": "severe",
        },
    }

    details = CombatService._inventory_weight_details(character_data)

    assert details["strength_bonus"] == -3
    assert details["weight_per_penalty"] == pytest.approx(3.5)
    assert details["penalty"] == 1


def test_skill_bonus_is_part_of_effective_strength_for_carrying_capacity():
    data = {
        "skills": {
            "physical": {
                "strength": {"base": 10, "bonus": 6},
            },
        },
    }

    details = CombatService._inventory_weight_details(data)

    assert details["effective_strength"] == 16
    assert details["weight_per_penalty"] == pytest.approx(6.5)


@pytest.mark.parametrize(
    ("armor_name", "covered", "not_covered"),
    [
        ("Армейский бронежилет", ("chest", "abdomen"), ("left_arm", "left_leg", "head")),
        ("Кожаная куртка", ("chest", "abdomen", "left_arm"), ("left_leg", "head")),
        ("Бронекостюм", ("chest", "abdomen", "left_arm", "left_leg"), ("head",)),
        ("Костюм Химзащиты", ("chest", "abdomen", "left_arm", "left_leg", "head"), ()),
    ],
)
def test_named_armor_uses_rulebook_protection_zones(armor_name, covered, not_covered):
    item = {"name": armor_name}

    for zone in covered:
        assert CombatService._armor_covers_zone("armor", item, {}, zone)
    for zone in not_covered:
        assert not CombatService._armor_covers_zone("armor", item, {}, zone)


def test_integrated_exoskeleton_helmet_uses_separate_head_protection():
    armor = {
        "name": "Экзоскелет",
        "protection": {"physical": 0.8},
    }
    character_data = {
        "equipment": {
            "armor": armor,
            "helmet": {
                "integratedWithArmor": True,
                "protection": {"physical": 0.7},
            },
        },
    }

    head_protection, head_layers = CombatService._target_armor(character_data, "head")
    torso_protection, _ = CombatService._target_armor(character_data, "chest")

    assert head_protection == 70
    assert torso_protection == 80
    assert len(head_layers) == 1
    assert head_layers[0]["item"] is armor


def test_armor_limb_protection_is_ten_points_lower_only_when_limbs_are_covered():
    covered = {
        "equipment": {
            "armor": {
                "name": "Test suit",
                "protection": {"physical": 0.3},
                "attributes": {"protection_zones": ["torso", "arms", "legs"]},
            },
        },
    }
    uncovered = {
        "equipment": {
            "armor": {
                "name": "Test vest",
                "protection": {"physical": 0.3},
                "attributes": {"protection_zones": ["torso"]},
            },
        },
    }

    assert CombatService._target_armor(covered, "left_leg")[0] == 20
    assert CombatService._target_armor(covered, "left_arm")[0] == 20
    assert CombatService._target_armor(uncovered, "left_leg")[0] == 0
    assert CombatService._target_armor(uncovered, "left_arm")[0] == 0


def test_integrated_helmet_accuracy_penalty_is_applied_once():
    character_data = {
        "equipment": {
            "armor": {
                "name": "Комбинезон ГРОБ",
                "protection": {"physical": 0.5},
            },
            "helmet": {
                "integratedWithArmor": True,
                "accuracyPenalty": 4,
            },
        },
    }

    assert CombatService._equipment_accuracy_penalty(character_data) == 4


@pytest.mark.parametrize(
    ("posture", "effective_required", "accuracy_penalty"),
    [
        ("standing", 8, 6),
        ("sitting", 6, 2),
        ("prone", 2, 0),
    ],
)
def test_weapon_strength_requirement_accounts_for_posture(
    posture,
    effective_required,
    accuracy_penalty,
):
    character = LocationCharacter(posture=posture)
    character.character = LobbyCharacter(data={
        "skills": {
            "physical": {
                "strength": {"base": 5, "bonus": 0},
            },
        },
    })

    profile = CombatService._weapon_strength_profile(
        character,
        {"minStrength": 8},
    )

    assert profile["effective_required"] == effective_required
    assert profile["accuracy_penalty"] == accuracy_penalty


def test_bipod_removes_strength_requirement_while_prone():
    character = LocationCharacter(posture="prone")
    character.character = LobbyCharacter(data={
        "skills": {
            "physical": {
                "strength": {"base": 1, "bonus": 0},
            },
        },
    })
    weapon = {
        "minStrength": 15,
        "installedModules": [{
            "name": "Сошки",
            "slotType": "handguard",
            "attributes": {"bipod": True},
        }],
    }

    profile = CombatService._weapon_strength_profile(character, weapon)

    assert profile["ignored_by_bipod"] is True
    assert profile["effective_required"] == 0
    assert profile["accuracy_penalty"] == 0


def test_legacy_root_movement_penalty_is_not_applied():
    location_character = LocationCharacter()
    location_character.character = LobbyCharacter(data={"movementPenalty": 9})

    assert CombatService._movement_penalty(location_character) == 0


def test_ammo_stack_weight_matches_inventory_rules():
    light_stack = {"category": "ammo", "quantity": 10, "volume": 0.02, "weight": 99}
    heavy_stack = {"category": "ammo", "quantity": 50, "volume": 0.02, "weight": 99}

    assert CombatService._item_total_weight(light_stack) == 0.1
    assert CombatService._item_total_weight(heavy_stack) == 0.25


@pytest.mark.parametrize(
    ("strength", "weight"),
    [
        (12, 5.5),
        (20, 7.5),
        (1, 2.5),
    ],
)
def test_strength_bonus_changes_weight_per_movement_penalty(strength, weight):
    character_data = {
        "skills": {
            "physical": {
                "strength": {"base": strength, "bonus": 0},
            },
        },
        "inventory": {
            "pockets": [{"category": "item", "weight": weight, "quantity": 1}],
        },
    }

    assert CombatService._inventory_movement_penalty(character_data) == 1


def test_equipped_backpack_reduces_weight_penalty():
    character_data = {
        "skills": {
            "physical": {
                "strength": {"base": 10, "bonus": 0},
            },
        },
        "inventory": {
            "backpack": [{"category": "item", "weight": 12, "quantity": 1}],
        },
        "equipment": {
            "backpack": {
                "category": "backpack",
                "attributes": {"limit": 40, "weight_reduction": 1},
            },
        },
    }

    assert CombatService._inventory_movement_penalty(character_data) == 1


@pytest.mark.parametrize(
    ("posture", "expected_cost"),
    [
        ("standing", 3),
        ("sitting", 6),
        ("prone", 9),
    ],
)
def test_posture_changes_walking_cost(posture, expected_cost):
    result = CombatService._movement_route_cost(
        movement_path(3),
        "walk",
        posture,
    )

    assert result["movement_points"] == expected_cost


def test_correction_movement_remains_free_while_sitting():
    result = CombatService._movement_route_cost(
        movement_path(3),
        "correction",
        "sitting",
    )

    assert result["movement_points"] == 0


@pytest.mark.parametrize(
    ("source", "target", "agility", "expected_cost"),
    [
        ("standing", "sitting", 14, 3),
        ("sitting", "standing", 14, 3),
        ("standing", "prone", 14, 6),
        ("prone", "standing", 14, 6),
    ],
)
def test_agility_reduces_posture_change_movement_cost(source, target, agility, expected_cost):
    location_character = LocationCharacter(posture=source)
    location_character.character = LobbyCharacter(data={
        "skills": {
            "physical": {
                "agility": {"base": agility, "bonus": 0},
            },
        },
    })

    options = CombatService._posture_change_options(location_character, target)

    expected_options = [{"resource": "movement", "cost": expected_cost}]
    if {source, target} == {"standing", "prone"}:
        expected_options.append({"resource": "action", "cost": 2})
    assert options == expected_options


def test_sitting_and_prone_transition_can_use_movement_or_action_points():
    location_character = LocationCharacter(posture="sitting")
    location_character.character = LobbyCharacter(data={})

    options = CombatService._posture_change_options(location_character, "prone")

    assert options == [
        {"resource": "movement", "cost": 4},
        {"resource": "action", "cost": 1},
    ]


def test_current_posture_cannot_be_selected_again():
    location_character = LocationCharacter(posture="standing")
    location_character.character = LobbyCharacter(data={})

    with pytest.raises(ValidationError, match="already"):
        CombatService._posture_change_options(location_character, "standing")


@pytest.mark.parametrize(
    ("posture", "movement_mode"),
    [
        ("sitting", "run"),
        ("sitting", "sprint"),
        ("prone", "run"),
        ("prone", "sprint"),
        ("prone", "correction"),
    ],
)
def test_posture_blocks_incompatible_movement_modes(posture, movement_mode):
    with pytest.raises(ValidationError):
        CombatService._validate_posture_movement(posture, movement_mode)


@pytest.mark.parametrize(
    ("posture", "movement_mode"),
    [
        ("standing", "walk"),
        ("standing", "run"),
        ("standing", "sprint"),
        ("standing", "correction"),
        ("sitting", "walk"),
        ("sitting", "correction"),
        ("prone", "walk"),
    ],
)
def test_posture_allows_compatible_movement_modes(posture, movement_mode):
    profile = CombatService._validate_posture_movement(posture, movement_mode)

    assert profile


@pytest.mark.parametrize(
    ("cover_class", "max_hp", "protection"),
    [
        ("conditional", 25, 0),
        ("flimsy", 50, 5),
        ("medium", 100, 20),
        ("strong", 200, 40),
        ("very_strong", 400, 60),
        ("titanium", 800, 90),
    ],
)
def test_cover_class_profiles(cover_class, max_hp, protection):
    cover = SimpleNamespace(properties={"cover_class": cover_class})

    profile = CombatService._cover_profile(cover)

    assert COVER_CLASSES[cover_class]["max_hp"] == max_hp
    assert profile["hp"] == max_hp
    assert profile["physical_protection"] == protection


def test_damaged_cover_loses_physical_protection_proportionally():
    cover = SimpleNamespace(properties={
        "cover_class": "strong",
        "cover_max_hp": 200,
        "cover_hp": 50,
    })

    profile = CombatService._cover_profile(cover)

    assert profile["physical_protection"] == 10


def test_bullet_damage_reduces_cover_hp_and_protection_without_deleting_cover(monkeypatch):
    monkeypatch.setattr("app.services.combat.flag_modified", lambda *args: None)
    cover = SimpleNamespace(properties={
        "cover_class": "strong",
        "cover_max_hp": 200,
        "cover_hp": 200,
        "cover_base_physical_protection": 40,
        "cover_physical_protection": 40,
    })

    result = CombatService.apply_cover_damage(cover, 15, "bullet")

    assert result["destroyed"] is False
    assert result["hp"] == 185
    assert result["physical_protection"] == 37
    assert cover.properties["cover_hp"] == 185
    assert cover.properties["cover_physical_protection"] == 37


def test_bullet_damage_leaves_zero_hp_cover_on_map(monkeypatch):
    monkeypatch.setattr("app.services.combat.flag_modified", lambda *args: None)
    cover = SimpleNamespace(properties={
        "cover_class": "flimsy",
        "cover_max_hp": 50,
        "cover_hp": 50,
        "cover_base_physical_protection": 5,
        "cover_physical_protection": 5,
    })

    result = CombatService.apply_cover_damage(cover, 100, "bullet")

    assert result["destroyed"] is False
    assert result["hp"] == 0
    assert result["physical_protection"] == 0


@pytest.mark.parametrize("damage_type", ["explosive", "crushing", "дробящий"])
def test_explosive_and_crushing_damage_destroy_zero_hp_cover(app, monkeypatch, damage_type):
    monkeypatch.setattr("app.services.combat.flag_modified", lambda *args: None)
    monkeypatch.setattr("app.services.combat.db.session.delete", lambda *args: None)
    cover = SimpleNamespace(id=7, properties={
        "cover_class": "flimsy",
        "cover_max_hp": 50,
        "cover_hp": 50,
        "cover_base_physical_protection": 5,
        "cover_physical_protection": 5,
    })

    result = CombatService.apply_cover_damage(cover, 50, damage_type)

    assert result["destroyed"] is True
    assert result["hp"] == 0


def test_object_between_shooter_and_target_intersects_fire_line():
    shooter = SimpleNamespace(pos_x=0, pos_y=0)
    target = SimpleNamespace(pos_x=4, pos_y=0)
    cover = SimpleNamespace(
        tile_x=2,
        tile_y=0,
        type="wall",
        properties={"dimensions": {"width": 1, "depth": 1}},
    )

    assert CombatService._line_object_entry(shooter, target, cover) == pytest.approx(0.375)


def test_object_outside_fire_line_does_not_intersect():
    shooter = SimpleNamespace(pos_x=0, pos_y=0)
    target = SimpleNamespace(pos_x=4, pos_y=0)
    cover = SimpleNamespace(
        tile_x=2,
        tile_y=2,
        type="wall",
        properties={"dimensions": {"width": 1, "depth": 1}},
    )

    assert CombatService._line_object_entry(shooter, target, cover) is None


@pytest.mark.parametrize(
    ("ergonomics", "draw", "reload_modifier", "aimed_cost", "accuracy"),
    [
        (0, 4, 2, 6, -2),
        (10, 4, 2, 6, -2),
        (11, 3, 2, 6, -1),
        (20, 3, 2, 6, -1),
        (21, 3, 1, 5, -1),
        (30, 3, 1, 5, -1),
        (31, 3, 1, 5, 0),
        (40, 3, 1, 5, 0),
        (41, 2, 1, 4, 0),
        (50, 2, 1, 4, 0),
        (51, 2, 0, 4, 0),
        (70, 2, 0, 4, 0),
        (71, 1, 0, 3, 0),
        (80, 1, 0, 3, 0),
        (81, 1, 0, 3, 1),
        (90, 1, 0, 3, 1),
        (91, 1, -1, 3, 1),
        (99, 1, -1, 3, 1),
        (100, 0, -2, 2, 2),
        (140, 0, -2, 2, 2),
    ],
)
def test_ergonomics_thresholds(ergonomics, draw, reload_modifier, aimed_cost, accuracy):
    profile = CombatService._ergonomics_effects(ergonomics)

    assert profile["draw_action_points"] == draw
    assert profile["reload_action_points_modifier"] == reload_modifier
    assert profile["aimed_shot_action_points"] == aimed_cost
    assert profile["accuracy_modifier"] == accuracy


def test_weapon_ergonomics_combines_all_available_sources():
    location_character = LocationCharacter(posture="sitting")
    location_character.character = LobbyCharacter(data={
        "skills": {
            "physical": {"shooting": {"base": 15, "bonus": 4}},
            "other": {"tactics": {"base": 10, "bonus": 3}},
        },
        "equipment": {
            "helmet": {"ergonomicsPenalty": 5},
        },
    })
    weapon = {
        "ergonomics": 20,
        "installedModules": [
            {"modifiers": {"ergonomics": "+5"}},
        ],
        "installedMagazine": {
            "ergonomics": -5,
        },
    }

    profile = CombatService._weapon_ergonomics_profile(location_character, weapon, 2)

    assert profile["value"] == 57
    assert profile["weapon_index"] == 2
    assert profile["shooting_value"] == 19
    assert profile["tactics_value"] == 13
    assert profile["posture_bonus"] == 10
    assert profile["module_modifier"] == 5
    assert profile["magazine_modifier"] == -5
    assert profile["helmet_penalty"] == 5
    assert profile["draw_action_points"] == 2
    assert profile["reload_action_points_modifier"] == 0
    assert profile["aimed_shot_action_points"] == 4


def test_active_weapon_is_read_from_persistent_character_data():
    location_character = LocationCharacter(drawn_weapon_index=None)
    location_character.character = LobbyCharacter(
        data={
            "weapons": [{"name": "first"}, {"name": "second"}],
            "activeWeaponIndex": 1,
        }
    )

    assert CombatService._persistent_weapon_index(location_character) == 1


def test_setting_active_weapon_updates_location_and_character_data():
    location_character = LocationCharacter(drawn_weapon_index=None)
    location_character.character = LobbyCharacter(
        data={"weapons": [{"name": "first"}, {"name": "second"}]}
    )

    CombatService._set_active_weapon(location_character, 1)

    assert location_character.drawn_weapon_index == 1
    assert location_character.character.data["activeWeaponIndex"] == 1


def test_invalid_persistent_weapon_index_is_ignored():
    location_character = LocationCharacter(drawn_weapon_index=None)
    location_character.character = LobbyCharacter(
        data={"weapons": [{"name": "only"}], "activeWeaponIndex": 4}
    )

    assert CombatService._persistent_weapon_index(location_character) is None


def test_aim_bonus_applies_only_to_same_target_and_weapon():
    location_character = LocationCharacter(
        aimed_target_character_id=12,
        aimed_weapon_index=1,
        aim_accuracy_bonus=4,
    )

    assert CombatService._aim_bonus_for_target(location_character, 12, 1) == 4
    assert CombatService._aim_bonus_for_target(location_character, 13, 1) == 0
    assert CombatService._aim_bonus_for_target(location_character, 12, 0) == 0


def test_clear_aim_resets_target_weapon_and_bonus():
    location_character = LocationCharacter(
        aimed_target_character_id=12,
        aimed_weapon_index=1,
        aim_accuracy_bonus=4,
    )

    CombatService._clear_aim(location_character)

    assert location_character.aimed_target_character_id is None
    assert location_character.aimed_weapon_index is None
    assert location_character.aim_accuracy_bonus == 0


@pytest.mark.parametrize('obj_type', ['chair', 'fence'])
@pytest.mark.parametrize('flag', ['blocks_movement', 'block_movement'])
@pytest.mark.parametrize('other_value', [None, True])
def test_negative_movement_flags_override_climb_cost(obj_type, flag, other_value):
    other = 'block_movement' if flag == 'blocks_movement' else 'blocks_movement'
    properties = {flag: False, 'climb_cost': 9}
    if other_value is not None:
        properties[other] = other_value
    assert CombatService._object_movement_profile({
        'type': obj_type, 'properties': properties,
    }) == {'blocked': False, 'climb_cost': 0}


@pytest.mark.parametrize('obj_type,expected', [('chair', 5), ('fence', 12)])
def test_default_and_custom_climb_cost_are_preserved(obj_type, expected):
    assert CombatService._object_movement_profile({'type': obj_type}) == {
        'blocked': False, 'climb_cost': expected,
    }
    assert CombatService._object_movement_profile({
        'type': obj_type, 'properties': {'climb_cost': 8},
    }) == {'blocked': False, 'climb_cost': 8}


@pytest.mark.parametrize('flag', ['blocks_movement', 'block_movement'])
@pytest.mark.parametrize('obj_type,properties,expected', [
    ('door', {}, {'blocked': True, 'climb_cost': 0}),
    ('window', {}, {'blocked': True, 'climb_cost': 0}),
    ('door', {'is_open': True}, {'blocked': False, 'climb_cost': 0}),
    ('door', {'climbable': True}, {'blocked': False, 'climb_cost': 12}),
    ('door', {'climbable': True, 'climb_cost': 7}, {'blocked': False, 'climb_cost': 7}),
    ('door', {'passable': True}, {'blocked': False, 'climb_cost': 0}),
    ('window', {'passable': True}, {'blocked': False, 'climb_cost': 0}),
    ('floor', {}, {'blocked': False, 'climb_cost': 0}),
])
def test_portals_and_passable_keep_priority_over_negative_flags(flag, obj_type, properties, expected):
    assert CombatService._object_movement_profile({
        'type': obj_type, 'properties': {**properties, flag: False},
    }) == expected


@pytest.mark.parametrize('portal_type', ['door', 'window'])
@pytest.mark.parametrize('wall_props,portal_props,expected', [
    ({'building_id': 'A'}, {'building_id': 'A'}, True),
    ({'building_id': 'A'}, {'building_id': 'B'}, False),
    ({}, {}, True),
    ({'building_id': None}, {}, True),
    ({'building_id': ''}, {'building_id': '   '}, True),
    ({'building_id': ' A '}, {'building_id': 'A'}, True),
    ({'building_id': 42}, {'building_id': ' 42 '}, True),
    ({'building_id': 'A'}, {}, False),
    ({}, {'building_id': 'A'}, False),
    ({'building_id': 'A', 'level': 0}, {'building_id': 'A', 'level': 1}, False),
    ({'building_id': 'A'}, {'building_id': 'A', 'rotation': 1.5707963267948966}, False),
    ({'building_id': 'A'}, {'building_id': 'A', 'offset_z': 2}, False),
    ({'building_id': 'A'}, {'building_id': 'A', 'elevation': 3}, False),
])
def test_wall_portal_requires_same_normalized_building_and_geometry(portal_type, wall_props, portal_props, expected):
    wall = SimpleNamespace(type='wall', tile_x=2, tile_y=2, properties=wall_props)
    portal = SimpleNamespace(type=portal_type, tile_x=2, tile_y=2, properties=portal_props)
    assert CombatService._door_is_in_wall(portal, wall) is expected


@pytest.mark.parametrize("terrain", ["snow", "sand", "swamp"])
def test_difficult_surface_route_cost_is_doubled(terrain, monkeypatch):
    location = SimpleNamespace(grid_width=3, grid_height=1, tiles_data=[
        [{"terrain": "concrete"}, {"terrain": terrain}, {"terrain": terrain}]
    ])
    monkeypatch.setattr(CombatService, "_build_movement_map", staticmethod(lambda *a, **k: (set(), {})))
    route = CombatService._find_movement_path(location, 0, 0, 2, 0)
    assert route["cost"] == 3.5
    assert CombatService._movement_route_cost(route, "walk")["movement_points"] == 3.5
    assert CombatService._movement_route_cost(route, "run")["movement_points"] == 1.75

def test_mixed_surface_route_counts_destination_and_keeps_climb_separate(monkeypatch):
    location = SimpleNamespace(grid_width=4, grid_height=1, tiles_data=[
        [{"terrain": "snow"}, {"terrain": "concrete"}, {"terrain": "sand"}, {"terrain": "concrete"}]
    ])
    monkeypatch.setattr(CombatService, "_build_movement_map", staticmethod(lambda *a, **k: (set(), {(2, 0): 5})))
    route = CombatService._find_movement_path(location, 0, 0, 3, 0)
    assert route["cost"] == 9.5
    assert route["climb_cost"] == 5
    assert CombatService._movement_route_cost(route, "run")["movement_points"] == 7.25


@pytest.mark.parametrize("terrain", ["grass", "concrete", "wood", "granite", "mud", "water"])
def test_unclassified_surface_is_not_implicitly_doubled(terrain):
    location = SimpleNamespace(tiles_data=[[{"terrain": terrain}]])
    assert CombatService._movement_surface_costs(location) == {}

@pytest.mark.parametrize("species", ["bush", "tall_grass"])
@pytest.mark.parametrize("state", ["green", "dry", "dead"])
def test_existing_vegetation_ids_cost_two(species, state):
    location = SimpleNamespace(tiles_data=[[{"terrain": "grass", "objects": []}]])
    obj = {"type": "tree", "tile_x": 0, "tile_y": 0, "properties": {
        "visual_asset": f"nature_{species}_{state}", "passable": True,
        "dimensions": {"width": .8, "depth": .8}}}
    assert CombatService._movement_surface_costs(location, [obj])[(0, 0, 0)] == 2

def test_floor_does_not_inherit_ground_snow_and_upper_vegetation_is_level_specific():
    location = SimpleNamespace(tiles_data=[[{"terrain": "snow"}]])
    objects = [{"type": "floor", "tile_x": 0, "tile_y": 0, "properties": {
        "level": 0, "dimensions": {"width": .8, "depth": .8}}},
        {"type": "tree", "tile_x": 0, "tile_y": 0, "properties": {
        "level": 1, "visual_asset": "nature_bush_green", "dimensions": {"width": .8, "depth": .8}}}]
    assert CombatService._movement_surface_costs(location, objects) == {(0, 0, 1): 2}

def test_storey_path_uses_the_same_difficult_surface_weights():
    from app.services.location_navigation import find_path
    location = SimpleNamespace(grid_width=4, grid_height=1, tiles_data=[[
        {"height": 1, "terrain": terrain} for terrain in ["grass", "sand", "swamp", "snow"]]])
    costs = CombatService._movement_surface_costs(location)
    result = find_path(location, [], (0, 0, 0), (3, 0, 0), lambda level: (set(), {}), terrain_costs=costs)
    assert result["cost"] == 6
    assert result["climb_cost"] == 0

def test_pathfinder_prefers_shorter_cost_over_shorter_difficult_route(monkeypatch):
    tiles = [[{"terrain": "grass"} for _ in range(3)] for _ in range(3)]
    tiles[1][1]["terrain"] = "sand"
    location = SimpleNamespace(grid_width=3, grid_height=3, tiles_data=tiles)
    monkeypatch.setattr(CombatService, "_build_movement_map", staticmethod(lambda *a, **k: (set(), {})))
    result = CombatService._find_movement_path(location, 0, 1, 2, 1)
    assert result["cost"] == pytest.approx(2**.5*2)
    assert (1, 1) not in result["path"]


def test_flight_keeps_existing_difficult_surface_immunity():
    location = SimpleNamespace(tiles_data=[[{"terrain": "snow"}]])
    assert CombatService._movement_surface_costs(location, ignores_terrain=True) == {}


@pytest.mark.parametrize(('strength', 'modifier', 'distance'), [(6, -2, 1.6), (10, 0, 2), (18, 4, 2.8)])
@pytest.mark.parametrize('mode', ['walk', 'run', 'sprint'])
def test_jump_preview_uses_canonical_modifier_and_running_rules(strength, modifier, distance, mode):
    data = {'skills': {'physical': {'strength': {'base': strength, 'bonus': 0}, 'agility': {'base': 14, 'bonus': 0}}}}
    preview = CombatService._jump_profile(data, mode, 2)
    assert preview['strength_modifier'] == modifier
    assert preview['base_distance'] == distance
    assert preview['max_distance'] == pytest.approx(distance * (2 if mode != 'walk' else 1))
    assert preview['difficulty'] == (10 if mode == 'walk' else 6)
    assert preview['agility_modifier'] == 2
    assert preview['action_points'] == 2
    assert preview['has_action_points'] is True
    assert preview['execution_available'] is True


def test_jump_preview_does_not_roll_or_mutate_and_reports_missing_ap(monkeypatch):
    import copy
    import app.services.combat as module
    monkeypatch.setattr(module.random, 'randint', lambda *args: pytest.fail('preview must not roll'))
    data = {'skills': {'physical': {'strength': {'base': 10, 'bonus': 0}}}}
    before = copy.deepcopy(data)
    assert CombatService._jump_profile(data, 'run', 1)['has_action_points'] is False
    assert data == before


def test_running_failure_rule_takes_priority_over_unstable_edge():
    assert CombatService._jump_profile({}, 'walk')['failure_1_to_3'] == 'unstable_edge'
    for mode in ['run', 'sprint']:
        preview = CombatService._jump_profile({}, mode)
        assert preview['failure_1_to_3'] == preview['failure_4_or_more'] == 'midpoint'


@pytest.mark.parametrize(('rise', 'walkable'), [(.99, True), (1, False), (1.01, False), (1.99, False), (2, False), (-1, False), (-2, False)])
def test_ordinary_walk_cannot_replace_explicit_height_transition(rise, walkable, monkeypatch):
    location = SimpleNamespace(grid_width=2, grid_height=1, tiles_data=[[{'height': 4}, {'height': 4 + rise}]])
    monkeypatch.setattr(CombatService, '_build_movement_map', staticmethod(lambda *a, **k: (set(), {})))
    result = CombatService._find_movement_path(location, 0, 0, 1, 0)
    assert (result is not None) is walkable


@pytest.mark.parametrize(('rise', 'walkable'), [(.99, True), (1, False), (1.99, False), (2, False), (-1, False), (-2, False)])
def test_storey_plain_surface_checks_real_height_not_only_level(rise, walkable):
    from app.services.location_navigation import connects
    a = {'x': 0, 'y': 0, 'level': 0, 'height': 4, 'stair': None}
    b = {'x': 1, 'y': 0, 'level': 0, 'height': 4 + rise, 'stair': None}
    assert connects(a, b) is walkable


@pytest.fixture
def gm_relocation_game(client, create_user, auth_headers):
    from app.extensions import db
    from app.models import Location, LocationObject
    gm, owner = create_user("gm-relocation-gm"), create_user("gm-relocation-owner")
    lobby_id = client.post("/lobbies/", headers=auth_headers(gm), json={
        "name": "GM relocation", "map_type": "empty", "chunks_width": 1, "chunks_height": 1
    }).get_json()["id"]
    assert client.post(f"/lobbies/{lobby_id}/join", headers=auth_headers(owner)).status_code == 200
    character = client.post(f"/lobbies/{lobby_id}/characters", headers=auth_headers(owner),
                            json={"name": "Relocation actor", "data": {}}).get_json()
    location = Location(lobby_id=lobby_id, name="Relocation", world_tile_x=1, world_tile_z=1,
                        grid_width=5, grid_height=5,
                        tiles_data=[[{"terrain": "concrete", "height": 1, "objects": []} for _ in range(5)] for _ in range(5)])
    db.session.add(location)
    db.session.flush()
    db.session.add(LocationObject(location_id=location.id, name="Upper floor", type="floor", tile_x=2, tile_y=2,
        properties={"level": 1, "dimensions": {"width": 5, "depth": 5, "height": .12}}))
    placed = LocationCharacter(location_id=location.id, character_id=character["id"], pos_x=1, pos_y=1,
        floor_level=1, controlled_by=owner["id"], action_points_current=3, movement_points_current=7)
    db.session.add(placed)
    db.session.commit()
    return gm, owner, f"/lobbies/{lobby_id}/locations/{location.id}/spawn_character", placed

def test_existing_character_relocation_is_gm_only(client, auth_headers, gm_relocation_game):
    from app.extensions import db
    gm, owner, url, placed = gm_relocation_game
    response = client.post(url, headers=auth_headers(owner), json={
        "character_id": placed.character_id, "tile_x": 2, "tile_y": 2})
    assert response.status_code == 403
    db.session.refresh(placed)
    assert (placed.pos_x, placed.pos_y, placed.floor_level) == (1, 1, 1)
    assert (placed.action_points_current, placed.movement_points_current) == (3, 7)

def test_gm_relocation_keeps_floor_controller_and_resources(client, auth_headers, gm_relocation_game, monkeypatch):
    from app.extensions import db
    import app.lobbies as module
    gm, owner, url, placed = gm_relocation_game
    events = []
    monkeypatch.setattr(module.socketio, "emit", lambda name, payload, **kw: events.append((name, payload)))
    response = client.post(url, headers=auth_headers(gm), json={
        "character_id": placed.character_id, "tile_x": 2, "tile_y": 2})
    assert response.status_code == 200
    db.session.refresh(placed)
    assert (placed.pos_x, placed.pos_y, placed.floor_level) == (2, 2, 1)
    assert (placed.action_points_current, placed.movement_points_current) == (3, 7)
    event = next(payload["character"] for name, payload in events if name == "character_spawned")
    assert event["controlled_by"] == owner["id"]
    assert event["floor_level"] == 1

@pytest.mark.parametrize("x", [True, -1, 5, float('inf'), "2"])
def test_gm_relocation_rejects_invalid_coordinates_without_changes(client, auth_headers, gm_relocation_game, x):
    from app.extensions import db
    gm, owner, url, placed = gm_relocation_game
    response = client.post(url, headers=auth_headers(gm), json={
        "character_id": placed.character_id, "tile_x": x, "tile_y": 2})
    assert response.status_code == 400
    db.session.refresh(placed)
    assert (placed.pos_x, placed.pos_y, placed.floor_level) == (1, 1, 1)
    assert (placed.action_points_current, placed.movement_points_current) == (3, 7)

def test_gm_relocation_can_choose_supported_floor_but_not_empty_air(client, auth_headers, gm_relocation_game):
    from app.extensions import db
    gm, owner, url, placed = gm_relocation_game
    payload = {"character_id": placed.character_id, "tile_x": 2, "tile_y": 2, "floor_level": 2}
    assert client.post(url, headers=auth_headers(gm), json=payload).status_code == 400
    payload["floor_level"] = 0
    assert client.post(url, headers=auth_headers(gm), json=payload).status_code == 200
    db.session.refresh(placed)
    assert placed.floor_level == 0
    assert (placed.action_points_current, placed.movement_points_current) == (3, 7)


def test_gm_explicit_transfer_can_place_in_air_and_preserves_character(client,auth_headers,gm_relocation_game,monkeypatch):
    from copy import deepcopy
    from app.extensions import db
    import app.lobbies as module
    gm,owner,url,placed=gm_relocation_game
    placed.facing_x,placed.facing_y=-1,0
    placed.posture='prone';placed.status='busy';placed.team_name='Команда';placed.team_color='#123456'
    placed.movement_distance_this_turn=2.5
    db.session.commit()
    health=deepcopy(placed.character.data)
    events=[];monkeypatch.setattr(module.socketio,'emit',lambda name,payload,**kw:events.append((name,payload)))
    response=client.post(url,headers=auth_headers(gm),json={
        'character_id':placed.character_id,'tile_x':3.2,'tile_y':3.7,'floor_level':2,'force_relocate':True})
    assert response.status_code==200
    db.session.refresh(placed)
    assert (placed.pos_x,placed.pos_y,placed.floor_level)==(3.2,3.7,2)
    assert (placed.action_points_current,placed.movement_points_current,placed.movement_distance_this_turn)==(3,7,2.5)
    assert (placed.facing_x,placed.facing_y,placed.posture,placed.status)==(-1,0,'prone','busy')
    assert placed.controlled_by==owner['id'] and placed.character.data==health
    event=next(payload['character'] for name,payload in events if name=='character_spawned')
    assert (event['facing_x'],event['facing_y'],event['team_name'],event['team_color'])==(-1,0,'Команда','#123456')


@pytest.mark.parametrize('flag',[True,'true',1,[]])
def test_player_cannot_use_gm_transfer_flag(client,auth_headers,gm_relocation_game,flag):
    from app.extensions import db
    gm,owner,url,placed=gm_relocation_game
    response=client.post(url,headers=auth_headers(owner),json={
        'character_id':placed.character_id,'tile_x':3.2,'tile_y':3.7,'floor_level':2,'force_relocate':flag})
    assert response.status_code in (400,403)
    db.session.refresh(placed)
    assert (placed.pos_x,placed.pos_y,placed.floor_level)==(1,1,1)


@pytest.mark.parametrize('legacy',[False,True])
def test_gm_can_restore_psi_zombie_sheet_with_current_loot_and_without_heal(client,auth_headers,gm_relocation_game,legacy):
    from copy import deepcopy
    from app.extensions import db
    from app.services.character_roles import is_mutant
    gm,owner,url,placed=gm_relocation_game
    data=deepcopy(placed.character.data)
    data['skills']={'physical':{'shooting':{'base':13,'bonus':0,'xp':2}}}
    data['inventory']={'backpack':[{'id':'saved-loot','name':'Сохранённый предмет','quantity':3}], 'pockets':[]}
    data['equipment']={'helmet':{'id':'helmet','name':'Шлем','durability':41}}
    data['weapons']=[{'id':'ordinary-rifle','name':'Винтовка','durability':37,'ammo':2}]
    placed.character.data=data
    assert CombatService._transform_into_zombie(placed,data)
    assert is_mutant(placed.character.data)
    zombie=deepcopy(placed.character.data)
    if legacy:zombie.pop('psiZombification',None)
    zombie['inventory']['backpack'][0]['quantity']=1
    zombie['inventory']['backpack'].append({'id':'new-loot','name':'Новая добыча','quantity':1})
    zombie['health']['current']=33
    zombie['health']['zones']['head']['current']=14
    placed.character.data=zombie;db.session.commit()
    restore_url=f'/lobbies/{placed.character.lobby_id}/characters/{placed.character_id}/restore-player'
    assert client.post(restore_url,headers=auth_headers(owner)).status_code==403
    if not legacy:
        snapshot=client.get(f'/lobbies/characters/{placed.character_id}',headers=auth_headers(gm)).get_json()['data']
        snapshot['psiZombification']['previousData']['skills']['physical']['shooting']['base']=999
        saved=client.put(f'/lobbies/characters/{placed.character_id}',headers=auth_headers(gm),json={'data':snapshot})
        assert saved.status_code==200
    response=client.post(restore_url,headers=auth_headers(gm))
    assert response.status_code==200,response.get_json()
    restored=response.get_json()['data']
    assert not is_mutant(restored)
    # Снимок дополнительно содержит вычисленную вместимость карманов;
    # сами предметы и снаряжение должны сохраниться без изменений.
    assert restored['inventory']=={**zombie['inventory'],'pocketMaxVolume':10}
    assert restored['equipment']==zombie['equipment']
    assert restored['weapons']==[{'id':'ordinary-rifle','name':'Винтовка','durability':37,'ammo':2}]
    assert restored['health']['current']==pytest.approx(33)
    assert restored['health']['zones']['head']['current']==pytest.approx(14)
    assert 'mutant' not in restored and 'psiZombification' not in restored
    if not legacy:assert restored['skills']['physical']['shooting']['base']==13
    db.session.refresh(placed)
    assert placed.controlled_by==owner['id']
    assert client.post(restore_url,headers=auth_headers(gm)).status_code==400

def test_owner_initial_spawn_remains_available(client, auth_headers, gm_relocation_game):
    gm, owner, url, placed = gm_relocation_game
    lobby_url = url.split("/locations/")[0]
    character = client.post(lobby_url + "/characters", headers=auth_headers(owner),
        json={"name": "Initial spawn", "data": {}}).get_json()
    response = client.post(url, headers=auth_headers(owner), json={
        "character_id": character["id"], "tile_x": 3, "tile_y": 3})
    assert response.status_code == 200


def test_high_terrain_blocks_server_line_of_sight(app, gm_relocation_game):
    from app.extensions import db
    from app.models import Location, LocationObject
    gm, owner, url, placed = gm_relocation_game
    location = db.session.get(Location, placed.location_id)
    for floor in LocationObject.query.filter_by(location_id=location.id, type='floor').all():
        db.session.delete(floor)
    location.tiles_data = [[{"terrain": "grass", "height": 6 if x == 2 else 1, "objects": []}
                            for x in range(5)] for _ in range(5)]
    db.session.flush()
    shooter = SimpleNamespace(pos_x=0, pos_y=2, floor_level=0, posture="standing")
    target = SimpleNamespace(pos_x=4, pos_y=2, floor_level=0, posture="standing")
    analysis = CombatService._cover_analysis(location.id, shooter, target)
    assert analysis["grade"] == "full"
    assert all(zone.get("terrain") for zone in analysis["zones"].values())

def test_raised_shooter_ray_uses_real_ground_height(app, gm_relocation_game):
    from app.extensions import db
    from app.models import Location, LocationObject
    gm, owner, url, placed = gm_relocation_game
    location = db.session.get(Location, placed.location_id)
    for floor in LocationObject.query.filter_by(location_id=location.id, type='floor').all():
        db.session.delete(floor)
    location.tiles_data = [[{"terrain": "grass", "height": 5 if x == 0 else 1, "objects": []}
                            for x in range(5)] for _ in range(5)]
    db.session.add(LocationObject(location_id=location.id, name="Low cover", type="wall", tile_x=2, tile_y=2,
        properties={"dimensions": {"width": .2, "depth": .2, "height": 2.5}}))
    db.session.flush()
    shooter = SimpleNamespace(pos_x=0, pos_y=2, floor_level=0, posture="standing")
    target = SimpleNamespace(pos_x=4, pos_y=2, floor_level=0, posture="standing")
    assert CombatService._cover_analysis(location.id, shooter, target)["grade"] == "none"


def test_terrain_server_matches_actual_renderer_triangles_and_rays():
    import json
    from pathlib import Path
    from app.services.terrain_los import TerrainMesh
    golden = json.loads((Path(__file__).parent / "fixtures/terrain_los_parity.json").read_text(encoding="utf-8"))
    for case in golden["cases"]:
        mesh = TerrainMesh(case["tiles"])
        triangles = mesh.triangles(2, 2)[0]
        assert len(triangles) == len(case["triangles"]), case["name"]
        for actual, expected in zip(triangles, case["triangles"]):
            for actual_point, expected_point in zip(actual, expected):
                assert actual_point == pytest.approx(expected_point, abs=1e-9), case["name"]
        for (start, end), expected in zip(case["rays"], case["hits"]):
            hit = mesh.first_hit(start, end)
            assert bool(hit) == bool(expected), case["name"]
            if hit:
                assert hit["t"] == pytest.approx(expected["t"], abs=1e-9), case["name"]
                assert hit["height"] == pytest.approx(expected["height"], abs=1e-9)

def test_terrain_open_hatch_is_not_filled_by_a_synthetic_ground_block():
    from app.services.terrain_los import TerrainMesh
    from app.services.location_navigation import opening_at
    tiles = [[{"terrain": "grass", "height": 6 if x == 2 else 1} for x in range(5)] for _ in range(5)]
    hatch = {"type": "hatch", "tile_x": 2, "tile_y": 2,
             "properties": {"level": 0, "is_open": True, "dimensions": {"width": 5, "depth": 5}}}
    mesh = TerrainMesh(tiles)
    accept = lambda hit: not opening_at([hatch], 0, hit["x"], hit["z"])
    assert mesh.first_hit((.5, 2.65, 2.5), (4.5, 2.7, 2.5), accept) is None
    hatch["properties"]["is_open"] = False
    assert mesh.first_hit((.5, 2.65, 2.5), (4.5, 2.7, 2.5), accept)


def test_ranged_hit_on_terrain_blocked_zone_cannot_damage_target(monkeypatch):
    import copy
    attacker = SimpleNamespace(character=SimpleNamespace(data={'weapons': [{}]}))
    target = SimpleNamespace(character=SimpleNamespace(data={'health': {'current': 700}}), hp_zones={},
                             grapple_live_shield=False, grapple_target_id=None)
    before = copy.deepcopy(target.character.data)
    monkeypatch.setattr(CombatService, '_ranged_damage_profile', lambda weapon: ({'damage': 100, 'armor_piercing': 20, 'effective_range': 0}, None))
    monkeypatch.setattr(CombatService, '_random_hit_zone', lambda *args: 'chest')
    monkeypatch.setattr(CombatService, '_live_shield_target', lambda *args: None)
    monkeypatch.setattr(CombatService, '_apply_attack_damage', lambda *a, **kw: pytest.fail('Terrain blocked projectile must not damage target'))
    result = CombatService._resolve_attack(target, attacker, {
        'weapon_index': 0, 'hit_difficulty': 1, 'fire_mode': 'unaimed', 'round_number': 1,
        'cover': {'zones': {'chest': {'terrain': True, 'object_id': None}}}}, forced_roll=20)
    assert result['blocked_by_terrain'] is True
    assert result['damage'] == 0
    assert target.character.data == before


def test_wall_sweep_checks_the_whole_segment_and_round_corners():
    from app.services.movement_geometry import WallBox
    wall = WallBox(0, 0, 2, .1, 0, 0)
    assert wall.intersects_sweep((0, -1), (0, 1))
    assert not wall.intersects_sweep((-2, -.4), (2, -.4))
    corner = WallBox(0, 0, 2, 2, 0, 0)
    assert not corner.intersects_sweep((1.2, 1.2), (1.2, 1.2))
    assert corner.intersects_sweep((1.18, 1.18), (1.18, 1.18))
    assert not corner.intersects_sweep((1.26, 0), (1.26, 0))


@pytest.mark.parametrize('angle', [0, .4, 1.5707963267948966])
def test_rotated_wall_sweep_uses_local_axes_instead_of_its_aabb(angle):
    import math
    from app.services.movement_geometry import WallBox
    wall = WallBox(5, 5, 4, .2, angle, 0)
    def world(x, y):
        return 5 + x * math.cos(angle) + y * math.sin(angle), 5 - x * math.sin(angle) + y * math.cos(angle)
    assert not wall.intersects_sweep(world(-1.5, -.4), world(1.5, -.4))
    assert wall.intersects_sweep(world(0, -1), world(0, 1))


@pytest.mark.parametrize('width, allowed', [(.5, False), (.6, True), (.9, True)])
def test_open_door_uses_actual_circle_clearance_and_same_building(width, allowed):
    wall = SimpleNamespace(type='wall', tile_x=2, tile_y=2, properties={
        'building_id': 'A', 'dimensions': {'width': 5, 'depth': .2, 'height': 2.4},
    })
    door = SimpleNamespace(type='door', tile_x=2, tile_y=2, properties={
        'building_id': 'A', 'is_open': True,
        'dimensions': {'width': width, 'depth': .18, 'height': 2},
    })
    location = SimpleNamespace(tiles_data=[])
    geometry = CombatService._movement_wall_geometry(location, [wall, door])
    assert geometry.can_traverse((2, 1, 0), (2, 3, 0)) is allowed
    door.properties['building_id'] = 'B'
    geometry = CombatService._movement_wall_geometry(location, [wall, door])
    assert not geometry.can_traverse((2, 1, 0), (2, 3, 0))
    door.properties.update(building_id='A', is_open=False)
    assert not CombatService._movement_wall_geometry(location, [wall, door]).can_traverse((2, 1, 0), (2, 3, 0))


def test_embedded_walls_and_climbable_objects_keep_distinct_movement_rules():
    location = SimpleNamespace(grid_width=3, grid_height=3, tiles_data=[[{'objects': [
        {'type': 'wall', 'properties': {'dimensions': {'width': 3, 'depth': .1, 'height': 2.4}}},
        {'type': 'wall', 'x': 2, 'z': 2, 'properties': {'climbable': True}},
    ]}]])
    geometry = CombatService._movement_wall_geometry(location, [])
    assert not geometry.can_traverse((0, -.5, 0), (0, .5, 0))
    assert geometry.can_traverse((0, -.5, 1), (0, .5, 1))
    assert len(geometry.boxes) == 1


def test_pripyat_saved_wall_no_longer_blocks_its_whole_edge_cell(monkeypatch):
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/pripyat_wall_geometry.json').read_text(encoding='utf-8'))
    # Реальная стена sourceIndex=4 из SHIFT03-NAV-GEOMETRY, не новый полигон.
    source = fixture['objects'][2]
    assert (source['type'], source['tile_x'], source['tile_y']) == ('wall', 6, 6)
    location = SimpleNamespace(grid_width=72, grid_height=72,
                               tiles_data=[[{'height': 1} for _ in range(72)] for _ in range(72)])
    objects = [SimpleNamespace(**o) for o in fixture['objects']]
    geometry = CombatService._movement_wall_geometry(location, objects)
    assert geometry.handles(objects[2])
    assert (5, 5) in CombatService._object_footprint_tiles(source)
    assert (5, 5) not in geometry.blocked_cells(0, 72, 72)
    assert geometry.can_traverse((5, 5, 0), (5, 6, 0))
    assert not geometry.can_traverse((5, 5, 0), (6, 5, 0))
    # Смещённый проём исходной стены №2 ещё требует дробных узлов.
    assert geometry.handles(objects[0])
    assert geometry.can_traverse((6.5,3.5,0),(6.5,5.5,0))
    monkeypatch.setattr(CombatService, '_movement_wall_geometry', staticmethod(lambda *args: geometry))
    monkeypatch.setattr(CombatService, '_build_movement_map', staticmethod(lambda *args, **kwargs: (geometry.blocked_cells(0, 72, 72), {})))
    route = CombatService._find_movement_path(location, 5, 5, 5, 6)
    assert route and route['cost'] == 1
    across = CombatService._find_movement_path(location, 5, 5, 6, 5)
    assert across and across['cost'] > 1
    assert all(geometry.can_traverse((*a, 0), (*b, 0)) for a, b in zip(across['path'], across['path'][1:]))


def test_movement_map_uses_circle_clearance_for_a_wall_by_default(app):
    from unittest.mock import patch
    from app.models import LocationObject
    location = SimpleNamespace(id=999, tiles_data=[])
    wall = SimpleNamespace(type='wall',tile_x=6,tile_y=6,properties={
        'offset_x':-.5,'offset_z':-.5,'rotation':-1.5707963267948966,
        'dimensions':{'width':2,'depth':.2,'height':2.4},
    })
    with patch.object(LocationObject,'query') as objects, patch.object(LocationCharacter,'query') as characters:
        objects.filter_by.return_value.all.return_value = [wall]
        characters.filter_by.return_value.all.return_value = []
        blocked, costs = CombatService._build_movement_map(location,floor_level=0)
    assert (5,5) in CombatService._object_footprint_tiles(wall)
    assert (5,5) not in blocked
    assert costs == {}


def test_server_preview_route_keeps_grapple_companion_constraints(app,monkeypatch):
    from unittest.mock import patch
    actor = SimpleNamespace(character_id=10,pos_x=1,pos_y=1,floor_level=0,grapple_target_id=2,
                            character=SimpleNamespace(data={}))
    captive = SimpleNamespace(character_id=11,pos_x=1,pos_y=2)
    location = SimpleNamespace(id=999,grid_width=6,grid_height=6,tiles_data=[])
    maps = []
    def movement_map(location,moving_character_id,ignored_character_ids,**kwargs):
        maps.append(ignored_character_ids)
        return {(3,2)}, {}
    monkeypatch.setattr(CombatService,'_build_movement_map',staticmethod(movement_map))
    with patch.object(LocationCharacter,'query') as characters:
        characters.filter_by.side_effect = lambda **filters: SimpleNamespace(first=lambda: actor if 'character_id' in filters else captive,all=lambda: [])
        route = CombatService._find_movement_path(location,1,1,3,1,moving_character_id=10)
    assert route is None
    assert maps == [[10,11]]


def test_fractional_route_passes_through_original_shifted_pripyat_door(monkeypatch):
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent/'fixtures/pripyat_wall_geometry.json').read_text(encoding='utf-8'))
    location = SimpleNamespace(grid_width=20,grid_height=10,tiles_data=[])
    geometry = CombatService._movement_wall_geometry(location,[SimpleNamespace(**o) for o in fixture['objects']])
    monkeypatch.setattr(CombatService,'_movement_wall_geometry',staticmethod(lambda *args: geometry))
    monkeypatch.setattr(CombatService,'_build_movement_map',staticmethod(lambda *args,**kwargs:(set(),{})))
    route = CombatService._find_movement_path(location,6.5,3.5,6.5,5.5)
    assert route is not None
    assert route['distance'] == pytest.approx(2)
    assert route['cost'] == pytest.approx(2)
    assert all(geometry.can_traverse((*a,0),(*b,0)) for a,b in zip(route['path'],route['path'][1:]))
    assert any(not float(x).is_integer() for x,y in route['path'])


def test_fractional_move_persists_coordinates_op_and_preview(client,create_user,auth_headers):
    from app.extensions import db
    from test_character_interactions import _setup_pair
    case = _setup_pair(client,create_user,auth_headers)
    actor = case['actor_location']
    actor.movement_points_current = 10
    db.session.commit()
    actor_id,location_id,lobby_id = actor.id,case['location'].id,case['lobby'].id
    _,cost,_ = CombatService.move_character(location_id,case['actor_user']['id'],case['actor_character'].id,
        1,1.25,movement_mode='walk')
    assert cost == .25
    db.session.expire_all()
    actor = db.session.get(LocationCharacter,actor_id)
    assert actor.pos_y == 1.25 and actor.movement_points_current == 9.75
    assert actor.movement_distance_this_turn == .25
    result = client.post(f'/lobbies/{lobby_id}/locations/{location_id}/movement-preview',
        headers=auth_headers(case['actor_user']),json={'character_id':actor.character_id,'x':1,'y':1.75,'floor_level':0})
    assert result.status_code == 200,result.get_json()
    assert result.get_json()['route']['distance'] == .5
    assert result.get_json()['route']['path'][0] == [1,1.25]


def test_moving_circle_cannot_pass_through_another_character():
    from app.services.movement_geometry import WallGeometry
    geometry = WallGeometry([])
    geometry.add_characters([SimpleNamespace(pos_x=1,pos_y=0,floor_level=0)])
    assert not geometry.can_traverse((0,0,0),(2,0,0))
    assert geometry.can_traverse((0,.6,0),(2,.6,0))
    assert geometry.can_traverse((0,0,1),(2,0,1))


def test_interaction_distance_uses_nearest_character_edges_with_fractional_positions():
    first = SimpleNamespace(id=1,pos_x=1,pos_y=1,floor_level=0)
    second = SimpleNamespace(id=2,pos_x=3,pos_y=1,floor_level=0)
    assert CombatService._character_distance(first,second) == 1.5
    assert not CombatService._is_adjacent(first,second)
    second.pos_x = 2.5
    assert CombatService._is_adjacent(first,second)
    second.floor_level = 1
    assert not CombatService._is_adjacent(first,second)


def test_rotated_obstacle_ray_does_not_use_its_axis_aligned_box():
    import math
    obj = SimpleNamespace(tile_x=4,tile_y=4,type='wall',properties={
        'rotation':math.pi/4,'offset_x':.5,'offset_z':.5,
        'dimensions':{'width':4,'depth':.2,'height':2.4},
    })
    a,b = SimpleNamespace(pos_x=1.5,pos_y=3.3),SimpleNamespace(pos_x=4,pos_y=3.3)
    assert CombatService._line_object_entry(a,b,obj) is None
    a.pos_y,b.pos_y,b.pos_x = 4.5,4.5,7.5
    assert CombatService._line_object_entry(a,b,obj) is not None


def test_fractional_migration_preserves_legacy_values_and_refuses_lossy_downgrade(client,create_user,auth_headers):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import sqlalchemy as sa
    from app.extensions import db
    from test_character_interactions import _setup_pair
    case = _setup_pair(client,create_user,auth_headers)
    actor_id = case['actor_location'].id
    file = Path(__file__).parents[1]/'migrations/versions/d7b3e5f9012a_fractional_location_positions.py'
    spec = importlib.util.spec_from_file_location('fractional_migration',file)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    db.session.remove()
    with db.engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()
        assert isinstance(next(c for c in sa.inspect(connection).get_columns('location_characters') if c['name']=='pos_x')['type'],sa.Integer)
        migration.upgrade()
        before = connection.execute(sa.text('SELECT pos_x,pos_y FROM location_characters WHERE id=:id'),{'id':actor_id}).one()
        assert tuple(before) == (1,1)
        connection.execute(sa.text('UPDATE location_characters SET pos_x=1.25,movement_points_current=2.5 WHERE id=:id'),{'id':actor_id})
        with pytest.raises(RuntimeError,match='дробные'):
            migration.downgrade()
        assert connection.execute(sa.text('SELECT pos_x FROM location_characters WHERE id=:id'),{'id':actor_id}).scalar_one() == 1.25


def test_all_saved_pripyat_stairs_have_bidirectional_physical_routes(client,create_user,auth_headers):
    import json,math
    from pathlib import Path
    from app.extensions import db
    from app.models import LocationObject
    from test_character_interactions import _setup_pair
    fixture_path = Path(__file__).parents[1]/'tmp/pripyat-regression.json'
    if not fixture_path.exists():
        pytest.skip('Локальная копия Припяти отсутствует; полный прогон лестниц требует её наличия')
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    case = _setup_pair(client,create_user,auth_headers)
    location = case['location']
    location.grid_width,location.grid_height = fixture['grid_width'],fixture['grid_height']
    location.tiles_data = fixture['tiles_data']
    for source in fixture['objects']:
        db.session.add(LocationObject(location_id=location.id,type=source['type'],name=source.get('name',source['type']),
            tile_x=source['tile_x'],tile_y=source['tile_y'],properties=source.get('properties') or {}))
    db.session.commit()
    result = []
    for index,source in enumerate(fixture['objects']):
        if source['type'] != 'stairs':
            continue
        p = source['properties']
        angle = p.get('rotation',0)
        x,y = source['tile_x']+p.get('offset_x',0),source['tile_y']+p.get('offset_z',0)
        level = p.get('level',0)
        half_depth = p['dimensions']['depth']/2
        low = (x-math.sin(angle)*(half_depth+.4),y-math.cos(angle)*(half_depth+.4),level)
        high = (x+math.sin(angle)*(half_depth+.4),y+math.cos(angle)*(half_depth+.4),level+1)
        up = CombatService._find_movement_path(location,*low[:2],*high[:2],start_level=low[2],end_level=high[2])
        down = CombatService._find_movement_path(location,*high[:2],*low[:2],start_level=high[2],end_level=low[2])
        result.append({'sourceIndex':index,'building':p.get('building_id'),'low':low,'high':high,
            'up':up,'down':down})
    assert len(result)==7
    assert all(row['up'] and row['down'] for row in result),[(row['sourceIndex'],bool(row['up']),bool(row['down'])) for row in result]


def test_pripyat_reported_street_tree_building_exit_and_drop(vertical_game):
    import json,time
    from pathlib import Path
    from app.extensions import db
    from app.models import LocationObject
    source_path=Path(__file__).parents[1]/'tmp/pripyat-regression.json'
    if not source_path.exists():
        pytest.skip('Нужна локальная копия Припяти')
    source=json.loads(source_path.read_text(encoding='utf-8'))
    case=vertical_game;location=case['location'];actor=case['actor_location']
    location.grid_width=source['grid_width'];location.grid_height=source['grid_height'];location.tiles_data=source['tiles_data']
    objects=[]
    for item in source['objects']:
        obj=LocationObject(location_id=location.id,**item);objects.append(obj);db.session.add(obj)
    db.session.commit()
    geometry=CombatService._movement_wall_geometry(location,objects)
    # Поваленное дерево из снимка 441: пересечение требует перелезания.
    assert geometry.climb_edge_events((31,46,0),(36,46,0))
    measurements=[]
    # Раньше конечные клетки возле края попадали в прямоугольник кроны.
    assert geometry.can_traverse((64,71,0),(64,71,0))
    assert not geometry.can_traverse((64.5,68.5,0),(64.5,68.5,0))
    for start,end in [((31,46,0),(36,46,0)),((64,29,0),(64,46,0)),
                      ((64,46,0),(64,71,0)),((64,46,0),(64,0,0)),
                      ((25,11,1),(25,3,0)),((25,7,0),(25,3,0))]:
        actor.pos_x,actor.pos_y,actor.floor_level=start;db.session.commit()
        t=time.perf_counter()
        route=CombatService._find_movement_path(location,*start[:2],*end[:2],actor.character_id,end_level=end[2])
        measurements.append(round((time.perf_counter()-t)*1000,2))
        assert route,(start,end)
        assert route['surfaces'][-1]['level']==end[2]
    actor.pos_x,actor.pos_y,actor.floor_level=27,6,1;db.session.commit()
    plan=CombatService._object_drop_plan(location,actor,objects[157])
    assert plan and plan['floor_level']==0
    assert plan['landing']['x']!=int(plan['landing']['x'])
    # Тот же план при выполнении: точка не округляется и не телепортируется.
    moved,_,_=CombatService.move_character(location.id,case['actor_user']['id'],actor.character_id,
        plan['landing']['x'],plan['landing']['y'],special_action='drop_down',object_id=objects[157].id,floor_level=0)
    assert moved.floor_level==0
    print('Припять: мс маршрутов',measurements)


def test_embedded_collision_uses_tile_origin_offsets_degrees_and_size():
    obj={'type':'wall','x':.3,'z':-.2,'rotation':90,'dimensions':{'width':3,'depth':.1,'height':2.4}}
    location=SimpleNamespace(tiles_data=[[{} for _ in range(5)] for _ in range(5)])
    location.tiles_data[3][4]={'objects':[obj]}
    geometry=CombatService._movement_wall_geometry(location,[])
    assert not geometry.can_traverse((3,3,0),(5,3,0))
    assert geometry.can_traverse((0,0,0),(1,0,0))
    box=geometry.boxes[0]
    assert (box.x,box.y)==pytest.approx((4.8,3.3))
    assert box.angle==pytest.approx(1.5707963267948966)


def test_drop_rechecks_closed_and_occupied_passage_before_payment(vertical_game,monkeypatch):
    from app.extensions import db
    from app.models import LocationObject
    case=vertical_game;actor=case['actor_location'];location=case['location']
    actor.pos_x,actor.pos_y,actor.floor_level=3,2,1
    floor=LocationObject(location_id=location.id,type='floor',tile_x=3,tile_y=3,
        properties={'level':1,'dimensions':{'width':6,'depth':6,'height':.1}})
    portal=LocationObject(location_id=location.id,type='hatch',tile_x=3,tile_y=3,
        properties={'level':1,'is_open':True,'dimensions':{'width':.9,'depth':.9,'height':.1}})
    db.session.add_all([floor,portal]);db.session.commit()
    plan=CombatService._object_drop_plan(location,actor,portal);assert plan
    landing=plan['landing'];before=(actor.action_points_current,actor.movement_points_current)
    monkeypatch.setattr(CombatService,'resolve_fall',staticmethod(lambda *a,**kw:pytest.fail('Недопустимый спуск не наносит урон')))
    def move():
        return CombatService.move_character(location.id,case['actor_user']['id'],actor.character_id,
            landing['x'],landing['y'],special_action='drop_down',object_id=portal.id,floor_level=0)
    portal.properties={**portal.properties,'is_open':False};db.session.commit()
    with pytest.raises(ValidationError,match='passage|landing'):
        move()
    portal.properties={**portal.properties,'is_open':True}
    blocker=case['target_location'];blocker.pos_x=landing['x'];blocker.pos_y=landing['y'];blocker.floor_level=0
    db.session.commit()
    with pytest.raises(ValidationError,match='passage|landing'):
        move()
    assert (actor.action_points_current,actor.movement_points_current)==before
    assert (actor.pos_x,actor.pos_y,actor.floor_level)==(3,2,1)


@pytest.mark.parametrize('kind',['tree','nature_oak_green','nature_oak_dead','nature_birch_dry','nature_pine_green'])
@pytest.mark.parametrize('angle',[0,.7853981633974483,1.5707963267948966])
def test_tree_crown_does_not_block_walk_but_trunk_still_does(kind,angle):
    import math
    tree=SimpleNamespace(type=kind,tile_x=5,tile_y=5,properties={'rotation':angle,'level':0,
        'dimensions':{'width':5.5,'depth':5,'height':8}})
    geometry=CombatService._movement_wall_geometry(SimpleNamespace(tiles_data=[]),[tree])
    def point(x,y):return (5+x*math.cos(angle)+y*math.sin(angle),5-x*math.sin(angle)+y*math.cos(angle),0)
    assert not geometry.can_traverse(point(-2,0),point(2,0))
    assert geometry.can_traverse(point(-2,1.5),point(2,1.5))
    assert geometry.can_traverse((3,5,1),(7,5,1))
