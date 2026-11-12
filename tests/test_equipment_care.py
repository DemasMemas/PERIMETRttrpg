"""Питание ПНВ и стерильность экземпляра через реальные сохранения и действия."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.services.portable_lights import advance,advance_lobby,start_battle,preserve
from app.services.equipment_care import infection_multiplier,has_timers
from app.services.equipment_modifications import make_record,supported,definition
from app.services.weapon_modules import uses_nvg
from app.services.combat import CombatService
from test_quick_skill_checks import checks


@pytest.mark.parametrize('mode', ['combat', 'outside', 'no_battery', 'trash', 'full', 'low_ap', 'other_turn'])
def test_artifact_power_station_recharges_the_installed_battery_once(client, checks, auth_headers, mode):
    from app.models import LocationCombatState
    from app.models.templates import ItemTemplate

    assert supported(definition('armor-d29'))
    character, actor = checks['actor_character'], checks['actor_location']
    grade = 'trash' if mode == 'trash' else '1'
    template = ItemTemplate(name='Артефакт для электростанции', category='artifact', item_class=grade)
    db.session.add(template)
    db.session.flush()
    battery = {'id': 'battery', 'slotType': 'exoskeleton_battery',
               'attributes': {'charge_days': 3, 'remaining_days': 3 if mode == 'full' else 0}}
    armor = {'id': 'exoskeleton', 'name': 'Экзоскелет', 'category': 'armor', 'powered': False,
             'modifications': [make_record('armor-d29')], 'installedModules': [] if mode == 'no_battery' else [battery]}
    data = deepcopy(character.data)
    data['equipment']['armor'] = armor
    data['inventory']['pockets'] = [{'id': 'station-fuel', 'name': template.name, 'category': 'artifact',
        'templateId': template.id, 'quantity': 2, 'attributes': {'artifact_class': '1'}}]
    character.data = data
    actor.action_points_current = 4 if mode == 'low_ap' else 8
    state = LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    if mode == 'outside':
        state.status = 'ended'
    if mode == 'other_turn':
        state.current_location_character_id = checks['target_location'].id
    db.session.commit()
    revision = character.revision
    payload = {'character_revision': revision, 'armor_id': armor['id'], 'operation_id': 'station-charge',
               'source': {'id': 'station-fuel', 'path': ['inventory', 'pockets', 0]}}
    url = f'/lobbies/characters/{character.id}/exoskeleton-recharge'
    headers = auth_headers(checks['actor_user'])
    response = client.post(url, headers=headers, json=payload)
    if mode in {'combat', 'outside'}:
        assert response.status_code == 200, response.json
        assert response.json['operation']['action_points'] == (5 if mode == 'combat' else 0)
        assert character.data['equipment']['armor']['installedModules'][0]['id'] == 'battery'
        assert character.data['equipment']['armor']['installedModules'][0]['attributes']['remaining_days'] == 3
        assert CombatService._exoskeleton_power_profile(character.data)['powered']
        repeat = client.post(url, headers=headers, json=payload)
        assert repeat.status_code == 200 and repeat.json['operation']['duplicate']
        assert character.data['inventory']['pockets'][0]['quantity'] == 1
        assert actor.action_points_current == (3 if mode == 'combat' else 8)
    else:
        assert response.status_code == (403 if mode == 'other_turn' else 400), response.json
        assert character.revision == revision
        assert character.data['inventory']['pockets'][0]['quantity'] == 2
        assert actor.action_points_current == (4 if mode == 'low_ap' else 8)


def test_polymer_regenerator_restores_current_stage_over_game_time():
    assert supported(definition('armor-d26'))
    armor={'name':'Экзоскелет','category':'armor','stage':2,'stageDurability':1000,
        'currentStageDurability':350,'modifications':[make_record('armor-d26')]}
    data={'equipment':{'armor':armor}}
    assert has_timers(data)
    advance(data,300)
    assert armor['currentStageDurability']==400
    advance(data,6000)
    assert armor['currentStageDurability']==1000
    assert armor['stage']==2
    assert not has_timers(data)


def test_spare_plates_restore_two_stages_once_and_free_slot(client,checks,auth_headers):
    assert supported(definition('armor-a49'))
    character=checks['actor_character'];actor=checks['actor_location']
    data=deepcopy(character.data)
    data['equipment']['armor']={'id':'armor-spare','name':'Тяжёлая броня','category':'armor',
        'durability':100,'maxDurability':100,'stage':4,'stageDurability':1000,
        'currentStageDurability':70,'modifications':[make_record('armor-a49')]}
    character.data=data;actor.action_points_current=12;db.session.commit()
    payload={'character_revision':character.revision,'item_id':'armor-spare','operation_id':'plates-once'}
    url=f'/lobbies/characters/{character.id}/spare-plates'
    first=client.post(url,headers=auth_headers(checks['actor_user']),json=payload)
    assert first.status_code==200,first.json
    armor=first.json['data']['equipment']['armor']
    assert armor['stage']==2 and armor['condition']=='2. Немного повреждена'
    assert armor['currentStageDurability']==armor['stageDurability']==1000
    assert armor['durability']==100 and armor['modifications']==[]
    assert actor.action_points_current==2
    repeated=client.post(url,headers=auth_headers(checks['actor_user']),json=payload)
    assert repeated.status_code==200 and repeated.json['operation']['duplicate']
    assert actor.action_points_current==2 and character.data['equipment']['armor']['stage']==2


def test_spare_plates_require_ten_ap_and_do_not_change_armor_on_failure(client,checks,auth_headers):
    character=checks['actor_character'];actor=checks['actor_location']
    data=deepcopy(character.data)
    data['equipment']['armor']={'id':'armor-spare','name':'Тяжёлая броня','category':'armor',
        'durability':100,'stage':5,'modifications':[make_record('armor-a49')]}
    character.data=data;actor.action_points_current=9;db.session.commit()
    payload={'character_revision':character.revision,'item_id':'armor-spare','operation_id':'plates-fail'}
    response=client.post(f'/lobbies/characters/{character.id}/spare-plates',
        headers=auth_headers(checks['actor_user']),json=payload)
    assert response.status_code==400,response.json
    assert character.data['equipment']['armor']['stage']==5
    assert len(character.data['equipment']['armor']['modifications'])==1
    assert actor.action_points_current==9


def test_spare_plates_require_own_turn_even_with_enough_ap(client,checks,auth_headers):
    from app.models import LocationCombatState

    character=checks['actor_character'];actor=checks['actor_location']
    data=deepcopy(character.data)
    armor={'id':'armor-spare','name':'Тяжёлая броня','category':'armor',
        'durability':100,'stage':4,'currentStageDurability':70,'modifications':[make_record('armor-a49')]}
    data['equipment']['armor']=armor
    character.data=data;actor.action_points_current=12
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    state.current_location_character_id=checks['target_location'].id
    db.session.commit()
    revision=character.revision
    response=client.post(f'/lobbies/characters/{character.id}/spare-plates',headers=auth_headers(checks['actor_user']),
        json={'character_revision':revision,'item_id':armor['id'],'operation_id':'plates-other-turn'})
    assert response.status_code==403,response.json
    assert actor.action_points_current==12 and character.revision==revision
    assert character.data['equipment']['armor']==armor


def test_polymer_regenerator_does_not_restore_destroyed_stage_or_other_armor():
    armor={'name':'Экзоскелет','category':'armor','stage':5,'stageDurability':1000,
        'currentStageDurability':0,'modifications':[make_record('armor-d26')]}
    other={'name':'Экзоскелет','stage':2,'stageDurability':1000,'currentStageDurability':350}
    data={'equipment':{'armor':armor},'inventory':{'pockets':[other]}}
    assert not has_timers(data)
    advance(data,600)
    assert armor['currentStageDurability']==0
    assert other['currentStageDurability']==350
    armor['stage']=2
    assert has_timers(data)
    advance(data,600)
    assert armor['currentStageDurability']==100


def test_polymer_regenerator_advances_with_lobby_time(checks):
    character=checks['actor_character']
    data=deepcopy(character.data)
    data['equipment']['armor']={'name':'Экзоскелет','stage':2,'stageDurability':500,
        'currentStageDurability':200,'modifications':[make_record('armor-d26')]}
    character.data=data
    db.session.commit()

    updated=advance_lobby(checks['lobby'],600)
    db.session.commit()

    assert character in updated
    assert character.data['equipment']['armor']['currentStageDurability']==300


def test_polymer_regenerator_advances_with_combat_round(checks):
    actor=checks['actor_location']
    data=deepcopy(actor.character.data)
    data['equipment']['armor']={'name':'Экзоскелет','stage':2,'stageDurability':500,
        'currentStageDurability':200,'modifications':[make_record('armor-d26')]}
    actor.character.data=data

    CombatService._advance_character_time(actor,6)

    assert actor.character.data['equipment']['armor']['currentStageDurability']==201


def test_nvg_clock_battle_and_raised_device_share_light_consumption():
    item={'id':'nvg','slotType':'nvg','lightPower':{'uses_left':10,'elapsed_seconds':0}}
    data={'equipment':{'helmet':{'installedModules':[item]}}}
    advance(data,599);assert item['lightPower']['uses_left']==10
    advance(data,1);assert item['lightPower']['uses_left']==9
    advance(data,battle_end=True);assert item['lightPower']['uses_left']==9
    item['raised']=True;advance(data,1200);assert item['lightPower']['uses_left']==9
    item['raised']=False;start_battle(data);advance(data,battle_end=True)
    assert item['lightPower']['uses_left']==8
    advance(data,4800);assert item['lightPower']['uses_left']==0 and not uses_nvg(data)
    assert CombatService._narrative_skill_check(data,'skills.physical.shooting',darkness=True)['equipment_modifier']==-5


def test_legacy_nvg_without_id_does_not_refill_on_sheet_save():
    old={'equipment':{'helmet':{'installedModules':[{'slotType':'nvg','lightPower':{'uses_left':2,'elapsed_seconds':90}}]}}}
    incoming=deepcopy(old);incoming['equipment']['helmet']['installedModules'][0]['lightPower']['uses_left']=10
    preserve(old,incoming)
    assert incoming['equipment']['helmet']['installedModules'][0]['lightPower']['uses_left']==2


def add_gloves(checks):
    data=deepcopy(checks['actor_character'].data)
    data['equipment']['gloves']={'id':'gloves','name':'Медицинские','category':'gloves','attributes':{'sterile_duration_seconds':3600}}
    data['inventory']['pockets']=[{'id':'alcohol','name':'Спирт','category':'consumable','quantity':1,'uses':3,'maxUses':5}]
    checks['actor_character'].data=data;db.session.commit()
    return data


def save(client,checks,auth_headers,data):
    data=deepcopy(data);data['_revision']=checks['actor_character'].revision
    return client.put(f"/lobbies/characters/{checks['actor_character'].id}",headers=auth_headers(checks['actor_user']),json={'data':data})


def sterilize(client,checks,auth_headers,**extra):
    character=checks['actor_character']
    return client.post(f'/lobbies/characters/{character.id}/sterilize-gloves',headers=auth_headers(checks['actor_user']),json={
        'character_revision':character.revision,'source_id':'alcohol','path':['inventory','pockets',0],**extra})


def test_glove_first_wear_unequip_and_reequip_preserve_timer(client,checks,auth_headers):
    data=add_gloves(checks)
    first=save(client,checks,auth_headers,data);assert first.status_code==200,first.json
    data=deepcopy(checks['actor_character'].data)
    assert data['equipment']['gloves']['sterility']['remaining_seconds']==3600
    advance(data,2400);checks['actor_character'].data=deepcopy(data);db.session.commit()
    gloves=data['equipment'].pop('gloves');data['inventory']['pockets'].append(gloves)
    assert save(client,checks,auth_headers,data).status_code==200
    data=deepcopy(checks['actor_character'].data)
    advance(data,3600)
    gloves=data['inventory']['pockets'].pop()
    assert gloves['sterility']['remaining_seconds']==1200
    gloves['sterility']['remaining_seconds']=3600;data['equipment']['gloves']=gloves
    assert save(client,checks,auth_headers,data).status_code==200
    assert checks['actor_character'].data['equipment']['gloves']['sterility']['remaining_seconds']==1200
    assert infection_multiplier(checks['actor_character'].data)==.5


def test_sterilization_spends_one_use_and_three_ap_without_drinking(client,checks,auth_headers):
    data=add_gloves(checks);data['equipment']['gloves']['sterility']={'remaining_seconds':0}
    checks['actor_character'].data=data;db.session.commit()
    assert infection_multiplier(data)==1
    response=sterilize(client,checks,auth_headers)
    assert response.status_code==200,response.json
    data=response.json['data']
    assert data['inventory']['pockets'][0]['uses']==2
    assert checks['actor_location'].action_points_current==2
    assert data['equipment']['gloves']['sterility']['remaining_seconds']==3600
    assert not data['health'].get('intoxication') and infection_multiplier(data)==.5


@pytest.mark.parametrize('problem',['ap','source','revision'])
def test_invalid_sterilization_cannot_spend_resources(client,checks,auth_headers,problem):
    data=add_gloves(checks)
    if problem=='ap':checks['actor_location'].action_points_current=2
    if problem=='source':data['inventory']['pockets'][0]['name']='Вода';checks['actor_character'].data=data
    db.session.commit();before=deepcopy(checks['actor_character'].data)
    response=sterilize(client,checks,auth_headers,**({'character_revision':0} if problem=='revision' else {}))
    assert response.status_code in {400,409},response.json
    after=checks['actor_character'].data
    assert after['inventory']==before['inventory'] and after['equipment']==before['equipment']
    assert after['health']['current']==before['health']['current']
    assert after['health']['combatMeta']['actionJournal'][-1]['status']=='rejected'


def test_nvg_buttons_charge_and_battery_replacement(client,checks,auth_headers):
    character=checks['actor_character'];data=deepcopy(character.data)
    module=data['equipment']['helmet']['installedModules'][0]
    module.update(id='nvg',lightPower={'uses_left':1,'elapsed_seconds':0})
    data['inventory']['pockets']=[{'id':'battery','name':'Батарейки','quantity':2,'category':'device'}]
    character.data=data;db.session.commit()
    url=f'/lobbies/characters/{character.id}/portable-lights'
    listing=client.get(url,headers=auth_headers(checks['actor_user']))
    assert listing.json['lights'][0]['kind']=='nvg'
    response=client.post(url,headers=auth_headers(checks['actor_user']),json={'operation':'battery','path':['equipment','helmet','installedModules',0],
        'source_id':'nvg','battery_path':['inventory','pockets',0],'character_revision':character.revision})
    assert response.status_code==200,response.json
    assert response.json['data']['equipment']['helmet']['installedModules'][0]['lightPower']['uses_left']==10
    assert response.json['data']['inventory']['pockets'][0]['quantity']==1
    assert checks['actor_location'].action_points_current==3
