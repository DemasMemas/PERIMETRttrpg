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


def test_all_trap_recipe_outputs_resolve_without_confusing_artifact_snare(workshop):
    from app.services.crafting import _template,recipe_by_id
    from app.services.equipment_catalog import load_catalog
    db.session.add_all(ItemTemplate(**row) for row in templates());db.session.commit()
    for key in PROFILES:assert _template(recipe_by_id(key)['output_name']).attributes['trap_recipe']==key
    snare=next(row for row in load_catalog() if row['name']=='Силок')
    assert snare['subcategory']=='Генераторы артефактов' and 'trap_recipe' not in snare['attributes']


@pytest.mark.parametrize('key,names',[('recipe-m8',['РГД-5']),('recipe-m9',['РГД-5','Черемуха'])])
def test_tripwire_output_preserves_each_selected_grenade(workshop,client,auth_headers,monkeypatch,key,names):
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
    data['inventory']['pockets']=[{'name':'Граната Ф-1','category':'grenade','quantity':1},*selected,{'name':'Электронные материалы','category':'crafting_material','quantity':2}]
    workshop['actor_character'].data=data;db.session.commit()
    started=call(client,workshop,auth_headers,'start',recipe_id=key,
        grenade_paths=[['inventory','pockets',i+1] for i in range(len(names))]);assert started.status_code==200,started.json
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
