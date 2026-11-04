"""Медицинские повторы: коррекция NOT_RUN; два срока шины ранее PASS."""
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from helpers.journal_assertions import assert_same_game_state
import sys
from types import SimpleNamespace

import pytest

import app.services.character_events as character_events_module
import app.services.combat as combat_module
import app.services.deferred_consumable as deferred_module
import app.services.medical_procedure as medical_module
from app.extensions import db
from app.models import ConsumableUse, DeferredCombatAction, LobbyCharacter
from test_consumable_use import pair
from test_medical_procedure import install_item, payload, apply
from test_regression_end_combat_operations import (
    canonical_fixture, combat_url, forbid_rng, full_snapshot, reject_unchanged,
)


@contextmanager
def scripted(monkeypatch, module, expected):
    sequence = deque(expected)
    def draw(low, high):
        assert sequence, (module.__name__, low, high)
        function, expected_low, expected_high, value = sequence.popleft()
        caller = sys._getframe(1)
        if caller.f_code.co_name == '<listcomp>':
            caller = caller.f_back
        assert caller.f_code.co_name == function
        assert caller.f_globals['__name__'] == module.__name__
        assert (low, high) == (expected_low, expected_high)
        return value
    with monkeypatch.context() as controlled:
        controlled.setattr(module, 'random', SimpleNamespace(randint=draw))
        yield
        assert not sequence


def actor_data(pair):
    db.session.expire_all()
    return deepcopy(db.session.get(LobbyCharacter, pair['actor_character'].id).data)


def configure(pair, kind):
    if kind == 'splint':
        effect = {'id': 'retry-fracture', 'type': 'fracture', 'area': 'leftLeg', 'source': 'fall'}
        name = 'Шина'
        description = 'Фиксирует перелом. Временно восстанавливает конечность до 1 ОЗ.'
        application = {'kind': 'injury', 'effect': deepcopy(effect),
                       'treatmentMode': 'fix_fracture', 'actionPoints': 6}
        quantity, cost, dc, maximum = 2, 8, 10, 1
    elif kind == 'ampoule':
        effect = None
        name, description = 'Тестовая ампула', 'Ампула. +5 ХП.'
        application = {'kind': 'self', 'actionPoints': 1}
        quantity, cost, dc, maximum = 2, 3, 4, 1
    else:
        assert kind == 'surgery'
        effect = {'id': 'retry-mangled', 'type': 'mangled_limb', 'area': 'leftLeg', 'source': 'damage'}
        name = 'Набор полного восстановления конечности'
        description = ('Время использования - 15 ОД. Восстанавливает утерянный орган '
                       'или искореженную конечность. Бонус медикамента +3. '
                       '5 использований. Усиливает боль на 3 уровня.')
        application = {'kind': 'injury', 'effect': deepcopy(effect),
                       'treatmentMode': 'restore_mangled_limb',
                       'application': {'item_uses': 1}, 'actionPoints': 15}
        quantity, cost, dc, maximum = 1, 17, 11, 5
    _, profile, item = install_item(pair, name, description, quantity=quantity,
                                    medicine=10, effects=[effect] if effect else [],
                                    pain=3 if kind == 'splint' else (1 if kind == 'surgery' else 0))
    data = deepcopy(pair['actor_character'].data)
    data['health']['current'] = 100
    data['health']['stress'] = 0
    if effect:
        data['health']['zones']['leftLeg']['current'] = 0
    pair['actor_character'].data = data
    canonical_fixture(pair)
    # Настоящая полная оплата через spend; начальные AP — разрешённая фикстура.
    pair['actor_location'].action_points_current = cost + 2
    db.session.commit()
    return {'kind': kind, 'item': deepcopy(item), 'profile': profile,
            'application': application, 'effect': effect,
            'cost': cost, 'dc': dc, 'maximum': maximum}


