"""Согласованные правила гранат: геометрия, проверки и оплата."""
import math
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import LocationObject,LocationCombatState
from app.services.combat import CombatService
from app.services.exceptions import ValidationError


@pytest.fixture
def grenade_game(client,create_user,auth_headers):
    from test_character_interactions import _setup_pair
    case=_setup_pair(client,create_user,auth_headers)
    location,actor=case['location'],case['actor_location']
    location.grid_width,location.grid_height=30,30
    location.tiles_data=[[{'terrain':'grass','height':1} for _ in range(30)] for _ in range(30)]
    actor.pos_x,actor.pos_y=2.1,5.2
    actor.facing_x,actor.facing_y=1,0
    actor.action_points_current=20
    case['target_location'].pos_x,case['target_location'].pos_y=25,25
    data=deepcopy(actor.character.data)
    data['skills']={'physical':{'strength':{'base':10},'agility':{'base':10}},'other':{'tactics':{'base':10}}}
    data['health']={'current':700,'max':700,'painLevel':0,'stress':0,'effects':[]}
    data['inventory']={'pockets':[{'name':'Граната РГД-5','category':'grenade','quantity':2,'attributes':{}}]}
    actor.character.data=data
    db.session.commit()
    return case


def dice(monkeypatch,values):
    pending=iter(values);drawn=[]
    def roll(low,high):
        if (low,high)==(1,20):
            value=next(pending);drawn.append(value);return value
        return low
    monkeypatch.setattr('app.services.combat.random.randint',roll)
    return drawn


@pytest.mark.parametrize('protection,protected',[(0,False),(35,False),(35.1,True),(65,True)])
def test_burning_round_damage_extra_pain_stress_and_thermal_boundary(grenade_game,monkeypatch,protection,protected):
    from app.services.burning import ignite
    target=grenade_game['actor_location']
    monkeypatch.setattr(CombatService,'_target_elemental_protection',lambda *a:protection)
    ignite(target,5,source='Молотов')
    CombatService._apply_periodic_health_effects(target,phase='turn_end')
    assert target.character.data['health']['current']==700
    CombatService._apply_periodic_health_effects(target,phase='round_end')
    health=target.character.data['health']
    assert health['current']==(700 if protected else 650)
    assert health['painLevel']==(0 if protected else 3)
    assert health['stress']==(0 if protected else 1)
    fire=next(e for e in health['effects'] if e['type']=='burning')
    assert fire['remaining']==4


def test_direct_fire_duration_and_future_mixture_difficulty(grenade_game):
    from app.services.burning import ignite,extinguish_difficulty
    target=grenade_game['actor_location']
    ignite(target,8,source='Молотов',extinguish_difficulty_modifier=4)
    ignite(target,5,source='Молотов',extinguish_difficulty_modifier=4)
    target.character.data['health']['stress']=2
    assert extinguish_difficulty(target.character.data)==18
    assert next(e for e in target.character.data['health']['effects'] if e['type']=='burning')['remaining']==8


def test_burning_ticks_once_at_end_of_entire_round(grenade_game):
    from app.services.burning import ignite
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    ignite(actor,8,source='Молотов');db.session.commit()
    CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,actor.id)
    assert actor.character.data['health']['current']==700
    assert next(e for e in actor.character.data['health']['effects'] if e['type']=='burning')['remaining']==8
    CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,target.id)
    health=actor.character.data['health']
    assert health['current']==650
    assert health['painLevel']==3
    assert health['stress']==1
    assert next(e for e in health['effects'] if e['type']=='burning')['remaining']==7


@pytest.mark.parametrize('roll,success',[(15,False),(16,True)])
def test_self_extinguish_is_raw_roll_with_stress_and_mixture_modifier(grenade_game,monkeypatch,roll,success):
    from app.services.burning import ignite
    actor=grenade_game['actor_location']
    actor.character.data['health']['stress']=1
    ignite(actor,8,source='Смесь',extinguish_difficulty_modifier=3)
    db.session.commit();dice(monkeypatch,[roll])
    result=CombatService.perform_action(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id,'extinguish_fire')
    check=result['narrative_action']['check']
    assert check=={'roll':roll,'total':roll,'difficulty':16,'success':success}
    assert actor.action_points_current==17
    assert any(e['type']=='burning' for e in actor.character.data['health']['effects']) is not success


def test_prepare_water_then_pour_consumes_container_and_reduces_burning(grenade_game):
    from app.services.burning import ignite
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'id':'water-test','name':'Вода','volume':2,'quantity':2,'maxUses':3,'uses':3,'attributes':{'uses':3}}]
    actor.character.data=data;ignite(actor,8,source='Молотов');db.session.commit()
    def action(key,**kw):
        return CombatService.perform_action(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id,key,**kw)
    with pytest.raises(ValidationError,match='Сначала возьмите'):
        action('pour_water')
    initial=actor.action_points_current
    action('prepare_water',item_path=['inventory','pockets',0])
    assert actor.action_points_current==initial-1
    before=actor.action_points_current
    assert actor.character.data['health']['combatMeta']['heldWaterItem']['litres']==1
    action('pour_water')
    assert actor.action_points_current==before-2
    assert actor.character.data['inventory']['pockets'][0]['quantity']==1
    assert 'heldWaterItem' not in actor.character.data['health']['combatMeta']
    assert next(e for e in actor.character.data['health']['effects'] if e['type']=='burning')['remaining']==6


@pytest.mark.parametrize('storage,cost',[('pockets',1),('backpack',2),('case',4)])
def test_water_retrieval_uses_only_inventory_access_cost(grenade_game,storage,cost):
    from app.services.burning import extinguishing_options
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    water={'id':'one-litre','name':'Вода','category':'consumable','volume':2,'quantity':1,'maxUses':3,'uses':3}
    if storage=='case':
        data['inventory']={'pockets':[{'name':'Кейс','category':'case','attributes':{'access_action_points':3},'contents':[water]}]}
        path=['inventory','pockets',0,'contents',0]
    else:
        data['inventory']={storage:[water]};path=['inventory',storage,0]
    actor.character.data=data;db.session.commit()
    option=extinguishing_options(actor)['water'][0]
    assert option['action_points']==cost and option['litres']==1
    before=actor.action_points_current
    CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'prepare_water',item_path=path)
    assert actor.action_points_current==before-cost


