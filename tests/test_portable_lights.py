"""Луч, питание, руки и оплата переносного света на настоящих игровых моделях."""
from copy import deepcopy
import math
import pytest

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db, socketio
from app.models import LocationCombatState,LocationObject
from app.services.combat import CombatService
from app.services.portable_lights import advance,advance_ground,bindings,covers,ergonomics,ground_sources,illumination,ready_sources,shooting,start_battle
from test_grenade_rules import grenade_game


def belt_mount(actor):
    from app.services.equipment_modifications import make_record
    data=deepcopy(actor.character.data);data.setdefault('equipment',{})['belt']={'id':'light-belt','category':'belt',
        'modifications':[make_record('armor-g62')],'pouches':[]}
    actor.character.data=data;db.session.commit()


def test_belt_mount_frees_hand_keeps_power_and_stops_after_belt_is_removed(client,grenade_game,auth_headers):
    from app.services.portable_lights import held_hand
    actor=install(grenade_game['actor_location']);belt_mount(actor);before=actor.action_points_current
    assert operate(client,grenade_game,auth_headers,'prepare',mount='hand').status_code==200
    assert operate(client,grenade_game,auth_headers,'toggle').status_code==200
    assert held_hand(actor.character.data)
    response=operate(client,grenade_game,auth_headers,'prepare',mount='belt');assert response.status_code==200,response.json
    assert response.json['light_operation']['action_points']==3 and actor.action_points_current==before-5
    assert len(bindings(actor.character.data))==1 and bindings(actor.character.data)[0]['on']
    assert not held_hand(actor.character.data)
    source=ready_sources(actor)[0];assert source['mount']=='belt' and source['y']==CombatService.physical_position(actor)['height']+1
    data=deepcopy(actor.character.data);data['equipment'].pop('belt');actor.character.data=data;db.session.commit()
    assert not ready_sources(actor)
    advance(data,600);assert not bindings(data)[0]['on']
    assert data['inventory']['pockets'][0]['lightPower']['uses_left']==10
    actor.character.data=data;db.session.commit()
    status=client.get(f'/lobbies/characters/{actor.character_id}/portable-lights',headers=auth_headers(grenade_game['actor_user'])).json
    assert status['lights'][0]['mount_available'] is False
    assert operate(client,grenade_game,auth_headers,'toggle').status_code==400


def test_belt_mount_requires_upgrade_and_allows_only_one_light(client,grenade_game,auth_headers):
    actor=install(grenade_game['actor_location']);before=actor.action_points_current
    assert operate(client,grenade_game,auth_headers,'prepare',mount='belt').status_code==400
    assert actor.action_points_current==before
    belt_mount(actor);assert operate(client,grenade_game,auth_headers,'prepare',mount='belt').status_code==200
    data=deepcopy(actor.character.data);data['inventory']['pockets'].append({'id':'second-light','name':'Фонарик','category':'misc','quantity':1})
    actor.character.data=data;db.session.commit()
    response=client.post(f'/lobbies/characters/{actor.character_id}/portable-lights',headers=auth_headers(grenade_game['actor_user']),
        json={'operation':'prepare','mount':'belt','path':['inventory','pockets',1],'source_id':'second-light','character_revision':actor.character.revision})
    assert response.status_code==400 and len(bindings(actor.character.data))==1


def test_active_glow_can_move_to_belt_without_restarting_timer(client,grenade_game,auth_headers):
    from app.services.portable_lights import held_hand
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['inventory']={'pockets':[{'id':f'light-{actor.id}','name':'ХИС','category':'misc','quantity':1}], 'backpack':[]}
    actor.character.data=data;db.session.commit();belt_mount(actor)
    assert operate(client,grenade_game,auth_headers,'ignite').status_code==200
    data=deepcopy(actor.character.data);advance(data,60);actor.character.data=data;db.session.commit()
    response=operate(client,grenade_game,auth_headers,'prepare',mount='belt');assert response.status_code==200,response.json
    assert not held_hand(actor.character.data) and ready_sources(actor)[0]['radial']
    assert actor.character.data['inventory']['pockets'][0]['glowState']['remaining_seconds']==1740


