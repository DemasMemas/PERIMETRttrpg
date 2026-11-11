"""Ловушки: отдельное скрытое состояние, маскировка и обезвреживание."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationTrap,LocationCombatState
from app.models.templates import ItemTemplate
from app.services.character_starting_purchase import item_from_template
from app.services.trap_catalog import templates
from app.services.traps import operate
from test_grenade_rules import grenade_game


@pytest.fixture
def trap_game(grenade_game):
    pair=grenade_game;actor=pair['actor_location'];actor.pos_x=actor.pos_y=4
    pair['target_location'].pos_x,pair['target_location'].pos_y=6,4
    LocationCombatState.query.filter_by(location_id=pair['location'].id).one().status='idle'
    rows={row['attributes']['trap_recipe']:ItemTemplate(**row) for row in templates()}
    db.session.add_all(rows.values());db.session.flush()
    data=deepcopy(actor.character.data);data['inventory']['pockets']=[item_from_template(rows['recipe-m4'])]
    actor.character.data=data;db.session.commit();pair['traps']=rows
    return pair


def request(pair,operation='place',**extra):
    character=pair['actor_character'];item=(character.data['inventory']['pockets'] or [{}])[0]
    return {'operation':operation,'operation_id':operation+'-trap','character_id':character.id,
        'character_revision':character.revision,'item_path':['inventory','pockets',0],'item_id':item.get('id'),'x':5,'y':4,**extra}


def call(pair,payload):return operate(pair['lobby'].id,pair['location'].id,pair['actor_user']['id'],payload)


@pytest.mark.parametrize('in_combat', [False, True])
def test_field_engineer_cannot_place_trap_or_spend_item_and_ap(trap_game, client, auth_headers, in_combat):
    from app.services.combat import CombatService
    pair = trap_game
    actor = pair['actor_location']
    data = deepcopy(actor.character.data)
    data.setdefault('basic', {})['background'] = {'ruleId': 'background-z'}
    actor.character.data = data
    if in_combat:
        CombatService.start_combat(actor.location_id, pair['lobby'].gm_id, [actor.id], actor.id)
    db.session.commit()
    db.session.expire_all()
    before, ap = deepcopy(actor.character.data), actor.action_points_current
    loc = f"/lobbies/{pair['lobby'].id}/locations/{actor.location_id}"
    payload = request(pair)
    if in_combat:
        response = client.post(loc + '/combat/action', headers=auth_headers(pair['actor_user']), json={
            'location_character_id': actor.id, 'action_key': 'trap', 'attribute_choice': payload,
            'pending_action_id': 'engineer-placement', 'item_path': payload['item_path'], 'item_id': payload['item_id'],
        })
    else:
        response = client.post(loc + '/traps', headers=auth_headers(pair['actor_user']), json=payload)
    assert response.status_code == 400, response.json
    assert 'Полевой инженер' in str(response.json)
    db.session.expire_all()
    after = deepcopy(actor.character.data)
    if in_combat:
        # Отказ сохраняется в журнале; игровые ресурсы и содержимое остаются прежними.
        history = after['health']['combatMeta'].pop('actionJournal')
        previous = before['health']['combatMeta'].pop('actionJournal', [])
        assert history[:-1] == previous
        assert history[-1]['action_points'] == 0 and 'Полевой инженер' in history[-1]['reason']
    assert after == before and actor.action_points_current == ap
    assert LocationTrap.query.count() == 0


@pytest.mark.parametrize('count,inactive,engineer,tool,expected', [
    (0, False, False, None, 12), (1, False, False, None, 16),
    (2, False, False, 'Нож', 21), (1, True, False, None, 12),
    (1, False, True, 'Нож', 18), (2, False, True, 'Лопата', 25),
])
def test_masking_traits_and_tools_set_saved_detection_dc(trap_game, monkeypatch, count, inactive, engineer, tool, expected):
    pair = trap_game
    call(pair, request(pair))
    trap = LocationTrap.query.one()
    data = deepcopy(pair['actor_character'].data)
    data['skills'] = {'other': {'stealth': {'base': 10}}}
    data.setdefault('features', {})['specialTraits'] = [
        {'ruleId': 'trait-f14', 'active': not inactive} for _ in range(count)
    ]
    if engineer:
        data.setdefault('basic', {})['background'] = {'ruleId': 'background-z'}
    data['inventory']['pockets'] = [{'name': tool, 'category': 'tool', 'quantity': 1}] if tool else []
    pair['actor_character'].data = data
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 12)
    payload = request(pair, 'mask', trap_id=trap.id, trap_revision=trap.revision,
                      tool_path=['inventory', 'pockets', 0] if tool else None)
    _, result = call(pair, payload)
    assert result['check']['total'] == expected
    assert trap.detection_dc == expected
    call(pair, payload)
    db.session.expire_all()
    assert db.session.get(LocationTrap, trap.id).detection_dc == expected


@pytest.mark.parametrize('engineer,roll,tool,expected_bonus,success', [
    (False, 2, 'Нож', 5, False), (True, 2, 'Нож', 8, True),
    (True, 1, 'Нож', 8, False), (True, 8, None, 0, True),
])
def test_engineer_defuse_tool_bonus_keeps_failure_and_optional_tool_rules(trap_game, monkeypatch, engineer, roll, tool, expected_bonus, success):
    pair = trap_game
    trap = LocationTrap(location_id=pair['location'].id, x=5, y=4, level=0,
                        recipe_id='recipe-m5', status='armed', known_users=[], data={})
    db.session.add(trap)
    data = deepcopy(pair['actor_character'].data)
    data['skills'] = {'physical': {'awareness': {'base': 10}}, 'other': {'engineering': {'base': 10}}}
    if engineer:
        data.setdefault('basic', {})['background'] = {'ruleId': 'background-z'}
    data['inventory']['pockets'] = [{'name': tool, 'category': 'tool', 'quantity': 1}] if tool else []
    pair['actor_character'].data = data
    db.session.commit()
    monkeypatch.setattr('app.services.traps.random.randint', lambda *_: roll)
    payload = request(pair, 'defuse', trap_id=trap.id, trap_revision=trap.revision,
                      tool_path=['inventory', 'pockets', 0] if tool else None)
    _, result = call(pair, payload)
    assert result['difficulty'] == 8 - expected_bonus
    assert result['check']['success'] is success
    assert trap.status == ('disarmed' if success else 'spent')
    assert pair['actor_character'].data['inventory']['pockets'] == data['inventory']['pockets']
    _, repeated = call(pair, payload)
    assert repeated['check'] == result['check']


def test_engineer_keeps_automatic_weight_tool_and_custom_trait_is_not_background(trap_game, monkeypatch):
    pair = trap_game
    data = deepcopy(pair['actor_character'].data)
    data.setdefault('features', {})['specialTraits'] = [{'name': 'Полевой инженер'}]
    pair['actor_character'].data = data
    db.session.commit()
    call(pair, request(pair))
    trap = LocationTrap.query.one()
    data = deepcopy(pair['actor_character'].data)
    data.setdefault('basic', {})['background'] = {'ruleId': 'background-z'}
    data['inventory']['pockets'] = [{'name': 'Грузик', 'category': 'tool', 'quantity': 1}]
    pair['actor_character'].data = data
    db.session.commit()
    monkeypatch.setattr('app.services.traps.random.randint', lambda *_: 1)
    _, result = call(pair, request(pair, 'defuse', trap_id=trap.id, trap_revision=trap.revision,
                                  tool_path=['inventory', 'pockets', 0]))
    assert result['check']['success'] and result['check']['tool_bonus'] == 'automatic'
    assert trap.status == 'disarmed'


def test_placement_is_replay_safe_and_masks_do_not_leak_in_location_api(trap_game,client,auth_headers,monkeypatch):
    pair=trap_game;payload=request(pair);call(pair,payload);call(pair,payload)
    trap=LocationTrap.query.one();assert pair['actor_character'].data['inventory']['pockets']==[]
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:12)
    call(pair,request(pair,'mask',trap_id=trap.id,trap_revision=trap.revision))
    assert trap.detection_dc is not None
    url=f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}"
    other=client.get(url,headers=auth_headers(pair['target_user']));assert other.status_code==200,other.json
    assert not any(o.get('type')=='placed_trap' for o in other.json['objects'])
    owner=client.get(url,headers=auth_headers(pair['actor_user']));assert any(o['type']=='placed_trap' for o in owner.json['objects'])
    assert all('detection_dc' not in str(o) and 'trapGrenades' not in str(o) for o in owner.json['objects'])


def test_failed_disarm_applies_real_trap_damage_once(trap_game,monkeypatch):
    pair=trap_game;call(pair,request(pair));trap=LocationTrap.query.one()
    monkeypatch.setattr('app.services.traps.random.randint',lambda low,high:1)
    before=pair['actor_character'].data['health']['current']
    payload=request(pair,'defuse',trap_id=trap.id,trap_revision=trap.revision)
    _,result=call(pair,payload);assert result['check']['success'] is False
    assert trap.status=='spent'
    health=pair['actor_character'].data['health']
    assert health['current']==before-75
    assert health['painLevel']>=3
    assert any(e['type']=='bleeding_external_medium' for e in health['effects'])
    call(pair,payload);assert pair['actor_character'].data['health']['current']==before-75


def test_successful_disarm_does_not_allow_picking_up_beartrap(trap_game,monkeypatch):
    from app.services.exceptions import ValidationError
    pair=trap_game;call(pair,request(pair));trap=LocationTrap.query.one()
    monkeypatch.setattr('app.services.traps.random.randint',lambda low,high:20)
    call(pair,request(pair,'defuse',trap_id=trap.id,trap_revision=trap.revision));assert trap.status=='disarmed'
    with pytest.raises(ValidationError):call(pair,request(pair,'recover',trap_id=trap.id,trap_revision=trap.revision))


def test_walking_into_beartrap_stops_at_cell_and_applies_effects(trap_game):
    from app.services.combat import CombatService
    pair=trap_game;pair['target_location'].pos_y=6;db.session.commit();call(pair,request(pair))
    actor=pair['actor_location'];before=actor.character.data['health']['current']
    CombatService.move_character(pair['location'].id,pair['actor_user']['id'],actor.character_id,7,4)
    assert actor.pos_x==5 and actor.pos_y==4
    assert actor.character.data['health']['current']==before-75
    assert LocationTrap.query.one().status=='spent'


def test_mine_explosion_uses_author_radial_damage_table(trap_game,monkeypatch):
    from app.services.combat import CombatService
    from app.services.trap_catalog import PROFILES
    pair=trap_game;target=pair['target_location'];target.pos_x,target.pos_y=7,4;db.session.commit()
    p=PROFILES['recipe-m7'];profile={'key':'trap-mine','name':'Мина','radius':2,'fragment':500,'blast_base':600,'blast_falloff':0,'penetration':50,
        'radial_damage':{key:p[key] for key in ('fragments','blast','penetration')}}
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:high)
    result=CombatService.resolve_explosion(pair['location'].id,5,4,profile,target_ids=[target.id],damage_objects=False)
    hit=next(row for row in result['targets'] if row['character_id']==target.character_id)
    assert hit['fragment_damage']==250 and hit['fragment_penetration']==20
    assert hit['blast_damage']==300


def test_hidden_trap_can_be_found_by_awareness_without_receiving_secret_id(trap_game,monkeypatch):
    from app.services.traps import projections
    pair=trap_game;call(pair,request(pair));trap=LocationTrap.query.one()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:12)
    call(pair,request(pair,'mask',trap_id=trap.id,trap_revision=trap.revision))
    assert not projections(pair['location'].id,pair['target_user']['id'])
    other=pair['target_location'];other.facing_x,other.facing_y=-1,0;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:20)
    _,result=operate(pair['lobby'].id,pair['location'].id,pair['target_user']['id'],{'operation':'detect',
        'operation_id':'find-trap','character_id':other.character_id,'character_revision':other.character.revision,'x':5,'y':4})
    assert result['found']==1
    assert projections(pair['location'].id,pair['target_user']['id'])[0]['properties']['trap_id']==trap.id


def test_net_stops_movement_and_strength_check_releases_character(trap_game,monkeypatch):
    from app.services.combat import CombatService
    from app.services.exceptions import ValidationError
    pair=trap_game;data=deepcopy(pair['actor_character'].data)
    data['inventory']['pockets']=[item_from_template(pair['traps']['recipe-m5'])]
    pair['actor_character'].data=data;pair['target_location'].pos_y=7;db.session.commit();call(pair,request(pair))
    actor=pair['actor_location'];CombatService.move_character(pair['location'].id,pair['actor_user']['id'],actor.character_id,7,4)
    assert actor.pos_x==5 and actor.character.data['health']['combatMeta']['trappedNet']
    with pytest.raises(ValidationError,match='сети'):CombatService.move_character(pair['location'].id,pair['actor_user']['id'],actor.character_id,6,4)
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:20)
    _,result=call(pair,request(pair,'escape_net'))
    assert result['check']['success'] and result['action_points']==2
    assert 'trappedNet' not in actor.character.data['health']['combatMeta']
    CombatService.move_character(pair['location'].id,pair['actor_user']['id'],actor.character_id,6,4)
    assert actor.pos_x==6


def test_combat_placement_and_defuse_charge_once_and_reject_bypass(trap_game,client,auth_headers,monkeypatch):
    from app.services.combat import CombatService
    pair=trap_game;actor=pair['actor_location']
    CombatService.start_combat(pair['location'].id,pair['lobby'].gm_id,[actor.id],actor.id)
    actor.action_points_current=20;db.session.commit()
    loc=f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}"
    bypass=client.post(loc+'/traps',headers=auth_headers(pair['actor_user']),json=request(pair))
    assert bypass.status_code==400 and LocationTrap.query.count()==0
    def act(payload,identity):return client.post(loc+'/combat/action',headers=auth_headers(pair['actor_user']),json={
        'location_character_id':actor.id,'action_key':'trap','attribute_choice':payload,'pending_action_id':identity,
        'item_path':payload.get('item_path'),'item_id':payload.get('item_id')})
    placed=act(request(pair),'combat-trap-place');assert placed.status_code==200,placed.json
    assert actor.action_points_current==14
    trap=LocationTrap.query.one();monkeypatch.setattr('app.services.traps.random.randint',lambda low,high:20)
    defused=act(request(pair,'defuse',trap_id=trap.id,trap_revision=trap.revision),'combat-trap-defuse')
    assert defused.status_code==200,defused.json
    assert actor.action_points_current==12 and trap.status=='disarmed'


def test_mine_waits_until_victims_turn_end(trap_game,monkeypatch):
    from app.services.combat import CombatService
    from app.services.traps import trigger
    pair=trap_game;actor=pair['actor_location']
    CombatService.start_combat(pair['location'].id,pair['lobby'].gm_id,[actor.id],actor.id)
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    trap=LocationTrap(location_id=actor.location_id,x=4,y=4,recipe_id='recipe-m7',data={},known_users=[])
    db.session.add(trap);db.session.flush();hits=[]
    monkeypatch.setattr(CombatService,'resolve_explosion',lambda *a,**k:hits.append((a,k)) or {})
    trigger(trap,actor,state);db.session.commit()
    assert not hits and state.pending_explosives[0]['trigger']=='turn_end'
    CombatService.end_turn(actor.location_id,pair['lobby'].gm_id,actor.id)
    assert len(hits)==1 and not state.pending_explosives


def test_same_receipt_cannot_be_used_for_another_cell(trap_game):
    from app.services.exceptions import ConflictError
    pair=trap_game;payload=request(pair);call(pair,payload)
    with pytest.raises(ConflictError):call(pair,{**payload,'y':5})
    assert LocationTrap.query.count()==1


def test_jump_triggers_only_landing_cell(trap_game,monkeypatch):
    from app.services.character_jump import prepare_jump,execute_jump
    pair=trap_game;actor=pair['actor_location'];pair['target_location'].pos_y=7;db.session.commit()
    call(pair,request(pair));before=actor.character.data['health']['current']
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:20)
    execute_jump(pair['location'],actor,prepare_jump(pair['location'],actor,6,4),0)
    assert actor.character.data['health']['current']==before and LocationTrap.query.one().status=='armed'
    execute_jump(pair['location'],actor,prepare_jump(pair['location'],actor,5,4),0)
    assert actor.character.data['health']['current']==before-75 and LocationTrap.query.one().status=='spent'


def test_migration_creates_trap_table_and_preserves_existing_template_ids(trap_game,monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    pair=trap_game;identity=pair['traps']['recipe-m4'].id
    LocationTrap.__table__.drop(db.session.connection())
    path=Path(__file__).resolve().parents[1]/'migrations/versions/e5b27a0fb386_add_location_traps.py'
    spec=importlib.util.spec_from_file_location('trap_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    migration.op=Operations(MigrationContext.configure(db.session.connection()));migration.upgrade()
    assert ItemTemplate.query.filter_by(id=identity).one().attributes['trap_recipe']=='recipe-m4'
    assert LocationTrap.query.count()==0


def test_ally_can_cut_net_with_real_weapon_and_net_does_not_end_victims_turn(trap_game):
    from app.services.combat import CombatService
    from app.services.traps import trigger
    pair=trap_game;actor=pair['actor_location'];other=pair['target_location']
    other.pos_x,other.pos_y=5,4
    data=deepcopy(other.character.data);data['weapons']=[{'id':'sharp-knife','category':'melee_weapon','name':'Нож',
        'attributes':{'allowed_attacks':['Режущая']}}];other.character.data=data
    CombatService.start_combat(pair['location'].id,pair['lobby'].gm_id,[actor.id,other.id],actor.id)
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    trap=LocationTrap(location_id=actor.location_id,x=4,y=4,recipe_id='recipe-m5',data={},known_users=[])
    db.session.add(trap);db.session.flush();trigger(trap,actor,state);db.session.commit()
    assert state.current_location_character_id==actor.id
    state.status='idle';db.session.commit()
    _,result=operate(pair['lobby'].id,pair['location'].id,pair['target_user']['id'],{
        'operation':'cut_net','operation_id':'cut-net','character_id':other.character_id,'character_revision':other.character.revision,
        'tool_path':['weapons',0],'target_character_id':actor.character_id})
    assert result['action_points']==3 and result['affected_character_ids']==[actor.character_id]
    assert 'trappedNet' not in actor.character.data['health']['combatMeta']


@pytest.mark.parametrize('fuse,expected',[('round','short_fuse'),('turn_end',None),('contact',None)])
def test_tripwire_shortens_fuse_and_triggers_both_grenades(trap_game,monkeypatch,fuse,expected):
    from app.services.combat import CombatService
    from app.services.traps import trigger
    pair=trap_game;actor=pair['actor_location']
    CombatService.start_combat(pair['location'].id,pair['lobby'].gm_id,[actor.id],actor.id)
    pair['target_location'].pos_x=25;pair['target_location'].pos_y=25
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    profile={'key':'test','name':'Тестовая граната','fuse':fuse,'radius':1,'fragment':0,'blast_base':0,'noise':0}
    monkeypatch.setattr(CombatService,'_explosive_profile',lambda item:deepcopy(profile))
    hits=[];monkeypatch.setattr(CombatService,'resolve_explosion',lambda *a,**k:hits.append((a,k)) or {})
    trap=LocationTrap(location_id=actor.location_id,x=4,y=4,recipe_id='recipe-m9',data={'item':{'trapGrenades':[{},{}]}},known_users=[])
    db.session.add(trap);db.session.flush();trigger(trap,actor,state)
    if expected:
        assert len(state.pending_explosives)==2 and all(e['trigger']==expected for e in state.pending_explosives)
        assert actor.character.data['health']['combatMeta']['grenadeReaction']['event_ids']
    else:assert len(hits)==2 and not state.pending_explosives


def test_masked_trap_is_published_only_to_users_who_know_it(trap_game,monkeypatch):
    from app.services.traps import publish,publish_effects
    pair=trap_game;call(pair,request(pair));trap=LocationTrap.query.one()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:12)
    call(pair,request(pair,'mask',trap_id=trap.id,trap_revision=trap.revision))
    emitted=[];monkeypatch.setattr('app.services.traps.socketio.emit',lambda event,payload,**kwargs:emitted.append((event,payload,kwargs)))
    publish(pair['location'].id,[trap.id])
    other=next(e for e in emitted if e[2]['room']==f"user_{pair['target_user']['id']}")
    owner=next(e for e in emitted if e[2]['room']==f"user_{pair['actor_user']['id']}")
    assert other[0]=='location_object_deleted' and other[1]['object'] is None
    assert owner[0]=='location_object_updated' and owner[1]['object']['type']=='placed_trap'
    publish_effects({'location_id':pair['location'].id,'audible_to_all':True})
    assert emitted[-1][0]=='trap_alarm' and emitted[-1][2]['room']==f"location_{pair['location'].id}"


def test_deferred_trap_is_not_created_before_payment_and_replays_once(trap_game,client,auth_headers):
    from app.services.combat import CombatService
    from app.models import DeferredCombatAction
    pair=trap_game;actor=pair['actor_location'];character=actor.character
    CombatService.start_combat(pair['location'].id,pair['lobby'].gm_id,[actor.id],actor.id)
    actor.action_points_current=2;db.session.commit();before=deepcopy(character.data['inventory']['pockets'])
    url=f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/action";payload=request(pair)
    response=client.post(url,headers=auth_headers(pair['actor_user']),json={
        'location_character_id':actor.id,'action_key':'trap','pending_action_id':'trap-deferred',
        'attribute_choice':payload,'item_path':payload['item_path'],'item_id':payload['item_id']})
    assert response.status_code==200 and response.json['pending_action'],response.json
    assert LocationTrap.query.count()==0 and character.data['inventory']['pockets']==before
    row=db.session.get(DeferredCombatAction,'trap-deferred');assert row.status=='ready'
    paid=actor.action_points_current
    resumed=client.post(url,headers=auth_headers(pair['actor_user']),json={
        'location_character_id':actor.id,'action_key':'trap','resume_pending_action_id':row.id,'attribute_choice':{'x':29}})
    assert resumed.status_code==200,resumed.json
    assert actor.action_points_current==paid and LocationTrap.query.one().x==5 and character.data['inventory']['pockets']==[]
    duplicate=client.post(url,headers=auth_headers(pair['actor_user']),json={
        'location_character_id':actor.id,'action_key':'trap','resume_pending_action_id':row.id})
    assert duplicate.status_code==409 and LocationTrap.query.count()==1