def test_water_remaining_uses_and_explicit_container_capacity(grenade_game):
    from app.services.burning import water_item
    data=deepcopy(grenade_game['actor_location'].character.data)
    data['inventory']['pockets']=[{'name':'Вода','volume':2,'uses':1,'maxUses':3},
        {'name':'Вода в канистре','volume':4,'uses':2,'maxUses':4,'attributes':{'water_litres':10,'consumable':{'direct':{'water_source':True}}}}]
    assert water_item(data,['inventory','pockets',0])[1]==pytest.approx(1/3)
    assert water_item(data,['inventory','pockets',1])[1]==5
    del data['inventory']['pockets'][1]['attributes']['water_litres']
    with pytest.raises(ValidationError,match='объём воды'):
        water_item(data,['inventory','pockets',1])


def test_water_options_exclude_distant_and_other_floor_targets_and_dry_food(client,grenade_game,auth_headers):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'name':'Сухой паёк','attributes':{'consumable':{'direct':{'satisfy_water':True}}}},
        {'name':'Ампула','attributes':{'consumable':{'direct':{'satisfy_water':True,'application_form':'injectable'}}}}]
    actor.character.data=data;db.session.commit()
    url=f'/lobbies/{grenade_game["lobby"].id}/locations/{actor.location_id}/extinguishing-options/{actor.id}'
    response=client.get(url,headers=auth_headers(grenade_game['actor_user']))
    assert response.status_code==200,response.json
    assert response.json['target_ids']==[actor.character_id]
    assert response.json['water']==[]
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y;db.session.commit()
    assert target.character_id in client.get(url,headers=auth_headers(grenade_game['actor_user'])).json['target_ids']
    target.floor_level=1;db.session.commit()
    assert target.character_id not in client.get(url,headers=auth_headers(grenade_game['actor_user'])).json['target_ids']
    actor.character.visible_to=[];actor.character.editable_to=[];db.session.commit()
    assert client.get(url,headers=auth_headers(grenade_game['target_user'])).status_code==403


@pytest.mark.parametrize('invalidation',['moved_target','replaced_water'])
def test_paid_pouring_is_cancelled_before_next_payment_if_conditions_disappear(grenade_game,invalidation):
    from app.models import DeferredCombatAction
    from app.services.burning import ignite
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'id':'water-paid','name':'Вода','volume':2,'quantity':1,'attributes':{}}]
    actor.character.data=data;ignite(target,8,source='Молотов');db.session.commit()
    CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'prepare_water',item_path=['inventory','pockets',0])
    actor.action_points_current=1;db.session.commit()
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'pour_water',
        target_character_id=target.character_id,pending_action_id='paid-pour')
    assert result['pending_action'] is True
    if invalidation=='moved_target':
        target.pos_x+=5
    else:
        data=deepcopy(actor.character.data);data['inventory']['pockets'][0]['id']='replacement';actor.character.data=data
    db.session.commit()
    CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,target.id)
    row=db.session.get(DeferredCombatAction,'paid-pour')
    assert row.status=='cancelled'
    assert actor.action_points_current==5
    assert actor.character.data['inventory']['pockets'][0]['quantity']==1


def test_thermal_protection_keeps_accuracy_penalty_and_armor_damage(grenade_game):
    from app.services.burning import ignite,tick
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['equipment']={'armor':{'name':'Термоброня','protection':{'thermal':.4},'stageDurability':1000,'currentStageDurability':1000,'attributes':{}}}
    actor.character.data=data;ignite(actor,8,source='Молотов');ignite(actor,12,source='Napalm-AN')
    before=CombatService._health_roll_modifier(actor.character.data,'skills.physical.shooting')
    tick(actor,1)
    health=actor.character.data['health']
    assert health['current']==700 and health['painLevel']==0 and health['stress']==0
    assert actor.character.data['equipment']['armor']['currentStageDurability']==950
    assert before==-3
    assert CombatService._health_roll_modifier(actor.character.data,'skills.physical.shooting')==-3


def test_entering_water_extinguishes_but_bridge_does_not(grenade_game,monkeypatch):
    from app.services.burning import ignite,extinguish_in_water
    actor=grenade_game['actor_location'];location=grenade_game['location']
    tiles=deepcopy(location.tiles_data);tiles[5][2]['terrain']='water';location.tiles_data=tiles
    monkeypatch.setattr(CombatService,'physical_position',lambda _: {'height':3.4})
    ignite(actor,8,source='Молотов')
    assert not extinguish_in_water(actor)
    assert any(e['type']=='burning' for e in actor.character.data['health']['effects'])
    monkeypatch.setattr(CombatService,'physical_position',lambda _: {'height':1})
    assert extinguish_in_water(actor)
    assert not any(e['type']=='burning' for e in actor.character.data['health']['effects'])


@pytest.mark.parametrize('kind',['door','window'])
def test_cloud_crosses_only_open_passages_and_refreshes_after_edit(grenade_game,kind):
    from app.services.cloud_geometry import prepare
    location=grenade_game['location']
    wall=LocationObject(location_id=location.id,type='wall',tile_x=5,tile_y=6,properties={
        'dimensions':{'width':20,'depth':.2,'height':2.4}})
    passage=LocationObject(location_id=location.id,type=kind,tile_x=5,tile_y=6,properties={
        'dimensions':{'width':1.5,'depth':.2,'height':1.2 if kind=='window' else 2},
        'elevation':.9 if kind=='window' else 0,'is_open':False})
    db.session.add_all([wall,passage]);db.session.commit()
    area={'type':'gas','x':5,'y':4,'floor_level':0,'radius':5}
    prepare(location,[wall,passage],area)
    assert [5,8,0] not in area['cells']
    passage.properties={**passage.properties,'is_open':True};db.session.commit()
    prepare(location,[wall,passage],area)
    assert [5,8,0] in area['cells']
    signature=deepcopy(area['cells_signature']);prepare(location,[wall,passage],area)
    assert area['cells_signature']==signature


def test_cloud_reaches_another_floor_only_through_opening(grenade_game):
    from app.services.cloud_geometry import prepare
    location=grenade_game['location']
    slab=LocationObject(location_id=location.id,type='floor',tile_x=5,tile_y=5,properties={
        'level':1,'dimensions':{'width':8,'depth':8,'height':.15}})
    db.session.add(slab);db.session.commit()
    area={'type':'gas','x':5,'y':5,'floor_level':0,'radius':5}
    prepare(location,[slab],area)
    assert [5,5,1] not in area['cells']
    hole=LocationObject(location_id=location.id,type='floor_opening',tile_x=5,tile_y=5,properties={
        'level':1,'dimensions':{'width':2,'depth':2}})
    db.session.add(hole);db.session.commit()
    prepare(location,[slab,hole],area)
    assert [5,5,1] in area['cells']


