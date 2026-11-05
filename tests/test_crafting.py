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
