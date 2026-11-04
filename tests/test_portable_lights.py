"""Луч, питание, руки и оплата переносного света на настоящих игровых моделях."""
from copy import deepcopy
import math

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCombatState,LocationObject
from app.services.combat import CombatService
from app.services.portable_lights import advance,bindings,covers,ergonomics,illumination,shooting,start_battle
from test_grenade_rules import grenade_game


def install(actor,*,kind='hand',on=False):
    data=deepcopy(actor.character.data)
    data['inventory']={'pockets':[{'id':f'light-{actor.id}','name':'Улучшенный фонарик' if kind=='improved' else 'Фонарик',
        'category':'misc','quantity':1,'attributes':{'portable_light':kind}}], 'backpack':[]}
    if on:
        data['health'].setdefault('combatMeta',{})['portableLights']=[{'id':f'light-{actor.id}','mount':'head','on':True,'period_used':True}]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    return actor


def operate(client,case,auth_headers,operation,**payload):
    actor=case['actor_location']
    return client.post(f'/lobbies/characters/{actor.character_id}/portable-lights',headers=auth_headers(case['actor_user']),
        json={'operation':operation,'path':['inventory','pockets',0],'source_id':f'light-{actor.id}',
              'character_revision':actor.character.revision,**payload})


def test_prepare_toggle_and_battle_end_consume_one_use(client,grenade_game,auth_headers):
    actor=install(grenade_game['actor_location']);before=actor.action_points_current
    assert operate(client,grenade_game,auth_headers,'prepare').status_code==200
    assert actor.action_points_current==before-1
    assert operate(client,grenade_game,auth_headers,'toggle').status_code==200
    assert actor.action_points_current==before-2
    assert bindings(actor.character.data)[0]['on']
    assert operate(client,grenade_game,auth_headers,'toggle').status_code==200
    assert actor.action_points_current==before-3
    assert actor.character.data['inventory']['pockets'][0]['lightPower']['uses_left']==10
    CombatService.end_combat(actor.location_id,grenade_game['lobby'].gm_id)
    assert actor.character.data['inventory']['pockets'][0]['lightPower']['uses_left']==9


def test_exact_ten_minutes_do_not_charge_again_at_same_battle_end(grenade_game):
    actor=install(grenade_game['actor_location'],on=True);data=actor.character.data
    start_battle(data)
    assert advance(data,599)
    item=data['inventory']['pockets'][0]
    assert item['lightPower']['uses_left']==10
    advance(data,1)
    assert item['lightPower']['uses_left']==9
    advance(data,battle_end=True)
    assert item['lightPower']['uses_left']==9
    start_battle(data);advance(data,battle_end=True)
    assert item['lightPower']['uses_left']==8
    advance(data,4800)
    assert item['lightPower']['uses_left']==0 and not bindings(data)[0]['on']


def test_beam_limits_sideways_target_and_real_wall(grenade_game):
    actor=install(grenade_game['actor_location'],on=True);target=grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+10,actor.pos_y;db.session.commit()
    assert covers(actor,target)
    target.pos_y=actor.pos_y+2;db.session.commit()
    assert not covers(actor,target)
    target.pos_y=actor.pos_y;db.session.commit()
    wall=LocationObject(location_id=actor.location_id,type='wall',name='Целая стена',tile_x=7,tile_y=5,
        properties={'rotation':math.pi/2,'dimensions':{'width':5,'depth':.2,'height':3}})
    db.session.add(wall);db.session.commit()
    assert not covers(actor,target)


def test_night_accuracy_glare_and_nvg_aim_ban(grenade_game):
    actor=install(grenade_game['actor_location'],on=True);target=install(grenade_game['target_location'],on=True)
    target.pos_x,target.pos_y=actor.pos_x+10,actor.pos_y;target.facing_x,target.facing_y=-1,0
    grenade_game['lobby'].game_time_minutes=0;db.session.commit()
    assert shooting(actor,target,'unaimed')['accuracy']==-1
    assert shooting(actor,target,'aimed')['accuracy']==1
    data=deepcopy(actor.character.data);data['equipment']={'helmet':{'installedModules':[{'slotType':'nvg','active':True}]}}
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    result=shooting(actor,target,'aimed')
    assert result['aimed_blocked'] and result['accuracy']==2