def test_additional_fragment_below_eleven_damage_still_causes_trauma(grenade_game,monkeypatch):
    actor=grenade_game['actor_location']
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:3 if b==20 else 1)
    CombatService._apply_attack_damage(actor,4,'left_arm',{},force_trauma=True)
    assert any(e['type']=='fracture' and e['area']=='leftArm' for e in actor.character.data['health']['effects'])


def test_flash_is_blocked_by_closed_wall_but_passes_open_door(grenade_game):
    actor=grenade_game['actor_location'];actor.pos_x,actor.pos_y=5,8;actor.facing_y=-1;actor.facing_x=0
    wall=LocationObject(location_id=actor.location_id,type='wall',tile_x=5,tile_y=6,properties={
        'dimensions':{'width':20,'depth':.2,'height':2.4}})
    door=LocationObject(location_id=actor.location_id,type='door',tile_x=5,tile_y=6,properties={
        'dimensions':{'width':1.5,'depth':.2,'height':2.1},'is_open':False})
    db.session.add_all([wall,door]);db.session.commit()
    def flash():
        results=CombatService._resolve_flash(actor.location_id,5,4,{'radius':10,'blindness':250,'noise':0})
        return next(r for r in results if r['character_id']==actor.character_id)
    assert flash()['blindness']==0
    door.properties={**door.properties,'is_open':True};db.session.commit()
    assert flash()['blindness']>0


@pytest.mark.parametrize('damage,band',[(150,0),(151,1),(300,1),(301,2),(450,2),(451,3)])
@pytest.mark.parametrize('roll',[1,2,3,4,5,6])
def test_blast_trauma_bands_and_table_entries(grenade_game,monkeypatch,damage,band,roll):
    target=grenade_game['actor_location']
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:roll)
    result=CombatService._apply_blast_trauma(target,damage)
    assert result['damage_band']==(150,300,450,700)[band]
    expected='hearing_damage' if roll==1 else 'concussion' if roll in (2,3) else 'bleeding' if roll==4 else 'fracture' if roll==5 and band else 'none'
    assert result['type']==expected
    if roll==1:
        CombatService._apply_periodic_health_effects(target,phase='turn_end')
        effect=next(e for e in target.character.data['health']['effects'] if e['type']=='deafness')
        assert effect['value']==(15,30,50,80)[band]
        assert effect['remaining']==(1,2,4,6)[band]
    if roll in (2,3):
        duration=(1,3,3,5)[band]
        CombatService._apply_periodic_health_effects(target,phase='turn_end')
        if band==0:
            assert not any(e['type']=='concussion' for e in target.character.data['health']['effects'])
            return
        assert next(e for e in target.character.data['health']['effects'] if e['type']=='concussion')['remaining']==duration
        CombatService._apply_periodic_health_effects(target,phase='round_end')
        effects=[e for e in target.character.data['health']['effects'] if e['type']=='concussion']
        assert (effects[0]['remaining'] if effects else 0)==duration-1


def throw(case,**kwargs):
    return CombatService.perform_action(case['location'].id,case['actor_user']['id'],case['actor_location'].id,
        'explosive_attack',target_x=5.35,target_y=5.2,item_path=['inventory','pockets',0],explosive_source='hand',**kwargs)


@pytest.mark.parametrize('name,roll,original,expected',[
    ('Граната РГД-5',12,'round','round'),('Граната РГД-5',13,'round','turn_end'),
    ('Граната РГН',12,'turn_end','turn_end'),('Граната РГН',13,'turn_end','instant'),
])
def test_delay_requires_tactics_and_always_costs_two_extra_ap(grenade_game,monkeypatch,name,roll,original,expected):
    actor=grenade_game['actor_location']
    data=deepcopy(actor.character.data);data['inventory']['pockets'][0]['name']=name;actor.character.data=data
    db.session.commit()
    drawn=dice(monkeypatch,[roll,20])
    monkeypatch.setattr(CombatService,'resolve_explosion',lambda *a,**kw:{'targets':[],'objects':[]})
    result=throw(grenade_game,explosive_fuse_mode='delay')['explosive']
    assert drawn==[roll,20]
    assert result['delay_check']['success']==(roll>=13)
    assert result['original_fuse']==original
    assert result['profile']['fuse']==expected
    assert result['action_points']==5
    held=((actor.character.data['health'].get('combatMeta') or {}).get('grenadeReaction') or {}).get('held_action_points',0)
    assert actor.action_points_current+held==15
    assert actor.character.data['inventory']['pockets'][0]['quantity']==1


def test_unpaid_delay_does_not_roll_or_consume_grenade(grenade_game,monkeypatch):
    actor=grenade_game['actor_location'];actor.action_points_current=4;db.session.commit()
    drawn=dice(monkeypatch,[])
    with pytest.raises(ValidationError,match='Not enough action points'):
        throw(grenade_game,explosive_fuse_mode='delay')
    assert drawn==[]
    assert actor.action_points_current==4
    assert actor.character.data['inventory']['pockets'][0]['quantity']==2


@pytest.mark.parametrize('roll,tactics,expected',[(1,30,True),(20,0,False)])
def test_throw_has_no_automatic_outcome_for_natural_one_or_twenty(grenade_game,monkeypatch,roll,tactics,expected):
    actor=grenade_game['actor_location']
    data=deepcopy(actor.character.data);data['skills']['other']['tactics']['base']=tactics;actor.character.data=data
    if roll==20:
        data['skills']['physical']['strength']['base']=0
        data['skills']['physical']['agility']['base']=0
    db.session.commit();dice(monkeypatch,[roll]);monkeypatch.setattr('app.services.combat.random.random',lambda:0)
    result=throw(grenade_game)['explosive']
    assert result['success'] is expected
    assert result['target']=={'x':5.35,'y':5.2}
    if expected:assert result['impact']==result['target']


