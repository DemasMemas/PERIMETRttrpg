"""Адресный остаток инвентарной карточки; подготовка, все случаи NOT_RUN."""
from copy import deepcopy
from helpers.journal_assertions import assert_same_game_state

import pytest

import app.services.combat as combat_module
from app.extensions import db
from app.models import ConsumableUse, DeferredCombatAction, LobbyCharacter, LocationCombatState
from test_consumable_use import pair
from test_magazine_installation import case
from test_ammo_loading import loading_case, request as ammo_request
from test_equipment_actions import add_template, inventory_item
from test_equipment_modules import templates as filter_templates, filter_item
from test_regression_end_combat_operations import (
    canonical_fixture, combat_url, forbid_rng, full_snapshot, reject_unchanged,
)
from test_regression_medical_retry import scripted


def data(pair):
    db.session.expire_all()
    return deepcopy(db.session.get(LobbyCharacter, pair['actor_character'].id).data)


def at(value, path):
    for key in path:
        value = value[key]
    return value


def save(pair, value):
    pair['actor_character'].data = deepcopy(value)
    db.session.commit()


def place(value, item, storage, category):
    value['inventory'] = {'pockets': [], 'backpack': []}
    value['equipment'] = {'belt': {'pouches': []}, 'vest': {'pouches': []}}
    item = deepcopy(item)
    if storage in {'pockets', 'backpack'}:
        value['inventory'][storage] = [item]
        return ['inventory', storage, 0]
    if storage == 'nested':
        value['inventory']['backpack'] = [{
            'id': 'cost-box', 'name': 'Закрытый кейс', 'category': 'container',
            'attributes': {'access_action_points': 3}, 'contents': [item]}]
        return ['inventory', 'backpack', 0, 'contents', 0]
    assert storage in {'compatible', 'incompatible'}
    value['equipment']['belt']['pouches'] = [{
        'id': 'cost-pouch', 'name': 'Подсумок', 'category': 'pouch',
        'attributes': {'allowed_categories': [category if storage == 'compatible' else 'unrelated']},
        'contents': [item]}]
    return ['equipment', 'belt', 'pouches', 0, 'contents', 0]


def prepare(pair, *, ap=1, tactics=15, disabled=False):
    value = deepcopy(pair['actor_character'].data)
    value.setdefault('skills', {}).setdefault('other', {})['tactics'] = {'base': tactics, 'bonus': 0}
    value['health']['current'] = 700
    value['health']['radiation'] = 0
    value['health']['stress'] = 0
    if disabled:
        value['health']['zones']['leftArm']['current'] = 0
    pair['actor_character'].data = value
    canonical_fixture(pair)
    assert pair['actor_location'].action_points_max == 5
    assert pair['actor_location'].free_actions_current == 1
    pair['actor_location'].action_points_current = ap
    pair['actor_location'].facing_x, pair['actor_location'].facing_y = 1, 0
    db.session.commit()


def action(client, pair, auth_headers, body):
    return client.post(combat_url(pair) + '/action', headers=auth_headers(pair['actor_user']),
        json={'location_character_id': pair['actor_location'].id, **body})


def unchanged_items(pair, original):
    current = data(pair)
    for key in ('inventory', 'equipment', 'weapons'):
        assert current.get(key) == original.get(key)


def pay_next(client, pair, auth_headers):
    response = client.post(combat_url(pair) + '/end_turn',
        headers=auth_headers(pair['target_user']),
        json={'location_character_id': pair['target_location'].id})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def pay_ready(client, pair, auth_headers, identity, original):
    for _ in range(8):
        db.session.expire_all()
        row = db.session.get(DeferredCombatAction, identity)
        unchanged_items(pair, original)
        if row.status == 'ready':
            assert row.remaining_action_points == 0
            return
        assert row.status == 'paying' and row.remaining_action_points > 0
        pay_next(client, pair, auth_headers)
    pytest.fail('Долг не оплачен за конечное число предусмотренных ходов')


def pending_start(client, pair, auth_headers, identity, body, remaining):
    response = action(client, pair, auth_headers, {**body, 'pending_action_id': identity})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['pending_action'] is True
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'paying' and row.remaining_action_points == remaining
    return response.get_json()


