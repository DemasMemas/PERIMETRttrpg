"""Каталог, совместимость, серверная оплата и обслуживание протезов."""
from copy import deepcopy

import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCombatState,DeferredCombatAction
from app.services.combat import CombatService
from app.services.prostheses import advance,limb,discharge,missing_templates
from app.services.prosthesis_catalog import PROFILES
from app.services.weapon_grip import functional_arms
from test_grenade_rules import grenade_game


def setup(case,name='Навесная голень со стопой',zone='leftLeg',extent='elbow_or_knee'):
    actor=case['actor_location'];data=deepcopy(actor.character.data)
    data['health']['zones']={zone:{'current':0,'max':100}}
    data['health']['effects']=[{'id':'lost','type':'amputation','area':zone,'active':True,'loss_extent':extent}]
    data['inventory']={'pockets':[{'id':'prosthesis-test','name':name,'quantity':1,
        'attributes':{'prosthesis':deepcopy(PROFILES[name])}}],'backpack':[]}
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit();return actor


def outside(case):
    state=LocationCombatState.query.filter_by(location_id=case['actor_location'].location_id).one();state.status='idle';db.session.commit()


def operation(client,case,headers,**payload):
    actor=case['actor_location']
    return client.post(f'/lobbies/characters/{actor.character_id}/prostheses',headers=headers(case['actor_user']),
        json={'character_revision':actor.character.revision,'zone':'leftLeg','path':['inventory','pockets',0],**payload})


def test_catalog_has_twenty_profiles_and_preserves_existing():
    entries=missing_templates([])
    assert len(entries)==20 and len({item['id'] for item in entries})==20
    assert len(missing_templates(entries))==0
    assert PROFILES['Кисть крюк']['attack']['types']==['Колющий','Вспарывающий']
    assert PROFILES['Кисть клинок']['attack']['damage']==85
    assert all((item['weight'],item['volume'])=={
        'hand_or_foot':(1,1),'elbow_or_knee':(2,4),'entire_limb':(3,10)
    }[item['attributes']['prosthesis']['extent']] for item in entries)


def test_outside_install_power_replaces_missing_leg_penalty_and_preserves_instance(client,grenade_game,auth_headers):
    actor=setup(grenade_game);outside(grenade_game)
    assert CombatService._disabled_limb_penalties(actor.character.data)['movement']==6
    assert operation(client,grenade_game,auth_headers,operation='install').status_code==200
    assert not actor.character.data['inventory']['pockets']
    assert limb(actor.character.data,'leftLeg') is None
    data=deepcopy(actor.character.data);data['inventory']['pockets']=[{'name':'Батарейка','quantity':2}]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    result=operation(client,grenade_game,auth_headers,operation='power',slot='base')
    assert result.status_code==200,result.json
    assert limb(actor.character.data,'leftLeg')['efficiency']==-3
    assert CombatService._disabled_limb_penalties(actor.character.data)['movement']==3
    assert not CombatService._disabled_limb_penalties(actor.character.data)['sprint_blocked']
    assert operation(client,grenade_game,auth_headers,operation='remove').status_code==200
    assert actor.character.data['inventory']['pockets'][-1]['id']=='prosthesis-test'
    assert CombatService._disabled_limb_penalties(actor.character.data)['movement']==6


@pytest.mark.parametrize('name', ['Навесная голень со стопой', 'Фиксированная голень со стопой'])
def test_prosthesis_rechargeable_cells_keep_identity_after_depletion(client, grenade_game, auth_headers, name):
    from app.services.power_cells import NAME, percentage

    actor = setup(grenade_game, name=name)
    outside(grenade_game)
    data = deepcopy(actor.character.data)
    item = data['inventory']['pockets'].pop()
    data['health']['prostheses'] = {'leftLeg': {'base': item}}
    count = PROFILES[name]['batteries']
    data['inventory']['pockets'] = [{'id': f'cell-{index}', 'name': NAME, 'quantity': 1,
        'attributes': {'rechargeable_battery': True, 'charge_percent': 50}} for index in range(count)]
    actor.character.data = data
    db.session.commit()
    paths = [['inventory', 'pockets', index] for index in range(count)]
    rejected = operation(client, grenade_game, auth_headers, operation='power', slot='base', battery_paths=paths[:-1])
    assert rejected.status_code == 400
    assert len(actor.character.data['inventory']['pockets']) == count
    if count > 1:
        duplicate = operation(client, grenade_game, auth_headers, operation='power', slot='base',
            battery_paths=[paths[0]] * count)
        assert duplicate.status_code == 400
    response = operation(client, grenade_game, auth_headers, operation='power', slot='base', battery_paths=paths)
    assert response.status_code == 200, response.json
    data = deepcopy(actor.character.data)
    item = data['health']['prostheses']['leftLeg']['base']
    assert item['energy_seconds'] == 43200
    assert data['inventory']['pockets'] == []
    advance(data, 21600)
    assert all(percentage(cell) == 25 for cell in item['rechargeableCells'])
    advance(data, 21600)
    assert 'rechargeableCells' not in item
    assert [(cell['id'], percentage(cell)) for cell in data['inventory']['pockets']] == [
        (f'cell-{index}', 0) for index in range(count)]