def test_unloading_belt_discount_includes_attached_light_once(grenade_game):
    from app.services.equipment_modifications import make_record
    actor=install(grenade_game['actor_location']);belt_mount(actor);data=deepcopy(actor.character.data)
    data['inventory']['pockets'][0]['weight']=2
    data['equipment']['belt']['modifications'].append(make_record('armor-g58'))
    data['health'].setdefault('combatMeta',{})['portableLights']=[{'id':f'light-{actor.id}','mount':'belt','on':False}]
    assert CombatService._inventory_weight_details(data)['total_weight']==1
    item=data['inventory']['pockets'].pop();data['equipment']['belt']['pouches']=[{'contents':[item]}]
    assert CombatService._inventory_weight_details(data)['total_weight']==1


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


@pytest.mark.parametrize('in_combat,new_charge,expected_on',[(False,7,True),(False,0,False),(True,7,None)])
def test_hand_lamp_replacement_is_one_action_only_outside_combat(client,grenade_game,auth_headers,in_combat,new_charge,expected_on):
    actor=install(grenade_game['actor_location']);data=deepcopy(actor.character.data)
    old=data['inventory']['pockets'][0];old['lightPower']={'uses_left':6,'elapsed_seconds':123,'scheduled_on':True}
    data['inventory']['pockets'].append({'id':'replacement-lamp','name':'Улучшенный фонарик','category':'misc','quantity':1,
        'lightPower':{'uses_left':new_charge,'elapsed_seconds':321}})
    data['health'].setdefault('combatMeta',{})['portableLights']=[{'id':old['id'],'mount':'hand','on':True}]
    actor.character.data=data
    if not in_combat:LocationCombatState.query.filter_by(location_id=actor.location_id).one().status='inactive'
    db.session.commit();before=deepcopy(actor.character.data);points=actor.action_points_current
    response=operate(client,grenade_game,auth_headers,'prepare',path=['inventory','pockets',1],source_id='replacement-lamp',mount='hand')
    if in_combat:
        assert response.status_code==400
        assert bindings(actor.character.data)==bindings(before) and actor.character.data['inventory']==before['inventory']
    else:
        assert response.status_code==200,response.json
        assert bindings(actor.character.data)==[{'id':'replacement-lamp','mount':'hand','on':expected_on}]
        old,new=actor.character.data['inventory']['pockets']
        assert old['lightPower']=={'uses_left':6,'elapsed_seconds':123,'scheduled_on':False}
        assert new['lightPower']['uses_left']==new_charge and new['lightPower']['elapsed_seconds']==321
        assert response.json['light_operation']['action_points']==0
    assert actor.action_points_current==points


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


