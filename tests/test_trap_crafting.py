"""Рецепты ловушек сохраняют конкретные гранаты, а не только общее название."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.trap_catalog import templates,PROFILES
from app.services.character_starting_purchase import item_from_template
from test_crafting import workshop,call
from test_grenade_rules import grenade_game
from test_quick_skill_checks import checks


FORBIDDEN_GRENADES = (
    'Подствольная граната Вог-25', 'Подствольная граната N-101-2',
    'Подствольный газовый', 'Подствольный дымовой', 'Подствольный светошумовой',
    'Граната ОГ-12', 'Коктейль Молотова', 'Кустарная бомба',
)


@pytest.mark.parametrize('name', FORBIDDEN_GRENADES)
@pytest.mark.parametrize('recipe_id', ['recipe-m8', 'recipe-m9'])
@pytest.mark.parametrize('explicit', [False, True])
def test_forbidden_grenades_cannot_be_reserved_for_tripwire(workshop, client, auth_headers, name, recipe_id, explicit):
    character = workshop['actor_character']
    data = deepcopy(character.data)
    data['skills']['other'].update(engineering={'base':20}, survival={'base':20})
    data['inventory']['pockets'] = [
        {'id':'forbidden', 'name':name, 'category':'grenade', 'quantity':2},
        {'name':'Электронные материалы', 'category':'crafting_material', 'quantity':2},
    ]
    # При явном выборе запрещённого предмета нельзя молча подменять его разрешённым.
    if explicit:
        data['inventory']['pockets'].append({'id':'allowed', 'name':'Граната Ф-1', 'category':'grenade', 'quantity':2})
    character.data = data
    db.session.add_all(ItemTemplate(**row) for row in templates())
    db.session.commit()
    before = deepcopy(character.data)
    revision = character.revision
    payload = {'grenade_paths':[['inventory','pockets',0]] * (2 if recipe_id == 'recipe-m9' else 1)} if explicit else {}
    response = call(client, workshop, auth_headers, 'start', recipe_id=recipe_id, **payload)
    assert response.status_code == 400, response.json
    db.session.refresh(character)
    assert character.data == before, 'Отказ не расходует материалы и не создаёт работу'
    assert character.revision == revision


def test_tripwire_choices_follow_server_rules_and_preserve_hand_grenades(workshop):
    from app.services.crafting import snapshot
    from app.services.trap_catalog import tripwire_grenade_allowed
    allowed = ['Граната РГД-5', 'Граната Ф-1', 'Химическая граната Черемуха', 'Светошумовая граната Flash-M2']
    character = workshop['actor_character']
    data = deepcopy(character.data)
    data['inventory']['pockets'] = [{'name':name, 'category':'grenade', 'quantity':1} for name in (*FORBIDDEN_GRENADES, *allowed)]
    character.data = data
    db.session.commit()
    choices = snapshot(character, workshop['actor_user']['id'])['inventory']
    assert [entry['item']['name'] for entry in choices if entry['tripwire_compatible']] == allowed
    # Настраиваемая дальность ручной гранаты не превращает её в подствольную.
    assert tripwire_grenade_allowed({'name':'Граната Ф-1', 'category':'grenade', 'attributes':{'range':100}})
    assert not tripwire_grenade_allowed({'name':'Граната Ф-1', 'category':'grenade', 'subcategory':'Подствольные гранаты'})


def test_renamed_launcher_grenade_is_still_forbidden(workshop):
    from app.services.trap_catalog import tripwire_grenade_allowed
    template = ItemTemplate(name='Граната ОГ-12', category='grenade', subcategory='Подствольные')
    db.session.add(template)
    db.session.flush()
    renamed = {'name':'Граната Ф-1', 'category':'grenade', 'templateId':template.id}
    assert not tripwire_grenade_allowed(renamed, workshop['lobby'].id)


@pytest.mark.parametrize('recipe_id', ['recipe-m8', 'recipe-m9'])
def test_old_forbidden_escrow_cannot_finish_as_new_tripwire(workshop, recipe_id):
    from app.services.exceptions import ConflictError
    from app.services.trap_catalog import bind_crafted_output
    output = {'name':'Растяжка'}
    with pytest.raises(ConflictError, match='не подходит'):
        bind_crafted_output(output, recipe_id, [{'component':0, 'amount':2 if recipe_id == 'recipe-m9' else 1,
            'item':{'name':'Кустарная бомба', 'category':'grenade'}}], workshop['lobby'].id)
    assert 'trapGrenades' not in output


def test_all_trap_recipe_outputs_resolve_without_confusing_artifact_snare(workshop):
    from app.services.crafting import _template,recipe_by_id
    from app.services.equipment_catalog import load_catalog
    db.session.add_all(ItemTemplate(**row) for row in templates());db.session.commit()
    for key in PROFILES:assert _template(recipe_by_id(key)['output_name']).attributes['trap_recipe']==key
    snare=next(row for row in load_catalog() if row['name']=='Силок')
    assert snare['subcategory']=='Генераторы артефактов' and 'trap_recipe' not in snare['attributes']


@pytest.mark.parametrize('key,names',[('recipe-m8',['РГД-5']),('recipe-m9',['РГД-5','Черемуха'])])
@pytest.mark.parametrize('constructor', [False, True])
def test_tripwire_output_preserves_each_selected_grenade(workshop,client,auth_headers,monkeypatch,key,names,constructor):
    from app.services.equipment_catalog import load_catalog
    from app.services.trap_catalog import preserve
    rows={row['name']:row for row in load_catalog() if row['category']=='grenade'}
    db.session.add_all(ItemTemplate(**row) for row in templates());db.session.flush()
    selected=[]
    for name in names:
        row=next(row for title,row in rows.items() if name.casefold() in title.casefold())
        template=ItemTemplate(**row);db.session.add(template);db.session.flush()
        item=item_from_template(template);item['customLabel']='Сохранить '+name;selected.append(item)
    data=deepcopy(workshop['actor_character'].data);data['skills']['other'].update(engineering={'base':20},survival={'base':20})
    data['inventory']['pockets']=[
        {'name':'Граната Ф-1','category':'grenade','quantity':len(names) if constructor else 1},
        *selected, {'name':'Электронные материалы','category':'crafting_material','quantity':4 if constructor else 2},
    ]
    if constructor:
        data.setdefault('basic', {})['background'] = {'name': 'Конструктор'}
    workshop['actor_character'].data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id=key,
        grenade_paths=[['inventory','pockets',i+1] for i in range(len(names))]);assert started.status_code==200,started.json
    assert sum(entry['amount'] for entry in started.json['result']['escrow'] if entry['component']==0) == len(names)*(2 if constructor else 1)
    workshop['lobby'].game_time_minutes+=60;db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote:{'success':True,'roll':100})
    finished=call(client,workshop,auth_headers,'finish',job_id=started.json['result']['id'],chosen_loss=[])
    assert finished.status_code==200,finished.json
    output=next(item for item in finished.json['data']['inventory']['pockets'] if item.get('attributes',{}).get('trap_recipe')==key)
    assert [g['customLabel'] for g in output['trapGrenades']]==[g['customLabel'] for g in selected]
    assert len({g['id'] for g in output['trapGrenades']})==len(selected)
    assert output['weight']==pytest.approx(sum(g['weight'] for g in selected))
    assert output['price']==PROFILES[key]['price']+sum(g['price'] for g in selected)
    before={'inventory':{'pockets':[output]}};forged=deepcopy(before);forged['inventory']['pockets'][0]['trapGrenades']=[]
    preserve(before,forged);assert forged==before
