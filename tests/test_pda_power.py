"""КПК: заряд в игровом времени, включение и замена батарейки."""
from copy import deepcopy
import pytest

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.services.portable_lights import advance, pda_ready, preserve
from test_grenade_rules import grenade_game


@pytest.mark.parametrize('damage,broken',[(200,False),(200.1,True),(500,True)])
def test_electric_anomaly_breaks_unprotected_pda_above_200(grenade_game,damage,broken):
    from app.services.combat import CombatService
    data={'inventory':{'pockets':[{'id':'pda','name':'КПК','pdaState':{'on':True,'charge_percent':50}}]}}
    CombatService._apply_anomaly_equipment_damage(data,damage,{'category':'electric'})
    assert bool(data['inventory']['pockets'][0]['pdaState'].get('broken'))==broken
    assert pda_ready(data)==(not broken)
    advance(data,60);assert bool(data['inventory']['pockets'][0]['pdaState'].get('broken'))==broken


@pytest.mark.parametrize('protected,roll,broken',[(False,10,True),(False,11,False),(True,1,False)])
def test_lightning_rod_recharges_and_dielectric_removes_burn_risk(grenade_game,monkeypatch,protected,roll,broken):
    from app.services.combat import CombatService
    mods=[{'ruleId':'pda-b23'}]+([{'ruleId':'pda-b13'}] if protected else [])
    data={'inventory':{'pockets':[{'id':'pda','name':'КПК','modifications':mods,'pdaState':{'on':False,'charge_percent':20}}]}}
    monkeypatch.setattr('app.services.electronics.random.randint',lambda a,b:roll)
    result=CombatService._apply_anomaly_equipment_damage(data,500,{'category':'electric'})
    state=data['inventory']['pockets'][0]['pdaState']
    assert bool(state.get('broken'))==broken and state['charge_percent']==(20 if broken else 100)
    assert result[0]['recharged']==(not broken)


def test_dielectric_alone_survives_electricity_without_recharging(grenade_game):
    from app.services.combat import CombatService
    data={'inventory':{'pockets':[{'id':'pda','name':'КПК','modifications':[{'ruleId':'pda-b13'}],
        'pdaState':{'on':True,'charge_percent':20}}]}}
    assert CombatService._apply_anomaly_equipment_damage(data,1000,{'category':'electric'})==[]
    assert data['inventory']['pockets'][0]['pdaState']=={'on':True,'charge_percent':20}


def test_dynamo_requires_modification_one_free_hand_and_stops_at_combat_start(client,grenade_game,auth_headers):
    from app.models import LocationCombatState
    from app.services.combat import CombatService
    from app.services.portable_lights import held_hand
    pair=grenade_game;actor=pair['actor_location']
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one();state.status='idle'
    actor.drawn_weapon_index=None
    data=deepcopy(actor.character.data);data['inventory']['pockets']=[{'id':'dynamo-pda','name':'КПК','category':'misc',
        'quantity':1,'pdaState':{'on':False,'charge_percent':50}}]
    actor.character.data=data;db.session.commit()
    url=f'/lobbies/characters/{actor.character_id}/portable-lights'
    def operation(name):return client.post(url,headers=auth_headers(pair['actor_user']),json={
        'operation':name,'path':['inventory','pockets',0],'source_id':'dynamo-pda','character_revision':actor.character.revision})
    assert operation('pda_dynamo_start').status_code==400
    data=deepcopy(actor.character.data);data['inventory']['pockets'][0]['modifications']=[{'ruleId':'pda-b7'}]
    actor.character.data=data;db.session.commit()
    response=operation('pda_dynamo_start');assert response.status_code==200,response.json
    assert held_hand(actor.character.data)
    from app.services.weapon_grip import draw_plan
    from app.services.exceptions import ValidationError
    with pytest.raises(ValidationError,match='рук'):
        draw_plan(actor.character.data,{'name':'Винтовка','category':'weapon'},'both_hands')
    CombatService.move_character(actor.location_id,pair['actor_user']['id'],actor.character_id,6.1,5.2)
    assert held_hand(actor.character.data)
    assert advance(actor.character.data,59)
    device=actor.character.data['inventory']['pockets'][0];assert device['pdaState']['charge_percent']==50
    assert advance(actor.character.data,1);assert device['pdaState']['charge_percent']==51
    before=deepcopy(actor.character.data);incoming=deepcopy(before);incoming['health']['combatMeta'].pop('pdaDynamo')
    preserve(before,incoming);assert incoming['health']['combatMeta']['pdaDynamo']==before['health']['combatMeta']['pdaDynamo']
    assert operation('pda_dynamo_stop').status_code==200 and not held_hand(actor.character.data)
    assert operation('pda_dynamo_start').status_code==200
    CombatService.start_combat(actor.location_id,pair['lobby'].gm_id,[actor.id],actor.id)
    assert not held_hand(actor.character.data) and 'pdaDynamo' not in actor.character.data['health']['combatMeta']
    assert operation('pda_dynamo_start').status_code==400