def test_visor_mount_light_and_nvg_keep_real_power_and_stop_when_visor_is_removed(client,grenade_game,auth_headers):
    from app.services.equipment_modifications import make_record,install as install_modification
    from app.services.weapon_modules import uses_nvg
    from app.services.exceptions import ValidationError
    import pytest
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    visor={'id':'mounted-visor','name':'Забрало','slotType':'visor','modifications':[make_record('armor-g18')],
        'installedModules':[{'id':'visor-nvg','name':'ПНВ','slotType':'nvg','active':True,'lightPower':{'uses_left':8}},
            {'id':'visor-light','name':'Фонарик','category':'misc','slotType':'light','quantity':1,'lightPower':{'uses_left':7}}]}
    data.setdefault('equipment',{})['helmet']={'id':'helmet','category':'helmet','installedModules':[visor]}
    actor.character.data=data;db.session.commit();path=['equipment','helmet','installedModules',0,'installedModules',1]
    status=client.get(f'/lobbies/characters/{actor.character_id}/portable-lights',headers=auth_headers(grenade_game['actor_user']))
    assert next(item for item in status.json['lights'] if item['id']=='visor-light')['visor_mount']
    before=actor.action_points_current
    assert operate(client,grenade_game,auth_headers,'prepare',path=path,source_id='visor-light',mount='head').status_code==200
    assert operate(client,grenade_game,auth_headers,'toggle',path=path,source_id='visor-light').status_code==200
    assert actor.action_points_current==before-2
    assert uses_nvg(actor.character.data) and ready_sources(actor)[0]['mount']=='head'
    owned=deepcopy(actor.character.data);advance(owned,600)
    assert owned['equipment']['helmet']['installedModules'][0]['installedModules'][1]['lightPower']['uses_left']==6
    visor=owned['equipment']['helmet']['installedModules'][0]
    with pytest.raises(ValidationError,match='фонарь'):install_modification(visor,make_record('armor-g19'),replacing='armor-g18')
    owned['equipment']['helmet']['installedModules']=[];owned['inventory']['backpack']=[visor]
    actor.character.data=owned
    assert not uses_nvg(owned) and ready_sources(actor)==[]


def test_visor_accepts_one_device_with_mount_and_preserves_legacy_items(client, grenade_game, auth_headers):
    from app.services.equipment_modifications import make_record

    actor = grenade_game['actor_location']
    data = deepcopy(actor.character.data)
    nvg = {'id':'visor-nvg', 'name':'ПНВ', 'slotType':'nvg', 'lightPower':{'uses_left':8}}
    light = {'id':'visor-light', 'name':'Фонарик', 'slotType':'light', 'lightPower':{'uses_left':7}}
    visor = {'id':'mounted-visor', 'name':'Забрало', 'slotType':'visor', 'installedModules':[]}
    data.setdefault('equipment', {})['helmet'] = {'id':'helmet', 'category':'helmet', 'installedModules':[visor]}
    actor.character.data = deepcopy(data)
    db.session.commit()
    endpoint = f'/lobbies/characters/{actor.character_id}'
    headers = auth_headers(grenade_game['actor_user'])

    def save():
        return client.put(endpoint, headers=headers, json={'data':{**data, '_revision':actor.character.revision}})

    visor['installedModules'] = [nvg]
    assert save().status_code == 400
    mounted = deepcopy(actor.character.data)
    mounted['equipment']['helmet']['installedModules'][0]['modifications'] = [
        {**make_record('armor-g18'), 'description':'Позволяет вешать фонарик и ПНВ'},
    ]
    actor.character.data = mounted
    db.session.commit()
    snapshot = client.get(endpoint, headers=headers).json['data']
    assert snapshot['equipment']['helmet']['installedModules'][0]['modifications'][0]['description'].endswith('фонарик или ПНВ.')
    visor['modifications'] = deepcopy(mounted['equipment']['helmet']['installedModules'][0]['modifications'])
    response = save()
    assert response.status_code == 200, response.json
    visor['installedModules'] = [nvg, light]
    assert save().status_code == 400

    # Существовавшее двойное крепление можно сохранить и освободить, не меняя экземпляры.
    actor.character.data = deepcopy(data)
    db.session.commit()
    assert save().status_code == 200
    visor['installedModules'] = [light]
    data['inventory']['backpack'] = [nvg]
    response = save()
    assert response.status_code == 200, response.json
    stored = actor.character.data
    assert stored['equipment']['helmet']['installedModules'][0]['installedModules'][0]['lightPower']['uses_left'] == 7
    assert stored['inventory']['backpack'][0]['lightPower']['uses_left'] == 8


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


def test_twenty_meter_beam_preserves_five_meter_end_width(grenade_game):
    actor=install(grenade_game['actor_location'],on=True);target=grenade_game['target_location']
    source=ready_sources(actor)[0]
    assert source['range']==20 and source['end_width']==5
    target.pos_x,target.pos_y=actor.pos_x+20,actor.pos_y;assert covers(actor,target)
    target.pos_x=actor.pos_x+20.01;assert not covers(actor,target)
    target.pos_x,target.pos_y=actor.pos_x+10,actor.pos_y+1.2;assert covers(actor,target)
    target.pos_y=actor.pos_y+1.4;assert not covers(actor,target)