@pytest.mark.parametrize('angle',[.03,.13,.28,.62,.87])
def test_scatter_keeps_exact_direction_length_and_ten_metre_cap(monkeypatch,angle):
    monkeypatch.setattr('app.services.combat.random.random',lambda:angle)
    for failure in (.35,2.75,10,99):
        x,y=CombatService._scatter_point(20.1,20.2,failure,50,50)
        distance=min(failure,10)
        assert (x,y)==pytest.approx((20.1+distance*math.cos(angle*math.tau),20.2+distance*math.sin(angle*math.tau)))


def test_scatter_stops_at_map_edge_along_original_ray(monkeypatch):
    monkeypatch.setattr('app.services.combat.random.random',lambda:.125)
    assert CombatService._scatter_point(8,5,10,10,10)==pytest.approx((9,6))


def test_scatter_stops_at_nearest_wall_on_same_floor(grenade_game,monkeypatch):
    location=grenade_game['location']
    for x,level in ((7,1),(9,0),(8,0)):
        db.session.add(LocationObject(location_id=location.id,type='wall',tile_x=x,tile_y=5,properties={
            'level':level,'dimensions':{'width':.2,'depth':3,'height':2},'traversal_category':'blocked'}))
    db.session.commit();monkeypatch.setattr('app.services.combat.random.random',lambda:0)
    assert CombatService._scatter_point(5.25,5.2,10,30,30,location=location)==pytest.approx((7.9,5.2))


def test_scatter_passes_open_door_and_stops_at_closed_door(grenade_game,monkeypatch):
    location=grenade_game['location']
    wall=LocationObject(location_id=location.id,type='wall',tile_x=5,tile_y=6,properties={
        'dimensions':{'width':4,'depth':.2,'height':2.4},'traversal_category':'blocked'})
    door=LocationObject(location_id=location.id,type='door',tile_x=5,tile_y=6,properties={
        'dimensions':{'width':1,'depth':.2,'height':2},'is_open':True})
    db.session.add_all([wall,door]);db.session.commit()
    monkeypatch.setattr('app.services.combat.random.random',lambda:.25)
    assert CombatService._scatter_point(5.1,3.2,10,30,30,location=location)==pytest.approx((5.1,13.2))
    door.properties={**door.properties,'is_open':False};db.session.commit()
    assert CombatService._scatter_point(5.1,3.2,10,30,30,location=location)==pytest.approx((5.1,5.9))


def test_projectile_range_does_not_round_fractional_impact():
    assert CombatService._clamp_projectile_point(1.1,2.2,4.35,6.7,0)==(4.35,6.7,False)
    x,y,airburst=CombatService._clamp_projectile_point(1.1,2.2,20.4,19.8,5)
    assert airburst and math.hypot(x-1.1,y-2.2)==pytest.approx(5)


@pytest.mark.parametrize('protection,armor,expected',[(20,32,75),(35,32,56),(50,0,0),(0,35,75),(0,50,0)])
def test_fragments_use_bullet_damage_formula_through_cover_and_armor(grenade_game,monkeypatch,protection,armor,expected):
    target=grenade_game['actor_location'];hits=[]
    monkeypatch.setattr(CombatService,'_target_armor',lambda *a,**kw:(armor,[]))
    monkeypatch.setattr(CombatService,'_apply_attack_damage',lambda target,damage,*a,**kw:hits.append(damage) or {})
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:a)
    cover={'protection':protection,'zones':{'chest':{'physical_protection':protection}}} if protection else {'protection':0}
    result=CombatService._explosion_fragment_hit(target,100,30,'chest',cover,1)
    assert result['damage']==expected
    assert hits==([expected] if expected else [])


def test_additional_fragment_checks_armor_of_its_own_zone(grenade_game,monkeypatch):
    target=grenade_game['actor_location']
    monkeypatch.setattr(CombatService,'_target_armor',lambda data,zone,**kw:(100 if zone=='head' else 0,[]))
    monkeypatch.setattr(CombatService,'_apply_attack_damage',lambda *a,**kw:{})
    main=CombatService._explosion_fragment_hit(target,100,20,'head',{},1)
    extra=CombatService._explosion_fragment_hit(target,16,20,'chest',{},1,additional=True)
    blocked=CombatService._explosion_fragment_hit(target,16,20,'head',{},1,additional=True)
    assert main['damage']==0 and extra['damage']==16 and blocked['damage']==0


def quiet_explosion(monkeypatch):
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:6 if (a,b)==(1,6) else 100 if b==100 else a)
    monkeypatch.setattr(CombatService,'_grenade_fragment_zone',lambda:'chest')


def profile(**kwargs):
    return {'key':'test','name':'Тест','blast_base':0,'blast_falloff':0,'fragment':0,'radius':5,'penetration':30,**kwargs}


def test_liquid_crystal_shields_wave_and_fragments_from_same_explosion(grenade_game,monkeypatch):
    from app.services.equipment_modifications import make_record
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['equipment']={'armor':{'id':'explosion-crystal','name':'Тяжёлая броня','category':'armor',
        'durability':100,'stageDurability':1000,'currentStageDurability':1000,
        'protection':{'physical':10},'modifications':[make_record('armor-a39')]}}
    actor.character.data=data;db.session.commit()
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x-1,actor.pos_y,
        profile(blast_base=100,fragment=40,penetration=0))
    entry=next(row for row in result['targets'] if row['character_id']==actor.character_id)
    assert entry['armor']==30 and entry['blast_armor']==30
    assert entry['fragment_damage']==0 and entry['blast_damage']==0
    assert actor.character.data['equipment']['armor']['liquidCrystalState']['charged'] is False
    later=CombatService.resolve_explosion(actor.location_id,actor.pos_x-1,actor.pos_y,
        profile(blast_base=100,fragment=40,penetration=0))
    after=next(row for row in later['targets'] if row['character_id']==actor.character_id)
    assert after['blast_armor']==10 and after['blast_damage']>0


@pytest.mark.parametrize('damage,shock',[(0,False),(1,True)])
def test_close_explosion_requires_actual_positive_damage_for_shock(grenade_game,monkeypatch,damage,shock):
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location']
    result=CombatService.resolve_explosion(grenade_game['location'].id,actor.pos_x-.5,actor.pos_y,profile(blast_base=damage))
    entry=next(r for r in result['targets'] if r['character_id']==actor.character_id)
    assert entry['blast_damage']==damage and entry['pain_shock'] is shock
    assert entry['extra_fragment_traumas']==[],'Граната без осколков не создаёт дополнительные осколки'