@pytest.mark.parametrize('extent',['entire_limb','hand_or_foot',None])
def test_wrong_extent_cannot_install_or_consume(client,grenade_game,auth_headers,extent):
    actor=setup(grenade_game,extent=extent);outside(grenade_game)
    response=operation(client,grenade_game,auth_headers,operation='install')
    assert response.status_code==400
    assert actor.character.data['inventory']['pockets'][0]['id']=='prosthesis-test'


def test_strap_install_combat_cost_and_remove(client,grenade_game,auth_headers):
    actor=setup(grenade_game);before=actor.action_points_current
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'prosthesis',
        equipment_operation='install',equipment_slot='leftLeg',item_path=['inventory','pockets',0])
    assert actor.action_points_current==before-10 and result['equipment_change']['action_points']==10
    CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'prosthesis',equipment_operation='remove',equipment_slot='leftLeg')
    assert actor.action_points_current==before-13


def prosthetic_data(name='Навесная голень со стопой',zone='leftLeg',**changes):
    item={'id':'maintenance','name':name,'durability':PROFILES[name]['durability'],'energy_seconds':86400,**changes}
    return {'health':{'effects':[{'type':'amputation','area':zone,'active':True}],
        'prostheses':{zone:{'base':item}}}}


def test_power_and_missed_lubrications_accumulate_and_never_repair():
    data=prosthetic_data();item=data['health']['prostheses']['leftLeg']['base']
    advance(data,86400)
    assert item['energy_seconds']==0 and limb(data,'leftLeg') is None
    advance(data,6*86400)
    assert item['missed_lubrications']==1 and item['durability']==25
    advance(data,7*86400)
    assert item['missed_lubrications']==2 and item['durability']==0 and item['broken']


def test_clock_rewind_cannot_restore_charge_or_lubrication():
    data=prosthetic_data(energy_seconds=42,lubrication_elapsed=500);before=deepcopy(data)
    assert not advance(data,-3600)
    assert data==before


def test_installed_mass_survives_equipping_and_virtual_attack_is_not_counted():
    from app.services.prostheses import sync_weapons
    data=prosthetic_data('Навесная база предплечья','leftArm')
    data['health']['prostheses']['leftArm']['hand']={'id':'hook','name':'Кисть крюк','durability':25}
    sync_weapons(data)
    assert CombatService._inventory_weight_details(data)['total_weight']==3
    base=data['health']['prostheses']['leftArm']['base']
    assert base['weight']==2 and base['volume']==4 and base['energy_seconds']==86400
    assert len(data['weapons'])==1 and CombatService._item_total_weight(data['weapons'][0])==0


@pytest.mark.parametrize('zone,expected', [('leftArm',['плечо','плечо','предплечье','предплечье','кисть','кисть']),
    ('rightLeg',['бедро','бедро','голень','голень','стопа','стопа'])])
def test_d6_parts_keep_living_stump_and_pick_only_replaced_segments(zone,expected):
    from app.services.prostheses import hit_part
    name='Навесная база предплечья' if zone.endswith('Arm') else 'Навесная голень со стопой'
    data=prosthetic_data(name,zone)
    if zone.endswith('Arm'):data['health']['prostheses'][zone]['hand']={'id':'hook','name':'Кисть крюк','durability':25}
    hits=[hit_part(data,zone,roll) for roll in range(1,7)]
    assert [hit['label'] for hit in hits]==expected
    assert [hit['mechanical'] for hit in hits]==[False,False,True,True,True,True]
    if zone.endswith('Arm'):assert hits[4]['item']['id']=='hook' and hits[2]['item']['id']=='maintenance'


