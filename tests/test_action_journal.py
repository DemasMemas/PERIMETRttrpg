"""Действие, отказ, долг, права и защита серверной истории."""
from copy import deepcopy

from app.extensions import db
from app.models import DeferredCombatAction
from app.services.action_journal import entries
from app.services.combat import CombatService
from test_grenade_rules import grenade_game


def action(client, case, headers, **changes):
    actor = case['actor_location']
    return client.post(f'/lobbies/{case["lobby"].id}/locations/{actor.location_id}/combat/action',
        headers=headers(case['actor_user']), json={'location_character_id':actor.id,
        'action_key':'narrative_action', 'narrative_action_name':'Проверочное действие',
        'action_points':2, **changes})


def test_completed_and_rejected_actions_keep_real_cost(client,grenade_game,auth_headers):
    actor = grenade_game['actor_location']
    before = actor.action_points_current
    response = action(client,grenade_game,auth_headers)
    assert response.status_code == 200, response.json
    journal = entries(actor.character)
    assert len(journal) == 1 and journal[0]['status'] == 'completed'
    assert journal[0]['action_points'] == 2 and actor.action_points_current == before-2
    response = action(client,grenade_game,auth_headers,action_key='unknown_action')
    assert response.status_code == 400
    journal = entries(actor.character)
    assert len(journal) == 2 and journal[-1]['status'] == 'rejected'
    assert journal[-1]['action_points'] == 0 and actor.action_points_current == before-2


def test_instant_request_id_is_not_executed_twice_after_reload(client,grenade_game,auth_headers):
    actor=grenade_game['actor_location'];before=actor.action_points_current
    response=action(client,grenade_game,auth_headers,pending_action_id='instant-journal-receipt')
    assert response.status_code==200,response.json
    db.session.expire_all()
    assert action(client,grenade_game,auth_headers,pending_action_id='instant-journal-receipt').status_code==409
    assert actor.action_points_current==before-2
    assert len(entries(actor.character))==1
    receipt=db.session.get(DeferredCombatAction,'instant-journal-receipt')
    assert receipt.status=='completed' and receipt.remaining_action_points==0


def test_rejected_then_corrected_request_keeps_real_payment(client,grenade_game,auth_headers):
    assert action(client,grenade_game,auth_headers,pending_action_id='corrected-journal',action_key='unknown_action').status_code==400
    assert action(client,grenade_game,auth_headers,pending_action_id='corrected-journal').status_code==200
    journal=entries(grenade_game['actor_location'].character)
    assert len(journal)==1
    assert journal[0]['status']=='completed' and journal[0]['action_points']==2
    assert 'reason' not in journal[0]


def test_public_roll_survives_journal_reload_without_chat_duplicates(client,grenade_game,auth_headers):
    from app.models import ChatMessage
    lobby=grenade_game['lobby'];user=grenade_game['actor_user']
    roll=ChatMessage(lobby_id=lobby.id,user_id=user['id'],username='System (Roll)',message='Проверка: d20 = 12')
    db.session.add_all([roll,ChatMessage(lobby_id=lobby.id,user_id=user['id'],username=user['username'],message='Обычный разговор')])
    db.session.commit()
    url=f'/lobbies/{lobby.id}/action-journal'
    for _ in range(2):
        response=client.get(url,headers=auth_headers(user))
        assert response.status_code==200
        journal=response.json['entries']
        assert len(journal)==1 and journal[0]['id']==f'chat:{roll.id}' and journal[0]['kind']=='roll'


def test_pending_replay_payment_and_cancel_update_one_entry(client,grenade_game,auth_headers):
    actor = grenade_game['actor_location']; actor.action_points_current = 1; db.session.commit()
    response = action(client,grenade_game,auth_headers,pending_action_id='journal-debt')
    assert response.status_code == 200, response.json
    entry = next(item for item in entries(actor.character) if item['id']=='journal-debt')
    assert entry['status'] == 'paying'
    count = len(entries(actor.character))
    assert action(client,grenade_game,auth_headers,pending_action_id='journal-debt').status_code == 409
    assert len(entries(actor.character)) == count
    row = db.session.get(DeferredCombatAction,'journal-debt')
    row.remaining_action_points = 0; row.status = 'ready'; db.session.commit()
    entry = next(item for item in entries(actor.character) if item['id']=='journal-debt')
    assert entry['status'] == 'ready' and entry['action_points'] == 2
    from app.services.deferred_action import cancel_deferred_action
    cancel_deferred_action(actor,row.id); db.session.commit()
    assert next(item for item in entries(actor.character) if item['id']=='journal-debt')['status'] == 'cancelled'


def test_journal_does_not_disclose_inaccessible_character(client,grenade_game,auth_headers):
    actor = grenade_game['actor_location']
    assert action(client,grenade_game,auth_headers).status_code == 200
    # Отзываем видимость, если общий стенд выдал её наблюдателю.
    actor.character.visible_to = []; actor.character.editable_to = []; db.session.commit()
    url = f'/lobbies/{grenade_game["lobby"].id}/action-journal'
    owner = client.get(url,headers=auth_headers(grenade_game['actor_user']))
    observer = client.get(url,headers=auth_headers(grenade_game['target_user']))
    assert owner.status_code == observer.status_code == 200
    assert len(owner.json['entries']) == 1 and observer.json['entries'] == []
    gm = client.get(url,headers=auth_headers(grenade_game['gm'])) if 'gm' in grenade_game else None
    if gm: assert len(gm.json['entries']) == 1


def test_sheet_save_cannot_forge_journal(client,grenade_game,auth_headers):
    actor = grenade_game['actor_location']
    assert action(client,grenade_game,auth_headers).status_code == 200
    snapshot = client.get(f'/lobbies/characters/{actor.character_id}',headers=auth_headers(grenade_game['actor_user'])).json['data']
    snapshot['health']['combatMeta']['actionJournal'] = []
    response = client.put(f'/lobbies/characters/{actor.character_id}',headers=auth_headers(grenade_game['actor_user']),json={'data':snapshot})
    assert response.status_code == 200, response.json
    assert len(entries(actor.character)) == 1


def test_end_turn_records_without_fake_action_payment(client,grenade_game,auth_headers):
    actor = grenade_game['actor_location']
    CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,actor.id)
    entry = entries(actor.character)[-1]
    assert entry['action'] == 'end_turn' and entry['action_points'] == 0