@pytest.mark.parametrize('direction,plate_name,protection,blast_damage',[
    (1,'Передняя плита',35,65),(-1,'Задняя плита',50,50),
])
def test_explosion_uses_plate_facing_epicenter(grenade_game,monkeypatch,direction,plate_name,protection,blast_damage):
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location']
    data=deepcopy(actor.character.data)
    def pouch(name,value):return {'attributes':{'slots':[{'type':'armor_plate'}]},'installedModules':[
        {'name':name,'category':'armor_plate','slotType':'armor_plate','durability':20,
         'stage':1,'stageDurability':1000,'currentStageDurability':1000,
         'attributes':{'protection':{'physical':value},'max_durability':20}}]}
    data['equipment']={'vest':{'pouches':[pouch('Передняя плита',35),pouch('Задняя плита',50)]}}
    actor.character.data=data;db.session.commit()
    result=CombatService.resolve_explosion(grenade_game['location'].id,actor.pos_x+direction,actor.pos_y,
        profile(blast_base=100,fragment=100))
    entry=next(row for row in result['targets'] if row['character_id']==actor.character_id)
    assert entry['blast_armor']==protection
    assert entry['armor']==protection
    assert entry['blast_damage']==blast_damage
    assert entry['fragment_zone']=='chest'
    assert [row['name'] for row in entry['blast_armor_damage']]==[plate_name]
    assert [row['name'] for row in entry['fragment_armor_damage']]==[plate_name]
    plates=[pouch['installedModules'][0] for pouch in actor.character.data['equipment']['vest']['pouches']]
    assert all((plate['currentStageDurability']<1000)==(plate['name']==plate_name) for plate in plates)


def test_full_cover_blocks_wave_but_passes_penetrating_fragments(grenade_game,monkeypatch):
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location']
    monkeypatch.setattr(CombatService,'_explosion_cover',lambda *a,**kw:{'grade':'full','protection':20,'objects':[],
        'zones':{zone:{'physical_protection':20,'class':'medium'} for zone in ('head','chest','abdomen','left_arm','right_arm','left_leg','right_leg')}})
    result=CombatService.resolve_explosion(grenade_game['location'].id,actor.pos_x-1,actor.pos_y,profile(blast_base=100,fragment=100,penetration=40))
    entry=next(r for r in result['targets'] if r['character_id']==actor.character_id)
    assert entry['blast_damage']==0 and entry['fragment_damage']==90
    assert entry['concussion'] is False


@pytest.mark.parametrize('indoors,damage,chance',[(False,90,120),(True,140,270)])
def test_indoor_explosion_increases_base_wave_and_base_concussion(grenade_game,monkeypatch,indoors,damage,chance):
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location']
    if indoors:
        db.session.add(LocationObject(location_id=grenade_game['location'].id,type='floor',tile_x=2,tile_y=5,
            properties={'building_id':'room','level':0,'dimensions':{'width':8,'depth':8,'height':.1}}))
        db.session.commit()
    result=CombatService.resolve_explosion(grenade_game['location'].id,actor.pos_x-1,actor.pos_y,profile(blast_base=100,blast_falloff=10))
    entry=next(r for r in result['targets'] if r['character_id']==actor.character_id)
    assert entry['blast_damage']==damage and entry['concussion_chance']==chance


def test_live_body_still_shields_rear_target_when_killed_by_same_blast(grenade_game,monkeypatch):
    quiet_explosion(monkeypatch)
    front,rear=grenade_game['actor_location'],grenade_game['target_location']
    front.pos_x,front.pos_y=3,5;rear.pos_x,rear.pos_y=4,5;db.session.commit()
    result=CombatService.resolve_explosion(grenade_game['location'].id,2,5,profile(blast_base=1000,fragment=100))
    entries={r['character_id']:r for r in result['targets']}
    assert entries[front.character_id]['blast_damage']==1000
    assert entries[rear.character_id]['blast_damage']==entries[rear.character_id]['fragment_damage']==0


def test_base_and_additional_concussion_durations_add(grenade_game,monkeypatch):
    actor=grenade_game['actor_location']
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:2 if b==6 else 1)
    result=CombatService.resolve_explosion(grenade_game['location'].id,actor.pos_x-2,actor.pos_y,profile(blast_base=200))
    entry=next(r for r in result['targets'] if r['character_id']==actor.character_id)
    assert entry['concussion'] and entry['blast_trauma']['rounds']==3
    effects=[e for e in actor.character.data['health']['effects'] if e['type']=='concussion']
    assert effects and all(e['remaining']==8 for e in effects)


def short_fuse(case):
    from app.services.grenade_reaction import prepare_event
    actor=case['actor_location']
    state=LocationCombatState.query.filter_by(location_id=case['location'].id).one()
    event={'id':'short-test','item_name':'Тест','x':5.1,'y':5.2,'actor_id':actor.id,'floor_level':0,
        'profile':profile(blast_base=10),'round':state.round_number}
    prepare_event(state,event);state.pending_explosives=[event]
    actor.action_points_current=5;actor.free_actions_current=1;actor.movement_points_current=8
    db.session.commit()
    return state


def test_short_fuse_reserves_existing_resources_and_keeps_same_turn(grenade_game,monkeypatch):
    from app.services.grenade_reaction import begin,window
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location'];state=short_fuse(grenade_game)
    begin(actor,state);db.session.commit()
    assert actor.action_points_current==2 and actor.movement_points_current==4
    CombatService.perform_action(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id,
        'narrative_action',narrative_action_name='Реакция',action_points=1,narrative_roll_required=False)
    CombatService.move_character(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.character_id,3.1,5.2,movement_mode='walk')
    db.session.expire_all()
    assert window(actor)['held_action_points']==3 and window(actor)['held_movement_points']==4
    snapshot=CombatService.get_state(grenade_game['location'].id,grenade_game['actor_user']['id'])
    assert snapshot['current_character']['grenade_reaction']
    result=CombatService.end_turn(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id)
    assert result['grenade_reaction_finished'] and state.current_location_character_id==actor.id
    assert actor.action_points_current==4 and actor.movement_points_current==7
    assert not window(actor)
    assert len([t for d in result['detonations'] for t in d['explosion']['targets'] if t['character_id']==actor.character_id])==1


