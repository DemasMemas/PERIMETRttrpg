"""Действующие черты: стоимость, маршруты, медицинские процедуры и сохранение."""
from copy import deepcopy
from helpers.journal_assertions import assert_same_game_state
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import LocationCombatState
from app.services.character_creation import build_character
from app.services.character_traits import facial_profile
from app.services.combat import CombatService
from app.services.exceptions import ValidationError,ConflictError,NotFoundError
from test_character_creation import plan,trait
from test_grenade_rules import grenade_game
from test_consumable_use import pair


@pytest.mark.parametrize('intoxication,roll,expected',[(0,100,4),(60,40,4),(60,41,0),(100,1,0)])
def test_tolik_rolls_once_for_entire_pain_gain(monkeypatch,intoxication,roll,expected):
    from app.services.effects import apply_effect_to_health
    calls=[]
    monkeypatch.setattr('app.services.effects.random.randint',lambda a,b:(calls.append((a,b)) or roll))
    data={'basic':{'background':{'name':'Дядя Толик'}},'health':{'painLevel':0,'intoxication':intoxication}}
    apply_effect_to_health(data['health'],{'type':'pain','value':4},character_data=data)
    assert data['health']['painLevel']==expected
    assert len(calls)==(1 if 0<intoxication<100 else 0)


@pytest.mark.parametrize('posture,mode,goose,limit,cost',[
    ('standing','backward_sideways',False,10,4),('prone','walk',False,8,6),
    ('sitting','walk',False,10,4),('sitting','walk',True,20,2),('prone','walk',True,10,4),
])
def test_sprinter_doubles_posture_and_sideways_limits(posture,mode,goose,limit,cost):
    data={'basic':{'background':{'name':'Спринтер'}},'features':{'specialTraits':[{'name':'Гусь и гусеничка'}] if goose else []}}
    assert CombatService._movement_distance_limit(mode,posture,False,0,data)==limit
    assert CombatService._movement_route_cost({'distance':4,'cost':4},mode,posture,data)['movement_points']==cost


@pytest.mark.parametrize('trait_present,cost',[(False,4),(True,2)])
def test_standalone_evasion_cost_expiry_and_no_unreserved_offturn(grenade_game,trait_present,cost):
    from app.services.exceptions import PermissionDenied
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(actor.character.data);data['features']={'specialTraits':[{'name':'Уклонист'}] if trait_present else []}
    actor.character.data=data;db.session.commit()
    CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'evade')
    assert actor.action_points_current==20-cost
    assert actor.character.data['health']['combatMeta']['standaloneEvasion']==5
    state=db.session.query(LocationCombatState).filter_by(location_id=actor.location_id).one();state.current_location_character_id=target.id;db.session.commit()
    with pytest.raises(PermissionDenied):CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'evade')
    assert actor.character.data['health']['combatMeta']['standaloneEvasion']==5
    CombatService._prepare_character_for_turn(actor)
    assert 'standaloneEvasion' not in actor.character.data['health']['combatMeta']


def test_brigadier_order_is_paid_requires_response_and_consumed_once(grenade_game,monkeypatch):
    from app.services.character_interaction import CharacterInteractionService as I
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Бригадир'}};actor.character.data=data
    actor.team_name=target.team_name='Союз';db.session.commit()
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'brigadier_order',target_character_id=target.character_id,attribute_choice={'detailed':True,'text':'Проверь проход'})
    order=result['narrative_action']['order']
    assert actor.action_points_current==16 and order['status']=='pending'
    assert not target.character.data.get('health',{}).get('combatMeta',{}).get('brigadierOrder')
    I.respond(order['id'],grenade_game['lobby'].gm_id,'accept')
    rolls=iter([2,15,5]);monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:next(rolls))
    first=CombatService._narrative_skill_check(target.character.data,'skills.other.engineering')
    assert first['advantage'] and first['rolls']==[2,15]
    second=CombatService._narrative_skill_check(target.character.data,'skills.other.engineering')
    assert not second['advantage'] and second['rolls']==[5]
    with pytest.raises(NotFoundError):I.respond(order['id'],grenade_game['lobby'].gm_id,'accept')


def test_brigadier_refusal_is_not_forced_by_strength(grenade_game):
    from app.services.character_interaction import CharacterInteractionService as I
    from app.services.brigadier_order import refusal
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.character.data={**deepcopy(actor.character.data),'basic':{'background':{'name':'Бригадир'}}}
    actor.team_name=target.team_name='Союз';db.session.commit()
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'brigadier_order',target_character_id=target.character_id,attribute_choice={'detailed':False,'text':'Проверь проход'})
    response=I.respond(result['narrative_action']['order']['id'],grenade_game['lobby'].gm_id,'decline')
    assert response['status']=='rejected' and 'forced' not in response['result']
    assert refusal(actor,target.character_id,'provoke')['must_provoke']
    assert not refusal(actor,target.character_id,'provoked')['must_provoke']


def test_order_cancels_disadvantage_on_next_opposed_roll_with_one_die(monkeypatch):
    data={'skills':{'physical':{'strength':{'base':10}}},'health':{'combatMeta':{'brigadierOrder':{'advantage':True}}}}
    calls=[];monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:(calls.append(1) or 8))
    first=CombatService._opposed_roll(data,'skills.physical.strength',disadvantage=True)
    assert first['rolls']==[8] and first['total']==11 and len(calls)==1
    assert 'brigadierOrder' not in data['health']['combatMeta']


def test_scout_records_actual_targets_and_observer_bonus_next_battle(grenade_game,monkeypatch):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+4,actor.pos_y
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Наблюдатель'}}
    data['weapons']=[{'range':50}];actor.character.data=data;actor.drawn_weapon_index=0
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='idle';db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:20)
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'scout_location')
    assert result['narrative_action']['check']['success']
    assert [entry['character_id'] for entry in result['narrative_action']['targets']]==[target.character_id]
    CombatService.start_combat(actor.location_id,grenade_game['lobby'].gm_id,[actor.id,target.id])
    assert target.character_id in actor.character.data['health']['combatMeta']['observerTargets']
    assert 'locationScout' not in actor.character.data['health']['combatMeta']


def test_remote_scout_gm_point_without_placing_or_moving_character(grenade_game,client,auth_headers,monkeypatch):
    from app.services.location_scout import observation_point,remote_inspect
    from app.services.exceptions import PermissionDenied
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='idle'
    actor.character.data={**deepcopy(actor.character.data),'weapons':[{'name':'Оптика','range':200}], 'basic':{'background':{'name':'Наблюдатель'}}}
    target.pos_x,target.pos_y=12,5;target.gm_hidden=False;target.cover_object_id=None
    db.session.commit()
    point={'location_id':actor.location_id,'x':4,'y':5,'floor_level':0,'facing_x':1,'facing_y':0}
    url=f'/lobbies/characters/{actor.character_id}/location-scout'
    denied=client.put(url,json=point,headers=auth_headers(grenade_game['target_user']))
    assert denied.status_code==403
    response=client.put(url,json=point,headers=auth_headers(grenade_game['actor_user']))
    assert response.status_code==200
    assert observation_point(actor.character_id,actor.character.owner_id)['point']==point
    old=(actor.pos_x,actor.pos_y,actor.floor_level,actor.action_points_current,actor.drawn_weapon_index)
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:20)
    result=remote_inspect(actor.character_id,actor.character.owner_id,{'weapon_index':0})
    assert result['check']['success'] and [item['character_id'] for item in result['targets']]==[target.character_id]
    assert old==(actor.pos_x,actor.pos_y,actor.floor_level,actor.action_points_current,actor.drawn_weapon_index)
    target.pos_x=2;db.session.commit()
    assert remote_inspect(actor.character_id,actor.character.owner_id,{'weapon_index':0})['targets']==[]
    target.pos_x=12;target.gm_hidden=True;db.session.commit()
    assert remote_inspect(actor.character_id,actor.character.owner_id,{'weapon_index':0})['targets']==[]


