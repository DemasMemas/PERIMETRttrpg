"""S+: серверные границы расходников и редактора. Подготовлено, NOT_RUN."""
from copy import deepcopy
from helpers.journal_assertions import assert_same_game_state
from types import SimpleNamespace

import pytest

import app.services.medical_procedure as medical_module
import app.services.deferred_consumable as deferred_module
import app.services.general_consumable as general_module
import app.services.addictions as addictions_module
from app.extensions import db
from app.models import ConsumableUse, DeferredCombatAction, LobbyCharacter, LocationCombatState
from app.models.lobby_templates import LobbyItemTemplate
from app.services.effects import advance_timed_effects
from test_consumable_use import pair
from test_general_consumable import install_consumable
from test_medical_procedure import install_item, payload as medical_payload, apply as apply_medical


@pytest.fixture(autouse=True)
def service_rng(monkeypatch):
    def forbidden(*args):
        raise AssertionError('Неожиданный медицинский d20')
    monkeypatch.setattr(medical_module, 'random', SimpleNamespace(randint=forbidden))
    monkeypatch.setattr(deferred_module, 'random', SimpleNamespace(randint=forbidden))
    # Контролируется конкретный сервис зависимости, а не общий модуль random.
    monkeypatch.setattr(addictions_module, 'random', SimpleNamespace(random=lambda: 0.999999, randint=forbidden))
    monkeypatch.setattr(general_module, 'random', SimpleNamespace(random=lambda: 0.999999))


def medical_rolls(monkeypatch, count, value=4):
    draws = []
    def draw(low, high):
        assert (low, high) == (1, 20)
        assert len(draws) < count
        draws.append(value)
        return value
    monkeypatch.setattr(medical_module, 'random', SimpleNamespace(randint=draw))
    return draws


def data(pair):
    db.session.expire_all()
    return deepcopy(db.session.get(LobbyCharacter, pair['actor_character'].id).data)


def save(pair, value):
    pair['actor_character'].data = deepcopy(value)
    db.session.commit()


def idle(pair):
    LocationCombatState.query.filter_by(location_id=pair['location'].id).one().status = 'idle'
    db.session.commit()


def receipt(pair, operation):
    db.session.expire_all()
    row = db.session.get(ConsumableUse, operation)
    assert row is not None
    assert row.actor_character_id == row.target_character_id == pair['actor_character'].id


def general(client, pair, auth_headers, item, operation, *, index=0, deferred=None, combat=False):
    return client.post(f"/lobbies/characters/{pair['actor_character'].id}/general-consumable",
        headers=auth_headers(pair['actor_user']), json={
            'operation_id': operation, 'deferred_action_id': deferred,
            'source': {'path': ['inventory', 'backpack', index], 'snapshot': deepcopy(item)},
            'application': {'kind': 'self', 'actionPoints': 0},
            'combat_location_id': pair['location'].id if combat else None,
            'actor_location_character_id': pair['actor_location'].id if combat else None,
        })


def elapse(pair, seconds):
    value = data(pair)
    health = value['health']
    health['effects'] = advance_timed_effects(health, health['effects'], seconds)
    save(pair, value)


