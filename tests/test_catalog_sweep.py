"""Расширенная проверка каталога по прямому поручению автора; только guarded memory DB."""
import hashlib
import json
import csv
import math
import subprocess
import pytest
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from app.services.equipment_importer import parse_equipment_templates
from app.services.consumable_importer import parse_consumable_templates_v2


REPORT = Path(__file__).resolve().parents[1] / '.private-testing/reports/catalog-sweep-2026-11-04'
SOURCE = Path.home() / 'Downloads/Снаряжение (3).xlsx'
# Книга автора — локальный источник, не зависимость обычного CI проекта.
pytestmark = pytest.mark.skipif(not SOURCE.is_file(), reason='Локальная книга каталога недоступна для расширенного аудита')


@lru_cache(maxsize=1)
def source_catalog():
    assert SOURCE.is_file(), 'Исходная книга снаряжения недоступна'
    equipment = parse_equipment_templates(SOURCE)
    medicines = parse_consumable_templates_v2(SOURCE)
    return equipment, medicines


def test_catalog_sweep_source_inventory(app):
    assert app.testing and str(app.config['SQLALCHEMY_DATABASE_URI']) in {'sqlite://', 'sqlite:///:memory:'}
    equipment, medicines = source_catalog()
    categories = {}
    for item in equipment:
        categories[item['category']] = categories.get(item['category'], 0) + 1
    assert categories.get('ammo', 0) > 10 and categories.get('armor', 0) > 10
    assert len(medicines) > 10
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / 'source.json').write_text(json.dumps({
        'source': str(SOURCE), 'sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'categories': categories, 'consumable_count': len(medicines),
        'equipment': equipment, 'consumables': medicines,
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def test_every_catalog_ammunition_against_every_armor_and_helmet(app, client, create_user, auth_headers, monkeypatch):
    from app.extensions import db
    from app.services.combat import CombatService
    from test_character_interactions import _setup_pair
    assert app.testing and str(app.config['SQLALCHEMY_DATABASE_URI']) in {'sqlite://', 'sqlite:///:memory:'}
    # Сначала сохраняется полный источник; варианты строятся кодом реального листа.
    test_catalog_sweep_source_inventory(app)
    subprocess.run(['node', str(Path(__file__).parent/'helpers/catalog_ammo_variants.cjs')], check=True,
                   cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=30)
    variants=json.loads((REPORT/'ammo-variants.json').read_text(encoding='utf-8'))
    equipment,_=source_catalog()
    protections=[None]+[item for item in equipment if item['category'] in {'armor','helmet'}]
    pair=_setup_pair(client,create_user,auth_headers)
    actor,target=pair['actor_location'],pair['target_location']
    actor.pos_x,actor.pos_y=1,1;target.pos_x,target.pos_y=3,1
    actor.facing_x,actor.facing_y=1,0
    target.facing_x,target.facing_y=-1,0
    # Не статистическая вероятность: одинаковый обычный успешный бросок, без критов.
    monkeypatch.setattr('app.services.combat.random',SimpleNamespace(randint=lambda low,high:min(high,max(low,10))))
    rows=[]
    for entry in variants:
        ammo=entry['item']
        # Подствольные снаряды используют взрывную систему и отмечены отдельно, а не как пули.
        if entry['base_name'].startswith('Подствольный'):
            continue
        effective=float(ammo['attributes'].get('effective_range') or ammo['attributes'].get('range') or 10)
        for distance in sorted({2.,5.,effective+5}):
            for protection in protections:
                target.pos_x = actor.pos_x + distance
                zone='head' if protection and protection['category']=='helmet' else 'chest'
                health={'current':700,'max':700,'painLevel':0,'stress':0,'effects':[],
                        'zones':{'head':{'current':100,'max':100},'chest':{'current':100,'max':100}}}
                item=deepcopy(protection) if protection else None
                if item:
                    item.update(durability=item['attributes']['max_durability'],maxDurability=item['attributes']['max_durability'])
                target.character.data={'health':health,'equipment':{protection['category']:item} if protection else {}}
                target.effects=[];target.hp_zones={};target.posture='standing'
                actor.character.data={'health':{'current':700,'max':700,'effects':[]},'weapons':[{
                    'name':'Проверочный ствол','attributes':{'range':1000},'fixedAmmo':[deepcopy(ammo)]}]}
                result=CombatService._resolve_attack(target,actor,{'weapon_index':0,'fire_mode':'unaimed',
                    'hit_difficulty':1,'round_number':1,'target_distance':distance},aimed_zone=zone,forced_roll=15)
                values={key:result.get(key,0) for key in ('base_damage','armor','armor_piercing','damage')}
                assert all(isinstance(v,(int,float)) and math.isfinite(v) and v>=0 for v in values.values())
                after=target.character.data['health']
                if result.get('base_damage',0) <= 0:
                    assert not result.get('bleedings') and not result.get('additional_traumas'), ammo['name']
                wear=sum(d.get('damage',0) for d in result.get('armor_damage') or [])
                if protection and result.get('base_damage',0)>0 and not result.get('gas_or_chemical_blocked') and (
                        protection['name']=='Костюм Химзащиты' or protection['category']=='helmet' and protection['attributes'].get('requires_filter')):
                    assert wear==result['base_damage'],(ammo['name'],protection['name'],wear,result['base_damage'])
                rows.append({'ammo':ammo['name'],'variant':entry['variant'],'distance':distance,
                    'protection':protection['name'] if protection else 'Без брони','zone':zone,
                    'head_part':(result.get('head_impact') or {}).get('part',''),**values,
                    'hp_after':after['current'],'pain_after':after.get('painLevel',0),
                    'armor_loss':wear,
                    'armor_stage_after':item.get('stage') if item else None,
                    'base_durability_after':item.get('durability') if item else None,
                    'effects':'; '.join(e.get('type','') for e in after.get('effects') or []),
                    'bleeding_count':len(result.get('bleedings') or []),
                    'trauma_count':len(result.get('additional_traumas') or []),
                    'ammunition_pain':result.get('ammunition_pain',0),
                    'ignited_rounds':result.get('ignited_rounds',0),
                    'hit_roll':result.get('roll'), 'death':bool(result.get('death'))})
        db.session.rollback()
    with (REPORT/'ammunition-armor.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (REPORT/'ammunition-summary.json').write_text(json.dumps({'rows':len(rows),
        'ammo_variants':len({r['ammo'] for r in rows}), 'armors':18,'helmets':17,
        'method':'Настоящий _resolve_attack, варианты из листа; один обычный бросок 15, остальные броски 10; каждый выстрел по свежему экземпляру. Голова — темя (10). Это расчёт механики, не вероятность травмы и не полный тест интерфейса.',
        'projectiles_separate':[e['base_name'] for e in variants if e['base_name'].startswith('Подствольный')]},ensure_ascii=False,indent=2),encoding='utf-8')
    assert rows


def test_every_consumable_through_actual_use_api(app, client, create_user, auth_headers, monkeypatch):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.models import LocationCombatState
    from sqlalchemy.orm.attributes import flag_modified
    from app.services.effects import normalize_effect_list, advance_timed_effects
    from test_character_interactions import _setup_pair
    assert app.testing
    _,catalog=source_catalog()
    installed={}
    for raw in catalog:
        template=ItemTemplate(**{k:deepcopy(v) for k,v in raw.items() if k in {
            'name','category','subcategory','item_class','description','price','weight','volume','attributes','compatible_ids'}})
        db.session.add(template);db.session.flush();installed[raw['name']]=template
    db.session.commit()
    pair=_setup_pair(client,create_user,auth_headers)
    actor=pair['actor_character'];target=pair['target_character']
    combat=LocationCombatState.query.filter_by(location_id=pair['location'].id).first();combat.status='idle';db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random',SimpleNamespace(randint=lambda low,high:high))
    rows=[]
    for ordinal,raw in enumerate(catalog):
        if raw['category'] != 'consumable':
            rows.append({'name':raw['name'],'status':'MODULE','reason':'Устанавливаемый фильтр; отдельная проверка установки','description':raw['description']})
            continue
        if raw['name'] in {'Капельница','Ингибитор'}:
            rows.append({'name':raw['name'],'status':'TOOL' if raw['name']=='Капельница' else 'DEFERRED',
                'reason':'Приспособление проверяется вместе с инфузией' if raw['name']=='Капельница' else 'Выброс/Ингибитор отложены автором, №92',
                'description':raw['description']})
            continue
        template=installed[raw['name']]
        profile=raw['attributes']['consumable'];direct=profile['direct']
        item={'id':'sweep-'+str(ordinal),'templateId':template.id,'name':template.name,'category':'consumable',
              'quantity':2,'uses':int(direct.get('uses') or 20),'maxUses':int(direct.get('uses') or 20),
              'attributes':deepcopy(template.attributes)}
        if direct.get('blood_packet'):
            item['attributes'].update(bloodType=1,bloodTypeKnown=True)
        effects=[]
        application={'kind':'self','actionPoints':0}
        health={'current':300,'max':700,'painLevel':5,'exhaustion':5,'stress':3,'radiation':50,'psyState':25,
                'intoxication':10,'infection':10,'temperature':38,'blood':'light','bloodStage':'light',
                'bloodGroup':1,'combatMeta':{'bloodType':1,'bloodTypeKnown':True},'needs':{'sleptToday':False},'effects':effects,
                'zones':{k:{'current':50,'max':100} for k in ('leftArm','rightArm','leftLeg','rightLeg','head','chest')}}
        if direct.get('requires_shock'):
            effects.append({'id':'sweep-shock','type':'shock','tick':'manual'})
        if direct.get('affects_all_limbs'):
            health['zones']['leftArm']['current']=0
            effects.append({'id':'sweep-disabled-fracture','type':'fracture','area':'leftArm','tick':'manual'})
        if direct.get('applications'):
            option=deepcopy(direct['applications'][0])
            effect={'id':'sweep-bleed','type':'bleeding_'+('internal' if option.get('internal') or option.get('internal_only') else 'external')+'_light','area':'leftArm'}
            effects.append(effect);application={'kind':'bleeding','effect':effect,'application':option,'actionPoints':option.get('action_points',1)}
        elif direct.get('wound_treatment'):
            effect={'id':'sweep-wound','type':'untreated_wound','area':'leftArm'}
            effects.append(effect);application={'kind':'wound','effect':effect,'actionPoints':1}
        elif direct.get('fracture_splint'):
            effect={'id':'sweep-fracture','type':'fracture','area':'leftArm'}
            effects.append(effect);application={'kind':'injury','treatmentMode':'fix_fracture','effect':effect,'actionPoints':1}
        elif direct.get('special_limb_treatment') or direct.get('surgical_kit'):
            health['zones']['leftArm']['current']=0
            application={'kind':'injury','treatmentMode':'restore_disabled_limb','effect':{'area':'leftArm'},'actionPoints':1}
        if direct.get('stop_all_bleeding'):
            effects.extend([{'id':'sweep-external','type':'bleeding_external_severe','area':'rightArm'},
                            {'id':'sweep-internal','type':'bleeding_internal_medium','area':'chest'},
                            {'id':'sweep-preserve-fracture','type':'fracture','area':'rightLeg'}])
        if direct.get('blood_type_test'):
            application={'kind':'blood_type_test','target':'character','actionPoints':1}
        health['effects']=normalize_effect_list(effects)
        common={'skills':{'other':{'medicine':{'base':20}},'physical':{'strength':{'base':20},'will':{'base':20}}},
                'inventory':{'pockets':[item,{'id':'lighter','name':'Зажигалка','quantity':1},
                    {'id':'water','name':'Вода','quantity':10,'attributes':{'litres':10}},
                    {'id':'infusion','name':'Капельница','templateId':installed['Капельница'].id,'quantity':1,
                     'attributes':deepcopy(installed['Капельница'].attributes)}],'backpack':[]},
                'equipment':{'gasMask':{'name':'Противогаз','durability':100,'maxDurability':100,
                    'attributes':{'protection':{'chemical':.5,'radiation':.5}},'installedModules':[]}},
                'health':deepcopy(health)}
        actor.data=common
        target.data={'health':deepcopy(health),'inventory':{},'equipment':{}}
        flag_modified(actor,'data');flag_modified(target,'data');db.session.commit()
        # Бессознательную цель лечит отдельный врач; обычные ампулы — на себе.
        recipient=actor
        if direct.get('requires_shock') or direct.get('surgical_kit'):
            recipient=target
            actor.data={**actor.data,'health':{'current':700,'max':700,'effects':[]}}
            flag_modified(actor,'data');db.session.commit()
        medical=bool(direct.get('medical_difficulty') is not None or direct.get('application_form')=='injectable'
                     or direct.get('requires_shock') or direct.get('applications') or direct.get('requires_injury')
                     or direct.get('blood_collection') or direct.get('blood_type_test') or direct.get('requires_infusion_tool') or direct.get('wound_treatment'))
        endpoint='medical-procedure' if medical else 'general-consumable'
        before=deepcopy(recipient.data['health'])
        response=client.post(f'/lobbies/characters/{actor.id}/{endpoint}',headers=auth_headers(pair['actor_user']),json={
            'operation_id':'catalog-sweep-'+str(ordinal),'target_character_id':recipient.id,
            'source':{'path':['inventory','pockets',0],'snapshot':deepcopy(item)},'application':application})
        row={'name':raw['name'],'description':raw['description'],'section':raw['attributes']['section'],
             'endpoint':endpoint,'status':str(response.status_code),'direct':deepcopy(direct),
             'declared_effects':deepcopy(profile.get('effects') or []),'declared_modifiers':deepcopy(profile.get('modifiers') or [])}
        if response.status_code == 200:
            db.session.refresh(recipient)
            row['before']=before;row['after']=deepcopy(recipient.data['health'])
            advanced=deepcopy(row['after'])
            advanced['effects']=advance_timed_effects(advanced,advanced.get('effects') or [],600,advance_medication_turns=True)
            row['after_10_minutes']=advanced
            if direct.get('affects_all_limbs'):
                assert row['after']['zones']['leftArm']['current']==1, raw['name']
                assert advanced['zones']['leftArm']['current']==0, raw['name']
                restoration=next(e for e in row['after']['effects'] if e.get('type')=='temporary_limb_restoration')
                assert restoration['tick']=='movement_end' and restoration['remaining']==1,raw['name']
            # Числа из книги, независимо от полей исполняемого профиля.
            expected={
                'Радогон':({'radiation':25,'exhaustion':6.5},{}),
                'Радогон-Экстра':({'radiation':25,'exhaustion':5},{}),
                'Стимулятор Котик':({'exhaustion':6},{}),
                'Научный стимулятор Волкодав':({'current':300},{'current':250}),
                'Стимулятор Полнокровка':({},{'blood':'normal'}),
                'Морфин':({'painLevel':5,'stress':3,'exhaustion':4},{'painLevel':2,'stress':1,'exhaustion':4}),
                'Миколий':({'painLevel':4,'intoxication':50},{}),
                'Стимулятор Полукровка':({'infection':5},{}),
                'Стимулятор Варвар':({'painLevel':2},{}),
                'Стимулятор Викинг':({'painLevel':0},{}),
                'Препарат 02':({'painLevel':2,'stress':1},{}),
                'Адреналин':({'current':250,'exhaustion':4},{}),
                'Эпинефрин':({'current':350,'exhaustion':3},{}),
                'Стимулятор Бык':({'painLevel':7,'exhaustion':7},{}),
                'Стимулятор Геракл':({'painLevel':9,'exhaustion':8},{}),
                'Стимулятор Грация':({'exhaustion':6},{}),
                'Стимулятор Б.О.Л.Т.':({'current':300,'exhaustion':5},{'current':250,'exhaustion':6}),
                'Стимулятор Болид':({'current':300,'exhaustion':5},{'current':200,'exhaustion':6}),
                'Стимулятор Орёл':({'exhaustion':6},{'exhaustion':6}),
                'Военный стимулятор ПЧеЛа':({'current':300,'exhaustion':6},{'current':200,'exhaustion':6}),
                'Стимулятор Мозгоправ':({'psyState':20,'exhaustion':6},{}),
                'Стимулятор Психолог-М':({'psyState':15},{}),
                'Стимулятор Покой':({'stress':0,'psyState':15},{}),
                'Стимулятор Гармония':({'stress':0,'psyState':5},{}),
                'Подавитель эмоций':({'psyState':0},{}),
                'Стимулятор Воля':({'exhaustion':5},{'exhaustion':6}),
                'Стимулятор Скала':({'current':300},{'current':200}),
                'Стимулятор Скала-Н':({'current':500,'max':900},{'current':200,'max':700}),
                'Жаропонижающее средство Мороз':({'temperature':38},{'temperature':34}),
                'Стимулятор Гора':({'painLevel':4,'current':300},{'painLevel':5,'current':250}),
                'Стимулятор Гора-Д':({'current':200},{}),
                'Питательный стимулятор':({'painLevel':4,'exhaustion':2},{'painLevel':7}),
                'Борщевик':({'current':300,'intoxication':15},{'current':550}),
                'Регенеративный стимулятор Альфа':({'exhaustion':6},{'current':700,'exhaustion':5}),
                'Регенеративный стимулятор Бета':({'exhaustion':6},{'current':700,'exhaustion':5}),
                'Регенеративный стимулятор Звезда':({'exhaustion':6},{'current':700,'exhaustion':6}),
                'Самопальный стимпак':({'current':300,'intoxication':25},{'current':500}),
                'Военный стимпак':({'current':300},{'current':700}),
                'Научный стимпак':({'radiation':45},{'current':700,'radiation':45}),
                'Антирад-А':({'radiation':50},{'radiation':40}),
                'Антирад-Б':({'radiation':50},{'radiation':30}),
                'Антирад-Г':({'radiation':50},{'radiation':20}),
                'Йод-плюс':({'radiation':50},{'radiation':25}),
            }.get(raw['name'].replace('"',''))
            row['numeric_rule_checks']=0
            plain_name=raw['name'].replace('"','')
            if expected:
                for snapshot,values in zip((row['after'],advanced),expected):
                    for field,value in values.items():
                        assert snapshot.get(field)==value,(raw['name'],field,snapshot.get(field),value)
                        row['numeric_rule_checks']+=1
            if direct.get('applications'):
                assert not any(effect.get('id')=='sweep-bleed' for effect in row['after']['effects']),raw['name']
                row['provoked_rule_checks']=['Остановлено подготовленное '+before['effects'][0]['type']]
            if direct.get('stop_all_bleeding'):
                assert not any(effect.get('type','').startswith(('bleeding_external_','bleeding_internal_')) for effect in row['after']['effects']),raw['name']
                assert any(effect.get('id')=='sweep-preserve-fracture' for effect in row['after']['effects']),raw['name']
                row['provoked_rule_checks']=['Остановлены внешнее и внутреннее кровотечения; перелом сохранён']
                from app.services.effects import apply_effect_to_health
                blocked=deepcopy(row['after']);new_bleed={'id':'new-provoked-bleed','type':'bleeding_external_severe','area':'head'}
                apply_effect_to_health(blocked,new_bleed)
                assert not any(effect.get('id')=='new-provoked-bleed' for effect in blocked['effects'])
                expired=deepcopy(advanced);apply_effect_to_health(expired,new_bleed)
                assert any(effect.get('id')=='new-provoked-bleed' for effect in expired['effects'])
                row['provoked_rule_checks'].append('Новая рана не кровоточит во время блока; после срока кровотечение снова возможно')
            if plain_name=='Научный стимулятор Волкодав':
                assert any(effect.get('type')=='sleep' for effect in advanced['effects'])
                row['provoked_rule_checks']=['После 15 ходов сняты 50 здоровья и активирован час сна']
            if response.get_json().get('medical_result'):
                row['medical_success']=response.get_json()['medical_result'].get('success')
        else:
            row['reason']=response.get_json().get('error')
        rows.append(row)
    (REPORT/'consumables.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    (REPORT/'consumables-summary.json').write_text(json.dumps({
        'total':len(rows),'success':sum(r['status']=='200' for r in rows),
        'other':[{'name':r['name'],'status':r['status'],'reason':r.get('reason')} for r in rows if r['status']!='200']},ensure_ascii=False,indent=2),encoding='utf-8')
    failures = [{k:r.get(k) for k in ('name','status','reason')} for r in rows
                if r['status'] not in {'200','MODULE','TOOL','DEFERRED'}]
    assert not failures, failures
    assert sum(r['status']=='200' for r in rows) == 117