def test_remote_scout_works_before_placement_and_does_not_allow_points_during_battle(grenade_game,monkeypatch):
    from app.services.location_scout import observation_point,remote_inspect
    from app.models import LocationCharacter
    actor=grenade_game['actor_location'];character=actor.character;location_id=actor.location_id
    state=LocationCombatState.query.filter_by(location_id=location_id).one()
    point={'location_id':location_id,'x':4,'y':5,'floor_level':0,'facing_x':1,'facing_y':0}
    with pytest.raises(ValidationError,match='до боя'):observation_point(character.id,grenade_game['lobby'].gm_id,point)
    state.status='idle';state.turn_order=[];state.current_location_character_id=None
    db.session.delete(actor);db.session.commit()
    observation_point(character.id,grenade_game['lobby'].gm_id,point)
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:20)
    result=remote_inspect(character.id,character.owner_id,{})
    assert result['point']==point and LocationCharacter.query.filter_by(character_id=character.id).count()==0


def test_scout_binocular_bonus_comes_from_catalog_not_item_fields(grenade_game,monkeypatch):
    from app.services.location_scout import inspect
    from app.models.templates import ItemTemplate
    actor=grenade_game['actor_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='idle'
    template=ItemTemplate(name='Бинокль с дальномером и увеличенной кратностью',category='misc',
        price=100,attributes={'scouting_bonus':25})
    db.session.add(template);db.session.flush()
    data=deepcopy(actor.character.data)
    data.setdefault('inventory',{})['pockets']=[{'id':'binocular','templateId':template.id,
        'name':template.name,'category':'misc','scouting_bonus':100,'attributes':{'scouting_bonus':100}}]
    actor.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:20)
    result=inspect(actor,{'item_path':['inventory','pockets',0]})
    assert result['check']['item_bonus']==25
    actor.character.data['inventory']['pockets'][0]['name']='Поддельный бинокль'
    with pytest.raises(ValidationError,match='из каталога'):
        inspect(actor,{'item_path':['inventory','pockets',0]})


def test_hunter_daily_stress_accumulates_is_locked_and_does_not_duplicate():
    from app.services.character_rules import advance_hunter_days
    from app.services.effects import apply_effect_to_health,sync_health_derived_statuses
    data={'basic':{'background':{'name':'Охотник'}},'health':{'stress':0}}
    advance_hunter_days(data,1,4)
    assert data['health']['stress']==3 and data['health']['combatMeta']['hunterStressDebt']==3
    advance_hunter_days(data,1,4);assert data['health']['stress']==3
    apply_effect_to_health(data['health'],{'type':'stress','value':-10});sync_health_derived_statuses(data['health'])
    assert data['health']['stress']==3
    data['health']['combatMeta']['hunterActivityDay']=4
    advance_hunter_days(data,4,5);assert data['health']['stress']==3


def test_grenadir_rolls_only_when_can_explode_and_explosion_clears_obligation(grenade_game,monkeypatch):
    from app.services.character_rules import start_turn,explosion_occurred
    actor=grenade_game['actor_location'];actor.character.data['basic']={'background':{'name':'Гренадир'}}
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:1)
    start_turn(actor)
    assert actor.character.data['health']['combatMeta']['grenadirMustExplode']
    explosion_occurred(actor.location_id)
    assert not actor.character.data['health']['combatMeta'].get('grenadirMustExplode')
    actor.character.data['health']['combatMeta'].pop('combatExplosionOccurred',None)
    actor.character.data['inventory']['pockets']=[]
    start_turn(actor)
    assert not actor.character.data['health']['combatMeta'].get('grenadirMustExplode')


def test_sprinter_cannot_end_while_can_spend_op_but_can_at_limit(grenade_game):
    from app.services.character_rules import before_end_turn
    actor=grenade_game['actor_location'];actor.character.data['basic']={'background':{'name':'Спринтер'}}
    actor.movement_mode_this_turn='sprint';actor.movement_points_current=5;actor.movement_points_max=10
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    with pytest.raises(ValidationError,match='все ОП'):before_end_turn(actor,state)
    actor.movement_distance_this_turn=30
    before_end_turn(actor,state)


def test_breakthrough_counts_approach_to_any_enemy_and_failed_will_does_not_reroll(grenade_game,monkeypatch):
    from app.services.character_rules import before_end_turn,record_approach
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.character.data['basic']={'background':{'name':'Прорывник'}}
    actor.movement_points_current=10;actor.movement_points_max=10
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:1)
    with pytest.raises(ValidationError,match='сближение'):before_end_turn(actor,state)
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:pytest.fail('Воля не перебрасывается кнопкой конца хода'))
    with pytest.raises(ValidationError,match='сближение'):before_end_turn(actor,state)
    record_approach(actor,{'path':[(2,5),(7,5)]})
    assert actor.character.data['health']['combatMeta']['breakthroughProgress']==5
    before_end_turn(actor,state)


def test_breakthrough_half_received_op_is_enough_on_expensive_ground(grenade_game,monkeypatch):
    from app.services.character_rules import before_end_turn,record_approach
    actor=grenade_game['actor_location'];actor.character.data['basic']={'background':{'name':'Прорывник'}}
    actor.movement_points_max=10;actor.movement_points_current=5
    record_approach(actor,{'path':[(2,5),(4.5,5)],'distance':2.5},spent_cost=5)
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:pytest.fail('Половина ОП уже потрачена на сближение'))
    before_end_turn(actor,LocationCombatState.query.filter_by(location_id=actor.location_id).one())


def test_breakthrough_cannot_spend_everything_away_before_rolling_will(grenade_game,monkeypatch):
    from app.services.character_rules import before_move
    actor=grenade_game['actor_location'];actor.character.data['basic']={'background':{'name':'Прорывник'}}
    actor.movement_points_max=10;actor.movement_points_current=10
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:1)
    with pytest.raises(ValidationError,match='в сторону противника'):
        before_move(actor,{'path':[(2.1,5.2),(1.1,5.2)],'distance':1},'backward_sideways')
    assert actor.movement_points_current==10


@pytest.mark.parametrize('category,falls',[('Лёгкая',True),('Тяжелая',False),('Экзоскелет',False)])
def test_heavy_load_respects_modification_category(grenade_game,monkeypatch,category,falls):
    from app.services.character_rules import shotgun_effects
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.character.data['features']={'specialTraits':[{'name':'Тяжелая навеска'}]}
    target.character.data['equipment']={'armor':{'attributes':{'modification_category':category}}}
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:1)
    outcome=shotgun_effects(actor,target,{'subcategory':'Дробовики'},{'caliber':'12*70'},'chest',100,10)
    assert bool(outcome.get('heavy_load'))==falls
    assert (target.posture=='prone')==falls


def test_torturer_scream_reaches_all_not_deaf_without_visibility(grenade_game,monkeypatch):
    from app.services.character_rules import shotgun_effects
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.character.data['features']={'specialTraits':[{'name':'Чернобыльский пыточник'}]}
    target.character.data['health']['zones']={'leftArm':{'current':0,'max':100}}
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:1)
    heard=shotgun_effects(actor,target,{'subcategory':'Дробовики'},{},'left_arm',100,20)['torturer_scream']
    assert [entry['character_id'] for entry in heard]==[target.character_id]
    target.character.data['health']['deafness']=90
    assert shotgun_effects(actor,target,{'subcategory':'Дробовики'},{},'left_arm',100,20)['torturer_scream']==[]


@pytest.mark.parametrize('condition',['dead','critical','pain_shock','active'])
def test_torturer_checks_victim_after_damage(grenade_game,monkeypatch,condition):
    from app.services.character_rules import shotgun_effects
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.character.data['features']={'specialTraits':[{'name':'Чернобыльский пыточник'}]}
    target.character.data['health']['zones']={'leftArm':{'current':0,'max':100}}
    original=CombatService._character_condition
    monkeypatch.setattr(CombatService,'_character_condition',lambda data:{'state':condition} if data is target.character.data else original(data))
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:1)
    outcome=shotgun_effects(actor,target,{'subcategory':'Дробовики'},{},'left_arm',100,20)
    assert ('torturer_scream' in outcome)==(condition in {'active','pain_shock'})


