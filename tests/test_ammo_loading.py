from copy import deepcopy

import pytest

from app.extensions import db
from app.models import DeferredCombatAction, LocationCombatState
from app.services.ammo_loading import load_ammunition, loading_bindings
from app.services.exceptions import ValidationError
from test_magazine_installation import case, pay


@pytest.fixture
def loading_case(case):
    data = deepcopy(case['actor_character'].data)
    data['inventory']['pockets'] = [{
        'id': 'ammo', 'templateId': 999001, 'name': '9x19', 'category': 'ammo', 'quantity': 12,
        'volume': 0.02, 'attributes': {'caliber': '9 x 19', 'damage': 50},
    }]
    case['actor_character'].data = data
    case['actor_location'].free_actions_current = 1
    db.session.commit()
    return case


def request(case, **extra):
    return {'target_path': case['path'], 'source_path': ['inventory', 'pockets', 0],
            'quantity': 3, 'action_points': 6, 'free_actions': 1, **extra}


def start(client, case, auth_headers, **extra):
    return client.post(f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat/action",
                       headers=auth_headers(case['actor_user']), json={
                           'location_character_id': case['actor_location'].id, 'action_key': 'load_ammunition',
                           'loading': request(case, **extra), 'pending_action_id': 'ammo-loading',
                       })


def resume(client, case, auth_headers):
    return client.post(f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat/action",
                       headers=auth_headers(case['actor_user']), json={
                           'location_character_id': case['actor_location'].id, 'action_key': 'load_ammunition',
                           'resume_pending_action_id': 'ammo-loading',
                           'loading': {'quantity': 999},  # Saved arguments, never replacement arguments.
                       })


def target(data, case):
    return data['equipment']['belt']['pouches'][0]['contents'][0]


def test_deferred_ammo_survives_reconnect_and_is_consumed_once(client, loading_case, auth_headers):
    case = loading_case
    assert start(client, case, auth_headers).status_code == 200
    assert case['actor_character'].data['inventory']['pockets'][0]['quantity'] == 12
    assert case['actor_location'].free_actions_current == 0
    state = pay(client, case, auth_headers)
    assert state['current_character']['deferred_action']['server_executable']
    free_actions = case['actor_location'].free_actions_current
    db.session.expire_all()
    response = resume(client, case, auth_headers)
    assert response.status_code == 200, response.json
    data = case['actor_character'].data
    assert data['inventory']['pockets'][0]['quantity'] == 9
    assert target(data, case)['currentAmmo'] == 9
    assert target(data, case)['ammo'][1]['ammo_variant'] == 'rip'
    assert target(data, case)['ammo'][-1]['attributes']['damage'] == 50
    assert case['actor_location'].action_points_current == 1
    assert case['actor_location'].free_actions_current == free_actions
    assert resume(client, case, auth_headers).status_code == 409
    assert db.session.get(DeferredCombatAction, 'ammo-loading').status == 'completed'


@pytest.mark.parametrize('change', ['source', 'target', 'quantity', 'caliber'])
def test_changed_loading_selection_cancels_paid_action_without_inventory_loss(client, loading_case, auth_headers, change):
    case = loading_case
    assert start(client, case, auth_headers).status_code == 200
    pay(client, case, auth_headers)
    data = deepcopy(case['actor_character'].data)
    if change == 'source':
        data['inventory']['pockets'][0]['id'] = 'replacement'
    elif change == 'target':
        target(data, case)['id'] = 'replacement'
    elif change == 'quantity':
        data['inventory']['pockets'][0]['quantity'] = 10
    else:
        data['inventory']['pockets'][0]['attributes']['caliber'] = '7.62x39'
    case['actor_character'].data = data
    db.session.commit()
    resources=(case['actor_location'].action_points_current,case['actor_location'].free_actions_current)
    response = resume(client, case, auth_headers)
    assert response.status_code == 400, response.json
    assert db.session.get(DeferredCombatAction, 'ammo-loading').status == 'cancelled'
    expected=deepcopy(data)
    expected['health']['combatMeta'].pop('pendingAction',None)
    expected['health']['combatMeta'].pop('completedPendingActionId',None)
    assert case['actor_character'].data == expected
    assert (case['actor_location'].action_points_current,case['actor_location'].free_actions_current)==resources
    assert resume(client,case,auth_headers).status_code==409
    assert case['actor_character'].data==expected


def test_payment_and_loading_roll_back_together(client, loading_case, auth_headers, monkeypatch):
    import app.services.ammo_loading as service
    case = loading_case
    before = deepcopy(case['actor_character'].data)
    original = service.load_ammunition

    def broken(data, request, **kwargs):
        original(data, request, **kwargs)
        db.session.flush()
        raise ValidationError('Injected error')

    monkeypatch.setattr(service, 'load_ammunition', broken)
    response = start(client, case, auth_headers, action_points=2)
    assert response.status_code == 400
    assert case['actor_location'].action_points_current == 2
    assert case['actor_location'].free_actions_current == 1
    history=case['actor_character'].data['health']['combatMeta']['actionJournal']
    assert len(history)==1 and history[0]['status']=='rejected' and history[0]['action_points']==0
    before['health'].setdefault('combatMeta',{})['actionJournal']=deepcopy(history)
    assert case['actor_character'].data == before


