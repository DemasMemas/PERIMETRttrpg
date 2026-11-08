"""Воздушный наполнитель отменяет побочный эффект, а не урон аномалии."""
from copy import deepcopy
import pytest
from app.services.combat import CombatService as C
from app.services.equipment_modifications import make_record,supported,definition
from app.services.effects import sync_health_derived_statuses
from test_combat_anomalies import _combat_with_anomaly
from test_grenade_rules import grenade_game


def armor(enabled=True):
    return {'category':'armor','attributes':{'modification_category':'Аномальная'},
        'protectionDisabled':not enabled,'modifications':[make_record('armor-d7')]}


@pytest.mark.parametrize('key,effect', [('banya','burning'),('akkumulyator','anomaly_electric_penalty'),
    ('otboynik','bleeding_external_light'),('varevo','fracture'),('ekho','anomaly_will_penalty')])
def test_anomaly_category_effect_cancelled_without_cancelling_damage(app,key,effect):
    _,scene,character,actor,_=_combat_with_anomaly(key)
    data=deepcopy(character.data);data['equipment']={'armor':armor()};character.data=data
    active=C._enter_anomaly(actor,C._anomalies_at_tile(scene,2,2)[0],1)
    result=C._apply_anomaly_exposure(actor,active,1,round_number=1)
    assert result.get('category_effect_cancelled')
    assert not any(e['type']==effect for e in character.data['health']['effects'])
    if key in {'banya','akkumulyator','otboynik'}:assert result['damage']>0


@pytest.mark.parametrize('worn',[True,False])
def test_cancellation_only_while_worn_and_enabled(app,worn):
    _,scene,character,actor,_=_combat_with_anomaly('akkumulyator')
    data=deepcopy(character.data)
    if worn:data['equipment']={'armor':armor(False)}
    else:data['inventory']={'pockets':[armor()]}
    character.data=data;active=C._enter_anomaly(actor,C._anomalies_at_tile(scene,2,2)[0],1)
    result=C._apply_anomaly_exposure(actor,active,1,round_number=1)
    assert 'category_effect_cancelled' not in result
    assert any(e['type']=='anomaly_electric_penalty' for e in character.data['health']['effects'])


def test_bleeding_severity_reduced_per_wound_stack_without_removing_wounds():
    health={'effects':[{'type':'bleeding_external_light','stacks':2},
        {'type':'bleeding_internal_medium','stacks':3}]}
    sync_health_derived_statuses(health);data={'health':health,'equipment':{'armor':armor()}}
    before=deepcopy(health['effects']);result=C._bleeding_check_profile(data)
    assert health['bleeding']['totalSeverity']==11
    assert result['equipmentSeverityReduction']==5 and result['severity']==6
    assert health['effects']==before
    data['equipment']['armor']['protectionDisabled']=True
    assert C._bleeding_check_profile(data)['severity']==11


def test_rule_is_installable_and_trauma_disadvantage_uses_existing_engine():
    assert supported(definition('armor-d7'))
    record=make_record('armor-d7')
    assert record['rules']['incoming_trauma_disadvantage'] is True
    assert record['rules']['anomaly_effect_cancel'] is True
    assert make_record('armor-d7',gunsmith=True)['rules']['bleeding_per_effect_reduction']==1.5


@pytest.mark.parametrize('key', ['zarosli','solyariy','mogilshchik'])
def test_category_effect_encoded_in_profile_is_also_cancelled(app,key):
    _,scene,character,actor,_=_combat_with_anomaly(key)
    character.data={**character.data,'equipment':{'armor':armor()}}
    active=C._enter_anomaly(actor,C._anomalies_at_tile(scene,2,2)[0],1)
    result=C._apply_anomaly_exposure(actor,active,1,round_number=1)
    assert result['category_effect_cancelled'] in {'radiation','chemical'}
    assert 'pain' not in result
    if key=='mogilshchik':assert result['exhaustion']==1
    else:assert 'exhaustion' not in result
    if key!='mogilshchik':assert result['radiation']['received']>0


def test_existing_other_effects_are_not_removed_by_new_exposure(app):
    _,scene,character,actor,_=_combat_with_anomaly('akkumulyator')
    character.data={**character.data,'equipment':{'armor':armor()},'health':{
        **character.data['health'],'effects':[{'type':'anomaly_electric_penalty','source':'earlier','remaining':1}]}}
    active=C._enter_anomaly(actor,C._anomalies_at_tile(scene,2,2)[0],1)
    C._apply_anomaly_exposure(actor,active,1,round_number=1)
    effects=[e for e in character.data['health']['effects'] if e['type']=='anomaly_electric_penalty']
    assert len(effects)==1 and effects[0]['source']=='earlier'


def test_actual_hit_uses_two_trauma_chance_rolls_and_keeps_lower(grenade_game,monkeypatch):
    from app.extensions import db
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['equipment']={'armor':armor()};actor.character.data=data;db.session.commit()
    rolls=iter([99,1]);drawn=[]
    def roll(low,high):
        if high==20:return 20
        assert (low,high)==(1,100)
        value=next(rolls);drawn.append(value);return value
    monkeypatch.setattr('app.services.combat.random.randint',roll)
    C._apply_attack_damage(actor,20,'chest',{},prepared_trauma_rolls=[1])
    assert drawn==[99,1] and actor.character.data['health']['lastTraumas']==[]