def test_prosthetic_hit_adds_armor_but_ignores_artifact_and_never_bleeds(grenade_game,monkeypatch):
    from app.services.prostheses import hit_part,sync_weapons
    target=grenade_game['target_location'];data=prosthetic_data('Навесная база предплечья','leftArm')
    data['health'].update(current=600,max=700,painLevel=0,stress=0,zones={'leftArm':{'current':0,'max':100}})
    data['health']['prostheses']['leftArm']['hand']={'id':'hook','name':'Кисть крюк','durability':25}
    data['equipment']={'armor':{'name':'Броня рук','attributes':{'protection':{'physical':50},'covers':['left_arm']}}}
    target.character.data=data;flag_modified(target.character,'data');db.session.commit()
    original=deepcopy(target.character.data['health']['effects'])
    monkeypatch.setattr('app.services.combat.artifact_passive_profile',lambda data:{'protection':{'physical':30}})
    hit=hit_part(target.character.data,'left_arm',5)
    assert CombatService._target_armor(target.character.data,'left_arm',limb_hit=hit)[0]==40
    assert CombatService._target_armor(target.character.data,'left_arm',limb_hit=hit_part(target.character.data,'left_arm',3))[0]==50
    assert CombatService._target_armor(target.character.data,'left_arm')[0]==70
    health=CombatService._apply_attack_damage(target,40,'left_arm',{'damage_type':'bullet'},limb_hit=hit)
    assert health['current']==600 and health['painLevel']==0 and health['stress']==0
    assert health['effects']==original
    assert health['_attackOutcome']['prosthesis_damage']['broken']
    assert health['_attackOutcome']['applied_damage']==0
    assert not limb(target.character.data,'leftArm')
    assert health['prostheses']['leftArm']['base']['durability']==50
    assert health['prostheses']['leftArm']['hand']['durability']==0
    assert health['effects'][0].get('loss_extent') is None


def test_real_bullet_hit_damages_prosthesis_and_records_d6_without_hurting_body(grenade_game,monkeypatch):
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    data=prosthetic_data();data['health'].update(current=600,max=700,painLevel=0,stress=0)
    data['health']['effects'][0]['loss_extent']='elbow_or_knee'
    target.character.data=data;flag_modified(target.character,'data');db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:6 if b==6 else min(b,max(a,10)))
    result=CombatService._resolve_attack(target,actor,{'fire_mode':'aimed','hit_difficulty':1,'target_distance':1,'round_number':1},
        forced_roll=20,aimed_zone='left_leg',ignore_cover=True,profile_override={'damage':100,'armor_piercing':100,'damage_type':'bullet'})
    assert result['hit'] and result['limb_hit']=={'roll':6,'label':'стопа','mechanical':True}
    assert result['prosthesis_damage']['damage']==75 and result['prosthesis_damage']['broken']
    assert result['damage']==0 and result['health']==600 and result['bleeding_check'] is None
    assert not result['bleedings'] and not result['additional_traumas']
    assert target.character.data['health']['painLevel']==0
    text=CombatService.format_attack_summary({'character':{'name':'Стрелок'},'attack':{'results':[result]}})
    assert 'd6 6' in text and 'слом' in text and '75' in text


def surgical_setup(case,integrated=False):
    from app.models.templates import ItemTemplate
    from app.services.consumable_effects import parse_consumable_effects
    name='Интегрированный протез голени' if integrated else 'Фиксированная голень со стопой'
    actor=setup(case,name);outside(case);data=deepcopy(actor.character.data)
    data.setdefault('skills',{}).setdefault('other',{}).update(medicine={'base':20},engineering={'base':20})
    for index,(name,description,quantity,uses) in enumerate([
        ('Хирургический набор "Хирург"','Восстанавливает часть тела. Она имеет 30 здоровья. 5 использований.',2,5),
        ('Морфий','Снижает боль на 5 уровней.',10,1),('Подавитель эмоций','Действует 6 перемещений. -25 Пси-состояний.',1,1)]):
        profile=parse_consumable_effects(name+'. '+description)
        template=ItemTemplate(name=name,category='consumable',description=description,attributes={'uses':uses,'consumable':profile})
        db.session.add(template);db.session.flush()
        data['inventory']['pockets'].append({'id':f'surgical-{index}','templateId':template.id,'name':name,
            'category':'consumable','quantity':quantity,'uses':uses,'maxUses':uses})
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit();return actor