def spend(client, pair, auth_headers, spec, identity):
    selection = {'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(spec['item'])},
                 'application': deepcopy(spec['application']),
                 'target_character_id': pair['actor_character'].id,
                 'treatment_request_id': None, 'server_authoritative': True}
    response = client.post(combat_url(pair) + '/spend',
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id,
            'action_points': spec['cost'], 'allow_deferred': True,
            'pending_action_id': identity, 'pending_action_label': 'Проверочная процедура',
            'consumable_selection': selection})
    assert response.status_code == 200, response.get_json()
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'ready'
    assert row.remaining_action_points == 0
    assert row.payload['required_action_points'] == spec['cost']
    assert row.payload['selection'] == selection
    assert pair['actor_location'].action_points_current == 2
    return row


def first_failure(client, pair, auth_headers, monkeypatch, kind, identity):
    spec = configure(pair, kind)
    before_payment = actor_data(pair)
    with scripted(monkeypatch, deferred_module, [('prepare_medicine_roll', 1, 20, 1)]):
        row = spend(client, pair, auth_headers, spec, identity)
    assert row.payload['medicine_roll'] == 1
    after_payment = actor_data(pair)
    assert after_payment['inventory'] == before_payment['inventory']
    assert after_payment['health']['current'] == before_payment['health']['current']
    assert after_payment['health']['zones'] == before_payment['health']['zones']
    assert after_payment['health']['effects'] == before_payment['health']['effects']
    body = payload(pair, spec['item'], spec['application'], operation=identity, combat=True)
    body['deferred_action_id'] = identity
    failed = apply(client, pair, auth_headers, body)
    assert failed.status_code == 200, failed.get_json()
    result = failed.get_json()['medical_result']
    assert result['success'] is False
    assert result['roll'] == 1
    assert result['difficulty'] == spec['dc']
    assert result['outcome'] is None
    assert result['consumable'] == {}  # Нет успешной exposure/addiction или бонуса ОД.
    stored = actor_data(pair)
    expected_inventory = deepcopy(after_payment['inventory'])
    expected_item = expected_inventory['backpack'][0]
    expected_item.update(quantity=1, uses=spec['maximum'] - 1 if kind == 'surgery' else 1,
                         maxUses=spec['maximum'])
    expected_item.setdefault('attributes', {})['uses_remaining'] = expected_item['uses']
    assert stored['inventory'] == expected_inventory
    retry = deepcopy(stored['health']['combatMeta']['mustDoRetry'])
    assert retry['kind'] == 'medical'
    assert retry['skill_path'] == 'skills.other.medicine'
    assert retry['difficulty'] == spec['dc']
    private = retry['medical_retry']
    assert private['server_authoritative'] is True
    assert private['actor_character_id'] == pair['actor_character'].id
    assert private['target_character_id'] == pair['actor_character'].id
    assert private['item_id'] == spec['item']['id']
    assert private['item_path'] == ['inventory', 'backpack', 0]
    assert private['item_snapshot'] == expected_item
    assert private['application'] == spec['application']
    assert private['interaction_context'] == {
        'actorLocationCharacterId': pair['actor_location'].id,
        'lobbyId': pair['lobby'].id, 'locationId': pair['location'].id}
    expected_health = deepcopy(after_payment['health'])
    expected_health['combatMeta'].pop('completedPendingActionId')
    expected_health['combatMeta']['mustDoRetry'] = deepcopy(retry)
    assert_same_game_state({'health':stored['health']}, {'health':expected_health}, journal_status='completed')
    journal=stored['health']['combatMeta']['actionJournal']
    assert journal[-1]['action']=='medical_procedure' and journal[-1]['action_points']==spec['cost']
    assert pair['actor_location'].action_points_current == 2
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'completed'
    assert row.payload['medicine_roll'] == 1
    assert ConsumableUse.query.count() == 1
    receipt = db.session.get(ConsumableUse, identity)
    assert receipt.actor_character_id == receipt.target_character_id == pair['actor_character'].id
    return spec, body, failed.get_json(), retry


