"""Реактивные свойства каталога не действуют до получения нужного урона."""
from copy import deepcopy
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
