"""Шум: реальная оплата/выстрел, помещение, защита, слух и спад."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationObject,LocationCombatState
from app.services.combat import CombatService
from app.services.effects import apply_effect_to_health
from app.services.noise import apply,space,hearing_modifier
from test_grenade_rules import grenade_game


@pytest.mark.parametrize('level,penalty',[(0,0),(20,0),(21,-1),(49,-1),(50,-3),(69,-3),(70,-5),(89,-5),(90,-5)])
def test_hearing_scale_does_not_penalize_visual_checks(grenade_game,monkeypatch,level,penalty):
    data=grenade_game['actor_location'].character.data
    apply_effect_to_health(data['health'],{'type':'deafness','value':level})
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:10)
    hearing=CombatService._narrative_skill_check(data,'skills.physical.awareness.hearing')
    visual=CombatService._narrative_skill_check(data,'skills.physical.awareness.visual')
    assert hearing['hearing_modifier']==penalty
    assert visual['hearing_modifier']==0
    assert hearing['automatic_failure']==(level>=90)
    if level<90:assert hearing['total']==visual['total']+penalty


def room(case,*,gap=False,damaged=False):
    location=case['location']
    objects=[LocationObject(location_id=location.id,type='floor',tile_x=10,tile_y=10,
        properties={'building_id':'noise-room','dimensions':{'width':12,'depth':12,'height':.12},
                    'building_generation':{'condition':'ruins' if damaged else 'intact'}})]
    for x,y,w,d in ((10,4,12,.2),(10,16,12,.2),(4,10,.2,12),(16,10,.2,12)):
        if gap and x==16:continue
        objects.append(LocationObject(location_id=location.id,type='wall',tile_x=x,tile_y=y,
            properties={'building_id':'noise-room','dimensions':{'width':w,'depth':d,'height':.8 if damaged and x==16 else 2.4}}))
    db.session.add_all(objects);db.session.commit()
    return objects


@pytest.mark.parametrize('gap,damaged,coefficient',[(False,False,2),(True,False,1),(False,True,1)])
def test_room_geometry_and_destroyed_walls(grenade_game,gap,damaged,coefficient):
    objects=room(grenade_game,gap=gap,damaged=damaged)
    assert space(grenade_game['location'],objects,10,10,0)['coefficient']==coefficient


def test_intact_room_in_partly_damaged_building_keeps_double_noise(grenade_game):
    objects=room(grenade_game)
    props=deepcopy(objects[0].properties);props['building_generation']['condition']='damaged'
    objects[0].properties=props;db.session.commit()
    assert space(grenade_game['location'],objects,10,10,0)['coefficient']==2


@pytest.mark.parametrize('kind',['door','window'])
@pytest.mark.parametrize('opened',[False,True])
def test_intact_room_keeps_double_noise_with_open_door_or_window(grenade_game,kind,opened):
    objects=room(grenade_game)
    portal=LocationObject(location_id=grenade_game['location'].id,type=kind,tile_x=10,tile_y=4,
        properties={'building_id':'noise-room','is_open':opened,'elevation':.9 if kind=='window' else 0,
            'dimensions':{'width':1.5,'depth':.2,'height':1.2 if kind=='window' else 2.1}})
    db.session.add(portal);db.session.commit();objects.append(portal)
    assert space(grenade_game['location'],objects,10,10,0)['coefficient']==2
    actor=grenade_game['actor_location'];actor.pos_x,actor.pos_y=10,10;db.session.commit()
    result=apply(grenade_game['location'],10,10,0,20,target_ids=[actor.id])
    assert result[0]['noise_received']==40


def test_all_in_same_room_hear_beyond_open_air_radius(grenade_game):
    room(grenade_game)
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.pos_x,actor.pos_y=8,10;target.pos_x,target.pos_y=14,10
    db.session.commit()
    result=apply(grenade_game['location'],8,10,0,10)
    assert {r['character_id'] for r in result}=={actor.character_id,target.character_id}
    assert all(r['noise_received']==20 for r in result)


def test_outdoors_range_and_individual_headphones_order(grenade_game):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+3,actor.pos_y
    data=deepcopy(actor.character.data);data['equipment']={'headphones':{'deafeningCoef':.5,'noiseAbsorption':3}}
    actor.character.data=data;db.session.commit()
    result=apply(grenade_game['location'],actor.pos_x,actor.pos_y,0,40)
    own=next(r for r in result if r['character_id']==actor.character_id)
    assert own['noise_received']==8.5
    assert CombatService._deafness_level(actor.character.data)==8.5
    assert next(r for r in result if r['character_id']==target.character_id)['noise_received']==20
    target.pos_x+=2;db.session.commit()
    assert all(r['character_id']!=target.character_id for r in apply(grenade_game['location'],actor.pos_x,actor.pos_y,0,40))


def test_noise_accumulates_and_decays_once_per_round_preserving_trauma_floor(grenade_game):
    actor=grenade_game['actor_location']
    apply_effect_to_health(actor.character.data['health'],{'type':'deafness','value':30,'minimum_value':30,'source':'blast_wave','remaining':2,'time_unit':'hour','tick':'time_elapsed'})
    apply(grenade_game['location'],actor.pos_x,actor.pos_y,0,40,count=3)
    assert CombatService._deafness_level(actor.character.data)==90
    CombatService._apply_periodic_health_effects(actor,phase='turn_end')
    assert CombatService._deafness_level(actor.character.data)==90
    CombatService._apply_periodic_health_effects(actor,phase='round_end')
    assert CombatService._deafness_level(actor.character.data)==70
    for _ in range(5):CombatService._apply_periodic_health_effects(actor,phase='round_end')
    assert CombatService._deafness_level(actor.character.data)==30


def test_threshold_ninety_triggers_stress_once_for_same_noise_effect(grenade_game,monkeypatch):
    actor=grenade_game['actor_location']
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:b)
    apply(grenade_game['location'],actor.pos_x,actor.pos_y,0,100,count=2)
    assert actor.character.data['health']['stress']==1
    apply(grenade_game['location'],actor.pos_x,actor.pos_y,0,100)
    assert actor.character.data['health']['stress']==1


def test_explosion_noise_outside_damage_radius_still_requires_gm_choice(grenade_game):
    from app.services.explosion_choice import candidates
    objects=room(grenade_game)
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.pos_x,actor.pos_y=8,10;target.pos_x,target.pos_y=14,10
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.turn_order=[actor.id]
    db.session.commit()
    event={'x':8,'y':10,'floor_level':0,'profile':{'radius':1,'noise':10}}
    assert candidates(state,event)==[target.id]


def test_explosion_noise_only_affects_selected_target_once(grenade_game):
    actor=grenade_game['actor_location']
    profile={'key':'noise-test','name':'Шум','radius':0,'effect':'flash','blindness':0,'noise':40}
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x,actor.pos_y,profile,target_ids=[actor.id])
    assert result['noise_targets'][0]['noise_received']==20
    assert CombatService._deafness_level(actor.character.data)==20
    CombatService.resolve_explosion(actor.location_id,actor.pos_x,actor.pos_y,profile,target_ids=[])
    assert CombatService._deafness_level(actor.character.data)==20


def test_grenade_catalog_preserves_noise_without_template_id():
    assert CombatService._explosive_profile({'name':'РГД-5'})['noise']==15


@pytest.mark.parametrize('name',[
    'Коктейль Молотова','Napalm-AN','Черемуха','Refresher','РДГ-6','Screen',
    'Подствольный газовый','Подствольный дымовой',
])
def test_special_grenades_without_noise_do_not_cause_deafness(grenade_game,name):
    profile=CombatService._explosive_profile({'name':name})
    assert profile is not None and profile.get('noise',0)==0
    actor=grenade_game['actor_location']
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x,actor.pos_y,profile,target_ids=[actor.id])
    assert result['noise_targets']==[]
    assert CombatService._deafness_level(actor.character.data)==0


@pytest.mark.parametrize('indoors,expected',[(False,10),(True,40)])
def test_rg60tb_noise_uses_approved_twenty(grenade_game,indoors,expected):
    actor=grenade_game['actor_location']
    if indoors:
        room(grenade_game);actor.pos_x,actor.pos_y=10,10;db.session.commit()
    profile=CombatService._explosive_profile({'name':'РГ-60ТБ'})
    assert profile['noise']==20
    # Шум проверяется отдельно от травмы слуха ударной волны.
    profile={**profile,'blast_base':0,'fragment':0}
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x,actor.pos_y,profile,target_ids=[actor.id])
    assert result['noise_targets'][0]['noise_received']==expected


def test_floor_alone_is_open_space_and_manual_room_without_building_id_is_closed(grenade_game):
    objects=room(grenade_game)
    for obj in objects:
        p=deepcopy(obj.properties);p.pop('building_id',None);obj.properties=p
    db.session.commit()
    assert space(grenade_game['location'],objects,10,10,0)['coefficient']==2
    assert space(grenade_game['location'],objects[:1],10,10,0)['coefficient']==.5


def test_real_shot_causes_noise_and_unpaid_shot_does_not(grenade_game,monkeypatch):
    from app.services.exceptions import ValidationError
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+3,actor.pos_y
    data=deepcopy(actor.character.data)
    data['weapons']=[{'id':'noise-gun','name':'Проверочный пистолет','subcategory':'Пистолеты','accuracy':0,'range':20,
        'damage':10,'durability':100,'maxDurability':100,'ergonomics':100,'fixedMagazine':True,'ammo':10,
        'fixedAmmo':[{'name':'9x18','quantity':10,'attributes':{'damage':10}}],
        'attributes':{'noise':40,'fire_rate':5,'min_strength':0,'magazine_size':10}}]
    actor.character.data=data;actor.drawn_weapon_index=0;actor.action_points_current=0;db.session.commit()
    def shoot():
        return CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',
            weapon_index=0,fire_mode='unaimed',shot_count=1,action_points=1,target_character_id=target.character_id)
    with pytest.raises(ValidationError,match='Not enough'):shoot()
    assert CombatService._deafness_level(actor.character.data)==0
    actor.action_points_current=20;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:a)
    result=shoot()
    assert result['attack']['shot_count']==1
    assert CombatService._deafness_level(actor.character.data)==20
    assert any(r['character_id']==target.character_id for r in result['attack']['noise_targets'])
    shoot()
    assert CombatService._deafness_level(actor.character.data)==40


@pytest.mark.parametrize('jam_after_first,expected_shots',[(False,3),(True,1)])
def test_burst_noise_counts_fired_shots_and_stops_after_jam(grenade_game,monkeypatch,jam_after_first,expected_shots):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+3,actor.pos_y
    data=deepcopy(actor.character.data)
    data['weapons']=[{'id':'burst-noise','name':'Проверочный автомат','subcategory':'Штурмовые винтовки',
        'range':20,'damage':10,'ergonomics':100,'durability':100,'maxDurability':100,'ammo':10,
        'fixedMagazine':True,'fixedAmmo':[{'name':'Патрон','quantity':10,'attributes':{'damage':10}}],
        'attributes':{'noise':40,'fire_rate':10,'fire_modes':{'supports_burst':True,'burst_size':3}}}]
    actor.character.data=data;actor.drawn_weapon_index=0;db.session.commit()
    def jam(weapon,roll):
        if jam_after_first:
            weapon['jam']={'type':'stovepipe','triggered':True,'blocks_fire':True}
            return weapon['jam']
        return None
    monkeypatch.setattr(CombatService,'_roll_weapon_jam',jam)
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:a)
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',
        weapon_index=0,fire_mode='burst',shot_count=3,action_points=3,target_character_id=target.character_id)
    assert result['attack']['shot_count']==expected_shots
    assert CombatService._deafness_level(actor.character.data)==20*expected_shots
    assert actor.character.data['weapons'][0]['ammo']==10-expected_shots
