from copy import deepcopy

import pytest

from app.extensions import db
from app.models import DeferredCombatAction, LobbyCharacter, LocationCharacter, LocationCombatState
from app.models.templates import ItemTemplate
from app.services.exceptions import ValidationError
from app.services.magazine import install_magazine
from test_character_interactions import _setup_pair


@pytest.fixture
def case(client, create_user, auth_headers):
    pair = _setup_pair(client, create_user, auth_headers)
    weapon_template = ItemTemplate(name='Test SMG', category='weapon', attributes={'caliber': '9 x 19', 'fixedMagazine': False})
    db.session.add(weapon_template)
    db.session.flush()
    template = ItemTemplate(name='Test magazine', category='magazine', attributes={
        'caliber': '9х19', 'capacity': 30, 'reload_time_od': 6,
        'compatible_weapons': [weapon_template.id],
    })
    db.session.add(template)
    db.session.flush()
    magazine = {
        'id': 'new-magazine', 'templateId': template.id, 'category': 'magazine', 'name': template.name,
        'quantity': 1, 'volume': 0.5, 'emptyWeight': 0.1, 'loadedWeight': 0.4,
        'createdByPlayer': True, 'customNote': 'Keep this', 'attributes': deepcopy(template.attributes),
        'ammo': [
            {'templateId': 999001, 'name': 'Standard', 'quantity': 4, 'attributes': {'caliber': '9*19'}},
            {'templateId': 999001, 'name': 'RIP', 'quantity': 2, 'ammo_variant': 'rip', 'attributes': {'caliber': '9x19', 'damage': 150}},
        ],
    }
    old = {
        'id': 'old-magazine', 'name': 'Old', 'templateId': template.id, 'capacity': 30,
        'caliber': '9x19', 'emptyWeight': 0.1, 'loadedWeight': 0.4,
        'createdByPlayer': True, 'customNote': 'Old note', 'volume': 0.7,
        'ammo': [{'name': 'BP', 'quantity': 3, 'ammo_variant': 'bp', 'attributes': {'caliber': '9x19'}}],
    }
    character = pair['actor_character']
    character.data = {**character.data, 'weapons': [{
        'id': 'weapon', 'templateId': weapon_template.id, 'name': weapon_template.name, 'ergonomics': 60,
        'installedMagazine': old, 'ammo': 3,
    }], 'inventory': {'backpack': [], 'pockets': []}, 'equipment': {'belt': {'pouches': [{'contents': [magazine]}]}}}
    pair['actor_location'].action_points_current = 2
    db.session.commit()
    return {**pair, 'magazine': magazine, 'old': old, 'weapon_template': weapon_template, 'template': template,
            'path': ['equipment', 'belt', 'pouches', 0, 'contents', 0]}


def start(client, case, auth_headers, **extra):
    return client.post(f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat/action",
                       headers=auth_headers(case['actor_user']), json={
                           'location_character_id': case['actor_location'].id,
                           'action_key': 'reload_weapon', 'weapon_index': 0,
                           'magazine_template_id': case['template'].id, 'item_path': case['path'],
                           'pending_action_id': 'magazine-install', **extra,
                       })


def pay(client, case, auth_headers):
    response = client.post(f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat/end_turn",
                           headers=auth_headers(case['target_user']), json={})
    assert response.status_code == 200, response.json
    return response.json


def resume(client, case, auth_headers):
    return client.post(f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat/action",
                       headers=auth_headers(case['actor_user']), json={
                           'location_character_id': case['actor_location'].id,
                           'action_key': 'reload_weapon', 'resume_pending_action_id': 'magazine-install',
                       })


def contents(data):
    return data['equipment']['belt']['pouches'][0]['contents']


def test_deferred_reload_installs_only_after_payment_and_survives_reconnect(client, case, auth_headers):
    assert start(client, case, auth_headers).status_code == 200
    data = case['actor_character'].data
    assert data['weapons'][0]['installedMagazine']['id'] == 'old-magazine'
    assert contents(data)[0]['id'] == 'new-magazine'
    state = pay(client, case, auth_headers)
    assert state['current_character']['deferred_action']['server_executable'] is True
    db.session.expire_all()
    response = resume(client, case, auth_headers)
    assert response.status_code == 200, response.json
    data = case['actor_character'].data
    installed = data['weapons'][0]['installedMagazine']
    assert installed['id'] == 'new-magazine'
    assert installed['ammo'] == case['magazine']['ammo']
    assert installed['customNote'] == 'Keep this'
    assert installed['createdByPlayer'] is True
    assert data['weapons'][0]['ammo'] == 6
    restored = contents(data)[0]
    assert restored['id'] == 'old-magazine'
    assert restored['ammo'] == case['old']['ammo']
    assert restored['volume'] == 0.7
    assert restored['customNote'] == 'Old note'
    assert restored['createdByPlayer'] is True
    assert restored['currentAmmo'] == 3
    assert restored['weight'] == 0.4
    assert case['actor_location'].action_points_current == 1
    assert resume(client, case, auth_headers).status_code == 409
    assert data['weapons'][0]['ammo'] == 6


def test_immediate_reload_spends_and_swaps_in_one_request(client, case, auth_headers):
    case['actor_location'].action_points_current = 8
    db.session.commit()
    response = start(client, case, auth_headers, inventory_retrieval_action_points=1)
    assert response.status_code == 200, response.json
    assert not response.json.get('pending_action')
    assert response.json['reload_weapon']['ammo'] == 6
    assert response.json['reload_weapon']['inventory_retrieval_action_points'] == 0
    assert case['actor_location'].action_points_current == 2
    assert contents(case['actor_character'].data)[0]['id'] == 'old-magazine'