def test_full_loading_outside_combat_uses_revision_and_publishes_snapshot(client, loading_case, auth_headers):
    case = loading_case
    state = LocationCombatState.query.filter_by(location_id=case['location'].id).one()
    state.status = 'idle'
    db.session.commit()
    payload = {'character_revision': case['actor_character'].revision, 'loading': request(case, full=True)}
    url = f"/lobbies/characters/{case['actor_character'].id}/ammunition"
    response = client.post(url, headers=auth_headers(case['actor_user']), json=payload)
    assert response.status_code == 200, response.json
    assert response.json['loading'] == {'quantity': 12, 'ammo': 18}
    assert response.json['data']['inventory']['pockets'] == []
    assert client.post(url, headers=auth_headers(case['actor_user']), json=payload).status_code == 409


def test_free_loading_cannot_bypass_combat(client, loading_case, auth_headers):
    case = loading_case
    response = client.post(f"/lobbies/characters/{case['actor_character'].id}/ammunition",
                           headers=auth_headers(case['actor_user']), json={
                               'character_revision': case['actor_character'].revision, 'loading': request(case, full=True),
                           })
    assert response.status_code == 400


@pytest.mark.parametrize('bad', [
    {'quantity': -1}, {'quantity': 2}, {'quantity': 31}, {'quantity': True}, {'full': True},
    {'action_points': -1}, {'action_points': 0, 'free_actions': 0}, {'target_path': ['weapons', 0, 'installedMagazine']},
])
def test_invalid_loading_does_not_spend(client, loading_case, auth_headers, bad):
    case = loading_case
    response = start(client, case, auth_headers, **bad)
    assert response.status_code == 400, response.json
    assert case['actor_location'].action_points_current == 2
    assert case['actor_location'].free_actions_current == 1
    assert DeferredCombatAction.query.count() == 0


def test_loading_tariff_without_inventory_preparation_is_rejected(
        client, loading_case, auth_headers):
    case = loading_case
    response = start(
        client, case, auth_headers,
        quantity=1, action_points=1, free_actions=0,
    )

    assert response.status_code == 400, response.json
    assert 'доставание' in response.json['error']['message'].lower()
    assert case['actor_location'].action_points_current == 2
    assert case['actor_location'].free_actions_current == 1
    assert DeferredCombatAction.query.count() == 0


def test_stale_initial_selection_rejected(client, loading_case, auth_headers):
    case = loading_case
    selection = loading_bindings(case['actor_character'].data, request(case))
    selection['source_path']['id'] = 'old-selection'
    assert start(client, case, auth_headers, selection=selection).status_code == 409


@pytest.mark.parametrize('fixed,weapon_name,subcategory,device_name,allowed', [
    (False, '', '', 'Лента на 5 патронов', True),
    (False, '', '', 'Спидлоадер для револьвера', False),
    (True, 'Револьвер', 'Пистолеты', 'Лента на 5 патронов', False),
    (True, 'Револьвер', 'Пистолеты', 'Спидлоадер для револьвера', True),
    (True, 'Винтовка', 'Снайперские винтовки', 'Лента на 5 патронов', True),
    (True, 'Дробовик', 'Дробовики', 'Спидлоадер для револьвера', False),
])
def test_loading_devices_respect_weapon_type(loading_case, fixed, weapon_name, subcategory, device_name, allowed):
    case = loading_case
    data = deepcopy(case['actor_character'].data)
    source = data['inventory']['pockets'][0]
    source.update(category='magazine', name=device_name, ammo=[deepcopy(source)])
    source['quantity'] = 1
    source['ammo'][0]['quantity'] = 5
    if fixed:
        data['weapons'] = [{'name': weapon_name, 'subcategory': subcategory,
                            'attributes': {'fixedMagazine': True, 'caliber': '9x19', 'magazine_size': 6}, 'fixedAmmo': []}]
    selection = request(case, full=True)
    if fixed:
        selection.pop('target_path')
        selection['weapon_index'] = 0
    if not allowed:
        with pytest.raises(ValidationError):
            load_ammunition(data, selection)
        return
    assert load_ammunition(data, selection)['quantity'] == 5
    assert source['ammo'] == []
    assert source['currentAmmo'] == 0


@pytest.mark.parametrize('caliber', ['7.62x39', '12x70 Картечь'])
def test_loaded_device_checks_actual_ammunition(loading_case, caliber):
    case = loading_case
    data = deepcopy(case['actor_character'].data)
    source = data['inventory']['pockets'][0]
    source.update(category='magazine', name='Лента на 5 патронов', ammo=[deepcopy(source)])
    source['ammo'][0]['attributes']['caliber'] = caliber
    with pytest.raises(ValidationError, match='Калибр'):
        load_ammunition(data, request(case, full=True))