@pytest.mark.parametrize('integrated,seconds',[(False,3600),(True,28800)])
def test_surgery_consumes_supplies_only_once_and_finishes_by_game_time(client,grenade_game,auth_headers,monkeypatch,integrated,seconds):
    from app.services.prostheses import advance_lobby
    from app.services.prosthesis_surgery import busy
    from app.services.character_interaction import CharacterInteractionService
    actor=surgical_setup(grenade_game,integrated)
    health_before=deepcopy(actor.character.data['health'])
    body={'operation':'surgery_install','operation_id':'surgical-success','target_revision':actor.character.revision}
    response=operation(client,grenade_game,auth_headers,**body)
    assert response.status_code==200,response.json
    assert busy(actor.character.data) and CharacterInteractionService.movement_locked(actor.id)
    assert not installed_for_test(actor)
    items=actor.character.data['inventory']['pockets']
    assert sum(item['quantity'] for item in items if item['name']=='Морфий')==(0 if integrated else 7)
    assert actor.character.data['health']['painLevel']==health_before['painLevel']
    assert actor.character.data['health']['effects']==health_before['effects']
    assert operation(client,grenade_game,auth_headers,**body).status_code==409
    monkeypatch.setattr('app.services.prosthesis_surgery.random.randint',lambda *_:20)
    advance_lobby(grenade_game['lobby'],seconds-1);db.session.commit()
    assert not installed_for_test(actor)
    advance_lobby(grenade_game['lobby'],1);db.session.commit()
    assert not busy(actor.character.data) and installed_for_test(actor)['id']=='prosthesis-test'
    assert actor.character.data['health']['combatMeta']['prosthesisSurgeryResult']['success']
    result=deepcopy(actor.character.data)
    advance_lobby(grenade_game['lobby'],0);assert actor.character.data==result
    assert db.session.get(DeferredCombatAction,'surgical-success').status=='completed'


def installed_for_test(actor):
    return actor.character.data.get('health',{}).get('prostheses',{}).get('leftLeg',{}).get('base')


@pytest.mark.parametrize('outcome', ['success','failure','cancel'])
def test_clamp_removal_reuses_surgery_and_only_detaches_after_success(client,grenade_game,auth_headers,monkeypatch,outcome):
    from app.services.prostheses import advance_lobby
    actor=surgical_setup(grenade_game)
    monkeypatch.setattr('app.services.prosthesis_surgery.random.randint',lambda *_:20)
    assert operation(client,grenade_game,auth_headers,operation='surgery_install',operation_id='install-before-removal',
        target_revision=actor.character.revision).status_code==200
    advance_lobby(grenade_game['lobby'],3600);db.session.commit()
    before=deepcopy(installed_for_test(actor));assert before['id']=='prosthesis-test'
    response=operation(client,grenade_game,auth_headers,operation='surgery_remove',operation_id='remove-clamp',
        target_revision=actor.character.revision)
    assert response.status_code==200,response.json
    assert installed_for_test(actor)['id']==before['id']
    assert sum(i['quantity'] for i in actor.character.data['inventory']['pockets'] if i['name']=='Морфий')==4
    monkeypatch.setattr('app.services.prosthesis_surgery.random.randint',lambda *_:1 if outcome=='failure' else 20)
    if outcome=='cancel':
        assert operation(client,grenade_game,auth_headers,operation='cancel_surgery').status_code==200
    else:
        advance_lobby(grenade_game['lobby'],3599);db.session.commit()
        assert installed_for_test(actor)['id']==before['id']
        advance_lobby(grenade_game['lobby'],1);db.session.commit()
    items=[i for i in actor.character.data['inventory']['pockets'] if i.get('id')==before['id']]
    if outcome=='success':
        assert not installed_for_test(actor)
        assert len(items)==1 and items[0]['durability']==before['durability']
    else:assert installed_for_test(actor)['id']==before['id'] and not items
    snapshot=deepcopy(actor.character.data)
    advance_lobby(grenade_game['lobby'],3600);db.session.commit()
    assert sum(i.get('id')==before['id'] for i in actor.character.data['inventory']['pockets'])==len(items)
    assert actor.character.data['health']['combatMeta']['prosthesisSurgeryResult']['operation']=='surgery_remove'