def sniper_position_case(case):
    from app.models import LocationObject
    from app.services.character_rules import meta
    actor=case['actor_location'];state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    state.status='active';state.round_number=4;state.turn_order=[actor.id,case['target_location'].id];state.current_location_character_id=actor.id
    actor.character.data['basic']={'background':{'name':'Снайпер'}}
    meta(actor)['sniperOldCover']={'id':None,'pending':True,'expires_after_round':5}
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(actor.character,'data')
    cover=LocationObject(location_id=actor.location_id,type='cover',tile_x=8,tile_y=5,properties={'dimensions':{'width':1,'depth':1,'height':2}})
    db.session.add(cover);db.session.commit()
    return actor,state,cover


def test_sniper_blocks_fire_without_charging_and_accepts_new_cover(grenade_game):
    from app.services.sniper_position import status,require_reposition
    actor,state,cover=sniper_position_case(grenade_game)
    hint=status(actor,state)
    assert hint['blocked'] and hint['cover']['object_id']==cover.id and hint['expires_after_round']==5
    before=deepcopy(actor.character.data);ap=actor.action_points_current
    with pytest.raises(ValidationError,match='перед выстрелом'):
        CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',fire_mode='unaimed',action_points=2)
    assert actor.action_points_current==ap
    assert_same_game_state(actor.character.data,before)
    actor.cover_object_id=cover.id
    require_reposition(actor,state)
    assert 'sniperOldCover' not in actor.character.data['health']['combatMeta']


def test_sniper_expiry_is_end_of_next_round_or_combat_end(grenade_game):
    from app.services.sniper_position import expire,status
    from app.services.character_rules import meta
    actor,state,cover=sniper_position_case(grenade_game)
    state.round_number=5;expire(actor.location_id,state)
    assert status(actor,state)['blocked']
    state.round_number=6;expire(actor.location_id,state)
    assert status(actor,state) is None
    meta(actor)['sniperOldCover']={'id':None,'pending':True,'expires_after_round':7}
    CombatService.end_combat(actor.location_id,grenade_game['lobby'].gm_id)
    assert 'sniperOldCover' not in meta(actor)


def test_sniper_uses_full_run_or_breathless_walk_distance(grenade_game):
    from app.services.sniper_position import status
    actor,state,cover=sniper_position_case(grenade_game)
    cover.tile_x=17;actor.movement_points_current=0;actor.movement_distance_this_turn=20
    db.session.commit()
    assert status(actor,state)['blocked']
    actor.strenuous_movement_blocked_until_round=4
    assert not status(actor,state)['blocked'] and status(actor,state)['mode']=='walk'
    actor.cover_object_id=cover.id;actor.character.data['health']['combatMeta']['sniperOldCover']['id']=cover.id
    assert not status(actor,state)['blocked']


def test_sniper_expiry_follows_real_turn_order(grenade_game):
    from app.services.character_rules import meta
    actor,state,_=sniper_position_case(grenade_game);other=grenade_game['target_location']
    gm=grenade_game['lobby'].gm_id
    for participant in (actor,other):CombatService.end_turn(actor.location_id,gm,participant.id)
    assert state.round_number==5 and meta(actor)['sniperOldCover']['pending']
    for participant in (actor,other):CombatService.end_turn(actor.location_id,gm,participant.id)
    assert state.round_number==6 and 'sniperOldCover' not in meta(actor)


def test_sniper_failed_kill_check_records_following_round(grenade_game,monkeypatch):
    from app.services.character_rules import after_kill,meta
    actor,state,_=sniper_position_case(grenade_game);target=grenade_game['target_location']
    target.character.data['health']['current']=0
    monkeypatch.setattr(CombatService,'_character_condition',lambda data:{'state':'dead'} if data is target.character.data else {'state':'active'})
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:1)
    after_kill(actor,target,True)
    assert meta(actor)['sniperOldCover']['expires_after_round']==5


@pytest.mark.parametrize('source',['team','world_group'])
def test_firefighter_straight_radius_requires_two_actual_allies(grenade_game,source):
    from app.models import LobbyCharacter,LocationCharacter,WorldGroup
    from app.services.character_rules import refresh_nearby
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.character.data={**deepcopy(actor.character.data),'basic':{'background':{'name':'Пожарный'}}}
    actor.team_name=target.team_name='Один отряд';target.pos_x,target.pos_y=actor.pos_x+2,actor.pos_y
    third=LobbyCharacter(lobby_id=actor.character.lobby_id,owner_id=actor.character.owner_id,name='Союзник',data=deepcopy(target.character.data));db.session.add(third);db.session.flush()
    ally=LocationCharacter(character_id=third.id,location_id=actor.location_id,pos_x=actor.pos_x+3,pos_y=actor.pos_y,team_name='Один отряд');db.session.add(ally)
    if source=='world_group':
        actor.team_name=target.team_name=ally.team_name=None
        db.session.add(WorldGroup(lobby_id=actor.character.lobby_id,name='Группа',tile_x=0,tile_y=0,created_by=grenade_game['lobby'].gm_id,member_character_ids=[actor.character_id,target.character_id,third.id]))
    db.session.commit()
    refresh_nearby(actor.location_id)
    assert CombatService._skill_value(actor.character.data,'skills.physical.strength')==12
    ally.floor_level=7;refresh_nearby(actor.location_id)
    assert CombatService._skill_value(actor.character.data,'skills.physical.strength')==10
    ally.floor_level=0
    ally.pos_x=actor.pos_x+16;refresh_nearby(actor.location_id)
    assert CombatService._skill_value(actor.character.data,'skills.physical.strength')==10


def test_pacifist_failure_spends_ap_without_ammo_then_allows_retry(grenade_game,monkeypatch):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+3,actor.pos_y
    actor.character.data={**deepcopy(actor.character.data),'basic':{'background':{'name':'Истинный пацифист'}},
        'weapons':[{'id':'rifle','category':'weapon','subcategory':'Винтовки','accuracy':100,'ergonomics':100,'damage':1,'range':20,'ammo':10,'attributes':{'damage':1,'range':20,'min_strength':0}}]}
    actor.drawn_weapon_index=0;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:1)
    def attack():return CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',weapon_index=0,fire_mode='unaimed',shot_count=1,action_points=2,target_character_id=target.character_id)
    before=actor.action_points_current;failed=attack()
    assert failed['narrative_action']['blocked_by_will'] and actor.action_points_current<before
    assert actor.character.data['weapons'][0]['ammo']==10
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:20)
    success=attack();assert success['attack']['hits']==1
    assert actor.character.data['weapons'][0]['ammo']==9
    assert target.character_id in actor.character.data['health']['combatMeta']['harmAllowedTargets']


def test_tolik_ignores_delayed_and_periodic_pain_too():
    from app.services.effects import apply_periodic_effects_to_health,advance_timed_effects
    data={'basic':{'background':{'name':'Дядя Толик'}},'health':{'painLevel':0,'intoxication':100}}
    effect={'type':'periodic_adjustment','remaining':1,'adjustments':[{'field':'painLevel','delta':4}]}
    apply_periodic_effects_to_health(data['health'],[effect],character_data=data)
    assert data['health']['painLevel']==0
    advance_timed_effects(data['health'],[{'type':'delayed_adjustment','remaining':1,'tick':'time_elapsed','time_unit':'minute','adjustments':[{'field':'painLevel','delta':3}]}],60,character_data=data)
    assert data['health']['painLevel']==0


def test_hunter_gets_credit_for_actual_fire_finisher_once(grenade_game,monkeypatch):
    from app.services.burning import ignite,tick
    from app.services.character_rules import advance_hunter_days
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.character.data={**deepcopy(actor.character.data),'basic':{'background':{'name':'Охотник'}}}
    target.character.data={**deepcopy(target.character.data),'is_mutant':True,'health':{'current':1,'max':700,'effects':[]}}
    ignite(target,5,source='Молотов',source_character_id=actor.character_id)
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:b)
    tick(target,1)
    assert CombatService._character_condition(target.character.data)['state']=='dead'
    assert actor.character.data['health']['combatMeta']['hunterActivityDay']==1
    advance_hunter_days(actor.character.data,1,2)
    assert actor.character.data['health']['stress']==0


