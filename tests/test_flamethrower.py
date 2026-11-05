"""Настоящие боевые запросы огнемётов, ресурсы и существующее горение."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState,LocationObject
from app.models.templates import ItemTemplate
from app.services.combat import CombatService as C
from app.services.flamethrower import templates,prepare,resources,charges,cells,scatter_cell
from app.services.burning import ignite_objects,advance_object_fires
from app.services.character_starting_purchase import item_from_template
from app.services.exceptions import ValidationError
from test_grenade_rules import grenade_game


@pytest.fixture
def flame_game(grenade_game,monkeypatch):
    case=grenade_game;models=[ItemTemplate(**t) for t in templates()]
    db.session.add_all(models);db.session.flush()
    case['flame_templates']={t.name:t for t in models}
    actor=case['actor_location'];data=deepcopy(actor.character.data)
    def item(name):return item_from_template(case['flame_templates'][name])
    case['item']=item
    data['skills']['physical'].update(strength={'base':20},shooting={'base':20})
    data['equipment']={};data['weapons']=[item('Струйный огнемёт')]
    data['equipment']['backpack']=item('Бак для струйного огнемета (Бензин)')
    data['weapons'][0]['installedModules']=[{**item('Бак сжатого воздуха'),'slotType':'flamethrower_air'}]
    data['activeWeaponIndex']=0;actor.drawn_weapon_index=0;actor.character.data=data
    target=case['target_location'];target.pos_x,target.pos_y=7,5;target.floor_level=actor.floor_level=0
    target_data=deepcopy(target.character.data);target_data['health']={'current':700,'max':700,'painLevel':0,'stress':0,'effects':[]}
    target_data['equipment']={};target.character.data=target_data
    case['lobby'].game_time_minutes=720;case['lobby'].weather_settings={}
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:high)
    return case


def shoot(client,case,auth_headers,**extra):
    return client.post(f'/lobbies/{case["lobby"].id}/locations/{case["location"].id}/combat/action',headers=auth_headers(case['actor_user']),
        json={'location_character_id':case['actor_location'].id,'action_key':'flamethrower_fire','weapon_index':0,'fire_mode':'short','target_x':6,'target_y':5,**extra})


@pytest.mark.parametrize('mode,cost,spent',[('short',3,1),('long',5,4)])
def test_jet_http_spends_both_resources_and_creates_fire(client,flame_game,auth_headers,mode,cost,spent):
    actor=flame_game['actor_location'];before=actor.action_points_current
    response=shoot(client,flame_game,auth_headers,fire_mode=mode)
    assert response.status_code==200,response.json
    fuel,air=resources(actor.character.data,actor.character.data['weapons'][0])
    assert charges(fuel)==8-spent and charges(air)==16-spent
    assert actor.action_points_current==before-cost
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    assert state.area_effects[-1]['shape']=='flame_rectangle'
    effects=flame_game['target_character'].data['health']['effects']
    fire=next(e for e in effects if e['type']=='burning')
    assert fire['remaining']==4 and fire['damage_per_round']==50 and fire['thermal_threshold']==40
    assert not any(e['type']=='blindness' for e in effects)


def reactive(case):
    data=deepcopy(case['actor_character'].data);weapon=case['item']('Реактивный огнемёт')
    weapon['installedModules']=[{**case['item']('Бак сжатого воздуха'),'slotType':'flamethrower_air'},
        {**case['item']('Баллон для реактивного огнемета (Напалм)'),'slotType':'flamethrower_fuel'}]
    data['weapons']=[weapon];data['equipment']={};case['actor_character'].data=data;db.session.commit()


@pytest.mark.parametrize('mode,cost,spent',[('short',3,1),('long',4,4)])
def test_reactive_burns_target_without_ground_area(client,flame_game,auth_headers,mode,cost,spent):
    reactive(flame_game);actor=flame_game['actor_location'];before=actor.action_points_current
    response=shoot(client,flame_game,auth_headers,fire_mode=mode,target_character_id=flame_game['target_character'].id)
    assert response.status_code==200,response.json
    assert actor.action_points_current==before-cost
    assert not LocationCombatState.query.filter_by(location_id=actor.location_id).one().area_effects
    fuel,air=resources(actor.character.data,actor.character.data['weapons'][0]);assert charges(fuel)==charges(air)==16-spent
    fire=next(e for e in flame_game['target_character'].data['health']['effects'] if e['type']=='burning')
    assert fire['remaining']==7 and fire['extinguish_difficulty_modifier']==3


def test_reactive_miss_uses_random_cell_and_can_hit_its_occupant(client,flame_game,auth_headers,monkeypatch):
    reactive(flame_game)
    monkeypatch.setattr(C,'_narrative_skill_check',lambda *args,**kwargs:{'roll':2,'total':9})
    def choose(choices):
        assert (7,5) in choices and all((x-7)**2+(y-5)**2<=9 for x,y in choices)
        return (7,5)
    monkeypatch.setattr('app.services.flamethrower.random.choice',choose)
    response=shoot(client,flame_game,auth_headers,target_character_id=flame_game['target_character'].id)
    assert response.status_code==200,response.json
    result=response.json['narrative_action']
    assert result['success'] is False and result['target']=={'x':7,'y':5}
    assert result['hits'][0]['character_id']==flame_game['target_character'].id


def test_reactive_scatter_is_capped_at_ten_metres(flame_game,monkeypatch):
    def choose(choices):
        assert choices and all((x-7)**2+(y-5)**2<=100 for x,y in choices)
        return choices[0]
    monkeypatch.setattr('app.services.flamethrower.random.choice',choose)
    scatter_cell(flame_game['location'],[],7,5,0,100)


@pytest.mark.parametrize('change',[{'fire_mode':'other'},{'target_x':29},{'target_floor_level':1},{'target_x':0}])
def test_bad_target_or_mode_does_not_spend_resources(client,flame_game,auth_headers,change):
    before=deepcopy(flame_game['actor_character'].data);ap=flame_game['actor_location'].action_points_current
    response=shoot(client,flame_game,auth_headers,**change)
    assert response.status_code==400,response.json
    assert flame_game['actor_location'].action_points_current==ap
    assert flame_game['actor_character'].data['weapons']==before['weapons']


def test_empty_fuel_and_hidden_reactive_target_are_rejected(client,flame_game,auth_headers):
    reactive(flame_game);flame_game['target_location'].gm_hidden=True;db.session.commit()
    # В этой фикстуре владелец стрелка — ГМ; ограничение обычного игрока проверяется явно.
    with pytest.raises(ValidationError,match='скрыта'):
        prepare(flame_game['actor_location'],0,'short',None,None,flame_game['target_character'].id,is_gm=False)
    data=deepcopy(flame_game['actor_character'].data);fuel,_=resources(data,data['weapons'][0]);fuel['uses']=0
    flame_game['actor_character'].data=data;db.session.commit()
    response=shoot(client,flame_game,auth_headers,fire_mode='long')
    assert response.status_code==400


def test_empty_tank_template_starts_empty(flame_game):
    assert charges(flame_game['item']('Бак для струйного огнемета (пустой)'))==0


def test_jet_rectangle_and_reactive_cone_are_different(flame_game):
    area={'x':4,'y':5,'origin_x':2,'origin_y':5,'direction':[1,0],'length':6,'width':2,'floor_level':0}
    rectangle=cells(flame_game['location'],[],{**area,'shape':'flame_rectangle'})
    cone=cells(flame_game['location'],[],{**area,'shape':'flame_cone'})
    assert [4,6,0] in rectangle and [4,6,0] not in cone
    assert [10,5,0] in rectangle and [3,5,0] not in rectangle


@pytest.mark.parametrize('material,blocked',[('concrete',True),('wood',False)])
def test_stream_does_not_pass_through_concrete_but_passes_wood(flame_game,material,blocked):
    wall=LocationObject(location_id=flame_game['location'].id,type='wall',name='Перегородка',tile_x=5,tile_y=5,
        properties={'material':material,'level':0,'dimensions':{'width':.2,'depth':6,'height':3}})
    db.session.add(wall);db.session.flush()
    area={'x':6,'y':5,'origin_x':2,'origin_y':5,'direction':[1,0],'length':6,'width':2,'floor_level':0,'shape':'flame_rectangle'}
    footprint=cells(flame_game['location'],[wall],area)
    assert ([7,5,0] not in footprint)==blocked


def test_direct_flamethrower_fire_damages_wood_each_round_without_spreading(client,flame_game,auth_headers):
    burning=LocationObject(location_id=flame_game['location'].id,type='tree',name='Дерево',tile_x=7,tile_y=5,
        properties={'material':'wood','level':0,'cover_max_hp':120,'cover_hp':120})
    adjacent=LocationObject(location_id=flame_game['location'].id,type='tree',name='Соседнее дерево',tile_x=13,tile_y=5,
        properties={'material':'wood','level':0,'cover_max_hp':120,'cover_hp':120})
    db.session.add_all([burning,adjacent]);db.session.commit()
    response=shoot(client,flame_game,auth_headers)
    assert response.status_code==200,response.json
    assert burning.id in response.json['narrative_action']['object_hits']
    assert burning.properties['object_fire']['damage_per_round']==50
    assert burning.properties['cover_hp']==120
    assert 'object_fire' not in adjacent.properties
    state=LocationCombatState.query.filter_by(location_id=flame_game['location'].id).one()
    ticks=C._advance_area_effects(state)
    assert {'object_id':burning.id,'type':'fire','damage':50,'destroyed':False} in ticks
    assert burning.properties['cover_hp']==70 and burning.properties['object_fire']['remaining']==3


def test_fire_spreads_diagonally_at_fifty_percent_next_round_only(flame_game,monkeypatch):
    objects=[LocationObject(location_id=flame_game['location'].id,type='tree',name=f'Дерево {index}',
        tile_x=7+index,tile_y=5+index,properties={'material':'wood','level':0,'cover_max_hp':300,'cover_hp':300})
        for index in range(3)]
    upstairs=LocationObject(location_id=flame_game['location'].id,type='tree',name='Дерево на другом этаже',
        tile_x=7,tile_y=5,properties={'material':'wood','level':1,'cover_max_hp':300,'cover_hp':300})
    db.session.add_all([*objects,upstairs]);db.session.flush()
    assert ignite_objects(objects,[[7,5,0]],rounds=4,damage=50,source='Бензин')==[objects[0].id]
    monkeypatch.setattr('app.services.burning.random.randint',lambda low,high:50)
    first=advance_object_fires(flame_game['location'].id)
    assert {'object_id':objects[1].id,'type':'fire_spread','from_object_id':objects[0].id} in first
    assert objects[0].properties['cover_hp']==250 and objects[1].properties['cover_hp']==300
    assert 'object_fire' not in objects[2].properties
    assert 'object_fire' not in upstairs.properties
    monkeypatch.setattr('app.services.burning.random.randint',lambda low,high:51)
    advance_object_fires(flame_game['location'].id)
    assert 'object_fire' not in objects[2].properties
    monkeypatch.setattr('app.services.burning.random.randint',lambda low,high:1)
    advance_object_fires(flame_game['location'].id)
    assert objects[2].properties['object_fire']['damage_per_round']==50


def test_service_mount_replaces_one_real_air_bottle_and_detaches_outside_combat(client,flame_game,auth_headers):
    case=flame_game;state=LocationCombatState.query.filter_by(location_id=case['location'].id).one();state.status='inactive'
    data=deepcopy(case['actor_character'].data);source=case['item']('Бак сжатого воздуха');source['quantity']=2
    data['inventory']['pockets'].append(source);index=len(data['inventory']['pockets'])-1
    case['actor_character'].data=data;db.session.commit()
    url=f'/lobbies/{case["lobby"].id}/locations/{case["location"].id}/combat/action'
    payload={'location_character_id':case['actor_location'].id,'action_key':'flamethrower_service','weapon_index':0,'item_path':['inventory','pockets',index]}
    response=client.post(url,headers=auth_headers(case['actor_user']),json=payload)
    assert response.status_code==200,response.json
    saved=case['actor_character'].data;_,air=resources(saved,saved['weapons'][0])
    assert saved['inventory']['pockets'][index]['quantity']==1 and air['quantity']==1
    assert saved['inventory']['pockets'][index]['id']!=air['id']
    old=deepcopy(air)
    payload.update(equipment_operation='detach',equipment_slot='air');payload.pop('item_path')
    response=client.post(url,headers=auth_headers(case['actor_user']),json=payload)
    assert response.status_code==200,response.json
    saved=case['actor_character'].data
    assert resources(saved,saved['weapons'][0])[1] is None
    assert any(i.get('id')==old['id'] for i in saved['inventory']['pockets'])


def test_unpriced_new_weapons_are_not_free_starting_equipment(flame_game):
    from app.services.character_starting_purchase import eligible
    assert not eligible(flame_game['flame_templates']['Струйный огнемёт'])


@pytest.mark.parametrize('thermal,damage',[(40,50),(40.1,0)])
def test_fuel_thermal_boundary_and_existing_burning(client,flame_game,auth_headers,monkeypatch,thermal,damage):
    assert shoot(client,flame_game,auth_headers).status_code==200
    target=flame_game['target_location']
    monkeypatch.setattr(C,'_target_elemental_protection',lambda *a,**kw:thermal)
    C._apply_periodic_health_effects(target,phase='round_end')
    assert target.character.data['health']['current']==700-damage


@pytest.mark.parametrize('cancel',[False,True])
def test_filling_empty_tank_reserves_the_container_and_five_litres(client,flame_game,auth_headers,cancel):
    from test_crafting import call
    case=flame_game;state=LocationCombatState.query.filter_by(location_id=case['location'].id).one();state.status='inactive'
    fuel=ItemTemplate(name='Топливо(1 Литр)',category='misc',price=10,weight=1,volume=1,attributes={})
    db.session.add(fuel);db.session.flush()
    data=deepcopy(case['actor_character'].data);data['skills'].setdefault('other',{})['engineering']={'base':20}
    data['inventory']['pockets']=[case['item']('Бак для струйного огнемета (пустой)'),item_from_template(fuel,5)]
    case['actor_character'].data=data;db.session.commit()
    started=call(client,case,auth_headers,'start',recipe_id='recipe-flame-39')
    assert started.status_code==200,started.json
    assert not started.json['data']['inventory']['pockets']
    case['lobby'].game_time_minutes+=5;db.session.commit()
    result=call(client,case,auth_headers,'cancel' if cancel else 'finish',job_id=started.json['result']['id'])
    assert result.status_code==200,result.json
    inventory=result.json['data']['inventory']['pockets']
    if cancel:
        assert charges(next(i for i in inventory if i['attributes'].get('flamethrower_tank')))==0
        assert next(i for i in inventory if i['name']=='Топливо(1 Литр)')['quantity']==5
    else:
        assert len(inventory)==1 and charges(inventory[0])==8 and inventory[0]['attributes']['fuel']=='Бензин'


def test_manual_air_pump_waits_five_minutes_and_keeps_pump(client,flame_game,auth_headers):
    from test_crafting import call
    case=flame_game;LocationCombatState.query.filter_by(location_id=case['location'].id).one().status='inactive'
    data=deepcopy(case['actor_character'].data);air=case['item']('Бак сжатого воздуха');air['uses']=0
    data['inventory']['pockets']=[air,{'id':'pump','name':'Ручной насос','category':'misc','quantity':1}]
    case['actor_character'].data=data;db.session.commit()
    started=call(client,case,auth_headers,'start',recipe_id='recipe-air-pump-manual')
    assert started.status_code==200,started.json
    assert call(client,case,auth_headers,'finish',job_id=started.json['result']['id']).status_code==400
    case['lobby'].game_time_minutes+=5;db.session.commit()
    result=call(client,case,auth_headers,'finish',job_id=started.json['result']['id'])
    assert result.status_code==200,result.json
    inventory=result.json['data']['inventory']['pockets']
    assert next(i for i in inventory if i.get('id')=='pump')['quantity']==1
    assert charges(next(i for i in inventory if (i.get('attributes') or {}).get('flamethrower_air')))==16


def test_sheet_save_cannot_refill_a_used_tank(client,flame_game,auth_headers):
    assert shoot(client,flame_game,auth_headers).status_code==200
    character=flame_game['actor_character'];data=character.data_snapshot();data['equipment']['backpack']['uses']=8
    response=client.put(f'/lobbies/characters/{character.id}',headers=auth_headers(flame_game['actor_user']),json={'data':data})
    assert response.status_code==200,response.json
    assert charges(character.data['equipment']['backpack'])==7