def test_movement_conversion_splits_points_without_creating_more(grenade_game,monkeypatch):
    from app.services.grenade_reaction import begin,window
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location'];state=short_fuse(grenade_game)
    actor.movement_points_current=0;db.session.commit();begin(actor,state)
    CombatService.perform_action(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id,'convert_free_action_to_movement')
    assert actor.movement_points_current==5 and window(actor)['held_movement_points']==5
    CombatService.move_character(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.character_id,3.1,5.2,movement_mode='walk')
    CombatService.end_turn(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id)
    assert actor.movement_points_current==9 and actor.free_actions_current==0


def test_each_victim_gets_own_reaction_before_own_damage(grenade_game,monkeypatch):
    from app.services.grenade_reaction import begin,window
    quiet_explosion(monkeypatch)
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(target.character.data);data['health']={'current':700,'max':700,'effects':[]};target.character.data=data
    target.pos_x,target.pos_y=7.1,5.2;db.session.commit()
    before=target.character.data['health']['current']
    state=short_fuse(grenade_game);begin(actor,state)
    CombatService.end_turn(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id)
    assert target.character.data['health']['current']==before
    CombatService.end_turn(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id)
    assert state.current_location_character_id==target.id and window(target)
    assert target.character.data['health']['current']==before and target.action_points_current==2
    result=CombatService.end_turn(grenade_game['location'].id,grenade_game['actor_user']['id'],target.id)
    assert result['grenade_reaction_finished'] and target.character.data['health']['current']==before-10
    assert not state.pending_explosives


def test_round_grenade_waits_for_throwers_next_turn(grenade_game,monkeypatch):
    state=LocationCombatState.query.filter_by(location_id=grenade_game['location'].id).one()
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    state.round_number=2
    state.pending_explosives=[{'id':'round-test','actor_id':actor.id,'trigger':'actor_turn_start','round':2}]
    calls=[]
    monkeypatch.setattr(CombatService,'_activate_explosive_event',lambda *a:calls.append(a) or {'detonated':True})
    assert CombatService._process_pending_explosives(state,phase='round_start')==[]
    assert CombatService._process_pending_explosives(state,phase='actor_turn_start',actor_id=target.id)==[]
    assert CombatService._process_pending_explosives(state,phase='actor_turn_start',actor_id=actor.id)==[{'detonated':True}]
    assert len(calls)==1 and not state.pending_explosives


def test_pending_action_payment_waits_until_after_grenade_reaction(grenade_game,monkeypatch):
    from app.models import DeferredCombatAction
    from app.services.deferred_action import remember_action
    from app.services.grenade_reaction import window
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location'];state=short_fuse(grenade_game)
    pending={'id':'after-grenade','label':'Долгое действие','total_action_points':6,'remaining_action_points':4}
    data=deepcopy(actor.character.data);data['health'].setdefault('combatMeta',{})['pendingAction']=pending
    actor.character.data=data
    remember_action(actor,pending,{'location_character_id':actor.id,'action_key':'narrative_action',
        'narrative_action_name':'Долгое действие','action_points':6,'narrative_roll_required':False})
    db.session.commit();CombatService._prepare_character_for_turn(actor);db.session.commit()
    assert actor.action_points_current==2 and window(actor)['held_action_points']==3
    row=db.session.get(DeferredCombatAction,pending['id'])
    assert row.remaining_action_points==4 and row.status=='paying'
    CombatService.perform_action(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id,
        'narrative_action',narrative_action_name='Реакция',action_points=1,narrative_roll_required=False)
    CombatService.end_turn(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id)
    assert row.remaining_action_points==0 and row.status=='ready' and actor.action_points_current==0,{
        'status':row.status,'meta':actor.character.data['health'].get('combatMeta'),'ap':actor.action_points_current}
    result=CombatService.perform_action(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id,
        'narrative_action',resume_pending_action_id=row.id)
    assert result['narrative_action']['name']=='Долгое действие'
    assert row.status=='completed' and actor.action_points_current==0


@pytest.mark.parametrize('roll,success',[(14,False),(15,True)])
def test_concussion_recovery_costs_two_ap_and_uses_penalized_will(grenade_game,monkeypatch,roll,success):
    from app.services.effects import apply_effect_to_health
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['skills']['physical']['will']={'base':10}
    apply_effect_to_health(data['health'],{'type':'generic','name':'Контузия','remaining':5,'tick':'round_end','source':'old_save'})
    apply_effect_to_health(data['health'],{'type':'concussion','remaining':3,'tick':'round_end','source':'blast_wave'})
    actor.character.data=data;actor.action_points_current=5;db.session.commit()
    CombatService._sync_location_effects_from_character(actor)
    assert CombatService._health_roll_modifier(data,'skills.physical.will')==-3
    assert len([e for e in data['health']['effects'] if e['type']=='concussion'])==1
    dice(monkeypatch,[roll])
    result=CombatService.perform_action(grenade_game['location'].id,grenade_game['actor_user']['id'],actor.id,'recover_concussion')
    assert result['narrative_action']['check']['success'] is success
    assert actor.action_points_current==3
    assert any(e['type']=='concussion' for e in actor.character.data['health']['effects']) is not success
    assert any(e['type']=='concussion' for e in result['character']['effects']) is not success


def test_real_full_wall_blocks_wave_even_when_fragments_penetrate(grenade_game,monkeypatch):
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location'];actor.pos_x,actor.pos_y=6,5
    db.session.add(LocationObject(location_id=actor.location_id,type='wall',tile_x=4,tile_y=5,properties={
        'cover_physical_protection':20,'cover_base_physical_protection':20,'level':0,
        'dimensions':{'width':.2,'depth':6,'height':3}}));db.session.commit()
    cover=CombatService._explosion_cover(actor.location_id,2,5,actor)
    assert cover['grade']=='full' and cover['protection']==20
    result=CombatService.resolve_explosion(actor.location_id,2,5,profile(blast_base=100,fragment=100,penetration=100))
    entry=next(t for t in result['targets'] if t['character_id']==actor.character_id)
    assert entry['blast_damage']==0 and entry['fragment_damage']==60