def test_scout_cannot_reveal_territory_while_fully_blind(grenade_game,monkeypatch):
    from app.services.location_scout import inspect
    actor=grenade_game['actor_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='idle'
    actor.character.data['health']['effects']=[{'type':'blindness','value':100}]
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:pytest.fail('Слепой не бросает осмотр'))
    with pytest.raises(ValidationError,match='видеть'):inspect(actor,{})


def test_explicit_opposing_teams_override_old_world_group(grenade_game):
    from app.models import WorldGroup
    from app.services.character_rules import allied_ids
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    actor.team_name='Красные';target.team_name='Синие'
    db.session.add(WorldGroup(lobby_id=actor.character.lobby_id,name='Прежняя группа',tile_x=0,tile_y=0,created_by=grenade_game['lobby'].gm_id,member_character_ids=[actor.character_id,target.character_id]));db.session.commit()
    assert target.character_id not in allied_ids(actor)


def test_hunter_completed_butchering_removes_all_debt_not_other_stress(grenade_game,monkeypatch):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='idle'
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Охотник'}}
    data['weapons']=[{'id':'knife','name':'Охотничий нож','category':'melee_weapon','attributes':{'damage':20,'armor_piercing':100,'allowed_attacks':['Колющий']}}]
    data['health']['stress']=6;data['health']['combatMeta']={'hunterStressDebt':4}
    actor.character.data=data;actor.drawn_weapon_index=0
    target.character.data={**deepcopy(target.character.data),'is_mutant':True,'basic':{'mutant_type':'Кабан'},'health':{'current':0,'max':700,'effects':[{'type':'death'}]}}
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:20)
    CombatService.butcher_mutant(actor.location_id,grenade_game['lobby'].gm_id,actor.id,target.character_id)
    assert actor.character.data['health']['stress']==6
    result=CombatService.butcher_mutant(actor.location_id,grenade_game['lobby'].gm_id,actor.id,target.character_id,allocation={})
    assert result['completed'] and actor.character.data['health']['stress']==2
    assert not actor.character.data['health']['combatMeta'].get('hunterStressDebt')


def test_helper_deferred_payment_clears_penalty_only_when_fully_paid(grenade_game):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Помощник'}}
    actor.character.data=data;actor.action_points_current=1
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.round_number=4
    db.session.commit();CombatService._update_helper_penalties(state)
    CombatService.reserve_reaction(actor.location_id,grenade_game['actor_user']['id'],actor.id,action_points=4,
        kind='help',help_target_character_id=target.character_id,help_action_label='Действие',help_skill_path='skills.other.engineering')
    assert CombatService._health_roll_modifier(actor.character.data,'skills.other.engineering')==-3
    actor.action_points_current=2;CombatService._pay_turn_obligations(actor)
    assert CombatService._health_roll_modifier(actor.character.data,'skills.other.engineering')==-3
    actor.action_points_current=2;CombatService._pay_turn_obligations(actor)
    assert actor.action_points_current==1
    assert actor.character.data['health']['combatMeta']['lastPaidHelpRound']==4
    assert CombatService._health_roll_modifier(actor.character.data,'skills.other.engineering')==0


def test_polished_machine_gun_in_nested_backpack_restores_half_actual_loss(grenade_game):
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['features']={'specialTraits':[{'name':'Протёрт до блеска'}]}
    gun={'id':'machine-gun','category':'weapon','subcategory':'Пулеметы','durability':100,'maxDurability':100}
    CombatService._apply_weapon_wear(gun,23)
    data['weapons']=[];data['inventory']['backpack']=[{'name':'Кейс','contents':[gun]}]
    actor.character.data=data;db.session.commit()
    CombatService.end_combat(actor.location_id,grenade_game['lobby'].gm_id)
    saved=actor.character.data['inventory']['backpack'][0]['contents'][0]
    assert saved['durability']==88 and '_combatDurabilityLoss' not in saved


def test_fast_hands_reduces_fixed_weapon_loading_but_not_inventory_magazines(app):
    from app.services.ammo_loading import load_ammunition
    data={'features':{'specialTraits':[{'name':'Быстрые руки'}]},'inventory':{'pockets':[
        {'id':'ammo','name':'Патрон','category':'ammo','quantity':6,'attributes':{'caliber':'9x19'}}]},
        'weapons':[{'id':'gun','name':'Оружие','subcategory':'Пистолеты','attributes':{'caliber':'9x19','fixedMagazine':True,'magazine_size':6}}],
        'combatMagazineLoading':{'targetType':'fixed','weaponIndex':0,'sourceId':'ammo'}}
    request={'source_path':['inventory','pockets',0],'weapon_index':0,'quantity':1,'action_points':1,'free_actions':0}
    result=load_ammunition(data,request,combat=True)
    assert result['quantity']==1 and data['inventory']['pockets'][0]['quantity']==5
    data['inventory']['pockets'].append({'id':'mag','name':'Магазин','category':'magazine','quantity':1,'attributes':{'caliber':'9x19','capacity':10},'ammo':[]})
    data['combatMagazineLoading']={'targetType':'inventory','targetId':'mag','sourceId':'ammo'}
    request.pop('weapon_index');request.update(target_path=['inventory','pockets',1],quantity=3)
    with pytest.raises(ValidationError,match='оплачена'):load_ammunition(data,request,combat=True)


def test_fast_hands_reduces_underbarrel_reload_without_discounting_retrieval(grenade_game):
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['features']={'specialTraits':[{'name':'Быстрые руки'}]}
    data['weapons']=[{'id':'rifle','installedModules':[{'attributes':{'type':'grenade_launcher','caliber':'ВОГ-25'}}]}]
    data['inventory']['pockets']=[{'id':'round','name':'ВОГ-25','category':'grenade','quantity':1,'attributes':{'caliber':'ВОГ-25'}}]
    actor.character.data=data;db.session.commit()
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'reload_underbarrel',
        weapon_index=0,item_path=['inventory','pockets',0])
    assert result['reload_underbarrel']['action_points']==5
    assert result['reload_underbarrel']['inventory_retrieval_action_points']==1


@pytest.mark.parametrize('posture,mode,cost,limit',[
    ('sitting','walk',4,10),('sitting','run',2,20),('prone','walk',8,5),
])
def test_goose_changes_both_route_cost_and_distance_limit(posture,mode,cost,limit):
    data={'features':{'specialTraits':[{'name':'Гусь и гусеничка'}]}}
    CombatService._validate_posture_movement(posture,mode,data)
    assert CombatService._movement_route_cost({'distance':4,'cost':4},mode,posture,data)['movement_points']==cost
    assert CombatService._movement_distance_limit(mode,posture,False,0,data)==limit


@pytest.mark.parametrize('mode',['walk','run'])
def test_sprinter_moves_on_ice_without_ap_or_slip_checks(grenade_game,monkeypatch,mode):
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['basic']={'background':{'name':'Спринтер'}};actor.character.data=data
    actor.action_points_current=0;actor.movement_points_current=10
    location=grenade_game['location'];location.tiles_data=[[{'terrain':'ice','height':1} for _ in range(30)] for _ in range(30)]
    db.session.commit()
    monkeypatch.setattr('app.services.slippery_movement._check',lambda *args:pytest.fail('Спринтер не скользит при своей ходьбе'))
    CombatService.move_character(actor.location_id,grenade_game['lobby'].gm_id,actor.character_id,10.1,5.2,movement_mode=mode)
    assert actor.pos_x==pytest.approx(10.1) and actor.action_points_current==0
    assert actor.movement_points_current==pytest.approx(2)  # лёд — сложная местность: 8 м ×2 /2
    assert not actor.strenuous_movement_blocked_until_round
    assert CombatService._movement_distance_limit(mode,'standing',False,0,data)==20
    assert CombatService._shooting_movement_modifiers('run',character_data=data)['difficulty_penalty']==1


def test_breakthrough_requires_appropriate_weapon_actually_drawn(app):
    data=build_character('Имя',plan('Прорывник'))
    data['weapons']=[{'subcategory':'Штурмовые винтовки'},{'subcategory':'Пистолеты'}]
    actor=SimpleNamespace(character=SimpleNamespace(data=data),drawn_weapon_index=None,posture='standing')
    base=CombatService._movement_penalty(actor)
    actor.drawn_weapon_index=0;assert CombatService._movement_penalty(actor)==base-2
    actor.drawn_weapon_index=1;assert CombatService._movement_penalty(actor)==base


