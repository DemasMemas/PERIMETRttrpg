"""Граничные случаи обмена: конкурирующие решения, комнаты и вложенные вещи."""
from copy import deepcopy

import pytest
from sqlalchemy.orm.attributes import set_committed_value

from app.extensions import db
from app.models import CharacterInteractionRequest, Lobby, LobbyParticipant
from app.services.character_interaction import CharacterInteractionService
from app.services.exceptions import ConflictError
from test_atomic_inventory import pair, _set_money, _money_request, _respond_money


@pytest.mark.parametrize('decision', ['cancel', 'decline'])
def test_stale_pending_snapshot_cannot_overwrite_a_completed_trade(client, pair, auth_headers, decision):
    _set_money(pair)
    pending = _money_request(client, pair, auth_headers, {'actor_money':100})
    assert _respond_money(client, pair, auth_headers, pending.json['id']).status_code == 200
    row = db.session.get(CharacterInteractionRequest, pending.json['id'])
    # Другой обработчик мог прочитать pending до завершения сделки.
    set_committed_value(row, 'status', 'pending')
    with pytest.raises(ConflictError):
        if decision == 'cancel':
            CharacterInteractionService.cancel_request(row.id, pair['actor_user']['id'])
        else:
            CharacterInteractionService.respond(row.id, pair['target_user']['id'], 'decline')
    db.session.expire_all()
    assert db.session.get(CharacterInteractionRequest, row.id).status == 'completed'
    assert pair['actor_character'].data['inventory']['money'] == 900
    assert pair['target_character'].data['inventory']['money'] == 600


def other_lobby(pair):
    lobby = Lobby(name='Другая комната', gm_id=pair['actor_user']['id'], invite_code='OTHER-TRADE')
    db.session.add(lobby);db.session.flush()
    for user in ('actor_user','target_user'):
        db.session.add(LobbyParticipant(lobby_id=lobby.id,user_id=pair[user]['id']))
    db.session.commit()
    return lobby


def test_trade_creation_rejects_location_from_another_room(client, pair, auth_headers):
    _set_money(pair)
    second = other_lobby(pair)
    result = client.post(f"/lobbies/{second.id}/locations/{pair['location'].id}/character-interactions",
        headers=auth_headers(pair['actor_user']), json={'kind':'trade',
            'actor_location_character_id':pair['actor_location'].id,
            'target_character_id':pair['target_character'].id,'payload':{'actor_money':100}})
    assert result.status_code == 404, result.json
    assert CharacterInteractionRequest.query.count() == 0


def test_trade_response_rejects_request_from_another_room(client, pair, auth_headers):
    _set_money(pair)
    pending = _money_request(client,pair,auth_headers,{'actor_money':100})
    second = other_lobby(pair)
    response = client.post(f"/lobbies/{second.id}/character-interactions/{pending.json['id']}/response",
        headers=auth_headers(pair['target_user']),json={'decision':'accept'})
    assert response.status_code == 404,response.json
    assert db.session.get(CharacterInteractionRequest,pending.json['id']).status == 'pending'
    assert pair['actor_character'].data['inventory']['money'] == 1000


@pytest.mark.parametrize('decision',['ошибка клиента',None,True,[],{}])
def test_unknown_trade_decision_does_not_decline_a_pending_offer(client, pair, auth_headers,decision):
    _set_money(pair)
    pending = _money_request(client,pair,auth_headers,{'actor_money':100})
    response = _respond_money(client,pair,auth_headers,pending.json['id'],decision)
    assert response.status_code == 400,response.json
    assert db.session.get(CharacterInteractionRequest,pending.json['id']).status == 'pending'


@pytest.mark.parametrize('storage',['contents','installedModules','installedMagazine','fixedAmmo','ammo','loadedGrenade'])
def test_trade_does_not_split_a_stack_with_shared_container_contents(client, pair, auth_headers,storage):
    data = deepcopy(pair['actor_character'].data)
    nested={'id':'valuable','name':'Прицел','category':'weapon_module','quantity':1}
    data['inventory']['backpack'] = [{'id':'filled-cases','name':'Кейс','category':'container','quantity':2,
        storage:nested if storage in {'installedMagazine','loadedGrenade'} else [nested]}]
    pair['actor_character'].data = data;db.session.commit()
    before = deepcopy(pair['actor_character'].data)
    response = _money_request(client,pair,auth_headers,{'actor_offer':[
        {'item_id':'filled-cases','path':['inventory','backpack',0],'amount':1}]})
    assert response.status_code == 400,response.json
    assert pair['actor_character'].data == before
    assert CharacterInteractionRequest.query.count() == 0


def test_transferred_id_does_not_collide_with_equipped_item(client, pair, auth_headers):
    data = deepcopy(pair['target_character'].data)
    data['equipment'] = {'helmet':{'id':'actor-bandage','name':'Шлем','category':'helmet'}}
    pair['target_character'].data = data;db.session.commit()
    response = _money_request(client,pair,auth_headers,{'actor_offer':[
        {'item_id':'actor-bandage','path':['inventory','backpack',0],'amount':1}]})
    result = _respond_money(client,pair,auth_headers,response.json['id'])
    assert result.status_code == 200,result.json
    data = pair['target_character'].data
    assert data['equipment']['helmet']['id'] == 'actor-bandage'
    assert data['inventory']['backpack'][-1]['id'] != 'actor-bandage'