def test_integrated_prosthesis_cannot_be_removed_by_forged_surgery(client,grenade_game,auth_headers,monkeypatch):
    from app.services.prostheses import advance_lobby
    actor=surgical_setup(grenade_game,True)
    monkeypatch.setattr('app.services.prosthesis_surgery.random.randint',lambda *_:20)
    assert operation(client,grenade_game,auth_headers,operation='surgery_install',operation_id='install-integrated',
        target_revision=actor.character.revision).status_code==200
    advance_lobby(grenade_game['lobby'],28800);db.session.commit();before=deepcopy(actor.character.data)
    response=operation(client,grenade_game,auth_headers,operation='surgery_remove',operation_id='forged-remove',
        target_revision=actor.character.revision)
    assert response.status_code==400
    assert actor.character.data==before


@pytest.mark.parametrize('success', [True,False])
def test_large_clock_jump_only_spends_power_until_actual_removal(client,grenade_game,auth_headers,monkeypatch,success):
    from app.services.prostheses import advance_lobby
    actor=surgical_setup(grenade_game)
    monkeypatch.setattr('app.services.prosthesis_surgery.random.randint',lambda *_:20)
    assert operation(client,grenade_game,auth_headers,operation='surgery_install',operation_id='install-clock',
        target_revision=actor.character.revision).status_code==200
    advance_lobby(grenade_game['lobby'],3600);db.session.commit()
    data=deepcopy(actor.character.data);data['health']['prostheses']['leftLeg']['base']['energy_seconds']=10800
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    assert operation(client,grenade_game,auth_headers,operation='surgery_remove',operation_id='remove-clock',
        target_revision=actor.character.revision).status_code==200
    monkeypatch.setattr('app.services.prosthesis_surgery.random.randint',lambda *_:20 if success else 1)
    advance_lobby(grenade_game['lobby'],7200);db.session.commit()
    item=next(i for i in actor.character.data['inventory']['pockets'] if i.get('id')=='prosthesis-test') if success else installed_for_test(actor)
    assert item['energy_seconds']==(7200 if success else 3600)


def test_failed_operation_keeps_loss_and_returns_prosthesis_but_not_supplies(client,grenade_game,auth_headers,monkeypatch):
    from app.services.prostheses import advance_lobby
    actor=surgical_setup(grenade_game)
    result=operation(client,grenade_game,auth_headers,operation='surgery_install',operation_id='surgical-fail',target_revision=actor.character.revision)
    assert result.status_code==200,result.json
    monkeypatch.setattr('app.services.prosthesis_surgery.random.randint',lambda *_:1)
    advance_lobby(grenade_game['lobby'],3600);db.session.commit()
    assert not installed_for_test(actor)
    items=actor.character.data['inventory']['pockets']
    assert sum(item['quantity'] for item in items if item['name']=='Морфий')==7
    assert next(item for item in items if item['id']=='prosthesis-test')['quantity']==1
    assert actor.character.data['health']['combatMeta']['prosthesisSurgeryResult']['success'] is False
    assert actor.character.data['health']['effects'][0]['loss_extent']=='elbow_or_knee'


def test_surgery_checks_all_supplies_before_spending_any(client,grenade_game,auth_headers):
    actor=surgical_setup(grenade_game,True);data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[item for item in data['inventory']['pockets'] if item['name']!='Подавитель эмоций']
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit();before=deepcopy(actor.character.data)
    result=operation(client,grenade_game,auth_headers,operation='surgery_install',operation_id='surgical-short',target_revision=actor.character.revision)
    assert result.status_code==400,result.json
    assert actor.character.data==before and not db.session.get(DeferredCombatAction,'surgical-short')