def test_improved_light_removes_residual_penalty(grenade_game):
    actor=install(grenade_game['actor_location'],kind='improved',on=True);target=grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+10,actor.pos_y;db.session.commit()
    light=illumination(target)
    assert light['lit'] and light['penalty']==0


def test_hand_light_changes_ergonomics_and_occupies_hand(client,grenade_game,auth_headers):
    actor=install(grenade_game['actor_location'])
    assert operate(client,grenade_game,auth_headers,'prepare').status_code==200
    rifle={'id':'light-rifle','name':'Винтовка','category':'weapon','subcategory':'Штурмовая винтовка'}
    pistol={'name':'Пистолет','subcategory':'Пистолеты'}
    assert ergonomics(actor,rifle)==-25 and ergonomics(actor,pistol)==0
    data=deepcopy(actor.character.data);data['weapons']=[rifle];data['health']['combatMeta']['weaponGripHands']=1
    actor.character.data=data;actor.drawn_weapon_index=0;flag_modified(actor.character,'data');db.session.commit()
    assert not CombatService._has_usable_free_hand(actor)
    data['health']['combatMeta']['portableLights'][0].update(mount='taped',weapon_id='light-rifle')
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    assert ergonomics(actor,rifle)==-15


def test_global_clock_advances_light_even_when_character_time_is_inactive(client,grenade_game,auth_headers):
    actor=install(grenade_game['actor_location'],on=True);actor.character.time_active=False;db.session.commit()
    lobby=grenade_game['lobby'];before=lobby.game_time_minutes;day=lobby.game_day
    response=client.patch(f'/lobbies/{lobby.id}/time',headers=auth_headers(grenade_game['actor_user']),
        json={'game_day':day,'game_time_minutes':before+10})
    assert response.status_code==200,response.json
    assert actor.character.data['inventory']['pockets'][0]['lightPower']['uses_left']==9


def test_battery_replacement_is_checked_and_preserved(client,grenade_game,auth_headers):
    actor=install(grenade_game['actor_location'])
    data=deepcopy(actor.character.data);data['inventory']['pockets'].append({'name':'Батарейка','quantity':2})
    data['inventory']['pockets'][0]['lightPower']={'uses_left':0,'elapsed_seconds':0}
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    before=actor.action_points_current
    response=operate(client,grenade_game,auth_headers,'battery',battery_path=['inventory','pockets',1])
    assert response.status_code==200,response.json
    assert actor.action_points_current==before-2  # 1 ОД достать из кармана + 1 ОД заменить.
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='idle';db.session.commit()
    response=operate(client,grenade_game,auth_headers,'battery',battery_path=['inventory','pockets',1])
    assert response.status_code==200,response.json
    assert actor.character.data['inventory']['pockets'][0]['lightPower']['uses_left']==10
    assert len(actor.character.data['inventory']['pockets'])==1


def test_head_mount_and_removal_in_combat_are_paid(client,grenade_game,auth_headers):
    actor=install(grenade_game['actor_location'],kind='head');before=actor.action_points_current
    response=operate(client,grenade_game,auth_headers,'prepare')
    assert response.status_code==200,response.json
    assert actor.action_points_current==before-2
    assert operate(client,grenade_game,auth_headers,'stow').status_code==200
    assert actor.action_points_current==before-3 and not bindings(actor.character.data)


def test_battery_underpayment_rolls_back_item_and_charge(client,grenade_game,auth_headers):
    actor=install(grenade_game['actor_location']);data=deepcopy(actor.character.data)
    data['inventory']['backpack']=[{'name':'Батарейка','quantity':1}]
    data['inventory']['pockets'][0]['lightPower']={'uses_left':0,'elapsed_seconds':0}
    actor.character.data=data;actor.action_points_current=2;flag_modified(actor.character,'data');db.session.commit()
    response=operate(client,grenade_game,auth_headers,'battery',battery_path=['inventory','backpack',0])
    assert response.status_code==400
    assert actor.action_points_current==2 and len(actor.character.data['inventory']['backpack'])==1
    assert actor.character.data['inventory']['pockets'][0]['lightPower']['uses_left']==0