def test_feeder_moves_ammo_without_being_consumed(loading_case):
    case = loading_case
    data = deepcopy(case['actor_character'].data)
    feeder = {'id': 'feeder', 'category': 'magazine', 'name': 'Подавач', 'quantity': 1}
    data['inventory']['pockets'].append(feeder)
    selection = request(case, quantity=10, feeder_path=['inventory', 'pockets', 1])
    selection['selection'] = loading_bindings(data, selection)
    assert load_ammunition(data, selection, combat=True)['quantity'] == 10
    assert data['inventory']['pockets'][1] == feeder
    assert data['combatMagazineLoading']['feederId'] == 'feeder'


@pytest.mark.parametrize('caliber,ammo_caliber', [
    ('12 x 70', '12х70 Картечь'), ('12*70', '12х70 Пуля'), ('.45acp', '.45аср'),
    ('7.62x25', '7,62х25'), ('9x19', '9*19'),
])
def test_caliber_spellings_and_fixed_full_loading(loading_case, caliber, ammo_caliber):
    data = deepcopy(loading_case['actor_character'].data)
    source = data['inventory']['pockets'][0]
    source['attributes']['caliber'] = ammo_caliber
    data['weapons'] = [{'attributes': {'fixedMagazine': True, 'magazine_size': 5.0, 'caliber': caliber}, 'fixedAmmo': None}]
    result = load_ammunition(data, {'weapon_index': 0, 'source_path': ['inventory', 'pockets', 0], 'full': True})
    assert result == {'quantity': 5, 'ammo': 5}
    assert source['quantity'] == 7
    assert data['weapons'][0]['fixedAmmo'][0]['attributes']['caliber'] == ammo_caliber


def test_source_before_target_in_same_container_and_empty_loader(loading_case):
    data = deepcopy(loading_case['actor_character'].data)
    source = data['inventory']['pockets'][0]
    magazine = {'id': 'empty-belt', 'category': 'magazine', 'name': 'Лента на 15 патронов', 'attributes': {'capacity': 15}}
    data['inventory']['pockets'].append(magazine)
    load_ammunition(data, {'target_path': ['inventory', 'pockets', 1], 'source_path': ['inventory', 'pockets', 0], 'full': True})
    assert data['inventory']['pockets'] == [magazine]
    assert magazine['attributes']['caliber'] == '9x19'
    assert magazine['currentAmmo'] == 12


def test_mixed_loader_preserves_lifo_order_and_partial_stack(loading_case):
    case = loading_case
    data = deepcopy(case['actor_character'].data)
    target(data, case)['attributes']['capacity'] = 9
    source = data['inventory']['pockets'][0]
    standard = deepcopy(source)
    standard['quantity'] = 4
    rip = deepcopy(standard)
    rip['ammo_variant'] = 'rip'
    rip['quantity'] = 2
    source.update(category='magazine', name='Лента на 10 патронов', quantity=1, ammo=[standard, rip])
    assert load_ammunition(data, request(case, full=True))['quantity'] == 3
    ammo = target(data, case)['ammo']
    assert ammo[-2]['ammo_variant'] == 'rip'
    assert ammo[-2]['quantity'] == 2
    assert ammo[-1].get('ammo_variant') is None
    assert ammo[-1]['quantity'] == 1
    assert source['ammo'] == [{**standard, 'quantity': 3}]


def test_missing_feeder_after_payment_cannot_finish(client, loading_case, auth_headers):
    case = loading_case
    data = deepcopy(case['actor_character'].data)
    data['inventory']['pockets'].append({'id': 'feeder', 'category': 'magazine', 'name': 'Подавач'})
    case['actor_character'].data = data
    db.session.commit()
    assert start(client, case, auth_headers, quantity=10, feeder_path=['inventory', 'pockets', 1]).status_code == 200
    pay(client, case, auth_headers)
    data = deepcopy(case['actor_character'].data)
    data['inventory']['pockets'].pop()
    case['actor_character'].data = data
    db.session.commit()
    assert resume(client, case, auth_headers).status_code == 409
    assert db.session.get(DeferredCombatAction, 'ammo-loading').status == 'ready'


def test_other_player_cannot_charge_or_resume(client, loading_case, auth_headers):
    case = loading_case
    url = f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat/action"
    response = client.post(url, headers=auth_headers(case['target_user']), json={
        'location_character_id': case['actor_location'].id, 'action_key': 'load_ammunition',
        'loading': request(case), 'pending_action_id': 'someone-else',
    })
    assert response.status_code == 403
    assert start(client, case, auth_headers).status_code == 200
    pay(client, case, auth_headers)
    response = client.post(url, headers=auth_headers(case['target_user']), json={
        'location_character_id': case['actor_location'].id, 'action_key': 'load_ammunition',
        'resume_pending_action_id': 'ammo-loading',
    })
    assert response.status_code == 403