def resume(client, pair, auth_headers, identity, key, **extra):
    return action(client, pair, auth_headers,
        {'action_key': key, 'resume_pending_action_id': identity, **extra})


def recovered_action(client, pair, auth_headers, identity, key, *, status='ready', remaining=0):
    db.session.expire_all()
    response = client.get(combat_url(pair), headers=auth_headers(pair['actor_user']))
    assert response.status_code == 200, response.get_json()
    actor = next(c for c in response.get_json()['characters']
                 if c['location_character_id'] == pair['actor_location'].id)
    summary = actor['deferred_action']
    assert summary['id'] == identity and summary['action_key'] == key
    assert summary['status'] == status and summary['remaining_action_points'] == remaining
    assert summary['server_executable'] is True


def finished_once(client, pair, auth_headers, identity, key):
    assert db.session.get(DeferredCombatAction, identity).status == 'completed'
    reject_unchanged(pair, lambda: resume(client, pair, auth_headers, identity, key), 409)


def general_body(pair, source, identity):
    return {'operation_id': identity, 'deferred_action_id': identity,
            'source': deepcopy(source), 'application': {'kind': 'self', 'actionPoints': 0},
            'combat_location_id': pair['location'].id,
            'actor_location_character_id': pair['actor_location'].id}


def general(client, pair, auth_headers, body):
    return client.post(f"/lobbies/characters/{pair['actor_character'].id}/general-consumable",
                       headers=auth_headers(pair['actor_user']), json=body)


@pytest.mark.parametrize('storage,tactics,disabled,retrieval,discount,total,final_ap', [
    ('pockets', 15, False, 1, 0, 4, 2),
    ('backpack', 15, False, 2, 0, 5, 1),
    ('compatible', 15, False, 0, 1, 2, 4),
    ('incompatible', 15, False, 2, 0, 5, 1),
    ('nested', 15, False, 5, 0, 8, 3),
    ('pockets', 15, True, 2, 0, 5, 1),
    ('compatible', 14, False, 0, 0, 3, 3),
])
def test_simple_consumable_storage_cost_is_paid_on_server_before_one_use(
        client, pair, auth_headers, monkeypatch, storage, tactics, disabled,
        retrieval, discount, total, final_ap):
    forbid_rng(monkeypatch)
    profile = {'editor_version': 1, 'direct': {'action_points_cost': 3, 'radiation_delta': 1},
               'effects': [], 'modifiers': [], 'addiction': {}}
    template = add_template('Проверочный простой расходник', 'consumable',
                            attributes={'uses': 1, 'consumable': profile})
    item = inventory_item(template, id='cost-simple', quantity=2, customNote='Сохранить')
    value = data(pair)
    path = place(value, item, storage, 'med')
    save(pair, value)
    prepare(pair, tactics=tactics, disabled=disabled)
    original = data(pair)
    source = {'path': path, 'snapshot': deepcopy(at(original, path))}
    selection = {'source': source, 'application': {'kind': 'self', 'actionPoints': 0},
                 'target_character_id': pair['actor_character'].id, 'treatment_request_id': None,
                 'server_authoritative': True, 'server_operation': 'general'}
    identity = 'simple-cost'
    def start(cost):
        return client.post(combat_url(pair) + '/spend', headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id, 'action_points': cost,
            'allow_deferred': True, 'pending_action_id': identity,
            'pending_action_label': 'Простой расходник', 'consumable_selection': selection})
    reject_unchanged(pair, lambda: start(0), 400)
    assert db.session.get(DeferredCombatAction, identity) is None
    started = start(total)
    assert started.status_code == 200, started.get_json()
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'paying' and row.remaining_action_points == total - 1
    payment = row.payload['payment_details']
    assert payment['total_action_points'] == total
    assert payment['retrieval_action_points'] == retrieval
    assert payment['discounted_treatment_action_points'] == 3 - discount
    assert 'medicine_roll' not in row.payload
    assert data(pair)['health']['radiation'] == 0
    assert ConsumableUse.query.count() == 0
    pay_ready(client, pair, auth_headers, identity, original)
    assert pair['actor_location'].action_points_current == final_ap
    recovered = client.get(combat_url(pair) + '/deferred-consumable/' + identity,
                           headers=auth_headers(pair['actor_user']))
    assert recovered.status_code == 200, recovered.get_json()
    assert recovered.get_json()['selection'] == selection
    assert recovered.get_json()['medicine_roll'] is None
    assert data(pair)['health']['radiation'] == 0
    forged = general_body(pair, source, identity)
    forged['source']['snapshot']['id'] = 'another-item'
    reject_unchanged(pair, lambda: general(client, pair, auth_headers, forged), 409)
    response = general(client, pair, auth_headers, general_body(pair, source, identity))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['consumable_result']['payment_details']['total_action_points'] == total
    result = data(pair)
    assert result['health']['radiation'] == 1
    remaining_item = at(result, path)
    assert remaining_item['id'] == 'cost-simple'
    assert remaining_item['quantity'] == 1 and remaining_item['customNote'] == 'Сохранить'
    assert pair['actor_location'].action_points_current == final_ap
    assert ConsumableUse.query.count() == 1
    assert db.session.get(ConsumableUse, identity).actor_character_id == pair['actor_character'].id
    assert db.session.get(DeferredCombatAction, identity).status == 'completed'
    reject_unchanged(pair, lambda: general(client, pair, auth_headers,
                                         general_body(pair, source, identity)), 409)


