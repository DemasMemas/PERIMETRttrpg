from copy import deepcopy

import pytest

from app.services.crafting import quote
from app.services.crafting_rules import catalog
from app.extensions import db
from app.models import LocationObject
from test_crafting import workshop, call, catalog_template, ammo_workshop
from test_quick_skill_checks import checks


@pytest.mark.parametrize('background', [{'name':'Фармацевт'}, {'ruleId':'background-an'}])
def test_pharmacist_applies_to_all_medical_recipes(workshop,background):
    character=workshop['actor_character']
    data=deepcopy(character.data)
    data['skills']['other']['medicine']={'base':13,'bonus':0}
    recipe_ids=[recipe['id'] for recipe in catalog()['recipes'] if recipe['skill']=='other.medicine']
    assert len(recipe_ids)==28
    for recipe_id in recipe_ids:
        base=quote(character,data,{'recipe_id':recipe_id})
        trained=deepcopy(data)
        trained.setdefault('basic',{})['background']=background
        result=quote(character,trained,{'recipe_id':recipe_id})
        assert result['difficulty']==base['difficulty']-40,recipe_id
        assert result['original_difficulty']==base['original_difficulty'],recipe_id
        assert result['missing']==base['missing'],recipe_id
        assert result['allocations']==base['allocations'],recipe_id


def test_pharmacist_does_not_change_electronics_recipe(workshop):
    character=workshop['actor_character']
    data=deepcopy(character.data)
    recipe_id=next(recipe['id'] for recipe in catalog()['recipes'] if recipe['group']=='Электроника')
    base=quote(character,data,{'recipe_id':recipe_id})
    data.setdefault('basic',{})['background']={'ruleId':'background-an'}
    trained=quote(character,data,{'recipe_id':recipe_id})
    assert trained['difficulty']==base['difficulty']


def test_pharmacist_bonus_is_used_by_real_creation_job(client,workshop,auth_headers,monkeypatch):
    from app.services.crafting import now

    character=workshop['actor_character']
    data=deepcopy(character.data)
    data.setdefault('basic',{})['background']={'ruleId':'background-an'}
    data['skills']['other']['medicine']={'base':5,'bonus':0}
    data['inventory']['pockets']=[
        {'name':'Химические материалы','category':'crafting_material','quantity':2},
        {'name':'Сахар','category':'consumable','quantity':1},
    ]
    character.data=data
    output=catalog_template('Анальгин (Таблетка)')
    actor=workshop['actor_location']
    db.session.add(LocationObject(location_id=workshop['location'].id,type='household_workbench',
        name='Смесительный станок',tile_x=actor.pos_x,tile_y=actor.pos_y,
        properties={'craft_workbench':'Смесительный станок'}))
    db.session.commit()
    planned=call(client,workshop,auth_headers,'quote',recipe_id='recipe-aa21')
    assert planned.status_code==200,planned.json
    assert planned.json['result']['difficulty']==25
    assert not planned.json['result']['missing']
    started=call(client,workshop,auth_headers,'start',recipe_id='recipe-aa21')
    assert started.status_code==200,started.json
    job=started.json['result']
    workshop['lobby'].game_time_minutes+=(job['ready_at']-now(character))//60
    db.session.commit()
    # 40 проходит СЛ25 Фармацевта, но не исходную СЛ65 персонажа с Медициной5.
    from app.services.crafting_rules import resolve_creation
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:resolve_creation(quote,roll=40))
    finished=call(client,workshop,auth_headers,'finish',job_id=job['id'])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['success'] and finished.json['result']['roll']==40
    made=[item for item in finished.json['data']['inventory']['pockets'] if item.get('templateId')==output.id]
    assert sum(item['quantity'] for item in made)==35
    repeated=call(client,workshop,auth_headers,'finish',job_id=job['id'],character_revision=0)
    assert repeated.status_code==200 and repeated.json['result']['replayed']
    assert repeated.json['data']['inventory']==finished.json['data']['inventory']


@pytest.mark.parametrize('recipe_id,variant', [
    ('recipe-v26','base'),('recipe-v26','bp'),('recipe-v26','ep'),
    ('recipe-v23','handmade'),('recipe-v29','improved'),
])
def test_ammo_trait_applies_only_to_chosen_caliber(workshop,recipe_id,variant):
    template=ammo_workshop(workshop)
    character=workshop['actor_character']
    data=deepcopy(character.data)
    payload={'recipe_id':recipe_id,'ammo_template_id':template.id,'ammo_variant':variant}
    base=quote(character,data,payload)
    data['features']={'specialTraits':[
        {'name':'Патронщик','choices':{'caliber':'5,45 × 39'}},
        {'ruleId':'trait-d22','choices':{'caliber':'5.45x39'}},
        {'name':'Патронщик','choices':{'caliber':'9x19'}},
        {'name':'Патронщик','choices':{'caliber':'5.45x39'},'active':False},
    ]}
    trained=quote(character,data,payload)
    assert trained['difficulty']==base['difficulty']-30
    assert trained['recipe']['output_count']==base['recipe']['output_count']
    assert trained['allocations']==base['allocations']
    assert trained['missing']==base['missing']


def test_truant_doubles_base_difficulty_before_skill_reduction(workshop):
    ammo = ammo_workshop(workshop)
    character = workshop['actor_character']
    data = deepcopy(character.data)
    recipe = next(entry for entry in catalog()['recipes'] if entry['id'] == 'recipe-v26')
    group, skill = recipe['skill'].split('.')
    data['skills'][group][skill] = {'base': 20, 'bonus': 0}
    payload = {'recipe_id': recipe['id'], 'ammo_template_id': ammo.id}
    assert quote(character, data, payload)['difficulty'] == 35
    data.setdefault('basic', {})['background'] = {'ruleId': 'background-m'}
    result = quote(character, data, payload)
    assert result['original_difficulty'] == 130
    assert result['difficulty'] == 100


def test_precise_eye_increases_ammo_and_medicine_by_twenty_percent_rounding_down(workshop):
    ammo = ammo_workshop(workshop)
    character = workshop['actor_character']
    data = deepcopy(character.data)
    data['skills']['other'].update(medicine={'base': 10}, survival={'base': 10})
    for payload in [{'recipe_id': 'recipe-v26', 'ammo_template_id': ammo.id},
                    {'recipe_id': 'recipe-aa21'}, {'recipe_id': 'recipe-aa15'}]:
        base = quote(character, data, payload)
        trained = deepcopy(data)
        trained['features'] = {'specialTraits': [{'ruleId': 'trait-d14'}, {'name': 'Точный глаз', 'active': False}]}
        result = quote(character, trained, payload)
        count = base['recipe']['output_count']
        assert result['recipe']['output_count'] == (count * 6 // 5 if payload['recipe_id'] != 'recipe-aa15' else count)
        assert result['allocations'] == base['allocations']