@pytest.mark.parametrize('name, radius', [('ХИС', 8), ('Светящаяся палочка', 5)])
def test_carried_glow_only_illuminates_forward_sector(client, grenade_game, auth_headers, name, radius):
    actor = grenade_game['actor_location']
    target = grenade_game['target_location']
    data = deepcopy(actor.character.data)
    data['inventory'] = {'pockets': [{'id': f'light-{actor.id}', 'name': name, 'category': 'misc', 'quantity': 1}]}
    actor.character.data = data
    actor.facing_x, actor.facing_y = 1, 0
    db.session.commit()
    assert operate(client, grenade_game, auth_headers, 'ignite').status_code == 200
    source = ready_sources(actor)[0]
    assert source['range'] == radius and source['arc_degrees'] == 150
    for angle, lit in [(0, True), (74, True), (76, False), (-74, True), (-76, False), (180, False)]:
        angle = math.radians(angle)
        target.pos_x = actor.pos_x + 2 * math.cos(angle)
        target.pos_y = actor.pos_y + 2 * math.sin(angle)
        assert bool(covers(actor, target, objects=[])) is lit


@pytest.mark.parametrize('mount,posture,height,forward',[('head','standing',1.65,.35),('head','prone',.32,.80),
    ('hand','standing',1.2,.35),('hand','sitting',.8,.35),('hand','prone',.28,.74),('belt','standing',1,.35)])
def test_emitter_uses_mount_height_instead_of_ground(grenade_game,mount,posture,height,forward):
    from app.services.portable_lights import mount_forward, mount_height
    actor=install(grenade_game['actor_location'],on=True);actor.posture=posture
    actor.facing_x,actor.facing_y=0,1
    # Наличие крепления на поясе проверяется отдельными сценариями.
    assert mount_height(mount,posture)==height
    assert mount_forward(mount,posture)==forward
    if mount!='belt':
        bindings(actor.character.data)[0]['mount']=mount
        source=ready_sources(actor)[0]
        assert source['mount_height']==height
        assert abs(source['y']-CombatService.physical_position(actor)['height']-height)<1e-8
        assert source['z'] == pytest.approx(actor.pos_y + .5 + forward)


@pytest.mark.parametrize('name,previous',[('Фонарик','Зона действия фонарика - конус с высотой 35 и основанием 5.'),
    ('Улучшенный фонарик','Зона действия фонарика - конус с высотой 55 и основанием 8. Не имеет штрафов.')])
def test_old_light_template_serializes_current_range_without_changing_storage(name,previous):
    from app.schemas.templates import ItemTemplateSchema
    source={'name':name,'category':'misc','description':previous,'attributes':{}}
    public=ItemTemplateSchema().dump(source)
    assert '20 м' in public['description'] and '5 м' in public['description']
    assert '35' not in public['description'] and '55' not in public['description']
    assert source['description']==previous


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


def test_ignition_core_lights_a_five_meter_radius_without_flashlight_bonus(grenade_game):
    actor=grenade_game['actor_location']
    target=grenade_game['target_location']
    data=deepcopy(actor.character.data)
    artifact={'id':'ignition-test','name':'Ядро возгорания','category':'artifact'}
    data.setdefault('equipment',{})['armor']={'containers':[{'item':artifact}]}
    actor.character.data=data
    actor.pos_x,actor.pos_y=10,10
    actor.facing_x,actor.facing_y=1,0
    target.pos_x,target.pos_y=actor.pos_x-5,actor.pos_y
    grenade_game['lobby'].game_time_minutes=0
    db.session.commit()

    source,=ready_sources(actor)
    assert source['range']==5 and source['radial'] and source['ignition_aura']
    assert illumination(actor)['lit']
    assert illumination(target)['lit']
    assert shooting(actor,target,'aimed')['accuracy']==0
    assert shooting(actor,target,'aimed')['lit']

    target.pos_x=actor.pos_x-5.01
    assert not illumination(target)['lit']
    target.pos_x=actor.pos_x-3
    target.floor_level=1
    assert not illumination(target)['lit']
    target.floor_level=0
    data=deepcopy(actor.character.data)
    data['equipment']['armor']['containers'].clear()
    actor.character.data=data
    assert not ready_sources(actor)
    assert not illumination(target)['lit']


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