def test_operation_on_other_player_requires_consent_and_installs_on_patient(client,grenade_game,auth_headers,monkeypatch):
    from app.models import CharacterInteractionRequest
    from app.services.prostheses import advance_lobby
    actor=surgical_setup(grenade_game);patient=grenade_game['target_location']
    grenade_game['lobby'].gm_id=grenade_game['target_user']['id']
    patient.pos_x,patient.pos_y=actor.pos_x+1,actor.pos_y
    data=deepcopy(patient.character.data);data['health']['effects']=[{'type':'amputation','area':'leftLeg','loss_extent':'elbow_or_knee'}]
    patient.character.data=data;flag_modified(patient.character,'data');db.session.commit()
    body={'operation':'surgery_install','operation_id':'surgical-other','target_character_id':patient.character_id,'target_revision':patient.character.revision}
    before=deepcopy(actor.character.data['inventory'])
    response=operation(client,grenade_game,auth_headers,**body)
    assert response.status_code in (403,404),response.json
    assert actor.character.data['inventory']==before
    consent=CharacterInteractionRequest(location_id=actor.location_id,actor_location_character_id=actor.id,
        target_location_character_id=patient.id,actor_user_id=grenade_game['actor_user']['id'],
        target_user_id=grenade_game['target_user']['id'],kind='treatment',status='accepted')
    db.session.add(consent);db.session.commit()
    body.update(interaction_request_id=consent.id,target_revision=patient.character.revision)
    response=operation(client,grenade_game,auth_headers,**body)
    assert response.status_code==200,response.json
    assert consent.status=='in_progress'
    monkeypatch.setattr('app.services.prosthesis_surgery.random.randint',lambda *_:20)
    advance_lobby(grenade_game['lobby'],3600);db.session.commit()
    assert installed_for_test(patient)['id']=='prosthesis-test' and not installed_for_test(actor)
    assert consent.status=='completed'


def test_cancelled_surgery_does_not_refund_drugs_or_duplicate_prosthesis(client,grenade_game,auth_headers):
    actor=surgical_setup(grenade_game)
    response=operation(client,grenade_game,auth_headers,operation='surgery_install',operation_id='surgical-cancel',target_revision=actor.character.revision)
    assert response.status_code==200,response.json
    response=operation(client,grenade_game,auth_headers,operation='cancel_surgery')
    assert response.status_code==200,response.json
    assert db.session.get(DeferredCombatAction,'surgical-cancel').status=='cancelled'
    assert sum(item.get('quantity',1) for item in actor.character.data['inventory']['pockets'] if item.get('id')=='prosthesis-test')==1
    assert sum(item['quantity'] for item in actor.character.data['inventory']['pockets'] if item['name']=='Морфий')==7
    assert operation(client,grenade_game,auth_headers,operation='cancel_surgery').status_code==409


def test_combat_start_interrupts_surgery_without_waiting_for_world_clock(client,grenade_game,auth_headers):
    from app.services.prosthesis_surgery import busy
    actor=surgical_setup(grenade_game)
    response=operation(client,grenade_game,auth_headers,operation='surgery_install',operation_id='surgical-combat',target_revision=actor.character.revision)
    assert response.status_code==200,response.json
    CombatService.start_combat(actor.location_id,grenade_game['lobby'].gm_id,location_character_ids=[actor.id],initiator_location_character_id=actor.id)
    assert not busy(actor.character.data) and not installed_for_test(actor)
    assert db.session.get(DeferredCombatAction,'surgical-combat').status=='cancelled'
    assert sum(item['quantity'] for item in actor.character.data['inventory']['pockets'] if item['name']=='Морфий')==7
    assert sum(item.get('quantity',1) for item in actor.character.data['inventory']['pockets'] if item.get('id')=='prosthesis-test')==1


def test_electric_discharge_preserves_durability_and_respects_shielding():
    data=prosthetic_data();item=data['health']['prostheses']['leftLeg']['base']
    assert discharge(data) and item['energy_seconds']==0 and item['durability']==75
    shielded=prosthetic_data('Интегрированный протез голени')
    assert not discharge(shielded)
    assert shielded['health']['prostheses']['leftLeg']['base']['energy_seconds']==86400


def test_base_and_hand_efficiency_and_functional_arm():
    data=prosthetic_data('Навесная база предплечья','leftArm')
    parts=data['health']['prostheses']['leftArm']
    assert limb(data,'leftArm') is None and functional_arms(data)==1
    parts['hand']={'name':'Кисть крюк','durability':25}
    assert limb(data,'leftArm')['efficiency']==-9 and functional_arms(data)==2
    assert CombatService._health_roll_modifier(data,'skills.other.medicine')==-9


