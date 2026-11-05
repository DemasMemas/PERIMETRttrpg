"""Запрос игрового времени: изоляция владельцев, согласование и один бросок."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import LobbyCharacter, LobbyParticipant, ChatMessage
from test_character_interactions import _setup_pair


@pytest.fixture
def plans(client,create_user,auth_headers):
    case=_setup_pair(client,create_user,auth_headers)
    other=LobbyCharacter(lobby_id=case['lobby'].id,owner_id=case['target_user']['id'],
        name='Второй персонаж',data=deepcopy(case['target_character'].data))
    db.session.add(other);db.session.commit()
    case['other_character']=other
    return case


def api(client,case,auth_headers,who,method,path,payload=None):
    return getattr(client,method)(f"/lobbies/{case['lobby'].id}/time-plans{path}",
        headers=auth_headers(case[who]),json=payload)


def create(client,case,auth_headers,**overrides):
    return api(client,case,auth_headers,'actor_user','post','',{
        'request_id':'time-plan-request-001','owner_ids':[case['target_user']['id']],
        'window_minutes':120,**overrides})


def entries(*durations):
    return [{'id':f'activity-{index:03d}','name':f'Дело {index}',
        'duration_minutes':duration,'note':''} for index,duration in enumerate(durations)]


def test_gm_request_creates_independent_plans_and_owner_sees_only_theirs(client,plans,auth_headers):
    created=create(client,plans,auth_headers)
    assert created.status_code==200,created.json
    assert len(created.json['plans'])==2
    assert {p['character_id'] for p in created.json['plans']}=={
        plans['target_character'].id,plans['other_character'].id}
    assert create(client,plans,auth_headers).json['replayed'] is True
    owner=api(client,plans,auth_headers,'target_user','get','')
    assert len(owner.json['plans'])==2 and not owner.json['owners']
    assert create(client,plans,auth_headers,window_minutes=60).status_code==409
    assert api(client,plans,auth_headers,'target_user','post','',{
        'request_id':'another-request','owner_ids':[plans['actor_user']['id']],
        'window_minutes':60}).status_code==403


def test_owners_cannot_read_each_others_plans(client,plans,auth_headers,create_user,monkeypatch):
    third=create_user('third-time-owner')
    db.session.add(LobbyParticipant(lobby_id=plans['lobby'].id,user_id=third['id']))
    third_character=LobbyCharacter(lobby_id=plans['lobby'].id,owner_id=third['id'],
        name='Третий персонаж',data=deepcopy(plans['target_character'].data))
    db.session.add(third_character);db.session.commit()
    notices=[]
    monkeypatch.setattr('app.services.time_planning.socketio.emit',
        lambda event,payload,**kwargs:notices.append((payload['plan_id'],kwargs.get('room')))
        if event=='time_plans_changed' else None)
    created=create(client,plans,auth_headers,owner_ids=[plans['target_user']['id'],third['id']])
    assert created.status_code==200 and len(created.json['plans'])==3
    for plan in created.json['plans']:
        rooms={room for plan_id,room in notices if plan_id==plan['id']}
        assert rooms=={f"user_{plans['actor_user']['id']}",f"user_{plan['owner_id']}"}
    own=api(client,plans,auth_headers,'target_user','get','').json['plans']
    assert {entry['character_id'] for entry in own}=={
        plans['target_character'].id,plans['other_character'].id}
    plans['third_user']=third
    other=api(client,plans,auth_headers,'third_user','get','').json['plans']
    assert [entry['character_id'] for entry in other]==[third_character.id]
    hidden=next(entry for entry in created.json['plans'] if entry['character_id']==third_character.id)
    assert api(client,plans,auth_headers,'target_user','put',f"/{hidden['id']}",{
        'revision':hidden['revision'],'activities':entries(10),'confirm':True}).status_code==403


def test_partial_confirmation_overflow_review_and_one_real_roll(client,plans,auth_headers,monkeypatch):
    before_time=(plans['lobby'].game_day,plans['lobby'].game_time_minutes)
    before_data=deepcopy(plans['target_character'].data)
    created=create(client,plans,auth_headers).json
    first,second=created['plans']
    saved=api(client,plans,auth_headers,'target_user','put',f"/{first['id']}",{
        'revision':first['revision'],'activities':entries(40),'confirm':True})
    assert saved.status_code==200,saved.json
    assert saved.json['free_minutes']==80 and saved.json['confirmed']
    assert (plans['lobby'].game_day,plans['lobby'].game_time_minutes)==before_time
    assert plans['target_character'].data==before_data
    assert api(client,plans,auth_headers,'target_user','put',f"/{first['id']}",{
        'revision':first['revision'],'activities':entries(40),'confirm':True}).json['replayed']
    assert api(client,plans,auth_headers,'actor_user','put',f"/{second['id']}",{
        'revision':second['revision'],'activities':entries(10),'confirm':True}).status_code==403
    assert api(client,plans,auth_headers,'actor_user','post',f"/{first['id']}/review",{
        'revision':saved.json['revision'],'action':'approve_activity','activity_id':'activity-000'}).status_code==200
    after=api(client,plans,auth_headers,'actor_user','get','').json['plans']
    first_now=next(p for p in after if p['id']==first['id'])
    assert first_now['activities'][0]['approved'] and second['confirmed'] is False
    rolled=[]
    def dice(low,high):
        rolled.append((low,high));return 15
    monkeypatch.setattr('app.services.combat.random.randint',dice)
    result=api(client,plans,auth_headers,'actor_user','post',f"/{first['id']}/roll",{
        'activity_id':'activity-000','skill_path':'skills.physical.strength','difficulty':10})
    assert result.status_code==200,result.json
    assert result.json['activities'][0]['roll']['success'] is True
    replay=api(client,plans,auth_headers,'actor_user','post',f"/{first['id']}/roll",{
        'activity_id':'activity-000','skill_path':'skills.physical.strength','difficulty':10})
    assert replay.status_code==200 and replay.json['replayed'] is True
    assert len(rolled)==1
    assert ChatMessage.query.filter_by(lobby_id=plans['lobby'].id,username='План времени').count()==1


def test_overflow_blocks_approval_until_gm_agrees(client,plans,auth_headers):
    first=create(client,plans,auth_headers).json['plans'][0]
    saved=api(client,plans,auth_headers,'target_user','put',f"/{first['id']}",{
        'revision':first['revision'],'activities':entries(80,60),'confirm':True}).json
    assert saved['overflow_minutes']==20 and saved['overflow_state']=='pending'
    blocked=api(client,plans,auth_headers,'actor_user','post',f"/{first['id']}/review",{
        'revision':saved['revision'],'action':'approve_activity','activity_id':'activity-001'})
    assert blocked.status_code==400
    denied=api(client,plans,auth_headers,'actor_user','post',f"/{first['id']}/review",{
        'revision':saved['revision'],'action':'deny_overflow'}).json
    assert denied['overflow_state']=='denied'
    approved=api(client,plans,auth_headers,'actor_user','post',f"/{first['id']}/review",{
        'revision':denied['revision'],'action':'approve_overflow'}).json
    assert approved['overflow_state']=='approved'
    activity=api(client,plans,auth_headers,'actor_user','post',f"/{first['id']}/review",{
        'revision':approved['revision'],'action':'approve_activity','activity_id':'activity-001'})
    assert activity.status_code==200 and activity.json['activities'][1]['approved']
    changed=api(client,plans,auth_headers,'target_user','put',f"/{first['id']}",{
        'revision':activity.json['revision'],'activities':entries(80,60,10),'confirm':True})
    assert changed.status_code==409