def test_balance_overflow_rolls_back_goods_money_and_acceptance(client,pair,auth_headers):
    from app.services.trade import MAX_MONEY
    _set_money(pair)
    data=deepcopy(pair['target_character'].data);data['inventory']['money']=MAX_MONEY
    pair['target_character'].data=data;db.session.commit()
    pending=_money_request(client,pair,auth_headers,{'actor_money':1,'actor_offer':[
        {'item_id':'actor-bandage','path':['inventory','backpack',0],'amount':1}]})
    before=deepcopy(pair['actor_character'].data),deepcopy(pair['target_character'].data)
    response=_respond_money(client,pair,auth_headers,pending.json['id'])
    assert response.status_code==400,response.json
    assert (pair['actor_character'].data,pair['target_character'].data)==before
    assert db.session.get(CharacterInteractionRequest,pending.json['id']).status=='pending'
    assert pair['actor_location'].action_points_current==5


@pytest.mark.parametrize('path',[
    ['inventory'],['equipment'],['inventory','money'],
    ['inventory','backpack',0,'attributes'],['inventory','backpack',0,'installedModules',0],
])
def test_trade_cannot_extract_inventory_roots_or_attached_metadata(client,pair,auth_headers,path):
    _set_money(pair)
    data=deepcopy(pair['actor_character'].data)
    data['inventory']['backpack'][0].update(attributes={'name':'Описание не предмет','category':'weapon'},
        installedModules=[{'id':'attached-module','name':'Глушитель','category':'weapon_module'}])
    pair['actor_character'].data=data;db.session.commit()
    before=deepcopy(data)
    response=_money_request(client,pair,auth_headers,{'actor_offer':[{'path':path,'amount':1}]})
    assert response.status_code==400,response.json
    assert pair['actor_character'].data==before
    assert CharacterInteractionRequest.query.count()==0


@pytest.mark.parametrize('amount',[1,2])
def test_empty_stacked_cases_can_split_and_full_stack_can_move(client,pair,auth_headers,amount):
    data=deepcopy(pair['actor_character'].data)
    contents=[{'id':'inside-case','name':'Бинт','quantity':3}] if amount==2 else []
    data['inventory']['backpack']=[{'id':'cases','name':'Кейс','category':'container','quantity':2,'contents':contents}]
    pair['actor_character'].data=data;db.session.commit()
    pending=_money_request(client,pair,auth_headers,{'actor_offer':[{'path':['inventory','backpack',0],'item_id':'cases','amount':amount}]})
    assert pending.status_code==201,pending.json
    response=_respond_money(client,pair,auth_headers,pending.json['id'])
    assert response.status_code==200,response.json
    moved=pair['target_character'].data['inventory']['backpack'][-1]
    assert moved['quantity']==amount and moved['contents']==contents
    assert len(pair['actor_character'].data['inventory']['backpack'])==2-amount


def test_nested_ids_avoid_collision_with_recipient_weapon_and_magazine(client,pair,auth_headers):
    actor=deepcopy(pair['actor_character'].data)
    actor['inventory']['backpack']=[{'id':'case','name':'Кейс','category':'container','quantity':1,'contents':[
        {'id':'occupied-magazine','name':'Магазин','quantity':1,'category':'magazine','ammo':[
            {'id':'occupied-rounds','name':'Патроны','category':'ammo','quantity':15}]}]}]
    target=deepcopy(pair['target_character'].data)
    target['weapons']=[{'id':'case','name':'Автомат','installedMagazine':{'id':'occupied-magazine','ammo':[
        {'id':'occupied-rounds','name':'Патроны','quantity':25}]}}]
    pair['actor_character'].data=actor;pair['target_character'].data=target;db.session.commit()
    pending=_money_request(client,pair,auth_headers,{'actor_offer':[{'path':['inventory','backpack',0],'item_id':'case','amount':1}]})
    assert _respond_money(client,pair,auth_headers,pending.json['id']).status_code==200
    result=pair['target_character'].data['inventory']['backpack'][-1]
    assert result['id']!='case' and result['contents'][0]['id']!='occupied-magazine'
    rounds=result['contents'][0]['ammo'][0]
    assert rounds['id']!='occupied-rounds' and rounds['quantity']==15
    assert pair['target_character'].data['weapons']==target['weapons']


def test_legacy_duplicate_ids_still_transfer_the_explicitly_selected_item(client,pair,auth_headers):
    actor=deepcopy(pair['actor_character'].data)
    actor['inventory']['backpack']=[{'id':'legacy-duplicate','name':'Дорогая вещь','quantity':1},
                                   {'id':'legacy-duplicate','name':'Дешёвая вещь','quantity':1}]
    pair['actor_character'].data=actor;db.session.commit()
    pending=_money_request(client,pair,auth_headers,{'actor_offer':[{'path':['inventory','backpack',1],
        'item_id':'legacy-duplicate','amount':1}]})
    assert pending.json['payload']['actor_offer'][0]['name']=='Дешёвая вещь'
    assert _respond_money(client,pair,auth_headers,pending.json['id']).status_code==200
    assert pair['actor_character'].data['inventory']['backpack'][0]['name']=='Дорогая вещь'
    assert pair['target_character'].data['inventory']['backpack'][-1]['name']=='Дешёвая вещь'