def test_sheet_cannot_forge_installed_prostheses(client,grenade_game,auth_headers):
    actor=setup(grenade_game);outside(grenade_game)
    assert operation(client,grenade_game,auth_headers,operation='install').status_code==200
    url=f'/lobbies/characters/{actor.character_id}'
    snapshot=client.get(url,headers=auth_headers(grenade_game['actor_user'])).json['data']
    snapshot['health']['prostheses']={}
    result=client.put(url,headers=auth_headers(grenade_game['actor_user']),json={'data':snapshot})
    assert result.status_code==200,result.json
    assert actor.character.data['health']['prostheses']['leftLeg']['base']['id']=='prosthesis-test'


def test_ten_ap_strap_installation_waits_for_full_payment(client,grenade_game,auth_headers):
    actor=setup(grenade_game);actor.action_points_current=1;db.session.commit()
    gm=grenade_game['lobby'].gm_id
    result=CombatService.perform_action(actor.location_id,gm,actor.id,'prosthesis',equipment_operation='install',
        equipment_slot='leftLeg',item_path=['inventory','pockets',0],pending_action_id='prosthesis-debt')
    assert result['pending_action'] and not actor.character.data['health'].get('prostheses')
    for _ in range(6):
        row=db.session.get(DeferredCombatAction,'prosthesis-debt')
        if row.status=='ready':break
        state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
        CombatService.end_turn(actor.location_id,gm,state.current_location_character_id)
    assert row.status=='ready' and not actor.character.data['health'].get('prostheses')
    CombatService.perform_action(actor.location_id,gm,actor.id,'prosthesis',resume_pending_action_id=row.id)
    assert actor.character.data['health']['prostheses']['leftLeg']['base']['id']=='prosthesis-test'
    assert row.status=='completed'


def test_lubrication_takes_five_minutes_and_does_not_repair_missed_cycles(client,grenade_game,auth_headers):
    actor=setup(grenade_game);outside(grenade_game)
    assert operation(client,grenade_game,auth_headers,operation='install').status_code==200
    data=deepcopy(actor.character.data)
    item=data['health']['prostheses']['leftLeg']['base'];item['missed_lubrications']=1;item['durability']=25
    item['lubrication_elapsed']=600
    data['inventory']['pockets']=[{'name':'Набор смазочных приспособлений','quantity':1}]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    result=operation(client,grenade_game,auth_headers,operation='lubricate',slot='base')
    assert result.status_code==200,result.json
    item=actor.character.data['health']['prostheses']['leftLeg']['base']
    assert item['lubrication_remaining']==300 and not actor.character.data['inventory']['pockets']
    advance(actor.character.data,299)
    assert item['lubrication_remaining']==1 and item['lubrication_elapsed']==899
    advance(actor.character.data,1)
    assert item['lubrication_elapsed']==0 and item['durability']==25 and item['missed_lubrications']==1


def test_hook_attack_uses_own_accuracy_and_never_scales_from_strength():
    from app.services.prostheses import sync_weapons
    data=prosthetic_data('Навесная база предплечья','leftArm')
    data['health']['prostheses']['leftArm']['hand']={'id':'hook','name':'Кисть крюк','durability':25}
    sync_weapons(data)
    weapon=data['weapons'][0];profile=CombatService._weapon_damage_profile(weapon,'Колющий')
    assert profile['accuracy']==-2 and profile['skip_strength_scaling']
    assert weapon['attributes']['prosthesis_efficiency']==-5
    data['health']['prostheses']['leftArm']['base']['energy_seconds']=0
    sync_weapons(data)
    assert not data['weapons']


def test_real_hook_attack_replaces_hand_penalty_and_ignores_strength(grenade_game,monkeypatch):
    from app.services.prostheses import sync_weapons
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    data=prosthetic_data('Навесная база предплечья','leftArm')
    data['health'].update(current=700,max=700,painLevel=0,stress=0,exhaustion=0)
    data['health']['prostheses']['leftArm']['hand']={'id':'combat-hook','name':'Кисть крюк','durability':25}
    data['skills']={'physical':{'strength':{'base':40},'melee':{'base':20}}}
    sync_weapons(data);actor.character.data=data;actor.action_points_current=20
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y;flag_modified(actor.character,'data');db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:min(b,max(a,15)))
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',
        weapon_index=0,attack_type='Колющий',target_character_id=target.character_id)
    assert result['attack']['results'][0]['hit']
    assert result['attack']['results'][0]['damage']==62
    assert result['attack']['hit_difficulty']==13  # 12 − (5 − 4 основы − 5 кисти + 5 замены) − (−2 точности).
