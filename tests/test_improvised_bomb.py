"""Рецепт v20 и согласованные свойства хрупкой бомбы на настоящем боевом движке."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import pytest
from app.extensions import db
from app.models import LocationObject,LocationCombatState
from app.models.templates import ItemTemplate
from app.services.combat import CombatService
from app.services.improvised_explosives import templates,ground,fragile_ground,pending
from app.services.trap_catalog import radial_damage
from app.services.container_transfer import ContainerTransferService
from test_grenade_rules import grenade_game,throw
from test_movement_shooting_balance import arena,shoot
from test_crafting import workshop,call
from test_quick_skill_checks import checks


def equip_bomb(pair):
    character=pair['actor_character'];data=deepcopy(character.data)
    data['inventory']['pockets'][0].update(id='bomb-stack',name='Кустарная бомба')
    character.data=data;db.session.commit()


def test_improvised_bomb_uses_confirmed_noise_through_common_explosion(grenade_game,monkeypatch):
    calls=[]
    monkeypatch.setattr('app.services.noise.apply',lambda *args,**kwargs:calls.append(args[4]) or [])
    profile=CombatService._explosive_profile({'name':'Кустарная бомба'})
    result=CombatService.resolve_explosion(grenade_game['location'].id,11,11,profile)
    assert not result.get('awaiting_gm') and calls==[20]


@pytest.mark.parametrize('distance,fragment,penetration,blast',[(0,800,50,800),(1,760,45,700),
    (2,722,40,600),(3,685.9,35,500),(4,651.605,30,400)])
def test_radial_table_matches_creation_book(distance,fragment,penetration,blast):
    profile=CombatService._explosive_profile({'name':'Кустарная бомба'})
    assert radial_damage(profile,distance)=={'fragments':fragment,'penetration':penetration,'blast':blast}


def test_template_and_repeatable_migration_preserve_old_record(app,monkeypatch):
    row=ItemTemplate(**templates()[0]);db.session.add(row);db.session.flush();identity=row.id
    assert (row.weight,row.volume,row.price)==(.5,1,0) and row.attributes['not_for_sale']
    spec=importlib.util.spec_from_file_location('bomb_migration',Path('migrations/versions/f08c617ab493_add_improvised_bomb_template.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module.op,'get_bind',lambda:db.session.connection())
    module.upgrade();module.upgrade();db.session.expire_all()
    assert ItemTemplate.query.one().id==identity
    from app.services.crafting import _template
    assert _template('Кустарная бомба').id==identity


@pytest.mark.parametrize('contact,detonated',[(50,True),(51,False)])
def test_contact_probability_consumes_one_bomb_and_dud_stays_whole(grenade_game,monkeypatch,contact,detonated):
    pair=grenade_game;equip_bomb(pair);calls=[]
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:20 if high==20 else contact)
    monkeypatch.setattr(CombatService,'resolve_explosion',lambda *args,**kw:calls.append(args) or {'targets':[],'objects':[]})
    result=throw(pair)['explosive']
    assert result['contact_roll']==contact and result['detonated'] is detonated
    assert pair['actor_character'].data['inventory']['pockets'][0]['quantity']==1
    if detonated:assert len(calls)==1 and LocationObject.query.count()==0
    else:
        assert calls==[]
        obj=LocationObject.query.one();item=obj.properties['contents'][0]
        assert fragile_ground(obj) and item['quantity']==1 and item['id']!='bomb-stack'
        assert not CombatService._is_cover_object(obj) and not result.get('pending')
        assert 'осталась целой' in CombatService.format_explosion_summary({'explosive':result})
        pair['actor_location'].pos_x=obj.tile_x;pair['actor_location'].pos_y=obj.tile_y;db.session.commit()
        ContainerTransferService.transfer(pair['lobby'].id,obj.id,pair['actor_user']['id'],{'character_id':pair['actor_character'].id,
            'character_revision':pair['actor_character'].revision,'object_revision':obj.revision,
            'direction':'to_character','item_path':['contents',0],'expected_item':deepcopy(item),'amount':1})
        assert any(entry['id']==item['id'] for entry in pair['actor_character'].data['inventory']['backpack'])


def test_another_explosion_triggers_chain_once_and_only_on_same_floor(grenade_game,monkeypatch):
    pair=grenade_game;pair['actor_location'].pos_x=pair['actor_location'].pos_y=20
    first=ground(pair['location'].id,4,4,0,{'name':'Кустарная бомба','quantity':1})
    second=ground(pair['location'].id,8,4,0,{'name':'Кустарная бомба','quantity':1})
    upstairs=ground(pair['location'].id,4,4,1,{'name':'Кустарная бомба','quantity':1});db.session.commit()
    profile=CombatService._explosive_profile({'name':'Граната РГД-5'})
    result=CombatService.resolve_explosion(pair['location'].id,2,4,profile)
    nodes=pending(result);assert len(nodes)==1 and nodes[0]['detonated'],[(node['ground_object_id'],node['floor_level']) for node in nodes]
    assert len(pending(nodes[0]['explosion']))==1
    db.session.commit()
    assert LocationObject.query.one().id==upstairs.id
    assert not pending(CombatService.resolve_explosion(pair['location'].id,2,4,profile))


def test_shot_breaks_body_and_does_not_erase_damage_to_shooter(arena,monkeypatch):
    user,location,actor,target=arena
    obj=ground(location.id,2,1,0,{'name':'Кустарная бомба','quantity':1});db.session.commit();identity=obj.id
    monkeypatch.setattr('app.services.combat.random.randint',lambda *args:1)
    result=shoot(arena,fire_mode='unaimed',target_object_id=obj.id,action_points=2)
    assert result['attack']['results'][0]['cover_damage']['fragile_detonated']
    assert len(result['secondary_explosions'])==1 and result['secondary_explosions'][0]['detonated']
    assert result['secondary_explosions'][0]['profile']['source_character_id']==actor.character_id
    assert db.session.get(LocationObject,identity) is None
    if actor.character.data['weapons']:assert actor.character.data['weapons'][0]['ammo']==19
    else:
        remains=LocationObject.query.all()
        assert remains and all(obj.id!=identity for obj in remains)
        assert any('"ammo": 19' in json.dumps(obj.properties) for obj in remains),[(obj.type,obj.name,list(obj.properties)) for obj in remains]
    assert actor.character.data['health']['current']<700


def test_recipe_v20_produces_real_grenade_template(client,workshop,auth_headers,monkeypatch):
    from app.services.crafting_rules import resolve_creation
    pair=workshop;character=pair['actor_character'];actor=pair['actor_location']
    data=deepcopy(character.data);data['inventory']['pockets']=[{'name':name,'quantity':count,'category':'crafting_material'}
        for name,count in [('Металлические детали',5),('Химические материалы',2),('Вода',1)]]
    character.data=data;db.session.add(ItemTemplate(**templates()[0]))
    db.session.add(LocationObject(location_id=pair['location'].id,type='household_workbench',name='Смесительный Станок',
        tile_x=actor.pos_x,tile_y=actor.pos_y,properties={'craft_workbench':'Смесительный Станок'}));db.session.commit()
    monkeypatch.setattr('app.services.crafting.resolve_creation',lambda quote,**kwargs:resolve_creation(quote,roll=100))
    response=call(client,pair,auth_headers,'start',recipe_id='recipe-v20');assert response.status_code==200,response.get_json()
    pair['lobby'].game_time_minutes+=30;db.session.commit()
    job=response.get_json()['result']
    response=call(client,pair,auth_headers,'finish',job_id=job['id'],chosen_loss=[0]*job['quote']['failure_loss']['chosen'])
    assert response.status_code==200 and response.get_json()['result']['success'],response.get_json()
    from app.services.portable_lights import walk
    item=next(item for _,item in walk(character.data,True) if item.get('name')=='Кустарная бомба')
    assert item['category']=='grenade' and item['quantity']==1 and CombatService._explosive_profile(item)['radius']==4


def test_old_ground_item_uses_saved_floor_without_making_it_cover():
    from app.services.location_navigation import level_of
    assert level_of({'type':'ground_item','properties':{'floor_level':2}})==2
    assert level_of({'type':'ground_item','properties':{'floor_level':2,'level':1}})==1
    assert level_of({'type':'box','properties':{'floor_level':2}})==0


def prepare_gm_chain(pair):
    pair['actor_location'].pos_x=pair['actor_location'].pos_y=20
    pair['target_location'].pos_x=6;pair['target_location'].pos_y=4
    state=LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    state.turn_order=[pair['actor_location'].id]
    ground(pair['location'].id,4,4,0,{'name':'Кустарная бомба','quantity':1});db.session.commit()
    return state,CombatService._explosive_profile({'name':'Граната РГД-5'})


def test_gm_decision_keeps_new_choice_for_secondary_bomb(grenade_game):
    from app.services.explosion_choice import decide
    pair=grenade_game;state,profile=prepare_gm_chain(pair)
    assert CombatService.resolve_explosion(pair['location'].id,2,4,profile)['awaiting_gm'];db.session.commit()
    original=state.pending_explosives[0]['id']
    decide(pair['location'].id,pair['actor_user']['id'],original,pair['target_location'].id,'exclude')
    assert len(state.pending_explosives)==1
    child=state.pending_explosives[0]
    assert child['profile']['key']=='improvised_bomb' and child['gm_choices']==[pair['target_location'].id]
    decide(pair['location'].id,pair['actor_user']['id'],child['id'],pair['target_location'].id,'exclude')
    assert state.pending_explosives==[]


def test_short_fuse_finish_keeps_new_choice_for_secondary_bomb(grenade_game):
    from app.services.grenade_reaction import _finish_events
    pair=grenade_game;state,profile=prepare_gm_chain(pair)
    event={'id':'short-fuse','trigger':'short_fuse','item_name':profile['name'],'x':2,'y':4,
        'profile':profile,'floor_level':0,'remaining_actor_ids':[],'gm_choices':[]}
    state.pending_explosives=[event];db.session.commit()
    _finish_events(state,[event]);db.session.commit()
    assert len(state.pending_explosives)==1 and state.pending_explosives[0]['profile']['key']=='improvised_bomb'


def test_fully_protected_ground_bomb_does_not_react_through_solid_wall(grenade_game):
    pair=grenade_game;pair['actor_location'].pos_x=pair['actor_location'].pos_y=20
    obj=ground(pair['location'].id,4,4,0,{'name':'Кустарная бомба','quantity':1})
    db.session.add(LocationObject(location_id=pair['location'].id,type='wall',name='Стена',tile_x=3,tile_y=4,
        properties={'width':.5,'depth':4,'height':3,'cover_max_hp':10000000,'cover_hp':10000000,
            'cover_base_physical_protection':100,'cover_physical_protection':100}));db.session.commit()
    result=CombatService.resolve_explosion(pair['location'].id,2,4,CombatService._explosive_profile({'name':'Граната РГД-5'}));db.session.commit()
    assert not pending(result) and fragile_ground(db.session.get(LocationObject,obj.id))