def no_private(value, path='response'):
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in {'medical_retry', 'item_snapshot', 'interaction_context'}, (path, key)
            no_private(child, path + '.' + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            no_private(child, f'{path}[{index}]')


def must_do(client, pair, auth_headers, *, user=None, **extra):
    return client.post(combat_url(pair) + '/action',
        headers=auth_headers(user or pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id,
            'action_key': 'must_do_it', **extra})


@pytest.mark.parametrize('kind', ['splint', 'ampoule', 'surgery'])
@pytest.mark.parametrize('success', [True, False], ids=['success', 'failure'])
def test_server_medical_retry_spends_once_uses_saved_procedure_and_preserves_failure(
        client, pair, auth_headers, monkeypatch, kind, success):
    forbid_rng(monkeypatch)
    identity = 'medical-retry-' + kind
    spec, body, _, saved_retry = first_failure(client, pair, auth_headers, monkeypatch, kind, identity)
    state = client.get(combat_url(pair), headers=auth_headers(pair['actor_user']))
    assert state.status_code == 200, state.get_json()
    actor = next(c for c in state.get_json()['characters']
                 if c['location_character_id'] == pair['actor_location'].id)
    assert actor['must_do_retry']['kind'] == 'medical'
    assert actor['must_do_retry']['difficulty'] == spec['dc']
    no_private(actor['must_do_retry'])
    reject_unchanged(pair, lambda: must_do(client, pair, auth_headers, user=pair['target_user']), 403)
    before = actor_data(pair)
    resources = (pair['actor_location'].action_points_current,
                 pair['actor_location'].free_actions_current, pair['actor_location'].movement_points_current)
    saved_row = deepcopy(db.session.get(DeferredCombatAction, identity).payload)
    sequence = [('_narrative_skill_check', 1, 20, 20 if success else 1)]
    if not success:
        sequence += [('apply_stress_trigger', 1, 20, 20), ('apply_stress_trigger', 1, 6, 2)]
    with scripted(monkeypatch, combat_module, sequence):
        response = must_do(client, pair, auth_headers,
            # Клиентские описания не заменяют серверный medical retry.
            narrative_action_name='Подмена', narrative_skill_path='skills.physical.strength',
            narrative_difficulty=1)
    assert response.status_code == 200, response.get_json()
    details = response.get_json()['must_do_it']
    assert details['kind'] == saved_retry['kind']
    assert details['name'] == saved_retry['name']
    assert details['skill_path'] == saved_retry['skill_path']
    assert details['difficulty'] == saved_retry['difficulty']
    assert details['check']['difficulty'] == spec['dc']
    assert details['check']['success'] is success
    assert details['check']['rolls'] == [20 if success else 1]
    no_private(response.get_json())
    after = actor_data(pair)
    assert after['inventory'] == before['inventory']
    assert (pair['actor_location'].action_points_current,
            pair['actor_location'].free_actions_current,
            pair['actor_location'].movement_points_current) == resources
    assert after['health']['stress'] == 1
    assert 'mustDoRetry' not in after['health']['combatMeta']
    assert after['health']['combatMeta']['mustDoUsage']['used'] == 1
    assert db.session.get(DeferredCombatAction, identity).payload == saved_row
    assert db.session.get(DeferredCombatAction, identity).status == 'completed'
    assert ConsumableUse.query.count() == 1
    assert 'addiction' not in details
    if success:
        assert details['medical_result']['target_character_id'] == pair['actor_character'].id
        if kind == 'splint':
            assert after['health']['zones']['leftLeg']['current'] == 0
            assert after['health']['painLevel'] == 2
            assert not any(e.get('id') == 'retry-fracture' for e in after['health']['effects'])
            fixed = [e for e in after['health']['effects'] if e['type'] == 'fracture_fixed']
            assert len(fixed) == 1 and fixed[0]['remaining_seconds'] == 86400
        elif kind == 'ampoule':
            assert after['health']['current'] == 105
            assert after['health']['effects'] == before['health']['effects']
        else:
            assert after['health']['zones']['leftLeg']['current'] == 100
            assert after['health']['zones']['leftLeg']['destructionDamage'] == 0
            assert after['health']['current'] == 200
            assert after['health']['painLevel'] == 4
            assert not any(e.get('id') == 'retry-mangled' for e in after['health']['effects'])
    else:
        assert 'medical_result' not in details
        assert after['health']['current'] == before['health']['current']
        assert after['health']['zones'] == before['health']['zones']
        assert after['health']['painLevel'] == before['health']['painLevel']
        manifestation = details['stress_manifestation']
        assert manifestation['trigger'] == 'must_do_it_failure'
        assert manifestation['before'] == manifestation['after'] == 1
        assert manifestation['manifested'] is True
        assert manifestation['effect_roll'] == 2
        stress = [e for e in after['health']['effects'] if e['source'] == 'stress_manifestation']
        assert len(stress) == 1 and stress[0]['type'] == 'stress_effect'
        assert stress[0]['stress_level'] == 1 and stress[0]['stress_roll'] == 2
        expected_effects = deepcopy(before['health']['effects']) + stress
        assert after['health']['effects'] == expected_effects
    # Replay обоих публичных путей: ни броска, ни нового расхода/receipt.
    reject_unchanged(pair, lambda: must_do(client, pair, auth_headers), 400)
    reject_unchanged(pair, lambda: apply(client, pair, auth_headers, body), 409)


def test_failed_medical_http_response_does_not_expose_private_saved_retry(
        client, pair, auth_headers, monkeypatch):
    forbid_rng(monkeypatch)
    events = []
    original_emit = character_events_module.socketio.emit
    def observe(event, *args, **kwargs):
        if event == 'character_data_updated':
            assert len(args) == 1
            if args[0].get('source') == 'medical_procedure':
                events.append(deepcopy(args[0]))
        return original_emit(event, *args, **kwargs)
    # Наблюдаем фактический wire payload после пересборки общего emitter.
    # Сам emitter, его авторизация и медицинская процедура выполняются штатно.
    monkeypatch.setattr(character_events_module.socketio, 'emit', observe)
    _, _, response, saved_retry = first_failure(client, pair, auth_headers, monkeypatch,
                                                'splint', 'medical-private-response')
    persisted = actor_data(pair)['health']['combatMeta']['mustDoRetry']
    assert persisted == saved_retry
    assert persisted['medical_retry']['server_authoritative'] is True
    expected_summary = deepcopy(saved_retry)
    expected_summary.pop('medical_retry')
    expected_summary.pop('attack_details', None)
    no_private(response)
    for field in ('actor_data', 'target_data'):
        public = response[field]
        assert public['health']['combatMeta']['mustDoRetry'] == expected_summary
        assert public['_character_id'] == pair['actor_character'].id
        assert public['_revision'] == pair['actor_character'].revision
    assert len(events) == 1
    assert events[0]['character_id'] == pair['actor_character'].id
    no_private(events[0])
    assert events[0]['updates']['data'] == response['actor_data']
    assert actor_data(pair)['health']['combatMeta']['mustDoRetry'] == saved_retry


def test_failed_medical_public_snapshot_save_preserves_trusted_retry_until_one_server_retry(
        client, pair, auth_headers, monkeypatch):
    forbid_rng(monkeypatch)
    identity = 'medical-public-save-retry'
    spec, old_body, response, saved_retry = first_failure(
        client, pair, auth_headers, monkeypatch, 'ampoule', identity)
    public = deepcopy(response['actor_data'])
    no_private(public)
    expected_summary = {key: deepcopy(value) for key, value in saved_retry.items()
                        if key not in {'medical_retry', 'attack_details'}}
    assert public['health']['combatMeta']['mustDoRetry'] == expected_summary
    original = actor_data(pair)
    resources = (pair['actor_location'].action_points_current,
                 pair['actor_location'].free_actions_current,
                 pair['actor_location'].movement_points_current)
    paid_payload = deepcopy(db.session.get(DeferredCombatAction, identity).payload)
    save_url = f"/lobbies/characters/{pair['actor_character'].id}"

    def assert_public_summary():
        state = client.get(combat_url(pair), headers=auth_headers(pair['actor_user']))
        assert state.status_code == 200, state.get_json()
        actor = next(c for c in state.get_json()['characters']
                     if c['location_character_id'] == pair['actor_location'].id)
        assert actor['must_do_retry'] == expected_summary
        no_private(actor['must_do_retry'])

    def save_snapshot(snapshot):
        saved = client.put(save_url, headers=auth_headers(pair['actor_user']),
                           json={'data': snapshot})
        assert saved.status_code == 200, saved.get_json()
        assert saved.get_json()['data']['_character_id'] == pair['actor_character'].id
        assert saved.get_json()['data']['_revision'] == pair['actor_character'].revision
        return saved

    reject_unchanged(pair, lambda: client.put(save_url,
        headers=auth_headers(pair['target_user']), json={'data': public}), 403)
    # Первый save использует именно snapshot/revision медицинского HTTP-ответа.
    public['notes'] = 'Обычное сохранение после неудачной процедуры'
    revision = public['_revision']
    save_snapshot(public)
    assert pair['actor_character'].revision > revision
    assert actor_data(pair)['health']['combatMeta']['mustDoRetry'] == saved_retry
    assert actor_data(pair)['notes'] == public['notes']
    assert_public_summary()

    forged = deepcopy(public)
    forged['_revision'] = pair['actor_character'].revision
    forged_retry = forged['health']['combatMeta']['mustDoRetry']
    forged_retry.update(kind='manual', name='Подменённая процедура', difficulty=1)
    forged_retry['medical_retry'] = {
        'server_authoritative': True,
        'actor_character_id': pair['target_character'].id,
        'target_character_id': pair['target_character'].id,
        'item_id': 'foreign-item', 'item_path': ['inventory', 'pockets', 999],
        'item_snapshot': {'id': 'foreign-item', 'quantity': 999},
        'application': {'kind': 'self', 'actionPoints': 0},
        'interaction_context': {'actorLocationCharacterId': pair['target_location'].id,
                                'lobbyId': -1, 'locationId': -1},
    }
    save_snapshot(forged)
    assert actor_data(pair)['health']['combatMeta']['mustDoRetry'] == saved_retry
    assert_public_summary()
    missing = deepcopy(public)
    missing['_revision'] = pair['actor_character'].revision
    missing['health']['combatMeta'].pop('mustDoRetry')
    save_snapshot(missing)
    before_retry = actor_data(pair)
    assert before_retry['health']['combatMeta']['mustDoRetry'] == saved_retry
    assert before_retry['inventory'] == {**original['inventory'],'pocketMaxVolume':10}
    assert before_retry['health']['current'] == 100
    assert_public_summary()
    assert ConsumableUse.query.count() == 1
    assert (pair['actor_location'].action_points_current,
            pair['actor_location'].free_actions_current,
            pair['actor_location'].movement_points_current) == resources

    with scripted(monkeypatch, combat_module, [('_narrative_skill_check', 1, 20, 20)]):
        retried = must_do(client, pair, auth_headers,
            narrative_action_name='Чужая процедура', narrative_skill_path='skills.physical.strength',
            narrative_difficulty=1)
    assert retried.status_code == 200, retried.get_json()
    details = retried.get_json()['must_do_it']
    for key in ('kind', 'name', 'skill_path', 'difficulty'):
        assert details[key] == saved_retry[key]
    assert details['check']['rolls'] == [20] and details['check']['success'] is True
    assert details['check']['difficulty'] == spec['dc']
    assert details['medical_result']['target_character_id'] == pair['actor_character'].id
    no_private(retried.get_json())
    after = actor_data(pair)
    assert after['health']['current'] == 105
    assert after['health']['stress'] == 1
    assert after['health']['effects'] == before_retry['health']['effects']
    assert after['inventory'] == {**original['inventory'],'pocketMaxVolume':10}
    assert 'mustDoRetry' not in after['health']['combatMeta']
    assert after['health']['combatMeta']['mustDoUsage']['used'] == 1
    assert (pair['actor_location'].action_points_current,
            pair['actor_location'].free_actions_current,
            pair['actor_location'].movement_points_current) == resources
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'completed' and row.payload == paid_payload
    assert ConsumableUse.query.count() == 1
    receipt = db.session.get(ConsumableUse, identity)
    assert receipt.actor_character_id == receipt.target_character_id == pair['actor_character'].id
    reject_unchanged(pair, lambda: must_do(client, pair, auth_headers), 400)
    reject_unchanged(pair, lambda: apply(client, pair, auth_headers, old_body), 409)

    # Новый save со свежей revision не может возродить завершённый retry.
    stale_retry = pair['actor_character'].data_snapshot()
    stale_retry['health']['combatMeta']['mustDoRetry'] = deepcopy(expected_summary)
    save_snapshot(stale_retry)
    final = actor_data(pair)
    assert 'mustDoRetry' not in final['health']['combatMeta']
    assert final['health']['current'] == 105 and final['inventory'] == {**original['inventory'],'pocketMaxVolume':10}
    assert ConsumableUse.query.count() == 1
    assert db.session.get(DeferredCombatAction, identity).payload == paid_payload
    # И новый private, присланный без существующей серверной процедуры, не принимается.
    fabricated = pair['actor_character'].data_snapshot()
    fabricated['health']['combatMeta']['mustDoRetry'] = deepcopy(forged_retry)
    save_snapshot(fabricated)
    assert 'mustDoRetry' not in actor_data(pair)['health']['combatMeta']
    assert actor_data(pair)['health']['current'] == 105
    assert actor_data(pair)['inventory'] == {**original['inventory'],'pocketMaxVolume':10}
    assert ConsumableUse.query.count() == 1
    assert (pair['actor_location'].action_points_current,
            pair['actor_location'].free_actions_current,
            pair['actor_location'].movement_points_current) == resources
    reject_unchanged(pair, lambda: must_do(client, pair, auth_headers), 400)


def timer_fixture(pair, name):
    fracture = {'id': 'timer-fracture', 'type': 'fracture', 'area': 'leftLeg', 'source': 'fall'}
    _, _, item = install_item(pair, name,
        'Фиксирует перелом. Временно восстанавливает конечность до 1 ОЗ.',
        quantity=2, medicine=10, effects=[fracture], pain=0)
    data = deepcopy(pair['actor_character'].data)
    data['health']['current'] = 700
    data['health']['zones']['leftLeg']['current'] = 0
    pair['actor_character'].data = data
    pair['actor_character'].time_active = True
    canonical_fixture(pair)
    pair['actor_location'].action_points_current = 10
    db.session.commit()
    return {'item': deepcopy(item), 'cost': 8,
            'application': {'kind': 'injury',
                'effect': {'type': 'damaged_zone', 'area': 'leftLeg'},
                'treatmentMode': 'restore_limb', 'actionPoints': 6}}


def assert_fracture_and_temporary(pair, *, remaining=None, seconds=None, expired=False):
    health = actor_data(pair)['health']
    fracture = [e for e in health['effects'] if e.get('id') == 'timer-fracture']
    assert len(fracture) == 1 and fracture[0]['type'] == 'fracture'
    assert not any(e['type'] == 'fracture_fixed' for e in health['effects'])
    temporary = [e for e in health['effects'] if e['type'] == 'temporary_limb_restoration']
    assert health['current'] == 700
    assert health['zones']['leftLeg']['current'] == (0 if expired else 1)
    if expired:
        assert temporary == []
    else:
        assert len(temporary) == 1
        assert temporary[0]['area'] == 'leftLeg'
        assert temporary[0]['previous_health'] == 0
        if remaining is not None:
            assert temporary[0]['remaining'] == remaining
        if seconds is not None:
            assert temporary[0]['remaining_seconds'] == seconds


def end_turn(client, pair, auth_headers, role):
    response = client.post(combat_url(pair) + '/end_turn',
        headers=auth_headers(pair[role + '_user']),
        json={'location_character_id': pair[role + '_location'].id})
    assert response.status_code == 200, response.get_json()


def test_basic_splint_expires_on_fourth_own_turn_without_curing_fracture(
        client, pair, auth_headers, monkeypatch):
    forbid_rng(monkeypatch)
    spec = timer_fixture(pair, 'Шина')
    identity = 'splint-four-turns'
    with scripted(monkeypatch, deferred_module, [('prepare_medicine_roll', 1, 20, 20)]):
        row = spend(client, pair, auth_headers, spec, identity)
    assert row.payload['medicine_roll'] == 20
    body = payload(pair, spec['item'], spec['application'], operation=identity, combat=True)
    body['deferred_action_id'] = identity
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['medical_result']['success'] is True
    assert response.get_json()['medical_result']['difficulty'] == 10
    assert_fracture_and_temporary(pair, remaining=4)
    used = deepcopy(actor_data(pair)['inventory'])
    assert used['backpack'][0]['quantity'] == 1
    assert ConsumableUse.query.count() == 1
    reject_unchanged(pair, lambda: apply(client, pair, auth_headers, body), 409)
    for elapsed in range(1, 5):
        end_turn(client, pair, auth_headers, 'actor')
        assert_fracture_and_temporary(pair, remaining=4 - elapsed if elapsed < 4 else None,
                                     expired=elapsed == 4)
        end_turn(client, pair, auth_headers, 'target')
        # Чужой ход и общий6s раунда не должны второй раз тикать тот же turn_end.
        assert_fracture_and_temporary(pair, remaining=4 - elapsed if elapsed < 4 else None,
                                     expired=elapsed == 4)
        assert actor_data(pair)['inventory'] == used
        assert ConsumableUse.query.count() == 1
    assert db.session.get(DeferredCombatAction, identity).status == 'completed'


def advance_minutes(client, pair, auth_headers, amount):
    db.session.refresh(pair['lobby'])
    lobby = pair['lobby']
    previous = lobby.game_day * 1440 + lobby.game_time_minutes
    day, minute = divmod(previous + amount, 1440)
    response = client.patch(f'/lobbies/{lobby.id}/time',
        headers=auth_headers(pair['actor_user']), json={'game_day': day, 'game_time_minutes': minute})
    assert response.status_code == 200, response.get_json()
    assert response.get_json() == {'game_day': day, 'game_time_minutes': minute}


def test_sharnirov_splint_expires_at_ten_global_minutes_without_curing_fracture(
        client, pair, auth_headers, monkeypatch):
    forbid_rng(monkeypatch)
    spec = timer_fixture(pair, 'Шина Шарнирова')
    ended = client.post(combat_url(pair) + '/end', headers=auth_headers(pair['actor_user']), json={})
    assert ended.status_code == 200, ended.get_json()
    assert ended.get_json()['status'] == 'idle'
    initial_ap = pair['actor_location'].action_points_current
    body = payload(pair, spec['item'], spec['application'], operation='splint-ten-minutes')
    with scripted(monkeypatch, medical_module, [('apply', 1, 20, 20)]):
        response = apply(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['medical_result']['success'] is True
    assert response.get_json()['medical_result']['difficulty'] == 10
    assert pair['actor_location'].action_points_current == initial_ap
    assert_fracture_and_temporary(pair, remaining=10, seconds=600)
    used = deepcopy(actor_data(pair)['inventory'])
    assert used['backpack'][0]['quantity'] == 1
    assert ConsumableUse.query.count() == 1
    assert db.session.get(DeferredCombatAction, 'splint-ten-minutes') is None
    reject_unchanged(pair, lambda: apply(client, pair, auth_headers, body), 409)
    advance_minutes(client, pair, auth_headers, 9)
    assert_fracture_and_temporary(pair, remaining=1, seconds=60)
    advance_minutes(client, pair, auth_headers, 1)
    assert_fracture_and_temporary(pair, expired=True)
    assert pair['actor_location'].action_points_current == initial_ap
    assert actor_data(pair)['inventory'] == used
    assert ConsumableUse.query.count() == 1
