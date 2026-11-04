"""Воспроизводимый профиль реального HTTP-выстрела на копии Припяти."""
from copy import deepcopy
from pathlib import Path
import cProfile
import json
import pstats
from time import perf_counter

import pytest
from sqlalchemy import event
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter,LocationCharacter,LocationObject
from app.services.combat import CombatService
from test_grenade_rules import grenade_game


def test_unrelated_floor_does_not_scan_passages(grenade_game):
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+1,actor.pos_y
    db.session.add(LocationObject(location_id=actor.location_id,type='floor',tile_x=30,tile_y=30,
        properties={'level':1,'dimensions':{'width':2,'depth':2,'height':.12}}));db.session.commit()
    profile=cProfile.Profile();profile.enable()
    try:result=CombatService._cover_analysis(actor.location_id,actor,target)
    finally:profile.disable()
    assert not any(getattr(entry.code,'co_name',None)=='opening_at' for entry in profile.getstats())
    assert not result['zones']


def test_crossed_floor_still_blocks_and_real_opening_still_passes(grenade_game):
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    target.pos_x,target.pos_y,target.floor_level=actor.pos_x+2,actor.pos_y,1
    floor=LocationObject(location_id=actor.location_id,type='floor',tile_x=3,tile_y=5,
        properties={'level':1,'building_id':'cover-test','dimensions':{'width':4,'depth':4,'height':.12}})
    db.session.add(floor);db.session.commit()
    assert CombatService._cover_analysis(actor.location_id,actor,target)['zones']['head']['object_id']==floor.id
    hole=LocationObject(location_id=actor.location_id,type='floor_opening',tile_x=3,tile_y=5,
        properties={'level':1,'building_id':'cover-test','dimensions':{'width':4,'depth':4,'height':.12}})
    db.session.add(hole);db.session.commit()
    assert 'head' not in CombatService._cover_analysis(actor.location_id,actor,target)['zones']


@pytest.mark.parametrize('attack_kind',['firearm','mutant'])
def test_pripyat_attack_profile(client,grenade_game,auth_headers,monkeypatch,attack_kind):
    root=Path(__file__).resolve().parents[1];fixture=root/'tmp/pripyat-regression.json'
    if not fixture.is_file():pytest.skip('Копия Припяти недоступна для профиля')
    source=json.loads(fixture.read_text(encoding='utf-8'))
    actor=grenade_game['actor_location'];target=grenade_game['target_location'];location=actor.location
    location.grid_width=source['grid_width'];location.grid_height=source['grid_height'];location.tiles_data=source['tiles_data']
    LocationObject.query.filter_by(location_id=location.id).delete()
    db.session.add_all([LocationObject(location_id=location.id,type=o['type'],name=o.get('name'),
        tile_x=o['tile_x'],tile_y=o['tile_y'],properties=o.get('properties') or {}) for o in source['objects']])
    data=deepcopy(actor.character.data)
    data['weapons']=[{'id':'latency-pistol','name':'Пистолет замера','category':'weapon','subcategory':'Пистолеты','ammo':100,
        'attributes':{'damage':10,'accuracy':100,'range':30,'noise':0,'fire_rate':100,'min_strength':0}}]
    if attack_kind=='mutant':
        data.update(is_mutant=True,basic={'is_mutant':True},mutant={'profile':'Кабан','traits':[]})
        data['weapons']=[{'id':'latency-bite','name':'Клыки замера','category':'melee_weapon','naturalWeapon':True,
            'attributes':{'damage':50,'accuracy':100,'natural_weapon':True,'weight_class':'Легкое',
                'allowed_attacks':['Колющий'],'skip_strength_scaling':True}}]
    actor.character.data=data;actor.drawn_weapon_index=0;actor.pos_x,actor.pos_y=41,48;actor.facing_x,actor.facing_y=1,0
    target.pos_x,target.pos_y=42,48
    target_data=deepcopy(target.character.data);target_data['health']={'current':100000,'max':100000,'effects':[]}
    if attack_kind=='mutant':target_data.update(is_mutant=True,basic={'is_mutant':True},mutant={'profile':'Кабан','traits':[]})
    target.character.data=target_data;flag_modified(actor.character,'data');flag_modified(target.character,'data')
    for index in range(28):
        character=LobbyCharacter(lobby_id=actor.character.lobby_id,owner_id=actor.character.owner_id,name=f'Наблюдатель {index}',
            data={'health':{'current':700,'max':700,'effects':[]}})
        db.session.add(character);db.session.flush()
        db.session.add(LocationCharacter(character_id=character.id,location_id=location.id,pos_x=45+index%7,pos_y=45+index//7))
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:min(b,max(a,10)))
    report=root/'.private-testing/reports';report.mkdir(exist_ok=True)
    times=[];queries=[];plain=[]
    for index in range(6):
        actor.action_points_current=20;db.session.commit()
        count=[0]
        def query(*args):count[0]+=1
        event.listen(db.engine,'before_cursor_execute',query)
        profiler=cProfile.Profile();start=perf_counter()
        if index<3:profiler.enable()
        try:
            response=client.post(f'/lobbies/{grenade_game["lobby"].id}/locations/{location.id}/combat/action',
                headers=auth_headers(grenade_game['actor_user']),json={'location_character_id':actor.id,
                    'action_key':'attack','weapon_index':0,'fire_mode':'unaimed' if attack_kind=='firearm' else None,
                    'attack_type':'Колющий' if attack_kind=='mutant' else None,'shot_count':1,
                    'action_points':1,'target_character_id':target.character_id})
        finally:
            if index<3:profiler.disable();times.append(perf_counter()-start)
            else:plain.append(perf_counter()-start)
            queries.append(count[0]);event.remove(db.engine,'before_cursor_execute',query)
        assert response.status_code==200,response.json
        assert response.json['attack']['results'][0]['hit']
        if index==2:
            with (report/('attack-latency-current-profile.txt' if attack_kind=='firearm' else 'attack-latency-mutant-profile.txt')).open('w',encoding='utf-8') as stream:
                pstats.Stats(profiler,stream=stream).strip_dirs().sort_stats('cumulative').print_stats(45)
    (report/('attack-latency-current.json' if attack_kind=='firearm' else 'attack-latency-mutant.json')).write_text(json.dumps({'scene':'Копия Припяти','characters':30,'attack_kind':attack_kind,
        'objects':len(source['objects']),'seconds':times,'without_profiler_seconds':plain,'sql_queries':queries},ensure_ascii=False,indent=2),encoding='utf-8')