def test_explosion_does_not_damage_character_through_intact_floor(grenade_game,monkeypatch):
    quiet_explosion(monkeypatch)
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(target.character.data);data['health']={'current':700,'max':700,'effects':[]};target.character.data=data
    target.pos_x,target.pos_y=actor.pos_x,actor.pos_y;target.floor_level=1
    db.session.add(LocationObject(location_id=actor.location_id,type='floor',tile_x=2,tile_y=5,
        properties={'level':1,'building_id':'intact','dimensions':{'width':6,'depth':6,'height':.2}}))
    db.session.commit();before=deepcopy(target.character.data)
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x,actor.pos_y,profile(blast_base=100))
    entry=next(t for t in result['targets'] if t['character_id']==target.character_id)
    assert entry['blast_damage']==entry['fragment_damage']==0
    assert target.character.data['health']['current']==before['health']['current']
    assert target.character.data['health']['effects']==before['health']['effects']


def test_explosion_passes_open_floor_and_counts_vertical_distance(grenade_game,monkeypatch):
    quiet_explosion(monkeypatch)
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y,target.floor_level=actor.pos_x,actor.pos_y,1
    data=deepcopy(target.character.data);data['health']={'current':700,'max':700,'effects':[]};target.character.data=data
    for kind in ('floor','floor_opening'):
        db.session.add(LocationObject(location_id=actor.location_id,type=kind,tile_x=2,tile_y=5,
            properties={'level':1,'building_id':'open-floor','dimensions':{'width':6 if kind=='floor' else 2,'depth':6 if kind=='floor' else 2,'height':.15}}))
    db.session.commit()
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x,actor.pos_y,profile(blast_base=100,blast_falloff=10))
    entry=next(r for r in result['targets'] if r['character_id']==target.character_id)
    assert entry['distance']==pytest.approx(2.4,abs=.05)
    assert entry['blast_damage']==76


@pytest.mark.parametrize('opening',[False,True])
def test_throw_to_another_floor_requires_open_trajectory_and_keeps_floor(grenade_game,monkeypatch,opening):
    actor=grenade_game['actor_location']
    db.session.add(LocationObject(location_id=actor.location_id,type='floor',tile_x=4,tile_y=5,
        properties={'level':1,'building_id':'throw-floor','dimensions':{'width':10,'depth':8,'height':.15}}))
    if opening:
        db.session.add(LocationObject(location_id=actor.location_id,type='floor_opening',tile_x=2,tile_y=5,
            properties={'level':1,'building_id':'throw-floor','dimensions':{'width':3,'depth':3}}))
    data=deepcopy(actor.character.data);data['skills']['other']['tactics']['base']=80;actor.character.data=data
    db.session.commit();dice(monkeypatch,[20])
    def perform():
        return CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'explosive_attack',
            target_x=6,target_y=5.2,target_floor_level=1,explosive_source='hand',item_path=['inventory','pockets',0])
    if not opening:
        with pytest.raises(ValidationError,match='перекрыт'):
            perform()
        assert actor.action_points_current==20
    else:
        result=perform()['explosive']
        assert result['floor_level']==1 and result['success'] is True
        state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
        assert state.pending_explosives[0]['floor_level']==1


def test_scattered_grenade_falls_through_open_floor_instead_of_hanging_in_air(grenade_game,monkeypatch):
    actor=grenade_game['actor_location']
    for kind in ('floor','floor_opening'):
        db.session.add(LocationObject(location_id=actor.location_id,type=kind,tile_x=2 if kind=='floor_opening' else 4,tile_y=5,
            properties={'level':1,'building_id':'scatter-floor','dimensions':{'width':3 if kind=='floor_opening' else 10,'depth':3 if kind=='floor_opening' else 8,'height':.15}}))
    db.session.commit();dice(monkeypatch,[1])
    monkeypatch.setattr(CombatService,'_scatter_point',lambda *a,**kw:(2.1,5.2))
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'explosive_attack',
        target_x=6,target_y=5.2,target_floor_level=1,explosive_source='hand',item_path=['inventory','pockets',0])['explosive']
    assert result['success'] is False and result['floor_level']==0
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    assert state.pending_explosives[0].get('floor_level',0)==0


def test_fire_area_does_not_reignite_character_standing_in_water(grenade_game,monkeypatch):
    from app.services.burning import ignite
    actor=grenade_game['actor_location'];location=grenade_game['location']
    tiles=deepcopy(location.tiles_data);tiles[5][2]['terrain']='water';location.tiles_data=tiles
    monkeypatch.setattr(CombatService,'physical_position',lambda _: {'height':1})
    ignite(actor,8,source='Молотов')
    area={'type':'fire','name':'Молотов','x':actor.pos_x,'y':actor.pos_y,'radius':2,'burn_rounds':5}
    result=CombatService._apply_area_effect_to_character(area,actor,1)
    assert result['extinguished_by_water'] is True
    assert not any(e['type']=='burning' for e in actor.character.data['health']['effects'])


def test_underbarrel_pepper_gas_keeps_pain_for_two_rounds_after_exit(grenade_game,monkeypatch):
    from app.services.combat import EXPLOSIVE_PROFILES
    actor=grenade_game['actor_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    area=CombatService._area_from_profile({**EXPLOSIVE_PROFILES['underbarrel_gas'],
        'key':'underbarrel_gas'},actor.pos_x,actor.pos_y,state.round_number)
    area['floor_level']=actor.floor_level or 0
    state.area_effects=[area]
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:high)
    CombatService._advance_area_effects(state)
    assert actor.character.data['health']['painLevel']==1
    actor.pos_x,actor.pos_y=10,10
    CombatService._advance_area_effects(state)
    after=next(effect for effect in actor.character.data['health']['effects']
        if effect.get('source')=='gas_cloud_exit')
    assert after['remaining']==2
    CombatService._apply_periodic_health_effects(actor,phase='round_end')
    assert actor.character.data['health']['painLevel']==2
    CombatService._apply_periodic_health_effects(actor,phase='round_end')
    assert actor.character.data['health']['painLevel']==3
    assert not any(effect.get('source')=='gas_cloud_exit'
        for effect in actor.character.data['health']['effects'])


def test_deleted_participant_does_not_keep_short_fuse_pending(grenade_game,monkeypatch):
    from app.services.grenade_reaction import begin
    quiet_explosion(monkeypatch)
    actor,target=grenade_game['actor_location'],grenade_game['target_location'];state=short_fuse(grenade_game)
    db.session.delete(target);db.session.commit();begin(actor,state)
    CombatService.end_turn(actor.location_id,grenade_game['actor_user']['id'],actor.id)
    assert state.pending_explosives==[]