def test_facial_damage_changes_level_bonus_and_rolls_without_double_count(monkeypatch):
    data=build_character('Имя',plan())
    data['skills']['social']['charisma']['base']=15
    data['health'].update(organs={'leftEye':{'current':0},'nose':{'current':0}},effects=[{'type':'organ_loss','area':'leftEye'}])
    assert facial_profile(data)['charisma_level']==-10
    assert CombatService._skill_value(data,'skills.social.charisma')==5
    assert CombatService._health_roll_modifier(data,'skills.physical.shooting')==-3
    data['features']['specialTraits']=[{'name':'Вечно красив'}]
    assert facial_profile(data)['charisma_bonus']==2
    assert CombatService._base_skill_modifier(data,'skills.social.charisma')==4
    data['health']['organs'].update(leftEar={'current':0},rightEar={'current':0},rightEye={'current':0})
    monkeypatch.setattr('app.services.combat.random.randint',lambda *args:pytest.fail('Без ушей нет броска слуха'))
    assert CombatService._narrative_skill_check(data,'skills.physical.awareness.hearing')['automatic_failure']
    assert CombatService._health_roll_modifier(data,'skills.physical.melee')==-5
    assert CombatService._health_roll_modifier(data,'skills.physical.agility')==-3


def test_welder_glasses_replace_penalty_only_for_welder(monkeypatch):
    data=build_character('Имя',plan())
    data['equipment']={'glasses':{'name':'Очки Сварщика','charismaBonus':-1}}
    monkeypatch.setattr('app.services.combat.random.randint',lambda *args:10)
    assert CombatService._narrative_skill_check(data,'skills.social.charisma')['equipment_modifier']==-1
    data['features']['specialTraits']=[{'name':'Сварщик'}]
    assert CombatService._narrative_skill_check(data,'skills.social.charisma')['equipment_modifier']==1


@pytest.mark.parametrize('count,reduction',[(1,4),(2,6)])
def test_fine_work_doubles_payment_and_reduces_real_treatment_dc(client,pair,auth_headers,monkeypatch,count,reduction):
    from test_medical_procedure import install_item,selected_bleeding,payload,apply
    effect={'id':'bleed','type':'bleeding_external_light','area':'leftArm'}
    template,profile,item=install_item(pair,'Бинт','Останавливает Слабое кровотечение.',effects=[effect])
    profile['direct'].update(treatment_action_points=4,treatment_time_uses_medicine=False)
    template.attributes={**template.attributes,'consumable':profile}
    data=deepcopy(pair['actor_character'].data);data['features']={'specialTraits':[{'name':'Тонкая работа'}]*count}
    pair['actor_character'].data=data;db.session.commit()
    application={**selected_bleeding(profile,effect),'fineWork':True,'actionPoints':8}
    monkeypatch.setattr('app.services.medical_procedure.random.randint',lambda *args:20)
    result=apply(client,pair,auth_headers,payload(pair,item,application))
    assert result.status_code==200,result.json
    assert result.json['medical_result']['base_difficulty']==8-reduction
    assert result.json['medical_result']['payment_details']['treatment_action_points']==8


def surgeon_case(case,*,self_treatment=False):
    actor=case['actor_location'];target=actor if self_treatment else case['target_location']
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Хирург'}};actor.character.data=data
    target.pos_x,target.pos_y=actor.pos_x+int(target!=actor),actor.pos_y
    data=deepcopy(target.character.data)
    data['health'].update(painLevel=0,zones={'leftArm':{'current':0,'max':100}})
    target.character.data=data;db.session.commit()
    consent=None
    if target!=actor:
        from app.services.character_interaction import CharacterInteractionService as Interaction
        request=Interaction.create_request(actor.location_id,case['lobby'].gm_id,actor.id,target.character_id,'treatment')
        Interaction.respond(request['id'],case['target_user']['id'],'accept');consent=request['id']
    return actor,target,{'target_character_id':target.character_id,'target_zone':'leftArm','attribute_choice':{'consentId':consent}}


@pytest.mark.parametrize('self_treatment',[True,False])
def test_surgeon_no_kit_no_dice_restores_one_hp_with_consent_and_patient_cooldown(grenade_game,monkeypatch,self_treatment):
    actor,target,arguments=surgeon_case(grenade_game,self_treatment=self_treatment)
    before=target.character.data['health']['current'];ap=actor.action_points_current
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:pytest.fail('Хирург не делает бросок'))
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'surgeon_reduction',**arguments)
    assert result['narrative_action']['roll_required'] is False
    assert actor.action_points_current==ap-2
    health=target.character.data['health']
    assert health['zones']['leftArm']['current']==1 and health['current']==min(health['max'],before+1)
    assert health['painLevel']==5 and health['combatMeta']['surgeonReductions']['leftArm']
    data=deepcopy(target.character.data);data['health']['zones']['leftArm']['current']=0;target.character.data=data;db.session.commit()
    with pytest.raises(ValidationError,match='8-часового'):CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'surgeon_reduction',**arguments)
    assert actor.action_points_current==ap-2


def test_surgeon_deferred_cancels_if_patient_walked_away(grenade_game):
    from app.models import DeferredCombatAction
    actor,target,arguments=surgeon_case(grenade_game)
    actor.action_points_current=1;db.session.commit()
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'surgeon_reduction',pending_action_id='surgeon-away',**arguments)
    assert result['pending_action'] is True
    target.pos_x+=5;db.session.commit()
    CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,target.id)
    assert db.session.get(DeferredCombatAction,'surgeon-away').status=='cancelled'
    assert target.character.data['health']['zones']['leftArm']['current']==0
    assert actor.action_points_current==5


def test_surgeon_deferred_executes_once_after_payment(grenade_game):
    from app.models import DeferredCombatAction
    actor,target,arguments=surgeon_case(grenade_game)
    actor.action_points_current=1;db.session.commit()
    CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'surgeon_reduction',pending_action_id='surgeon-ready',**arguments)
    CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,target.id)
    assert db.session.get(DeferredCombatAction,'surgeon-ready').status=='ready'
    CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'surgeon_reduction',resume_pending_action_id='surgeon-ready',**arguments)
    assert target.character.data['health']['zones']['leftArm']['current']==1
    assert db.session.get(DeferredCombatAction,'surgeon-ready').status=='completed'
    with pytest.raises(ConflictError):CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'surgeon_reduction',resume_pending_action_id='surgeon-ready',**arguments)
    assert target.character.data['health']['painLevel']==5


@pytest.mark.parametrize('background,rest_type,healing,alcohol,pain,cooldown',[
    ('Атлет','rest',52,80,0,True),('Атлет','sleep',525,5,0,False),
    ('Дядя Толик','sleep',350,55,1,False),
])
def test_rest_applies_traits_and_patient_surgeon_reset(client,grenade_game,auth_headers,background,rest_type,healing,alcohol,pain,cooldown):
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['basic']={'background':{'name':background}}
    data['health'].update(current=100,max=700,intoxication=80,painLevel=0,combatMeta={'surgeonReductions':{'leftArm':True}},needs={'mealsToday':3,'drinksToday':3})
    actor.character.data=data;actor.character.time_active=True;db.session.commit()
    response=client.post(f"/lobbies/{grenade_game['lobby'].id}/rest",headers=auth_headers(grenade_game['actor_user']),json={'type':rest_type,'character_ids':[actor.character_id]})
    assert response.status_code==200,response.json
    health=actor.character.data['health']
    assert health['current']==100+healing and health['intoxication']==alcohol and health['painLevel']==pain
    assert bool(health['combatMeta'].get('surgeonReductions'))==cooldown


def test_cook_double_exhaustion_only_for_food_and_water():
    from app.lobbies import _resolve_character_day
    health={'exhaustion':0,'needs':{}}
    _resolve_character_day(health,[],slept=False,character_data={'basic':{'background':{'name':'Повар'}}})
    assert health['exhaustion']==5


