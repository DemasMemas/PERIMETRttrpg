"""Вторая кожа: отдельные сроки снижения боли и обычное восстановление."""
from copy import deepcopy
from app.extensions import db
from app.services.artifact_effects import artifact_passive_profile
from app.services.effects import advance_timed_effects
from app.services.world_rules import artifact_catalog
from app.services.combat import CombatService
from test_grenade_rules import grenade_game


def skin():
    rule=next(a for a in artifact_catalog() if a['name']=='Вторая кожа')
    return {'health':{'current':500,'max':700,'painLevel':7,'radiation':0,'effects':[]},
            'equipment':{'armor':{'containers':[{'item':{'id':'skin-one','name':rule['name'],
                'category':'artifact','attributes':deepcopy(rule)}}]}}}


def advance(data,seconds):
    health=data['health'];health['effects']=advance_timed_effects(health,health['effects'],seconds,
        character_data=data,recover_pain=True,advance_medication_turns=True)


def test_skin_world_six_second_tick_is_additional_and_not_ten_minute_relief():
    data=skin();advance(data,5)
    assert data['health']['painLevel']==7
    advance(data,1)
    assert data['health']['painLevel']==5
    assert data['health']['current']==500 and data['health']['radiation']==0
    assert artifact_passive_profile(data)['pain_per_movement']==-3


def test_skin_remove_returns_to_natural_recovery_without_delayed_extra():
    data=skin();advance(data,6);assert data['health']['painLevel']==5
    data['equipment']={};advance(data,6)
    assert data['health']['painLevel']==4
    advance(data,600);assert data['health']['painLevel']==0


def test_skin_round_relief_occurs_once_after_all_participants(grenade_game):
    case=grenade_game;actor=case['actor_location'];target=case['target_location']
    actor.character.data=skin();db.session.commit()
    CombatService.end_turn(actor.location_id,case['lobby'].gm_id,actor.id)
    assert actor.character.data['health']['painLevel']==7
    CombatService.end_turn(actor.location_id,case['lobby'].gm_id,target.id)
    assert actor.character.data['health']['painLevel']==5


def test_skin_relief_does_not_bypass_permanent_minimum():
    data=skin();data['health']['combatMeta']={'toughNut':{'minimumPain':4,'uses':0}}
    advance(data,600)
    assert data['health']['painLevel']==4


def test_skin_relief_also_applies_after_new_pain_when_natural_recovery_is_blocked(grenade_game):
    actor=grenade_game['actor_location'];actor.character.data=skin()
    actor.character.data['health']['combatMeta']={'painIncreased':True}
    CombatService._apply_end_of_round_pain_recovery([actor])
    assert actor.character.data['health']['painLevel']==6
    actor.character.data['equipment']={}
    actor.character.data['health']['combatMeta']['painIncreased']=True
    CombatService._apply_end_of_round_pain_recovery([actor])
    assert actor.character.data['health']['painLevel']==6
