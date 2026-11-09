"""Создание, размещение и использование устройств в отдельной БД тестов."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from app.extensions import db
from app.models import LocationObject,LocationCombatState
from app.models.templates import ItemTemplate
from app.services.portable_stations import operate,templates,PROFILES
from app.services.character_starting_purchase import item_from_template
from app.services.exceptions import ValidationError,ConflictError,PermissionDenied
from test_grenade_rules import grenade_game


@pytest.fixture
def station_game(grenade_game):
    pair=grenade_game;actor=pair['actor_location'];actor.pos_x=actor.pos_y=4
    LocationCombatState.query.filter_by(location_id=actor.location_id).one().status='idle'
    catalogue={row['attributes']['portable_station']:ItemTemplate(**row) for row in templates()}
    db.session.add_all(catalogue.values());db.session.flush()
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[item_from_template(catalogue['recipe-m45'])]
    data['inventory']['pockets'][0]['customState']={'left':7}
    actor.character.data=data;db.session.commit();pair['stations']=catalogue
    return pair


def payload(pair,operation='place',**changes):
    character=pair['actor_character'];item=character.data['inventory']['pockets'][0]
    return {'operation':operation,'operation_id':'station-once','character_id':character.id,
        'character_revision':character.revision,'item_path':['inventory','pockets',0],
        'item_id':item['id'],'x':5.2,'y':4,'rotation':0,**changes}


def place(pair):
    request=payload(pair)
    result=operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],request)
    return request,result[1]


def test_all_seven_station_templates_use_approved_physical_values():
    assert len(templates())==7
    for item in templates():
        assert item['weight']==item['volume']==item['price']==0
        assert item['attributes']['not_for_sale'] and item['attributes']['craft_only']
        assert 'uses' not in item['attributes']


def test_all_station_recipe_outputs_resolve_and_migration_is_repeatable(app,monkeypatch):
    import importlib.util
    from pathlib import Path
    from app.services.crafting import _template,recipe_by_id
    path=Path(__file__).resolve().parents[1]/'migrations/versions/c3f05e8d9164_add_portable_station_templates.py'
    spec=importlib.util.spec_from_file_location('portable_station_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    row=ItemTemplate(**templates()[0]);db.session.add(row);db.session.flush();identity=row.id
    monkeypatch.setattr(migration.op,'get_bind',lambda:db.session.connection())
    migration.upgrade();migration.upgrade();db.session.expire_all()
    assert row.id==identity and ItemTemplate.query.count()==7
    for key in PROFILES:
        output=_template(recipe_by_id(key)['output_name'])
        assert output and output.attributes['portable_station']==key


def test_place_replay_and_move_by_another_adjacent_character_preserve_instance(station_game):
    pair=station_game;request,obj=place(pair);identity=obj.properties['station_item']['id']
    assert pair['actor_character'].data['inventory']['pockets']==[]
    assert obj.properties['station_item']['customState']=={'left':7}
    assert operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],request)[1].id==obj.id
    assert LocationObject.query.filter_by(location_id=obj.location_id).count()==1
    with pytest.raises(ConflictError):operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],{**request,'x':5.1})
    other=pair['target_location'];other.pos_x,other.pos_y=6.5,4;db.session.commit()
    operate(pair['lobby'].id,pair['location'].id,pair['target_user']['id'],{
        'operation':'move','operation_id':'move-once','character_id':other.character_id,
        'character_revision':other.character.revision,'object_id':obj.id,'object_revision':obj.revision,
        'x':6.5,'y':5.2,'rotation':1.2})
    assert obj.properties['station_item']['id']==identity
    assert obj.properties['station_item']['customState']=={'left':7}
    assert obj.properties['rotation']==1.2


def test_place_legacy_distillery_keeps_its_instance(station_game):
    pair=station_game;character=pair['actor_character']
    data=deepcopy(character.data)
    data['inventory']['pockets']=[{'id':'old-distillery','name':'Самогонный Аппарат','category':'tool','quantity':1}]
    character.data=data;db.session.commit()
    _,obj=place(pair)
    assert obj.properties['standard_distillery'] is True
    assert obj.properties['station_item']['id']=='old-distillery'
    assert character.data['inventory']['pockets']==[]


@pytest.mark.parametrize('changes',[{'x':10},{'x':4,'y':4},{'rotation':'0'},{'floor_level':1}])
def test_invalid_placement_keeps_item_and_creates_no_object(station_game,changes):
    pair=station_game;before=deepcopy(pair['actor_character'].data)
    with pytest.raises(ValidationError):operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],payload(pair,**changes))
    assert pair['actor_character'].data==before
    assert LocationObject.query.filter_by(location_id=pair['location'].id).count()==0


def test_station_route_rejects_wrong_owner_and_legacy_patch(station_game,client,auth_headers):
    pair=station_game;request=payload(pair)
    url=f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/stations"
    response=client.post(url,headers=auth_headers(pair['target_user']),json=request)
    assert response.status_code==403
    response=client.post(url,headers=auth_headers(pair['actor_user']),json=request)
    assert response.status_code==200,response.json
    obj=response.json['object'];other=pair['target_location'];other.pos_x,other.pos_y=6.5,4;db.session.commit()
    denied=client.patch(f"/lobbies/{pair['lobby'].id}/locations/objects/{obj['id']}",
        headers=auth_headers(pair['target_user']),json={'tile_x':0,'properties':{'station_tools':[]}})
    assert denied.status_code==403


def load_tool(pair,obj):
    character=pair['actor_character'];data=deepcopy(character.data)
    tool={'id':'solder-tool','name':'Паяльные инструменты 50/50','category':'tool','quantity':1,'uses':3,'maxUses':50}
    data['inventory']['pockets'].append(tool);character.data=data;db.session.commit()
    operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],{
        'operation':'load','operation_id':'load-once','character_id':character.id,
        'character_revision':character.revision,'object_id':obj.id,'object_revision':obj.revision,
        'item_path':['inventory','pockets',0],'item_id':tool['id']})


def test_load_and_unload_preserve_tool_identity_and_charges(station_game):
    pair=station_game;_,obj=place(pair);load_tool(pair,obj)
    assert pair['actor_character'].data['inventory']['pockets']==[]
    assert obj.properties['station_tools'][0]['uses']==3
    character=pair['actor_character']
    operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],{
        'operation':'unload','operation_id':'unload-once','character_id':character.id,
        'character_revision':character.revision,'object_id':obj.id,'object_revision':obj.revision,'item_id':'solder-tool'})
    assert not obj.properties['station_tools']
    assert character.data['inventory']['pockets'][0]['id']=='solder-tool'
    assert character.data['inventory']['pockets'][0]['uses']==3


def test_high_pda_modification_uses_one_loaded_station_charge(station_game,client,auth_headers,monkeypatch):
    pair=station_game;_,obj=place(pair);load_tool(pair,obj);character=pair['actor_character']
    data=deepcopy(character.data);data['features']={'specialTraits':[{'name':'Программист'}]}
    data['skills']['other']['engineering']={'base':20}
    data['inventory']['pockets']=[{'id':'modified-pda','name':'КПК','category':'misc','quantity':1},
        {'name':'Плата','category':'crafting_material','quantity':1},
        {'name':'Электронные материалы','category':'crafting_material','quantity':1},
        {'id':'unused-tool','name':'Паяльные инструменты','category':'tool','quantity':1,'uses':3}]
    character.data=data;db.session.commit()
    def call(operation,**changes):return client.post(f'/lobbies/characters/{character.id}/crafting',headers=auth_headers(pair['actor_user']),json={
        'operation':operation,'operation_id':operation+'-pda-station','character_revision':character.revision,
        'target_path':['inventory','pockets',0],'item_id':'modified-pda','rule_id':'pda-b15','tool_path':['inventory','pockets',3],**changes})
    quote=call('quote_pda_modification');assert quote.status_code==200 and not quote.json['result']['missing'],quote.json
    assert quote.json['result']['station_id']==obj.id
    started=call('install_pda_modification');assert started.status_code==200,started.json
    assert obj.properties['station_tools'][0]['uses']==2
    assert next(i for i in character.data['inventory']['pockets'] if i.get('id')=='unused-tool')['uses']==3
    pair['lobby'].game_time_minutes+=60;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100})
    monkeypatch.setattr('app.services.crafting.random.SystemRandom',lambda:SimpleNamespace(randint=lambda *args:100))
    finished=call('finish',job_id=started.json['result']['id'],chosen_loss=[]);assert finished.status_code==200,finished.json
    assert character.data['inventory']['pockets'][0]['modifications'][0]['ruleId']=='pda-b15'


def test_weapon_modification_accepts_deployed_station_and_spends_one_charge(station_game,client,auth_headers,monkeypatch):
    pair=station_game;_,obj=place(pair);character=pair['actor_character']
    obj.name='Кустарный станок оружейника';obj.properties={**obj.properties,'portable_station':'recipe-m42',
        'station_tools':[{'id':'gunsmith-tool','name':'Набор инструментов Оружейника (Стандартные)',
            'category':'tool','quantity':1,'uses':3,'maxUses':25}]}
    data=deepcopy(character.data);data['skills']['other']['engineering']={'base':20}
    data['weapons']=[{'id':'station-gun','name':'Пистолет','category':'weapon','subcategory':'Пистолеты','itemClass':'1',
        'durability':100,'maxDurability':100,'modifications':[]}]
    data['crafting']={'library':[{'book_kind':'equipment','book_subject':'Пистолеты','name':'Книга'}]}
    character.data=data;db.session.commit()
    def call(operation,**changes):return client.post(f'/lobbies/characters/{character.id}/crafting',headers=auth_headers(pair['actor_user']),json={
        'operation':operation,'operation_id':operation+'-weapon-station','character_revision':character.revision,
        'target_path':['weapons',0],'rule_id':'weapon-c23','station_id':obj.id,**changes})
    quoted=call('quote_modification');assert quoted.status_code==200,quoted.json
    assert quoted.json['result']['tool_uses']==1 and quoted.json['result']['duration_seconds']==900
    started=call('modify');assert started.status_code==200,started.json
    assert obj.properties['station_tools'][0]['uses']==2
    pair['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100})
    monkeypatch.setattr('app.services.crafting.random.SystemRandom',lambda:SimpleNamespace(randint=lambda *args:100))
    finished=call('finish',job_id=started.json['result']['id'],chosen_loss=[]);assert finished.status_code==200,finished.json
    assert character.data['weapons'][0]['modifications'][0]['ruleId']=='weapon-c23'
    obj.tile_x=20;db.session.commit()
    assert call('quote_modification',rule_id='weapon-b23',replace_rule_id='weapon-c23').status_code==400


def test_empty_station_does_not_unlock_recipes_but_oven_needs_no_tool(station_game):
    from app.services.crafting import quote,recipe_by_id,_tools
    pair=station_game;_,obj=place(pair);character=pair['actor_character']
    data=deepcopy(character.data);data['skills']['other']['engineering']={'base':20}
    character.data=data;db.session.commit()
    assert any('Станок Электрика' in value for value in quote(character,data,{'recipe_id':'recipe-f5'})['missing'])
    load_tool(pair,obj)
    assert not any('Станок Электрика' in value for value in quote(character,character.data,{'recipe_id':'recipe-f5'})['missing'])
    obj.properties={**obj.properties,'portable_station':'recipe-m41','craft_workbench':'Печь','station_tools':[]}
    assert not _tools(character,character.data,recipe_by_id('recipe-m31'),0)


@pytest.mark.parametrize('key,name',[
    ('recipe-m40','Паяльные инструменты 50/50'),
    ('recipe-m42','Набор инструментов Оружейника (Упрощёные) 15/15'),
    ('recipe-m43','Набор инструментов Бронника (Стандартные) 20/20'),
    ('recipe-m44','Смесительные инструменты 30/30'),
    ('recipe-m45','Паяльные инструменты 50/50'),
])
def test_each_station_accepts_approved_tool_and_becomes_unavailable_after_last_charge(station_game,key,name):
    from app.services.portable_stations import consume_for_job,ready
    pair=station_game;_,obj=place(pair);character=pair['actor_character']
    obj.properties={**obj.properties,'portable_station':key};data=deepcopy(character.data)
    data['inventory']['pockets']=[{'id':'approved-tool','name':name,'category':'tool','quantity':1,'uses':1}]
    character.data=data;db.session.commit()
    operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],{
        'operation':'load','operation_id':'approved-tool-load','character_id':character.id,
        'character_revision':character.revision,'object_id':obj.id,'object_revision':obj.revision,
        'item_path':['inventory','pockets',0],'item_id':'approved-tool'})
    assert ready(obj)
    consume_for_job(obj);db.session.commit()
    assert obj.properties['station_tools'][0]['uses']==0 and not ready(obj)
    with pytest.raises(ConflictError):consume_for_job(obj)


@pytest.mark.parametrize('operation',['cancel','finish'])
def test_aeration_accepts_mixed_dirty_water_and_preserves_filter_remainders(station_game,client,auth_headers,operation):
    from app.services.equipment_catalog import load_catalog
    pair=station_game;character=pair['actor_character']
    names={'Болотная вода','Дождевая вода','Радиоактивная вода','Вода','Фильтр для противогаза'}
    catalogue={row['name']:ItemTemplate(**row) for row in load_catalog() if row['name'] in names}
    db.session.add_all(catalogue.values());db.session.flush()
    data=deepcopy(character.data);data['inventory']['pockets']=[item_from_template(pair['stations']['recipe-m34'])]
    data['skills']['other']['survival']={'base':20};character.data=data;db.session.commit()
    _,obj=place(pair)
    data=deepcopy(character.data)
    water=[item_from_template(catalogue[name],amount) for name,amount in [('Болотная вода',1),('Дождевая вода',1),('Радиоактивная вода',2)]]
    filters=[]
    for amount in (6,7):
        item=item_from_template(catalogue['Фильтр для противогаза']);item['durability']=amount
        item['attributes']['durability']=amount;filters.append(item)
    data['inventory']['pockets']=[*water,*filters];character.data=data;db.session.commit()
    def call(action,**changes):return client.post(f'/lobbies/characters/{character.id}/crafting',
        headers=auth_headers(pair['actor_user']),json={'operation':action,'operation_id':action+'-aeration',
            'character_revision':character.revision,**changes})
    started=call('start',recipe_id='recipe-aa12');assert started.status_code==200,started.json
    assert started.json['result']['station_usage']['spoil_chance']==0
    pair['lobby'].game_time_minutes+=30;db.session.commit()
    result=call(operation,job_id=started.json['result']['id'],chosen_loss=[])
    assert result.status_code==200,result.json
    from app.services.general_consumable import _available_uses
    from app.services.gas_mask_filters import filter_charges
    inventory=result.json['data']['inventory']['pockets']
    assert sum(_available_uses(i) for i in inventory if i['name']=='Вода')==(8 if operation=='finish' else 0)
    assert sum(_available_uses(i) for i in inventory if i['name'] in names-{'Вода','Фильтр для противогаза'})==(2 if operation=='finish' else 12)
    assert sum(filter_charges(i) for i in inventory if i['name']=='Фильтр для противогаза')==(3 if operation=='finish' else 13)


@pytest.mark.parametrize('roll,success',[(15,False),(16,True)])
def test_crafting_consumes_one_station_charge_and_checks_spoil_once(station_game,client,auth_headers,monkeypatch,roll,success):
    from app.services.equipment_catalog import load_catalog
    from app.services.crafting import recipe_by_id
    pair=station_game;_,obj=place(pair);load_tool(pair,obj);character=pair['actor_character']
    output=next(row for row in load_catalog() if row['name']=='Улучшенный фонарик')
    db.session.add(ItemTemplate(**output))
    data=deepcopy(character.data);data['skills']['other']['engineering']={'base':20}
    data['inventory']['pockets']=[{'name':entry['name'],'quantity':entry['quantity'],'category':'crafting_material'}
        for entry in recipe_by_id('recipe-f5')['components']]
    character.data=data;db.session.commit()
    url=f'/lobbies/characters/{character.id}/crafting'
    def call(operation,**changes):return client.post(url,headers=auth_headers(pair['actor_user']),json={
        'operation':operation,'operation_id':operation+'-station','character_revision':character.revision,**changes})
    quoted=call('quote',recipe_id='recipe-f5');assert quoted.status_code==200,quoted.json
    assert quoted.json['result']['spoil_chance']==15
    started=call('start',recipe_id='recipe-f5');assert started.status_code==200,started.json
    assert obj.properties['station_tools'][0]['uses']==2
    pair['lobby'].game_time_minutes+=30;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100})
    monkeypatch.setattr('app.services.crafting.random.SystemRandom',lambda:SimpleNamespace(randint=lambda *args:roll))
    finished=call('finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['success'] is success
    assert finished.json['result']['spoiled_by_station'] is not success
    repeated=call('finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert repeated.status_code==200 and repeated.json['result']['replayed']
    assert obj.properties['station_tools'][0]['uses']==2
