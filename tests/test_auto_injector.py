"""Физический препарат, оплата и общая медицинская система автоинъектора."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import ConsumableUse,LocationCombatState
from app.services.equipment_modifications import make_record,install
from app.services.equipment_care import preserve
from app.services.combat import CombatService
from test_consumable_use import pair
from test_medical_procedure import install_item


def setup_injector(pair,rule='armor-a15'):
    _,_,item=install_item(pair,'Тестовый анальгетик','Ампула. -2 уровня боли.',quantity=2,medicine=0,pain=3)
    character=pair['actor_character'];data=deepcopy(character.data)
    data.setdefault('equipment',{})['armor']={'id':'injector-armor','name':'Лёгкая броня','category':'armor',
        'modifications':[make_record(rule)]}
    character.data=data;db.session.commit();return item


def change(client,pair,auth_headers,operation='load',source=None,revision=None):
    character=pair['actor_character']
    payload={'operation':operation,'character_revision':character.revision if revision is None else revision,'armor_id':'injector-armor'}
    if operation=='load':payload['source']=source or {'path':['inventory','backpack',0],'snapshot':deepcopy(character.data['inventory']['backpack'][0])}
    return client.post(f'/lobbies/characters/{character.id}/auto-injector',headers=auth_headers(pair['actor_user']),json=payload)


def dose(client,pair,auth_headers,payment='free',**extra):
    character=pair['actor_character'];item=character.data['equipment']['armor']['installedModules'][0]
    return client.post(f'/lobbies/characters/{character.id}/medical-procedure',headers=auth_headers(pair['actor_user']),json={
        'operation_id':'inject-dose','target_character_id':character.id,
        'source':{'path':['equipment','armor','installedModules',0],'snapshot':deepcopy(item)},
        'application':{'kind':'self'},'auto_injector_payment':payment,**extra})


@pytest.mark.parametrize('rule,payment',[('armor-a15','free'),('armor-a31','action')])
def test_load_then_inject_uses_one_physical_dose_without_medicine_roll(client,pair,auth_headers,monkeypatch,rule,payment):
    item=setup_injector(pair,rule);actor=pair['actor_location'];actor.action_points_current=8;actor.free_actions_current=1;db.session.commit()
    first=change(client,pair,auth_headers);assert first.status_code==200,first.json
    data=pair['actor_character'].data;loaded=data['equipment']['armor']['installedModules'][0]
    assert data['inventory']['backpack'][0]['quantity']==1 and loaded['quantity']==1
    assert loaded['id']!=item['id'] and actor.action_points_current==3
    monkeypatch.setattr('app.services.medical_procedure.random.randint',lambda *_:pytest.fail('Автоинъектор не должен бросать Медицину'))
    result=dose(client,pair,auth_headers,payment);assert result.status_code==200,result.json
    assert result.json['medical_result']['success'] and result.json['medical_result']['roll'] is None
    assert pair['actor_character'].data['health']['painLevel']==1
    assert pair['actor_character'].data['equipment']['armor']['installedModules']==[]
    assert actor.action_points_current==(3 if payment=='free' else 2)
    assert actor.free_actions_current==(0 if payment=='free' else 1)
    assert ConsumableUse.query.count()==1


def test_load_failure_rolls_back_and_stale_reload_cannot_spend_twice(client,pair,auth_headers):
    setup_injector(pair);actor=pair['actor_location'];actor.action_points_current=4;db.session.commit()
    before=deepcopy(actor.character.data)
    assert change(client,pair,auth_headers).status_code==400
    assert actor.character.data['inventory']==before['inventory'] and actor.action_points_current==4
    actor.action_points_current=10;db.session.commit();revision=actor.character.revision
    assert change(client,pair,auth_headers).status_code==200
    assert change(client,pair,auth_headers,revision=revision).status_code==409
    assert actor.action_points_current==5


def test_unload_returns_same_whole_syringe_and_costs_three(client,pair,auth_headers):
    setup_injector(pair);actor=pair['actor_location'];actor.action_points_current=10;db.session.commit()
    assert change(client,pair,auth_headers).status_code==200
    loaded=deepcopy(actor.character.data['equipment']['armor']['installedModules'][0])
    response=change(client,pair,auth_headers,'unload');assert response.status_code==200,response.json
    result=actor.character.data['inventory']['pockets'][-1]
    assert result['id']==loaded['id'] and 'slotType' not in result
    assert actor.action_points_current==2


def test_injection_rejects_no_resource_other_patient_and_false_source(client,pair,auth_headers):
    setup_injector(pair);actor=pair['actor_location'];actor.action_points_current=5;actor.free_actions_current=0;db.session.commit()
    assert change(client,pair,auth_headers).status_code==200
    assert dose(client,pair,auth_headers,'free').status_code==400
    assert dose(client,pair,auth_headers,'action').status_code==400
    actor.action_points_current=2;db.session.commit()
    assert dose(client,pair,auth_headers,'action',target_character_id=pair['target_character'].id).status_code==400
    false_source={'path':['inventory','backpack',0],'snapshot':deepcopy(actor.character.data['inventory']['backpack'][0])}
    assert dose(client,pair,auth_headers,'action',source=false_source).status_code==400
    assert actor.action_points_current==2 and ConsumableUse.query.count()==0


def test_armor_saving_preserves_injection_and_weight_without_copy(client,pair,auth_headers):
    setup_injector(pair);actor=pair['actor_location'];actor.action_points_current=10;db.session.commit()
    assert change(client,pair,auth_headers).status_code==200
    data=deepcopy(actor.character.data);module=data['equipment']['armor']['installedModules'][0];module['weight']=.25
    before=CombatService._inventory_weight_details(data)['total_weight']
    forged=deepcopy(data);forged['equipment']['armor']['installedModules']=[];preserve(data,forged)
    assert forged['equipment']['armor']['installedModules']==[module]
    backpack=deepcopy(data);armor=backpack['equipment'].pop('armor');backpack['inventory']['backpack'].append(armor)
    assert CombatService._inventory_weight_details(backpack)['total_weight']==before
    preserve(data,backpack);assert backpack['inventory']['backpack'][-1]['installedModules']==[module]


def test_cannot_replace_loaded_injector_and_cannot_use_out_of_turn(client,pair,auth_headers):
    from app.services.exceptions import ValidationError
    setup_injector(pair);actor=pair['actor_location'];actor.action_points_current=10;db.session.commit()
    assert change(client,pair,auth_headers).status_code==200
    with pytest.raises(ValidationError):install(deepcopy(actor.character.data['equipment']['armor']),make_record('armor-a14'),'armor-a15')
    state=LocationCombatState.query.filter_by(location_id=pair['location'].id).one();state.current_location_character_id=pair['target_location'].id;db.session.commit()
    assert dose(client,pair,auth_headers).status_code==403
    assert change(client,pair,auth_headers,'unload').status_code==403


def test_injector_out_of_combat_needs_no_action_points_and_rejects_duplicate(client,pair,auth_headers):
    setup_injector(pair);state=LocationCombatState.query.filter_by(location_id=pair['location'].id).one();state.status='finished';db.session.commit()
    actor=pair['actor_location'];before=actor.action_points_current
    assert change(client,pair,auth_headers).status_code==200
    character=pair['actor_character'];item=deepcopy(character.data['equipment']['armor']['installedModules'][0])
    response=dose(client,pair,auth_headers,'action');assert response.status_code==200,response.json
    assert actor.action_points_current==before
    retry={'operation_id':'inject-dose','auto_injector_payment':'action','application':{'kind':'self'},
        'source':{'path':['equipment','armor','installedModules',0],'snapshot':item}}
    assert client.post(f'/lobbies/characters/{character.id}/medical-procedure',headers=auth_headers(pair['actor_user']),json=retry).status_code in {400,409}
    assert ConsumableUse.query.count()==1 and character.data['health']['painLevel']==1


def test_injector_stops_selected_bleeding_through_existing_medical_procedure(client,pair,auth_headers,monkeypatch):
    setup_injector(pair)
    effect={'id':'injector-bleeding','type':'bleeding_external_light','area':'leftArm','source':'cut'}
    _,profile,item=install_item(pair,'Оксицел','Ампула. Останавливает Слабое кровотечение. Бонус медикамента +4.',quantity=1,effects=[effect])
    actor=pair['actor_location'];actor.action_points_current=8;actor.free_actions_current=1;db.session.commit()
    assert change(client,pair,auth_headers).status_code==200
    assert actor.character.data['equipment']['armor']['installedModules'][0]['id']==item['id']
    from test_medical_procedure import selected_bleeding
    response=dose(client,pair,auth_headers,'free',application=selected_bleeding(profile,effect))
    assert response.status_code==200,response.json
    assert response.json['medical_result']['roll'] is None
    assert not any(e.get('id')==effect['id'] and not e.get('closed') for e in actor.character.data['health']['effects'])


def test_injector_rejects_noninjectable_consumable_without_losing_it(client,pair,auth_headers):
    setup_injector(pair)
    _,_,item=install_item(pair,'Тестовая еда','Продукты. -1 уровень истощения.',quantity=1)
    before=deepcopy(pair['actor_character'].data['inventory'])
    response=change(client,pair,auth_headers)
    assert response.status_code==400,response.json
    assert pair['actor_character'].data['inventory']==before