def test_source_filters_install_and_really_exhaust_radiation_protection(app,client,create_user,auth_headers):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.equipment_module import module_action_details
    from app.services.combat import CombatService
    from app.services.gas_mask_filters import consume_equipped_filter_charges, filter_charges
    from test_equipment_modules import create_lobby,create_character
    equipment,catalog=source_catalog()
    user=create_user('catalog-filter-owner');lobby=create_lobby(client,user,auth_headers)
    mask_raw=next(t for t in equipment if t['category']=='gas_mask')
    mask=ItemTemplate(**mask_raw);db.session.add(mask);db.session.flush()
    records=[]
    for raw in catalog:
        if raw['category']!='gas_mask_module':continue
        template=ItemTemplate(**raw);db.session.add(template);db.session.flush()
        maximum=20 if raw['name']=='Фильтр для противогаза' else 10
        assert raw['attributes']['consumable']['direct']['filter_install_action_points']==8
        assert 'action_points_delta' not in raw['attributes']['consumable']['direct']
        item={'id':'source-filter-'+str(template.id),'templateId':template.id,'name':template.name,
            'category':template.category,'attributes':deepcopy(template.attributes),'quantity':1}
        mask_item={'templateId':mask.id,'category':'gas_mask','name':mask.name,'attributes':deepcopy(mask.attributes)}
        char=create_character(client,lobby,user,auth_headers,{'health':{'effects':[]},
            'inventory':{'pockets':[item],'backpack':[]},'equipment':{'gasMask':mask_item}})
        selection={'target':{'templateId':mask.id},'source':{'id':item['id']}}
        details=module_action_details(char['data'],'install',['equipment','gasMask'],'filter',
            item_path=['inventory','pockets',0],selection=selection,in_combat=True)
        assert details['action_points']==9 and details['base_action_points']==8
        installed=client.post(f"/lobbies/characters/{char['id']}/equipment-module",headers=auth_headers(user),json={
            'operation':'install','slot_type':'filter','target_path':['equipment','gasMask'],
            'item_path':['inventory','pockets',0],'module_selection':selection,'character_revision':char['data']['_revision']})
        assert installed.status_code==200,installed.json
        data=installed.json['data'];module=data['equipment']['gasMask']['installedModules'][0]
        assert filter_charges(module)==maximum and not data['inventory']['pockets']
        protection=CombatService._equipped_radiation_protection(data)
        assert protection>0
        for move in range(maximum):
            spent=consume_equipped_filter_charges(data)
            assert spent['consumed']==1
            assert filter_charges(module)==maximum-move-1
        assert consume_equipped_filter_charges(data)['consumed']==0
        assert CombatService._equipped_radiation_protection(data)==0
        records.append({'name':template.name,'charges':maximum,'combat_cost_from_pockets':details['action_points'],
            'actual_install_api':200,'moves_until_empty':maximum,'remaining':filter_charges(module),
            'radiation_protection_before':protection,'radiation_protection_after':0})
    (REPORT/'filters.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    assert len(records)==2
