"""Остаточные применения каталога: только подготовка, все случаи NOT_RUN."""
from contextlib import contextmanager
from copy import deepcopy
from helpers.journal_assertions import assert_same_game_state
import sys
from types import SimpleNamespace

import pytest

import app.services.addictions as addictions_module
import app.services.combat as combat_module
import app.services.character_interaction as interaction_module
import app.services.deferred_consumable as deferred_module
import app.services.general_consumable as general_module
import app.services.medical_procedure as medical_module
from app.extensions import db
from app.models import CharacterInteractionRequest, ConsumableUse, DeferredCombatAction, LobbyCharacter, LocationCombatState
from app.models.templates import ItemTemplate
from app.models.lobby_templates import LobbyItemTemplate
from app.services.consumable_effects import parse_consumable_effects
from test_consumable_use import pair
from test_general_consumable import install_consumable
from test_medical_procedure import install_item, payload as medical_payload, apply as medical
from test_regression_consumable_boundaries import create_profile, local_item, general
from test_regression_end_combat_operations import (
    canonical_fixture, combat_url, forbid_rng, full_snapshot, record, reject_unchanged,
)
from test_regression_medical_retry import scripted, end_turn


@pytest.fixture(autouse=True)
def scoped_rng(monkeypatch):
    forbid_rng(monkeypatch)
    def forbidden(*args):
        pytest.fail(f'Неожиданный случайный результат: {args}')
    monkeypatch.setattr(addictions_module, 'random', SimpleNamespace(random=forbidden, randint=forbidden))
    monkeypatch.setattr(general_module, 'random', SimpleNamespace(random=forbidden))
    monkeypatch.setattr(interaction_module, 'random', SimpleNamespace(randint=forbidden))


def data(pair, role='actor'):
    db.session.expire_all()
    return deepcopy(db.session.get(LobbyCharacter, pair[role + '_character'].id).data)


def write(pair, value, role='actor'):
    pair[role + '_character'].data = deepcopy(value)
    db.session.commit()


def prepare(pair, *, active=False, ap=5):
    canonical_fixture(pair)
    state = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    state.status = 'active' if active else 'idle'
    pair['actor_location'].action_points_current = ap
    db.session.commit()


def use_receipt(pair, identity, *, target='actor'):
    row = db.session.get(ConsumableUse, identity)
    assert row is not None
    assert row.actor_character_id == pair['actor_character'].id
    assert row.target_character_id == pair[target + '_character'].id
    assert ConsumableUse.query.count() == 1


def paid(client, pair, auth_headers, item, application, identity, total, *, general_use=False):
    selection = {'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
                 'application': deepcopy(application), 'target_character_id': pair['actor_character'].id,
                 'treatment_request_id': None, 'server_authoritative': True,
                 'server_operation': 'general' if general_use else 'medical'}
    response = client.post(combat_url(pair) + '/spend', headers=auth_headers(pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_points': total,
        'allow_deferred': True, 'pending_action_id': identity,
        'pending_action_label': 'Проверка каталога', 'consumable_selection': selection})
    assert response.status_code == 200, response.get_json()
    row = db.session.get(DeferredCombatAction, identity)
    assert row.status == 'ready' and row.remaining_action_points == 0
    assert row.payload['required_action_points'] == total
    assert row.payload['payment_details']['total_action_points'] == total
    assert row.payload['selection'] == selection
    return row


def medical_body(pair, item, application, identity, *, combat=False):
    body = medical_payload(pair, item, application, operation=identity, combat=combat)
    if combat:
        body['deferred_action_id'] = identity
    return body


def template_url(pair, identity):
    return f"/lobbies/{pair['lobby'].id}/templates/{identity}"


def editor_snapshot(pair):
    return {'world': full_snapshot(pair),
            'global': [record(row) for row in ItemTemplate.query.order_by(ItemTemplate.id)],
            'local': [record(row) for row in LobbyItemTemplate.query.order_by(LobbyItemTemplate.id)]}


def reject_editor(pair, operation):
    before = editor_snapshot(pair)
    response = operation()
    assert response.status_code == 400, response.get_json()
    assert editor_snapshot(pair) == before
    return response