def test_repeated_ammo_loading_prepares_source_magazine_and_feeder_once(
        client, loading_case, auth_headers, monkeypatch):
    forbid_rng(monkeypatch)
    pair = loading_case
    value = data(pair)
    feeder = {'id': 'repeat-feeder', 'name': 'Подавач', 'category': 'magazine',
              'quantity': 1, 'attributes': {'loadingTool': 'feeder'}}
    value['inventory']['pockets'].append(deepcopy(feeder))
    save(pair, value)
    prepare(pair, ap=2, tactics=10)
    original = data(pair)
    loading = ammo_request(pair, feeder_path=['inventory', 'pockets', 1],
                           action_points=3, free_actions=1)
    identity = 'repeat-ammo-first'
    pending_start(client, pair, auth_headers, identity,
                  {'action_key': 'load_ammunition', 'loading': loading}, 1)
    row = db.session.get(DeferredCombatAction, identity)
    assert row.payload['loading'] == loading
    assert row.payload['_bindings']['loading']['feeder_path'] == feeder
    assert pair['actor_location'].free_actions_current == 0
    unchanged_items(pair, original)
    pay_ready(client, pair, auth_headers, identity, original)
    assert pair['actor_location'].action_points_current == 4
    assert pair['actor_location'].free_actions_current == 1
    recovered = client.get(combat_url(pair), headers=auth_headers(pair['actor_user']))
    assert recovered.status_code == 200, recovered.get_json()
    recovered_action(client, pair, auth_headers, identity, 'load_ammunition')
    response = resume(client, pair, auth_headers, identity, 'load_ammunition',
                      loading={'quantity': 999, 'action_points': 0, 'free_actions': 0})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['reload_weapon'] == {'quantity': 3, 'ammo': 9}
    first = data(pair)
    assert at(first, pair['path'])['currentAmmo'] == 9
    assert first['inventory']['pockets'][0]['quantity'] == 9
    assert first['inventory']['pockets'][1] == feeder
    assert at(first, pair['path'])['ammo'][1]['ammo_variant'] == 'rip'
    assert at(first, pair['path'])['ammo'][-1]['attributes']['damage'] == 50
    assert first['combatMagazineLoading'] == {
        'targetType': 'inventory', 'targetId': 'new-magazine',
        'sourceId': 'ammo', 'feederId': 'repeat-feeder'}
    assert pair['actor_location'].action_points_current == 4
    assert pair['actor_location'].free_actions_current == 1
    finished_once(client, pair, auth_headers, identity, 'load_ammunition')
    assert client.get(combat_url(pair), headers=auth_headers(pair['actor_user'])).status_code == 200
    assert data(pair)['combatMagazineLoading'] == first['combatMagazineLoading']
    repeat = {**loading, 'action_points': 0, 'free_actions': 0}
    reject_unchanged(pair, lambda: action(client, pair, auth_headers,
        {'action_key': 'load_ammunition', 'loading': repeat}), 400)
    repeat['action_points'] = 1
    response = action(client, pair, auth_headers,
        {'action_key': 'load_ammunition', 'loading': repeat, 'pending_action_id': 'repeat-ammo-second'})
    assert response.status_code == 200, response.get_json()
    assert not response.get_json().get('pending_action')
    assert response.get_json()['reload_weapon'] == {'quantity': 3, 'ammo': 12}
    second = data(pair)
    assert at(second, pair['path'])['currentAmmo'] == 12
    assert second['inventory']['pockets'][0]['quantity'] == 6
    assert second['inventory']['pockets'][1] == feeder
    assert at(second, pair['path'])['ammo'][-1]['quantity'] == 6
    assert at(second, pair['path'])['ammo'][-1]['attributes']['damage'] == 50
    assert second['combatMagazineLoading'] == first['combatMagazineLoading']
    assert pair['actor_location'].action_points_current == 3
    assert pair['actor_location'].free_actions_current == 1
    assert db.session.get(DeferredCombatAction, 'repeat-ammo-second').status == 'completed'
    assert ConsumableUse.query.count() == 0
    finished_once(client, pair, auth_headers, identity, 'load_ammunition')