def test_dynamo_loses_partial_minute_on_stop_and_finishes_at_full_charge(client,grenade_game,auth_headers):
    from app.services.portable_lights import start_battle,held_hand
    data={'inventory':{'pockets':[{'id':'pda','name':'КПК','modifications':[{'ruleId':'pda-b7'}],
        'pdaState':{'on':False,'charge_percent':99}}]},'health':{'combatMeta':{'pdaDynamo':{'id':'pda','elapsed_seconds':0}}}}
    advance(data,30);assert data['inventory']['pockets'][0]['pdaState']['charge_percent']==99
    assert start_battle(data) and not held_hand(data)
    data['health']['combatMeta']['pdaDynamo']={'id':'pda','elapsed_seconds':0}
    advance(data,30);assert data['inventory']['pockets'][0]['pdaState']['charge_percent']==99
    advance(data,30);assert data['inventory']['pockets'][0]['pdaState']['charge_percent']==100 and not held_hand(data)


def test_pda_power_toggle_drain_and_paid_battery(client, grenade_game, auth_headers):
    actor=grenade_game['actor_location']
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[
        {'id':'test-pda','name':'КПК','category':'misc','quantity':1},
        {'id':'test-batteries','name':'Батарейки','category':'device','quantity':2},
    ]
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    url=f'/lobbies/characters/{actor.character_id}/portable-lights'
    headers=auth_headers(grenade_game['actor_user'])
    def operation(name,**extra):
        return client.post(url,headers=headers,json={'operation':name,'path':['inventory','pockets',0],
            'source_id':'test-pda','character_revision':actor.character.revision,**extra})
    assert pda_ready(actor.character.data)
    initial=client.get(url,headers=headers)
    assert initial.status_code==200
    assert next(item for item in initial.json['lights'] if item['kind']=='pda')['pda_state']['charge_percent']==100
    before=actor.action_points_current
    assert operation('pda_toggle').status_code==200
    assert not pda_ready(actor.character.data)
    assert not advance(actor.character.data,3600)
    assert actor.character.data['inventory']['pockets'][0]['pdaState']['charge_percent']==100
    assert operation('pda_toggle').status_code==200
    assert actor.action_points_current==before-2
    assert advance(actor.character.data,3600)
    assert actor.character.data['inventory']['pockets'][0]['pdaState']['charge_percent']==99.5
    data=deepcopy(actor.character.data)
    data['inventory']['pockets'][0]['pdaState']['charge_percent']=0
    actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
    assert not pda_ready(actor.character.data)
    assert operation('pda_toggle').status_code==200
    assert operation('pda_toggle').status_code==400
    actor.action_points_current=3;db.session.commit()
    assert operation('pda_battery',battery_path=['inventory','pockets',1]).status_code==400
    assert actor.character.data['inventory']['pockets'][1]['quantity']==2
    assert actor.character.data['inventory']['pockets'][0]['pdaState']['charge_percent']==0
    actor.action_points_current=10;db.session.commit()
    before=actor.action_points_current
    response=operation('pda_battery',battery_path=['inventory','pockets',1])
    assert response.status_code==200,response.json
    assert actor.action_points_current==before-4
    assert actor.character.data['inventory']['pockets'][0]['pdaState']['charge_percent']==100
    assert actor.character.data['inventory']['pockets'][1]['quantity']==1
    assert not pda_ready(actor.character.data)
    assert operation('pda_toggle').status_code==200
    assert pda_ready(actor.character.data)
    assert advance(actor.character.data,720000)
    assert actor.character.data['inventory']['pockets'][0]['pdaState']['charge_percent']==0
    assert not pda_ready(actor.character.data)


def test_pda_drain_uses_global_time_and_sheet_cannot_refill(client, grenade_game, auth_headers):
    actor=grenade_game['actor_location'];lobby=grenade_game['lobby']
    data=deepcopy(actor.character.data)
    data['inventory']['pockets']=[{'name':'КПК','category':'misc','quantity':1,
        'pdaState':{'on':True,'charge_percent':50}}]
    actor.character.data=data;actor.character.time_active=False
    flag_modified(actor.character,'data');db.session.commit()
    response=client.patch(f'/lobbies/{lobby.id}/time',headers=auth_headers(grenade_game['actor_user']),
        json={'game_day':lobby.game_day,'game_time_minutes':lobby.game_time_minutes+60})
    assert response.status_code==200,response.json
    current=actor.character.data
    assert current['inventory']['pockets'][0]['pdaState']['charge_percent']==49.5
    incoming=deepcopy(current)
    incoming['inventory']['pockets'][0]['pdaState']['charge_percent']=100
    preserve(current,incoming)
    assert incoming['inventory']['pockets'][0]['pdaState']['charge_percent']==49.5


def test_pda_catalog_migration_keeps_custom_template(app, monkeypatch):
    import importlib.util
    from pathlib import Path
    from app.models.templates import ItemTemplate
    path=Path(__file__).resolve().parents[1]/'migrations/versions/e91c7b2a4f60_update_pda_battery_rule.py'
    spec=importlib.util.spec_from_file_location('pda_battery_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    item=ItemTemplate(name='КПК',category='misc',price=1500,
        description='КПК для связи и хранения электронных книг. Батарейка работает одну игровую неделю.',
        attributes={'import_source':'equipment_workbook','battery_duration_seconds':604800})
    db.session.add(item);db.session.flush()
    monkeypatch.setattr(migration.op,'get_bind',lambda:db.session.connection())
    migration.upgrade();db.session.expire(item)
    assert item.attributes['battery_duration_seconds']==720000
    assert item.description.endswith('0,5% заряда батарейки за игровой час.')
    item.description='Описание ГМа';db.session.flush()
    migration.upgrade();db.session.expire(item)
    assert item.description=='Описание ГМа'
