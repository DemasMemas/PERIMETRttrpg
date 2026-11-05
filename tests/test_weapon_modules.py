"""Полный каталог и реальные действия с оружейными модулями."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.combat import CombatService
from app.services.exceptions import ValidationError
from app.services.weapon_modules import builtin_templates,missing_templates,weapon_stat,conditional_accuracy,uses_nvg
from test_grenade_rules import grenade_game
from test_character_creation import creation_case


def module(key):
    t=next(t for t in builtin_templates() if t['attributes']['moduleRuleId']==key)
    return {**deepcopy(t),'templateId':t['id'],'slotType':t['subcategory'],'quantity':1}


def test_all_thirteen_modules_have_approved_price_tier_weight_volume_and_slot():
    items=builtin_templates()
    assert len(items)==13 and len({t['id'] for t in items})==13
    assert all(t['weight']==.5 and t['volume']==1 for t in items)
    assert [t['price'] for t in items]==[5000,3500,0,3500,7500,5000,7500,3500,6500,5000,1500,12500,15000]
    assert [t['item_class'] for t in items]==['2','2',None,'1','2','2','2','1','2','2','1','3','3']
    assert {t['subcategory'] for t in items}=={'scope','barrel','handguard'}
    assert module('short_barrel')['attributes']['removable'] is False
    assert module('short_barrel')['attributes']['not_for_sale']
    assert module('bayonet')['attributes']['allowed_attacks']==['Колющий']


def test_existing_modules_and_same_named_melee_weapon_do_not_duplicate_or_hide_catalog():
    existing=[{'name':'Сошки','category':'weapon_module','attributes':{}},
              {'name':'Штык-нож','category':'melee_weapon','attributes':{}}]
    missing=missing_templates(existing)
    assert len(missing)==12 and any(t['name']=='Штык-нож' for t in missing)
    assert len(missing_templates(existing,'scope'))==3


def test_old_catalog_module_receives_defaults_without_mutating_source():
    from app.services.weapon_catalog import normalize_template
    old={'name':'Сошки','category':'weapon_module','weight':.5,'volume':.5,'attributes':{'modifiers':{'ergonomics':-25}}}
    result=normalize_template(old)
    assert result['volume']==1 and result['weight']==.5 and result['attributes']['bipod']
    assert result['attributes']['modifiers']['ergonomics']==-25
    assert old['volume']==.5 and 'bipod' not in old['attributes']


def test_templates_api_exposes_modules_without_running_data_migrations(creation_case,client):
    c=creation_case;url=f'/lobbies/{c["lobby"].id}/templates'
    response=client.get(url+'?category=weapon_module',headers=c['headers'][c['player'].id])
    assert response.status_code==200
    assert len(response.json['global'])==13
    response=client.get(url+'?category=weapon_module&subcategory=scope',headers=c['headers'][c['player'].id])
    assert len(response.json['global'])==3


def test_module_migration_is_repeatable_and_preserves_custom_tuning(app,monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    old=ItemTemplate(name='Сошки',category='weapon_module',weight=.5,volume=.5,price=6000,
                     attributes={'bipod':True,'custom':'настройка ГМа','modifiers':{'ergonomics':-25}})
    db.session.add(old);db.session.flush()
    spec=importlib.util.spec_from_file_location('module_migration',Path('migrations/versions/f9d5a7132c04_add_weapon_modules_catalog.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(db.session.connection())))
    migration.upgrade();migration.upgrade();db.session.expire_all()
    assert ItemTemplate.query.filter_by(category='weapon_module').count()==13
    db.session.refresh(old)
    assert old.price==6000 and old.attributes['custom']=='настройка ГМа'
    assert old.attributes['modifiers']['ergonomics']==-25 and old.volume==1


@pytest.mark.parametrize('key,stat,base,expected',[
    ('suppressor','noise',40,2),('muzzle_brake','accuracy',0,1),('short_barrel','accuracy',1,-1),
    ('collimator','range',20,25),('optical_scope','range',20,35),('night_scope','range',20,25),
    ('laser','accuracy',0,1),('tactical_grip','accuracy',0,1),('bipod','ergonomics',50,20),
])
def test_base_modifiers(key,stat,base,expected):
    assert weapon_stat({'installedModules':[module(key)]},stat,base)==expected


@pytest.mark.parametrize('key,mode,distance,posture,nvg,expected',[
    ('optical_scope','aimed',9.9,'standing',False,-1),('optical_scope','aimed',10,'standing',False,0),
    ('optical_scope','unaimed',5,'standing',False,0),('laser','unaimed',5,'standing',False,1),
    ('laser','aimed',5,'standing',False,0),('laser','aimed',5,'standing',True,1),
    ('laser','unaimed',5,'standing',True,1),('tactical_grip','aimed',5,'sitting',False,1),
    ('tactical_grip','aimed',5,'prone',False,0),
])
def test_conditional_modifiers(key,mode,distance,posture,nvg,expected):
    assert conditional_accuracy({'installedModules':[module(key)]},fire_mode=mode,distance=distance,posture=posture,nvg=nvg)==expected


def test_nvg_in_inventory_is_not_in_use():
    item={'slotType':'nvg','name':'ПНВ'}
    assert not uses_nvg({'inventory':{'pockets':[item]}})
    data={'equipment':{'helmet':{'installedModules':[item]}}}
    assert uses_nvg(data)
    item['enabled']=False;assert not uses_nvg(data)
    assert not uses_nvg({'equipment':{'helmet':'Старое название шлема'}})
    assert not uses_nvg({'equipment':{'helmet':{'installedModules':{}}}})


@pytest.mark.parametrize('penalty', [-2, -1, 0])
def test_nvg_applies_its_numeric_penalty_without_darkness_disadvantage(app, monkeypatch, penalty):
    module={'slotType':'nvg','attributes':{'night_vision':True,'accuracy_penalty':penalty,'awareness_penalty':penalty}}
    data={'equipment':{'helmet':{'installedModules':[module]}},'health':{'effects':[]}}
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:10)
    for path in ('skills.physical.awareness.visual','skills.physical.shooting'):
        result=CombatService._narrative_skill_check(data,path,darkness=True)
        assert result['equipment_modifier']==penalty and not result['disadvantage']
        assert CombatService._narrative_skill_check(data,path,darkness=False)['equipment_modifier']==0
    assert CombatService._narrative_skill_check(data,'skills.physical.awareness.hearing',darkness=True)['equipment_modifier']==0
    module['raised']=True
    result=CombatService._narrative_skill_check(data,'skills.physical.awareness.visual',darkness=True)
    assert result['equipment_modifier']==-5 and not result['disadvantage']


def prepare_gun(case,keys):
    actor,target=case['actor_location'],case['target_location']
    target.pos_x,target.pos_y=actor.pos_x+3,actor.pos_y
    data=deepcopy(actor.character.data)
    data['weapons']=[{'id':'module-gun','name':'Проверочный автомат','subcategory':'Штурмовые винтовки','accuracy':0,'range':20,
        'damage':10,'durability':100,'maxDurability':100,'ergonomics':100,'fixedMagazine':True,'ammo':10,
        'fixedAmmo':[{'name':'Патрон','quantity':10,'attributes':{'damage':10}}],
        'attributes':{'noise':40,'fire_rate':5,'min_strength':0,'magazine_size':10},'installedModules':[module(key) for key in keys]}]
    actor.character.data=data;actor.drawn_weapon_index=0;actor.action_points_current=30;db.session.commit()
    return actor,target


def shoot(case,actor,target,mode='unaimed'):
    if mode=='aimed':
        CombatService.perform_action(actor.location_id,case['lobby'].gm_id,actor.id,'aim',weapon_index=0,target_character_id=target.character_id)
    cost=2 if mode=='unaimed' else CombatService._weapon_ergonomics_profile(actor,actor.character.data['weapons'][0],0)['aimed_shot_action_points']
    return CombatService.perform_action(actor.location_id,case['lobby'].gm_id,actor.id,'attack',
        weapon_index=0,fire_mode=mode,shot_count=1,target_character_id=target.character_id,
        action_points=cost,target_zone='chest' if mode=='aimed' else None)['attack']


def test_real_shot_uses_suppressor_range_and_accuracy(grenade_game,monkeypatch):
    actor,target=prepare_gun(grenade_game,['suppressor','collimator','laser'])
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:a)
    result=shoot(grenade_game,actor,target)
    assert result['noise']==2 and result['weapon_range']==25
    assert result['weapon_accuracy']==2 and result['module_conditional_accuracy']==1
    assert CombatService._deafness_level(actor.character.data)==1


def test_night_scope_only_removes_numeric_darkness_for_aimed_shot(grenade_game,monkeypatch):
    actor,target=prepare_gun(grenade_game,['night_scope'])
    monkeypatch.setattr(CombatService,'_is_dark_at_position',staticmethod(lambda *args,**kwargs:True))
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:a)
    result=shoot(grenade_game,actor,target,'aimed')
    assert not result['base_shooting_disadvantage']
    result=shoot(grenade_game,actor,target,'unaimed')
    assert not result['base_shooting_disadvantage']


def test_real_firearm_shot_pays_numeric_nvg_penalty(grenade_game, monkeypatch):
    actor,target=prepare_gun(grenade_game,[])
    data=deepcopy(actor.character.data)
    data.setdefault('equipment',{})['helmet']={'installedModules':[{'slotType':'nvg','attributes':{'night_vision':True,'accuracy_penalty':0}}]}
    actor.character.data=data;db.session.commit()
    monkeypatch.setattr(CombatService,'_is_dark_at_position',staticmethod(lambda *args,**kwargs:True))
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:10 if b==20 else b)
    baseline=shoot(grenade_game,actor,target)
    data=deepcopy(actor.character.data);data['equipment']['helmet']['installedModules'][0]['attributes']['accuracy_penalty']=-2
    actor.character.data=data;db.session.commit()
    result=shoot(grenade_game,actor,target)
    assert not result['base_shooting_disadvantage']
    assert result['hit_difficulty']==baseline['hit_difficulty']+2


def test_bayonet_uses_melee_catalog_piercing_profile_and_two_ap(grenade_game,monkeypatch):
    actor,target=prepare_gun(grenade_game,['bayonet'])
    target.pos_x=actor.pos_x+1
    knife=ItemTemplate(name='Штык-нож',category='melee_weapon',weight=.5,
        attributes={'damage':40,'accuracy':1,'armor_piercing':15,'allowed_attacks':['Колющий','Режущий'],'weight_class':'Легкое'})
    db.session.add(knife);db.session.commit()
    profile=CombatService._virtual_melee_profile('bayonet',actor.character.data['weapons'][0])
    assert profile['damage']==50 and profile['armor_piercing']==25
    assert profile['action_points']==2 and profile['melee_damage_type']=='piercing'
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:20 if b==20 else a)
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',
        weapon_index=0,attack_type='bayonet',target_character_id=target.character_id)
    assert result['attack']['action_points']==2
    assert actor.action_points_current==28
    assert actor.character.data['weapons'][0]['ammo']==10
    with pytest.raises(ValidationError,match='не установлен'):
        CombatService._virtual_melee_profile('bayonet',{'installedModules':[]})


def test_bipod_ignores_strength_on_rest_as_well_as_prone():
    gun={'minStrength':15,'attributes':{'min_strength':15},'installedModules':[module('bipod')]}
    actor=SimpleNamespace(character=SimpleNamespace(data={'weapons':[gun],'skills':{'physical':{'strength':{'base':5}}}}),
        posture='standing',weapon_braced=True,braced_weapon_index=0)
    assert CombatService._weapon_strength_profile(actor,gun)['ignored_by_bipod']


@pytest.mark.parametrize('key,caliber,name',[
    ('gp25','ВОГ-25','ВОГ-25'),('n101_2','N-101-2','N-101-2'),
])
def test_underbarrels_reload_and_fire_canonical_ammunition(grenade_game,monkeypatch,key,caliber,name):
    actor,target=prepare_gun(grenade_game,[key])
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'id':'launcher-round','name':name,'category':'grenade','quantity':2,
                                    'weight':.3,'volume':1,'attributes':{'caliber':caliber}}]
    actor.character.data=data;db.session.commit()
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'reload_underbarrel',
        weapon_index=0,item_path=['inventory','pockets',0])
    assert result['reload_underbarrel']['action_points']==6  # 5 за перезарядку + 1 достать из кармана
    assert result['reload_underbarrel']['inventory_retrieval_action_points']==1
    assert actor.character.data['inventory']['pockets'][0]['quantity']==1
    assert actor.character.data['weapons'][0]['installedModules'][0]['loadedGrenade']['name']==name
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:20 if b==20 else a)
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'explosive_attack',
        weapon_index=0,explosive_source='underbarrel',target_x=12,target_y=5.2)
    assert result['explosive']['source']=='underbarrel'
    assert not actor.character.data['weapons'][0]['installedModules'][0]['loadedGrenade']
    assert actor.character.data['weapons'][0]['ammo']==10


def test_underbarrel_nvg_removes_only_darkness_disadvantage(grenade_game,monkeypatch):
    actor,target=prepare_gun(grenade_game,['gp25'])
    data=deepcopy(actor.character.data)
    data.setdefault('equipment',{})['helmet']={'installedModules':[{'slotType':'nvg','attributes':{'night_vision':True,'accuracy_penalty':-2}}]}
    data['weapons'][0]['installedModules'][0]['loadedGrenade']={'name':'ВОГ-25','category':'grenade','quantity':1,'attributes':{'caliber':'ВОГ-25'}}
    actor.character.data=data;db.session.commit()
    monkeypatch.setattr(CombatService,'_is_dark_at_position',staticmethod(lambda *args,**kwargs:True))
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:20 if b==20 else b)
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'explosive_attack',
        weapon_index=0,explosive_source='underbarrel',target_x=12,target_y=5.2)
    assert not result['explosive']['disadvantage']
    assert result['explosive']['roll_kind']=='shooting'