@pytest.mark.parametrize('visible,mutant,expected',[(True,False,2),(False,False,0),(True,True,0)])
def test_death_extra_stress_requires_visible_human_and_is_not_repeated(grenade_game,monkeypatch,visible,mutant,expected):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Математик'}}
    data['features']={'specialTraits':[{'name':'Поставлю за них свечку'}]};actor.character.data=data
    data=deepcopy(target.character.data);data['is_mutant']=mutant
    data['health']['effects']=[{'type':'death'}];target.character.data=data;db.session.commit()
    monkeypatch.setattr(CombatService,'_can_see_location_character',lambda *a:visible)
    draws=[];monkeypatch.setattr(CombatService,'apply_stress_trigger',lambda observer,amount,**kw:draws.append((observer.id,amount)))
    CombatService._notify_observed_death(target);CombatService._sync_location_effects_from_character(target)
    assert sum(amount for observer,amount in draws if observer==actor.id)==expected


def test_selected_owned_weapons_change_accuracy_per_target_without_transfer(app):
    from app.services.character_traits import selected_weapon_accuracy,validate_target_weapons
    data={'basic':{'background':{'name':'Один для бандитов\nВторой для мутантов'}},
        'weapons':[{'id':'one'},{'id':'two'}],'ruleChoices':{'targetWeapons':{'human':'one','mutant':'two'}}}
    validate_target_weapons({},data)
    assert [selected_weapon_accuracy(data,weapon,{'is_mutant':False}) for weapon in data['weapons']]==[2,-5]
    assert [selected_weapon_accuracy(data,weapon,{'is_mutant':True}) for weapon in data['weapons']]==[-5,2]
    assert selected_weapon_accuracy({},data['weapons'][0],{})==0
    invalid=deepcopy(data);invalid['ruleChoices']['targetWeapons']['human']='other-person-gun'
    with pytest.raises(ValidationError,match='собственное'):validate_target_weapons(data,invalid)
    invalid['ruleChoices']['targetWeapons']['human']='two'
    with pytest.raises(ValidationError,match='разных'):validate_target_weapons(data,invalid)


@pytest.mark.parametrize('volley_count,roll,expected',[(1,2,1),(2,2,2),(1,20,6)])
def test_post_bonus_one_per_volley_even_all_missed_without_extra_bullets(grenade_game,monkeypatch,volley_count,roll,expected):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Пост принял'}}
    data['weapons']=[{'id':'rifle','damage':1,'durability':100,'maxDurability':100}];actor.character.data=data;db.session.commit()
    details={'weapon_index':0,'fire_mode':'burst','shot_count':6,'volley_count':volley_count,'hit_difficulty':19,'round_number':1}
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:roll)
    results=CombatService._resolve_shot_sequence([target],actor,details)
    assert len(results)==6 and sum(bool(item['hit']) for item in results)==expected
    assert sum(bool(item.get('post_bonus_hit')) for item in results)==(volley_count if roll==2 else 0)


def test_fencer_optional_discount_once_per_turn_and_heavy_accuracy(grenade_game,monkeypatch):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Фехтовальщик'}}
    data['weapons']=[{'id':'axe','category':'melee_weapon','attributes':{'damage':1,'weight_class':'heavy','allowed_attacks':['Рубящий']}}]
    actor.character.data=data;actor.drawn_weapon_index=0;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:2)
    def hit(**kw):return CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',weapon_index=0,attack_type='Рубящий',target_character_id=target.character_id,**kw)
    before=actor.action_points_current
    discounted=hit(payment='fencer')['attack']
    assert discounted['action_points']==1 and actor.action_points_current==before-1
    assert actor.character.data['health']['combatMeta']['fencerUsed']
    with pytest.raises(ValidationError,match='недоступен'):hit(payment='fencer')
    normal=hit()['attack'];assert normal['action_points']==3
    assert normal['weapon_accuracy']==-2 and normal['hits']==0


def sniper_case(case,monkeypatch,mode='precision',damage=50,zone='chest'):
    actor,target=case['actor_location'],case['target_location']
    target.pos_x,target.pos_y=actor.pos_x+3,actor.pos_y
    data=deepcopy(actor.character.data)
    data['features']={'specialTraits':[{'name':'Микроскопическая точность' if mode=='precision' else 'Снайперское милосердие'}]}
    data['weapons']=[{'id':'sniper','category':'weapon','subcategory':'Снайперские винтовки','accuracy':100,'ergonomics':100,
        'durability':100,'maxDurability':100,'ammo':5,'fixedMagazine':True,'attributes':{'damage':damage,'armor_piercing':100,'range':20,'min_strength':0}}]
    actor.character.data=data;actor.drawn_weapon_index=0;actor.aimed_weapon_index=0;actor.aimed_target_character_id=target.character_id
    data=deepcopy(target.character.data);data['health'].update(current=700,max=700,painLevel=0,zones={'head':{'max':100,'current':100},'chest':{'max':200,'current':200}})
    target.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:100 if high==100 else min(high,10))
    cost=CombatService._weapon_ergonomics_profile(actor,actor.character.data['weapons'][0],0)['aimed_shot_action_points']+int(mode=='precision')
    result=CombatService.perform_action(actor.location_id,case['lobby'].gm_id,actor.id,'attack',weapon_index=0,fire_mode='aimed',shot_count=1,action_points=cost,
        target_character_id=target.character_id,target_zone=zone,attribute_choice={'sniperMode':mode})
    return actor,target,result,cost


def test_microscopic_choice_is_saved_frozen_and_applied_once_without_another_shot(grenade_game,monkeypatch):
    from app.services.exceptions import PermissionDenied
    actor,target,result,cost=sniper_case(grenade_game,monkeypatch)
    assert actor.action_points_current==20-cost
    choice=result['character']['sniper_trauma_choices'][0]
    assert [card['roll'] for card in choice['cards']]==[9,10,11]
    before=deepcopy(target.character.data['health']);assert before['painLevel']==1
    assert before['lastTrauma']['pending']
    for operation in (
        lambda:CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,actor.id),
        lambda:CombatService.end_combat(actor.location_id,grenade_game['lobby'].gm_id),
        lambda:CombatService.move_character(actor.location_id,grenade_game['lobby'].gm_id,actor.character_id,actor.pos_x+1,actor.pos_y),
    ):
        with pytest.raises(ConflictError,match='травму'):operation()
    choice_args={'attribute_choice':{'id':choice['id'],'index':1}}
    with pytest.raises(PermissionDenied):CombatService.perform_action(actor.location_id,grenade_game['target_user']['id'],actor.id,'choose_sniper_trauma',**choice_args)
    db.session.expire_all()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:pytest.fail('Выбор карточки не перебрасывает кубы'))
    selected=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'choose_sniper_trauma',**choice_args)
    assert selected['narrative_action']['trauma']['roll']==10
    assert target.character.data['health']['painLevel']==4
    assert actor.character.data['weapons'][0]['ammo']==4 and actor.action_points_current==20-cost
    assert selected['character']['sniper_trauma_choices']==[]
    with pytest.raises(ConflictError):CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'choose_sniper_trauma',**choice_args)
    assert target.character.data['health']['painLevel']==4


def test_sniper_mercy_can_disable_head_without_damaging_skull_brain_or_organs(grenade_game,monkeypatch):
    actor,target,result,cost=sniper_case(grenade_game,monkeypatch,mode='mercy',damage=120,zone='head')
    health=target.character.data['health']
    assert health['zones']['head']['current']==0
    assert not health.get('lastTraumas') and not result['character']['sniper_trauma_choices']
    assert all(organ.get('current',1)>0 for organ in health.get('organs',{}).values())
    assert CombatService._character_condition(target.character.data)['state']!='dead'


