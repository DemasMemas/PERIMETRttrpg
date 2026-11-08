"""Вскрытие, физические инструменты и закрытое содержимое в HTTP/сети."""
from copy import deepcopy
import json
import pytest
from app.extensions import db,socketio
from app.models import LocationObject,LocationCombatState
from app.models.templates import ItemTemplate
from app.schemas.location import LocationObjectSchema
from test_consumable_use import pair


@pytest.fixture
def locked(pair):
    character=pair['actor_character'];data=deepcopy(character.data)
    data['skills']={'physical':{'strength':{'base':10},'agility':{'base':10}},'other':{'engineering':{'base':10}}}
    data['inventory']={'pockets':[],'backpack':[]};character.data=data
    obj=LocationObject(location_id=pair['location'].id,name='Закрытый ящик',type='chest',tile_x=1,tile_y=2,
        properties={'locked':True,'lock_difficulty':8,'contents':[{'id':'secret-item','name':'Скрытая вещь','category':'misc','quantity':1}]})
    db.session.add(obj);db.session.commit();return {**pair,'object':obj}


def add_tool(game,name='Отмычка',quantity=1):
    template=ItemTemplate(name=name,category='tool' if name!='Термический Заряд' else 'misc');db.session.add(template);db.session.flush()
    item={'id':'tool-'+str(template.id),'name':name,'category':template.category,'templateId':template.id,'quantity':quantity}
    data=deepcopy(game['actor_character'].data);data['inventory']['backpack'].append(item);game['actor_character'].data=data;db.session.commit()
    return {'path':['inventory','backpack',len(data['inventory']['backpack'])-1],'snapshot':deepcopy(item)}


def command(game,**extra):
    return {'character_id':game['actor_character'].id,'character_revision':game['actor_character'].revision,
        'object_revision':game['object'].revision,'mode':'tools','tool':None,'operation_id':'lock-attempt',**extra}


def attempt(client,game,auth_headers,payload=None,user=None):
    return client.post(f"/lobbies/{game['lobby'].id}/locations/objects/{game['object'].id}/lock",
        headers=auth_headers(user or game['actor_user']),json=payload or command(game))


def patch(client,game,auth_headers,properties,user=None):
    return client.patch(f"/lobbies/{game['lobby'].id}/locations/objects/{game['object'].id}",
        headers=auth_headers(user or game['actor_user']),json={'properties':properties})


def test_tools_use_exact_stats_and_ap_once(client,locked,auth_headers,monkeypatch):
    game=locked;tool=add_tool(game);data=deepcopy(game['actor_character'].data)
    data['skills']['physical']['agility']['base']=14;data['skills']['other']['engineering']['base']=12
    game['actor_character'].data=data;db.session.commit();monkeypatch.setattr('app.services.object_locks.random.randint',lambda *_:3)
    body=command(game,tool=tool);first=attempt(client,game,auth_headers,body);assert first.status_code==200,first.json
    result=first.json['operation'];assert result['difficulty']==3 and result['success']
    assert result['agility_bonus']==2 and result['engineering_bonus']==1 and result['tool_bonus']==2
    assert not game['object'].properties['locked'] and game['actor_location'].action_points_current==2
    assert attempt(client,game,auth_headers,body).json['operation']['duplicate']
    assert game['actor_location'].action_points_current==2
    entries=[entry for entry in game['actor_character'].data['health']['combatMeta']['actionJournal'] if entry['id']=='lock-attempt']
    assert len(entries)==1 and entries[0]['action_points']==3


def test_failed_pick_breaks_one_from_stack_without_opening(client,locked,auth_headers,monkeypatch):
    game=locked;tool=add_tool(game,quantity=2);rolls=iter([1,50]);monkeypatch.setattr('app.services.object_locks.random.randint',lambda *_:next(rolls))
    response=attempt(client,game,auth_headers,command(game,tool=tool));assert response.status_code==200,response.json
    assert response.json['operation']['tool_broken'] and response.json['operation']['risk_percent']==50
    assert game['actor_character'].data['inventory']['backpack'][0]['quantity']==1
    assert game['object'].properties['locked'] and not game['object'].properties['lock_jammed']