def test_reload_exact_eight_pays_two_then_five_then_one_without_early_swap(
        client, case, auth_headers, monkeypatch):
    forbid_rng(monkeypatch)
    pair = case
    value = data(pair)
    magazine = value['equipment']['belt']['pouches'][0]['contents'].pop()
    value['inventory']['backpack'] = [magazine]
    save(pair, value)
    prepare(pair, ap=2, tactics=10)
    original = data(pair)
    path = ['inventory', 'backpack', 0]
    identity = 'reload-exact-eight'
    body = {'action_key': 'reload_weapon', 'weapon_index': 0,
            'magazine_template_id': pair['template'].id, 'item_path': path,
            'inventory_retrieval_action_points': 0, 'inventory_use_action_discount': 99}
    pending_start(client, pair, auth_headers, identity, body, 6)
    assert pair['actor_location'].action_points_current == 0
    row = db.session.get(DeferredCombatAction, identity)
    assert row.payload['item_path'] == path and row.payload['weapon_index'] == 0
    assert row.payload['_bindings']['item'] == magazine
    unchanged_items(pair, original)
    reject_unchanged(pair, lambda: resume(client, pair, auth_headers, identity, 'reload_weapon'), 409)
    recovered_action(client, pair, auth_headers, identity, 'reload_weapon', status='paying', remaining=6)
    db.session.expire_all()
    assert db.session.get(DeferredCombatAction, identity).remaining_action_points == 6
    pay_next(client, pair, auth_headers)
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'paying' and row.remaining_action_points == 1
    assert pair['actor_location'].action_points_current == 0
    unchanged_items(pair, original)
    reject_unchanged(pair, lambda: resume(client, pair, auth_headers, identity, 'reload_weapon'), 409)
    pay_next(client, pair, auth_headers)
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'ready' and row.remaining_action_points == 0
    assert pair['actor_location'].action_points_current == 4
    unchanged_items(pair, original)
    recovered_action(client, pair, auth_headers, identity, 'reload_weapon')
    response = resume(client, pair, auth_headers, identity, 'reload_weapon',
                      weapon_index=999, item_path=['inventory', 'pockets', 999])
    assert response.status_code == 200, response.get_json()
    details = response.get_json()['reload_weapon']
    assert details['action_points'] == 8
    assert details['inventory_retrieval_action_points'] == 2
    assert details['inventory_use_action_discount'] == 0
    result = data(pair)
    installed = result['weapons'][0]['installedMagazine']
    assert installed['id'] == 'new-magazine' and installed['ammo'] == magazine['ammo']
    assert installed['customNote'] == 'Keep this' and installed['createdByPlayer'] is True
    assert result['weapons'][0]['ammo'] == 6
    returned = result['inventory']['backpack']
    assert len(returned) == 1 and returned[0]['id'] == 'old-magazine'
    assert returned[0]['ammo'] == pair['old']['ammo']
    assert returned[0]['customNote'] == 'Old note' and returned[0]['currentAmmo'] == 3
    assert pair['actor_location'].action_points_current == 4
    assert ConsumableUse.query.count() == 0
    finished_once(client, pair, auth_headers, identity, 'reload_weapon')


