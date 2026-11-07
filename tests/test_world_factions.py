"""Собственные группировки мира без придуманных названий и отношений."""
from app.models import WorldFactionConfig
from app.services.world_factions_preset import ready_world_factions
from test_character_interactions import _setup_pair


def settings(**changes):
    return {'revision':0,'source':'custom','groups':[
        {'id':'group-first','name':'Группа автора А','factions':[
            {'id':'faction-first','name':'Отделение автора'}]},
        {'id':'group-second','name':'Группа автора Б','factions':[]}],
        'relations':[{'from_id':'group-first','to_id':'group-second','value':'Отношение автора'}],**changes}


def test_author_ready_groups_can_be_copied_without_changing_source(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    gm=auth_headers(pair['actor_user']);player=auth_headers(pair['target_user'])
    payload={'name':'Мир с группировками автора','map_type':'empty',
        'chunks_width':1,'chunks_height':1,'faction_source':'ready'}
    created=client.post('/lobbies/',headers=gm,json=payload)
    assert created.status_code==201,created.json
    url=f"/lobbies/{created.json['id']}/world-factions"
    ready=client.get(url,headers=gm)
    assert ready.status_code==200 and ready.json['source']=='ready'
    expected=ready_world_factions()
    assert ready.json['groups']==expected['groups']
    assert ready.json['relations']==expected['relations']
    assert len(expected['groups'])==13 and len(expected['relations'])==34
    assert all(not group['factions'] for group in expected['groups'])
    names={group['id']:group['name'] for group in expected['groups']}
    assert all(names[relation['from_id']]!='ЧНИГ Светило'
        for relation in expected['relations'])
    assert client.get(url,headers=player).status_code==403
    changed={**ready.json,'groups':[dict(group) for group in ready.json['groups']]}
    changed['groups'][0]={**changed['groups'][0],'name':'Название ГМа'}
    rejected=client.put(url,headers=gm,json=changed)
    assert rejected.status_code==400,(rejected.status_code,rejected.json)
    copied={**changed,'source':'ready_customized'}
    saved=client.put(url,headers=gm,json=copied)
    assert saved.status_code==200,saved.json
    assert saved.json['groups'][0]['id']==expected['groups'][0]['id']
    assert saved.json['groups'][0]['name']=='Название ГМа'
    second=client.post('/lobbies/',headers=gm,json={**payload,'name':'Другой готовый мир'})
    assert second.status_code==201,second.json
    other=client.get(f"/lobbies/{second.json['id']}/world-factions",headers=gm)
    assert other.json['groups']==expected['groups'] and other.json['source']=='ready'
    editable=client.post('/lobbies/',headers=gm,json={**payload,
        'name':'Мир с донастройкой','faction_source':'ready_customized'})
    assert editable.status_code==201,editable.json
    edit_url=f"/lobbies/{editable.json['id']}/world-factions"
    starting=client.get(edit_url,headers=gm).json
    assert starting['source']=='ready_customized' and starting['groups']==expected['groups']
    starting['groups'][0]['name']='Другое название ГМа'
    assert client.put(edit_url,headers=gm,json=starting).status_code==200


def test_gm_can_save_rename_and_replay_world_factions(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    url=f"/lobbies/{pair['lobby'].id}/world-factions"
    gm=auth_headers(pair['actor_user'])
    empty=client.get(url,headers=gm)
    assert empty.status_code==200 and empty.json=={'revision':0,'source':'custom','groups':[],'relations':[]}
    saved=client.put(url,headers=gm,json=settings())
    assert saved.status_code==200,saved.json
    assert saved.json['revision']==1 and WorldFactionConfig.query.count()==1
    repeated=client.put(url,headers=gm,json=settings())
    assert repeated.status_code==200 and repeated.json['replayed']
    changed=settings(revision=1)
    changed['groups'][0]['name']='Переименовано ГМом'
    renamed=client.put(url,headers=gm,json=changed)
    assert renamed.status_code==200,renamed.json
    assert renamed.json['groups'][0]['id']=='group-first'
    assert renamed.json['relations'][0]['from_id']=='group-first'


def test_player_cannot_read_or_change_groups_and_invalid_links_preserve_config(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    url=f"/lobbies/{pair['lobby'].id}/world-factions"
    gm=auth_headers(pair['actor_user']);player=auth_headers(pair['target_user'])
    assert client.put(url,headers=gm,json=settings()).status_code==200
    assert client.get(url,headers=player).status_code==403
    assert client.put(url,headers=player,json=settings(revision=1)).status_code==403
    bad=settings(revision=1)
    bad['relations'][0]['to_id']='removed-group'
    result=client.put(url,headers=gm,json=bad)
    assert result.status_code==400,result.json
    assert client.get(url,headers=gm).json['groups']==settings()['groups']


def test_used_faction_cannot_be_removed_without_reassignment(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    url=f"/lobbies/{pair['lobby'].id}/world-factions"
    gm=auth_headers(pair['actor_user'])
    assert client.put(url,headers=gm,json=settings()).status_code==200
    character=pair['target_character']
    assignment=client.put(url+f'/characters/{character.id}',headers=gm,json={
        'character_revision':character.revision,'group_id':'group-first','faction_id':'faction-first'})
    assert assignment.status_code==200,assignment.json
    changed=settings(revision=1)
    changed['groups'][0]['factions']=[]
    result=client.put(url,headers=gm,json=changed)
    assert result.status_code==400,result.json
    assert client.get(url,headers=gm).json['groups'][0]['factions'][0]['id']=='faction-first'
    moved=settings(revision=1)
    moved['groups'][1]['factions']=moved['groups'][0]['factions']
    moved['groups'][0]['factions']=[]
    assert client.put(url,headers=gm,json=moved).status_code==400


def test_only_gm_assigns_valid_nested_faction_and_sheet_cannot_override_it(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    url=f"/lobbies/{pair['lobby'].id}/world-factions"
    gm=auth_headers(pair['actor_user']);player=auth_headers(pair['target_user'])
    assert client.put(url,headers=gm,json=settings()).status_code==200
    character=pair['target_character'];assign=url+f'/characters/{character.id}'
    payload={'character_revision':character.revision,'group_id':'group-first','faction_id':'faction-first'}
    assert client.put(assign,headers=player,json=payload).status_code==403
    assert client.put(assign,headers=gm,json={**payload,'group_id':'group-second'}).status_code==400
    assigned=client.put(assign,headers=gm,json=payload)
    assert assigned.status_code==200,assigned.json
    assert character.data['basic']['faction_id']=='faction-first'
    snapshot=character.data_snapshot();snapshot['basic']['group_id']='group-second'
    snapshot['basic']['faction_id']=None
    sheet=client.put(f'/lobbies/characters/{character.id}',headers=player,json={'data':snapshot})
    assert sheet.status_code==200,sheet.json
    assert character.data['basic']['group_id']=='group-first'
    assert character.data['basic']['faction_id']=='faction-first'


def test_unapproved_ready_set_cannot_be_selected_during_lobby_creation(client,create_user,auth_headers):
    user=create_user('faction_creator')
    url='/lobbies/'
    response=client.post(url,headers=auth_headers(user),json={'name':'Мир','map_type':'empty',
        'faction_source':'approved'})
    assert response.status_code==400
    own=client.post(url,headers=auth_headers(user),json={'name':'Свой мир','map_type':'empty',
        'faction_source':'custom'})
    assert own.status_code==201,own.json
    config=client.get(f"/lobbies/{own.json['id']}/world-factions",headers=auth_headers(user))
    assert config.status_code==200 and config.json['source']=='custom'