def test_glow_stick_split_ignite_throw_and_expire(client,grenade_game,auth_headers,monkeypatch):
    actor=grenade_game['actor_location'];location=grenade_game['location']
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'id':'glow-stack','name':'Светящаяся палочка',
        'category':'misc','weight':.1,'quantity':2}]
    data['weapons']=[{'id':'glow-pistol','name':'Пистолет','category':'firearm','weight':1}]
    data['health'].setdefault('combatMeta',{})['weaponGripHands']=1
    actor.character.data=data;actor.drawn_weapon_index=0
    flag_modified(actor.character,'data');db.session.commit()
    before=actor.action_points_current
    url = f'/lobbies/characters/{actor.character_id}/portable-lights'
    headers = auth_headers(grenade_game['actor_user'])
    payload = {'operation': 'ignite', 'path': ['inventory', 'pockets', 0],
               'source_id': 'glow-stack', 'character_revision': actor.character.revision}
    assert client.post(url, headers=headers, json={**payload, 'color': '#000000'}).status_code == 400
    assert actor.action_points_current == before
    assert 'glowState' not in actor.character.data['inventory']['pockets'][0]
    # Запись об отклонённом действии в журнале меняет ревизию листа.
    payload['character_revision'] = actor.character.revision
    response = client.post(url, headers=headers, json={**payload, 'color': '#f9666b'})
    assert response.status_code==200,response.json
    sticks=actor.character.data['inventory']['pockets']
    assert len(sticks)==2 and sticks[0]['quantity']==1
    assert sticks[1]['glowState']=={'activated':True,'remaining_seconds':600,'color':'#f9666b'}
    assert actor.action_points_current==before-2
    assert ready_sources(actor)[0]['radial'] and ready_sources(actor)[0]['range']==5
    assert ready_sources(actor)[0]['color'] == '#f9666b'
    assert not CombatService._has_usable_free_hand(actor)
    monkeypatch.setattr('app.services.brigadier_order.boost_roll',lambda data,rolls,**kwargs:([20],20,0))
    result=CombatService.perform_action(location.id,grenade_game['lobby'].gm_id,actor.id,
        'throw_item',item_path=['inventory','pockets',1],item_id=sticks[1]['id'],
        target_x=4,target_y=5)
    assert result['thrown_item']['success']
    assert not bindings(actor.character.data)
    assert len(ground_sources(location.id))==1
    assert ground_sources(location.id)[0]['color'] == '#f9666b'
    advance_ground(location.id,599)
    assert ground_sources(location.id)[0]['range']==5
    advance_ground(location.id,1)
    assert not ground_sources(location.id)


