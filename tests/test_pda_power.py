"""КПК: заряд в игровом времени, включение и замена батарейки."""
from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.services.portable_lights import advance, pda_ready, preserve
from test_grenade_rules import grenade_game


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