@pytest.mark.parametrize('original,rolls,index,selected',[(1,[20,1,2],0,20),(20,[19,20,1],2,1)])
def test_microscopic_table_wraps_at_both_ends_and_applies_wrapped_card(grenade_game,monkeypatch,original,rolls,index,selected):
    from app.services.sniper_trauma import enqueue,choices,decide
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(target.character.data);data['health'].update(current=700,max=700,zones={'chest':{'max':200,'current':200}})
    target.character.data=data;db.session.commit()
    enqueue(actor,target,{'area':'chest','roll':original,'chance_roll':100},20,'chest',True,True)
    db.session.commit();choice=choices(actor.character.data)[0]
    assert [card['roll'] for card in choice['cards']]==rolls
    assert len(choice['cards'])==3
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:pytest.fail('Выбранная травма не перебрасывается'))
    result=decide(actor,grenade_game['lobby'].gm_id,{'id':choice['id'],'index':index})
    assert result['trauma']['roll']==selected
    assert result['trauma']['organ']==('spine' if selected==20 else 'heart')


def social_case(case,background='',traits=()):
    actor=case['actor_location'];data=deepcopy(actor.character.data)
    data['basic']={'background':{'name':background}}
    data['features']={'specialTraits':[{'name':name} for name in traits]}
    data['skills']['social']={name:{'base':10} for name in ('charisma','barter','persuasion','deception','intimidation')}
    actor.character.data=data;db.session.commit()
    return actor,case['target_location']


def social_roll(case,*,path='persuasion',difficulty=10,operation='social-check',**extra):
    from app.services.social_check import run
    return run(case['actor_character'].id,case['lobby'].gm_id,{'skill_path':f'skills.social.{path}',
        'target_character_id':case['target_character'].id,'difficulty':difficulty,'operation_id':operation,**extra})


def test_social_tags_apply_charisma_levels_not_flat_roll_and_leave_skills_intact(grenade_game,monkeypatch):
    actor,target=social_case(grenade_game,traits=['Блатной жаргон','Цель номер один'])
    data=deepcopy(target.character.data);data['basic']={'tags':['Бандит']};target.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    covered=social_roll(grenade_game,face_visible=False)
    exposed=social_roll(grenade_game,face_visible=True,operation='social-face')
    assert covered['check']['related_modifier']==1 and exposed['check']['related_modifier']==-5
    assert actor.character.data['skills']['social']['charisma']=={'base':10}


def test_balabol_memory_is_per_skill_target_capped_and_expires_from_last_dialog(grenade_game,monkeypatch):
    actor,target=social_case(grenade_game,'Балабол')
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    totals=[social_roll(grenade_game,difficulty=5,operation=f'chat-{i}')['check']['total'] for i in range(6)]
    assert totals==[10,11,12,13,14,14]
    assert social_roll(grenade_game,path='barter',operation='barter')['check']['total']==10
    grenade_game['lobby'].game_day+=1;db.session.commit()
    assert social_roll(grenade_game,operation='tomorrow')['check']['total']==10


def test_social_receipt_does_not_reroll_or_award_another_balabol_bonus(grenade_game,monkeypatch):
    actor,target=social_case(grenade_game,'Балабол')
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    first=social_roll(grenade_game,difficulty=5)
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:pytest.fail('Повтор запроса не бросает кубы'))
    second=social_roll(grenade_game,difficulty=5)
    assert second['check']==first['check'] and second['replayed']
    assert actor.character.data['health']['combatMeta']['socialMemory'][str(target.character_id)]['skills']['skills.social.persuasion']==1


def test_honest_eyes_only_first_deception_and_psychologist_blocks_lie(grenade_game,monkeypatch):
    actor,target=social_case(grenade_game,traits=['Честные глаза'])
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    assert social_roll(grenade_game,path='deception')['check']['total']==13
    assert social_roll(grenade_game,path='deception',operation='second-lie')['check']['total']==10
    data=deepcopy(actor.character.data);data['features']['specialTraits'].append({'name':'Психолог на пол ставки'});actor.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:1)
    result=social_roll(grenade_game,path='deception',operation='cannot-lie')
    assert result['check']['blocked'] and not result['check']['will_check']['success']


def test_diplomat_repeat_once_penalty_only_social_until_stress_decrease(grenade_game,monkeypatch):
    from app.services.social_check import run
    actor,target=social_case(grenade_game,'Дипломат')
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:min(high,10))
    assert social_roll(grenade_game,difficulty=40)['diplomat_retry_available']
    result=run(actor.character_id,grenade_game['lobby'].gm_id,{'retry':True,'operation_id':'diplomat-retry'})
    assert not result['check']['success'] and not result['diplomat_retry_available']
    assert actor.character.data['health']['stress']==1
    assert CombatService._health_roll_modifier(actor.character.data,'skills.social.persuasion')==-2
    assert CombatService._health_roll_modifier(actor.character.data,'skills.physical.agility')==0
    with pytest.raises(ConflictError):run(actor.character_id,grenade_game['lobby'].gm_id,{'retry':True,'operation_id':'retry-again'})
    CombatService.apply_stress_trigger(actor,-1,trigger='calm',check_manifestation=False)
    assert CombatService._health_roll_modifier(actor.character.data,'skills.social.persuasion')==0


def test_social_advantage_cancellation_and_military_tags(grenade_game,monkeypatch):
    actor,target=social_case(grenade_game,'Добрый товарищ Военком')
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    assert social_roll(grenade_game)['check']['total']==2
    data=deepcopy(target.character.data);data['basic']={'tags':['Военный']};target.character.data=data;db.session.commit()
    result=social_roll(grenade_game,operation='military')['check']
    assert result['advantage'] and len(result['rolls'])==2 and result['total']>10


def test_social_api_permissions_and_server_owned_memory(client,grenade_game,auth_headers,monkeypatch):
    actor,target=social_case(grenade_game,'Балабол')
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    url=f'/lobbies/characters/{actor.character_id}/social-check'
    payload={'operation_id':'api','target_character_id':target.character_id,'skill_path':'skills.social.persuasion','difficulty':5}
    assert client.post(url,headers=auth_headers(grenade_game['target_user']),json=payload).status_code==403
    response=client.post(url,headers=auth_headers(grenade_game['actor_user']),json=payload)
    assert response.status_code==200,response.json
    from app.services.deferred_action import preserve_action_payment
    forged=deepcopy(actor.character.data);forged['health']['combatMeta']['socialMemory']={}
    preserve_action_payment(actor.character.data,forged)
    assert forged['health']['combatMeta']['socialMemory']==actor.character.data['health']['combatMeta']['socialMemory']


def test_social_without_target_keeps_ordinary_roll_available(grenade_game,monkeypatch):
    from app.services.social_check import run
    actor,target=social_case(grenade_game,'Добрый товарищ Военком')
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    result=run(actor.character_id,grenade_game['lobby'].gm_id,{'operation_id':'untargeted','skill_path':'skills.social.persuasion','difficulty':10})
    assert result['check']['total']==10 and not result['check']['rules']


def test_hunter_target_bonus_is_for_chosen_species_and_trickster_only_penalizes_people():
    from app.services.character_traits import target_attack_accuracy
    hunter={'basic':{'background':{'name':'Охотник','choices':{'favoredMutant':'Кабан'}}}}
    chosen={'is_mutant':True,'basic':{'mutant_type':'Кабан'}}
    assert target_attack_accuracy(hunter,chosen)==3
    assert target_attack_accuracy(hunter,chosen,ranged=False)==3
    assert target_attack_accuracy(hunter,{'is_mutant':True,'basic':{'mutant_type':'Плоть'}})==0
    assert target_attack_accuracy(hunter,{})==0
    trickster={'basic':{'background':{'name':'Трюкач'}}}
    assert target_attack_accuracy(trickster,{})==-1
    assert target_attack_accuracy(trickster,chosen)==0
    assert target_attack_accuracy(trickster,{},ranged=False)==0


def test_trickster_escape_gets_four_with_real_anomaly_payment(app,monkeypatch):
    from test_combat_anomalies import _combat_with_anomaly
    user,location,actor,placed,_=_combat_with_anomaly()
    placed.pos_x=placed.pos_y=2
    CombatService._enter_anomaly(placed,CombatService._anomalies_at_tile(location,2,2)[0],1)
    data=deepcopy(actor.data);data['basic']={'background':{'name':'Трюкач'}};actor.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda low,high:min(high,10))
    result=CombatService.perform_action(location.id,user.id,placed.id,'escape_anomaly',target_x=1,target_y=2)
    check=result['anomaly']['check']
    assert check['trickster_bonus']==4 and check['total']==14
    assert placed.action_points_current==2