def test_glow_stick_can_be_placed_on_own_floor(client,grenade_game,auth_headers,monkeypatch):
    actor=grenade_game['actor_location'];actor.floor_level=1
    stick={'id':'held-glow','name':'Светящаяся палочка','category':'misc','weight':.1,
        'quantity':1,'glowState':{'activated':True,'remaining_seconds':600}}
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[stick]
    data['health'].setdefault('combatMeta',{})['portableLights']=[{'id':'held-glow','mount':'hand','on':True}]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    scene_updates = []
    original_emit = socketio.emit

    def capture_emit(event, payload, **kwargs):
        if event == 'combat_state_updated':
            scene_updates.append(payload)
        return original_emit(event, payload, **kwargs)

    monkeypatch.setattr(socketio, 'emit', capture_emit)
    response=client.post(f"/lobbies/{grenade_game['lobby'].id}/locations/{actor.location_id}/drop-item",
        headers=auth_headers(grenade_game['actor_user']),json={
            'character_id':actor.character_id,'character_revision':actor.character.revision,
            'item_path':['inventory','pockets',0],'expected_item':stick,'amount':1})
    assert response.status_code==200,response.json
    assert not bindings(actor.character.data)
    assert len(ground_sources(actor.location_id))==1
    assert ground_sources(actor.location_id)[0]['floor_level']==1
    assert len(scene_updates) == 1
    updated_actor = next(item for item in scene_updates[0]['characters'] if item['character_id'] == actor.character_id)
    assert not updated_actor['portable_lights']


def test_his_uses_eight_meter_light_for_thirty_minutes(client,grenade_game,auth_headers):
    from app.services.portable_lights import active_his_sources
    actor=grenade_game['actor_location']
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'id':'his-1','name':'ХИС','category':'misc','quantity':1}]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()

    response=client.post(f'/lobbies/characters/{actor.character_id}/portable-lights',
        headers=auth_headers(grenade_game['actor_user']),json={
            'operation':'ignite','path':['inventory','pockets',0],
            'source_id':'his-1','character_revision':actor.character.revision})
    assert response.status_code==200,response.json
    source=ready_sources(actor)[0]
    assert source['radial'] and source['range']==8
    sound=active_his_sources(actor.location_id)
    assert len(sound)==1 and sound[0]['kind']=='conversation' and sound[0]['continuous']
    assert sound[0]['holder_character_id']==actor.character_id
    assert actor.character.data['inventory']['pockets'][0]['glowState']['remaining_seconds']==1800
    from app.services.portable_lights import advance_glow
    data=deepcopy(actor.character.data)
    advance_glow(data,1799)
    assert data['inventory']['pockets'][0]['glowState']['remaining_seconds']==1
    advance_glow(data,1)
    assert data['inventory']['pockets'][0]['glowState']['remaining_seconds']==0
    assert not bindings(data)[0]['on']
    item=actor.character.data['inventory']['pockets'][0]
    response=client.post(f"/lobbies/{grenade_game['lobby'].id}/locations/{actor.location_id}/drop-item",
        headers=auth_headers(grenade_game['actor_user']),json={
            'character_id':actor.character_id,'character_revision':actor.character.revision,
            'item_path':['inventory','pockets',0],'expected_item':item,'amount':1})
    assert response.status_code==200,response.json
    assert ground_sources(actor.location_id)[0]['range']==8
    dropped=active_his_sources(actor.location_id)
    assert len(dropped)==1 and dropped[0]['holder_character_id'] is None
    advance_ground(actor.location_id,1800)
    assert not ground_sources(actor.location_id)
    assert not active_his_sources(actor.location_id)


def test_nvg_charge_is_preserved_when_helmet_leaves_belt_mount():
    from app.services.portable_lights import preserve
    module={'id':'nvg-belt','slotType':'nvg','lightPower':{'uses_left':4,'elapsed_seconds':120}}
    for stored in ({'extraStoredItem':{'id':'helmet-belt','category':'helmet','installedModules':[deepcopy(module)]}},
            {'storedItem':{'savedAttributes':{'id':'helmet-belt','category':'helmet','installedModules':[deepcopy(module)]}}}):
        current={'equipment':{'belt':stored}}
        incoming={'inventory':{'backpack':[{'id':'helmet-belt','category':'helmet',
            'installedModules':[{'id':'nvg-belt','slotType':'nvg','lightPower':{'uses_left':10,'elapsed_seconds':0}}]}]}}
        preserve(current,incoming)
        assert incoming['inventory']['backpack'][0]['installedModules'][0]['lightPower']==module['lightPower']
