"""Структура генератора НПС не придумывает уровни и численные профили."""
from app.extensions import db
from app.models import NpcGenerationConfig, LobbyCharacter, NpcCreationReceipt, LocationCharacter
from app.models.templates import ItemTemplate
from app.services.npc_generation import preview_name
from test_character_interactions import _setup_pair
from test_npc_loot import pool, profile


def config(**changes):
    danger={'id':'danger_low','name':'Уровень автора А'}
    wealth={'id':'wealth_low','name':'Уровень автора Б'}
    return {'revision':0,'names':['Имя автора 1','Имя автора 2'],
        'danger_levels':[danger],'wealth_levels':[wealth],
        'combinations':[{'danger_id':danger['id'],'wealth_id':wealth['id'],'profile':profile()}],
        **changes}


def test_gm_saves_independent_level_pair_and_can_reroll_name(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    url=f"/lobbies/{pair['lobby'].id}/npc-generation-config"
    empty=client.get(url,headers=auth_headers(pair['actor_user']))
    assert empty.status_code==200 and empty.json['revision']==0
    assert empty.json['names']==[] and empty.json['combinations']==[]
    saved=client.put(url,headers=auth_headers(pair['actor_user']),json=config())
    assert saved.status_code==200,saved.json
    assert saved.json['revision']==1
    assert saved.json['combinations'][0]['profile']==profile()
    assert NpcGenerationConfig.query.count()==1
    repeated=client.put(url,headers=auth_headers(pair['actor_user']),json=config())
    assert repeated.status_code==200 and repeated.json['replayed']
    assert NpcGenerationConfig.query.count()==1
    suggested=preview_name(pair['lobby'],pair['actor_user']['id'],exclude='Имя автора 1')
    assert suggested=={'name':'Имя автора 2','source':'gm_name_pool'}


def test_config_is_private_and_invalid_pair_cannot_replace_it(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    url=f"/lobbies/{pair['lobby'].id}/npc-generation-config"
    assert client.put(url,headers=auth_headers(pair['actor_user']),json=config()).status_code==200
    assert client.get(url,headers=auth_headers(pair['target_user'])).status_code==403
    assert client.post(url+'/name-preview',headers=auth_headers(pair['target_user']),json={}).status_code==403
    bad=config(revision=1,combinations=[{'danger_id':'missing123','wealth_id':'wealth_low','profile':profile()}])
    response=client.put(url,headers=auth_headers(pair['actor_user']),json=bad)
    assert response.status_code==400,response.json
    assert client.get(url,headers=auth_headers(pair['actor_user'])).json['combinations']==config()['combinations']


def test_npc_creation_uses_named_pair_without_numeric_profile_and_replays(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    db.session.add(ItemTemplate(name='Бинт',category='consumable',subcategory='Медицина',
        item_class='1',price=50,attributes={}))
    db.session.commit()
    url=f"/lobbies/{pair['lobby'].id}/npc-generation-config"
    assert client.put(url,headers=auth_headers(pair['actor_user']),json=config()).status_code==200
    create={'operation_id':'npc-create-123','name':'Имя автора 1',
        'danger_id':'danger_low','wealth_id':'wealth_low'}
    first=client.post(url+'/create',headers=auth_headers(pair['actor_user']),json=create)
    assert first.status_code==201,first.json
    identifier=first.json['character']['id']
    npc=LobbyCharacter.query.get(identifier)
    assert npc.data['basic']['is_npc'] is True
    assert npc.data['npcGenerator']=={'name_source':'gm_name_pool','danger_id':'danger_low',
        'wealth_id':'wealth_low','numeric_profile_id':None,'group_id':None,'faction_id':None}
    assert npc.data['npcLoot']['profile']==profile()
    assert npc.data['npcLoot']['initial_receipt']['spent']==100
    assert npc.data['npcLoot']['initial_receipt']['money']==10
    assert [item['name'] for item in npc.data['inventory']['backpack']]==['Бинт','Бинт']
    assert len({item['id'] for item in npc.data['inventory']['backpack']})==2
    assert npc.data['inventory']['money']==10
    assert NpcCreationReceipt.query.count()==1
    assert len(npc.data['inventory']['backpack'])==2 and npc.data['inventory']['money']==10
    replay=client.post(url+'/create',headers=auth_headers(pair['actor_user']),json=create)
    assert replay.status_code==200 and replay.json['replayed']
    assert replay.json['character']['id']==identifier
    assert NpcCreationReceipt.query.count()==1
    changed=client.post(url+'/create',headers=auth_headers(pair['actor_user']),json={**create,'name':'Другое имя'})
    assert changed.status_code==409


def test_npc_creation_with_unmatched_pool_rolls_back_entire_npc(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    url=f"/lobbies/{pair['lobby'].id}/npc-generation-config"
    assert client.put(url,headers=auth_headers(pair['actor_user']),json=config()).status_code==200
    before=LobbyCharacter.query.count()
    response=client.post(url+'/create',headers=auth_headers(pair['actor_user']),json={
        'operation_id':'npc-missing-loot','name':'Имя автора 1',
        'danger_id':'danger_low','wealth_id':'wealth_low'})
    assert response.status_code==400,response.json
    assert LobbyCharacter.query.count()==before and NpcCreationReceipt.query.count()==0


def test_generated_npc_wears_compatible_armor_and_helmet_once(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    db.session.add_all([
        ItemTemplate(name='Защита А',category='armor',item_class='1',price=100,attributes={'max_durability':80}),
        ItemTemplate(name='Шлем А',category='helmet',item_class='1',price=50,attributes={'max_durability':40}),
    ]);db.session.commit()
    selected=profile(budget=150,money_min=0,money_max=0,pools=[
        pool(category='armor',subcategory='',draws=1),
        pool(category='helmet',subcategory='',draws=1)])
    settings=config(combinations=[{'danger_id':'danger_low','wealth_id':'wealth_low','profile':selected}])
    url=f"/lobbies/{pair['lobby'].id}/npc-generation-config"
    assert client.put(url,headers=auth_headers(pair['actor_user']),json=settings).status_code==200
    payload={'operation_id':'npc-equipped-123','name':'Имя автора 1','danger_id':'danger_low','wealth_id':'wealth_low'}
    created=client.post(url+'/create',headers=auth_headers(pair['actor_user']),json=payload)
    assert created.status_code==201,created.json
    npc=db.session.get(LobbyCharacter,created.json['character']['id'])
    assert npc.data['equipment']['armor']['name']=='Защита А'
    assert npc.data['equipment']['helmet']['name']=='Шлем А'
    assert npc.data['npcLoot']['initial_receipt']['equipped']==[
        {'slot':slot,'item_id':npc.data['equipment'][slot]['id']} for slot in ('armor','helmet')]
    assert not npc.data['inventory']['backpack']
    replay=client.post(url+'/create',headers=auth_headers(pair['actor_user']),json=payload)
    assert replay.status_code==200 and replay.json['replayed']
    assert not db.session.get(LobbyCharacter,npc.id).data['inventory']['backpack']


def test_incompatible_generated_helmet_and_mask_cancel_npc(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    db.session.add_all([
        ItemTemplate(name='Шлем с забралом А',category='helmet',item_class='1',price=50,
            attributes={'integrated_visor':True,'max_durability':40}),
        ItemTemplate(name='Противогаз А',category='gas_mask',item_class='1',price=50,attributes={}),
    ]);db.session.commit()
    selected=profile(budget=100,money_min=0,money_max=0,pools=[
        pool(category='helmet',subcategory='',draws=1),
        pool(category='gas_mask',subcategory='',draws=1)])
    url=f"/lobbies/{pair['lobby'].id}/npc-generation-config"
    assert client.put(url,headers=auth_headers(pair['actor_user']),json=config(combinations=[
        {'danger_id':'danger_low','wealth_id':'wealth_low','profile':selected}])).status_code==200
    before=LobbyCharacter.query.count()
    created=client.post(url+'/create',headers=auth_headers(pair['actor_user']),json={
        'operation_id':'npc-incompatible-123','name':'Имя автора 1',
        'danger_id':'danger_low','wealth_id':'wealth_low'})
    assert created.status_code==400,created.json
    assert LobbyCharacter.query.count()==before and NpcCreationReceipt.query.count()==0


def test_generated_exoskeleton_is_preconfigured_without_invented_tool_action(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    db.session.add(ItemTemplate(name='Экзоскелет',category='armor',item_class='4',price=500,
        attributes={'is_exoskeleton':True,'max_durability':100}));db.session.commit()
    selected=profile(budget=500,money_min=0,money_max=0,pools=[
        pool(category='armor',subcategory='',item_class='4',max_price=500,draws=1)])
    url=f"/lobbies/{pair['lobby'].id}/npc-generation-config"
    assert client.put(url,headers=auth_headers(pair['actor_user']),json=config(combinations=[
        {'danger_id':'danger_low','wealth_id':'wealth_low','profile':selected}])).status_code==200
    created=client.post(url+'/create',headers=auth_headers(pair['actor_user']),json={
        'operation_id':'npc-exoskeleton-123','name':'Имя автора 1',
        'danger_id':'danger_low','wealth_id':'wealth_low'})
    assert created.status_code==201,created.json
    npc=db.session.get(LobbyCharacter,created.json['character']['id'])
    assert npc.data['equipment']['armor']['isExoskeleton'] is True
    assert npc.data['npcLoot']['initial_receipt']['equipped'][0]['slot']=='armor'


def test_generated_npc_can_be_placed_with_equipment_unchanged(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    db.session.add(ItemTemplate(name='Защита А',category='armor',item_class='1',price=100,
        attributes={'max_durability':80}));db.session.commit()
    selected=profile(budget=100,money_min=0,money_max=0,pools=[
        pool(category='armor',subcategory='',draws=1)])
    config_url=f"/lobbies/{pair['lobby'].id}/npc-generation-config"
    assert client.put(config_url,headers=auth_headers(pair['actor_user']),json=config(combinations=[
        {'danger_id':'danger_low','wealth_id':'wealth_low','profile':selected}])).status_code==200
    created=client.post(config_url+'/create',headers=auth_headers(pair['actor_user']),json={
        'operation_id':'npc-place-123','name':'Имя автора 1',
        'danger_id':'danger_low','wealth_id':'wealth_low'})
    assert created.status_code==201,created.json
    npc_id=created.json['character']['id']
    before=db.session.get(LobbyCharacter,npc_id).data['equipment']['armor']['id']
    hidden=client.get(f'/lobbies/characters/{npc_id}',headers=auth_headers(pair['target_user']))
    assert hidden.status_code==403
    placed=client.post(f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/spawn_character",
        headers=auth_headers(pair['actor_user']),json={'character_id':npc_id,'tile_x':3,'tile_y':2,'floor_level':0})
    assert placed.status_code==200,placed.json
    location=LocationCharacter.query.filter_by(location_id=pair['location'].id,character_id=npc_id).one()
    assert (location.pos_x,location.pos_y,location.floor_level)==(3,2,0)
    assert db.session.get(LobbyCharacter,npc_id).data['equipment']['armor']['id']==before


def test_player_cannot_create_npc_even_through_old_route(client,create_user,auth_headers):
    pair=_setup_pair(client,create_user,auth_headers)
    lobby_id=pair['lobby'].id
    response=client.post(f'/lobbies/{lobby_id}/characters',headers=auth_headers(pair['target_user']),
        json={'name':'Чужой НПС','data':{'basic':{'is_npc':True}}})
    assert response.status_code==403
    response=client.post(f'/lobbies/{lobby_id}/npc-generation-config/create',
        headers=auth_headers(pair['target_user']),json={'operation_id':'other-npc-123','name':'НПС',
            'danger_id':None,'wealth_id':None})
    assert response.status_code==403