@pytest.mark.parametrize('name', ['Кровоостанавливающее "Хлопок"', 'Кровоостанавливающее «Хлопок»'])
def test_cotton_quote_variants_apply_then_expire_after_ten_recipient_turns(
        client, pair, auth_headers, monkeypatch, name):
    effects = [{'id': 'cotton-external', 'type': 'bleeding_external_light', 'area': 'leftArm'},
               {'id': 'cotton-internal', 'type': 'bleeding_internal_extreme', 'area': 'chest'},
               {'id': 'cotton-fracture', 'type': 'fracture', 'area': 'rightLeg'}]
    template, profile, item = install_item(pair, name, 'Ампула. Останавливает все кровотечения.',
                                         quantity=2, medicine=10, effects=effects)
    assert profile['direct']['stop_all_bleeding'] is True
    value = data(pair)
    value['health'].update(current=700, exhaustion=0)
    write(pair, value)
    prepare(pair, active=True)
    original = data(pair)
    application = {'kind': 'self', 'actionPoints': 0}
    identity = 'cotton-ten-turns'
    with scripted(monkeypatch, deferred_module, [('prepare_medicine_roll', 1, 20, 20)]):
        paid(client, pair, auth_headers, item, application, identity, 3)
    assert data(pair)['inventory'] == original['inventory']
    body = medical_body(pair, item, application, identity, combat=True)
    response = medical(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['success'] is True and result['roll'] == 20 and result['difficulty'] == 4
    assert result['outcome']['closed_bleeding'] == 2
    health = data(pair)['health']
    assert not any(e['type'].startswith('bleeding_') and e['type'] != 'bleeding_prevention'
                   for e in health['effects'])
    assert any(e.get('id') == 'cotton-fracture' for e in health['effects'])
    assert health['exhaustion'] == 1
    prevention = [e for e in health['effects'] if e['type'] == 'bleeding_prevention']
    assert len(prevention) == 1 and prevention[0]['remaining'] == 10
    assert prevention[0]['tick'] == 'turn_end'
    expected_inventory = deepcopy(original['inventory'])
    expected_inventory['backpack'][0].update(quantity=1, uses=1, maxUses=1)
    expected_inventory['backpack'][0].setdefault('attributes', {})['uses_remaining'] = 1
    assert data(pair)['inventory'] == expected_inventory
    assert pair['actor_location'].action_points_current == 2
    use_receipt(pair, identity)
    reject_unchanged(pair, lambda: medical(client, pair, auth_headers, body), 409)
    used_inventory = deepcopy(data(pair)['inventory'])
    for number in range(1, 11):
        end_turn(client, pair, auth_headers, 'actor')
        remaining = [e for e in data(pair)['health']['effects'] if e['type'] == 'bleeding_prevention']
        if number < 10:
            assert len(remaining) == 1 and remaining[0]['remaining'] == 10 - number
        else:
            assert remaining == []
        end_turn(client, pair, auth_headers, 'target')
        assert [e for e in data(pair)['health']['effects'] if e['type'] == 'bleeding_prevention'] == remaining
        assert data(pair)['inventory'] == used_inventory
    assert db.session.get(ItemTemplate, template.id).attributes['consumable'] == profile
    use_receipt(pair, identity)


@pytest.mark.parametrize('name,tool,recipient,packet,packet_known,expected_duration,error', [
    ('Пакет физраствора', None, None, None, False, None, 'капельница'),
    ('Пакет физраствора', 'Хирургический набор', None, None, False, 2, None),
    ('Пакет физраствора', 'Капельница', None, None, False, 3, None),
    ('Пакет крови', None, 2, 1, True, None, 'капельница'),
    ('Пакет крови', 'Хирургический набор', None, 1, True, None, 'определить группу'),
    ('Пакет крови', 'Капельница', 2, None, False, None, 'определить группу'),
    ('Пакет крови', 'Хирургический набор', 2, 1, True, 4, None),
    # Чек-лист79: скрытая числовая группа не равна определённой игроком группе.
    ('Пакет крови', 'Капельница', 2, 1, False, None, 'определить группу'),
])
def test_saline_and_blood_infusion_require_real_tools_and_known_types(
        client, pair, auth_headers, monkeypatch, name, tool, recipient, packet, packet_known, expected_duration, error):
    template, profile, item = install_item(pair, name, 'Восстанавливает стадию кровопотери.',
                                         quantity=1, medicine=10)
    assert profile['direct']['requires_infusion_tool'] is True
    value = data(pair)
    value['health'].update(current=700, blood='medium', bloodStage='medium')
    value['health'].setdefault('combatMeta', {}).update(bloodType=recipient, bloodTypeKnown=recipient is not None)
    if name == 'Пакет крови':
        value['inventory']['backpack'][0]['attributes'] = {'bloodType': packet, 'bloodTypeKnown': packet_known}
    equipment = {'id': 'infusion-tool', 'name': tool, 'category': 'misc', 'quantity': 1,
                 'customNote': 'Многоразовый инструмент'} if tool else None
    if equipment:
        value['inventory']['backpack'].append(equipment)
    write(pair, value)
    prepare(pair)
    item = data(pair)['inventory']['backpack'][0]
    body = medical_body(pair, item, {'kind': 'self', 'actionPoints': 0}, 'infusion-boundary')
    if error:
        before = full_snapshot(pair)
        response = medical(client, pair, auth_headers, body)
        assert response.status_code == 400, response.get_json()
        assert error in response.get_json()['error']['message']
        assert_same_game_state(full_snapshot(pair), before)
        assert ConsumableUse.query.count() == 0
        return
    with scripted(monkeypatch, medical_module, [('apply', 1, 20, 4)]):
        response = medical(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['roll'] == 4 and result['difficulty'] == 4 and result['success'] is True
    assert result['payment_details']['total_action_points'] == 2
    assert result['outcome']['infusion_bonus'] == (1 if tool == 'Капельница' else 0)
    health = data(pair)['health']
    recovery = [e for e in health['effects'] if e['type'] == 'blood_recovery']
    assert len(recovery) == 1 and recovery[0]['remaining'] == expected_duration
    assert recovery[0]['tick'] == 'turn_end'
    assert health['bloodStage'] == 'medium'
    assert data(pair)['inventory']['backpack'] == [equipment]
    assert pair['actor_location'].action_points_current == 5
    use_receipt(pair, 'infusion-boundary')
    reject_unchanged(pair, lambda: medical(client, pair, auth_headers, body), 409)
    assert db.session.get(ItemTemplate, template.id).attributes['consumable'] == profile


def test_antirad_b4_is_paid_applied_once_and_removes_five_radiation_per_own_turn(
        client, pair, auth_headers, monkeypatch):
    # Существующий профиль tests/test_consumable_rules.py: Антирад-Б, ампула на4хода.
    template, profile, item = install_item(pair, 'Антирад-Б',
        'Ампула. -5 радиации. Действует 4 хода. +1 Уровень истощения', quantity=2, medicine=10)
    value = data(pair)
    value['health'].update(current=700, radiation=30, exhaustion=0)
    write(pair, value)
    prepare(pair, active=True)
    saved_profile = deepcopy(db.session.get(ItemTemplate, item['templateId']).attributes['consumable'])
    assert saved_profile == profile
    treatment = [e for e in profile['effects'] if e['type'] == 'radiation_treatment']
    assert len(treatment) == 1 and treatment[0]['value'] == -5 and treatment[0]['remaining'] == 4
    application = {'kind': 'self', 'actionPoints': 0}
    with scripted(monkeypatch, deferred_module, [('prepare_medicine_roll', 1, 20, 20)]):
        paid(client, pair, auth_headers, item, application, 'antirad-b4', 3)
    assert data(pair)['health']['radiation'] == 30
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 2
    body = medical_body(pair, item, application, 'antirad-b4', combat=True)
    response = medical(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['success'] is True and result['roll'] == 20 and result['difficulty'] == 4
    assert result['payment_details']['total_action_points'] == 3
    assert data(pair)['health']['radiation'] == 30 and data(pair)['health']['exhaustion'] == 1
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 1
    assert pair['actor_location'].action_points_current == 2
    use_receipt(pair, 'antirad-b4')
    reject_unchanged(pair, lambda: medical(client, pair, auth_headers, body), 409)
    for number in range(1, 5):
        end_turn(client, pair, auth_headers, 'actor')
        assert data(pair)['health']['radiation'] == 30 - 5 * number
        active = [e for e in data(pair)['health']['effects'] if e['type'] == 'radiation_treatment']
        if number < 4:
            assert len(active) == 1 and active[0]['remaining'] == 4 - number
        else:
            assert active == []
        end_turn(client, pair, auth_headers, 'target')
        assert data(pair)['health']['radiation'] == 30 - 5 * number
    assert data(pair)['inventory']['backpack'][0]['quantity'] == 1
    assert db.session.get(ItemTemplate, item['templateId']).attributes['consumable'] == saved_profile
    use_receipt(pair, 'antirad-b4')


def test_blood_collection_uses_real_patient_consent_and_creates_one_typed_packet(
        client, pair, auth_headers):
    packet_profile = parse_consumable_effects('Пакет крови. Восстанавливает стадию кровопотери.')
    packet_template = ItemTemplate(name='Пакет крови', category='consumable',
                                   attributes={'consumable': packet_profile})
    db.session.add(packet_template)
    _, profile, item = install_item(pair, 'Набор для забора крови',
        'При использовании выкачивает кровь и преобразует набор в пакет крови.', quantity=1, medicine=10)
    # Врач — обычный владелец; пациент становится ГМ до baseline, не обход доступа.
    pair['lobby'].gm_id = pair['target_user']['id']
    db.session.commit()
    donor = data(pair, 'target')
    donor['health'].update(current=700, blood='normal', bloodStage='normal', exhaustion=0)
    donor['health'].setdefault('combatMeta', {}).update(bloodType=4, bloodTypeKnown=True)
    write(pair, donor, 'target')
    prepare(pair)
    body = medical_payload(pair, item, {'kind': 'self', 'actionPoints': 0},
                           operation='real-consent-blood-draw', target=pair['target_character'])
    reject_unchanged(pair, lambda: medical(client, pair, auth_headers, body), 403)
    created = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/character-interactions",
        headers=auth_headers(pair['actor_user']), json={
            'actor_location_character_id': pair['actor_location'].id,
            'target_character_id': pair['target_character'].id, 'kind': 'treatment',
            'payload': {'procedure': {'item_name': 'Набор для забора крови'}}})
    assert created.status_code == 201, created.get_json()
    consent_id = created.get_json()['id']
    assert created.get_json()['status'] == 'pending'
    decided = client.post(f"/lobbies/{pair['lobby'].id}/character-interactions/{consent_id}/response",
                           headers=auth_headers(pair['target_user']), json={'decision': 'accept'})
    assert decided.status_code == 200 and decided.get_json()['status'] == 'accepted'
    body['interaction_request_id'] = consent_id
    response = medical(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['roll'] is None and result['success'] is True
    assert result['outcome']['blood_stage'] == 'medium'
    assert result['payment_details']['total_action_points'] == 2
    assert data(pair, 'target')['health']['bloodStage'] == 'medium'
    assert data(pair, 'target')['health']['exhaustion'] == 2
    actor = data(pair)
    assert actor['inventory']['backpack'] == []
    packets = [i for i in actor['inventory']['pockets'] if i.get('templateId') == packet_template.id]
    assert len(packets) == 1
    packet = packets[0]
    assert packet['id'] == result['outcome']['packet_id'] and packet['quantity'] == 1
    assert packet['attributes']['bloodType'] == 4 and packet['attributes']['bloodTypeKnown'] is True
    assert packet['attributes']['donorCharacterId'] == pair['target_character'].id
    assert packet['attributes']['bloodDonorProof'] and packet['isStackable'] is False
    assert db.session.get(CharacterInteractionRequest, consent_id).status == 'completed'
    assert pair['actor_location'].action_points_current == 5
    use_receipt(pair, 'real-consent-blood-draw', target='target')
    reject_unchanged(pair, lambda: medical(client, pair, auth_headers, body), 403)
    assert profile['direct']['blood_collection'] is True


@pytest.mark.parametrize('name,description,stress,psy,chance,duration,tick', [
    ('Подавитель эмоций', 'Действует 6 перемещений. -25 Пси-состояний.', 5, 25, 50, 6, 'movement_end'),
    ('Стимулятор Гармония', '-3 Стресса. -20 Пси-состояний. Действует 30 минут.', 2, 30, 0, 30, 'time_elapsed'),
])
def test_renamed_official_stress_profiles_apply_through_api_and_keep_real_advantage(
        client, pair, auth_headers, monkeypatch, name, description, stress, psy, chance, duration, tick):
    official_item, base = install_consumable(pair, name, description)
    source_profile = deepcopy(base)
    profile = deepcopy(base)
    profile['editor_version'] = 2
    created = create_profile(client, pair, auth_headers, profile, name)
    assert created.status_code == 201, created.get_json()
    local_id = created.get_json()['id']
    renamed = client.put(template_url(pair, local_id), headers=auth_headers(pair['actor_user']),
                         json={'name': 'Переименованный раствор', 'description': 'Нейтральное описание'})
    assert renamed.status_code == 200, renamed.get_json()
    item = local_item(local_id)
    item['name'] = 'Совсем другой ярлык'
    value = data(pair)
    value['inventory'] = {'pockets': [], 'backpack': [item]}
    value['health'].update(current=700, stress=5, psyState=50)
    write(pair, value)
    prepare(pair, active=True)
    identity = 'renamed-stress-profile'
    paid(client, pair, auth_headers, item, {'kind': 'self', 'actionPoints': 0}, identity, 2, general_use=True)
    assert data(pair)['health']['stress'] == 5 and data(pair)['health']['psyState'] == 50
    response = general(client, pair, auth_headers, item, identity, deferred=identity, combat=True)
    assert response.status_code == 200, response.get_json()
    health = data(pair)['health']
    assert health['stress'] == stress and health['psyState'] == psy
    resistance = [e for e in health['effects'] if e['type'] == 'stress_resistance']
    assert len(resistance) == 1
    assert resistance[0]['stress_advantage'] is True
    assert resistance[0]['stress_attack_block_chance'] == chance
    assert resistance[0]['remaining'] == duration and resistance[0]['tick'] == tick
    if tick == 'time_elapsed':
        assert resistance[0]['remaining_seconds'] == 1800
    assert pair['actor_location'].action_points_current == 3
    assert data(pair)['inventory']['backpack'][0]['uses'] == 1
    use_receipt(pair, identity)
    reject_unchanged(pair, lambda: general(client, pair, auth_headers, item, identity,
                                          deferred=identity, combat=True), 409)
    # Реальный неатакующий стресс: преимущество сохраняется, полного иммунитета нет.
    with scripted(monkeypatch, combat_module, [('apply_stress_trigger', 1, 20, 20),
                                               ('apply_stress_trigger', 1, 20, 20)]):
        adjusted = client.post(f"/lobbies/{pair['lobby'].id}/characters/{pair['actor_character'].id}/stress",
                               headers=auth_headers(pair['actor_user']), json={'amount': 1})
    assert adjusted.status_code == 200, adjusted.get_json()
    result = adjusted.get_json()['stress']
    assert result['before'] == stress and result['after'] == stress + 1
    assert result['blocked'] is False and result['manifested'] is False
    assert result['rolls'] == [20, 20]
    assert result['attack_stress_block_roll'] is None and result['attack_stress_block_chance'] == 0
    assert data(pair)['health']['stress'] == stress + 1
    assert db.session.get(LobbyItemTemplate, local_id).attributes['consumable'] == profile
    assert db.session.get(ItemTemplate, official_item['templateId']).attributes['consumable'] == source_profile
    use_receipt(pair, identity)


@contextmanager
def exposure_roll(monkeypatch, count):
    calls = []
    def draw():
        assert sys._getframe(1).f_code.co_name == 'record_exposure'
        assert sys._getframe(1).f_globals['__name__'] == addictions_module.__name__
        calls.append(0.5)
        assert len(calls) <= count
        return 0.5
    with monkeypatch.context() as controlled:
        controlled.setattr(addictions_module, 'random', SimpleNamespace(random=draw))
        yield
        assert calls == [0.5] * count


@pytest.mark.parametrize('explicit_empty', [False, True], ids=['legacy-missing', 'explicit-empty'])
def test_saved_legacy_and_explicit_empty_addiction_profiles_differ_in_real_api(
        client, pair, auth_headers, monkeypatch, explicit_empty):
    # Старый формат без editor_version действительно принимается существующим API.
    profile = {'direct': {'radiation_delta': 1}, 'effects': [], 'modifiers': []}
    if explicit_empty:
        profile['addiction'] = {}
        prepare(pair)
        advanced = {**deepcopy(profile), 'editor_version': 2}
        refused = reject_editor(pair, lambda: create_profile(
            client, pair, auth_headers, advanced, 'Пустая зависимость в точном редакторе', uses=1))
        assert 'ключ и вид' in refused.get_json()['error']['message']
    created = create_profile(client, pair, auth_headers, profile, 'Кофе', uses=1)
    assert created.status_code == 201, created.get_json()
    local_id = created.get_json()['id']
    saved = client.put(template_url(pair, local_id), headers=auth_headers(pair['actor_user']),
                       json={'attributes': {'uses': 1, 'consumable': deepcopy(profile)}})
    assert saved.status_code == 200, saved.get_json()
    item = local_item(local_id, uses=1)
    item['name'] = 'Имя экземпляра не определяет зависимость'
    value = data(pair)
    value['inventory'] = {'pockets': [], 'backpack': [item]}
    value['health'].update(current=700, radiation=0)
    write(pair, value)
    prepare(pair)
    identity = 'catalog-addiction'
    with exposure_roll(monkeypatch, 0 if explicit_empty else 1):
        response = general(client, pair, auth_headers, item, identity)
    assert response.status_code == 200, response.get_json()
    addiction = response.get_json()['consumable_result']['consumable']['addiction']
    assert addiction['applicable'] is (not explicit_empty)
    health = data(pair)['health']
    assert health['radiation'] == 1
    assert data(pair)['inventory']['backpack'] == []
    assert pair['actor_location'].action_points_current == 5
    if explicit_empty:
        assert health.get('addictions', {}).get('exposures', {}) == {}
    else:
        assert addiction['profile']['key'] == 'caffeine'
        assert addiction['chance'] == 1 and addiction['roll'] == 50
        assert addiction['acquired'] is False and addiction['dose'] == 1
        assert health['addictions']['exposures']['caffeine']['dose'] == 1
        assert health['addictions']['records'] == {}
    stored = db.session.get(LobbyItemTemplate, local_id).attributes['consumable']
    assert stored == profile and ('addiction' in stored) is explicit_empty
    use_receipt(pair, identity)
    reject_unchanged(pair, lambda: general(client, pair, auth_headers, item, identity), 409)


def composite_profile(version):
    return {'editor_version': version,
            'direct': {'action_points_cost': 1, 'stress_delta': -1},
            'modifiers': [{'stat': 'strength', 'value': 3, 'remaining': 2, 'tick': 'turn_end'}],
            'effects': [{'type': 'delayed_adjustment', 'name': 'Состав: радиация',
                         'area': 'editor-active', 'remaining': 2, 'tick': 'turn_end',
                         'adjustments': [{'field': 'radiation', 'delta': 4, 'min': 0, 'max': 100}]}]}


@pytest.mark.parametrize('version,combat_mode', [(1, True), (2, True), (1, False)],
                         ids=['simple-combat', 'advanced-combat', 'simple-idle'])
def test_composite_editor_modes_delete_nested_parameter_and_edit_actual_active_effect(
        client, pair, auth_headers, version, combat_mode):
    profile = composite_profile(version)
    created = create_profile(client, pair, auth_headers, profile, 'Состав до редактирования')
    assert created.status_code == 201, created.get_json()
    local_id = created.get_json()['id']
    edited = deepcopy(profile)
    edited['effects'][0]['adjustments'][0].pop('max')
    edited['effects'][0]['name'] = 'Новая схема'
    response = client.put(template_url(pair, local_id), headers=auth_headers(pair['actor_user']),
        json={'name': 'Состав после редактирования', 'attributes': {'uses': 2, 'consumable': edited}})
    assert response.status_code == 200, response.get_json()
    db.session.expire_all()
    assert db.session.get(LobbyItemTemplate, local_id).attributes['consumable'] == edited
    assert 'max' not in db.session.get(LobbyItemTemplate, local_id).attributes['consumable']['effects'][0]['adjustments'][0]
    item = local_item(local_id)
    value = data(pair)
    value['inventory'] = {'pockets': [], 'backpack': [item]}
    value['health'].update(current=700, stress=5, radiation=0)
    write(pair, value)
    prepare(pair, active=combat_mode)
    identity = 'composite-active'
    if combat_mode:
        paid(client, pair, auth_headers, item, {'kind': 'self', 'actionPoints': 0}, identity, 3, general_use=True)
    response = general(client, pair, auth_headers, item, identity,
                       deferred=identity if combat_mode else None, combat=combat_mode)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['consumable_result']['payment_details']['total_action_points'] == 3
    health = data(pair)['health']
    assert health['stress'] == 4 and health['radiation'] == 0
    modifiers = health['combatMeta']['consumableModifiers']
    assert len(modifiers) == 1 and modifiers[0]['stat'] == 'strength' and modifiers[0]['value'] == 3
    active = [e for e in health['effects'] if e.get('area') == 'editor-active']
    assert len(active) == 1 and active[0]['name'] == 'Новая схема' and active[0]['remaining'] == 2
    assert active[0]['adjustments'] == [{'field': 'radiation', 'delta': 4, 'min': 0}]
    assert data(pair)['inventory']['backpack'][0]['uses'] == 1
    assert pair['actor_location'].action_points_current == (2 if combat_mode else 5)
    use_receipt(pair, identity)
    reject_unchanged(pair, lambda: general(client, pair, auth_headers, item, identity,
                                          deferred=identity if combat_mode else None, combat=combat_mode), 409)
    changed_template = deepcopy(edited)
    changed_template['effects'][0]['name'] = 'Другое имя будущей дозы'
    changed_template['effects'][0]['adjustments'][0]['delta'] = 7
    response = client.put(template_url(pair, local_id), headers=auth_headers(pair['actor_user']),
        json={'attributes': {'uses': 2, 'consumable': changed_template}})
    assert response.status_code == 200, response.get_json()
    assert [e for e in data(pair)['health']['effects'] if e.get('area') == 'editor-active'] == active
    # Редактируется уже активный экземпляр штатным сохранением листа с revision.
    snapshot = pair['actor_character'].data_snapshot()
    chosen = next(e for e in snapshot['health']['effects'] if e['id'] == active[0]['id'])
    chosen['name'] = 'Изменённый активный эффект'
    chosen['remaining'] = 1
    chosen['adjustments'][0]['delta'] = 2
    saved = client.put(f"/lobbies/characters/{pair['actor_character'].id}",
                       headers=auth_headers(pair['actor_user']), json={'data': snapshot})
    assert saved.status_code == 200, saved.get_json()
    updated = [e for e in data(pair)['health']['effects'] if e.get('area') == 'editor-active']
    assert len(updated) == 1 and updated[0]['id'] == active[0]['id']
    assert updated[0]['name'] == 'Изменённый активный эффект' and updated[0]['remaining'] == 1
    assert updated[0]['adjustments'] == [{'field': 'radiation', 'delta': 2, 'min': 0}]
    if combat_mode:
        end_turn(client, pair, auth_headers, 'actor')
        assert data(pair)['health']['radiation'] == 2
        assert not [e for e in data(pair)['health']['effects'] if e.get('area') == 'editor-active']
    else:
        assert data(pair)['health']['radiation'] == 0
        assert [e for e in data(pair)['health']['effects'] if e.get('area') == 'editor-active'] == updated
        assert DeferredCombatAction.query.count() == 0
    assert data(pair)['inventory']['backpack'][0]['uses'] == 1
    assert db.session.get(LobbyItemTemplate, local_id).attributes['consumable'] == changed_template
    use_receipt(pair, identity)


@pytest.mark.parametrize('version', [1, 2], ids=['simple-zero', 'advanced-zero-diagnostic'])
def test_zero_effect_profile_create_and_update_reject_without_mutating_catalog_or_world(
        client, pair, auth_headers, version):
    prepare(pair)
    valid = composite_profile(version)
    created = create_profile(client, pair, auth_headers, valid, 'Ненулевой состав')
    assert created.status_code == 201, created.get_json()
    local_id = created.get_json()['id']
    zero = {'editor_version': version, 'direct': {'action_points_cost': 1, 'radiation_delta': 0},
            'effects': [], 'modifiers': []}
    # Норма чек-листа102 требует отказа сохранения нулевого эффекта в обоих режимах.
    # До исправления v2 принимал непустой direct из цены/нулей; ожидание не ослаблено.
    reject_editor(pair, lambda: create_profile(client, pair, auth_headers, zero, 'Нулевой состав'))
    reject_editor(pair, lambda: client.put(template_url(pair, local_id),
        headers=auth_headers(pair['actor_user']), json={'attributes': {'uses': 2, 'consumable': zero}}))
    assert db.session.get(LobbyItemTemplate, local_id).attributes['consumable'] == valid
    assert ConsumableUse.query.count() == 0


def test_renamed_blood_group_template_copy_still_tests_selected_packet_once(
        client, pair, auth_headers):
    seed, _, _ = install_item(pair, 'Набор для определения группы крови',
                              'Позволяет определить группу крови.', quantity=1, medicine=10)
    original = deepcopy(seed.attributes['consumable'])
    copied = deepcopy(original)
    copied['editor_version'] = 2
    created = create_profile(client, pair, auth_headers, copied, 'Копия определения группы', uses=2)
    assert created.status_code == 201, created.get_json()
    local_id = created.get_json()['id']
    renamed = client.put(template_url(pair, local_id), headers=auth_headers(pair['actor_user']),
                         json={'name': 'Определитель №2', 'description': 'Новое описание'})
    assert renamed.status_code == 200, renamed.get_json()
    blood_profile = parse_consumable_effects('Пакет крови. Восстанавливает стадию кровопотери.')
    blood = ItemTemplate(name='Пакет крови', category='consumable', attributes={'consumable': blood_profile})
    db.session.add(blood)
    db.session.flush()
    packet_copy = deepcopy(blood_profile)
    packet_copy['editor_version'] = 2
    cloned = create_profile(client, pair, auth_headers, packet_copy, 'Копия пакета крови', uses=1)
    assert cloned.status_code == 201, cloned.get_json()
    packet_local_id = cloned.get_json()['id']
    renamed_packet = client.put(template_url(pair, packet_local_id), headers=auth_headers(pair['actor_user']),
                                json={'name': 'Переименованный пакет', 'description': 'Нейтральная этикетка'})
    assert renamed_packet.status_code == 200, renamed_packet.get_json()
    item = local_item(local_id)
    packet = {'id': 'catalog-unknown-packet', 'templateId': packet_local_id + 1_000_000, 'name': 'Неизвестный пакет',
              'category': 'consumable', 'quantity': 1,
              'attributes': {'bloodType': 3, 'bloodTypeKnown': False}}
    value = data(pair)
    value['inventory'] = {'pockets': [], 'backpack': [item, packet]}
    write(pair, value)
    prepare(pair)
    application = {'kind': 'blood_type_test', 'target': 'packet', 'actionPoints': 0,
                   'entry': {'path': ['inventory', 'backpack', 1], 'item': deepcopy(packet)}}
    body = medical_body(pair, item, application, 'renamed-blood-test')
    response = medical(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['roll'] is None and result['outcome']['kind'] == 'blood_type_test'
    assert result['outcome']['blood_type'] == 3 and result['outcome']['target'] == 'packet'
    assert result['payment_details']['total_action_points'] == 2
    stored = data(pair)['inventory']['backpack']
    assert len(stored) == 2 and stored[0]['uses'] == 1
    assert stored[1] == {**packet, 'attributes': {**packet['attributes'], 'bloodTypeKnown': True}}
    assert pair['actor_location'].action_points_current == 5
    assert db.session.get(ItemTemplate, seed.id).attributes['consumable'] == original
    assert db.session.get(LobbyItemTemplate, local_id).attributes['consumable'] == copied
    assert db.session.get(LobbyItemTemplate, packet_local_id).attributes['consumable'] == packet_copy
    assert db.session.get(ItemTemplate, blood.id).attributes['consumable'] == blood_profile
    use_receipt(pair, 'renamed-blood-test')
    reject_unchanged(pair, lambda: medical(client, pair, auth_headers, body), 409)


@pytest.mark.parametrize('mode,selected_id,uses', [(0, 'selected-light', 1), (1, 'selected-medium', 2)])
def test_saved_advanced_medical_applications_treat_only_selected_injury_with_real_payment(
        client, pair, auth_headers, monkeypatch, mode, selected_id, uses):
    effects = [{'id': 'selected-light', 'type': 'bleeding_external_light', 'area': 'leftArm'},
               {'id': 'selected-medium', 'type': 'bleeding_external_medium', 'area': 'rightArm'},
               {'id': 'untouched-fracture', 'type': 'fracture', 'area': 'rightLeg'}]
    seed, profile, _ = install_item(pair, 'Бинт стерильный', 'Останавливает кровотечение.',
                                    quantity=1, medicine=10, effects=effects)
    original_profile = deepcopy(profile)
    profile = deepcopy(profile)
    profile['editor_version'] = 2
    created = create_profile(client, pair, auth_headers, profile, 'Два выбранных режима', uses=4)
    assert created.status_code == 201, created.get_json()
    local_id = created.get_json()['id']
    item = local_item(local_id, uses=4)
    value = data(pair)
    value['inventory'] = {'pockets': [], 'backpack': [item]}
    value['health']['current'] = 700
    write(pair, value)
    prepare(pair, active=True, ap=8)
    before = data(pair)
    chosen = next(e for e in before['health']['effects'] if e.get('id') == selected_id)
    canonical = deepcopy(profile['direct']['applications'][mode])
    assert canonical.get('item_uses', 1) == uses and canonical['treated'] is True
    application = {'kind': 'bleeding', 'effect': deepcopy(chosen), 'application': canonical, 'actionPoints': 0}
    identity = 'selected-medical-mode'
    with scripted(monkeypatch, deferred_module, [('prepare_medicine_roll', 1, 20, 20)]):
        paid(client, pair, auth_headers, item, application, identity, 6)
    assert data(pair)['inventory'] == before['inventory']
    body = medical_body(pair, item, application, identity, combat=True)
    forged = deepcopy(body)
    forged['application']['application']['medicine_bonus'] = 99
    reject_unchanged(pair, lambda: medical(client, pair, auth_headers, forged), 409)
    response = medical(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['success'] is True and result['roll'] == 20 and result['difficulty'] == 8
    assert result['payment_details']['total_action_points'] == 6
    after = data(pair)
    assert not any(e.get('id') == selected_id for e in after['health']['effects'])
    untouched_id = 'selected-medium' if mode == 0 else 'selected-light'
    assert next(e for e in after['health']['effects'] if e.get('id') == untouched_id) == next(
        e for e in before['health']['effects'] if e.get('id') == untouched_id)
    assert any(e.get('id') == 'untouched-fracture' for e in after['health']['effects'])
    assert not any(e['type'] == 'untreated_wound' for e in after['health']['effects'])
    expected = deepcopy(before['inventory'])
    expected['backpack'][0].update(uses=4 - uses, maxUses=4, quantity=1)
    expected['backpack'][0].setdefault('attributes', {})['uses_remaining'] = 4 - uses
    assert after['inventory'] == expected
    assert pair['actor_location'].action_points_current == 2
    assert db.session.get(LobbyItemTemplate, local_id).attributes['consumable'] == profile
    assert db.session.get(ItemTemplate, seed.id).attributes['consumable'] == original_profile
    use_receipt(pair, identity)
    reject_unchanged(pair, lambda: medical(client, pair, auth_headers, body), 409)