@pytest.mark.parametrize('weapon_name,cost,accuracy',[('Топор',1,0),('Нож',2,-1)])
def test_firefighter_axe_discount_and_other_melee_penalty(grenade_game,monkeypatch,weapon_name,cost,accuracy):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Пожарный'}}
    data['weapons']=[{'id':'tool','name':weapon_name,'category':'melee_weapon','attributes':{'damage':1,'weight_class':'light','allowed_attacks':['Рубящий']}}]
    actor.character.data=data;actor.drawn_weapon_index=0;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:2)
    result=CombatService.perform_action(actor.location_id,grenade_game['lobby'].gm_id,actor.id,'attack',weapon_index=0,attack_type='Рубящий',target_character_id=target.character_id)
    assert result['attack']['action_points']==cost and result['attack']['weapon_accuracy']==accuracy


@pytest.mark.parametrize('targeted',[True,False])
def test_hunter_and_vegetation_context_stack_once_without_changing_skill(grenade_game,monkeypatch,targeted):
    from app.services.social_check import run
    actor,target=social_case(grenade_game,'Охотник',traits=['Человек-куст'])
    data=deepcopy(actor.character.data);data['basic']['background']['choices']={'favoredMutant':'Кабан'}
    data['skills']['other']['stealth']={'base':10};actor.character.data=data
    data=deepcopy(target.character.data);data.update(is_mutant=True,basic={'mutant_type':'Кабан'});target.character.data=data;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda *a:10)
    result=run(actor.character_id,grenade_game['lobby'].gm_id,{'operation_id':'hidden-from-boar','skill_path':'skills.other.stealth',
        'difficulty':12,'target_character_id':target.character_id if targeted else None,'vegetation':True,'related_favored':True})
    assert result['check']['total']==17 and result['check']['success']
    assert actor.character.data['skills']['other']['stealth']=={'base':10}


def test_true_sight_private_visible_family_and_no_hidden_original_id(client,grenade_game,auth_headers,monkeypatch):
    from app.models import LobbyCharacter,LocationCharacter
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(actor.character.data);data['features']={'specialTraits':[{'name':'Истинное зрение'}]};actor.character.data=data
    original=LobbyCharacter(lobby_id=actor.character.lobby_id,owner_id=actor.character.owner_id,name='Оригинал',data={'is_mutant':True,'health':{'current':700,'max':700,'effects':[]}})
    db.session.add(original);db.session.flush()
    original_location=LocationCharacter(location_id=actor.location_id,character_id=original.id,pos_x=actor.pos_x+2,pos_y=actor.pos_y)
    db.session.add(original_location)
    data=deepcopy(target.character.data);data.update(is_mutant=True,basic={'is_psi_clone':True,'psi_clone_source_id':original.id,'psi_clone_display_name':'Оригинал'})
    target.character.data=data;db.session.commit()
    monkeypatch.setattr(CombatService,'_can_see_location_character',lambda source,other:other.id==target.id)
    endpoint=f'/lobbies/{actor.character.lobby_id}/locations/{actor.location_id}/true-sight/{actor.character_id}'
    assert client.get(endpoint,headers=auth_headers(grenade_game['target_user'])).status_code==403
    response=client.get(endpoint,headers=auth_headers(grenade_game['actor_user']))
    assert response.status_code==200,response.json
    assert response.json=={'active':True,'marks':[{'character_id':target.character_id,'kind':'copy'}]}
    assert CombatService._serialize_character(target)['name']=='Оригинал'
    monkeypatch.setattr(CombatService,'_can_see_location_character',lambda *a:True)
    response=client.get(endpoint,headers=auth_headers(grenade_game['actor_user']))
    assert {mark['kind'] for mark in response.json['marks']}=={'copy','original'}
    data=deepcopy(actor.character.data);data['health']['organs']={'leftEye':{'current':0},'rightEye':{'current':0}};actor.character.data=data;db.session.commit()
    response=client.get(endpoint,headers=auth_headers(grenade_game['actor_user']))
    assert response.json=={'active':False,'marks':[]}


def test_gm_reviving_and_killing_again_resets_observed_death_once_per_death(grenade_game,monkeypatch):
    from app.services.character import CharacterService
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    data=deepcopy(actor.character.data);data['basic']={'background':{'name':'Математик'}};actor.character.data=data;db.session.commit()
    monkeypatch.setattr(CombatService,'_can_see_location_character',lambda *a:True)
    original=CombatService.apply_stress_trigger
    monkeypatch.setattr(CombatService,'apply_stress_trigger',lambda target,amount,**kw:original(target,amount,check_manifestation=False,**kw))
    def save(effects):
        data=target.character.data_snapshot();data['health']['effects']=effects
        CharacterService.update_character(target.character_id,grenade_game['lobby'].gm_id,{'data':data})
    save([{'type':'death'}]);assert actor.character.data['health']['stress']==1
    save([]);assert not target.character.data['health']['combatMeta'].get('observedDeathNotified')
    save([{'type':'death'}]);assert actor.character.data['health']['stress']==2
    save([{'type':'death'}]);assert actor.character.data['health']['stress']==2


def test_sprinter_does_not_bypass_exoskeleton_running_restriction(app):
    data={'basic':{'background':{'name':'Спринтер'}},'equipment':{'armor':{'name':'Экзоскелет','isExoskeleton':True}}}
    assert not CombatService._sprinter_walk(data,'walk','standing')
    assert CombatService._movement_distance_limit('walk','standing',False,0,data)==10


def test_fractional_cosmetic_charisma_bonuses_are_not_truncated(app):
    data = {'equipment': {'gloves': {'charismaBonus': .5}, 'ring': {'charismaBonus': .25},
                          'bracelet1': {'charismaBonus': .25}}, 'health': {'effects': []}}
    result = CombatService._narrative_skill_check(data, 'skills.social.charisma')
    assert result['equipment_modifier'] == 1


def test_welder_recognizes_new_catalog_name_of_welding_glasses(app):
    data = {'features': {'specialTraits': [{'name':'Сварщик'}]},
            'equipment': {'glasses': {'name':'Сварочные защитные очки', 'charismaBonus':-1}},
            'health': {'effects': []}}
    assert CombatService._narrative_skill_check(data, 'skills.social.charisma')['equipment_modifier'] == 1


def test_military_gloves_protect_only_when_worn_and_do_not_add_physical_armor(app):
    gloves = {'name': 'Военные', 'attributes': {'anomaly_protection_bonus': 3}}
    data = {'equipment': {'gloves': gloves}, 'inventory': {'pockets': []}}
    for damage_type in ('thermal', 'electric', 'chemical', 'radiation'):
        assert CombatService._target_elemental_protection(data, damage_type) == 3
    assert CombatService._equipped_radiation_protection(data) == 3
    assert CombatService._target_elemental_protection(data, 'physical') == 0
    data['equipment'].pop('gloves')
    data['inventory']['pockets'].append(gloves)
    assert CombatService._target_elemental_protection(data, 'electric') == 0


def test_glasses_blinding_coefficient_applies_to_actual_flash_points(app):
    assert CombatService._received_flash({}, 250, 0, 1) == 250
    data = {'equipment': {'glasses': {'flashProtection': .25}}}
    assert CombatService._received_flash(data, 250, 0, 1) == 62
    assert CombatService._received_flash(data, 250, 5, .5) == 6
    data['equipment']['glasses']['protectionDisabled'] = True
    assert CombatService._received_flash(data, 250, 0, 1) == 250


def test_trauma_choice_does_not_lock_battle_if_gm_removed_target(grenade_game):
    from app.services.sniper_trauma import enqueue,choices,decide,require_resolved
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    enqueue(actor,target,{'area':'chest','roll':10,'chance_roll':100},20,'chest',True,True);db.session.commit()
    choice=choices(actor.character.data)[0]
    db.session.delete(target);db.session.commit()
    result=decide(actor,grenade_game['lobby'].gm_id,{'id':choice['id'],'index':1})
    assert result['trauma'] is None and choices(actor.character.data)==[]
    require_resolved(LocationCombatState.query.filter_by(location_id=actor.location_id).one())
