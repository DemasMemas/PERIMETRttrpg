"""«За перемещение» — десять минут времени, а не отдельное движение."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState
from app.services.artifact_effects import is_periodic_clock,preserve,sync_clocks
from app.services.effects import advance_timed_effects,apply_periodic_effects_to_health,apply_expired_effects_to_health
from test_consumable_use import pair


def data():
    return {'health':{'current':500,'max':700,'radiation':0,'painLevel':0,'effects':[]},
        'equipment':{'armor':{'containers':[{'item':{'id':'timed-artifact','category':'artifact',
            'name':'Проверка десяти минут','attributes':{'positive_effect':'Общее здоровье +25 за перемещение',
                'negative_effect':'3 Радиации за перемещение Усиливает боль на 1 уровень за перемещение'}}}]}}}


def advance(value,seconds,**kwargs):
    health=value['health'];health['effects']=advance_timed_effects(health,health['effects'],seconds,character_data=value,**kwargs)
    return health


def test_first_pulse_waits_full_ten_minutes_then_repeats():
    value=data();health=advance(value,599)
    assert (health['current'],health['radiation'],health['painLevel'])==(500,0,0)
    advance(value,1)
    assert (health['current'],health['radiation'],health['painLevel'])==(525,3,1)
    advance(value,600)
    assert (health['current'],health['radiation'],health['painLevel'])==(550,6,2)


@pytest.mark.parametrize('parts',[[1201],[6]*200+[1],[599,1,599,2],[300,100,801]])
def test_dividing_time_between_modes_preserves_pulses_and_remainder(parts):
    value=data()
    for seconds in parts:health=advance(value,seconds,advance_event_modifiers=False)
    assert (health['current'],health['radiation'],health['painLevel'])==(550,6,2)
    assert next(e for e in health['effects'] if is_periodic_clock(e))['remaining_seconds']==599


def test_any_number_of_movement_events_does_not_trigger_time_properties():
    value=data();health=value['health'];health['effects']=sync_clocks(value)
    for _ in range(25):
        apply_periodic_effects_to_health(health,health['effects'],'movement_end',character_data=value)
        apply_expired_effects_to_health(health,health['effects'],'movement_end',character_data=value)
    assert (health['current'],health['radiation'],health['painLevel'])==(500,0,0)
    assert next(e for e in health['effects'] if is_periodic_clock(e))['remaining_seconds']==600


def test_new_pain_at_minute_ten_is_not_healed_retroactively():
    value=data();health=advance(value,600,advance_medication_turns=True,recover_pain=True)
    assert health['painLevel']==1
    advance(value,6,advance_medication_turns=True,recover_pain=True)
    assert health['painLevel']==0


def test_timed_source_can_be_removed_and_cannot_be_accelerated_by_sheet():
    value=data();advance(value,400);incoming=deepcopy(value)
    timer=next(e for e in incoming['health']['effects'] if is_periodic_clock(e))
    timer.update(remaining_seconds=.001,onExpire=[{'field':'radiation','delta':100}])
    preserve(value,incoming)
    timer=next(e for e in incoming['health']['effects'] if is_periodic_clock(e))
    assert timer['remaining_seconds']==200 and timer['onExpire']==[]
    value['equipment']['armor']['containers']=[];advance(value,600)
    assert value['health']['radiation']==0 and not any(is_periodic_clock(e) for e in value['health']['effects'])


def test_multiple_artifacts_keep_their_own_contact_intervals():
    value=data();advance(value,300)
    second=deepcopy(value['equipment']['armor']['containers'][0]);second['item']['id']='second-timed-artifact'
    value['equipment']['armor']['containers'].append(second)
    health=advance(value,300);assert health['radiation']==3
    advance(value,300);assert health['radiation']==6


def test_world_clock_applies_properties_without_moving_and_respects_activity(client,pair,auth_headers):
    from test_regression_time_stress import advance_global_minutes
    LocationCombatState.query.one().status='idle'
    character=pair['actor_character'];value=deepcopy(character.data)
    value['equipment']=data()['equipment'];value['health'].update(current=500,radiation=0,painLevel=0)
    character.data=value;character.time_active=False;db.session.commit()
    advance_global_minutes(client,pair,auth_headers,10)
    assert character.data['health']['radiation']==0
    character.time_active=True;db.session.commit()
    advance_global_minutes(client,pair,auth_headers,9)
    assert character.data['health']['radiation']==0
    advance_global_minutes(client,pair,auth_headers,1)
    assert character.data['health']['radiation']==3 and character.data['health']['current']==525


def test_world_transition_waits_for_world_time_and_does_not_double_pulse(client,pair,auth_headers,monkeypatch):
    LocationCombatState.query.one().status='idle';lobby=pair['lobby'];headers=auth_headers(pair['actor_user'])
    character=pair['actor_character'];value=deepcopy(character.data)
    value['equipment']=data()['equipment'];value['health'].update(current=500,radiation=0,painLevel=0)
    character.data=value;character.time_active=True;db.session.commit()
    monkeypatch.setattr('app.lobbies.roll_travel_event',lambda *_:None)
    root=f'/lobbies/{lobby.id}/world-groups'
    first=client.post(root,headers=headers,json={'name':'Первая','tile_x':5,'tile_y':5}).json
    second=client.post(root,headers=headers,json={'name':'Вторая','tile_x':5,'tile_y':5}).json
    result=client.patch(root+f'/{first["id"]}/members',headers=headers,json={'character_ids':[character.id]})
    assert result.status_code==200,result.json
    response=client.post(root+f'/{first["id"]}/move',headers=headers,json={'tile_x':6,'tile_y':5})
    assert response.status_code==200,response.json
    assert character.data['health']['radiation']==0
    response=client.post(root+f'/{second["id"]}/move',headers=headers,json={'tile_x':6,'tile_y':5})
    assert response.status_code==200,response.json
    assert character.data['health']['radiation']==3 and character.data['health']['current']==525


@pytest.mark.parametrize('radiation,effect',[(73,'critical_condition'),(97,'death')])
def test_periodic_radiation_keeps_existing_threshold_consequences(radiation,effect):
    value=data();value['health']['radiation']=radiation
    health=advance(value,600)
    assert any(e['type']==effect and e.get('source')=='radiation_sickness' for e in health['effects'])
