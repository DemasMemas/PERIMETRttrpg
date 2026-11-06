"""Создание: строгий к100, ожидание игрового времени и сохранность компонентов."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState
from app.models.templates import ItemTemplate
from app.services.crafting_rules import resolve_creation
from app.services.general_consumable import _available_uses
from test_quick_skill_checks import checks


@pytest.fixture
def workshop(checks):
    data=deepcopy(checks['actor_character'].data)
    data['skills']['other']={'engineering':{'base':13,'bonus':0}}
    data['inventory']['pockets']=[{'name':name,'category':'crafting_material','quantity':quantity,'weight':.5}
        for name,quantity in [('Металлические детали',2),('Электронные материалы',2),('КПК',1),('Молоток',1)]]
    data['inventory']['backpack']=[]
    checks['actor_character'].data=data
    LocationCombatState.query.filter_by(location_id=checks['location'].id).one().status='inactive'
    output=ItemTemplate(name='Фонарик',category='misc',price=400,weight=.5,volume=1,attributes={'portable_light':'hand'})
    db.session.add(output);db.session.commit()
    return checks


def call(client,workshop,auth_headers,operation,**payload):
    character=workshop['actor_character']
    return client.post(f'/lobbies/characters/{character.id}/crafting',headers=auth_headers(workshop['actor_user']),
        json={'operation':operation,'operation_id':f'{operation}-once','character_revision':character.revision,**payload})


def units(data,name):return sum(_available_uses(i) for i in data['inventory']['pockets'] if i['name']==name)


def test_depleted_workbench_does_not_satisfy_recipe_requirement(client,workshop,auth_headers,monkeypatch):
    from app.services import crafting
    original=crafting.recipe_by_id
    def required_workbench(recipe_id):
        recipe=deepcopy(original(recipe_id))
        recipe['tools_text']='Станок Электрика'
        return recipe
    monkeypatch.setattr(crafting,'recipe_by_id',required_workbench)
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets'].append({'name':'Станок Электрика','category':'tool',
        'quantity':1,'maxUses':50,'uses':0})
    workshop['actor_character'].data=data;db.session.commit()
    depleted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-f4')
    assert depleted.status_code==200,depleted.json
    assert 'Нужны: Станок Электрика' in depleted.json['result']['missing']
    data['inventory']['pockets'][-1]['uses']=1
    workshop['actor_character'].data=data;db.session.commit()
    charged=call(client,workshop,auth_headers,'quote',recipe_id='recipe-f4')
    assert charged.status_code==200,charged.json
    assert 'Нужны: Станок Электрика' not in charged.json['result']['missing']


def test_lockpick_recipe_creates_two_catalog_items(client,workshop,auth_headers,monkeypatch):
    data=deepcopy(workshop['actor_character'].data)
    data['basic']={'background':{'name':'Полевой учёный'}}
    data['inventory']['pockets'].append({'name':'Печь','category':'tool','quantity':1,'uses':1,'maxUses':50})
    workshop['actor_character'].data=data
    output=ItemTemplate(name='Отмычка',category='misc',price=150,weight=.1,volume=.2)
    db.session.add(output);db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-m27')
    assert quoted.status_code==200,quoted.json
    assert quoted.json['result']['output_id']==output.id
    assert not quoted.json['result']['missing']
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-m27')
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=40;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['output_count']==2
    assert units(finished.json['data'],'Отмычка')==2


@pytest.mark.parametrize('recipe_id,catalog_name',[
    ('recipe-aa21','Анальгин (Таблетка)'),
    ('recipe-aa24','Пенициллин (Таблетка)'),
    ('recipe-aa35','Хирургический набор "Крест"'),
    ('recipe-aa28','Кустарный набор "Айболит"'),
    ('recipe-aa39','Ибупрофен (Таблетка)'),
    ('recipe-aa41','Жаропонижающее средство "Мороз"'),
])
def test_named_medicine_recipe_resolves_real_catalog_item(client,workshop,auth_headers,recipe_id,catalog_name):
    output=ItemTemplate(name=catalog_name,category='consumable',price=100)
    db.session.add(output);db.session.flush()
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['medicine']={'base':15}
    workshop['actor_character'].data=data;db.session.commit()
    response=call(client,workshop,auth_headers,'quote',recipe_id=recipe_id)
    assert response.status_code==200,response.json
    assert response.json['result']['output_id']==output.id


def test_analgin_recipe_finishes_with_thirty_five_catalog_tablets_once(client,workshop,auth_headers,monkeypatch):
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['medicine']={'base':20}
    data['inventory']['pockets'].extend({'name':name,'category':'crafting_material','quantity':1}
        for name in ('Серная кислота','Натрий','Сахар','Смесительный Станок'))
    workshop['actor_character'].data=data
    db.session.add(ItemTemplate(name='Анальгин (Таблетка)',category='consumable',price=100))
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa21')
    assert started.status_code==200,started.json
    workshop['lobby'].game_day+=1;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    completed=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'])
    assert completed.status_code==200,completed.json
    assert units(completed.json['data'],'Анальгин (Таблетка)')==35
    repeated=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'])
    assert repeated.status_code==200 and repeated.json['result']['replayed']
    assert units(repeated.json['data'],'Анальгин (Таблетка)')==35


def test_work_gloves_lower_creation_difficulty_only_while_worn(client,workshop,auth_headers):
    baseline=call(client,workshop,auth_headers,'quote',recipe_id='recipe-f4')
    assert baseline.status_code==200,baseline.json
    gloves=ItemTemplate(name='Рабочие',category='gloves',price=100,
        attributes={'craft_difficulty_modifier':-5})
    db.session.add(gloves);db.session.flush()
    data=deepcopy(workshop['actor_character'].data)
    data['equipment']['gloves']={'name':'Рабочие','category':'gloves','templateId':gloves.id}
    workshop['actor_character'].data=data;db.session.commit()
    equipped=call(client,workshop,auth_headers,'quote',recipe_id='recipe-f4')
    assert equipped.status_code==200,equipped.json
    assert equipped.json['result']['difficulty']==baseline.json['result']['difficulty']-5
    data['equipment']['gloves']['protectionDisabled']=True
    workshop['actor_character'].data=data;db.session.commit()
    disabled=call(client,workshop,auth_headers,'quote',recipe_id='recipe-f4')
    assert disabled.json['result']['difficulty']==baseline.json['result']['difficulty']
    data['equipment']['gloves']={'name':'Поддельные','category':'gloves','templateId':gloves.id,
        'attributes':{'craft_difficulty_modifier':-100}}
    workshop['actor_character'].data=data;db.session.commit()
    forged=call(client,workshop,auth_headers,'quote',recipe_id='recipe-f4')
    assert forged.json['result']['difficulty']==baseline.json['result']['difficulty']


@pytest.mark.parametrize('recipe_id,catalog_name,uses,maximum',[
    ('recipe-m31','Шлифовальные инструменты 50/50',20,50),
    ('recipe-m32','Паяльные инструменты 50/50',10,50),
    ('recipe-m33','Смесительные инструменты 50/50',30,50),
    ('recipe-m35','Набор инструментов Оружейника (Упрощёные) 15/15',5,15),
    ('recipe-m36','Набор инструментов Оружейника (Стандартные) 25/25',10,25),
    ('recipe-m37','Набор инструментов Бронника (Упрощёные) 10/10',5,10),
    ('recipe-m38','Набор инструментов Бронника (Стандартные) 20/20',10,20),
])
def test_crafted_tool_quote_keeps_catalog_maximum(client,workshop,auth_headers,recipe_id,catalog_name,uses,maximum):
    data=deepcopy(workshop['actor_character'].data)
    data['basic']={'background':{'name':'Полевой учёный'}}
    workshop['actor_character'].data=data
    template=ItemTemplate(name=catalog_name,category='tool',price=3000,attributes={'uses':maximum})
    db.session.add(template);db.session.commit()
    response=call(client,workshop,auth_headers,'quote',recipe_id=recipe_id)
    assert response.status_code==200,response.json
    assert response.json['result']['output_id']==template.id
    assert response.json['result']['output_uses']==uses


def test_crafted_tool_has_recipe_charges_and_catalog_maximum(client,workshop,auth_headers,monkeypatch):
    data=deepcopy(workshop['actor_character'].data)
    data['basic']={'background':{'name':'Полевой учёный'}}
    data['inventory']['pockets'][0]['quantity']=5
    data['inventory']['pockets'].extend([
        {'name':'Тканные материалы','category':'crafting_material','quantity':1},
        {'name':'Печь','category':'tool','quantity':1},
    ])
    workshop['actor_character'].data=data
    template=ItemTemplate(name='Шлифовальные инструменты 50/50',category='tool',price=3000,
        weight=2,volume=3,attributes={'uses':50})
    db.session.add(template);db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-m31')
    assert started.status_code==200,started.json
    workshop['lobby'].game_day+=1;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    completed=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0])
    assert completed.status_code==200,completed.json
    tool=next(i for i in completed.json['data']['inventory']['pockets'] if i.get('templateId')==template.id)
    assert (tool['uses'],tool['maxUses'],tool['price'],tool['weight'],tool['volume'])==(20,50,3000,2,3)
    repeated=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'])
    assert repeated.status_code==200 and repeated.json['result']['replayed']
    assert sum(i.get('templateId')==template.id for i in repeated.json['data']['inventory']['pockets'])==1


def test_cooking_mutant_meat_keeps_source_radiation_and_reboils_without_duplication(
        client, workshop, auth_headers, monkeypatch):
    from app.services.cooked_meat import templates as meat_templates

    db.session.add_all(ItemTemplate(**entry) for entry in meat_templates())
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':20}
    data['inventory']['pockets'].extend([
        {'id':'raw-meat','name':'Мясо: Слепыш','category':'mutant_part','quantity':1,'price':25,
         'attributes':{'mutant_part':'meat','source_mutant':'Слепыш','radiation':25}},
        {'name':'Вода','category':'consumable','quantity':4},
        {'name':'Костер','category':'tool','quantity':1},
        {'name':'котелок','category':'tool','quantity':1},
    ])
    workshop['actor_character'].data=data
    db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    for sequence,expected in [(1,18.75),(2,12.5)]:
        started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa16',
                     operation_id=f'start-meat-{sequence}')
        assert started.status_code==200,started.json
        workshop['lobby'].game_time_minutes+=60
        db.session.commit()
        completed=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],
                       operation_id=f'finish-meat-{sequence}')
        assert completed.status_code==200,completed.json
        cooked=[item for item in completed.json['data']['inventory']['pockets']
                if item.get('attributes',{}).get('cooked_meat')=='boiled']
        assert len(cooked)==1
        assert cooked[0]['attributes']['meat_remaining_radiation']==expected
        assert (cooked[0]['uses'],cooked[0]['price'],cooked[0]['weight'],cooked[0]['volume'])==(3,25,1,1)
    assert not any(item.get('id')=='raw-meat' for item in completed.json['data']['inventory']['pockets'])


def test_meat_from_tusha_cannot_be_fried_but_loses_half_radiation_when_boiled():
    from app.services.cooked_meat import ingredient_matches, cooked_output, templates
    raw={'name':'Мясо: Туша','category':'mutant_part','price':300,
         'attributes':{'mutant_part':'meat','source_mutant':'Туша','radiation':25}}
    assert not ingredient_matches('recipe-aa15',raw)
    assert ingredient_matches('recipe-aa16',raw)
    output={**templates()[1], 'attributes':deepcopy(templates()[1]['attributes']), 'uses':3}
    cooked_output(raw,'recipe-aa16',output)
    assert output['attributes']['meat_remaining_radiation']==12.5
    assert output['price']==300


def test_frying_meat_uses_real_raw_part_and_fat(client, workshop, auth_headers, monkeypatch):
    from app.services.cooked_meat import templates as meat_templates

    db.session.add_all(ItemTemplate(**entry) for entry in meat_templates())
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':20}
    data['inventory']['pockets'].extend([
        {'id':'raw-frying','name':'Мясо: Слепыш','category':'mutant_part','quantity':1,'price':25,
         'attributes':{'mutant_part':'meat','source_mutant':'Слепыш','radiation':25}},
        {'name':'Жир','category':'crafting_material','quantity':1},
        {'name':'Костер','category':'tool','quantity':1},
        {'name':'котелок','category':'tool','quantity':1},
    ])
    workshop['actor_character'].data=data
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa15')
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=30
    db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    completed=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'])
    assert completed.status_code==200,completed.json
    cooked=[item for item in completed.json['data']['inventory']['pockets']
            if item.get('attributes',{}).get('cooked_meat')=='fried']
    assert len(cooked)==1
    assert cooked[0]['attributes']['meat_remaining_radiation']==6.25
    assert cooked[0]['attributes']['cooking_count']==1
    assert cooked[0]['price']==25


def test_cooked_meat_migration_adds_only_missing_templates(app, monkeypatch):
    import importlib.util
    from pathlib import Path

    path=Path(__file__).resolve().parents[1]/'migrations/versions/d39b6c1f2a82_add_cooked_meat_templates.py'
    spec=importlib.util.spec_from_file_location('cooked_meat_migration',path)
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration.op,'get_bind',lambda:db.session.connection())
    migration.upgrade();migration.upgrade()
    from app.services.cooked_meat import NAMES
    assert ItemTemplate.query.filter(ItemTemplate.name.in_(NAMES.values())).count()==2


def test_approved_salvage_materials_and_craft_only_flamethrowers():
    from app.services.equipment_catalog import load_catalog
    from app.services.crafting_rules import catalog
    items={item['name']:item for item in load_catalog()}
    for name,(price,volume,weight) in {
        'Плата':(1000,1,.5),'Запал':(350,.25,.1),
        'Нитроглицерин':(1350,.5,.5),'Взрыватель':(1000,.25,.1),
    }.items():
        item=items[name]
        assert (item['price'],item['volume'],item['weight'])==(price,volume,weight)
        assert not item['attributes'].get('unspecified_fields')
        assert next(m for m in catalog()['salvage_materials'] if m['name']==name)['price']==price
    flame=[item for item in items.values() if item['attributes'].get('flamethrower') or
           item['attributes'].get('flamethrower_tank') or item['attributes'].get('flamethrower_air')]
    assert len(flame)==11
    assert all(item['item_class'] is None and item['attributes'].get('craft_only') and
               'price' in item['attributes']['unspecified_fields'] for item in flame)


@pytest.mark.parametrize('roll,expected',[(1,False),(40,False),(41,True),(100,True)])
def test_creation_uses_strict_threshold_without_critical_override(roll,expected):
    assert resolve_creation({'difficulty':40,'automatic_success':False},roll=roll)['success']==expected


def test_start_wait_finish_replay_and_reserved_weight(client,workshop,auth_headers,monkeypatch):
    from app.services.combat import CombatService
    before_weight=CombatService._inventory_weight_details(workshop['actor_character'].data)['total_weight']
    quote=call(client,workshop,auth_headers,'quote',recipe_id='recipe-f4')
    assert quote.status_code==200,quote.json
    assert quote.json['result']['difficulty']==15 and not quote.json['result']['missing']
    response=call(client,workshop,auth_headers,'start',recipe_id='recipe-f4')
    assert response.status_code==200,response.json
    job=response.json['result'];data=workshop['actor_character'].data
    assert units(data,'КПК')==0 and len(job['escrow'])==3
    assert CombatService._inventory_weight_details(data)['total_weight']==before_weight
    assert call(client,workshop,auth_headers,'finish',job_id=job['id'],chosen_loss=[0]).status_code==400
    workshop['lobby'].game_time_minutes+=20;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':16,'automatic':False})
    result=call(client,workshop,auth_headers,'finish',job_id=job['id'],chosen_loss=[0])
    assert result.status_code==200,result.json
    assert result.json['result']['success'] and units(result.json['data'],'Фонарик')==1
    repeat=call(client,workshop,auth_headers,'finish',job_id=job['id'],chosen_loss=[0],character_revision=0)
    assert repeat.status_code==200 and repeat.json['result']['replayed']
    assert units(repeat.json['data'],'Фонарик')==1
    entries=repeat.json['data']['health']['combatMeta']['actionJournal']
    works=[entry for entry in entries if entry['id']=='crafting-job:'+job['id']]
    assert len(works)==1 and 'к100 16, СЛ 15' in works[0]['outcome']


def test_reservation_of_legacy_single_items_does_not_confuse_shifted_inventory_paths(client,workshop,auth_headers):
    data=deepcopy(workshop['actor_character'].data)
    for item in data['inventory']['pockets']:item['quantity']=1;item.pop('id',None)
    workshop['actor_character'].data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-f4')
    assert started.status_code==200,started.json
    assert [item['name'] for item in started.json['data']['inventory']['pockets']]==['Молоток']
    assert len({entry['item']['id'] for entry in started.json['result']['escrow']})==3


def test_failure_loses_chosen_and_random_units_once(client,workshop,auth_headers,monkeypatch):
    start=call(client,workshop,auth_headers,'start',recipe_id='recipe-f4')
    assert start.status_code==200,start.json
    workshop['lobby'].game_time_minutes+=20;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':False,'roll':15,'automatic':False})
    response=call(client,workshop,auth_headers,'finish',job_id=start.json['result']['id'],chosen_loss=[0])
    assert response.status_code==200,response.json
    assert sum(response.json['result']['lost'].values())==2
    assert response.json['result']['lost']['0']==1
    assert not units(response.json['data'],'Фонарик')


def test_cancel_restores_all_units_and_stale_save_cannot_erase_job(client,workshop,auth_headers):
    before=deepcopy(workshop['actor_character'].data)
    response=call(client,workshop,auth_headers,'start',recipe_id='recipe-f4')
    assert response.status_code==200,response.json
    character=workshop['actor_character'];stale=deepcopy(character.data);stale.pop('crafting');stale['_revision']=character.revision
    saved=client.put(f'/lobbies/characters/{character.id}',headers=auth_headers(workshop['actor_user']),json={'data':stale})
    assert saved.status_code==200,saved.json
    assert character.data['crafting']['current']['id']==response.json['result']['id']
    result=call(client,workshop,auth_headers,'cancel',job_id=response.json['result']['id'])
    assert result.status_code==200,result.json
    for name in ['Металлические детали','Электронные материалы','КПК']:assert units(result.json['data'],name)==units(before,name)


def test_player_cannot_approve_tool_but_gm_approval_changes_quote(client,workshop,auth_headers):
    # Создатель здесь ГМ; второй пользователь не должен одобрять его заявку.
    request=call(client,workshop,auth_headers,'request_tool',recipe_id='recipe-f4',tool_path=['inventory','pockets',3])
    assert request.status_code==200,request.json
    character=workshop['actor_character']
    response=client.post(f'/lobbies/characters/{character.id}/crafting',headers=auth_headers(workshop['target_user']),json={
        'operation':'approve_tool','operation_id':'forged','character_revision':character.revision,'request_id':request.json['result']['id'],'modifier':-15,'approved':True})
    assert response.status_code==403,response.json
    approved=call(client,workshop,auth_headers,'approve_tool',request_id=request.json['result']['id'],modifier=-15,approved=True)
    assert approved.status_code==200,approved.json
    quote=call(client,workshop,auth_headers,'quote',recipe_id='recipe-f4',approval_id=request.json['result']['id'])
    assert quote.json['result']['difficulty']==0
    assert not quote.json['result']['automatic_success']


def test_multi_use_component_spends_uses_instead_of_bottles(client,workshop,auth_headers,monkeypatch):
    from app.services.crafting import recipe_by_id
    recipe=recipe_by_id('recipe-f4');recipe.update(id='test-water',components=[{'name':'Вода','quantity':2}],tools_text='Не нужен')
    monkeypatch.setattr('app.services.crafting.recipe_by_id',lambda _:recipe)
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets']=[{'name':'Вода','category':'consumable','quantity':1,'uses':3,'maxUses':3}]
    workshop['actor_character'].data=data;db.session.commit()
    response=call(client,workshop,auth_headers,'start',recipe_id='test-water')
    assert response.status_code==200,response.json
    assert response.json['data']['inventory']['pockets'][0]['uses']==1
    cancelled=call(client,workshop,auth_headers,'cancel',job_id=response.json['result']['id'])
    assert sum(_available_uses(i) for i in cancelled.json['data']['inventory']['pockets'])==3


@pytest.mark.parametrize('kind,expected',[('skill',2),('equipment',1)])
def test_reading_takes_four_hours_and_only_skill_books_give_training(client,workshop,auth_headers,kind,expected):
    from app.services.character_starting_purchase import item_from_template
    from app.services.character_development import award_session
    book=ItemTemplate(name='Книга для проверки',category='misc',price=100,weight=0,volume=1,
        attributes={'book_kind':kind,'book_subject':'Физические' if kind=='skill' else 'Пистолеты','reading_seconds':14400})
    db.session.add(book);db.session.flush()
    data=deepcopy(workshop['actor_character'].data);data['inventory']['pockets'].append(item_from_template(book))
    workshop['actor_character'].data=data;db.session.commit()
    result=call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',4],skill_path='physical.strength' if kind=='skill' else None)
    assert result.status_code==200,result.json
    job=result.json['result']
    workshop['lobby'].game_time_minutes+=239;db.session.commit()
    assert call(client,workshop,auth_headers,'finish',job_id=job['id']).status_code==400
    workshop['lobby'].game_time_minutes+=1;db.session.commit()
    finished=call(client,workshop,auth_headers,'finish',job_id=job['id'])
    assert finished.status_code==200,finished.json
    assert len(finished.json['data']['crafting']['library'])==1
    data=deepcopy(workshop['actor_character'].data)
    reward=award_session(data,{'operationId':'book-xp','trainingSkill':'physical.strength'})
    assert reward['trainingPoints']==expected
    data['skills']['physical']['strength'].update(base=16,xp=0)
    reward=award_session(data,{'operationId':'book-xp-cap','trainingSkill':'physical.strength'})
    assert reward['trainingPoints']==1


def test_electronic_book_requires_powered_pda_at_start_and_finish(client,workshop,auth_headers):
    from app.services.character_starting_purchase import item_from_template
    book=ItemTemplate(name='Проверочная электронная книга',category='misc',price=100,weight=0,volume=1,
        attributes={'book_kind':'equipment','book_subject':'Пистолеты','reading_seconds':60})
    db.session.add(book);db.session.flush()
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets'].append(item_from_template(book))
    data['inventory']['pockets'][2]['pdaState']={'on':False,'charge_percent':100}
    workshop['actor_character'].data=data;db.session.commit()
    assert call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',4]).status_code==400
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets'][2]['pdaState']={'on':True,'charge_percent':0}
    workshop['actor_character'].data=data;db.session.commit()
    assert call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',4]).status_code==400
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets'][2]['pdaState']={'on':True,'charge_percent':100}
    workshop['actor_character'].data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',4])
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=1
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets'][2]['pdaState']['on']=False
    workshop['actor_character'].data=data;db.session.commit()
    assert call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id']).status_code==409
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets'][2]['pdaState']['on']=True
    workshop['actor_character'].data=data;db.session.commit()
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'])
    assert finished.status_code==200,finished.json