@pytest.mark.parametrize('fire', ['lighter', 'matches', 'missing'])
def test_smoking_reusable_lighter_match_priority_and_missing_fire_are_server_applied(client, pair, auth_headers, fire):
    idle(pair)
    cigarette, _ = install_consumable(pair, 'Сигареты дешёвые',
                                     'Снижение стресса через 5 минут. 20 использований.', quantity=1)
    lighter = {'id': 's-lighter', 'name': 'Зажигалка', 'category': 'misc', 'quantity': 1,
               'durability': 9, 'customNote': 'Многоразовая'}
    matches = {'id': 's-match', 'name': 'Спички', 'category': 'misc', 'quantity': 1}
    value = data(pair)
    sources = [lighter, matches] if fire == 'lighter' else ([matches] if fire == 'matches' else [])
    value['inventory']['backpack'] = deepcopy(sources) + [cigarette]
    save(pair, value)
    before = data(pair)
    index = len(sources)
    response = general(client, pair, auth_headers, cigarette, 's-smoke-1', index=index)
    if fire == 'missing':
        assert response.status_code == 400, response.get_json()
        assert_same_game_state(data(pair), before)
        assert ConsumableUse.query.count() == 0
        return
    assert response.status_code == 200, response.get_json()
    expected_source = 'Зажигалка' if fire == 'lighter' else 'Спички'
    assert response.get_json()['consumable_result']['requirements']['fire_source'] == expected_source
    stored = data(pair)
    tobacco = next(item for item in stored['inventory']['backpack'] if item['id'] == cigarette['id'])
    assert tobacco['quantity'] == 1 and tobacco['uses'] == 19
    assert any(e['type'] == 'delayed_adjustment' and e.get('remaining_seconds') == 300
               for e in stored['health']['effects'])
    receipt(pair, 's-smoke-1')
    if fire == 'lighter':
        assert stored['inventory']['backpack'][:2] == [lighter, matches]
        second = general(client, pair, auth_headers, tobacco, 's-smoke-2', index=2)
        assert second.status_code == 200, second.get_json()
        stored = data(pair)
        assert stored['inventory']['backpack'][:2] == [lighter, matches]
        assert stored['inventory']['backpack'][2]['uses'] == 18
        receipt(pair, 's-smoke-2')
        assert ConsumableUse.query.count() == 2
    else:
        assert [item['id'] for item in stored['inventory']['backpack']] == [cigarette['id']]
        assert ConsumableUse.query.count() == 1
    assert pair['actor_location'].action_points_current == 5