def damage_state(pair):
    health = pair['target_character'].data['health']
    return {key: deepcopy(health.get(key)) for key in ('current', 'zones', 'effects')}


def explosive_state(pair):
    return LocationCombatState.query.filter_by(location_id=pair['location'].id).one()


@pytest.mark.parametrize('storage,retrieval,total,final_ap', [
    ('pockets', 1, 6, 1), ('nested', 5, 10, 2),
])
def test_underbarrel_new_retrieval_paths_wait_for_payment_without_firing(
        client, pair, auth_headers, monkeypatch, storage, retrieval, total, final_ap):
    forbid_rng(monkeypatch)
    grenade = {'id': 'cost-vog', 'name': 'Подствольная граната ВОГ-25', 'category': 'ammo',
               'quantity': 2, 'customNote': 'Сохранить патрон',
               'attributes': {'caliber': '40x46', 'batch': 'cost-lot'}}
    value = data(pair)
    path = place(value, grenade, storage, 'ammo')
    value['weapons'] = [{'id': 'cost-gun', 'name': 'Тестовое оружие', 'installedModules': [
        {'id': 'cost-launcher', 'name': 'Подствольник', 'loaded': False,
         'attributes': {'type': 'grenade_launcher', 'caliber': '40x46'}}]}]
    save(pair, value)
    prepare(pair, ap=2)
    original = data(pair)
    identity = 'underbarrel-new-path'
    body = {'action_key': 'reload_underbarrel', 'weapon_index': 0, 'item_path': path,
            'inventory_retrieval_action_points': 0, 'inventory_use_action_discount': 99,
            'action_points': 0}
    pending_start(client, pair, auth_headers, identity, body, total - 2)
    row = db.session.get(DeferredCombatAction, identity)
    assert row.payload['item_path'] == path and row.payload['weapon_index'] == 0
    assert row.payload['_bindings']['item'] == at(original, path)
    unchanged_items(pair, original)
    assert not explosive_state(pair).pending_explosives
    assert not data(pair)['weapons'][0]['installedModules'][0].get('loadedGrenade')
    pay_ready(client, pair, auth_headers, identity, original)
    assert not explosive_state(pair).pending_explosives
    assert pair['actor_location'].action_points_current == final_ap
    recovered = client.get(combat_url(pair), headers=auth_headers(pair['actor_user']))
    assert recovered.status_code == 200, recovered.get_json()
    recovered_action(client, pair, auth_headers, identity, 'reload_underbarrel')
    response = resume(client, pair, auth_headers, identity, 'reload_underbarrel',
                      weapon_index=999, item_path=['inventory', 'backpack', 999])
    assert response.status_code == 200, response.get_json()
    details = response.get_json()['reload_underbarrel']
    assert details['action_points'] == total
    assert details['inventory_retrieval_action_points'] == retrieval
    assert details['inventory_use_action_discount'] == 0
    assert details['item_path'] == path and details['weapon_index'] == 0
    result = data(pair)
    loaded = result['weapons'][0]['installedModules'][0]
    assert loaded['id'] == 'cost-launcher' and loaded['loaded'] is True
    assert loaded['loadedGrenade'] == {**grenade, 'quantity': 1}
    assert at(result, path) == {**grenade, 'quantity': 1}
    assert not explosive_state(pair).pending_explosives and not explosive_state(pair).area_effects
    assert pair['actor_location'].action_points_current == final_ap
    assert ConsumableUse.query.count() == 0
    finished_once(client, pair, auth_headers, identity, 'reload_underbarrel')


