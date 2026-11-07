"""Созданные укрытия: реальные экземпляры, повреждения, окна и оплата в бою."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationObject,LocationCombatState
from app.models.templates import ItemTemplate
from app.services.character_starting_purchase import item_from_template
from app.services.crafted_covers import PROFILES,templates,operate
from app.services.exceptions import ValidationError,ConflictError
from test_portable_stations import station_game
from test_grenade_rules import grenade_game


@pytest.fixture
def cover_game(station_game):
    pair=station_game;catalogue={row['attributes']['crafted_cover']:ItemTemplate(**row) for row in templates()}
    db.session.add_all(catalogue.values());db.session.flush()
    data=deepcopy(pair['actor_character'].data)
    data['inventory']['pockets']=[item_from_template(catalogue['recipe-m19'])]
    data['inventory']['pockets'][0]['customLabel']='Мой экземпляр'
    pair['actor_character'].data=data;db.session.commit();pair['covers']=catalogue
    return pair


def request(pair,operation='place',**changes):
    return {'operation':operation,'operation_id':operation+'-cover','character_id':pair['actor_character'].id,
        'character_revision':pair['actor_character'].revision,'item_path':['inventory','pockets',0],
        'item_id':pair['actor_character'].data['inventory']['pockets'][0]['id'] if pair['actor_character'].data['inventory']['pockets'] else None,
        'x':5.2,'y':4,'rotation':0,**changes}


def call(pair,payload):return operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],payload)


def test_templates_match_author_and_all_recipe_outputs_resolve(cover_game):
    from app.services.crafting import _template,recipe_by_id
    assert len(templates())==6
    for key,profile in PROFILES.items():
        output=_template(recipe_by_id(key)['output_name'])
        assert output.attributes['crafted_cover']==key
        assert (output.weight,output.volume)==(profile['weight'],profile['volume'])
        assert output.price==0 and output.attributes['not_for_sale']


@pytest.mark.parametrize('change',[{'x':9},{'x':4},{'rotation':'0'},{'floor_level':1}])
def test_invalid_placement_preserves_inventory(cover_game,change):
    pair=cover_game;before=deepcopy(pair['actor_character'].data)
    with pytest.raises(ValidationError):call(pair,request(pair,**change))
    assert pair['actor_character'].data==before and LocationObject.query.count()==0


def test_damage_survives_move_recovery_replay_and_redeployment(cover_game):
    from app.services.combat import CombatService
    pair=cover_game;initial=request(pair);_,obj,_,_=call(pair,initial);identity=obj.properties['placed_item']['id']
    assert call(pair,initial)[1].id==obj.id and LocationObject.query.count()==1
    CombatService.apply_cover_damage(obj,400,'bullet');db.session.commit()
    assert obj.properties['cover_hp']==600
    moved=request(pair,'move',object_id=obj.id,object_revision=obj.revision,x=5.2,y=4.8,rotation=.6)
    call(pair,moved);assert obj.properties['cover_hp']==600
    recovered=request(pair,'recover',object_id=obj.id,object_revision=obj.revision)
    call(pair,recovered);call(pair,recovered)
    assert LocationObject.query.count()==0
    backpack=pair['actor_character'].data['inventory']['backpack'];assert len(backpack)==1
    assert backpack[0]['id']==identity and backpack[0]['customLabel']=='Мой экземпляр'
    _,obj,_,_=call(pair,request(pair,operation_id='second-placement',item_path=['inventory','backpack',0],item_id=identity))
    assert obj.properties['cover_hp']==600 and obj.properties['cover_physical_protection']==3


def test_any_adjacent_controlled_character_can_recover_but_brick_cannot_move(cover_game):
    pair=cover_game;_,obj,_,_=call(pair,request(pair));other=pair['target_location'];other.pos_x,other.pos_y=6.5,4;db.session.commit()
    operate(pair['lobby'].id,pair['location'].id,pair['target_user']['id'],{
        'operation':'recover','operation_id':'other-owner','character_id':other.character_id,
        'character_revision':other.character.revision,'object_id':obj.id,'object_revision':obj.revision})
    assert any(item.get('customLabel')=='Мой экземпляр' for item in other.character.data['inventory']['backpack'])
    data=deepcopy(pair['actor_character'].data);data['inventory']['pockets']=[item_from_template(pair['covers']['recipe-m23'])]
    pair['actor_character'].data=data;db.session.commit();other.pos_x=20;db.session.commit()
    _,brick,_,_=call(pair,request(pair,operation_id='brick-place'))
    for operation in ('move','recover'):
        with pytest.raises(ValidationError,match='Кирпичную'):
            call(pair,request(pair,operation,object_id=brick.id,object_revision=brick.revision))


def test_curtain_requires_window_and_uses_window_size(cover_game):
    pair=cover_game;data=deepcopy(pair['actor_character'].data)
    data['inventory']['pockets']=[item_from_template(pair['covers']['recipe-m18'])]
    pair['actor_character'].data=data;db.session.commit()
    with pytest.raises(ValidationError,match='окно'):call(pair,request(pair))
    window=LocationObject(location_id=pair['location'].id,type='window',name='Окно',tile_x=5,tile_y=4,
        properties={'dimensions':{'width':1.2,'depth':.14,'height':1.1},'elevation':1,'is_open':True})
    db.session.add(window);db.session.commit()
    _,curtain,_,_=call(pair,request(pair,anchor_id=window.id))
    assert curtain.properties['dimensions']=={'width':1.2,'depth':.03,'height':1.1}
    assert curtain.properties['cover_anchor_id']==window.id and curtain.properties['cover_hp']==200
    assert curtain.properties['cover_physical_protection']==0 and curtain.properties['blocks_movement'] is False


def test_cover_damage_cannot_be_repaired_by_sheet_save():
    from app.services.crafted_covers import preserve
    original={'inventory':{'backpack':[{'id':'cover','coverState':{'hp':600,'max_hp':1000}}]}}
    changed=deepcopy(original);changed['inventory']['backpack'][0]['coverState']['hp']=1000
    preserve(original,changed);assert changed==original


def test_battle_place_move_and_recover_pay_server_costs(cover_game,client,auth_headers):
    from app.services.combat import CombatService
    pair=cover_game;actor=pair['actor_location']
    CombatService.start_combat(pair['location'].id,pair['lobby'].gm_id,[actor.id],actor.id)
    actor.action_points_current=20;db.session.commit()
    url=f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/action"
    bypass=client.post(f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/crafted-covers",
        headers=auth_headers(pair['actor_user']),json=request(pair))
    assert bypass.status_code==400 and LocationObject.query.count()==0 and actor.action_points_current==20
    def act(payload,identity):return client.post(url,headers=auth_headers(pair['actor_user']),json={
        'location_character_id':actor.id,'action_key':'place_cover','attribute_choice':payload,'action_points':0,
        'item_path':payload.get('item_path') if payload['operation']=='place' else None,
        'item_id':payload.get('item_id'),'target_object_id':payload.get('object_id'),'pending_action_id':identity})
    placed=act(request(pair),'battle-cover-place');assert placed.status_code==200,placed.json
    obj=LocationObject.query.filter_by(location_id=pair['location'].id,type='cover').one()
    assert actor.action_points_current==15
    moved=act(request(pair,'move',object_id=obj.id,object_revision=obj.revision,x=5.2,y=4.8),'battle-cover-move')
    assert moved.status_code==200,moved.json
    assert actor.action_points_current==12
    recovered=act(request(pair,'recover',object_id=obj.id,object_revision=obj.revision),'battle-cover-recover')
    assert recovered.status_code==200,recovered.json
    assert actor.action_points_current==7 and LocationObject.query.count()==0


def test_deferred_placement_waits_for_full_payment_and_resumes_once(cover_game,client,auth_headers):
    from app.services.combat import CombatService
    from app.models import DeferredCombatAction
    pair=cover_game;actor=pair['actor_location'];character=pair['actor_character']
    CombatService.start_combat(pair['location'].id,pair['lobby'].gm_id,[actor.id],actor.id)
    actor.action_points_current=2;db.session.commit();original=deepcopy(character.data['inventory']['pockets'])
    url=f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/action"
    initial=request(pair)
    response=client.post(url,headers=auth_headers(pair['actor_user']),json={'location_character_id':actor.id,
        'action_key':'place_cover','pending_action_id':'cover-deferred','attribute_choice':initial,
        'item_path':initial['item_path'],'item_id':initial['item_id']})
    assert response.status_code==200 and response.json['pending_action'],response.json
    assert LocationObject.query.count()==0 and character.data['inventory']['pockets']==original
    row=db.session.get(DeferredCombatAction,'cover-deferred');assert row.status=='ready'
    paid_ap=actor.action_points_current
    resumed=client.post(url,headers=auth_headers(pair['actor_user']),json={'location_character_id':actor.id,
        'action_key':'place_cover','resume_pending_action_id':row.id,'attribute_choice':{'x':29}})
    assert resumed.status_code==200,resumed.json
    assert actor.action_points_current==paid_ap and LocationObject.query.count()==1
    assert character.data['inventory']['pockets']==[]
    duplicate=client.post(url,headers=auth_headers(pair['actor_user']),json={'location_character_id':actor.id,
        'action_key':'place_cover','resume_pending_action_id':row.id})
    assert duplicate.status_code==409 and LocationObject.query.count()==1


def test_cover_migration_can_repeat_without_changing_ids(app,monkeypatch):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'migrations/versions/d4a16f9ea275_add_crafted_cover_templates.py'
    spec=importlib.util.spec_from_file_location('covers_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    row=ItemTemplate(**templates()[0]);db.session.add(row);db.session.flush();identity=row.id
    monkeypatch.setattr(migration.op,'get_bind',lambda:db.session.connection())
    migration.upgrade();migration.upgrade();db.session.expire_all()
    assert row.id==identity and ItemTemplate.query.count()==6
