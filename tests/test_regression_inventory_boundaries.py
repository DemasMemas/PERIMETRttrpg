"""I+: серверные границы хранения/экипировки. Подготовка, все случаи NOT_RUN."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import ConsumableUse, DeferredCombatAction, LobbyCharacter, LocationCharacter, LocationCombatState
from app.services.exceptions import ValidationError
from test_character_interactions import _setup_pair
from test_magazine_installation import case as magazine_case
from test_equipment_actions import add_template, inventory_item
from test_equipment_modules import templates as filter_templates, character_data as filter_data


@pytest.fixture
def pair(client, create_user, auth_headers):
    return _setup_pair(client, create_user, auth_headers)


def data(pair):
    db.session.expire_all()
    return deepcopy(db.session.get(LobbyCharacter, pair['actor_character'].id).data)


def save(pair, value):
    pair['actor_character'].data = deepcopy(value)
    db.session.commit()


def snapshot(pair):
    value = data(pair)
    actor = db.session.get(LocationCharacter, pair['actor_location'].id)
    return (value, actor.character.revision, actor.action_points_current,
            actor.free_actions_current, actor.movement_points_current,
            [(r.id, r.status, r.remaining_action_points, r.revision, deepcopy(r.payload))
             for r in DeferredCombatAction.query.order_by(DeferredCombatAction.id).all()],
            ConsumableUse.query.count())


def action(client, pair, auth_headers, body):
    return client.post(f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/action",
        headers=auth_headers(pair['actor_user']),
        json={'location_character_id': pair['actor_location'].id, **body})


def pay_to_ready(client, pair, auth_headers, action_id, unchanged):
    for _ in range(4):
        db.session.expire_all()
        row = db.session.get(DeferredCombatAction, action_id)
        assert row.status in {'paying', 'ready'}
        current = data(pair)
        for key in ('inventory', 'equipment', 'weapons'):
            assert current.get(key) == unchanged.get(key)
        if row.status == 'ready':
            assert row.remaining_action_points == 0
            return
        response = client.post(f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/end_turn",
            headers=auth_headers(pair['target_user']), json={'location_character_id': pair['target_location'].id})
        assert response.status_code == 200, response.get_json()
    pytest.fail('Оплата не завершилась за предусмотренные ходы')


def execute(client, pair, auth_headers, body, action_id):
    unchanged = data(pair)
    response = action(client, pair, auth_headers, {**body, 'pending_action_id': action_id})
    assert response.status_code == 200, response.get_json()
    deferred = response.get_json().get('pending_action', False)
    if deferred:
        pay_to_ready(client, pair, auth_headers, action_id, unchanged)
        response = action(client, pair, auth_headers,
                          {'action_key': body['action_key'], 'resume_pending_action_id': action_id})
        assert response.status_code == 200, response.get_json()
        assert db.session.get(DeferredCombatAction, action_id).status == 'completed'
        after = snapshot(pair)
        replay = action(client, pair, auth_headers,
                        {'action_key': body['action_key'], 'resume_pending_action_id': action_id})
        assert replay.status_code == 409, replay.get_json()
        assert snapshot(pair) == after
    else:
        assert db.session.get(DeferredCombatAction, action_id) is None
    return response.get_json()


def at_path(value, path):
    for part in path:
        value = value[part]
    return value


# Пять мест, три соседних значения Тактики и несовместимый подсумок: восемь случаев.
@pytest.mark.parametrize('place,tactics,compatible,retrieval,discount,cost', [
    ('pockets', 14, True, 1, 0, 7), ('backpack', 14, True, 2, 0, 8),
    ('nested', 14, True, 5, 0, 11), ('belt', 14, True, 0, 0, 6),
    ('belt', 15, True, 0, 1, 5), ('belt', 16, True, 0, 1, 5),
    ('vest', 15, True, 0, 1, 5), ('belt', 15, False, 2, 0, 8),
])
def test_same_magazine_five_stores_tactics_boundary_uses_server_price(client, magazine_case, auth_headers, place, tactics, compatible, retrieval, discount, cost):
    pair = magazine_case
    value = data(pair)
    value['inventory'] = {'pockets': [], 'backpack': []}
    value['equipment'] = {'belt': {'pouches': []}, 'vest': {'pouches': []}}
    value.setdefault('skills', {}).setdefault('other', {})['tactics'] = {'base': tactics, 'bonus': 0}
    magazine = deepcopy(pair['magazine'])
    if place in {'pockets', 'backpack'}:
        value['inventory'][place] = [magazine]
        path = ['inventory', place, 0]
    elif place == 'nested':
        value['inventory']['backpack'] = [{'id': 'i-case', 'category': 'container',
            'attributes': {'access_action_points': 3}, 'contents': [magazine]}]
        path = ['inventory', 'backpack', 0, 'contents', 0]
    else:
        value['equipment'][place]['pouches'] = [{'id': 'i-pouch', 'category': 'pouch',
            'attributes': {'allowed_categories': ['magazine' if compatible else 'med']}, 'contents': [magazine]}]
        path = ['equipment', place, 'pouches', 0, 'contents', 0]
    pair['actor_location'].action_points_current = 5
    save(pair, value)
    result = execute(client, pair, auth_headers, {
        'action_key': 'reload_weapon', 'weapon_index': 0, 'magazine_template_id': pair['template'].id,
        'item_path': path, 'action_points': 0, 'inventory_retrieval_action_points': 0,
        'inventory_use_action_discount': 99,
    }, 'i-magazine')
    details = result['reload_weapon']
    assert details['action_points'] == cost
    assert details['inventory_retrieval_action_points'] == retrieval
    assert details['inventory_use_action_discount'] == discount
    stored = data(pair)
    installed = stored['weapons'][0]['installedMagazine']
    assert installed['id'] == magazine['id']
    assert installed['ammo'] == magazine['ammo']
    assert installed['customNote'] == magazine['customNote']
    assert installed['createdByPlayer'] is True
    assert stored['weapons'][0]['ammo'] == 6
    returned = at_path(stored, path)
    assert returned['id'] == pair['old']['id']
    assert returned['ammo'] == pair['old']['ammo']
    assert returned['customNote'] == pair['old']['customNote']
    assert returned['volume'] == 0.7 and returned['currentAmmo'] == 3
    # Оплата начинается с5, каждый настоящий следующий ход даёт ещё5.
    expected_ap = {5: 0, 6: 4, 7: 3, 8: 2, 11: 4}[cost]
    assert pair['actor_location'].action_points_current == expected_ap
    assert ConsumableUse.query.count() == 0


@pytest.mark.parametrize('name', ['Лента 9x19', 'Спидлоадер 9x19'])
def test_name_only_legacy_loader_cannot_replace_magazine(client, magazine_case, auth_headers, name):
    pair = magazine_case
    value = data(pair)
    chosen = at_path(value, pair['path'])
    chosen['name'] = name
    assert not chosen['attributes'].get('isLoader')
    assert not chosen['attributes'].get('loadingDevice')
    save(pair, value)
    before = snapshot(pair)
    response = action(client, pair, auth_headers, {
        'action_key': 'reload_weapon', 'weapon_index': 0, 'magazine_template_id': pair['template'].id,
        'item_path': pair['path'], 'pending_action_id': 'i-loader',
    })
    assert response.status_code == 400, response.get_json()
    assert snapshot(pair) == before
    assert db.session.get(DeferredCombatAction, 'i-loader') is None


def setup_filters(pair):
    gas_mask, filter_template = filter_templates()
    value = data(pair)
    prepared = filter_data(gas_mask, filter_template, quantity=2)
    value['inventory'] = prepared['inventory']
    value['equipment'] = prepared['equipment']
    value['equipment']['gasMask']['installedModules'][0]['customNote'] = 'Сохранить частичный заряд'
    value['inventory']['backpack'][0]['customNote'] = 'Новая стопка'
    state = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    state.status = 'idle'  # Исходная конфигурация вне боя, до тестируемых действий.
    save(pair, value)
    return gas_mask


def filter_request(pair, gas_mask, operation='install'):
    selection = {'target': {'templateId': gas_mask.id, 'name': gas_mask.name, 'category': 'gas_mask'}}
    if operation == 'install':
        selection['source'] = {'id': 'new-filter'}
    return {'operation': operation, 'slot_type': 'filter', 'target_path': ['equipment', 'gasMask'],
            'item_path': ['inventory', 'backpack', 0], 'module_selection': selection,
            'character_revision': pair['actor_character'].revision}


def filter_post(client, pair, auth_headers, body):
    return client.post(f"/lobbies/characters/{pair['actor_character'].id}/equipment-module",
                        headers=auth_headers(pair['actor_user']), json=body)


def assert_filter_totals(value, installed):
    backpack = value['inventory']['backpack']
    modules = value['equipment']['gasMask']['installedModules']
    all_items = backpack + modules
    assert sum(i.get('quantity', 1) for i in all_items if i.get('id') == 'new-filter') == 2
    old = [i for i in all_items if i.get('id') == 'old-filter']
    assert len(old) == 1
    assert (old[0]['durability'], old[0]['maxDurability']) == (3, 10)
    assert old[0]['customNote'] == 'Сохранить частичный заряд'
    assert len(modules) == (1 if installed else 0)
    for item in all_items:
        if item.get('id') == 'new-filter':
            assert item['durability'] == 20
            assert item['customNote'] == 'Новая стопка'


def test_filter_stack_swap_partial_charge_uninstall_and_stale_replay_are_lossless(client, pair, auth_headers):
    gas_mask = setup_filters(pair)
    request = filter_request(pair, gas_mask)
    response = filter_post(client, pair, auth_headers, request)
    assert response.status_code == 200, response.get_json()
    assert_filter_totals(data(pair), True)
    after_install = snapshot(pair)
    assert filter_post(client, pair, auth_headers, request).status_code == 409
    assert snapshot(pair) == after_install
    request = filter_request(pair, gas_mask, 'uninstall')
    response = filter_post(client, pair, auth_headers, request)
    assert response.status_code == 200, response.get_json()
    assert_filter_totals(data(pair), False)
    after_remove = snapshot(pair)
    assert filter_post(client, pair, auth_headers, request).status_code == 409
    assert snapshot(pair) == after_remove
    assert ConsumableUse.query.count() == 0 and DeferredCombatAction.query.count() == 0


def test_filter_swap_commit_failure_rolls_back_stack_slot_charge_and_revision(client, pair, auth_headers, monkeypatch):
    gas_mask = setup_filters(pair)
    request = filter_request(pair, gas_mask)
    before = snapshot(pair)

    def fail_commit():
        db.session.flush()
        raise ValidationError('Контролируемый отказ сохранения фильтра')

    with monkeypatch.context() as controlled:
        controlled.setattr(db.session, 'commit', fail_commit)
        response = filter_post(client, pair, auth_headers, request)
    assert response.status_code == 400, response.get_json()
    assert snapshot(pair) == before
    assert data(pair)['equipment']['gasMask']['installedModules'][0]['durability'] == 3
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 2


def test_http_heavy_helmet_waits_for_payment_then_removes_without_retrieval(client, pair, auth_headers):
    helmet = add_template('I+ шлем', 'helmet', weight=2.1)
    value = data(pair)
    value['equipment'] = {}
    value['inventory'] = {'pockets': [], 'backpack': [inventory_item(helmet, id='i-helmet', durability=27, customNote='Метка шлема')]}
    pair['actor_location'].action_points_current = 2
    save(pair, value)
    equipped = execute(client, pair, auth_headers, {
        'action_key': 'change_equipment', 'equipment_operation': 'equip', 'equipment_slot': 'helmet',
        'item_path': ['inventory', 'backpack', 0], 'inventory_retrieval_action_points': 0, 'action_points': 0,
    }, 'i-equip')
    assert equipped['equipment_change']['action_points'] == 6
    value = data(pair)
    assert value['inventory']['backpack'] == []
    assert value['equipment']['helmet']['id'] == 'i-helmet'
    assert value['equipment']['helmet']['durability'] == 27
    assert pair['actor_location'].action_points_current == 1
    removed = execute(client, pair, auth_headers, {
        'action_key': 'change_equipment', 'equipment_operation': 'unequip', 'equipment_slot': 'helmet',
        'inventory_retrieval_action_points': 99,
    }, 'i-unequip')
    assert removed['equipment_change']['action_points'] == 3
    value = data(pair)
    assert 'helmet' not in value['equipment']
    assert len(value['inventory']['backpack']) == 1
    returned = value['inventory']['backpack'][0]
    assert (returned['id'], returned['durability'], returned['customNote']) == ('i-helmet', 27, 'Метка шлема')
    assert pair['actor_location'].action_points_current == 3
    assert DeferredCombatAction.query.filter_by(status='completed').count() == 2


def setup_underbarrel(pair):
    value = data(pair)
    grenade = {'id': 'i-grenade', 'name': 'Подствольная граната ВОГ-25', 'category': 'ammo', 'quantity': 2,
               'customNote': 'Сохранить патрон', 'attributes': {'caliber': '40x46', 'batch': 'i-lot'}}
    value['inventory'] = {'pockets': [], 'backpack': [grenade]}
    value['equipment'] = {}
    value['weapons'] = [{'id': 'i-gun', 'name': 'Тестовое оружие', 'installedModules': [
        {'id': 'i-launcher', 'name': 'Подствольник', 'loaded': False,
         'attributes': {'type': 'grenade_launcher', 'caliber': '40x46'}}]}]
    pair['actor_location'].action_points_current = 5
    save(pair, value)
    return deepcopy(grenade)


def underbarrel_body():
    return {'action_key': 'reload_underbarrel', 'weapon_index': 0,
            'item_path': ['inventory', 'backpack', 0], 'inventory_retrieval_action_points': 0,
            'inventory_use_action_discount': 99, 'action_points': 0}


def test_underbarrel_load_pays_server_retrieval_preserves_round_and_never_fires(client, pair, auth_headers):
    grenade = setup_underbarrel(pair)
    result = execute(client, pair, auth_headers, underbarrel_body(), 'i-underbarrel')
    details = result['reload_underbarrel']
    assert details['action_points'] == 7
    assert details['inventory_retrieval_action_points'] == 2
    assert details['inventory_use_action_discount'] == 0
    value = data(pair)
    loaded = value['weapons'][0]['installedModules'][0]
    assert loaded['id'] == 'i-launcher' and loaded['loaded'] is True
    assert loaded['loadedGrenade'] == {**grenade, 'quantity': 1}
    assert value['inventory']['backpack'][0] == {**grenade, 'quantity': 1}
    assert pair['actor_location'].action_points_current == 3
    state = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    assert not state.pending_explosives and not state.area_effects
    assert ConsumableUse.query.count() == 0


@pytest.mark.parametrize('failure', ['underpayment', 'changed-source'])
def test_underbarrel_underpayment_or_changed_paid_source_cannot_load(client, pair, auth_headers, failure):
    setup_underbarrel(pair)
    body = underbarrel_body()
    if failure == 'underpayment':
        before = snapshot(pair)
        response = action(client, pair, auth_headers, body)
        assert response.status_code == 400, response.get_json()
        assert snapshot(pair) == before
        assert DeferredCombatAction.query.count() == 0
    else:
        unchanged = data(pair)
        response = action(client, pair, auth_headers, {**body, 'pending_action_id': 'i-changed'})
        assert response.status_code == 200 and response.get_json()['pending_action'] is True
        pay_to_ready(client, pair, auth_headers, 'i-changed', unchanged)
        value = data(pair)
        value['inventory']['backpack'][0]['attributes']['batch'] = 'Другой патрон'
        save(pair, value)
        before = snapshot(pair)
        response = action(client, pair, auth_headers,
                          {'action_key': 'reload_underbarrel', 'resume_pending_action_id': 'i-changed'})
        assert response.status_code == 400, response.get_json()
        after = snapshot(pair)
        expected = deepcopy(before[0])
        # По согласованному правилу неудавшееся оплаченное действие
        # автоматически исчезает из панели, потраченные ОД не возвращаются.
        expected['health']['combatMeta'].pop('pendingAction', None)
        expected['health']['combatMeta'].pop('completedPendingActionId', None)
        assert after[0] == expected
        assert after[2:5] == before[2:5]
        assert after[6] == before[6]
        assert db.session.get(DeferredCombatAction, 'i-changed').status == 'cancelled'
        assert after[5][0][4] == before[5][0][4]
        response = action(client, pair, auth_headers,
                          {'action_key': 'reload_underbarrel', 'resume_pending_action_id': 'i-changed'})
        assert response.status_code == 409, response.get_json()
        assert snapshot(pair) == after
    assert not data(pair)['weapons'][0]['installedModules'][0].get('loadedGrenade')
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 2
    assert ConsumableUse.query.count() == 0