@pytest.mark.parametrize('storage,total,final_ap', [
    ('pockets', 5, 2), ('backpack', 6, 1), ('compatible', 3, 4),
    ('incompatible', 6, 1), ('nested', 9, 3),
])
def test_delayed_hand_grenade_pays_retrieval_and_delay_before_one_pending_event(
        client, pair, auth_headers, monkeypatch, storage, total, final_ap):
    forbid_rng(monkeypatch)
    grenade = {'id': 'cost-rgd', 'name': 'Граната РГД-5', 'category': 'grenade',
               'quantity': 2, 'attributes': {}, 'customNote': 'Ручная граната'}
    value = data(pair)
    path = place(value, grenade, storage, 'grenade')
    save(pair, value)
    prepare(pair, ap=2)
    original = data(pair)
    target_health = damage_state(pair)
    identity = 'delayed-hand-grenade'
    body = {'action_key': 'explosive_attack', 'explosive_source': 'hand',
            'explosive_fuse_mode': 'delay', 'item_path': path, 'target_x': 8, 'target_y': 1,
            'inventory_retrieval_action_points': 0, 'inventory_use_action_discount': 99,
            'action_points': 0}
    # При неполной оплате бросков нет; проверки задержки и броска — при исполнении.
    pending_start(client, pair, auth_headers, identity, body, total - 2)
    row = db.session.get(DeferredCombatAction, identity)
    assert row.payload['explosive_fuse_mode'] == 'delay'
    assert row.payload['item_path'] == path and row.payload['target_x'] == 8
    assert row.payload['_bindings']['item'] == at(original, path)
    unchanged_items(pair, original)
    assert not explosive_state(pair).pending_explosives and not explosive_state(pair).area_effects
    pay_ready(client, pair, auth_headers, identity, original)
    assert not explosive_state(pair).pending_explosives and not explosive_state(pair).area_effects
    assert pair['actor_location'].action_points_current == final_ap
    assert damage_state(pair) == target_health
    recovered = client.get(combat_url(pair), headers=auth_headers(pair['actor_user']))
    assert recovered.status_code == 200, recovered.get_json()
    recovered_action(client, pair, auth_headers, identity, 'explosive_attack')
    throw_draws=[]
    def throw_roll(low,high):
        throw_draws.append((low,high));return 20
    with scripted(monkeypatch, combat_module, [('_narrative_skill_check', 1, 20, 20)]), monkeypatch.context() as throwing:
        throwing.setattr('random.randint',throw_roll)
        response = resume(client, pair, auth_headers, identity, 'explosive_attack',
                          target_x=0, target_y=0, explosive_fuse_mode='normal',
                          item_path=['inventory', 'backpack', 999])
    assert throw_draws==[(1,20)]
    assert response.status_code == 200, response.get_json()
    details = response.get_json()['explosive']
    assert details['action_points'] == total
    assert details['target'] == {'x': 8, 'y': 1}
    assert details['impact'] == {'x': 8, 'y': 1}
    assert details['source'] == 'hand' and details['fuse_mode'] == 'delay'
    assert details['detonated'] is False and details['pending']['trigger'] == 'short_fuse'
    assert details['rolls'] == [20] and details['success'] is True
    events = explosive_state(pair).pending_explosives
    assert len(events) == 1
    assert events[0]['id'] == details['pending']['id']
    assert (events[0]['x'], events[0]['y'], events[0]['actor_id']) == (8, 1, pair['actor_location'].id)
    assert events[0]['trigger'] == 'short_fuse'
    assert at(data(pair), path) == {**grenade, 'quantity': 1}
    assert damage_state(pair) == target_health
    assert not explosive_state(pair).area_effects
    assert pair['actor_location'].action_points_current == final_ap
    assert ConsumableUse.query.count() == 0
    finished_once(client, pair, auth_headers, identity, 'explosive_attack')


