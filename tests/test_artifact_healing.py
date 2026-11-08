"""Множитель артефакта должен менять фактическое лечение, а не только профиль."""
from copy import deepcopy
import pytest
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LocationCombatState
from app.services.world_rules import artifact_catalog
from app.services.effects import apply_effect_to_health,apply_periodic_effects_to_health,advance_timed_effects
from test_consumable_use import pair
from test_general_consumable import install_consumable,apply_general


def with_artifacts(names):
    rules={rule['name']:rule for rule in artifact_catalog()}
    return {'health':{'current':100,'max':700,'radiation':0,'effects':[],
                      'zones':{'leftArm':{'current':20,'max':90}}},
            'equipment':{'armor':{'containers':[{'item':{'id':'heal-'+str(index),'name':name,
                'category':'artifact','attributes':deepcopy(rules[name])}} for index,name in enumerate(names)]}}}


@pytest.mark.parametrize('name,multiplier',[
    ('Угольная печать',.75),('Пепельная роза',.75),('Пиявка',.75),
    ('Огненная вена',.5),('Чернобыльская губка',.5),
])
def test_real_artifact_changes_healing_pool_and_zone_budget(name,multiplier):
    data=with_artifacts([name]);apply_effect_to_health(data['health'],{'type':'heal','value':80},character_data=data)
    assert data['health']['current']==100+80*multiplier
    assert data['health']['zones']['leftArm']['current']==20+80*multiplier


def test_regeneration_uses_current_equipment_each_tick_and_keeps_original_magnitude():
    data=with_artifacts(['Чернобыльская губка']);effect={'type':'regeneration','value':40,'tick':'turn_end','remaining':3}
    apply_periodic_effects_to_health(data['health'],[effect],character_data=data)
    assert data['health']['current']==120 and effect['value']==40
    data['equipment']['armor']['containers']=[]
    apply_periodic_effects_to_health(data['health'],[effect],character_data=data)
    assert data['health']['current']==160 and effect['value']==40


def test_delayed_healing_scales_at_expiry_and_does_not_change_promised_amount():
    data=with_artifacts(['Чернобыльская губка']);effect={'id':'delayed-heal','type':'delayed_adjustment',
        'remaining':1,'remaining_seconds':1,'time_unit':'second','tick':'time_elapsed','onExpire':[{'field':'current','delta':80}]}
    data['health']['effects']=[effect]
    data['health']['effects']=advance_timed_effects(data['health'],data['health']['effects'],1,character_data=data)
    assert data['health']['current']==140 and effect['onExpire'][0]['delta']==80


def test_surgical_recovery_and_reanimation_scale_only_recovered_health():
    from app.services.medical_procedure import _restore_zone_and_pool
    from app.services.equipment_care import consume_reanimation
    from app.services.equipment_modifications import make_record
    data=with_artifacts(['Чернобыльская губка'])
    result=_restore_zone_and_pool(data['health'],'leftArm',80,character_data=data)
    assert result['recovered']==30 and data['health']['current']==130 and data['health']['zones']['leftArm']['current']==50
    data['equipment']['armor']['modifications']=[make_record('armor-d31')]
    assert consume_reanimation(data)
    assert data['health']['current']==135 and data['health']['zones']['leftArm']['current']==55


def test_multiple_artifact_factors_apply_once_and_removal_restores_normal_healing():
    data=with_artifacts(['Угольная печать','Чернобыльская губка'])
    apply_effect_to_health(data['health'],{'type':'heal','value':80},character_data=data)
    assert data['health']['current']==130
    data['equipment']['armor']['containers']=[]
    apply_effect_to_health(data['health'],{'type':'heal','value':80},character_data=data)
    assert data['health']['current']==210


def test_custom_healing_multiplier_does_not_exceed_part_maximum():
    from app.services.medical_procedure import _restore_zone_and_pool
    data=with_artifacts(['Чернобыльская губка'])
    data['equipment']['armor']['containers'][0]['item']['attributes']['negative_effect']='Восстановление здоровья из любых источников 2х'
    result=_restore_zone_and_pool(data['health'],'leftArm',80,character_data=data)
    assert result['after']==90 and result['recovered']==70 and data['health']['current']==170


def test_periodic_artifact_healing_is_reduced_but_health_loss_is_not():
    from app.services.artifact_effects import apply_artifact_world_movement
    healing=next(rule['name'] for rule in artifact_catalog() if 'Общее здоровье +25 за перемещение' in rule['positive_effect'])
    data=with_artifacts([healing,'Чернобыльская губка'])
    item=data['equipment']['armor']['containers'][0]['item']
    result=apply_artifact_world_movement(data,artifact=item)
    assert result['health_delta']==12.5 and data['health']['current']==112.5
    data=with_artifacts(['Штормовой камень','Чернобыльская губка'])
    result=apply_artifact_world_movement(data,artifact=data['equipment']['armor']['containers'][0]['item'])
    assert result['health_delta']==-50 and data['health']['current']==50


@pytest.mark.parametrize('mode,expected',[('rest',126.25),('sleep',362.5)])
def test_gm_rest_uses_artifact_healing_once(client,pair,auth_headers,mode,expected):
    LocationCombatState.query.one().status='inactive'
    data=deepcopy(pair['actor_character'].data);data['equipment']=with_artifacts(['Угольная печать'])['equipment']
    data['health']['current']=100;data['health'].setdefault('combatMeta',{})['sleepMode']='bed'
    pair['actor_character'].data=data;flag_modified(pair['actor_character'],'data');db.session.commit()
    response=client.post(f"/lobbies/{pair['lobby'].id}/rest",headers=auth_headers(pair['actor_user']),
        json={'type':mode,'character_ids':[pair['actor_character'].id]})
    assert response.status_code==200,response.json
    assert pair['actor_character'].data['health']['current']==expected


def test_medicine_http_uses_worn_artifact_once(client,pair,auth_headers):
    LocationCombatState.query.one().status='inactive'
    item,_=install_consumable(pair,'Тестовый напиток лечения','+80 ХП.',quantity=1,health={'current':100})
    data=deepcopy(pair['actor_character'].data);data['equipment']=with_artifacts(['Чернобыльская губка'])['equipment']
    pair['actor_character'].data=data;flag_modified(pair['actor_character'],'data');db.session.commit()
    response=apply_general(client,pair,auth_headers,item,operation='artifact-heal-once')
    assert response.status_code==200,response.json
    assert pair['actor_character'].data['health']['current']==140
    repeated=apply_general(client,pair,auth_headers,item,operation='artifact-heal-once')
    assert repeated.status_code==409 and pair['actor_character'].data['health']['current']==140
