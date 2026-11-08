"""Реактивные свойства каталога не действуют до получения нужного урона."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from app.services.artifact_effects import artifact_passive_profile
from app.services.artifact_effects import damage_reactions, preserve, is_reaction
from app.services.world_rules import artifact_catalog
from app.services.effects import advance_timed_effects
from app.services.combat import CombatService
from app.extensions import db
from test_grenade_rules import grenade_game


def equipped(name):
    rule=next(rule for rule in artifact_catalog() if rule['name']==name)
    return {'health':{'current':700,'max':700,'effects':[]},'equipment':{'armor':{'containers':[{'item':{
        'id':'reactive-artifact','name':name,'category':'artifact','attributes':deepcopy(rule)}}]}}}


@pytest.mark.parametrize('name', ['Пламень крови','Огненная вена','Дар пламени'])
def test_thermal_reaction_is_not_a_permanent_passive(name):
    profile=artifact_passive_profile(equipped(name))
    assert profile['movement_penalty']==0 and profile['protection']['physical']==0 and profile['bleeding_severity']==0


@pytest.mark.parametrize('damage,kind',[(0,'thermal'),(20,'physical'),(20,'chemical'),(20,'electric')])
def test_other_damage_or_full_block_does_not_start_thermal_reaction(damage,kind):
    data=equipped('Дар пламени')
    assert damage_reactions(data,damage,kind)==[]
    assert data['health']['effects']==[]


def test_repeated_hit_refreshes_own_timer_without_stacking_and_unequip_removes_bonus():
    data=equipped('Огненная вена')
    damage_reactions(data,20,'thermal')
    health=data['health'];health['effects']=advance_timed_effects(health,health['effects'],12,character_data=data)
    assert next(e for e in health['effects'] if is_reaction(e))['remaining_seconds']==6
    damage_reactions(data,20,'thermal')
    assert len([e for e in health['effects'] if is_reaction(e)])==1
    assert next(e for e in health['effects'] if is_reaction(e))['remaining_seconds']==18
    assert CombatService._target_elemental_protection(data,'physical')==5
    old=deepcopy(data);data['equipment']={}
    assert CombatService._target_elemental_protection(data,'physical')==0
    preserve(old,data)
    assert not any(is_reaction(e) for e in data['health']['effects'])


def test_managed_reaction_cannot_be_forged_removed_or_extended_by_sheet_save():
    data=equipped('Огненная вена');damage_reactions(data,10,'thermal')
    for changed in ('removed','forged','extended'):
        incoming=deepcopy(data)
        if changed=='removed':incoming['health']['effects']=[]
        elif changed=='forged':incoming['health']['effects'][0]['value']=100
        else:incoming['health']['effects'][0]['remaining_seconds']=999999
        preserve(data,incoming)
        assert incoming['health']['effects']==data['health']['effects']
    incoming=deepcopy(data);preserve({},incoming)
    assert not any(is_reaction(e) for e in incoming['health']['effects'])


def test_creation_and_save_preserve_server_owned_reaction(grenade_game):
    from app.services.character import CharacterService
    data=equipped('Огненная вена');damage_reactions(data,10,'thermal')
    case=grenade_game
    created=CharacterService.create_character(case['lobby'].id,case['lobby'].gm_id,'Подмена реакции',data)
    assert not any(is_reaction(e) for e in created.data['health']['effects'])
    target=case['target_location'];target.character.data=equipped('Огненная вена');db.session.commit()
    CombatService._apply_general_damage(target,10,damage_type='thermal');db.session.commit()
    expected=deepcopy([e for e in target.character.data['health']['effects'] if is_reaction(e)])
    incoming=deepcopy(target.character.data);incoming['health']['effects']=[]
    incoming['_revision']=target.character.revision
    saved=CharacterService.update_character(target.character_id,case['lobby'].gm_id,{'data':incoming})
    assert [e for e in saved.character.data['health']['effects'] if is_reaction(e)]==expected


def test_burning_round_starts_reaction_and_round_clock_ticks_once(grenade_game):
    from app.services.burning import ignite
    case=grenade_game;actor=case['actor_location'];target=case['target_location']
    actor.character.data=equipped('Пламень крови');ignite(actor,1,source='Проверка артефакта');db.session.commit()
    CombatService.end_turn(actor.location_id,case['lobby'].gm_id,actor.id)
    assert not any(is_reaction(e) for e in actor.character.data['health']['effects'])
    CombatService.end_turn(actor.location_id,case['lobby'].gm_id,target.id)
    remaining=next(e for e in actor.character.data['health']['effects'] if is_reaction(e))['remaining_seconds']
    assert remaining==12
    CombatService.end_turn(actor.location_id,case['lobby'].gm_id,actor.id)
    assert next(e for e in actor.character.data['health']['effects'] if is_reaction(e))['remaining_seconds']==remaining
    CombatService.end_turn(actor.location_id,case['lobby'].gm_id,target.id)
    assert next(e for e in actor.character.data['health']['effects'] if is_reaction(e))['remaining_seconds']==remaining-6


@pytest.mark.parametrize('roll,extra',[(1,40),(50,40),(51,0),(100,0)])
def test_shard_chance_boundary_and_extra_damage_only_in_general_pool(grenade_game,monkeypatch,roll,extra):
    target=grenade_game['target_location'];target.character.data=equipped('Пылающий осколок');db.session.commit()
    draws=[]
    def percent(low,high):
        assert (low,high)==(1,100);draws.append(roll);return roll
    monkeypatch.setattr('app.services.artifact_effects.random',SimpleNamespace(randint=percent))
    health=CombatService._apply_attack_damage(target,10,'chest',{'damage_type':'crushing'},trauma_checks=0,allow_bleeding=False)
    assert health['current']==700-10-extra
    assert health['zones']['chest']['current']==health['zones']['chest']['max']-10
    assert draws==[roll]
    reaction=health['_attackOutcome'].get('artifact_reactions',[])
    assert len(reaction)==int(extra>0)
    if reaction:assert reaction[0]['raw_damage']==50 and reaction[0]['protection']==20 and reaction[0]['damage']==40


@pytest.mark.parametrize('damage,kind',[(0,'physical'),(5,'thermal'),(5,'electric'),(5,'chemical'),(5,'psi')])
def test_shard_does_not_roll_for_other_or_zero_damage(grenade_game,monkeypatch,damage,kind):
    target=grenade_game['target_location'];target.character.data=equipped('Пылающий осколок');db.session.commit()
    def forbidden(*args):raise AssertionError('Не должно быть броска шанса')
    monkeypatch.setattr('app.services.artifact_effects.random',SimpleNamespace(randint=forbidden))
    result=CombatService._apply_general_damage(target,damage,damage_type=kind)
    assert not result.get('artifact_reactions')
    assert result['current_health']==700-damage


def test_shard_extra_respects_full_thermal_protection_and_can_start_thermal_modifier(grenade_game,monkeypatch):
    target=grenade_game['target_location'];data=equipped('Пылающий осколок')
    data['equipment']['armor']['containers'].extend(equipped('Огненная вена')['equipment']['armor']['containers'])
    data['equipment']['armor']['containers'][1]['item']['id']='second-artifact'
    target.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.artifact_effects.random',SimpleNamespace(randint=lambda a,b:50))
    result=CombatService._apply_general_damage(target,10)
    assert result['artifact_reactions'][0]['damage']==30
    assert artifact_passive_profile(target.character.data)['protection']['physical']==5
    data=deepcopy(target.character.data);data['equipment']['armor']['protection']={'thermal':100};target.character.data=data
    before=data['health']['current'];result=CombatService._apply_general_damage(target,10)
    assert result['artifact_reactions'][0]['damage']==0
    assert result['current_health']==before-10


def test_shard_general_damage_can_kill_without_zone_damage_or_repeating_backlash(grenade_game,monkeypatch):
    target=grenade_game['target_location'];data=equipped('Пылающий осколок');data['health']['current']=45
    target.character.data=data;db.session.commit();draws=[]
    def percent(low,high):draws.append((low,high));return 50
    monkeypatch.setattr('app.services.artifact_effects.random',SimpleNamespace(randint=percent))
    result=CombatService._apply_general_damage(target,10)
    assert result['current_health']==0 and result['health_state']=='dead'
    assert draws==[(1,100)]
    assert all(zone['current']==zone['max'] for zone in target.character.data['health']['zones'].values())


@pytest.mark.parametrize('kind,expected',[('physical',50),('thermal',55),('electric',50),('chemical',50)])
def test_blood_flame_increases_only_incoming_thermal_damage(grenade_game,kind,expected):
    target=grenade_game['target_location'];target.character.data=equipped('Пламень крови');db.session.commit()
    result=CombatService._apply_general_damage(target,50,damage_type=kind)
    assert result['damage']==expected
    assert result['current_health']==700-expected


def test_blood_flame_increases_thermal_zone_damage_once_and_stops_when_removed(grenade_game):
    target=grenade_game['target_location'];target.character.data=equipped('Пламень крови');db.session.commit()
    health=CombatService._apply_attack_damage(target,50,'chest',{'damage_type':'crushing'},damage_type='thermal',trauma_checks=0,allow_bleeding=False)
    assert health['current']==645
    assert health['zones']['chest']['current']==health['zones']['chest']['max']-55
    data=deepcopy(target.character.data);data['equipment']={};target.character.data=data
    result=CombatService._apply_general_damage(target,50,damage_type='thermal')
    assert result['damage']==50 and result['current_health']==595


@pytest.mark.parametrize('name,rounds,movement,physical,bleeding',[
    ('Пламень крови',3,-1,0,0),('Огненная вена',3,0,5,0),('Дар пламени',2,-1,0,3),
])
def test_real_damage_starts_temporary_modifiers_and_world_time_expires_them(grenade_game,name,rounds,movement,physical,bleeding):
    target=grenade_game['target_location'];data=deepcopy(target.character.data);data['equipment']=equipped(name)['equipment']
    target.character.data=data;db.session.commit()
    CombatService._apply_general_damage(target,10,source='artifact-test',damage_type='thermal')
    data=target.character.data;profile=artifact_passive_profile(data)
    assert (profile['movement_penalty'],profile['protection']['physical'],profile['bleeding_severity'])==(movement,physical,bleeding)
    health=data['health'];health['effects']=advance_timed_effects(health,health['effects'],rounds*6-1,character_data=data)
    assert any(e.get('source','').startswith('artifact-reaction:') for e in health['effects'])
    health['effects']=advance_timed_effects(health,health['effects'],1,character_data=data)
    profile=artifact_passive_profile(data)
    assert profile['movement_penalty']==0 and profile['protection']['physical']==0 and profile['bleeding_severity']==0