def test_force_failure_jams_object_and_gm_can_reset(client,locked,auth_headers,monkeypatch):
    game=locked;rolls=iter([1,1]);monkeypatch.setattr('app.services.object_locks.random.randint',lambda *_:next(rolls))
    response=attempt(client,game,auth_headers,command(game,mode='force'));assert response.status_code==200,response.json
    assert response.json['operation']['difficulty']==12 and response.json['operation']['jammed']
    assert response.json['operation']['risk_percent']==60
    before=game['actor_location'].action_points_current
    again=attempt(client,game,auth_headers,command(game,mode='force',operation_id='second'))
    assert again.status_code==400 and game['actor_location'].action_points_current==before
    assert patch(client,game,auth_headers,{'lock_jammed':False}).status_code==200
    assert not game['object'].properties['lock_jammed']


@pytest.mark.parametrize('mode,expected',[('tools',18),('force',12)])
def test_no_tools_and_force_difficulties(client,locked,auth_headers,monkeypatch,mode,expected):
    monkeypatch.setattr('app.services.object_locks.random.randint',lambda *_:20)
    response=attempt(client,locked,auth_headers,command(locked,mode=mode));assert response.status_code==200,response.json
    assert response.json['operation']['difficulty']==expected and response.json['operation']['success']


def test_burner_needs_and_consumes_real_fuel(client,locked,auth_headers,monkeypatch):
    game=locked;tool=add_tool(game,'Горелка');before=game['actor_location'].action_points_current
    assert attempt(client,game,auth_headers,command(game,tool=tool)).status_code==400
    assert game['actor_location'].action_points_current==before
    add_tool(game,'Баллон Горелки',quantity=2);monkeypatch.setattr('app.services.object_locks.random.randint',lambda *_:20)
    response=attempt(client,game,auth_headers,command(game,tool=tool));assert response.status_code==200,response.json
    assert response.json['operation']['tool_bonus']==4
    assert game['actor_character'].data['inventory']['backpack'][1]['quantity']==1


@pytest.mark.parametrize('roll',[1,20])
def test_thermal_charge_is_consumed_on_success_and_failure(client,locked,auth_headers,monkeypatch,roll):
    tool=add_tool(locked,'Термический Заряд');monkeypatch.setattr('app.services.object_locks.random.randint',lambda *_:roll)
    response=attempt(client,locked,auth_headers,command(locked,tool=tool));assert response.status_code==200,response.json
    assert response.json['operation']['tool_bonus']==6 and not locked['actor_character'].data['inventory']['backpack']


def test_locked_contents_hidden_in_http_and_socket_but_gm_keeps_full_data(client,locked,auth_headers,app):
    game=locked;obj=game['object'];url=f"/lobbies/{game['lobby'].id}/locations/objects/{obj.id}"
    game['lobby'].is_active=True;db.session.commit()
    public=client.get(url,headers=auth_headers(game['target_user']));assert public.status_code==200
    assert public.json['properties']['contents']==[] and public.json['properties']['contents_hidden']
    assert client.get(url,headers=auth_headers(game['actor_user'])).json['properties']['contents'][0]['id']=='secret-item'
    detail=client.get(f"/lobbies/{game['lobby'].id}/locations/{game['location'].id}",headers=auth_headers(game['target_user']))
    assert not detail.json['objects'][0]['properties']['contents']
    from app.sockets.location import handle_join_location
    socketio.on_event('join_location',handle_join_location)
    observer=socketio.test_client(app,flask_test_client=client)
    observer.emit('join_location',{'token':game['target_user']['token'],'location_id':game['location'].id});observer.get_received()
    changed=patch(client,game,auth_headers,{'lock_difficulty':10});assert changed.status_code==200,changed.json
    packets=[entry for entry in observer.get_received() if entry['name']=='location_object_updated']
    assert packets and 'Скрытая вещь' not in json.dumps(packets,ensure_ascii=False)
    assert changed.json['properties']['contents'][0]['id']=='secret-item';observer.disconnect()


def test_old_open_and_lock_patch_cannot_bypass_player_permissions(client,locked,auth_headers):
    game=locked;obj=game['object'];obj.type='door';db.session.commit()
    assert patch(client,game,auth_headers,{'locked':False},game['target_user']).status_code==403
    assert patch(client,game,auth_headers,{'is_open':True},game['target_user']).status_code==400
    assert patch(client,game,auth_headers,{'lock_difficulty':7}).status_code==400
    assert attempt(client,game,auth_headers,user=game['target_user']).status_code==403