def test_instant_self_explosion_keeps_damage_and_inventory_expense(grenade_game,monkeypatch):
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['inventory']['pockets'][0]['name']='Граната РГН';data['skills']['other']['tactics']['base']=80
    actor.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:b)
    result=throw(grenade_game,explosive_fuse_mode='delay')['explosive']
    entry=next(t for t in result['explosion']['targets'] if t['character_id']==actor.character_id)
    assert entry['blast_damage']>0 and actor.character.data['health']['current']==entry['current_health']<700
    assert actor.character.data['inventory']['pockets'][0]['quantity']==1


def test_smoke_does_not_become_physical_cover_against_explosion(grenade_game,monkeypatch):
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    state.area_effects=[{'type':'smoke','x':actor.pos_x,'y':actor.pos_y,'radius':5,'floor_level':0}]
    db.session.commit()
    cover=CombatService._explosion_cover(actor.location_id,actor.pos_x-1,actor.pos_y,actor)
    assert cover['grade']=='none' and not cover['zones']
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x-1,actor.pos_y,profile(blast_base=100,fragment=100))
    entry=next(t for t in result['targets'] if t['character_id']==actor.character_id)
    assert entry['blast_damage']==100 and entry['fragment_damage']>0


@pytest.mark.parametrize('protection,category,death',[(0,'conditional',True),(5,'flimsy',True),(20,'medium',False)])
def test_prone_facing_special_death_respects_cover_categories(grenade_game,monkeypatch,protection,category,death):
    quiet_explosion(monkeypatch)
    actor=grenade_game['actor_location'];actor.posture='prone';actor.facing_x=-1;actor.facing_y=0
    monkeypatch.setattr(CombatService,'_explosion_cover',lambda *a,**kw:{'grade':'full','protection':protection,'objects':[],
        'zones':{'chest':{'class':category,'physical_protection':protection}}})
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x-.5,actor.pos_y,profile(blast_base=10))
    entry=next(t for t in result['targets'] if t['character_id']==actor.character_id)
    assert entry['death'] is death


@pytest.mark.parametrize('posture,expected',[('standing',3.25),('sitting',6.25),('prone',8.25)])
def test_throw_formula_uses_three_skill_bonuses_and_only_highest_same_floor_obstacle(grenade_game,monkeypatch,posture,expected):
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['skills']['physical']['strength']['base']=14;data['skills']['physical']['agility']['base']=12
    data['skills']['other']['tactics']['base']=16;actor.character.data=data;actor.posture=posture
    for x,height,level in ((3,1,0),(4,2,0),(5,10,1)):
        db.session.add(LocationObject(location_id=actor.location_id,type='wall',tile_x=x,tile_y=5,properties={
            'level':level,'dimensions':{'width':.2,'depth':3,'height':height}}))
    db.session.commit();dice(monkeypatch,[20])
    result=throw(grenade_game)['explosive']
    # 3 − 2 − 1 − 3 + 3,25 м + 3 (только высшее препятствие) + положение.
    assert result['difficulty']==pytest.approx(expected)


@pytest.mark.parametrize('fuse',['instant','round','turn_end'])
@pytest.mark.parametrize('choice',['include','exclude'])
def test_gm_decides_outsiders_for_every_explosion_before_any_damage(grenade_game,monkeypatch,fuse,choice):
    from app.services.explosion_choice import decide
    from app.services.grenade_reaction import prepare_event,begin,window
    from app.services.exceptions import ConflictError,PermissionDenied
    quiet_explosion(monkeypatch)
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(target.character.data);data['health']={'current':700,'max':700,'effects':[]};target.character.data=data
    target.pos_x,target.pos_y=7.1,5.2
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.turn_order=[actor.id]
    event={'id':'outside-test','item_name':'Тест','x':5.1,'y':5.2,'floor_level':0,'profile':profile(blast_base=10),'actor_id':actor.id,'round':state.round_number}
    if fuse=='turn_end':
        prepare_event(state,event);state.pending_explosives=[event];begin(actor,state)
        assert not window(actor)
    elif fuse=='round':
        event['trigger']='actor_turn_start';state.pending_explosives=[event]
        CombatService._process_pending_explosives(state,phase='actor_turn_start',actor_id=actor.id)
    else:
        result=CombatService.resolve_explosion(actor.location_id,5.1,5.2,event['profile'])
        assert result['awaiting_gm']
    db.session.commit();pending=state.pending_explosives[0]
    assert pending['gm_choices']==[target.id] and target.character.data['health']['current']==700
    with pytest.raises(ConflictError):
        CombatService.end_turn(actor.location_id,grenade_game['actor_user']['id'],actor.id)
    with pytest.raises(PermissionDenied):
        decide(actor.location_id,grenade_game['target_user']['id'],pending['id'],target.id,choice)
    result=decide(actor.location_id,grenade_game['lobby'].gm_id,pending['id'],target.id,choice)
    assert (target.id in state.turn_order)==(choice=='include')
    if fuse=='turn_end':
        assert window(actor)
        CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,actor.id)
        if choice=='include':
            CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,actor.id)
            assert window(target) and target.character.data['health']['current']==700
            CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,target.id)
    assert target.character.data['health']['current']==(690 if choice=='include' else 700)
    with pytest.raises(ConflictError):
        decide(actor.location_id,grenade_game['lobby'].gm_id,pending['id'],target.id,choice)


@pytest.mark.parametrize('effect',['flash','gas','fire'])
def test_gm_exclusion_also_covers_special_grenades_and_persistent_areas(grenade_game,monkeypatch,effect):
    from app.services.explosion_choice import decide
    quiet_explosion(monkeypatch)
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=5.1,5.2
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.turn_order=[actor.id]
    before=deepcopy(target.character.data)
    result=CombatService.resolve_explosion(actor.location_id,5.1,5.2,profile(effect=effect,chemical_damage=100,burn_damage=100))
    assert result['awaiting_gm']
    event=state.pending_explosives[0];db.session.commit()
    decide(actor.location_id,grenade_game['lobby'].gm_id,event['id'],target.id,'exclude')
    for area in state.area_effects or []:
        assert CombatService._apply_area_effect_to_character(area,target,state.round_number) is None
    assert target.character.data['health']['current']==before['health']['current']
    assert target.character.data['health']['effects']==before['health']['effects']
    assert target.character.data['inventory']==before['inventory']