def test_renamed_injectable_keeps_medical_route_and_one_real_d20(client, pair, auth_headers, monkeypatch):
    template, profile, item = install_item(pair, 'Тестовая ампула', 'Ампула. +5 ХП.', medicine=10)
    template.name = 'Раствор №7'
    template.description = 'Название и описание больше не подсказывают способ применения'
    db.session.commit()
    item = {**item, 'name': 'Бесцветная жидкость'}
    value = data(pair)
    value['inventory']['backpack'] = [item]
    value['health']['current'] = 100
    value['health']['max'] = 700
    save(pair, value)
    db.session.refresh(template)
    assert template.attributes['consumable'] == profile
    assert template.name == 'Раствор №7'
    assert 'Ампула' not in template.description
    before = data(pair)
    refused = general(client, pair, auth_headers, item, 's-injectable-bypass')
    assert refused.status_code == 400, refused.get_json()
    assert_same_game_state(data(pair), before)
    assert ConsumableUse.query.count() == 0
    draws = medical_rolls(monkeypatch, 1)
    body = medical_payload(pair, item, {'kind': 'self', 'actionPoints': 0}, operation='s-injectable')
    response = apply_medical(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['roll'] == 4 and result['difficulty'] == 4 and result['success'] is True
    assert draws == [4]
    stored = data(pair)
    assert stored['health']['current'] == 105
    assert stored['inventory']['backpack'][0]['quantity'] == 1
    receipt(pair, 's-injectable')
    assert ConsumableUse.query.count() == 1


@pytest.mark.parametrize('name,pain', [('Стимулятор Варвар', 1), ('Ампула Миколия', 5), ('Препарат 02', 1)])
def test_other_restorative_ampoules_refresh_once_and_expire_to_original_health(client, pair, auth_headers, monkeypatch, name, pain):
    fracture = {'id': 's-fracture', 'type': 'fracture', 'area': 'leftArm', 'source': 'injury'}
    zones = {'leftArm': {'current': 0, 'max': 50}, 'rightArm': {'current': 20, 'max': 50},
             'leftLeg': {'current': 50, 'max': 50}, 'rightLeg': {'current': 50, 'max': 50}}
    _, profile, item = install_item(pair, name, 'Ампула. Действует 10 минут.', quantity=2,
                                     medicine=10, effects=[fracture], zones=zones, pain=7)
    assert profile['direct']['temporary_limb_health_minutes'] == 10
    assert not profile['direct'].get('minimum_limb_health')
    pool = data(pair)['health']['current']
    draws = medical_rolls(monkeypatch, 2)
    for dose in range(2):
        item = deepcopy(data(pair)['inventory']['backpack'][0])
        response = apply_medical(client, pair, auth_headers,
            medical_payload(pair, item, {'kind': 'self', 'actionPoints': 1}, operation=f's-dose-{dose}'))
        assert response.status_code == 200, response.get_json()
        assert response.get_json()['medical_result']['success'] is True
        assert response.get_json()['medical_result']['roll'] == 4
        health = data(pair)['health']
        temporary = [e for e in health['effects'] if e['type'] == 'temporary_limb_restoration']
        assert len(temporary) == 4
        assert {e['area'] for e in temporary} == set(zones)
        assert all(e['remaining_seconds'] == 600 and e['suppress_fracture'] is True for e in temporary)
        arm = next(e for e in temporary if e['area'] == 'leftArm')
        assert arm['previous_health'] == 0 and arm['restore_on_expire'] is True
        assert arm['minimum_limb_health'] == 0
        assert health['zones']['leftArm']['current'] == 1
        assert health['zones']['rightArm']['current'] == 20
        assert health['current'] == pool
        receipt(pair, f's-dose-{dose}')
        if dose == 0:
            assert data(pair)['inventory']['backpack'][0]['quantity'] == 1
            elapse(pair, 300)
            assert next(e for e in data(pair)['health']['effects'] if e['type'] == 'temporary_limb_restoration')['remaining_seconds'] == 300
    assert draws == [4, 4]
    assert data(pair)['inventory']['backpack'] == []
    assert data(pair)['health']['painLevel'] == pain
    elapse(pair, 599)
    health = data(pair)['health']
    assert health['zones']['leftArm']['current'] == 1
    assert all(e['remaining_seconds'] == 1 for e in health['effects'] if e['type'] == 'temporary_limb_restoration')
    elapse(pair, 1)
    health = data(pair)['health']
    assert not any(e['type'] == 'temporary_limb_restoration' for e in health['effects'])
    assert health['zones']['leftArm']['current'] == 0
    assert health['zones']['rightArm']['current'] == 20
    assert health['current'] == pool
    assert any(e.get('id') == 's-fracture' for e in health['effects'])
    assert ConsumableUse.query.count() == 2


def test_ammonia_without_shock_has_no_effect_consumption_or_roll(client, pair, auth_headers):
    _, _, item = install_item(pair, 'Бутылек нашатыря',
        'Даёт +2 к проверке выхода из болевого шока и преимущество. Не тратится.', quantity=1, effects=[])
    before = data(pair)
    response = apply_medical(client, pair, auth_headers,
        medical_payload(pair, item, {'kind': 'self', 'actionPoints': 0}, operation='s-ammonia'))
    assert response.status_code == 409, response.get_json()
    assert_same_game_state(data(pair), before)
    assert ConsumableUse.query.count() == 0
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 1
    assert 'willShockBonus' not in data(pair)['health'].get('combatMeta', {})
    assert pair['actor_location'].action_points_current == 5


def base_profile():
    return {'editor_version': 1, 'direct': {'action_points_cost': 0, 'stress_delta': -1},
            'modifiers': [{'stat': 'strength', 'value': 3, 'remaining': 2, 'tick': 'turn_end'}],
            'effects': [{'type': 'delayed_adjustment', 'name': 'Отложенная радиация', 'area': 'editor-1',
                         'remaining': 2, 'tick': 'time_elapsed', 'time_unit': 'minute',
                         'adjustments': [{'field': 'radiation', 'delta': 4, 'min': 0}]}]}


def create_profile(client, pair, auth_headers, profile, name, uses=2):
    return client.post(f"/lobbies/{pair['lobby'].id}/templates", headers=auth_headers(pair['actor_user']),
        json={'name': name, 'category': 'consumable', 'attributes': {'uses': uses, 'consumable': deepcopy(profile)}})


def local_item(template_id, uses=2):
    return {'id': 's-custom', 'templateId': template_id + 1_000_000, 'name': 'Без связи с профилем',
            'category': 'consumable', 'quantity': 1, 'uses': uses, 'maxUses': uses}


def test_long_ordinary_consumable_recovers_selection_and_completes_once_after_real_payment(client, pair, auth_headers):
    profile = {'editor_version': 1, 'direct': {'action_points_cost': 8, 'radiation_delta': 3},
               'modifiers': [], 'effects': []}
    created = create_profile(client, pair, auth_headers, profile, 'Длительный состав', uses=1)
    assert created.status_code == 201, created.get_json()
    item = local_item(created.get_json()['id'], uses=1)
    value = data(pair)
    value['inventory']['backpack'] = [item]
    value['health']['radiation'] = 0
    pair['actor_location'].action_points_current = 2
    save(pair, value)
    url = f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat"
    selection = {'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
                 'application': {'kind': 'self', 'actionPoints': 0},
                 'target_character_id': pair['actor_character'].id, 'treatment_request_id': None,
                 'server_authoritative': True, 'server_operation': 'general'}
    started = client.post(url + '/spend', headers=auth_headers(pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_points': 10, 'allow_deferred': True,
        'pending_action_id': 's-long', 'pending_action_label': 'Состав', 'consumable_selection': selection})
    assert started.status_code == 200, started.get_json()
    preparation_url = url + '/deferred-consumable/s-long'
    assert client.get(preparation_url, headers=auth_headers(pair['actor_user'])).status_code == 409
    for remaining in (8, 3):
        db.session.expire_all()
        row = db.session.get(DeferredCombatAction, 's-long')
        assert row.status == 'paying' and row.remaining_action_points == remaining
        assert 'medicine_roll' not in row.payload
        assert data(pair)['inventory']['backpack'] == [item]
        assert data(pair)['health']['radiation'] == 0
        assert ConsumableUse.query.count() == 0
        recovered = client.get(url, headers=auth_headers(pair['actor_user']))
        assert recovered.status_code == 200
        actor = next(c for c in recovered.get_json()['characters']
                     if c['location_character_id'] == pair['actor_location'].id)
        assert actor['deferred_action']['id'] == 's-long'
        assert actor['deferred_action']['status'] == 'paying'
        assert 'selection' not in actor['deferred_action']
        ended = client.post(url + '/end_turn', headers=auth_headers(pair['target_user']),
                            json={'location_character_id': pair['target_location'].id})
        assert ended.status_code == 200, ended.get_json()
    ready = client.get(preparation_url, headers=auth_headers(pair['actor_user']))
    assert ready.status_code == 200, ready.get_json()
    assert ready.get_json()['selection'] == selection
    assert ready.get_json()['medicine_roll'] is None
    assert db.session.get(DeferredCombatAction, 's-long').remaining_action_points == 0
    assert pair['actor_location'].action_points_current == 2
    response = general(client, pair, auth_headers, ready.get_json()['selection']['source']['snapshot'],
                       's-long', deferred='s-long', combat=True)
    assert response.status_code == 200, response.get_json()
    assert data(pair)['health']['radiation'] == 3
    assert data(pair)['inventory']['backpack'] == []
    assert pair['actor_location'].action_points_current == 2
    assert db.session.get(DeferredCombatAction, 's-long').status == 'completed'
    receipt(pair, 's-long')
    before = data(pair)
    revision = pair['actor_character'].revision
    assert general(client, pair, auth_headers, item, 's-long', deferred='s-long', combat=True).status_code == 409
    assert_same_game_state(data(pair), before)
    assert pair['actor_character'].revision == revision
    assert pair['actor_location'].action_points_current == 2
    assert ConsumableUse.query.count() == 1


def test_editor_clone_composite_profile_is_independent_persisted_and_applied(client, pair, auth_headers):
    idle(pair)
    original = base_profile()
    created = create_profile(client, pair, auth_headers, original, 'Оригинал')
    assert created.status_code == 201, created.get_json()
    original_id = created.get_json()['id']
    combined = deepcopy(original)
    combined['direct']['pain_delta'] = -1
    combined['effects'].append({'type': 'delayed_adjustment', 'name': 'Отложенное истощение',
        'area': 'editor-2', 'remaining': 2, 'tick': 'time_elapsed', 'time_unit': 'minute',
        'adjustments': [{'field': 'exhaustion', 'delta': 1, 'min': 0, 'max': 10}]})
    cloned = create_profile(client, pair, auth_headers, combined, 'Копия с комбинацией')
    assert cloned.status_code == 201, cloned.get_json()
    clone_id = cloned.get_json()['id']
    assert clone_id != original_id
    renamed = client.put(f"/lobbies/{pair['lobby'].id}/templates/{clone_id}",
                         headers=auth_headers(pair['actor_user']), json={'name': 'Переименованный состав'})
    assert renamed.status_code == 200
    db.session.expire_all()
    assert db.session.get(LobbyItemTemplate, original_id).attributes['consumable'] == original
    assert db.session.get(LobbyItemTemplate, clone_id).attributes['consumable'] == combined
    item = local_item(clone_id)
    value = data(pair)
    value['inventory']['backpack'] = [item]
    value['health'].update(stress=5, painLevel=2, radiation=0, exhaustion=0)
    save(pair, value)
    response = general(client, pair, auth_headers, item, 's-composite')
    assert response.status_code == 200, response.get_json()
    health = data(pair)['health']
    assert health['stress'] == 4 and health['painLevel'] == 1
    assert any(m['stat'] == 'strength' and m['value'] == 3 for m in health['combatMeta']['consumableModifiers'])
    assert {e['area'] for e in health['effects'] if e['type'] == 'delayed_adjustment'} == {'editor-1', 'editor-2'}
    assert data(pair)['inventory']['backpack'][0]['uses'] == 1
    receipt(pair, 's-composite')
    elapse(pair, 120)
    health = data(pair)['health']
    assert health['radiation'] == 4 and health['exhaustion'] == 1
    assert db.session.get(LobbyItemTemplate, original_id).attributes['consumable'] == original
    assert db.session.get(LobbyItemTemplate, clone_id).attributes['consumable'] == combined


@pytest.mark.parametrize('invalid', ['negative-duration', 'not-a-number', 'conflicting-stat'])
def test_editor_invalid_create_or_update_preserves_valid_profile_and_row_count(client, pair, auth_headers, invalid):
    valid = base_profile()
    created = create_profile(client, pair, auth_headers, valid, 'Валидный состав')
    assert created.status_code == 201, created.get_json()
    template_id = created.get_json()['id']
    count = LobbyItemTemplate.query.count()
    broken = deepcopy(valid)
    if invalid == 'negative-duration':
        broken['modifiers'][0]['remaining'] = -1
    elif invalid == 'not-a-number':
        broken['direct']['stress_delta'] = 'не число'
    else:
        broken['modifiers'].append({'stat': 'strength', 'value': -2, 'remaining': 3, 'tick': 'turn_end'})
    rejected = create_profile(client, pair, auth_headers, broken, 'Недопустимая копия')
    assert rejected.status_code == 400, rejected.get_json()
    assert LobbyItemTemplate.query.count() == count
    assert LobbyItemTemplate.query.filter_by(name='Недопустимая копия').first() is None
    edited = client.put(f"/lobbies/{pair['lobby'].id}/templates/{template_id}",
        headers=auth_headers(pair['actor_user']), json={'attributes': {'uses': 2, 'consumable': broken}})
    assert edited.status_code == 400, edited.get_json()
    db.session.expire_all()
    assert db.session.get(LobbyItemTemplate, template_id).attributes['consumable'] == valid
    assert LobbyItemTemplate.query.count() == count
    assert ConsumableUse.query.count() == 0
