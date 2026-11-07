"""Дозиметр: реальное питание и право игрока видеть радиацию."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models import LocationCombatState
from app.models.templates import ItemTemplate
from app.services.character_starting_purchase import item_from_template
from app.services.electronics import templates,active,advance,preserve,DOSIMETER_SECONDS
from test_grenade_rules import grenade_game


@pytest.fixture
def dosimeter_game(grenade_game):
    pair=grenade_game
    for suffix in ('location','character','user'):
        pair['actor_'+suffix],pair['target_'+suffix]=pair['target_'+suffix],pair['actor_'+suffix]
    actor=pair['actor_location']
    LocationCombatState.query.filter_by(location_id=actor.location_id).one().status='idle'
    row=ItemTemplate(**templates()[0]);db.session.add(row);db.session.flush()
    data=deepcopy(actor.character.data);data['inventory']['pockets']=[item_from_template(row),{'id':'battery','name':'Батарейки','category':'device','quantity':2}]
    actor.character.data=data;db.session.commit();return pair


def operate(client,pair,headers,operation,**extra):
    actor=pair['actor_location'];item=actor.character.data['inventory']['pockets'][0]
    return client.post(f'/lobbies/characters/{actor.character_id}/portable-lights',headers=headers(pair['actor_user']),json={
        'operation':operation,'path':['inventory','pockets',0],'source_id':item['id'],'character_revision':actor.character.revision,**extra})


def test_dosimeter_controls_radiation_readings_and_uses_one_battery(client,dosimeter_game,auth_headers):
    pair=dosimeter_game;actor=pair['actor_location'];url=f"/lobbies/{pair['lobby'].id}/radiation-access?character_id={actor.character_id}"
    def allowed(user):return client.get(url,headers=auth_headers(user))
    assert not allowed(pair['actor_user']).json['can_read']
    assert allowed(pair['target_user']).json['can_read']
    forbidden=client.get(f"/lobbies/{pair['lobby'].id}/radiation-access?character_id={pair['target_character'].id}",headers=auth_headers(pair['actor_user']))
    assert forbidden.status_code==403
    assert operate(client,pair,auth_headers,'dosimeter_toggle').status_code==200
    assert allowed(pair['actor_user']).json['can_read'] and active(actor.character.data)
    assert not client.get(url+'&location_id=999999',headers=auth_headers(pair['actor_user'])).json['can_read']
    assert advance(actor.character.data,DOSIMETER_SECONDS-1) and active(actor.character.data)
    assert advance(actor.character.data,1) and not active(actor.character.data)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(actor.character,'data')
    db.session.commit()
    assert operate(client,pair,auth_headers,'dosimeter_toggle').status_code==400
    response=operate(client,pair,auth_headers,'dosimeter_battery',battery_path=['inventory','pockets',1]);assert response.status_code==200,response.json
    assert actor.character.data['inventory']['pockets'][1]['quantity']==1
    assert not active(actor.character.data)
    assert operate(client,pair,auth_headers,'dosimeter_toggle').status_code==200
    assert active(actor.character.data)


def test_dosimeter_power_is_protected_and_off_device_does_not_drain(dosimeter_game):
    data=dosimeter_game['actor_character'].data
    assert not advance(data,3600)
    before=deepcopy(data);incoming=deepcopy(data);incoming['inventory']['pockets'][0]['deviceState']={'on':True,'remaining_seconds':1}
    preserve(before,incoming);assert incoming['inventory']['pockets'][0]['deviceState']=={'on':False,'remaining_seconds':DOSIMETER_SECONDS}


def test_dosimeter_migration_is_repeatable_and_preserves_id(dosimeter_game,monkeypatch):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'migrations/versions/f6c38b1de497_add_dosimeter_template.py'
    spec=importlib.util.spec_from_file_location('dosimeter_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    row=ItemTemplate.query.filter_by(name='Дозиметр').one();identity=row.id
    monkeypatch.setattr(migration.op,'get_bind',lambda:db.session.connection());migration.upgrade();migration.upgrade()
    assert ItemTemplate.query.filter_by(name='Дозиметр').one().id==identity