def test_attempt_requires_same_floor_nearby_own_turn_and_resource(client,locked,auth_headers):
    game=locked;actor=game['actor_location'];actor.floor_level=1;db.session.commit()
    assert attempt(client,game,auth_headers).status_code==400
    actor.floor_level=0;actor.pos_x=10;db.session.commit();assert attempt(client,game,auth_headers).status_code==400
    actor.pos_x=1;actor.action_points_current=2;db.session.commit();assert attempt(client,game,auth_headers).status_code==400
    actor.action_points_current=5;state=LocationCombatState.query.filter_by(location_id=game['location'].id).one()
    state.current_location_character_id=game['target_location'].id;db.session.commit()
    assert attempt(client,game,auth_headers).status_code==403
    assert actor.action_points_current==5 and game['object'].properties['locked']


def test_editor_history_retains_closed_contents_on_edit_and_undo(client,locked,auth_headers):
    game=locked;obj=game['object'];before=LocationObjectSchema(reveal_locked=True).dump(obj);after=deepcopy(before);after['tile_x']=3
    url=f"/lobbies/{game['lobby'].id}/locations/{game['location'].id}/object-edits"
    changed=client.post(url,headers=auth_headers(game['actor_user']),json={'objects':[{'id':obj.id,'before':before,'after':after}]})
    assert changed.status_code==200,changed.json
    current=changed.json['objects'][0];assert current['properties']['contents'][0]['id']=='secret-item'
    restored=client.post(url,headers=auth_headers(game['actor_user']),json={'objects':[{'id':obj.id,'before':current,'after':before}]})
    assert restored.status_code==200,restored.json
    assert restored.json['objects'][0]['tile_x']==1 and obj.properties['contents'][0]['id']=='secret-item'


def test_hatch_lock_can_be_reached_from_below_only_with_ladder(client,locked,auth_headers,monkeypatch):
    game=locked;obj=game['object'];obj.type='hatch';obj.properties={**obj.properties,'level':1,'building_id':'lock-hatch'};db.session.commit()
    assert attempt(client,game,auth_headers).status_code==400
    ladder=LocationObject(location_id=obj.location_id,type='ladder',tile_x=obj.tile_x,tile_y=obj.tile_y,
        properties={'level':0,'building_id':'lock-hatch'})
    db.session.add(ladder);db.session.commit();monkeypatch.setattr('app.services.object_locks.random.randint',lambda *_:20)
    response=attempt(client,game,auth_headers);assert response.status_code==200,response.json
    assert response.json['operation']['success']


@pytest.mark.parametrize('mode',[ 'tools','force'])
def test_positive_skill_bonus_can_remove_break_or_jam_risk(client,locked,auth_headers,monkeypatch,mode):
    game=locked;tool=add_tool(game) if mode=='tools' else None
    data=deepcopy(game['actor_character'].data);data['skills']['physical']['strength']['base']=22
    data['skills']['other']['engineering']['base']=20;game['actor_character'].data=data;db.session.commit()
    rolls=[]
    def only_d20(low,high):
        assert high==20;rolls.append(1);return 1
    monkeypatch.setattr('app.services.object_locks.random.randint',only_d20)
    response=attempt(client,game,auth_headers,command(game,mode=mode,tool=tool));assert response.status_code==200,response.json
    assert response.json['operation']['risk_percent']==0 and not response.json['operation']['tool_broken'] and not response.json['operation']['jammed']
    assert len(rolls)==1


def test_crowbar_adds_strength_bonus_to_its_tool_bonus(client,locked,auth_headers,monkeypatch):
    game=locked;tool=add_tool(game,'Монтировка');data=deepcopy(game['actor_character'].data)
    data['skills']['physical']['strength']['base']=14;game['actor_character'].data=data;db.session.commit()
    monkeypatch.setattr('app.services.object_locks.random.randint',lambda *_:20)
    response=attempt(client,game,auth_headers,command(game,tool=tool));assert response.status_code==200,response.json
    assert response.json['operation']['tool_bonus']==5 and response.json['operation']['difficulty']==3