@pytest.mark.parametrize('slot,equip_cost,remove_cost,equip_ap,remove_ap', [
    ('armor', 22, 10, 4, 4), ('gasMask', 4, 1, 2, 1),
])
def test_http_armor_and_gas_mask_equip_and_remove_pay_real_costs(
        client, pair, auth_headers, monkeypatch, slot, equip_cost, remove_cost, equip_ap, remove_ap):
    forbid_rng(monkeypatch)
    if slot == 'armor':
        template = add_template('Проверочная средняя броня', 'armor', subcategory='Средняя', weight=8,
            attributes={'max_durability': 40, 'movement_penalty': 3,
                        'protection': {'physical': 0.4}, 'protection_zones': ['chest', 'abdomen']})
    else:
        template = add_template('Проверочный противогаз', 'gas_mask', weight=1)
    item = inventory_item(template, id='cost-equipment', durability=27, customNote='Метка экипировки')
    value = data(pair)
    path = place(value, item, 'backpack', template.category)
    save(pair, value)
    prepare(pair)
    original = data(pair)
    equip_id = 'cost-equip'
    body = {'action_key': 'change_equipment', 'equipment_operation': 'equip',
            'equipment_slot': slot, 'item_path': path,
            'inventory_retrieval_action_points': 0, 'action_points': 0}
    pending_start(client, pair, auth_headers, equip_id, body, equip_cost - 1)
    row = db.session.get(DeferredCombatAction, equip_id)
    assert row.payload['equipment_slot'] == slot and row.payload['item_path'] == path
    assert row.payload['_bindings']['item'] == at(original, path)
    pay_ready(client, pair, auth_headers, equip_id, original)
    assert pair['actor_location'].action_points_current == equip_ap
    assert client.get(combat_url(pair), headers=auth_headers(pair['actor_user'])).status_code == 200
    recovered_action(client, pair, auth_headers, equip_id, 'change_equipment')
    response = resume(client, pair, auth_headers, equip_id, 'change_equipment',
                      equipment_slot='helmet', equipment_operation='unequip',
                      item_path=['inventory', 'pockets', 999])
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['equipment_change']['action_points'] == equip_cost
    assert response.get_json()['equipment_change']['slot'] == slot
    equipped = data(pair)
    assert equipped['inventory']['backpack'] == []
    assert equipped['equipment'][slot]['id'] == 'cost-equipment'
    assert equipped['equipment'][slot]['durability'] == 27
    assert equipped['equipment'][slot]['customNote'] == 'Метка экипировки'
    assert pair['actor_location'].action_points_current == equip_ap
    finished_once(client, pair, auth_headers, equip_id, 'change_equipment')
    remove_id = 'cost-remove'
    remove = {'action_key': 'change_equipment', 'equipment_operation': 'unequip',
              'equipment_slot': slot, 'inventory_retrieval_action_points': 99,
              'action_points': 0}
    if slot == 'armor':
        pending_start(client, pair, auth_headers, remove_id, remove, remove_cost - equip_ap)
        assert db.session.get(DeferredCombatAction, remove_id).payload['equipment_slot'] == slot
        pay_ready(client, pair, auth_headers, remove_id, equipped)
        recovered_action(client, pair, auth_headers, remove_id, 'change_equipment')
        response = resume(client, pair, auth_headers, remove_id, 'change_equipment',
                          equipment_slot='gasMask', equipment_operation='equip')
    else:
        response = action(client, pair, auth_headers, {**remove, 'pending_action_id': remove_id})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['equipment_change']['action_points'] == remove_cost
    assert response.get_json()['equipment_change']['slot'] == slot
    result = data(pair)
    assert slot not in result['equipment']
    assert len(result['inventory']['backpack']) == 1
    returned = result['inventory']['backpack'][0]
    assert (returned['id'], returned['durability'], returned['customNote']) == (
        'cost-equipment', 27, 'Метка экипировки')
    assert pair['actor_location'].action_points_current == remove_ap
    assert ConsumableUse.query.count() == 0
    if slot == 'armor':
        finished_once(client, pair, auth_headers, remove_id, 'change_equipment')
    else:
        assert db.session.get(DeferredCombatAction, remove_id).status == 'completed'
        reject_unchanged(pair, lambda: action(client, pair, auth_headers, remove), 400)


