"""Минутное выведение и цена фактически выведенной радиации по исходной строке."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState
from app.models.templates import ItemTemplate
from app.services.world_rules import artifact_catalog
from app.services.artifact_effects import is_radiation_clock,preserve,apply_artifact_world_movement
from app.services.effects import advance_timed_effects,apply_effect_to_health
from app.services.general_consumable import _apply_profile
from test_consumable_use import pair


def sponge_data(radiation=3):
    rule=next(r for r in artifact_catalog() if r['name']=='Чернобыльская губка')
    return {'health':{'current':700,'max':700,'radiation':radiation,'effects':[]},
        'equipment':{'armor':{'containers':[{'item':{'id':'sponge-unit','name':rule['name'],'category':'artifact',
            'attributes':deepcopy(rule)}}]}}}


def advance(data,seconds,**kwargs):
    health=data['health'];health['effects']=advance_timed_effects(health,health['effects'],seconds,character_data=data,**kwargs)
    return health


def test_minute_clock_clears_radiation_and_charges_actual_amount_once():
    data=sponge_data();health=advance(data,59)
    assert health['radiation']==3 and health['current']==700
    health=advance(data,1);assert health['radiation']==0 and health['current']==670
    assert next(e for e in health['effects'] if is_radiation_clock(e))['remaining_seconds']==60
    advance(data,120);assert health['current']==670


def test_long_interval_preserves_later_exposure_and_fractional_clock():
    data=sponge_data();data['health']['effects']=[{'id':'later-radiation','type':'delayed_adjustment','tick':'time_elapsed',
        'time_unit':'second','remaining':125,'adjustments':[{'field':'radiation','delta':2,'min':0}]}]
    health=advance(data,130)
    assert health['radiation']==2 and health['current']==670
    assert next(e for e in health['effects'] if is_radiation_clock(e))['remaining_seconds']==50


@pytest.mark.parametrize('steps',[[60],[6]*10,[12,13,35]])
def test_same_game_time_has_same_result(steps):
    data=sponge_data()
    for seconds in steps:advance(data,seconds,advance_event_modifiers=False)
    assert data['health']['radiation']==0 and data['health']['current']==670


def test_artifact_clock_does_not_tick_battle_medicine_twice():
    data=sponge_data();data['health']['effects']=[{'id':'round-drug','type':'regeneration','tick':'round_end',
        'source':'consumable-template:123','remaining':2,'value':20}]
    health=advance(data,60,advance_event_modifiers=False)
    medicine=next(e for e in health['effects'] if e.get('id')=='round-drug')
    assert medicine['remaining']==2 and health['current']==670


def test_unwearing_removes_clock_and_rewearing_starts_new_contact_minute():
    data=sponge_data();advance(data,59)
    artifact=data['equipment']['armor']['containers'].pop()
    health=advance(data,10);assert health['radiation']==3 and health['current']==700
    assert not any(is_radiation_clock(e) for e in health['effects'])
    data['equipment']['armor']['containers'].append(artifact)
    advance(data,59);assert health['radiation']==3
    advance(data,1);assert health['radiation']==0 and health['current']==670


def test_two_simultaneous_clocks_do_not_charge_same_radiation_twice():
    data=sponge_data();second=deepcopy(data['equipment']['armor']['containers'][0]);second['item']['id']='sponge-two'
    data['equipment']['armor']['containers'].append(second)
    health=advance(data,60);assert health['radiation']==0 and health['current']==670


def test_sheet_cannot_speed_up_or_replace_managed_timer():
    current=sponge_data();advance(current,6);incoming=deepcopy(current)
    timer=next(e for e in incoming['health']['effects'] if is_radiation_clock(e))
    timer.update(remaining_seconds=.01,onExpire=[{'field':'current','delta':500}])
    preserve(current,incoming);timer=next(e for e in incoming['health']['effects'] if is_radiation_clock(e))
    assert timer['remaining_seconds']==54 and timer['onExpire']==[{'field':'radiation','delta':0,'min':0,'max':0}]
    advance(incoming,.01);assert incoming['health']['radiation']==3


def test_instant_treatment_does_not_charge_sponge_health_cost():
    data=sponge_data(2)
    apply_effect_to_health(data['health'],{'type':'radiation','value':-10},character_data=data)
    assert data['health']['radiation']==0 and data['health']['current']==700


@pytest.mark.parametrize('delayed',[False,True])
def test_sponge_charges_only_the_radiation_left_after_another_treatment(delayed):
    data=sponge_data(3)
    if delayed:
        data['health']['effects']=[{'id':'ordinary-treatment','type':'delayed_adjustment','tick':'time_elapsed',
            'time_unit':'second','remaining':30,'adjustments':[{'field':'radiation','delta':-2,'min':0}]}]
    else:
        apply_effect_to_health(data['health'],{'type':'radiation','value':-2},character_data=data)
    health=advance(data,59)
    assert health['radiation']==1 and health['current']==700
    advance(data,1)
    assert health['radiation']==0 and health['current']==690


def test_other_sources_of_radiation_removal_do_not_charge_sponge_health_cost():
    data=sponge_data(3);health=data['health']
    _apply_profile(health,{'effects':[]},{'radiation_delta':-1},{'name':'Проверочная вода'},in_combat=False,character_data=data)
    assert health['radiation']==2 and health['current']==700
    data['equipment']['armor']['containers'].append({'item':{'category':'artifact','attributes':{'positive_effect':'-2 Радиации за перемещение'}}})
    apply_artifact_world_movement(data);assert health['radiation']==0 and health['current']==700


def test_water_component_does_not_charge_sponge_cost_through_game_route(client,pair,auth_headers):
    from app.services.equipment_catalog import load_catalog
    from app.services.character_starting_purchase import item_from_template
    LocationCombatState.query.one().status='idle'
    templates=[]
    for name in ['Вода','Сахар']:
        template=ItemTemplate(**next(r for r in load_catalog() if r['name']==name));db.session.add(template);templates.append(template)
    db.session.flush();character=pair['actor_character'];data=deepcopy(character.data)
    data['equipment']=sponge_data()['equipment'];data['health'].update(radiation=3,current=700,stress=2)
    data['inventory']['backpack']=[item_from_template(t) for t in templates]
    character.data=data;db.session.commit();sugar=deepcopy(data['inventory']['backpack'][1])
    result=client.post(f'/lobbies/characters/{character.id}/general-consumable',headers=auth_headers(pair['actor_user']),json={
        'operation_id':'sponge-sugar','source':{'path':['inventory','backpack',1],'snapshot':sugar},'application':{'kind':'self'}})
    assert result.status_code==200,result.json
    assert character.data['health']['radiation']==1 and character.data['health']['current']==700


def test_medical_route_does_not_charge_sponge_health_cost(client,pair,auth_headers,monkeypatch):
    from test_medical_procedure import install_item,payload,apply
    LocationCombatState.query.one().status='idle'
    _,_,item=install_item(pair,'Антирад','Ампула. -10 радиации.',quantity=1)
    character=pair['actor_character'];data=deepcopy(character.data)
    data['equipment']=sponge_data()['equipment'];data['health'].update(radiation=3,current=700)
    character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint',lambda *_:20)
    response=apply(client,pair,auth_headers,payload(pair,item,{'kind':'self'}))
    assert response.status_code==200,response.json
    assert character.data['health']['radiation']==0 and character.data['health']['current']==700


def test_global_time_respects_character_activity(client,pair,auth_headers):
    from test_regression_time_stress import advance_global_minutes
    LocationCombatState.query.one().status='idle'
    character=pair['actor_character'];data=deepcopy(character.data)
    data['equipment']=sponge_data()['equipment'];data['health'].update(radiation=3,current=700)
    character.data=data;character.time_active=False;db.session.commit()
    advance_global_minutes(client,pair,auth_headers,1)
    assert character.data['health']['radiation']==3 and character.data['health']['current']==700
    character.time_active=True;db.session.commit()
    advance_global_minutes(client,pair,auth_headers,1)
    assert character.data['health']['radiation']==0 and character.data['health']['current']==670


def test_real_combat_rounds_advance_the_same_minute_clock(client,pair,auth_headers):
    from test_regression_time_stress import end_turn
    character=pair['actor_character'];data=deepcopy(character.data)
    data['equipment']=sponge_data()['equipment'];data['health'].update(radiation=3,current=700)
    character.data=data;db.session.commit()
    for _ in range(9):
        end_turn(client,pair,auth_headers,'actor');end_turn(client,pair,auth_headers,'target')
    assert character.data['health']['radiation']==3 and character.data['health']['current']==700
    end_turn(client,pair,auth_headers,'actor');end_turn(client,pair,auth_headers,'target')
    assert character.data['health']['radiation']==0 and character.data['health']['current']==670
