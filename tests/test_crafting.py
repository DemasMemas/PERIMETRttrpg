"""Создание: строгий к100, ожидание игрового времени и сохранность компонентов."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState,LocationObject,LocationCharacter
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


def catalog_template(name):
    from app.services.equipment_catalog import load_catalog

    template = ItemTemplate(**next(row for row in load_catalog() if row['name'] == name))
    db.session.add(template)
    db.session.flush()
    return template


def assert_recipe_wait(client, workshop, auth_headers, job, minutes, **finish_payload):
    from app.services.crafting import now
    assert job['ready_at'] - now(workshop['actor_character']) == minutes * 60
    lobby = workshop['lobby']

    def advance(elapsed):
        days, minute = divmod(lobby.game_time_minutes + elapsed, 1440)
        lobby.game_day += days
        lobby.game_time_minutes = minute
        db.session.commit()

    advance(minutes - 1)
    character = workshop['actor_character']
    before = deepcopy(character.data)
    revision = character.revision
    early = call(client, workshop, auth_headers, 'finish', job_id=job['id'], **finish_payload)
    assert early.status_code == 400, early.json
    assert character.data == before
    assert character.revision == revision
    advance(1)


@pytest.mark.parametrize('recipe_id,name,materials',[('recipe-m29','Отвертка',[('Металлические детали',3),('Веревка 10м',1)]),
    ('recipe-m30','Кусачки',[('Металлические детали',4),('Резина',1)])])
def test_handmade_recipe_creates_the_ordinary_catalog_tool(client,workshop,auth_headers,monkeypatch,recipe_id,name,materials):
    from app.services.equipment_catalog import load_catalog
    from app.services.crafting import normalized,quote
    row=next(r for r in load_catalog() if normalized(r['name'])==normalized(name))
    template=ItemTemplate(**row);db.session.add(template)
    character=workshop['actor_character'];actor=workshop['actor_location'];data=deepcopy(character.data)
    data['features']={'specialTraits':[{'name':'Мастер на все руки'}]}
    data['inventory']['pockets']=[{'name':label,'category':'crafting_material','quantity':count} for label,count in materials]
    character.data=data
    db.session.add(LocationObject(location_id=workshop['location'].id,type='household_workbench',name='Печь',
        tile_x=actor.pos_x,tile_y=actor.pos_y,properties={'craft_workbench':'Печь'}))
    db.session.commit()
    plan=quote(character,character.data,{'recipe_id':recipe_id})
    assert not plan['missing'],plan['missing'];assert plan['output_id']==template.id
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    started=call(client,workshop,auth_headers,'start',recipe_id=recipe_id)
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=20;db.session.commit()
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0]*started.json['result']['quote']['failure_loss']['chosen'])
    assert finished.status_code==200 and finished.json['result']['success'],finished.json
    items=[item for item in finished.json['data']['inventory']['pockets'] if item.get('templateId')==template.id]
    assert len(items)==1 and items[0]['name']==template.name
    assert (items[0]['weight'],items[0]['volume'])==(template.weight,template.volume)


def test_pda_modification_is_installed_on_selected_device(client,workshop,auth_headers,monkeypatch):
    character=workshop['actor_character'];data=deepcopy(character.data)
    data['features']={'specialTraits':[{'name':'Программист'}]}
    data['inventory']['pockets']=[
        {'id':'pda-first','name':'КПК','category':'misc','quantity':1},
        {'id':'pda-second','name':'КПК','category':'misc','quantity':1},
        {'name':'Электронные материалы','category':'crafting_material','quantity':4},
        {'name':'Паяльные инструменты 50/50','category':'tool','quantity':1,'uses':3,'maxUses':50},
    ]
    character.data=data;db.session.commit()
    target_path=['inventory','pockets',0]
    tool_path=['inventory','pockets',3]
    args={'target_path':target_path,'item_id':'pda-first','rule_id':'pda-b2','tool_path':tool_path}
    quote=call(client,workshop,auth_headers,'quote_pda_modification',**args)
    assert quote.status_code==200,quote.json
    assert quote.json['result']['missing']==[]
    assert quote.json['result']['difficulty']==30
    started=call(client,workshop,auth_headers,'install_pda_modification',**args)
    assert started.status_code==200,started.json
    pockets=started.json['data']['inventory']['pockets']
    assert next(item for item in pockets if item.get('name','').startswith('Паяльные'))['uses']==2
    assert units(started.json['data'],'Электронные материалы')==3
    workshop['lobby'].game_time_minutes+=60;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0])
    assert finished.status_code==200,finished.json
    devices=[item for item in finished.json['data']['inventory']['pockets'] if item.get('name')=='КПК']
    assert [m['ruleId'] for m in devices[0]['modifications']]==['pda-b2']
    assert not devices[1].get('modifications')
    duplicate=call(client,workshop,auth_headers,'quote_pda_modification',**args)
    assert duplicate.status_code==400


@pytest.mark.parametrize(('rule_id','price'), [('pda-b2',700),('pda-b15',2550)])
def test_electrician_installs_pda_modification_for_catalog_formula(workshop,rule_id,price):
    from app.services.professional_modification import quote,perform
    pair=workshop;actor=pair['actor_character'];specialist=pair['target_character']
    owned=deepcopy(actor.data);owned['inventory']['money']=3000
    owned['inventory']['pockets']=[{'id':'paid-pda','name':'КПК','category':'misc','quantity':1}]
    actor.data=owned
    specialist.data={**deepcopy(specialist.data),'is_npc':True,
        'merchant':{'modification_enabled':True,'workbenches':['Станок Электрика']},
        'inventory':{'money':0,'pockets':[],'backpack':[]}}
    db.session.commit()
    payload={'actor_location_character_id':pair['actor_location'].id,
        'specialist_character_id':specialist.id,'target_path':['inventory','pockets',0],
        'item_id':'paid-pda','rule_id':rule_id,'action':'install_pda'}
    planned=quote(pair['location'].id,pair['actor_user']['id'],payload)
    assert planned['price']==price
    outcome=perform(pair['location'].id,pair['actor_user']['id'],{**payload,'operation_id':'paid-pda-once',
        'actor_revision':actor.revision,'specialist_revision':specialist.revision})
    assert outcome['price']==price
    assert actor.data['inventory']['money']==3000-price
    assert specialist.data['inventory']['money']==price
    modification=actor.data['inventory']['pockets'][0]['modifications'][0]
    assert modification['ruleId']==rule_id
    assert modification['installerId']==specialist.id
    assert modification.get('profileOwnerId')==(actor.id if rule_id=='pda-b15' else None)


def test_high_pda_modification_needs_nearby_electrician_station(workshop):
    from app.services.pda_modifications import plan
    character=workshop['actor_character'];data=deepcopy(character.data)
    data['features']={'specialTraits':[{'name':'Программист'}]}
    data['inventory']['pockets']=[{'id':'complex-pda','name':'КПК','category':'misc','quantity':1},
        {'name':'Плата','category':'crafting_material','quantity':1},
        {'name':'Электронные материалы','category':'crafting_material','quantity':1},
        {'name':'Паяльные инструменты 50/50','category':'tool','quantity':1,'uses':2,'maxUses':50}]
    payload={'target_path':['inventory','pockets',0],'item_id':'complex-pda',
        'rule_id':'pda-b15','tool_path':['inventory','pockets',3]}
    without=plan(character,data,payload)
    assert any('Станок Электрика' in reason for reason in without['missing'])
    placement=LocationCharacter.query.filter_by(character_id=character.id).first()
    db.session.add(LocationObject(location_id=placement.location_id,name='Станок Электрика',type='workbench',
        tile_x=round(placement.pos_x),tile_y=round(placement.pos_y),properties={'level':placement.floor_level or 0}))
    db.session.flush()
    assert plan(character,data,payload)['missing']==[]


def test_pda_rule_record_survives_sheet_save_and_cannot_be_forged(client,workshop,auth_headers):
    from app.services.pda_modifications import record,rule_by_id
    character=workshop['actor_character'];data=deepcopy(character.data)
    data['inventory']['pockets']=[{'id':'saved-pda','name':'КПК','category':'misc','quantity':1,
        'modifications':[record(rule_by_id('pda-b2'),character.id)]}]
    character.data=data;db.session.commit()
    incoming=character.data_snapshot()
    incoming['inventory']['pockets'][0]['modifications']=[record(rule_by_id('pda-b3'),character.id)]
    saved=client.put(f'/lobbies/characters/{character.id}',headers=auth_headers(workshop['actor_user']),
        json={'data':incoming})
    assert saved.status_code==200,saved.json
    assert [mod['ruleId'] for mod in character.data['inventory']['pockets'][0]['modifications']]==['pda-b2']


def test_high_voltage_pda_battery_doubles_runtime(app):
    from app.services.portable_lights import advance_pda
    from app.services.pda_modifications import record,rule_by_id
    normal={'inventory':{'pockets':[{'name':'КПК','pdaState':{'on':True,'charge_percent':100}}]}}
    upgraded=deepcopy(normal)
    upgraded['inventory']['pockets'][0]['modifications']=[record(rule_by_id('pda-b4'),1)]
    advance_pda(normal,7200)
    advance_pda(upgraded,7200)
    assert normal['inventory']['pockets'][0]['pdaState']['charge_percent']==99
    assert upgraded['inventory']['pockets'][0]['pdaState']['charge_percent']==99.5


def test_sausage_uses_two_boiled_mutant_meat_portions_and_removes_radiation(
        client,workshop,auth_headers,monkeypatch):
    from app.services.cooked_meat import ingredient_matches
    from app.services.equipment_catalog import load_catalog
    assert not ingredient_matches('recipe-aa8',{'name':'Мясо(вареное)','quantity':2})
    source=next(item for item in load_catalog() if item['name']=='Колбаса')
    db.session.add(ItemTemplate(**source))
    character=workshop['actor_character'];data=deepcopy(character.data)
    data['skills']['other']['survival']={'base':20,'bonus':0}
    data['inventory']['pockets']=[
        {'id':'boiled-mutant','name':'Мясо(вареное) (3 использования)','category':'consumable',
         'quantity':1,'uses':3,'maxUses':3,'attributes':{'cooked_meat':'boiled',
             'source_mutant':'Кабан','meat_original_radiation':30,'meat_remaining_radiation':22.5}},
        {'name':'Хлеб','category':'consumable','quantity':1},
        {'name':'Бумага','category':'crafting_material','quantity':1},
    ]
    character.data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa8')
    assert started.status_code==200,started.json
    assert next(item for item in started.json['data']['inventory']['pockets'] if item['id']=='boiled-mutant')['uses']==1
    workshop['lobby'].game_time_minutes+=60;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    sausage=next(item for item in finished.json['data']['inventory']['pockets'] if item['name']=='Колбаса')
    assert sausage['quantity']==5
    assert 'radiation' not in sausage['attributes']


def test_scientist_mechanic_recharges_reanimation_for_five_percent(workshop):
    from app.services.equipment_modifications import make_record
    from app.services.professional_repair import quote,perform
    pair=workshop;actor=pair['actor_character'];specialist=pair['target_character']
    template=ItemTemplate(name='Проверочный экзоскелет',category='armor',price=10001,
        weight=20,volume=10,attributes={'import_source':'equipment_workbook'})
    db.session.add(template);db.session.flush()
    owned=deepcopy(actor.data);owned['inventory']['money']=1000
    owned['inventory']['pockets']=[{'id':'protocol-armor','templateId':template.id,
        'name':template.name,'category':'armor','quantity':1,'reanimationCharge':False,
        'modifications':[make_record('armor-d31')]}]
    actor.data=owned
    specialist.data={**deepcopy(specialist.data),'is_npc':True,
        'merchant':{'repair_enabled':True,'scientist_enabled':True,
            'workbenches':['Станок Электрика']},
        'inventory':{'money':0,'pockets':[],'backpack':[]}}
    db.session.commit()
    payload={'actor_location_character_id':pair['actor_location'].id,
        'mechanic_character_id':specialist.id,'item_path':['inventory','pockets',0],
        'item_id':'protocol-armor','mode':'protocol_recharge'}
    assert quote(pair['location'].id,pair['actor_user']['id'],payload)['price']==500
    outcome=perform(pair['location'].id,pair['actor_user']['id'],{**payload,'operation_id':'protocol-once',
        'actor_revision':actor.revision,'mechanic_revision':specialist.revision})
    assert outcome['price']==500
    assert actor.data['inventory']['money']==500
    assert actor.data['inventory']['pockets'][0]['reanimationCharge'] is True


def ammo_workshop(workshop,name='5.45x39'):
    from app.services.equipment_catalog import load_catalog
    from app.services.character_starting_purchase import item_from_template
    template_data=next(item for item in load_catalog() if item['name']==name and item['category']=='ammo')
    template=ItemTemplate(**template_data);db.session.add(template);db.session.flush()
    character=workshop['actor_character'];data=deepcopy(character.data)
    data['skills']['other']['engineering']={'base':20,'bonus':0}
    data['inventory']['pockets']=[item_from_template(template,35),
        {'name':'Форма для патронов','category':'crafting_material','quantity':1},
        {'name':'Металлические детали','category':'crafting_material','quantity':5},
        {'name':'Порох','category':'crafting_material','quantity':1}]
    character.data=data
    placement=LocationCharacter.query.filter_by(character_id=character.id).first()
    db.session.add(LocationObject(location_id=placement.location_id,name='Смесительный станок',type='workbench',
        tile_x=round(placement.pos_x),tile_y=round(placement.pos_y),properties={}))
    db.session.commit()
    return template


def test_placed_station_is_available_only_nearby_on_same_floor(workshop):
    from app.services.crafting import _tools, recipe_by_id
    from app.services.equipment_catalog import load_catalog

    character=workshop['actor_character']
    placement=LocationCharacter.query.filter_by(character_id=character.id).first()
    station=LocationObject(location_id=placement.location_id,name='Верстак',type='household_workbench',
        tile_x=round(placement.pos_x),tile_y=round(placement.pos_y),
        properties={'craft_workbench':'Смесительный Станок','level':placement.floor_level or 0,
                    'visual_asset':'station_mixer'})
    db.session.add(station);db.session.commit()
    recipe=recipe_by_id('recipe-v26')
    assert _tools(character,character.data,recipe,0)==[]
    station.type='chair';station.name='Смесительный станок';station.properties={};db.session.commit()
    assert any('смеситель' in name.casefold() for name in _tools(character,character.data,recipe,0))
    station.type='household_workbench';station.name='Верстак'
    station.properties={'craft_workbench':'Смесительный Станок','level':placement.floor_level or 0,
                        'visual_asset':'station_mixer'}
    station.tile_x+=3;db.session.commit()
    assert any('смеситель' in name.casefold() for name in _tools(character,character.data,recipe,0))
    station.tile_x-=3;station.properties={**station.properties,'level':(placement.floor_level or 0)+1};db.session.commit()
    assert any('смеситель' in name.casefold() for name in _tools(character,character.data,recipe,0))
    assert not any(entry['attributes'].get('craft_workbench') for entry in load_catalog())


def test_ammo_recipe_requires_catalog_caliber_and_author_category_two_numbers(workshop):
    from app.services.crafting import quote
    from app.services.exceptions import ValidationError
    template=ammo_workshop(workshop)
    character=workshop['actor_character']
    plan=quote(character,character.data,{'recipe_id':'recipe-v26','ammo_template_id':template.id})
    assert plan['recipe']['output_name']=='5.45x39'
    assert plan['recipe']['output_count']==20
    assert plan['recipe']['components'][2]['quantity']==1
    assert plan['recipe']['difficulty_options']==[65]
    assert plan['output_id']==template.id
    with pytest.raises(ValidationError,match='категории'):
        quote(character,character.data,{'recipe_id':'recipe-v25','ammo_template_id':template.id})
    with pytest.raises(ValidationError,match='Выберите конкретный калибр'):
        quote(character,character.data,{'recipe_id':'recipe-v26'})
    with pytest.raises(ValidationError,match='Неизвестный вид'):
        quote(character,character.data,{'recipe_id':'recipe-v26','ammo_template_id':template.id,'ammo_variant':['bp']})


def test_ammo_base_recipe_creates_catalog_rounds(client,workshop,auth_headers,monkeypatch):
    template=ammo_workshop(workshop)
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-v26',ammo_template_id=template.id)
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    produced=[item for item in finished.json['data']['inventory']['pockets'] if item['name']=='5.45x39']
    assert len(produced)==2
    assert sorted(item['quantity'] for item in produced)==[20,35]
    assert all(item['price']==template.price and item['attributes']['ammo_variant'] is None for item in produced)


@pytest.mark.parametrize('variant,rounds,metal,difficulty', [('bp',30,3,80),('ep',25,2,75)])
def test_ammo_bp_ep_uses_plain_same_caliber_and_catalog_price(client,workshop,auth_headers,monkeypatch,variant,rounds,metal,difficulty):
    from app.services.crafting import quote
    template=ammo_workshop(workshop)
    character=workshop['actor_character'];payload={'recipe_id':'recipe-v26','ammo_template_id':template.id,'ammo_variant':variant}
    plan=quote(character,character.data,payload)
    assert plan['recipe']['output_count']==20
    assert [part['quantity'] for part in plan['recipe']['components']]==[rounds,metal]
    assert plan['recipe']['difficulty_options']==[difficulty]
    assert not plan['missing'],plan['missing']
    started=call(client,workshop,auth_headers,'start',**payload)
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    produced=next(item for item in finished.json['data']['inventory']['pockets'] if item['name']==f"5.45x39 {'БП' if variant=='bp' else 'ЭП'}")
    assert produced['quantity']==20
    assert produced['price']==template.price
    assert produced['templateId']==template.id
    assert produced['attributes']['ammo_variant']==variant
    assert units(finished.json['data'],'5.45x39')==35-rounds


def test_ammo_bp_ep_rejects_special_or_other_caliber_as_source(workshop):
    from app.services.crafting import quote
    template=ammo_workshop(workshop);character=workshop['actor_character']
    data=deepcopy(character.data)
    ammo=data['inventory']['pockets'][0]
    ammo['name']='5.45x39 +';ammo['attributes']['ammo_variant']='improved'
    data['inventory']['pockets'].append({**deepcopy(ammo),'id':'wrong-caliber','name':'5.56x45','quantity':40})
    plan=quote(character,data,{'recipe_id':'recipe-v26','ammo_template_id':template.id,'ammo_variant':'bp'})
    assert any(message.startswith('5.45x39: не хватает 30') for message in plan['missing'])


@pytest.mark.parametrize('recipe_id,variant,price,difficulty',[
    ('recipe-v23','handmade',10,45),('recipe-v29','improved',60,75)])
def test_ammo_special_recipes_use_catalog_caliber_price_and_cannot_feed_bp(
        client,workshop,auth_headers,monkeypatch,recipe_id,variant,price,difficulty):
    from app.services.crafting import quote
    template=ammo_workshop(workshop);character=workshop['actor_character']
    data=deepcopy(character.data)
    next(item for item in data['inventory']['pockets'] if item['name']=='Порох')['quantity']=4
    character.data=data;db.session.commit()
    payload={'recipe_id':recipe_id,'ammo_template_id':template.id,'ammo_variant':variant}
    plan=quote(character,character.data,payload)
    assert plan['recipe']['difficulty_options']==[difficulty]
    assert not plan['missing'],plan['missing']
    started=call(client,workshop,auth_headers,'start',**payload)
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    special=next(item for item in finished.json['data']['inventory']['pockets']
        if item['attributes'].get('ammo_variant')==variant)
    assert (special['quantity'],special['price'],special['templateId'])==(20,price,template.id)
    no_base=deepcopy(finished.json['data']);special['quantity']=30
    no_base['inventory']['pockets']=[special,{'name':'Металлические детали','category':'crafting_material','quantity':5}]
    bp=quote(character,no_base,{'recipe_id':'recipe-v26','ammo_template_id':template.id,'ammo_variant':'bp'})
    assert any(message.startswith('5.45x39: не хватает 30') for message in bp['missing'])


def test_improved_ammo_fractional_catalog_price_rounds_down(client,workshop,auth_headers,monkeypatch):
    template=ammo_workshop(workshop,'.45 ACP');character=workshop['actor_character']
    data=deepcopy(character.data)
    next(item for item in data['inventory']['pockets'] if item['name']=='Порох')['quantity']=4
    character.data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-v29',ammo_template_id=template.id)
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    item=next(item for item in finished.json['data']['inventory']['pockets'] if item['name']=='.45 ACP улучшенные')
    assert template.price==35 and item['price']==52


@pytest.mark.parametrize('recipe_id,name,count,price', [
    ('recipe-v38','Форма для патронов',1,200),
    ('recipe-v41','Форма гранаты',2,650),
])
def test_craft_molds_have_approved_catalog_properties_and_are_created(
        client,workshop,auth_headers,monkeypatch,recipe_id,name,count,price):
    from app.services.equipment_catalog import load_catalog

    template_data=next(item for item in load_catalog() if item['name']==name)
    assert (template_data['price'],template_data['weight'],template_data['volume'])==(price,.25,.25)
    assert template_data['attributes']['consumed_on_use']
    db.session.add(ItemTemplate(**template_data))
    character=workshop['actor_character']
    data=deepcopy(character.data)
    data['skills']['other']['engineering']={'base':20,'bonus':0}
    data['inventory']['pockets']=[{'name':material,'category':'crafting_material','quantity':amount}
        for material,amount in [('Металлические детали',3),('Взрыватель',1),('Запал',1)]]
    character.data=data
    placement=LocationCharacter.query.filter_by(character_id=character.id).first()
    db.session.add(LocationObject(location_id=placement.location_id,name='Смесительный станок',type='workbench',
        tile_x=round(placement.pos_x),tile_y=round(placement.pos_y),properties={}))
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id=recipe_id)
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert units(finished.json['data'],name)==count


@pytest.mark.parametrize('recipe_id,name,material,price,size',[
    ('recipe-f13','Датчик','Электронные материалы',300,.1),
    ('recipe-v39','Смесь Дымовая','Химические материалы',600,.25),
])
def test_approved_components_can_be_crafted_and_consumed_in_next_recipe(
        client,workshop,auth_headers,monkeypatch,recipe_id,name,material,price,size):
    from app.services.equipment_catalog import load_catalog
    from app.services.crafting import _allocate
    template=next(item for item in load_catalog() if item['name']==name)
    assert (template['category'],template['price'],template['weight'],template['volume'])==('crafting_material',price,size,size)
    db.session.add(ItemTemplate(**template))
    character=workshop['actor_character'];data=deepcopy(character.data)
    data['skills']['other']['engineering']={'base':20}
    data['inventory']['pockets']=[{'name':material,'category':'crafting_material','quantity':1}]
    character.data=data
    placement=workshop['actor_location']
    db.session.add(LocationObject(location_id=placement.location_id,name='Смесительный станок',type='workbench',
        tile_x=round(placement.pos_x),tile_y=round(placement.pos_y),properties={}))
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id=recipe_id)
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert units(finished.json['data'],name)==1
    allocations,missing=_allocate(finished.json['data'],{'id':'component-reuse','components':[{'name':name,'quantity':1}]})
    assert not missing and sum(entry['amount'] for entry in allocations)==1
    assert units(finished.json['data'],material)==0


def test_grenade_molds_are_reserved_as_consumed_items(app):
    from app.services.crafting import _allocate, _escrow, recipe_by_id

    recipe=recipe_by_id('recipe-v12')
    mold_index=next(i for i,part in enumerate(recipe['components']) if part['name']=='Форма гранаты')
    assert recipe['components'][mold_index]['quantity']==2
    data={'inventory':{'pockets':[{'name':'Форма гранаты','category':'crafting_material','quantity':2}]}}
    allocations,_=_allocate(data,recipe)
    mold_allocations=[entry for entry in allocations if entry['component']==mold_index]
    assert sum(entry['amount'] for entry in mold_allocations)==2
    reserved=_escrow(data,mold_allocations)
    assert reserved[0]['amount']==2
    assert data['inventory']['pockets']==[]


def test_crafting_quote_reports_inventory_counts_for_alternative_materials(app):
    from app.services.crafting import _allocate, recipe_by_id

    recipe=recipe_by_id('recipe-m8')
    data={'inventory':{'pockets':[{'name':'Тканные материалы','category':'crafting_material','quantity':2}]}}
    allocations,missing,statuses=_allocate(data,recipe,with_status=True)
    assert missing == ['Граната: не хватает 1']
    assert statuses[0]['alternatives']==[{'name':'Граната','available':0,'required':1}]
    assert statuses[1]['alternatives']==[
        {'name':'Электронные материалы','available':0,'required':1},
        {'name':'Тканные материалы','available':2,'required':1},
    ]
    assert allocations[0]['item']['name']=='Тканные материалы'


@pytest.mark.parametrize('recipe_id,ingredient,amount',[('recipe-aa30','Активированный уголь (Таблетка)',1),
    ('recipe-aa44','Активированный уголь (Таблетка)',1),
    ('recipe-aa45','Активированный уголь (Таблетка)',4),
    ('recipe-aa46','Активированный уголь (Таблетка)',6),
    ('recipe-m5','Веревка 10м',2),('recipe-m11','Веревка 10м',1)])
def test_catalog_component_names_match_recipes(app,recipe_id,ingredient,amount):
    from app.services.crafting import _allocate, recipe_by_id
    recipe=recipe_by_id(recipe_id)
    data={'inventory':{'pockets':[{'name':ingredient,'category':'consumable','quantity':amount}]}}
    allocations,missing=_allocate(data,recipe)
    assert any(entry['amount']==amount for entry in allocations)
    assert not any(text.casefold().startswith(('активированный уголь:','веревка:')) for text in missing)
    data['inventory']['pockets'][0]['quantity']=amount-1
    assert any(text.casefold().startswith(('активированный уголь:','веревка:')) for text in _allocate(data,recipe)[1])


def test_bark_tincture_spends_catalog_charcoal_tablet(client,workshop,auth_headers,monkeypatch):
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['medicine']={'base':13,'bonus':0}
    data['inventory']['pockets']=[{'name':'Вода','category':'consumable','quantity':1},
        {'name':'Активированный уголь (Таблетка)','category':'consumable','quantity':2}]
    workshop['actor_character'].data=data
    db.session.add(ItemTemplate(name='Очищенная настойка коры',category='consumable',price=300))
    db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa30')
    assert quoted.status_code==200 and quoted.json['result']['missing']==[],quoted.json
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa30')
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=5;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0])
    assert finished.status_code==200,finished.json
    assert units(finished.json['data'],'Активированный уголь (Таблетка)')==1
    assert units(finished.json['data'],'Очищенная настойка коры')==1


def place_distillery(workshop, *, portable=False):
    actor=workshop['actor_location']
    properties={'craft_workbench':'Самогонный Аппарат'}
    if portable:
        properties.update(portable_station='recipe-m40',station_tools=[{'name':'Паяльные инструменты','uses':3,'maxUses':3}])
    station=LocationObject(location_id=actor.location_id,name='Самогонный Аппарат',type='workbench',
        tile_x=round(actor.pos_x)+1,tile_y=round(actor.pos_y),properties=properties)
    db.session.add(station);db.session.commit()
    return station


def advance_distillery_clock(client, workshop, auth_headers, minutes):
    lobby=workshop['lobby']
    days, minute=divmod(lobby.game_time_minutes+minutes,1440)
    response=client.patch(f'/lobbies/{lobby.id}/time',headers=auth_headers(workshop['actor_user']),
        json={'game_day':lobby.game_day+days,'game_time_minutes':minute})
    assert response.status_code==200,response.json


def test_spirit_recipe_accepts_catalog_beer_and_moonshine_still(client,workshop,auth_headers,monkeypatch):
    from app.services.character_starting_purchase import item_from_template

    beer = catalog_template('Банка пива')
    output = catalog_template('Спирт')
    still = {'id': 'legacy-still', 'name': 'Самогонный Аппарат', 'category': 'tool', 'quantity': 1}
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':20,'bonus':0}
    data['inventory']['pockets']=[
        {'name':'Вода','category':'consumable','quantity':5},
        item_from_template(beer),
        {'name':'Хлеб','category':'food','quantity':2},
        still,
    ]
    workshop['actor_character'].data=data
    db.session.commit()
    packed=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa7')
    assert any('размещённому' in text for text in packed.json['result']['missing'])
    station=place_distillery(workshop,portable=True)
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa7')
    assert quoted.status_code==200 and quoted.json['result']['missing']==[],quoted.json
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa7')
    assert started.status_code==200,started.json
    reserved = {'Вода': 5, 'Банка пива': 1, 'Хлеб': 2}
    assert {row['item']['name']: row['amount'] for row in started.json['result']['escrow']} == reserved
    assert started.json['data']['inventory']['pockets'] == [still]
    cancelled = call(client, workshop, auth_headers, 'cancel', job_id=started.json['result']['id'])
    assert cancelled.status_code == 200 and cancelled.json['result']['cancelled'], cancelled.json
    assert {name: units(cancelled.json['data'], name) for name in reserved} == reserved
    assert units(cancelled.json['data'], 'Спирт') == 0

    started = call(client, workshop, auth_headers, 'start', recipe_id='recipe-aa7', operation_id='start-after-cancel')
    assert started.status_code == 200, started.json
    job = started.json['result']
    assert job['preparation_remaining']==600
    placement=workshop['actor_location']
    origin=placement.pos_x
    advance_distillery_clock(client,workshop,auth_headers,4)
    placement.pos_x+=4;db.session.commit()
    advance_distillery_clock(client,workshop,auth_headers,60)
    assert workshop['actor_character'].data['crafting']['current']['preparation_remaining']==360
    placement.pos_x=origin;db.session.commit()
    advance_distillery_clock(client,workshop,auth_headers,6)
    job=workshop['actor_character'].data['crafting']['current']
    placement.pos_x+=4;db.session.commit()
    assert_recipe_wait(client, workshop, auth_headers, job, 1440, chosen_loss=[])
    remote=call(client,workshop,auth_headers,'finish',job_id=job['id'],chosen_loss=[])
    assert remote.status_code==400 and 'Вернитесь' in str(remote.json)
    placement.pos_x=origin;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    from types import SimpleNamespace
    monkeypatch.setattr('app.services.crafting.random.SystemRandom',lambda:SimpleNamespace(randint=lambda *args:100))
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['success']
    bottles = [item for item in finished.json['data']['inventory']['pockets'] if item.get('templateId') == output.id]
    assert sum(item['quantity'] for item in bottles) == 3
    assert all(item['uses'] == item['maxUses'] == 10 for item in bottles)
    assert still in finished.json['data']['inventory']['pockets']
    assert station.properties['station_tools'][0]['uses']==1
    assert {name: units(finished.json['data'], name) for name in reserved} == dict.fromkeys(reserved, 0)


def test_distillery_is_exclusive_until_cancelled_and_other_apparatus_is_independent(client,workshop,auth_headers):
    station=place_distillery(workshop,portable=True)
    second_station=place_distillery(workshop,portable=True)
    catalog_template('Самогон')
    actor,other=workshop['actor_location'],workshop['target_location']
    other.pos_x,other.pos_y=actor.pos_x,actor.pos_y
    for placement in (actor,other):
        data=deepcopy(placement.character.data)
        data.setdefault('skills',{}).setdefault('other',{})['medicine']={'base':20}
        data['inventory']['pockets']=[{'name':'Спирт','quantity':1,'uses':10,'maxUses':10},{'name':'Сахар','quantity':1}]
        placement.character.data=data
    db.session.commit()
    first=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa48',station_id=station.id)
    assert first.status_code==200,first.json
    competitor={**workshop,'actor_character':other.character,'actor_user':workshop['target_user']}
    tincture=call(client,competitor,auth_headers,'quote',recipe_id='recipe-aa29',station_id=station.id)
    assert 'Самогонный аппарат уже занят' in tincture.json['result']['missing']
    blocked=call(client,competitor,auth_headers,'start',recipe_id='recipe-aa48',station_id=station.id)
    assert blocked.status_code in {400,409} and 'занят' in str(blocked.json)
    assert units(other.character.data,'Спирт')==10
    separate=call(client,competitor,auth_headers,'start',recipe_id='recipe-aa48',station_id=second_station.id)
    assert separate.status_code==200,separate.json
    assert call(client,workshop,auth_headers,'cancel',job_id=first.json['result']['id']).status_code==200
    assert call(client,competitor,auth_headers,'cancel',job_id=separate.json['result']['id']).status_code==200
    restart=call(client,competitor,auth_headers,'start',recipe_id='recipe-aa48',station_id=station.id,operation_id='next-batch')
    assert restart.status_code==200,restart.json
    assert station.properties['station_tools'][0]['uses']==1


def test_beer_recipe_produces_eight_catalog_cans(client,workshop,auth_headers,monkeypatch):
    output = catalog_template('Банка пива')
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':20,'bonus':0}
    data['inventory']['pockets']=[
        {'name':'Вода','category':'consumable','quantity':3},
        {'name':'Хлеб','category':'food','quantity':1},
        {'name':'Сахар','category':'food','quantity':1},
        {'name':'Металлические детали','category':'crafting_material','quantity':2},
        {'name':'Самогонный Аппарат','category':'tool','quantity':1},
    ]
    workshop['actor_character'].data=data
    db.session.commit()
    place_distillery(workshop)
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa13')
    assert quoted.status_code==200 and quoted.json['result']['missing']==[],quoted.json
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa13')
    assert started.status_code==200,started.json
    job = started.json['result']
    assert {row['item']['name']: row['amount'] for row in job['escrow']} == {
        'Вода': 3, 'Хлеб': 1, 'Сахар': 1, 'Металлические детали': 2,
    }
    assert job['preparation_remaining']==1800
    advance_distillery_clock(client,workshop,auth_headers,30)
    job=workshop['actor_character'].data['crafting']['current']
    assert_recipe_wait(client, workshop, auth_headers, job, 2880, chosen_loss=[])
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['success']
    cans = [item for item in finished.json['data']['inventory']['pockets'] if item.get('templateId') == output.id]
    assert sum(item['quantity'] for item in cans) == 8
    assert all(item['uses'] == item['maxUses'] == 1 for item in cans)
    assert units(finished.json['data'],'Самогонный Аппарат')==1
    replay = call(client,workshop,auth_headers,'finish',job_id=job['id'],chosen_loss=[])
    assert replay.status_code == 200 and replay.json['result']['replayed'], replay.json
    assert replay.json['data']['inventory']['pockets'] == finished.json['data']['inventory']['pockets']


def test_moonshine_recipe_spends_five_spirit_uses_and_keeps_still(client,workshop,auth_headers,monkeypatch):
    output = catalog_template('Самогон')
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['medicine']={'base':20,'bonus':0}
    data['inventory']['pockets']=[
        {'name':'Спирт','category':'consumable','quantity':1,'uses':10,'maxUses':10},
        {'name':'Сахар','category':'food','quantity':1},
        {'name':'Самогонный Аппарат','category':'tool','quantity':1},
    ]
    workshop['actor_character'].data=data
    db.session.commit()
    place_distillery(workshop)
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa48')
    assert quoted.status_code==200 and quoted.json['result']['missing']==[],quoted.json
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa48')
    assert started.status_code==200,started.json
    assert units(started.json['data'],'Спирт')==5
    job = started.json['result']
    assert {row['item']['name']: row['amount'] for row in job['escrow']} == {'Спирт': 5, 'Сахар': 1}
    assert job['preparation_remaining']==3600
    advance_distillery_clock(client,workshop,auth_headers,60)
    job=workshop['actor_character'].data['crafting']['current']
    assert_recipe_wait(client, workshop, auth_headers, job, 1440, chosen_loss=[])
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['success']
    assert sum(item['quantity'] for item in finished.json['data']['inventory']['pockets']
               if item.get('templateId') == output.id) == 4
    assert units(finished.json['data'],'Спирт')==5
    assert units(finished.json['data'],'Самогонный Аппарат')==1
    replay = call(client,workshop,auth_headers,'finish',job_id=job['id'],chosen_loss=[])
    assert replay.status_code == 200 and replay.json['result']['replayed'], replay.json
    assert replay.json['data']['inventory']['pockets'] == finished.json['data']['inventory']['pockets']


@pytest.mark.parametrize('vessel_name,uses,maximum', [
    ('Котелок (Объем 2 литра)(6 использований воды)', 6, 6),
    ('Котелок (Объем 2 литра)(6 использований воды)', 0, 6),
    ('Кастрюля (Объем 5 литров) (15 использований воды)', 15, 15),
])
def test_glue_recipe_uses_catalog_pot_without_consuming_it(client,workshop,auth_headers,monkeypatch,vessel_name,uses,maximum):
    from app.services.character_starting_purchase import item_from_template

    vessel = item_from_template(catalog_template(vessel_name))
    vessel.update(uses=uses, maxUses=maximum)
    output = catalog_template('Клей')
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':20,'bonus':0}
    data['inventory']['pockets']=[{'name':'Жир','category':'misc','quantity':3},
        vessel]
    workshop['actor_character'].data=data
    placed=LocationCharacter.query.filter_by(character_id=workshop['actor_character'].id).first()
    fire = LocationObject(location_id=placed.location_id,name='Костёр',type='campfire',
        tile_x=round(placed.pos_x),tile_y=round(placed.pos_y),properties={'is_lit':False})
    db.session.add(fire)
    db.session.commit()
    dark = call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa9')
    assert dark.status_code == 200 and dark.json['result']['missing'] == ['Нужны: Костер'], dark.json
    fire.properties = {'is_lit': True}
    db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa9')
    assert quoted.status_code==200 and quoted.json['result']['missing']==[],quoted.json
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa9')
    assert started.status_code==200,started.json
    job = started.json['result']
    assert [(entry['item']['name'], entry['amount']) for entry in job['escrow']] == [('Жир', 3)]
    assert_recipe_wait(client, workshop, auth_headers, job, 60, chosen_loss=[])
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert units(finished.json['data'],'Жир')==0
    assert vessel in finished.json['data']['inventory']['pockets']
    assert [item['quantity'] for item in finished.json['data']['inventory']['pockets']
            if item.get('templateId') == output.id] == [1]
    if vessel_name.startswith('Кастрюля'):
        catalog_template('Кустарный самогонный аппарат')
        data = deepcopy(workshop['actor_character'].data)
        data['features'] = {'specialTraits': [{'name': 'Мастер на все руки'}]}
        data['inventory']['pockets'].extend(
            {'name': name, 'category': 'crafting_material', 'quantity': quantity}
            for name, quantity in [('Металлические детали', 4), ('Электронные материалы', 4),
                ('Стеклянная бутылка', 2), ('Канистра', 1), ('Датчик', 1)]
        )
        workshop['actor_character'].data = data
        db.session.commit()
        still_job = call(client, workshop, auth_headers, 'start', recipe_id='recipe-m40', operation_id='start-still')
        assert still_job.status_code == 200, still_job.json
        reserved = next(entry for entry in still_job.json['result']['escrow']
                        if entry['item'].get('templateId') == vessel['templateId'])
        assert (reserved['maximum'], reserved['amount'], reserved['item']['quantity'], reserved['item']['uses']) == (1, 1, 1, 15)
        assert not any(item['id'] == vessel['id'] for item in still_job.json['data']['inventory']['pockets'])


def test_extinguished_campfire_does_not_satisfy_recipe_tool(workshop):
    from app.services.crafting import _tools,recipe_by_id
    placed=LocationCharacter.query.filter_by(character_id=workshop['actor_character'].id).first()
    fire=LocationObject(location_id=placed.location_id,name='Костёр',type='campfire',
        tile_x=round(placed.pos_x),tile_y=round(placed.pos_y),properties={'is_lit':False})
    db.session.add(fire);db.session.commit()
    recipe=recipe_by_id('recipe-aa9')
    data={'inventory':{'pockets':[{'name':'Котелок','category':'misc','quantity':1}]}}
    assert 'Костер' in _tools(workshop['actor_character'],data,recipe,0)
    fire.properties={'is_lit':True};db.session.commit()
    assert _tools(workshop['actor_character'],data,recipe,0)==[]


def test_glue_uses_dead_mutant_on_the_map_without_fat(client,workshop,auth_headers,monkeypatch):
    character=workshop['actor_character']
    data=deepcopy(character.data)
    data['skills']['other']['survival']={'base':20,'bonus':0}
    data['inventory']['pockets']=[{'id':'pot','name':'Котелок','category':'misc','quantity':1}]
    character.data=data
    mutant=workshop['target_character']
    mutant.data={**mutant.data,'is_mutant':True,'health':{'current':700,'max':700,'effects':[]}}
    placement=workshop['actor_location']
    db.session.add(LocationObject(location_id=placement.location_id,name='Костёр',type='campfire',
        tile_x=round(placement.pos_x),tile_y=round(placement.pos_y),properties={'is_lit':True}))
    output=catalog_template('Клей')
    db.session.commit()
    blocked=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa9')
    assert blocked.status_code==400,blocked.json
    mutant.data={**mutant.data,'health':{'current':0,'max':700,'effects':[{'type':'death'}]}}
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa9')
    assert started.status_code==200,started.json
    job=started.json['result']
    assert job['escrow']==[]
    assert_recipe_wait(client,workshop,auth_headers,job,60,chosen_loss=[])
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    result=call(client,workshop,auth_headers,'finish',job_id=job['id'],chosen_loss=[])
    assert result.status_code==200,result.json
    assert any(item.get('templateId')==output.id for item in result.json['data']['inventory']['pockets'])


def test_empty_catalog_pot_remains_usable_as_cooking_tool(workshop):
    from app.services.crafting import _tools,recipe_by_id
    placed=LocationCharacter.query.filter_by(character_id=workshop['actor_character'].id).first()
    db.session.add(LocationObject(location_id=placed.location_id,name='Костёр',type='campfire',
        tile_x=round(placed.pos_x),tile_y=round(placed.pos_y),properties={'is_lit':True}))
    db.session.commit()
    recipe=recipe_by_id('recipe-aa9')
    data={'inventory':{'pockets':[{'name':'Котелок (Объем 2 литра)(6 использований воды)',
        'category':'misc','quantity':1,'uses':0,'maxUses':6}]}}
    assert _tools(workshop['actor_character'],data,recipe,0)==[]
    data['inventory']['pockets'][0]['quantity']=0
    assert _tools(workshop['actor_character'],data,recipe,0)


@pytest.mark.parametrize('recipe_id,name,amount',[('recipe-m33','Стеклянная бутылка (Объем 1 литр) (3 использования воды)',3),
    ('recipe-m40','Стеклянная бутылка (Объем 1 литр) (3 использования воды)',2),
    ('recipe-m44','Стеклянная бутылка (Объем 1 литр) (3 использования воды)',10),
    ('recipe-m34','Пластиковая бутылка (Объем 1 литр) (3 использования воды)',8),
    ('recipe-m40','Канистра(Пустая)(Объем 15 литров)(45 использований воды)',1)])
def test_catalog_vessels_cover_recipe_components(app,recipe_id,name,amount):
    from app.services.crafting import _allocate, item_names, recipe_by_id
    recipe=recipe_by_id(recipe_id)
    data={'inventory':{'pockets':[{'name':name,'category':'misc','quantity':amount,'uses':3,'maxUses':3}]}}
    component=next((i for i,c in enumerate(recipe['components']) if c['name'].casefold() in item_names(data['inventory']['pockets'][0])),None)
    assert component is not None
    allocations,missing=_allocate(data,recipe)
    assert sum(entry['amount'] for entry in allocations if entry['component']==component)==amount
    assert all(entry['whole'] for entry in allocations if entry['component']==component)
    assert not any(text.startswith(recipe['components'][component]['name']+':') for text in missing)
    data['inventory']['pockets'][0]['quantity']=amount-1
    assert any(text.startswith(recipe['components'][component]['name']+':') for text in _allocate(data,recipe)[1])


def test_vessel_reservation_takes_entire_bottle_and_cancel_restores_it(client,workshop,auth_headers,monkeypatch):
    from app.services.crafting import recipe_by_id
    recipe={**recipe_by_id('recipe-f4'),'id':'test-bottle',
        'components':[{'name':'Стеклянная бутылка','quantity':1}],
        'tools_text':'Не нужен'}
    monkeypatch.setattr('app.services.crafting.recipe_by_id',lambda _:recipe)
    data=deepcopy(workshop['actor_character'].data)
    name='Стеклянная бутылка (Объем 1 литр) (3 использования воды)'
    data['inventory']['pockets']=[{'name':name,'category':'misc','quantity':2,'uses':3,'maxUses':3}]
    workshop['actor_character'].data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='test-bottle')
    assert started.status_code==200,started.json
    assert started.json['data']['inventory']['pockets'][0]['quantity']==1
    assert started.json['data']['inventory']['pockets'][0]['uses']==3
    cancelled=call(client,workshop,auth_headers,'cancel',job_id=started.json['result']['id'])
    assert cancelled.status_code==200,cancelled.json
    assert sum(item['quantity'] for item in cancelled.json['data']['inventory']['pockets'] if item['name']==name)==2


@pytest.mark.parametrize('recipe_id,liters',[('recipe-v12',3),('recipe-v13',1)])
def test_grenade_fuel_uses_catalog_one_liter_units(app,recipe_id,liters):
    from app.services.crafting import _allocate, recipe_by_id
    recipe=recipe_by_id(recipe_id)
    data={'inventory':{'pockets':[{'name':'Топливо(1 Литр)','category':'misc','quantity':liters}]}}
    allocations,missing=_allocate(data,recipe)
    component=next(i for i,c in enumerate(recipe['components']) if c['name']=='Топливо')
    assert sum(entry['amount'] for entry in allocations if entry['component']==component)==liters
    assert not any(text.startswith('Топливо:') for text in missing)
    data['inventory']['pockets'][0]['quantity']=liters-1
    assert any(text.startswith('Топливо:') for text in _allocate(data,recipe)[1])


def test_three_liters_of_fuel_are_reserved_as_three_catalog_units(client,workshop,auth_headers,monkeypatch):
    from app.services.crafting import recipe_by_id
    recipe={**recipe_by_id('recipe-f4'),'id':'test-fuel',
        'components':[{'name':'Топливо','quantity':3}], 'tools_text':'Не нужен'}
    monkeypatch.setattr('app.services.crafting.recipe_by_id',lambda _:recipe)
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets']=[{'name':'Топливо(1 Литр)','category':'misc','quantity':4}]
    workshop['actor_character'].data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='test-fuel')
    assert started.status_code==200,started.json
    assert units(started.json['data'],'Топливо(1 Литр)')==1
    assert started.json['result']['escrow'][0]['amount']==3


def test_molotov_can_be_created_with_three_spirit_uses(client,workshop,auth_headers,monkeypatch):
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets']=[
        {'name':'Спирт','category':'consumable','quantity':1,'uses':10,'maxUses':10},
        {'name':'Стеклянная бутылка (Объем 1 литр) (3 использования воды)','category':'misc','quantity':1},
        {'name':'Тканные материалы','category':'crafting_material','quantity':1},
    ]
    workshop['actor_character'].data=data
    db.session.add(ItemTemplate(name='Коктейль Молотова',category='grenade',price=300))
    db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-v11')
    assert quoted.status_code==200 and quoted.json['result']['missing']==[],quoted.json
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-v11')
    assert started.status_code==200,started.json
    assert units(started.json['data'],'Спирт')==7
    assert units(started.json['data'],'Стеклянная бутылка (Объем 1 литр) (3 использования воды)')==0
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0])
    assert finished.status_code==200,finished.json
    assert units(finished.json['data'],'Коктейль Молотова')==1


def test_molotov_reserves_half_liter_of_fuel_and_returns_it_on_cancel(client,workshop,auth_headers):
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets']=[
        {'name':'Топливо(1 Литр)','category':'misc','quantity':1,'weight':1,'volume':1},
        {'name':'Стеклянная бутылка (Объем 1 литр) (3 использования воды)','category':'misc','quantity':1},
        {'name':'Тканные материалы','category':'crafting_material','quantity':1},
    ]
    workshop['actor_character'].data=data
    db.session.add(ItemTemplate(name='Коктейль Молотова',category='grenade',price=300))
    db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-v11')
    assert quoted.status_code==200 and quoted.json['result']['missing']==[],quoted.json
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-v11')
    assert started.status_code==200,started.json
    fuel=[item for item in started.json['data']['inventory']['pockets'] if item['name']=='Топливо(1 Литр)']
    assert [item['quantity'] for item in fuel]==[.5]
    reserved=next(entry for entry in started.json['result']['escrow'] if entry['item']['name']=='Топливо(1 Литр)')
    assert reserved['amount']==.5 and reserved['liquid_liters']
    cancelled=call(client,workshop,auth_headers,'cancel',job_id=started.json['result']['id'])
    assert cancelled.status_code==200,cancelled.json
    fuel=[item for item in cancelled.json['data']['inventory']['pockets'] if item['name']=='Топливо(1 Литр)']
    assert sum(item['quantity'] for item in fuel)==1


def test_molotov_returns_reserved_fuel_to_its_canister(client,workshop,auth_headers):
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets']=[
        {'id':'molotov-canister','name':'Канистра(Пустая)(Объем 15 литров)(45 использований воды)',
         'category':'misc','attributes':{'liquid_capacity_liters':15},'quantity':1,'fuelUsed':True,
         'contents':[{'id':'canister-fuel','name':'Топливо(1 Литр)','category':'misc','quantity':1}]},
        {'name':'Стеклянная бутылка (Объем 1 литр) (3 использования воды)','category':'misc','quantity':1},
        {'name':'Тканные материалы','category':'crafting_material','quantity':1},
    ]
    workshop['actor_character'].data=data
    db.session.add(ItemTemplate(name='Коктейль Молотова',category='grenade',price=300))
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-v11')
    assert started.status_code==200,started.json
    assert [item['quantity'] for item in started.json['data']['inventory']['pockets'][0]['contents']]==[.5]
    cancelled=call(client,workshop,auth_headers,'cancel',job_id=started.json['result']['id'])
    assert cancelled.status_code==200,cancelled.json
    canister=cancelled.json['data']['inventory']['pockets'][0]
    assert sum(item['quantity'] for item in canister['contents'])==1
    assert not any(item['name']=='Топливо(1 Литр)' for item in cancelled.json['data']['inventory']['pockets'])


def test_molotov_spends_only_half_liter_of_fuel_on_success(client,workshop,auth_headers,monkeypatch):
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets']=[
        {'name':'Топливо(1 Литр)','category':'misc','quantity':1},
        {'name':'Стеклянная бутылка (Объем 1 литр) (3 использования воды)','category':'misc','quantity':1},
        {'name':'Тканные материалы','category':'crafting_material','quantity':1},
    ]
    workshop['actor_character'].data=data
    db.session.add(ItemTemplate(name='Коктейль Молотова',category='grenade',price=300))
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-v11')
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0])
    assert finished.status_code==200,finished.json
    fuel=[item for item in finished.json['data']['inventory']['pockets'] if item['name']=='Топливо(1 Литр)']
    assert sum(item['quantity'] for item in fuel)==.5


def test_failed_molotov_loses_only_reserved_half_liter(client,workshop,auth_headers,monkeypatch):
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets']=[
        {'name':'Топливо(1 Литр)','category':'misc','quantity':1},
        {'name':'Стеклянная бутылка (Объем 1 литр) (3 использования воды)','category':'misc','quantity':1},
        {'name':'Тканные материалы','category':'crafting_material','quantity':1},
    ]
    workshop['actor_character'].data=data
    db.session.add(ItemTemplate(name='Коктейль Молотова',category='grenade',price=300))
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-v11')
    assert started.status_code==200,started.json
    fuel_index=next(index for index,entry in enumerate(started.json['result']['escrow'])
                    if entry['item']['name']=='Топливо(1 Литр)')
    workshop['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':False,'roll':1,'automatic':False})
    failed=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[fuel_index])
    assert failed.status_code==200,failed.json
    assert failed.json['result']['lost'][str(fuel_index)]==.5
    fuel=[item for item in failed.json['data']['inventory']['pockets'] if item['name']=='Топливо(1 Литр)']
    assert sum(item['quantity'] for item in fuel)==.5


@pytest.mark.parametrize('water_name', ['Дождевая вода', 'Болотная вода'])
def test_water_purification_spends_one_tablet_from_chlorine_packet(client,workshop,auth_headers,water_name):
    from app.services.character_starting_purchase import item_from_template

    water = item_from_template(catalog_template(water_name))
    water['uses'] = 2
    chlorine = item_from_template(catalog_template('Хлорные таблетки (10 штук)'))
    output = catalog_template('Радиоактивная вода')
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':13,'bonus':0}
    data['inventory']['pockets'] = [water, chlorine]
    workshop['actor_character'].data=data
    db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa10')
    assert quoted.status_code==200 and quoted.json['result']['missing']==[],quoted.json
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa10')
    assert started.status_code==200,started.json
    packet=next(item for item in started.json['data']['inventory']['pockets'] if item['name']=='Хлорные таблетки (10 штук)')
    assert packet['uses']==9 and packet['maxUses']==10 and packet['quantity']==1
    job = started.json['result']
    assert {entry['item']['name']: entry['amount'] for entry in job['escrow']} == {
        water_name: 2, chlorine['name']: 1,
    }
    assert_recipe_wait(client, workshop, auth_headers, job, 1, chosen_loss=[])
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert units(finished.json['data'],'Хлорные таблетки (10 штук)')==9
    assert finished.json['result']['success']
    assert [item['quantity'] for item in finished.json['data']['inventory']['pockets']
            if item.get('templateId') == output.id] == [1]


def test_cancel_water_purification_restores_chlorine_tablet(client,workshop,auth_headers):
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':13,'bonus':0}
    data['inventory']['pockets']=[{'name':'Дождевая вода','category':'consumable','quantity':2},
        {'name':'Хлорные таблетки (10 штук)','category':'misc','quantity':1}]
    workshop['actor_character'].data=data
    db.session.add(ItemTemplate(name='Радиоактивная вода',category='consumable',price=0));db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa10')
    assert started.status_code==200,started.json
    cancelled=call(client,workshop,auth_headers,'cancel',job_id=started.json['result']['id'])
    assert cancelled.status_code==200,cancelled.json
    assert units(cancelled.json['data'],'Хлорные таблетки (10 штук)')==10
    assert units(cancelled.json['data'],'Дождевая вода')==2
    assert units(cancelled.json['data'],'Радиоактивная вода')==0


def test_empty_chlorine_packet_cannot_satisfy_water_recipe(app):
    from app.services.crafting import _allocate,recipe_by_id
    recipe=recipe_by_id('recipe-aa10')
    data={'inventory':{'pockets':[{'name':'Дождевая вода','quantity':2},
        {'name':'Хлорные таблетки (10 штук)','quantity':1,'uses':0,'maxUses':10}]}}
    _,missing=_allocate(data,recipe)
    assert any(text.startswith('Хлорные таблетки:') for text in missing)


@pytest.mark.parametrize('cancel', [True, False])
def test_aeration_spends_charges_across_filters_and_produces_eight_water_portions(
        client,workshop,auth_headers,cancel):
    from app.services.character_starting_purchase import item_from_template
    from app.services.equipment_catalog import load_catalog
    from app.services.gas_mask_filters import filter_charges
    from app.services.crafting import recipe_by_id

    templates={item['name']:ItemTemplate(**item) for item in load_catalog()
        if item['name'] in {'Вода','Радиоактивная вода','Фильтр для противогаза'}}
    db.session.add_all(templates.values());db.session.flush()
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':20,'bonus':0}
    filters=[]
    for remaining in (6,7):
        item=item_from_template(templates['Фильтр для противогаза'])
        item['durability']=remaining
        item['attributes']['durability']=remaining
        filters.append(item)
    radioactive=item_from_template(templates['Радиоактивная вода'],4)
    radioactive['uses'] = 1
    assert _available_uses(radioactive) == 10
    data['inventory']['pockets']=[radioactive,*filters]
    workshop['actor_character'].data=data
    placement=LocationCharacter.query.filter_by(character_id=workshop['actor_character'].id).first()
    db.session.add(LocationObject(location_id=placement.location_id,
        name=recipe_by_id('recipe-aa12')['tools_text'],type='station_aeration',
        tile_x=round(placement.pos_x),tile_y=round(placement.pos_y),properties={'portable_station':'recipe-m34'}))
    db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa12')
    assert started.status_code==200,started.json
    inventory=started.json['data']['inventory']['pockets']
    assert [(i['id'], filter_charges(i)) for i in inventory if i['name']=='Фильтр для противогаза'] == [(filters[1]['id'], 3)]
    job = started.json['result']
    assert units(started.json['data'],'Радиоактивная вода')==0
    assert sum(entry['amount'] for entry in job['escrow'] if entry.get('filter_charges')) == 10
    if cancel:
        result=call(client,workshop,auth_headers,'cancel',job_id=started.json['result']['id'])
        assert result.status_code==200,result.json
        inventory=result.json['data']['inventory']['pockets']
        assert sum(filter_charges(i) for i in inventory if i['name']=='Фильтр для противогаза')==13
        assert units(result.json['data'],'Радиоактивная вода')==10
        assert units(result.json['data'],'Вода')==0
    else:
        start_x=placement.pos_x
        placement.pos_x+=3
        db.session.commit()
        assert_recipe_wait(client, workshop, auth_headers, job, 30, chosen_loss=[])
        remote=call(client,workshop,auth_headers,'finish',job_id=job['id'],chosen_loss=[])
        assert remote.status_code==400 and 'Вернитесь к аэрационной установке' in str(remote.json)
        placement.pos_x=start_x
        db.session.commit()
        result=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
        assert result.status_code==200,result.json
        assert units(result.json['data'],'Вода')==8
        assert units(result.json['data'],'Радиоактивная вода')==0
        produced = [item for item in result.json['data']['inventory']['pockets']
                    if item.get('templateId') == templates['Вода'].id]
        assert [(item['quantity'], item['uses'], item['maxUses']) for item in produced] == [(3, 2, 3)]
        replay = call(client, workshop, auth_headers, 'finish', job_id=job['id'], chosen_loss=[])
        assert replay.status_code == 200 and replay.json['result']['replayed'], replay.json
        assert replay.json['data']['inventory']['pockets'] == result.json['data']['inventory']['pockets']


def test_grenade_recipe_chain_accepts_catalog_names_and_smoke_output(app):
    from app.services.crafting import _allocate, _template, recipe_by_id
    smoke=ItemTemplate(name='Дымовая граната РДГ-6',category='grenade',price=300)
    napalm=ItemTemplate(name='Зажигательная граната Napalm-AN',category='grenade',price=300)
    thermobaric=ItemTemplate(name='Термобарическая граната РГ-60ТБ',category='grenade',price=300)
    db.session.add_all([smoke,napalm,thermobaric]);db.session.flush()
    assert _template('РДГ-6').id==smoke.id
    assert _template(recipe_by_id('recipe-v12')['output_name']).id==napalm.id
    assert _template(recipe_by_id('recipe-v13')['output_name']).id==thermobaric.id
    for recipe_id,ingredient in [('recipe-v5','Граната Ф-1'),('recipe-v6','Граната РГД-5')]:
        recipe=recipe_by_id(recipe_id)
        _,missing=_allocate({'inventory':{'pockets':[{'name':ingredient,'category':'grenade','quantity':1}]}},recipe)
        assert not any(text.startswith(recipe['components'][0]['name']+':') for text in missing)


def test_tool_charge_suffix_matches_recipe_and_multiple_sets_cover_fifty_uses(workshop):
    from app.services.crafting import _allocate, _tools, recipe_by_id
    for recipe_id,name,maximum in [
        ('recipe-m42','Набор инструментов Оружейника (Расширенные) 40/40',40),
        ('recipe-m43','Набор инструментов Бронника (Расширенные) 35/35',35),
    ]:
        recipe=recipe_by_id(recipe_id)
        data={'inventory':{'pockets':[{'name':name,'category':'tool','quantity':2,
            'uses':maximum,'maxUses':maximum}]}}
        allocations,missing=_allocate(data,recipe)
        assert not any(text.startswith(recipe['components'][-1]['name']+':') for text in missing)
        assert sum(a['amount'] for a in allocations if a['component']==len(recipe['components'])-1)==50
    recipe={**recipe_by_id('recipe-flame-41'),'tools_text':'Смесительные инструменты',
        'difficulty_options':[50]}
    data={'inventory':{'pockets':[{'name':'Смесительные инструменты 50/50',
        'category':'tool','quantity':1,'uses':1,'maxUses':50}]}}
    assert _tools(workshop['actor_character'],data,recipe,0)==[]
    data['inventory']['pockets'][0]['uses']=0
    assert _tools(workshop['actor_character'],data,recipe,0)==['Смесительные инструменты']


def test_dry_ration_recipe_spends_one_box_of_ten_matches(client,workshop,auth_headers,monkeypatch):
    from app.services.character_starting_purchase import item_from_template

    matches = catalog_template('Спички (10 штук)')
    output = catalog_template('Сухой паек')
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['survival']={'base':13,'bonus':0}
    data['inventory']['pockets']=[{'name':name,'category':'consumable','quantity':quantity}
        for name,quantity in [('Консервы',2),('Хлеб',1),('Бумага',1)]]
    boxes = [item_from_template(matches), item_from_template(matches)]
    data['inventory']['pockets'].extend(boxes)
    workshop['actor_character'].data=data
    db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa4')
    assert quoted.status_code==200,quoted.json
    assert quoted.json['result']['missing']==[]
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa4')
    assert started.status_code==200,started.json
    job = started.json['result']
    assert_recipe_wait(client, workshop, auth_headers, job, 5, chosen_loss=[0])
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['success']
    pockets = finished.json['data']['inventory']['pockets']
    assert [item['id'] for item in pockets if item.get('templateId') == matches.id] == [boxes[1]['id']]
    assert [item['quantity'] for item in pockets if item.get('templateId') == output.id] == [1]
    repeat = call(client, workshop, auth_headers, 'finish', job_id=job['id'], chosen_loss=[0])
    assert repeat.status_code == 200 and repeat.json['result']['replayed'], repeat.json
    assert repeat.json['data']['inventory']['pockets'] == pockets
    stale_job = call(client, workshop, auth_headers, 'finish', job_id=job['id'], chosen_loss=[0],
                     operation_id='finish-again')
    assert stale_job.status_code == 409, stale_job.json
    assert workshop['actor_character'].data['inventory']['pockets'] == pockets


@pytest.mark.parametrize('name,quantity',[
    ('Анальгин (Таблетка)',3),('Бинт',3),('Шина',2),('Жгут',2),
])
def test_medical_materials_recipe_accepts_each_whole_alternative(app,name,quantity):
    from app.services.crafting import _allocate, recipe_by_id
    recipe=recipe_by_id('recipe-aa23')
    assert [(option['name'],option['quantity']) for option in recipe['components'][0]['alternatives']]==[
        ('Анальгин',3),('Бинт',3),('Шина',2),('Жгут',2)]
    data={'inventory':{'pockets':[{'name':name,'category':'consumable','quantity':quantity}]}}
    allocations,missing=_allocate(data,recipe)
    assert missing==[]
    assert sum(item['amount'] for item in allocations)==quantity
    data['inventory']['pockets'][0]['quantity']=quantity-1
    assert _allocate(data,recipe)[1]


@pytest.mark.parametrize('name,quantity', [
    ('Анальгин (Таблетка)', 3), ('Бинт', 3), ('Шина', 2), ('Жгут', 2),
])
def test_medical_materials_recipe_finishes_with_each_alternative(client,workshop,auth_headers,monkeypatch,name,quantity):
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['medicine']={'base':15,'bonus':0}
    alternatives = [('Анальгин (Таблетка)', 3), ('Бинт', 3), ('Шина', 2), ('Жгут', 2)]
    insufficient = {label: required - 1 for label, required in alternatives}
    data['inventory']['pockets'] = [
        {'name':label,'category':'consumable','quantity':amount} for label, amount in insufficient.items()
    ]
    workshop['actor_character'].data=data
    output = catalog_template('Медицинские материалы')
    db.session.commit()
    blocked = call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa23')
    assert blocked.status_code == 200 and blocked.json['result']['missing'], blocked.json
    assert call(client,workshop,auth_headers,'start',recipe_id='recipe-aa23').status_code == 400
    data = deepcopy(workshop['actor_character'].data)
    next(item for item in data['inventory']['pockets'] if item['name'] == name)['quantity'] = quantity
    workshop['actor_character'].data = data
    db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa23')
    assert quoted.status_code==200,quoted.json
    assert quoted.json['result']['missing']==[]
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa23')
    assert started.status_code==200,started.json
    job = started.json['result']
    assert [(entry['item']['name'], entry['amount']) for entry in job['escrow']] == [(name, quantity)]
    assert_recipe_wait(client, workshop, auth_headers, job, 10, chosen_loss=[])
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100,'automatic':False})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['success']
    assert units(finished.json['data'],name)==0
    assert {label: units(finished.json['data'], label) for label in insufficient if label != name} == {
        label: amount for label, amount in insufficient.items() if label != name
    }
    assert [item['quantity'] for item in finished.json['data']['inventory']['pockets']
            if item.get('templateId') == output.id] == [1]


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


@pytest.mark.parametrize('outcome', ['success', 'cancel', 'failure'])
def test_lockpick_recipe_creates_two_catalog_items(client,workshop,auth_headers,monkeypatch,outcome):
    data=deepcopy(workshop['actor_character'].data)
    data['basic']={'background':{'name':'Полевой учёный'}}
    workshop['actor_character'].data=data
    output = catalog_template('Отмычка')
    actor = workshop['actor_location']
    db.session.add(LocationObject(location_id=actor.location_id, name='Печь', type='household_workbench',
        tile_x=actor.pos_x, tile_y=actor.pos_y, properties={'craft_workbench':'Печь'}))
    db.session.commit()
    quoted=call(client,workshop,auth_headers,'quote',recipe_id='recipe-m27')
    assert quoted.status_code==200,quoted.json
    assert quoted.json['result']['output_id']==output.id
    assert not quoted.json['result']['missing']
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-m27')
    assert started.status_code==200,started.json
    job = started.json['result']
    assert [(entry['item']['name'], entry['amount']) for entry in job['escrow']] == [('Металлические детали', 1)]
    if outcome == 'cancel':
        cancelled = call(client, workshop, auth_headers, 'cancel', job_id=job['id'])
        assert cancelled.status_code == 200 and cancelled.json['result']['cancelled'], cancelled.json
        assert units(cancelled.json['data'], 'Металлические детали') == 2
        assert units(cancelled.json['data'], 'Отмычка') == 0
        return
    assert_recipe_wait(client, workshop, auth_headers, job, 40, chosen_loss=[0])
    roll = 100 if outcome == 'success' else 1
    monkeypatch.setattr('app.services.crafting.resolve_creation', lambda quote: resolve_creation(quote, roll=roll))
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[0])
    assert finished.status_code==200,finished.json
    assert units(finished.json['data'], 'Металлические детали') == 1
    result = finished.json['result']
    items = [item for item in finished.json['data']['inventory']['pockets'] if item.get('templateId') == output.id]
    if outcome == 'failure':
        assert not result['success'] and result['roll'] == 1 and not items
        assert result['lost_items'] == [{'name': 'Металлические детали', 'amount': 1}]
        return
    assert result['success'] and result['output_count'] == 2
    assert [(i['quantity'], i['weight'], i['volume'], i['price']) for i in items] == [
        (2, output.weight, output.volume, output.price),
    ]
    replay = call(client, workshop, auth_headers, 'finish', job_id=job['id'], chosen_loss=[0])
    assert replay.status_code == 200 and replay.json['result']['replayed'], replay.json
    assert replay.json['data']['inventory']['pockets'] == finished.json['data']['inventory']['pockets']


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
    data['inventory']['pockets'].extend([
        {'name':'Химические материалы','category':'crafting_material','quantity':2},
        {'name':'Сахар','category':'crafting_material','quantity':1},
    ])
    workshop['actor_character'].data=data
    placement=LocationCharacter.query.filter_by(character_id=workshop['actor_character'].id).first()
    db.session.add(LocationObject(location_id=placement.location_id,name='Смесительный Станок',
        type='household_workbench',tile_x=round(placement.pos_x),tile_y=round(placement.pos_y),
        properties={'craft_workbench':'Смесительный Станок','level':placement.floor_level or 0}))
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


def test_approved_medicine_recipes_use_existing_materials():
    from app.services.crafting import recipe_by_id
    assert [(entry['name'],entry['quantity']) for entry in recipe_by_id('recipe-aa21')['components']] == [
        ('Химические материалы',2),('Сахар',1)]
    assert [entry['name'] for entry in recipe_by_id('recipe-aa31')['components']] == [
        'Металлические детали','Марля','Бумага']
    assert [(entry['name'],entry['quantity']) for entry in recipe_by_id('recipe-aa39')['components']] == [
        ('Медицинские материалы',2)]


def test_grass_recipe_reads_location_terrain_without_inventory_item(workshop):
    from app.services.crafting import quote
    character=workshop['actor_character']
    location=workshop['location']
    character.data.setdefault('skills',{}).setdefault('other',{})['medicine']={'base':10}
    location.tiles_data=[[{'terrain':'dirt','height':1,'objects':[]}]]
    missing=quote(character,character.data,{'recipe_id':'recipe-aa25'})['missing']
    assert any('клетка с травой' in reason for reason in missing)
    location.tiles_data=[[{'terrain':'grass','height':1,'objects':[]}]]
    available=quote(character,character.data,{'recipe_id':'recipe-aa25'})['missing']
    assert not any('травой' in reason or 'условий ГМом' in reason for reason in available)


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
    data['features']={'specialTraits':[{'ruleId':'trait-c14'},{'ruleId':'trait-c14'}]}
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
        assert started.json['result']['cook_bonus']==(2 if sequence==1 else 1)
        workshop['lobby'].game_time_minutes+=60
        db.session.commit()
        completed=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],
                       operation_id=f'finish-meat-{sequence}')
        assert completed.status_code==200,completed.json
        cooked=[item for item in completed.json['data']['inventory']['pockets']
                if item.get('attributes',{}).get('cooked_meat')=='boiled']
        assert len(cooked)==1
        assert cooked[0]['attributes']['meat_remaining_radiation']==expected
        assert cooked[0]['attributes']['cook_exhaustion_bonus']==2
        assert (cooked[0]['uses'],cooked[0]['price'],cooked[0]['weight'],cooked[0]['volume'])==(3,25,1,1)
        updated=deepcopy(workshop['actor_character'].data)
        updated['features']['specialTraits'].pop()
        workshop['actor_character'].data=updated
        db.session.commit()
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


def test_only_scene_condition_can_be_approved_without_changing_difficulty(client,workshop,auth_headers):
    data=deepcopy(workshop['actor_character'].data)
    data['skills']['other']['medicine']={'base':5,'bonus':0}
    data['skills']['other']['survival']={'base':5,'bonus':0}
    workshop['actor_character'].data=data
    db.session.commit()
    arbitrary=call(client,workshop,auth_headers,'request_tool',recipe_id='recipe-f4',tool_path=['inventory','pockets',3])
    assert arbitrary.status_code==400
    before=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa29')
    assert before.status_code==200,before.json
    assert any('Нужно подтверждение условий ГМом' in part for part in before.json['result']['missing'])
    barrier=call(client,workshop,auth_headers,'quote',recipe_id='recipe-m19')
    assert barrier.status_code==200,barrier.json
    assert any('мебель из дерева' in part for part in barrier.json['result']['missing'])
    request=call(client,workshop,auth_headers,'request_tool',recipe_id='recipe-aa29')
    assert request.status_code==200,request.json
    character=workshop['actor_character']
    response=client.post(f'/lobbies/characters/{character.id}/crafting',headers=auth_headers(workshop['target_user']),json={
        'operation':'approve_tool','operation_id':'forged','character_revision':character.revision,'request_id':request.json['result']['id'],'modifier':0,'approved':True})
    assert response.status_code==403,response.json
    approved=call(client,workshop,auth_headers,'approve_tool',request_id=request.json['result']['id'],modifier=0,approved=True)
    assert approved.status_code==200,approved.json
    quote=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa29',approval_id=request.json['result']['id'])
    assert quote.status_code==200,quote.json
    assert quote.json['result']['difficulty']==before.json['result']['difficulty']
    assert not any('Нужно подтверждение условий ГМом' in part for part in quote.json['result']['missing'])


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


def test_skill_book_cannot_be_read_as_another_skill(client,workshop,auth_headers):
    from app.services.character_starting_purchase import item_from_template

    book=ItemTemplate(name='Книга. Раздел: Сила',category='misc',price=100,weight=0,volume=0,
        attributes={'book_kind':'skill','book_subject':'Физические',
                    'book_skill_path':'physical.strength','reading_seconds':60})
    db.session.add(book);db.session.flush()
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets'].append(item_from_template(book))
    workshop['actor_character'].data=data;db.session.commit()
    rejected=call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',4],
        skill_path='physical.agility')
    assert rejected.status_code==400
    assert 'другому навыку' in rejected.json['error']['message']
    accepted=call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',4],
        skill_path='physical.strength')
    assert accepted.status_code==200,accepted.json


def test_book_finishes_automatically_when_game_time_advances(client,workshop,auth_headers):
    from app.services.character_starting_purchase import item_from_template

    book=ItemTemplate(name='Книга? Раздел: Сила',category='misc',price=100,weight=0,volume=0,
        attributes={'book_kind':'skill','book_subject':'Физические',
                    'book_skill_path':'physical.strength','reading_seconds':60})
    db.session.add(book);db.session.flush()
    character=workshop['actor_character']
    data=deepcopy(character.data)
    data['inventory']['pockets'].append(item_from_template(book))
    character.data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',4],
                 skill_path='physical.strength')
    assert started.status_code==200,started.json
    advanced=client.patch(f"/lobbies/{workshop['lobby'].id}/time",
        headers=auth_headers(workshop['actor_user']),
        json={'game_day':1,'game_time_minutes':1})
    assert advanced.status_code==200,advanced.json
    state=character.data['crafting']
    assert state.get('current') is None
    assert len(state['library'])==1
    assert state['library'][0]['skill_path']=='physical.strength'
    snapshot=client.get(f'/lobbies/characters/{character.id}/crafting',
        headers=auth_headers(workshop['actor_user']))
    assert snapshot.status_code==200,snapshot.json
    assert len(snapshot.json['state']['library'])==1


def test_old_ready_book_is_recorded_when_files_are_opened(client,workshop,auth_headers):
    from app.services.character_starting_purchase import item_from_template

    book=ItemTemplate(name='Старая электронная книга',category='misc',price=100,weight=0,volume=0,
        attributes={'book_kind':'equipment','book_subject':'Пистолеты','reading_seconds':60})
    db.session.add(book);db.session.flush()
    character=workshop['actor_character']
    data=deepcopy(character.data)
    data['inventory']['pockets'].append(item_from_template(book))
    character.data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',4])
    assert started.status_code==200,started.json
    workshop['lobby'].game_time_minutes+=1
    db.session.commit()
    fetched=client.get(f'/lobbies/characters/{character.id}/crafting',
        headers=auth_headers(workshop['actor_user']))
    assert fetched.status_code==200,fetched.json
    assert fetched.json['state'].get('current') is None
    assert len(fetched.json['state']['library'])==1


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


def test_electronic_book_moves_into_pda_files_without_duplication(client,workshop,auth_headers):
    from app.services.character_starting_purchase import item_from_template
    book=ItemTemplate(name='Файл для КПК',category='misc',price=100,weight=0,volume=1,
        attributes={'book_kind':'equipment','book_subject':'Пистолеты','reading_seconds':60})
    db.session.add(book);db.session.flush()
    character=workshop['actor_character'];data=deepcopy(character.data)
    file_item=item_from_template(book)
    assert file_item['volume']==0
    data['inventory']['pockets'].append(file_item)
    character.data=data;db.session.commit()
    response=client.post(f'/lobbies/characters/{character.id}/portable-lights',
        headers=auth_headers(workshop['actor_user']),json={
            'operation':'pda_import_book','path':['inventory','pockets',2],
            'book_path':['inventory','pockets',4], 'character_revision':character.revision})
    assert response.status_code==200,response.json
    pockets=response.json['data']['inventory']['pockets']
    assert len(pockets)==4
    assert [entry['id'] for entry in pockets[2]['contents']]==[file_item['id']]
    assert pockets[2]['contents'][0]['volume']==0
    started=call(client,workshop,auth_headers,'read_book',path=['inventory','pockets',2,'contents',0])
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