def setup_filter(pair, slot, *, empty=False):
    mask_template, filter_template = filter_templates()
    if slot == 'helmet':
        target_template = add_template('Противогазо-шлем', 'helmet', weight=1,
            attributes={'slots': [{'type': 'filter', 'maxItems': 1}], 'requires_filter': True})
    else:
        target_template = mask_template
    value = data(pair)
    target = inventory_item(target_template, id='cost-filter-target', installedModules=[])
    item = filter_item(filter_template, charges=20)
    item['customNote'] = 'Метка фильтра'
    if empty:
        item['durability'] = 0
        item['attributes']['durability'] = 0
    value['equipment'] = {slot: target}
    value['inventory'] = {'pockets': [], 'backpack': [item]}
    save(pair, value)
    prepare(pair)
    return {'action_key': 'change_equipment_module', 'module_operation': 'install',
            'module_slot_type': 'filter', 'module_target_path': ['equipment', slot],
            'item_path': ['inventory', 'backpack', 0],
            'module_selection': {'target': {'id': 'cost-filter-target'}, 'source': {'id': 'new-filter'}},
            'inventory_retrieval_action_points': 0, 'action_points': 0}


def test_http_helmet_filter_install_waits_for_retrieval_payment_then_applies_once(
        client, pair, auth_headers, monkeypatch):
    forbid_rng(monkeypatch)
    body = setup_filter(pair, 'helmet')
    original = data(pair)
    identity = 'helmet-filter-paid'
    pending_start(client, pair, auth_headers, identity, body, 1)
    row = db.session.get(DeferredCombatAction, identity)
    assert row.payload['module_target_path'] == ['equipment', 'helmet']
    assert row.payload['module_selection'] == body['module_selection']
    assert row.payload['_bindings']['item'] == original['inventory']['backpack'][0]
    pay_ready(client, pair, auth_headers, identity, original)
    assert pair['actor_location'].action_points_current == 4
    assert client.get(combat_url(pair), headers=auth_headers(pair['actor_user'])).status_code == 200
    recovered_action(client, pair, auth_headers, identity, 'change_equipment_module')
    response = resume(client, pair, auth_headers, identity, 'change_equipment_module',
                      module_target_path=['equipment', 'gasMask'], module_operation='uninstall',
                      item_path=['inventory', 'pockets', 999])
    assert response.status_code == 200, response.get_json()
    details = response.get_json()['module_change']
    assert details['action_points'] == 2 and details['target_path'] == ['equipment', 'helmet']
    assert details['operation'] == 'install'
    result = data(pair)
    assert result['inventory'] == {'pockets': [], 'backpack': []}
    modules = result['equipment']['helmet']['installedModules']
    assert len(modules) == 1
    assert modules[0]['id'] == 'new-filter' and modules[0]['quantity'] == 1
    assert modules[0]['slotType'] == 'filter'
    assert (modules[0]['durability'], modules[0]['maxDurability']) == (20, 20)
    assert modules[0]['customNote'] == 'Метка фильтра'
    assert pair['actor_location'].action_points_current == 4
    assert ConsumableUse.query.count() == 0
    finished_once(client, pair, auth_headers, identity, 'change_equipment_module')


@pytest.mark.parametrize('slot', ['gasMask', 'helmet'])
def test_http_explicitly_empty_filter_rejected_without_payment_or_mutation(
        client, pair, auth_headers, monkeypatch, slot):
    forbid_rng(monkeypatch)
    body = setup_filter(pair, slot, empty=True)
    source = data(pair)['inventory']['backpack'][0]
    assert source['durability'] == 0 and source['maxDurability'] == 20
    assert source['attributes']['filter_charges'] == 20
    before = full_snapshot(pair)
    response = action(client, pair, auth_headers, {**body, 'pending_action_id': 'empty-filter'})
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['error']['code'] == 400
    assert 'непустой' in response.get_json()['error']['message']
    assert_same_game_state(full_snapshot(pair), before)
    assert db.session.get(DeferredCombatAction, 'empty-filter') is None
    assert ConsumableUse.query.count() == 0
    assert pair['actor_location'].action_points_current == 1
