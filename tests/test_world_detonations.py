"""Взрыв без сохранённой подлокации использует общий боевой расчёт."""
import pytest
from app.extensions import db
from app.models import Location,LocationCharacter
from app.services.combat import CombatService
from app.services.world_detonations import apply_to_character
from test_grenade_rules import grenade_game


@pytest.mark.parametrize('name',['Граната РГД-5','Светошумовая граната Заря','Химическая граната Черемуха','Коктейль Молотова'])
def test_bearer_receives_grenade_effect_without_inserting_fake_location(grenade_game,name,monkeypatch):
    pair=grenade_game;character=pair['actor_character']
    profile=CombatService._explosive_profile({'name':name,'category':'grenade'})
    assert profile
    locations=Location.query.count();people=LocationCharacter.query.count();before=character.data['health']['current']
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:b)
    result=apply_to_character(character,profile)
    db.session.commit()
    assert Location.query.count()==locations and LocationCharacter.query.count()==people
    assert not db.session.new
    assert result['targets']
    health=character.data['health']
    if profile.get('effect')=='flash':assert any(e['type']=='blindness' for e in health['effects'])
    elif profile.get('effect')=='fire':assert any(e['type']=='burning' for e in health['effects'])
    elif profile.get('effect')=='gas':assert health.get('painLevel',0)>0 or health['current']<before or health['effects']
    else:assert health['current']<before


def test_global_damage_sync_does_not_notify_witnesses_in_old_location(grenade_game,monkeypatch):
    pair=grenade_game;character=pair['actor_character'];calls=[]
    monkeypatch.setattr(CombatService,'_notify_observed_death',lambda actor,**kwargs:calls.append(actor.location_id))
    profile=CombatService._explosive_profile({'name':'Граната Ф-1','category':'grenade'})
    apply_to_character(character,profile)
    assert all(location_id is None for location_id in calls)
