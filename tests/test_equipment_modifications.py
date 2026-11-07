"""Установка и замена улучшений, Оружейник, слоты и серверные эффекты."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.combat import CombatService
from app.services.crafting_rules import catalog
from app.services.equipment_modifications import make_record,stat,rule,fire_profile
from test_crafting import workshop,call
from test_quick_skill_checks import checks
from test_grenade_rules import grenade_game
from test_flamethrower import flame_game
from test_movement_shooting_balance import arena,shoot


def stop_visor():
    return {'id':'stop-visor','name':'Забрало','slotType':'visor','category':'helmet_module',
        'durability':100,'maxDurability':100,'modifications':[make_record('armor-g21')],
        'installedModules':[{'id':'preserved-nvg','slotType':'nvg'}]}


@pytest.mark.parametrize('damage,subzone,blocked',[(10,'face',False),(11,'face',True),(30,'back',False),(30,'crown',False)])
def test_stop_layer_threshold_and_face_only(grenade_game,damage,subzone,blocked):
    target=grenade_game['actor_location'];data=deepcopy(target.character.data)
    data['equipment']={'helmet':{'category':'helmet','installedModules':[stop_visor()]}}
    target.character.data=data;db.session.commit()
    before=data['health']['current']
    health=CombatService._apply_attack_damage(target,damage,'head',{'bleeding':'сильное'},
        head_impact={'armor_zone':subzone,'damage_head':True},trauma_checks=0)
    outcome=health.pop('_attackOutcome');assert bool(outcome.get('stop_layer')) is blocked
    assert health['current']==before-(0 if blocked else damage)
    visor=target.character.data['equipment']['helmet']['installedModules'][0]
    if blocked:
        assert visor['stopLayerBroken'] and visor['protectionDisabled'] and visor['durability']==0
        assert visor['installedModules'][0]['id']=='preserved-nvg'
        assert outcome['bleedings']==[] and outcome['additional_traumas']==[]
        again=CombatService._apply_attack_damage(target,damage,'head',{},head_impact={'armor_zone':'face','damage_head':True},trauma_checks=0,allow_bleeding=False)
        assert again['_attackOutcome']['applied_damage']==damage


def test_stop_layer_in_real_shot_blocks_pepper_and_saves_destroyed_instance(arena,monkeypatch):
    from app.services.equipment_modifications import preserve
    _,_,shooter,target=arena
    data=deepcopy(target.character.data);data['equipment']={'helmet':{'category':'helmet',
        'attributes':{'protection_zones':['crown']},'installedModules':[stop_visor()]}}
    target.character.data=data;target.facing_x=-1
    weapon=deepcopy(shooter.character.data);weapon['weapons'][0].update(damage=100,
        installedMagazine={'ammo':[{'name':'Перцовый патрон','quantity':20,'damage':100,'attributes':{'caliber':'18x45 И'}}]})
    shooter.character.data=weapon;db.session.commit()
    assert CombatService._ranged_damage_profile(weapon['weapons'][0])[0]['pepper_round']
    head=CombatService._head_impact_profile
    monkeypatch.setattr(CombatService,'_head_impact_profile',lambda _,attacker,target:head(4,attacker,target))
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:20 if high==20 else high)
    CombatService.perform_action(target.location_id,arena[0].id,shooter.id,'aim',weapon_index=0,target_character_id=target.character_id)
    result=shoot(arena,fire_mode='aimed',target_character_id=target.character_id,target_zone='head',action_points=4)
    hit=result['attack']['results'][0]
    assert hit['damage']==0 and hit['stop_layer']['item_id']=='stop-visor'
    assert not hit.get('pepper_head_hit') and target.character.data['health']['current']==700
    saved=deepcopy(target.character.data);incoming=deepcopy(saved)
    visor=incoming['equipment']['helmet']['installedModules'][0]
    visor.update(stopLayerBroken=False,protectionDisabled=False,durability=100,stage=1)
    preserve(saved,incoming,is_gm=False)
    assert visor['stopLayerBroken'] and visor['protectionDisabled'] and visor['durability']==0


def test_visor_own_protection_only_covers_face_and_is_lost_when_shattered(app):
    visor=stop_visor();visor['protection']={'physical':30}
    data={'equipment':{'helmet':{'category':'helmet','protection':{'physical':50},
        'attributes':{'protection_zones':['crown']},'installedModules':[visor]}}}
    assert CombatService._target_armor(data,'head',head_protection_zone='face')[0]==30
    assert CombatService._target_armor(data,'head',head_protection_zone='crown')[0]==50
    visor.update(stopLayerBroken=True,protectionDisabled=True)
    assert CombatService._target_armor(data,'head',head_protection_zone='face')[0]==0


def test_shattered_mount_preserves_and_switches_off_devices_without_free_reactivation(client,grenade_game,auth_headers):
    from app.services.portable_lights import ready_sources
    target=grenade_game['actor_location'];data=deepcopy(target.character.data)
    visor=stop_visor();visor['modifications'].append(make_record('armor-g18'))
    visor['installedModules'].append({'id':'preserved-light','name':'Фонарик','slotType':'light','category':'misc','quantity':1})
    data['equipment']={'helmet':{'category':'helmet','installedModules':[visor]}}
    data['health'].setdefault('combatMeta',{})['portableLights']=[{'id':'preserved-light','mount':'head','on':True}]
    target.character.data=data;db.session.commit()
    CombatService._apply_attack_damage(target,20,'head',{},head_impact={'armor_zone':'face','damage_head':True})
    db.session.commit();assert ready_sources(target)==[]
    path=['equipment','helmet','installedModules',0,'installedModules']
    before=target.action_points_current
    for index,identity in enumerate(['preserved-nvg','preserved-light']):
        response=client.post(f'/lobbies/characters/{target.character_id}/portable-lights',headers=auth_headers(grenade_game['actor_user']),
            json={'operation':'toggle','path':path+[index],'source_id':identity,'character_revision':target.character.revision})
        assert response.status_code==400,response.json
    assert target.action_points_current==before
    status=client.get(f'/lobbies/characters/{target.character_id}/portable-lights',headers=auth_headers(grenade_game['actor_user'])).json
    assert all(not item['binding']['on'] and item['mount_available'] is False for item in status['lights'] if item['id'] in {'preserved-nvg','preserved-light'})


@pytest.mark.parametrize('rule_id,family,previous',[('armor-d11','Аномальная',['armor-d9','armor-d10']),
    ('armor-d31','Экзоскелет',[]),('armor-d14','Аномальная',[])])
def test_existing_sealing_and_reanimation_effects_are_installable_at_specialist(workshop,rule_id,family,previous):
    from app.services.professional_modification import quote,perform
    from app.services.equipment_care import consume_reanimation
    pair=workshop;owner=pair['actor_character'];specialist=pair['target_character']
    template=ItemTemplate(name='Проверочная броня '+family,category='armor',item_class='Н',price=10000,
        attributes={'modification_category':family})
    db.session.add(template);db.session.flush()
    data=deepcopy(owner.data);data['inventory']['money']=200000
    data.setdefault('equipment',{})['armor']={'id':'armor-special','name':template.name,'templateId':template.id,
        'category':'armor','stage':3,'stage3DamageType':'electric','modifications':[make_record(key) for key in previous]}
    owner.data=data
    specialist.data={**deepcopy(specialist.data),'is_npc':True,'merchant':{'modification_enabled':True,
        'scientist_enabled':True,'workbenches':['Станок Бронника']},'inventory':{'money':0}}
    db.session.commit()
    payload={'actor_location_character_id':pair['actor_location'].id,'specialist_character_id':specialist.id,
        'target_path':['equipment','armor'],'item_id':'armor-special','rule_id':rule_id}
    plan=quote(pair['location'].id,pair['actor_user']['id'],payload)
    command={**payload,'actor_revision':owner.revision,'specialist_revision':specialist.revision,'operation_id':'install-existing-effect'}
    result=perform(pair['location'].id,pair['actor_user']['id'],command)
    assert result['price']==plan['price'] and owner.data['inventory']['money']==200000-plan['price']
    armor=owner.data['equipment']['armor'];assert armor['modifications'][-1]['ruleId']==rule_id
    if rule_id=='armor-d11':
        assert CombatService._armor_stage_penalty(armor,'electric')==0
        armor['stage']=4;assert CombatService._armor_stage_penalty(armor,'electric')==10
    elif rule_id=='armor-d31':
        owner.data['health'].update(current=350,max=700,zones={'head':{'current':50,'max':100}},painLevel=5)
        assert armor['reanimationCharge'] is True and consume_reanimation(owner.data)
        assert owner.data['health']['painLevel']==0 and owner.data['health']['zones']['head']['current']==60
        assert not consume_reanimation(owner.data)
    else:
        from app.services.electronics import electric_protection
        assert electric_protection(owner.data,{'name':'КПК'})
    assert perform(pair['location'].id,pair['actor_user']['id'],command)=={'duplicate':True}


def test_player_cannot_assign_free_modifications_but_existing_records_remain(app):
    from app.services.equipment_modifications import preserve
    from app.services.exceptions import ValidationError

    current={'weapons':[{'id':'gun','name':'Пистолет','category':'weapon',
        'modifications':[{'name':'Старая ручная модификация','description':'Сохранить'}]}],
        'modifications':{'pda':{'items':[{'name':'Старая запись','effect':'Сохранить'}]}}}
    unchanged=deepcopy(current)
    preserve(current,unchanged,is_gm=False)
    assert unchanged['weapons'][0]['modifications']==current['weapons'][0]['modifications']
    added=deepcopy(current)
    added['weapons'][0]['modifications'].append({'name':'Новая модификация'})
    with pytest.raises(ValidationError,match='через игровой процесс'):
        preserve(current,added,is_gm=False)
    pda_changed=deepcopy(current)
    pda_changed['modifications']['pda']['items'].append({'name':'Новая запись'})
    with pytest.raises(ValidationError,match='через игровой процесс'):
        preserve(current,pda_changed,is_gm=False)
    preserve(current,added,is_gm=True)


def test_player_cannot_change_visor_modifications_but_gm_can():
    from app.services.equipment_modifications import preserve
    from app.services.exceptions import ValidationError

    current={'equipment':{'helmet':{'id':'helmet','category':'helmet',
        'installedModules':[{'id':'visor','slotType':'visor','category':'visor',
            'modifications':[{'name':'Старая модификация'}]}]}}}
    unchanged=deepcopy(current)
    preserve(current,unchanged,is_gm=False)
    visor=unchanged['equipment']['helmet']['installedModules'][0]
    assert visor['modifications']==[{'name':'Старая модификация'}]
    visor['modifications'].append({'name':'Бесплатная модификация'})
    with pytest.raises(ValidationError,match='через игровой процесс'):
        preserve(current,unchanged,is_gm=False)
    preserve(current,unchanged,is_gm=True)
    assert visor['modifications'][-1]['name']=='Бесплатная модификация'


def test_exoskeleton_run_upgrade_changes_actual_movement(grenade_game):
    actor=grenade_game['actor_location'];location=grenade_game['location']
    data=deepcopy(actor.character.data)
    data['equipment']={'armor':{'name':'Экзоскелет','category':'armor',
        'modifications':[make_record('armor-d32')]}}
    actor.character.data=data
    actor.movement_points_current=100;actor.action_points_current=20
    db.session.commit()
    moved,_,_=CombatService.move_character(location.id,grenade_game['lobby'].gm_id,
        actor.character_id,3.1,5.2,movement_mode='run')
    assert moved.pos_x==pytest.approx(3.1)
    assert CombatService._serialize_character(moved)['exoskeleton_can_run']


def test_iron_grip_reduces_burst_penalty_only_while_worn():
    from app.services.equipment_modifications import worn_rule
    armor={'name':'Экзоскелет','modifications':[make_record('armor-d30')]}
    data={'equipment':{'armor':armor}}
    rifle={'subcategory':'Штурмовые винтовки и карабины'}
    shotgun={'subcategory':'Дробовики'}
    assert CombatService._burst_followup_accuracy_penalty(rifle,1,data)==2
    assert CombatService._burst_followup_accuracy_penalty(shotgun,1,data)==4
    assert worn_rule(data,'snatch_difficulty_bonus')==6
    armor['protectionDisabled']=True
    assert CombatService._burst_followup_accuracy_penalty(rifle,1,data)==4
    assert worn_rule(data,'snatch_difficulty_bonus')==0


def test_nvg_mount_can_be_installed_and_requires_removing_nvg_before_replacement():
    from app.services.equipment_modifications import install
    from app.services.exceptions import ValidationError
    helmet={'name':'Шлем','category':'helmet','attributes':{'slots':[]},'installedModules':[]}
    install(helmet,make_record('armor-g25'))
    assert rule(helmet,'nvg_mount')
    helmet['installedModules']=[{'slotType':'nvg','name':'ПНВ'}]
    with pytest.raises(ValidationError,match='снимите ПНВ'):
        install(helmet,make_record('armor-g26'),replacing='armor-g25')
    assert rule(helmet,'nvg_mount')
    helmet['installedModules'].clear()
    install(helmet,make_record('armor-g26'),replacing='armor-g25')
    assert not rule(helmet,'nvg_mount')


def test_expanded_belt_adds_two_pouch_places_and_protects_filled_places(app):
    from app.services.equipment_modifications import belt_pouch_limit,install,supported,definition,validate_belt_pouches
    from app.services.exceptions import ValidationError
    template=ItemTemplate(name='Пояс',category='belt',attributes={'max_pouches':2})
    helmet=ItemTemplate(name='Шлем',category='helmet')
    db.session.add_all([template,helmet]);db.session.flush()
    belt={'id':'belt-1','templateId':template.id,'category':'belt','pouches':[{},{}]}
    assert supported(definition('armor-g57'))
    assert belt_pouch_limit(belt)==2
    with pytest.raises(ValidationError,match='не больше 2'):
        validate_belt_pouches({'equipment':{'belt':belt}},
            {'equipment':{'belt':{**belt,'pouches':[{}, {}, {}]}}},None)
    install(belt,make_record('armor-g57'))
    assert belt_pouch_limit(belt)==4
    belt['extraStoredItem']={'id':'helmet-extra','name':'Шлем','category':'helmet','templateId':helmet.id}
    assert belt_pouch_limit(belt)==2
    validate_belt_pouches({'equipment':{'belt':belt}}, {'equipment':{'belt':belt}},None)
    with pytest.raises(ValidationError,match='дополнительный шлем'):
        install(belt,make_record('armor-g58'),replacing='armor-g57')
    belt['extraStoredItem']=None
    belt['pouches'].extend([{},{}])
    validate_belt_pouches({'equipment':{'belt':belt}}, {'equipment':{'belt':belt}},None)
    with pytest.raises(ValidationError,match='дополнительные подсумки'):
        install(belt,make_record('armor-g58'),replacing='armor-g57')
    assert rule(belt,'extra_belt_pouches')==2
    belt['pouches']=belt['pouches'][:2]
    install(belt,make_record('armor-g58'),replacing='armor-g57')
    assert belt_pouch_limit(belt)==2


def test_existing_overfilled_belt_is_preserved_without_adding_more_pouches(app):
    from app.services.equipment_modifications import validate_belt_pouches
    from app.services.exceptions import ValidationError
    template=ItemTemplate(name='Пояс',category='belt',attributes={'max_pouches':2})
    db.session.add(template);db.session.flush()
    current={'equipment':{'belt':{'id':'legacy','templateId':template.id,'pouches':[{} for _ in range(5)]}}}
    validate_belt_pouches(current,deepcopy(current),None)
    incoming=deepcopy(current)
    incoming['equipment']['belt']['pouches'].append({})
    with pytest.raises(ValidationError,match='не больше 2'):
        validate_belt_pouches(current,incoming,None)


def test_character_save_enforces_belt_pouch_limit_after_preserving_upgrade(grenade_game):
    from app.services.character import CharacterService
    from app.services.exceptions import ValidationError
    actor=grenade_game['actor_location'].character
    template=ItemTemplate(name='Пояс',category='belt',attributes={'max_pouches':2})
    helmet=ItemTemplate(name='Шлем',category='helmet')
    db.session.add_all([template,helmet]);db.session.flush()
    data=deepcopy(actor.data)
    data.setdefault('equipment',{})['belt']={'id':'belt-save','templateId':template.id,
        'category':'belt','pouches':[{},{}]}
    actor.data=data;db.session.commit()
    incoming=actor.data_snapshot()
    incoming['equipment']['belt']['pouches'].append({})
    with pytest.raises(ValidationError,match='не больше 2'):
        CharacterService.update_character(actor.id,grenade_game['lobby'].gm_id,{'data':incoming})
    data=deepcopy(actor.data)
    data['equipment']['belt']['modifications']=[make_record('armor-g57')]
    actor.data=data;db.session.commit()
    incoming=actor.data_snapshot()
    incoming['equipment']['belt']['pouches'].extend([{},{}])
    CharacterService.update_character(actor.id,grenade_game['lobby'].gm_id,{'data':incoming})
    assert len(actor.data['equipment']['belt']['pouches'])==4
    incoming=actor.data_snapshot()
    incoming['equipment']['belt']['pouches']=incoming['equipment']['belt']['pouches'][:2]
    incoming['equipment']['belt']['extraStoredItem']={'id':'helmet-extra','templateId':helmet.id,
        'name':'Шлем','category':'helmet'}
    CharacterService.update_character(actor.id,grenade_game['lobby'].gm_id,{'data':incoming})
    assert actor.data['equipment']['belt']['extraStoredItem']['id']=='helmet-extra'
    data=deepcopy(actor.data)
    data['equipment']['belt']['extraStoredItem']['modifications']=[make_record('armor-g25')]
    actor.data=data;db.session.commit()
    incoming=actor.data_snapshot()
    incoming['equipment']['belt']['extraStoredItem']['modifications']=[]
    CharacterService.update_character(actor.id,grenade_game['lobby'].gm_id,{'data':incoming})
    assert rule(actor.data['equipment']['belt']['extraStoredItem'],'nvg_mount')
    incoming=actor.data_snapshot()
    incoming['equipment']['belt']['pouches'].append({})
    with pytest.raises(ValidationError,match='занимает места двух подсумков'):
        CharacterService.update_character(actor.id,grenade_game['lobby'].gm_id,{'data':incoming})


def test_helmet_upgrade_survives_belt_unequip_and_reequip():
    from app.services.equipment_modifications import preserve
    extra={'id':'helmet-saved','category':'helmet','modifications':[make_record('armor-g25')]}
    belt={'id':'belt-saved','category':'belt','extraStoredItem':extra}
    equipped={'equipment':{'belt':deepcopy(belt)}}
    stored={'inventory':{'backpack':[deepcopy(belt)]}}
    preserve(equipped,stored)
    assert rule(stored['inventory']['backpack'][0]['extraStoredItem'],'nvg_mount')
    reequip={'equipment':{'belt':deepcopy(stored['inventory']['backpack'][0])}}
    preserve(stored,reequip)
    assert rule(reequip['equipment']['belt']['extraStoredItem'],'nvg_mount')


def test_iron_grip_raises_real_disarm_difficulty(client,grenade_game,auth_headers,monkeypatch):
    case=grenade_game
    target=case['target_location']
    target.pos_x,target.pos_y=3.1,5.2
    target.drawn_weapon_index=0
    data=deepcopy(target.character.data)
    data['weapons']=[{'id':'target-gun','name':'Пистолет','category':'weapon'}]
    data['equipment']={'armor':{'name':'Экзоскелет','modifications':[make_record('armor-d30')]}}
    target.character.data=data
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:10)

    response=client.post(f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat/action",
        headers=auth_headers(case['actor_user']),json={'location_character_id':case['actor_location'].id,
        'action_key':'melee_disarm','target_character_id':case['target_character'].id})

    assert response.status_code==200,response.json
    assert response.json['melee_action']['difficulty']==18
    assert response.json['melee_action']['success'] is False


def test_reinforced_frame_adds_armor_and_durability_only_while_worn():
    from app.services.equipment_modifications import install, worn_rule
    armor={'name':'Броня','category':'armor','durability':50,'maxDurability':100}
    install(armor,make_record('armor-a24'))
    assert (armor['durability'],armor['maxDurability'])==(55,105)
    assert stat(armor,'physical',10)==15
    assert worn_rule({'equipment':{'armor':armor}},'incoming_trauma_disadvantage')
    armor['protectionDisabled']=True
    assert not worn_rule({'equipment':{'armor':armor}},'incoming_trauma_disadvantage')


def test_liquid_crystal_layer_first_hit_and_timed_recharge(client,workshop,auth_headers,monkeypatch):
    from app.services.equipment_care import advance,consume_liquid_crystal,liquid_crystal_ready
    from app.services.equipment_modifications import install
    character=workshop['actor_character'];data=deepcopy(character.data)
    armor={'id':'crystal-armor','name':'Тяжёлая броня','category':'armor','quantity':1,
           'protection':{'physical':15},'modifications':[]}
    install(armor,make_record('armor-a39'))
    data.setdefault('equipment',{})['armor']=armor
    assert CombatService._target_armor(data,'chest')[0]==35
    assert liquid_crystal_ready(armor)
    assert consume_liquid_crystal(armor)
    assert not liquid_crystal_ready(armor)
    assert CombatService._target_armor(data,'chest')[0]==15
    assert advance(data,599)
    assert armor['liquidCrystalState']['remaining_seconds']==1
    assert advance(data,1)
    character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:1)
    def recharge(operation_id):
        return client.post(f'/lobbies/characters/{character.id}/liquid-crystal-recharge',
            headers=auth_headers(workshop['actor_user']),json={'character_revision':character.revision,
                'engineer_revision':character.revision,'engineer_id':character.id,
                'item_id':'crystal-armor','operation_id':operation_id})
    failed=recharge('crystal-fail')
    assert failed.status_code==200,failed.json
    assert failed.json['operation']['success'] is False
    assert failed.json['operation']['remaining_seconds']==600
    repeated=recharge('crystal-fail')
    assert repeated.status_code==200 and repeated.json['operation']['duplicate']
    assert character.data['equipment']['armor']['liquidCrystalState']['remaining_seconds']==600
    data=deepcopy(character.data)
    assert advance(data,600)
    character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:20)
    success=recharge('crystal-success')
    assert success.status_code==200,success.json
    assert success.json['operation']['success'] is True
    assert liquid_crystal_ready(character.data['equipment']['armor'])
    assert CombatService._target_armor(character.data,'chest')[0]==35
    assert make_record('armor-a39',gunsmith=True)['rules']['liquid_crystal_layer']==30


def test_nearby_engineer_can_recharge_another_characters_armor(client,workshop,auth_headers,monkeypatch):
    owner=workshop['actor_character'];engineer=workshop['target_character']
    data=deepcopy(owner.data)
    data.setdefault('equipment',{})['armor']={'id':'shared-crystal','name':'Броня','category':'armor',
        'modifications':[make_record('armor-a39')],
        'liquidCrystalState':{'charged':False,'remaining_seconds':0}}
    owner.data=data
    engineer_data=deepcopy(engineer.data)
    engineer_data.setdefault('skills',{}).setdefault('other',{})['engineering']={'base':20}
    engineer.data=engineer_data
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:20)
    def recharge(operation_id):
        return client.post(f'/lobbies/characters/{owner.id}/liquid-crystal-recharge',
            headers=auth_headers(workshop['target_user']),json={'character_revision':owner.revision,
                'engineer_revision':engineer.revision,'engineer_id':engineer.id,
                'item_id':'shared-crystal','operation_id':operation_id})
    response=recharge('shared-crystal-near')
    assert response.status_code==200,response.json
    assert response.json['operation']['success']
    assert owner.data['equipment']['armor']['liquidCrystalState']['charged']
    data=deepcopy(owner.data)
    data['equipment']['armor']['liquidCrystalState']={'charged':False,'remaining_seconds':0}
    owner.data=data
    workshop['target_location'].pos_x=20
    db.session.commit()
    far=recharge('shared-crystal-far')
    assert far.status_code==400,far.json
    assert owner.data['equipment']['armor']['liquidCrystalState']['charged'] is False


def test_sheet_save_cannot_restore_spent_liquid_crystal_charge():
    from app.services.equipment_care import preserve
    old={'equipment':{'armor':{'id':'crystal-saved','category':'armor',
        'liquidCrystalState':{'charged':False,'remaining_seconds':317}}}}
    incoming=deepcopy(old)
    incoming['equipment']['armor']['liquidCrystalState']={'charged':True,'remaining_seconds':0}
    preserve(old,incoming)
    assert incoming['equipment']['armor']['liquidCrystalState']==old['equipment']['armor']['liquidCrystalState']
    old['equipment']['armor'].pop('liquidCrystalState')
    preserve(old,incoming)
    assert 'liquidCrystalState' not in incoming['equipment']['armor']


@pytest.fixture
def gunsmith(workshop):
    data=deepcopy(workshop['actor_character'].data)
    data['basic']={'background':{'name':'Оружейник'}}
    data['skills']['other'].update(engineering={'base':20},tactics={'base':10})
    data['weapons']=[{'id':'upgrade-gun','name':'Пистолет','category':'weapon','subcategory':'Пистолеты','itemClass':'1',
        'ergonomics':20,'accuracy':0,'fireRate':5,'durability':100,'maxDurability':100,'modifications':[]}]
    data['inventory']['money']=5000
    data['inventory']['pockets'].append({'id':'upgrade-tools','name':'Набор инструментов Оружейника (Расширенные)',
        'category':'tool','quantity':1,'uses':50,'maxUses':50})
    data['crafting']={'library':[{'book_kind':'equipment','book_subject':'Пистолеты','name':'Книга'}]}
    workshop['actor_character'].data=data;db.session.commit()
    return workshop


def modify(client,gunsmith,auth_headers,rule_id='weapon-c23',**extra):
    return call(client,gunsmith,auth_headers,'modify',rule_id=rule_id,target_path=['weapons',0],tool_path=['inventory','pockets',4],**extra)


def finish(client,gunsmith,auth_headers,job,**extra):
    gunsmith['lobby'].game_time_minutes+=60;db.session.commit()
    return call(client,gunsmith,auth_headers,'finish',job_id=job['id'],**extra)


def test_every_weapon_definition_has_typed_effects():
    for definition in catalog()['modifications']:
        if definition['kind']=='weapon':
            record=make_record(definition['id'],gunsmith=True)
            assert record['modifiers'] or record['rules']
    assert make_record('weapon-b29',gunsmith=True)['modifiers']['ergonomics']=='=75.0'
    assert rule({'modifications':[make_record('weapon-b38',gunsmith=True)]},'burst_ap_delta')==-1
    helmet=make_record('armor-g52',gunsmith=True)
    assert helmet['modifiers']['movement_penalty']==1 and helmet['modifiers']['ergonomics']==-5
    assert helmet['modifiers']['physical']==7.5


def test_gunsmith_install_waits_spends_tools_and_rounds_final_ergonomics(client,gunsmith,auth_headers,monkeypatch):
    started=modify(client,gunsmith,auth_headers)
    assert started.status_code==200,started.json
    assert started.json['data']['inventory']['pockets'][4]['uses']==40
    assert started.json['data']['weapons'][0]['modifications']==[]
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda q:{'success':True,'roll':100})
    response=finish(client,gunsmith,auth_headers,started.json['result'])
    assert response.status_code==200,response.json
    weapon=response.json['data']['weapons'][0]
    assert stat(weapon,'ergonomics',20)==27.5
    # Оружейник не получает Тактику к эргономике: 20 +7,5 +10 Стрельбы =37,5 →38.
    assert CombatService._weapon_ergonomics_profile(gunsmith['actor_location'],weapon,0)['value']==38


def test_same_level_replacement_removes_old_effect_without_gaining_an_extra_slot(client,gunsmith,auth_headers,monkeypatch):
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda q:{'success':True,'roll':100})
    started=modify(client,gunsmith,auth_headers);assert started.status_code==200,started.json
    assert finish(client,gunsmith,auth_headers,started.json['result']).status_code==200
    replacement=modify(client,gunsmith,auth_headers,'weapon-b23',replace_rule_id='weapon-c23',operation_id='replace-start')
    assert replacement.status_code==200,replacement.json
    response=finish(client,gunsmith,auth_headers,replacement.json['result'],operation_id='replace-finish')
    assert response.status_code==200,response.json
    weapon=response.json['data']['weapons'][0]
    assert len(weapon['modifications'])==1 and stat(weapon,'ergonomics',20)==20
    assert weapon['maxDurability']==115 and weapon['durability']==115


def test_crafting_endpoint_cannot_buy_slot_without_mechanic(client,gunsmith,auth_headers):
    response=call(client,gunsmith,auth_headers,'unlock_slot',target_path=['weapons',0])
    assert response.status_code==400,response.json
    assert gunsmith['actor_character'].data['inventory']['money']==5000
    assert not gunsmith['actor_character'].data['weapons'][0].get('unlockedModificationSlots')


def test_weapon_levels_require_every_previous_level_even_with_open_slots(client,gunsmith,auth_headers):
    data=deepcopy(gunsmith['actor_character'].data)
    data['weapons'][0]['itemClass']='3'
    gunsmith['actor_character'].data=data;db.session.commit()
    second=modify(client,gunsmith,auth_headers,'weapon-b24')
    assert second.status_code==400 and 'последовательно' in str(second.json)
    data=deepcopy(gunsmith['actor_character'].data)
    data['weapons'][0]['modifications']=[make_record('weapon-b23')]
    gunsmith['actor_character'].data=data;db.session.commit()
    assert modify(client,gunsmith,auth_headers,'weapon-b25').status_code==400
    data=deepcopy(gunsmith['actor_character'].data)
    data['weapons'][0]['modifications'].append(make_record('weapon-b24'))
    gunsmith['actor_character'].data=data;db.session.commit()
    quote=call(client,gunsmith,auth_headers,'quote_modification',rule_id='weapon-b25',
        target_path=['weapons',0],tool_path=['inventory','pockets',4])
    assert quote.status_code==200,quote.json


def test_armor_branch_order_allows_multiple_first_levels_and_checks_replacement(app):
    from app.services.crafting import modification_target_plan
    from app.services.exceptions import ValidationError

    armor={'id':'light-armor','name':'Лёгкая броня','category':'armor','itemClass':'4',
        'attributes':{'modification_category':'Лёгкая'},'modifications':[]}
    data={'equipment':{'armor':armor}}
    def plan(rule_id,**extra):
        return modification_target_plan(data,{'rule_id':rule_id,
            'target_path':['equipment','armor'],**extra})

    for rule_id in ('armor-a5','armor-a9','armor-a13'):
        assert plan(rule_id)['rule']['level']==1
        armor['modifications'].append(make_record(rule_id))
    with pytest.raises(ValidationError,match='последовательно'):
        plan('armor-a11')
    assert plan('armor-a10')['rule']['level']==2
    assert plan('armor-a6')['rule']['level']==2
    armor['modifications']=[make_record(rule_id) for rule_id in ('armor-a5','armor-a6','armor-a7')]
    with pytest.raises(ValidationError,match='последовательно'):
        plan('armor-a9',replace_rule_id='armor-a5')


def test_weapon_slot_counts_follow_item_class(app):
    from app.services.equipment_modifications import slots
    for item_class,expected in [('1',1),('2',2),('3',3),('Н',3)]:
        item={'name':'Пистолет','category':'weapon','subcategory':'Пистолеты',
              'itemClass':item_class,'modifications':[]}
        assert slots(item)['available']==expected


@pytest.mark.parametrize('rule_id,effect,expected',[
    ('armor-g58','belt_weight_reduction',.75),('armor-g66','medical_pouch_capacity_multiplier',3),
    ('armor-g77','belt_reload_ap_reduction',1),
    ('armor-g78','large_weapon_ergonomics',15),
])
def test_belt_uses_class_one_armor_rules_and_own_service_price(client,gunsmith,auth_headers,monkeypatch,rule_id,effect,expected):
    data=deepcopy(gunsmith['actor_character'].data)
    data['inventory']['pockets'].extend([
        {'id':'belt-one','name':'Пояс','category':'belt','quantity':1,'modifications':[]},
        {'id':'belt-tools','name':'Набор инструментов Бронника (Упрощенные)','category':'tool','quantity':1,'uses':30,'maxUses':30},
    ])
    gunsmith['actor_character'].data=data;db.session.commit()
    args={'rule_id':rule_id,'target_path':['inventory','pockets',5],'tool_path':['inventory','pockets',6]}
    quote=call(client,gunsmith,auth_headers,'quote_modification',**args)
    assert quote.status_code==200,quote.json
    assert quote.json['result']['slots']['factor']==1
    assert quote.json['result']['required_engineering']==11
    assert quote.json['result']['professional_price']==1000
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda q:{'success':True,'roll':100})
    started=call(client,gunsmith,auth_headers,'modify',**args)
    assert started.status_code==200,started.json
    assert started.json['data']['inventory']['pockets'][6]['uses']==20
    completed=finish(client,gunsmith,auth_headers,started.json['result'])
    assert completed.status_code==200,completed.json
    assert rule(completed.json['data']['inventory']['pockets'][5],effect)==expected


def test_missing_book_or_insufficient_tool_grade_does_not_spend_charges(client,gunsmith,auth_headers):
    data=deepcopy(gunsmith['actor_character'].data);data['crafting']['library']=[]
    gunsmith['actor_character'].data=data;db.session.commit()
    response=modify(client,gunsmith,auth_headers)
    assert response.status_code==400,response.json
    assert gunsmith['actor_character'].data['inventory']['pockets'][4]['uses']==50


def test_reanimation_protocol_heals_each_zone_once_and_needs_recharge(app):
    from app.services.equipment_care import consume_reanimation,reanimation_ready
    armor={'category':'armor','modifications':[make_record('armor-d31')]}
    data={'equipment':{'armor':armor},'health':{'current':100,'max':700,'painLevel':9,
        'zones':{'head':{'current':40,'max':50},'chest':{'current':100,'max':150}}}}
    assert reanimation_ready(data)
    assert consume_reanimation(data)
    assert data['health']['painLevel']==0
    assert data['health']['zones']['head']['current']==50
    assert data['health']['zones']['chest']['current']==110
    assert data['health']['current']==120
    assert armor['reanimationCharge'] is False
    assert not consume_reanimation(data)


def test_sheet_save_cannot_recharge_reanimation_protocol(app):
    from app.services.equipment_care import preserve
    current={'equipment':{'armor':{'id':'armor-one','category':'armor','reanimationCharge':False}}}
    incoming=deepcopy(current)
    incoming['equipment']['armor']['reanimationCharge']=True
    preserve(current,incoming)
    assert incoming['equipment']['armor']['reanimationCharge'] is False


def test_sheet_cannot_forge_rule_records(client,gunsmith,auth_headers):
    character=gunsmith['actor_character'];data=character.data_snapshot()
    data['weapons'][0]['modifications']=[make_record('weapon-c42',gunsmith=True)]
    response=client.put(f'/lobbies/characters/{character.id}',headers=auth_headers(gunsmith['actor_user']),json={'data':data})
    assert response.status_code==200,response.json
    assert not character.data['weapons'][0]['modifications']


def test_numeric_armor_protection_and_pistol_burst_use_installed_profile(app):
    armor={'name':'Костюм','category':'armor','protection':{'physical':20},'durability':100,'stage':1,
        'modifications':[make_record('armor-a5',gunsmith=True)]}
    total,_=CombatService._target_armor({'equipment':{'armor':armor}},'chest')
    assert total==35
    weapon={'modifications':[make_record('weapon-c24',gunsmith=True)]}
    assert fire_profile(weapon,{},6)['burst_size']==6


def test_launcher_upgrade_preserves_profile_and_makes_two_independent_wave_checks(grenade_game,monkeypatch):
    from app.services.equipment_modifications import explosive_profile
    from test_grenade_rules import profile
    actor=grenade_game['actor_location']
    base=profile(blast_base=100)
    upgraded=explosive_profile({'modifications':[make_record('weapon-c46',gunsmith=True)]},base)
    assert 'blast_trauma_checks' not in base and upgraded['blast_trauma_checks']==2
    calls=[]
    def trauma(target,damage,**kwargs):
        calls.append(target.character_id)
        return {'type':'hearing_damage' if len(calls)%2 else 'bleeding','roll':len(calls)}
    monkeypatch.setattr(CombatService,'_apply_blast_trauma',trauma)
    result=CombatService.resolve_explosion(actor.location_id,actor.pos_x-1,actor.pos_y,upgraded)
    entry=next(t for t in result['targets'] if t['character_id']==actor.character_id)
    assert len(entry['blast_traumas'])==2 and calls.count(actor.character_id)==2
    assert entry['blast_traumas'][0]['roll']!=entry['blast_traumas'][1]['roll']


def test_gunsmith_fractional_durability_survives_wear(app):
    from app.services.equipment_modifications import install
    weapon={'durability':100,'maxDurability':100}
    install(weapon,make_record('weapon-b44',gunsmith=True))
    assert CombatService._weapon_durability(weapon)==(122.5,122.5)
    CombatService._apply_weapon_wear(weapon,1)
    assert weapon['durability']==121.5 and weapon['maxDurability']==122.5
    assert weapon['_combatDurabilityLoss']==1


def test_extra_pockets_and_full_limb_armor_are_conditional_on_wearing(app):
    from app.services.character_traits import sync_pocket_capacity
    armor={'category':'armor','protection':{'physical':20},'durability':100,'stage':1,
        'modifications':[make_record('armor-a30',gunsmith=True),make_record('armor-a41')]}
    data={'equipment':{'armor':armor}}
    sync_pocket_capacity({},data);assert data['inventory']['pocketMaxVolume']==25
    assert CombatService._target_armor(data,'left_arm')[0]==25
    armor['protectionDisabled']=True
    sync_pocket_capacity({},data);assert data['inventory']['pocketMaxVolume']==10
    assert CombatService._target_armor(data,'left_arm')[0]==0


def test_gas_mask_mods_reduce_actual_accuracy_charisma_and_filter_consumption(app):
    from app.services.equipment_modifications import charisma_bonus
    from app.services.gas_mask_filters import consume_equipped_filter_charges
    mask={'category':'gas_mask','accuracyPenalty':3,'charismaBonus':-4,
        'modifications':[make_record('armor-g12'),make_record('armor-g11'),make_record('armor-g13')],
        'installedModules':[{'attributes':{'filter_charges':10},'durability':10}]}
    data={'equipment':{'gasMask':mask}}
    assert CombatService._equipment_accuracy_penalty(data)==0
    assert charisma_bonus(mask,-4)==0
    result=consume_equipped_filter_charges(data,1)
    assert result['consumed']==.5 and mask['installedModules'][0]['durability']==9.5


def test_tinted_lenses_and_ear_protection_do_not_protect_whole_head(app):
    from app.services.equipment_passives import flash_multiplier
    helmet={'category':'helmet','protection':{'physical':0},'stage':1,'durability':100,
        'modifications':[make_record('armor-g6'),make_record('armor-g14')]}
    data={'equipment':{'helmet':helmet}}
    assert CombatService._target_armor(data,'head',head_protection_zone='ears')[0]==15
    assert CombatService._target_armor(data,'head',head_protection_zone='eyes')[0]==0
    assert flash_multiplier(data)==.5
    assert CombatService._equipment_accuracy_penalty(data)==1


def test_hearing_advantage_does_not_apply_to_visual_checks(checks):
    data=deepcopy(checks['actor_character'].data)
    data['equipment']['helmet']['modifications']=[make_record('armor-g26')]
    hearing=CombatService._narrative_skill_check(data,'skills.physical.awareness.hearing')
    visual=CombatService._narrative_skill_check(data,'skills.physical.awareness.visual')
    assert hearing['advantage'] and len(hearing['rolls'])==2
    assert not visual['advantage'] and len(visual['rolls'])==1


@pytest.mark.parametrize('gunsmith,penalty',[(False,-1),(True,0)])
def test_helmet_reduces_concussion_without_removing_effect(checks,gunsmith,penalty):
    data=deepcopy(checks['actor_character'].data);data['health']['effects']=[{'type':'concussion','remaining':3,'active':True}]
    data['equipment']['helmet']['modifications']=[make_record('armor-g35',gunsmith=gunsmith)]
    assert CombatService._health_roll_modifier(data,'skills.other.engineering')==penalty
    assert data['health']['effects'][0]['remaining']==3


def test_extra_container_slots_are_real_and_cannot_delete_a_filled_slot(app):
    from app.services.equipment_modifications import install
    from app.services.exceptions import ValidationError
    armor={'containers':[{'item':{'id':'original-artifact'}}],'modifications':[]}
    install(armor,make_record('armor-a11'))
    assert len(armor['containers'])==2 and armor['containers'][0]['item']['id']=='original-artifact'
    armor['containers'][1]['item']={'id':'extra-artifact'};before=deepcopy(armor)
    with pytest.raises(ValidationError,match='освободите'):install(armor,make_record('armor-a7'),replacing='armor-a11')
    assert armor==before
    armor['containers'][1]['item']=None
    install(armor,make_record('armor-a7'),replacing='armor-a11')
    assert len(armor['containers'])==1 and armor['containers'][0]['item']['id']=='original-artifact'


@pytest.mark.parametrize('gunsmith,expected',[(False,5),(True,2.5)])
def test_belt_discount_applies_only_to_items_on_that_belt(app,gunsmith,expected):
    data={'inventory':{'pockets':[{'weight':3,'quantity':1}]},'equipment':{'belt':{
        'modifications':[make_record('armor-g58',gunsmith=gunsmith)],'pouches':[{'contents':[{'weight':10,'quantity':1}]}]}}}
    assert CombatService._inventory_weight_details(data)['total_weight']==3+expected


def test_fractional_accuracy_is_not_rounded_between_shots(app,monkeypatch):
    from types import SimpleNamespace
    attacker=SimpleNamespace(character=SimpleNamespace(data={'weapons':[{'durability':100,'maxDurability':100}]}))
    seen=[]
    def hit(target,actor,details,**kwargs):
        seen.append(details['hit_difficulty']);return {'roll':10,'hit':False}
    monkeypatch.setattr(CombatService,'_resolve_attack',hit)
    monkeypatch.setattr(CombatService,'_roll_character_weapon_jam',lambda *a:{'triggered':False})
    details={'weapon_index':0,'hit_difficulty':10.5,'hit_difficulty_without_weapon_jam':10.5,'shot_count':1,'fire_mode':'unaimed'}
    CombatService._resolve_shot_sequence([SimpleNamespace()],attacker,details)
    assert seen==[10.5]
    assert CombatService._area_fire_hit_count(13,10.5,4)==1


def test_armor_material_discount_uses_catalog_price_and_spends_materials_at_start(client,gunsmith,auth_headers):
    from app.models.templates import ItemTemplate
    from app.services.character_starting_purchase import item_from_template
    material=ItemTemplate(name='Проверочные металлические детали',category='crafting_material',price=150,weight=.5,volume=1,attributes={})
    db.session.add(material);db.session.flush()
    data=deepcopy(gunsmith['actor_character'].data)
    data['equipment']['armor']={'name':'Костюм','category':'armor','itemClass':'1','attributes':{'modification_category':'Лёгкая'},
        'durability':100,'maxDurability':100,'modifications':[]}
    data['crafting']['library'].append({'book_kind':'equipment','book_subject':'Лёгкая броня'})
    data['inventory']['pockets'][4]['name']='Набор инструментов Бронника (Расширенные)'
    data['inventory']['pockets'][0]=item_from_template(material,2);data['inventory']['pockets'][0]['price']=999999
    gunsmith['actor_character'].data=data;db.session.commit()
    payload={'rule_id':'armor-a5','target_path':['equipment','armor'],'tool_path':['inventory','pockets',4],
        'material_contributions':[{'path':['inventory','pockets',0],'quantity':2}]}
    quote=call(client,gunsmith,auth_headers,'quote_modification',**payload)
    assert quote.status_code==200,quote.json
    assert quote.json['result']['material_value']==300 and quote.json['result']['difficulty']==18
    started=call(client,gunsmith,auth_headers,'modify',**payload)
    assert started.status_code==200,started.json
    assert not any(i.get('templateId')==material.id for i in started.json['data']['inventory']['pockets'])
    cancelled=call(client,gunsmith,auth_headers,'cancel',job_id=started.json['result']['id'])
    assert cancelled.status_code==200,cancelled.json
    assert not any(i.get('templateId')==material.id for i in cancelled.json['data']['inventory']['pockets'])
    assert next(i for i in cancelled.json['data']['inventory']['pockets'] if i.get('id')=='upgrade-tools')['uses']==40


@pytest.mark.parametrize('gunsmith,protection,wear',[(False,45,50),(True,57.5,25)])
def test_explosion_upgrade_protects_fragments_and_reduces_real_armor_wear(grenade_game,gunsmith,protection,wear):
    target=grenade_game['actor_location'];data=deepcopy(target.character.data)
    armor={'category':'armor','protection':{'physical':20},'durability':100,'stage':1,
        'stageDurability':1000,'currentStageDurability':1000,'modifications':[make_record('armor-a20',gunsmith=gunsmith)]}
    data['equipment']={'armor':armor};target.character.data=data
    assert CombatService._target_armor(data,'chest')[0]==20
    result=CombatService._explosion_fragment_hit(target,100,100,'chest',{'protection':0},1)
    assert result['armor']==protection and result['damage']==100
    assert result['armor_damage'][0]['damage']==wear
    assert armor['currentStageDurability']==1000-wear


@pytest.mark.parametrize('gunsmith,wear',[(False,100),(True,50)])
def test_anomaly_upgrade_halves_actual_equipment_wear(app,gunsmith,wear):
    armor={'category':'armor','durability':100,'stage':1,'stageDurability':1000,'currentStageDurability':1000,
        'modifications':[make_record('armor-d10',gunsmith=gunsmith)]}
    result=CombatService._apply_anomaly_equipment_damage({'equipment':{'armor':armor}},100,{'armor_damage_multiplier':2})
    assert result[0]['damage']==wear and armor['currentStageDurability']==1000-wear


def shotgun(case):
    data=deepcopy(case['actor_character'].data)
    data['weapons']=[{'id':'mod-shotgun','name':'Проверочный дробовик','category':'weapon','subcategory':'Дробовики',
        'accuracy':0,'range':20,'ergonomics':50,'minStrength':0,'durability':100,'maxDurability':100,'fireRate':10,
        'fireModes':{'supports_burst':True,'supports_area_fire':True,'burst_size':2},
        'installedMagazine':{'ammo':[{'name':'12х70 Картечь','quantity':4,
            'attributes':{'caliber':'12x70','ammo_variant':'buckshot','damage':30,'armor_piercing':0}}]},
        'modifications':[make_record('weapon-c29')]}]
    data['equipment']={};case['actor_character'].data=data;db.session.commit()


def test_buckshot_upgrade_checks_each_target_even_when_first_misses(client,flame_game,auth_headers,monkeypatch):
    from app.models import LobbyCharacter,LocationCharacter
    shotgun(flame_game);case=flame_game;actor=case['actor_location']
    other=LobbyCharacter(lobby_id=case['lobby'].id,owner_id=case['target_user']['id'],name='Вторая цель',data=deepcopy(case['target_character'].data))
    db.session.add(other);db.session.flush()
    db.session.add(LocationCharacter(location_id=actor.location_id,character_id=other.id,pos_x=8,pos_y=5));db.session.commit()
    original=CombatService._resolve_attack;checks=[]
    def resolve(target,attacker,details,**kwargs):
        kwargs['forced_roll']=2 if not checks else 20
        result=original(target,attacker,{**details,'hit_difficulty':12},**kwargs)
        checks.append((target.character_id,result['hit']));return result
    monkeypatch.setattr(CombatService,'_resolve_attack',resolve)
    response=client.post(f'/lobbies/{case["lobby"].id}/locations/{actor.location_id}/combat/action',headers=auth_headers(case['actor_user']),json={
        'location_character_id':actor.id,'action_key':'attack','weapon_index':0,'fire_mode':'area',
        'target_character_ids':[case['target_character'].id,other.id],'area_center_x':7,'area_center_y':5,
        'shot_count':4,'volley_count':2,'action_points':5})
    assert response.status_code==200,response.json
    assert checks==[(case['target_character'].id,False),(other.id,True)]
    assert actor.action_points_current==15
    assert not actor.character.data['weapons'][0]['installedMagazine']['ammo']


@pytest.mark.parametrize('braced,bonus',[(False,0),(True,2)])
def test_supported_armor_accuracy_requires_actual_support(client,flame_game,auth_headers,monkeypatch,braced,bonus):
    shotgun(flame_game);case=flame_game;actor=case['actor_location']
    data=deepcopy(actor.character.data)
    data['equipment']['armor']={'category':'armor','modifications':[make_record('armor-a48')]}
    actor.character.data=data;actor.weapon_braced=braced;actor.braced_weapon_index=0 if braced else None;db.session.commit()
    original=CombatService._resolve_attack;seen=[]
    def resolve(target,attacker,details,**kwargs):
        seen.append(details['weapon_accuracy']);return original(target,attacker,details,**kwargs)
    monkeypatch.setattr(CombatService,'_resolve_attack',resolve)
    response=client.post(f'/lobbies/{case["lobby"].id}/locations/{actor.location_id}/combat/action',headers=auth_headers(case['actor_user']),json={
        'location_character_id':actor.id,'action_key':'attack','weapon_index':0,'fire_mode':'unaimed',
        'target_character_id':case['target_character'].id,'shot_count':1,'action_points':2})
    assert response.status_code==200,response.json
    assert seen==[bonus]


def test_helmet_explosion_upgrade_and_combined_anomaly_tradeoff(app):
    helmet={'category':'helmet','durability':100,'protection':{'physical':30},'modifications':[make_record('armor-g40')]}
    data={'equipment':{'helmet':helmet}}
    assert CombatService._target_armor(data,'head')[0]==30
    assert CombatService._target_armor(data,'head',explosive=True)[0]==50
    armor={'category':'armor','protection':{'radiation':10,'thermal':30},'modifications':[make_record('armor-d24',gunsmith=True)]}
    data={'equipment':{'armor':armor}}
    assert CombatService._equipped_radiation_protection(data)==45
    assert CombatService._target_elemental_protection(data,'thermal')==20


@pytest.mark.parametrize('gunsmith,bonus',[(False,4),(True,6)])
def test_stealth_upgrade_affects_only_worn_armor(app,monkeypatch,gunsmith,bonus):
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    armor={'modifications':[make_record('armor-a14',gunsmith=gunsmith)]}
    data={'equipment':{'armor':armor}}
    def total(skill):return CombatService._narrative_skill_check(data,skill)['total']
    baseline=total('skills.other.stealth')
    armor['protectionDisabled']=True
    assert total('skills.other.stealth')==baseline-bonus
    armor.pop('protectionDisabled')
    assert total('skills.physical.awareness.visual')==CombatService._narrative_skill_check(
        {'equipment':{}},'skills.physical.awareness.visual')['total']


@pytest.mark.parametrize('amplified,bonus',[(False,10),(True,15)])
def test_belt_padding_requires_weapon_size_six(gunsmith,amplified,bonus):
    actor=gunsmith['actor_location']
    data=deepcopy(actor.character.data)
    weapon=data['weapons'][0]
    data['equipment']={'belt':{'category':'belt','modifications':[make_record('armor-g78',gunsmith=amplified)]}}
    actor.character.data=data;db.session.commit()
    def value():return CombatService._weapon_ergonomics_profile(actor,weapon,0)['value']
    weapon['size']=5
    small=value()
    weapon['size']=6
    assert value()==small+bonus
    data['equipment']['belt']['protectionDisabled']=True
    actor.character.data=data
    assert value()==small


def test_work_gloves_lower_modification_difficulty(client,gunsmith,auth_headers):
    args={'rule_id':'weapon-c23','target_path':['weapons',0],'tool_path':['inventory','pockets',4]}
    baseline=call(client,gunsmith,auth_headers,'quote_modification',**args)
    assert baseline.status_code==200,baseline.json
    gloves=ItemTemplate(name='Рабочие',category='gloves',price=100,
        attributes={'craft_difficulty_modifier':-5})
    db.session.add(gloves);db.session.flush()
    data=deepcopy(gunsmith['actor_character'].data)
    data['equipment']={'gloves':{'name':'Рабочие','category':'gloves','templateId':gloves.id}}
    gunsmith['actor_character'].data=data;db.session.commit()
    equipped=call(client,gunsmith,auth_headers,'quote_modification',**args)
    assert equipped.status_code==200,equipped.json
    assert equipped.json['result']['difficulty']==baseline.json['result']['difficulty']-5


@pytest.mark.parametrize('gunsmith,expected_blast,expected_wear',[(False,55,27.5),(True,42,10.5)])
def test_explosion_upgrade_changes_wave_damage_and_real_wear(grenade_game,monkeypatch,gunsmith,expected_blast,expected_wear):
    from test_grenade_rules import profile
    target=grenade_game['actor_location'];data=deepcopy(target.character.data)
    data['equipment']={'armor':{'category':'armor','protection':{'physical':20},'durability':100,
        'stageDurability':1000,'currentStageDurability':1000,'modifications':[make_record('armor-a20',gunsmith=gunsmith)]}}
    target.character.data=data;db.session.commit()
    monkeypatch.setattr(CombatService,'_explosion_is_indoors',lambda *a:False)
    result=CombatService.resolve_explosion(target.location_id,target.pos_x,target.pos_y,
        profile(fragment=0,blast_base=100,blast_falloff=0),target_ids=[target.id])
    entry=result['targets'][0]
    assert entry['blast_damage']==expected_blast
    assert entry['blast_armor_damage'][0]['damage']==expected_wear


@pytest.mark.parametrize('gunsmith,reduction',[(False,3),(True,4.5)])
def test_anomaly_exit_upgrade_changes_real_check_difficulty(app,monkeypatch,gunsmith,reduction):
    from test_combat_anomalies import _combat_with_anomaly
    from app.services.anomaly_profiles import anomaly_profile
    user,location,actor,placed,_=_combat_with_anomaly()
    placed.pos_x=placed.pos_y=2
    CombatService._enter_anomaly(placed,CombatService._anomalies_at_tile(location,2,2)[0],1)
    data=deepcopy(actor.data);data['equipment']={'armor':{'modifications':[make_record('armor-d23',gunsmith=gunsmith)]}}
    actor.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:20)
    result=CombatService.perform_action(location.id,user.id,placed.id,'escape_anomaly',target_x=1,target_y=2)
    assert result['anomaly']['check']['difficulty']==max(1,anomaly_profile('otboynik')['dc']-reduction)
    assert (placed.pos_x,placed.pos_y)==(1,2) and placed.action_points_current==2