def test_reload_uses_server_inventory_access_instead_of_client_numbers(client, case, auth_headers):
    data = deepcopy(case['actor_character'].data)
    magazine = data['equipment']['belt']['pouches'][0]['contents'].pop()
    data['inventory']['backpack'] = [magazine]
    case['actor_character'].data = data
    case['actor_location'].action_points_current = 8
    db.session.commit()

    response = start(
        client, case, auth_headers,
        item_path=['inventory', 'backpack', 0],
        inventory_retrieval_action_points=0,
        inventory_use_action_discount=20,
    )

    assert response.status_code == 200, response.json
    details = response.json['reload_weapon']
    assert details['inventory_retrieval_action_points'] == 2
    assert details['inventory_use_action_discount'] == 0
    assert details['action_points'] == 8
    assert case['actor_location'].action_points_current == 0


@pytest.mark.parametrize('change', ['missing', 'replaced', 'ammo', 'weapon'])
def test_changed_selection_cannot_consume_another_item(client, case, auth_headers, change):
    assert start(client, case, auth_headers).status_code == 200
    pay(client, case, auth_headers)
    data = deepcopy(case['actor_character'].data)
    if change == 'missing':
        contents(data).clear()
    elif change == 'replaced':
        contents(data)[0]['id'] = 'someone-else'
    elif change == 'ammo':
        contents(data)[0]['ammo'][0]['quantity'] -= 1
    else:
        data['weapons'][0]['id'] = 'other-weapon'
    case['actor_character'].data = data
    db.session.commit()
    response = resume(client, case, auth_headers)
    assert response.status_code == 400, response.json
    meta=data['health']['combatMeta']
    meta.pop('pendingAction',None);meta.pop('completedPendingActionId',None)
    assert case['actor_character'].data == data
    assert db.session.get(DeferredCombatAction, 'magazine-install').status == 'cancelled'
    assert case['actor_location'].action_points_current == 1


@pytest.mark.parametrize('kind', ['caliber', 'compatibility', 'fixed', 'loader', 'capacity', 'loaded_caliber', 'bad_path'])
def test_invalid_selection_does_not_pay_or_change_inventory(client, case, auth_headers, kind):
    data = deepcopy(case['actor_character'].data)
    magazine = contents(data)[0]
    extra = {}
    if kind == 'caliber':
        magazine['attributes']['caliber'] = '7.62x39'
    elif kind == 'compatibility':
        case['template'].attributes = {**case['template'].attributes, 'compatible_weapons': [999999]}
    elif kind == 'fixed':
        case['weapon_template'].attributes = {**case['weapon_template'].attributes, 'fixedMagazine': True}
    elif kind == 'loader':
        magazine['attributes']['isLoader'] = True
    elif kind == 'capacity':
        magazine['ammo'][0]['quantity'] = 100
    elif kind == 'loaded_caliber':
        magazine['ammo'][0]['attributes']['caliber'] = '7.62x25'
    else:
        extra['item_path'] = ['weapons', 0]
    case['actor_character'].data = data
    db.session.commit()
    response = start(client, case, auth_headers, **extra)
    assert response.status_code == 400, response.json
    assert case['actor_location'].action_points_current == 2
    assert case['actor_character'].data == data
    assert DeferredCombatAction.query.count() == 0


def test_failed_commit_rolls_back_installation_and_ready_marker(client, case, auth_headers, monkeypatch):
    start(client, case, auth_headers)
    pay(client, case, auth_headers)
    before = deepcopy(case['actor_character'].data)
    def fail():
        db.session.flush()
        raise ValidationError('Injected failure')
    monkeypatch.setattr(db.session, 'commit', fail)
    response = resume(client, case, auth_headers)
    assert response.status_code == 400
    assert case['actor_character'].data == before
    assert db.session.get(DeferredCombatAction, 'magazine-install').status == 'ready'


def outside(client, case, auth_headers, *, user=None, revision=None):
    return client.post(f"/lobbies/characters/{case['actor_character'].id}/magazine",
                       headers=auth_headers(user or case['actor_user']), json={
                           'weapon_index': 0, 'item_path': case['path'], 'magazine_template_id': case['template'].id,
                           'character_revision': revision if revision is not None else case['actor_character'].revision,
                       })


def test_outside_combat_installation_needs_no_model_distance_and_rejects_stale_revision(client, case, auth_headers):
    LocationCombatState.query.one().status = 'idle'
    db.session.commit()
    revision = case['actor_character'].revision
    response = outside(client, case, auth_headers)
    assert response.status_code == 200, response.json
    assert response.json['data']['weapons'][0]['installedMagazine']['id'] == 'new-magazine'
    assert response.json['data']['_revision'] > revision
    assert outside(client, case, auth_headers, revision=revision).status_code == 409


def test_outside_endpoint_cannot_bypass_combat_or_permissions(client, case, auth_headers):
    assert outside(client, case, auth_headers).status_code == 400
    assert outside(client, case, auth_headers, user=case['target_user']).status_code == 403
    assert case['actor_location'].action_points_current == 2


def test_round_trip_keeps_all_magazine_metadata_and_ammo(case):
    data = deepcopy(case['actor_character'].data)
    install_magazine(data, 0, case['path'])
    install_magazine(data, 0, case['path'])
    restored = contents(data)[0]
    assert restored['id'] == case['magazine']['id']
    assert restored['ammo'] == case['magazine']['ammo']
    assert restored['volume'] == case['magazine']['volume']
    assert restored['customNote'] == 'Keep this'
    assert restored['createdByPlayer'] is True
