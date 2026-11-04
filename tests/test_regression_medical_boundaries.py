"""M+: адресные медицинские границы. Подготовлено, все случаи NOT_RUN."""
from copy import deepcopy
from helpers.journal_assertions import assert_same_game_state
import sys
from types import SimpleNamespace

import pytest

import app.services.medical_procedure as medical_module
import app.services.deferred_consumable as deferred_module
import app.services.combat as combat_module
from app.extensions import db
from app.models import (CharacterInteractionRequest, ConsumableUse, DeferredCombatAction,
                        LobbyCharacter)
from app.services.effects import advance_timed_effects
from test_consumable_use import pair
from test_medical_procedure import install_item, selected_bleeding, payload, apply


SHOV = ('Гемостатик "Шов"', 'Ампула. Останавливает Сильное внутреннее кровотечение. '
        'Не пригодно для внешних кровотечений. Бонус медикамента -8.')
KITS = {
    'surgeon': ('Хирургический набор «Хирург»',
                'Время использования - 10 ОД. Восстанавливает часть тела. Она имеет 30 здоровья. 5 использований.'),
    'full': ('Набор полного восстановления конечности',
             'Время использования - 15 ОД. Восстанавливает утерянный орган или искореженную конечность. '
             'Бонус медикамента +3. 5 использований.'),
}


def saved(pair, who='actor'):
    db.session.expire_all()
    return deepcopy(db.session.get(LobbyCharacter, pair[who + '_character'].id).data)


def store_data(pair, data, who='actor'):
    pair[who + '_character'].data = deepcopy(data)
    db.session.commit()


def one_roll(monkeypatch, value):
    draws = []

    def draw(low, high):
        assert (low, high) == (1, 20)
        assert draws == []
        draws.append(value)
        return value

    rng = SimpleNamespace(randint=draw)
    monkeypatch.setattr(medical_module, 'random', rng)
    monkeypatch.setattr(deferred_module, 'random', rng)
    return draws


def no_roll(monkeypatch):
    def forbidden(*args):
        raise AssertionError('Процедура не должна повторно или преждевременно бросать d20')
    rng = SimpleNamespace(randint=forbidden)
    monkeypatch.setattr(medical_module, 'random', rng)
    monkeypatch.setattr(deferred_module, 'random', rng)


def assert_receipt(pair, operation, target=None):
    db.session.expire_all()
    row = db.session.get(ConsumableUse, operation)
    assert row is not None
    assert row.actor_character_id == pair['actor_character'].id
    assert row.target_character_id == (target or pair['actor_character']).id
    assert ConsumableUse.query.count() == 1


def assert_rejected_unchanged(client, pair, auth_headers, body, monkeypatch, status=400):
    before = saved(pair)
    ap = pair['actor_location'].action_points_current
    no_roll(monkeypatch)
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == status, response.get_json()
    assert_same_game_state(saved(pair), before)
    assert pair['actor_location'].action_points_current == ap
    assert ConsumableUse.query.count() == 0


def combat_url(pair):
    return f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat"


def pay_procedure(client, pair, auth_headers, item, application, operation, *, cost=6, target=None):
    selection = {'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
                 'application': deepcopy(application),
                 'target_character_id': (target or pair['actor_character']).id,
                 'treatment_request_id': None, 'server_authoritative': True}
    return client.post(combat_url(pair) + '/spend', headers=auth_headers(pair['actor_user']), json={
        'location_character_id': pair['actor_location'].id, 'action_points': cost,
        'allow_deferred': True, 'pending_action_id': operation,
        'pending_action_label': 'Медицинская проверка M+', 'consumable_selection': selection,
    })


def test_unconscious_patient_needs_no_consent_but_healer_cannot_edit_sheet(client, pair, auth_headers, monkeypatch):
    bleed = {'id': 'm-bleed', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', medicine=20)
    # Врач — обычный владелец своего персонажа, а не GM пациента.
    pair['lobby'].gm_id = pair['target_user']['id']
    patient = deepcopy(pair['target_character'].data)
    patient['health']['effects'] = [bleed, {'id': 'm-unconscious', 'type': 'unconsciousness', 'active': True}]
    pair['target_character'].data = patient
    pair['target_character'].editable_to = []
    pair['target_character'].visible_to = []
    db.session.commit()
    target = pair['target_character']
    denied = client.put(f'/lobbies/characters/{target.id}', headers=auth_headers(pair['actor_user']),
                        json={'name': 'Запрещённое редактирование'})
    assert denied.status_code == 403
    draws = one_roll(monkeypatch, 20)
    application = selected_bleeding(profile, bleed)
    paid = pay_procedure(client, pair, auth_headers, item, application, 'm-unconscious', cost=5, target=target)
    assert paid.status_code == 200, paid.get_json()
    row = db.session.get(DeferredCombatAction, 'm-unconscious')
    assert row.status == 'ready'
    assert row.payload['medicine_roll'] == 20
    assert draws == [20]
    assert pair['actor_location'].action_points_current == 0
    no_roll(monkeypatch)
    body = payload(pair, item, application, operation='m-unconscious', target=target, combat=True)
    body['deferred_action_id'] = 'm-unconscious'
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    health = saved(pair, 'target')['health']
    assert not any(e['type'].startswith('bleeding_') for e in health['effects'])
    assert any(e['type'] == 'untreated_wound' and e['area'] == 'leftArm' for e in health['effects'])
    assert any(e['id'] == 'm-unconscious' for e in health['effects'])
    assert saved(pair)['inventory']['backpack'][0]['quantity'] == 1
    assert CharacterInteractionRequest.query.count() == 0
    assert_receipt(pair, 'm-unconscious', target)
    assert db.session.get(DeferredCombatAction, 'm-unconscious').status == 'completed'
    denied = client.put(f'/lobbies/characters/{target.id}', headers=auth_headers(pair['actor_user']),
                        json={'name': 'Всё ещё запрещено'})
    assert denied.status_code == 403
    assert target.editable_to == []
    assert apply(client, pair, auth_headers, body).status_code == 409
    assert saved(pair)['inventory']['backpack'][0]['quantity'] == 1
    assert_receipt(pair, 'm-unconscious', target)


def test_medium_bleeding_with_one_bandage_rejects_before_rng_without_receipt(client, pair, auth_headers, monkeypatch):
    effect = {'id': 'm-medium', 'type': 'bleeding_external_medium', 'area': 'rightLeg', 'source': 'shot'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.',
                                     quantity=1, medicine=10, effects=[effect])
    before = saved(pair)
    draws = []

    def unexpected_draw(low, high):
        # Счётчик позволяет проверить rollback ресурсов, затем провалить RNG-условие.
        assert (low, high) == (1, 20)
        draws.append(20)
        return 20

    monkeypatch.setattr(medical_module.random, 'randint', unexpected_draw)
    body = payload(pair, item, selected_bleeding(profile, effect, 1), operation='m-insufficient')
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == 400, response.get_json()
    assert_same_game_state(saved(pair), before)
    assert pair['actor_location'].action_points_current == 5
    assert ConsumableUse.query.count() == 0
    assert draws == []
    # Недостаток зарядов не должен начинать оплату и сохранять медицинский кубик.
    paid = pay_procedure(client, pair, auth_headers, item, selected_bleeding(profile, effect, 1),
                         'm-insufficient-paid', cost=6)
    assert paid.status_code == 400, paid.get_json()
    assert_same_game_state(saved(pair), before)
    assert pair['actor_location'].action_points_current == 5
    assert db.session.get(DeferredCombatAction, 'm-insufficient-paid') is None
    assert ConsumableUse.query.count() == 0
    assert draws == []


@pytest.mark.parametrize('stage,mode,result_stage', [('light', 'close', None), ('medium', 'close', None),
                                                     ('severe', 'close', None), ('extreme', 'weaken', 'severe')])
def test_shov_internal_boundaries_use_saved_profile_and_exact_dc(client, pair, auth_headers, monkeypatch, stage, mode, result_stage):
    effect = {'id': 'm-shov', 'type': 'bleeding_internal_' + stage, 'area': 'chest', 'source': 'injury'}
    template, profile, item = install_item(pair, *SHOV, medicine=10, effects=[effect])
    assert db.session.get(type(template), template.id).attributes['consumable'] == profile
    draws = one_roll(monkeypatch, 12)
    operation = 'm-shov-' + stage
    response = apply(client, pair, auth_headers, payload(pair, item, selected_bleeding(profile, effect), operation=operation))
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert (result['base_difficulty'], result['medicine_bonus'], result['medication_bonus'], result['difficulty']) == (4, 0, -8, 12)
    assert result['roll'] == 12 and result['success'] is True
    assert result['outcome']['mode'] == mode
    assert result['outcome']['result_stage'] == result_stage
    health = saved(pair)['health']
    bleeding = [e['type'] for e in health['effects'] if e['type'].startswith('bleeding_')]
    assert bleeding == (['bleeding_internal_severe'] if stage == 'extreme' else [])
    assert saved(pair)['inventory']['backpack'][0]['quantity'] == 1
    assert draws == [12]
    assert_receipt(pair, operation)


@pytest.mark.parametrize('effect_type', ['bleeding_external_light', 'bleeding_external_severe',
                                         'bleeding_external_extreme', 'bleeding_internal_catastrophic'])
def test_shov_rejects_external_and_unknown_stage_without_rng(client, pair, auth_headers, monkeypatch, effect_type):
    effect = {'id': 'm-invalid', 'type': effect_type, 'area': 'chest', 'source': 'injury'}
    _, profile, item = install_item(pair, *SHOV, medicine=10, effects=[effect])
    body = payload(pair, item, selected_bleeding(profile, effect), operation='m-invalid')
    assert_rejected_unchanged(client, pair, auth_headers, body, monkeypatch)


@pytest.mark.parametrize('deferred', [False, True], ids=['instant', 'paid-deferred'])
def test_same_medicine_has_exact_dc_and_paid_completion_never_rerolls(client, pair, auth_headers, monkeypatch, deferred):
    effect = {'id': 'm-dc', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', medicine=10, effects=[effect])
    application = selected_bleeding(profile, effect)
    operation = 'm-dc'
    draws = one_roll(monkeypatch, 8)
    body = payload(pair, item, application, operation=operation, combat=deferred)
    if deferred:
        bleeding_draws = []

        def bleeding_draw(low, high):
            assert sys._getframe(1).f_code is combat_module.CombatService._resolve_bleeding_check.__code__
            assert (low, high) == (1, 20)
            assert bleeding_draws == []
            bleeding_draws.append(20)
            return 20

        monkeypatch.setattr(combat_module, 'random', SimpleNamespace(randint=bleeding_draw))
        pair['actor_location'].action_points_current = 2
        db.session.commit()
        started = pay_procedure(client, pair, auth_headers, item, application, operation)
        assert started.status_code == 200, started.get_json()
        row = db.session.get(DeferredCombatAction, operation)
        assert row.status == 'paying' and row.remaining_action_points == 4
        assert 'medicine_roll' not in row.payload
        assert draws == []
        assert bleeding_draws == [20]
        assert saved(pair)['health']['combatMeta']['bleedingCheck']['roll'] == 20
        ended = client.post(combat_url(pair) + '/end_turn', headers=auth_headers(pair['target_user']),
                            json={'location_character_id': pair['target_location'].id})
        assert ended.status_code == 200, ended.get_json()
        db.session.expire_all()
        row = db.session.get(DeferredCombatAction, operation)
        assert row.status == 'ready' and row.remaining_action_points == 0
        assert row.payload['medicine_roll'] == 8
        assert draws == [8]
        assert bleeding_draws == [20]
        assert pair['actor_location'].action_points_current == 1
        no_roll(monkeypatch)
        body['deferred_action_id'] = operation
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert (result['base_difficulty'], result['medicine_bonus'], result['medication_bonus'], result['difficulty']) == (8, 0, 0, 8)
    assert result['roll'] == 8 and result['success'] is True
    assert draws == [8]
    data = saved(pair)
    assert not any(e.get('id') == 'm-dc' for e in data['health']['effects'])
    assert data['inventory']['backpack'][0]['quantity'] == 1
    assert_receipt(pair, operation)
    if deferred:
        assert db.session.get(DeferredCombatAction, operation).status == 'completed'
        before = saved(pair)
        assert apply(client, pair, auth_headers, body).status_code == 409
        assert_same_game_state(saved(pair), before)
        assert pair['actor_location'].action_points_current == 1
        assert_receipt(pair, operation)


def test_chimera_with_fracture_cures_only_at_sixty_seconds(client, pair, auth_headers, monkeypatch):
    fracture = {'id': 'm-fracture', 'type': 'fracture', 'area': 'leftArm', 'source': 'fall'}
    other = {'id': 'm-other', 'type': 'fracture', 'area': 'rightLeg', 'source': 'other'}
    _, _, item = install_item(pair, '«Химера»', 'Ампула. Излечивает перелом. Срабатывает через 1 минуту.',
                                quantity=1, medicine=10, effects=[fracture, other])
    draws = one_roll(monkeypatch, 20)
    application = {'kind': 'injury', 'effect': {'type': 'body_zone', 'area': 'leftArm'},
                   'treatmentMode': 'chimera', 'actionPoints': 1}
    response = apply(client, pair, auth_headers, payload(pair, item, application, operation='m-chimera'))
    assert response.status_code == 200, response.get_json()
    before = saved(pair)
    assert before['inventory']['backpack'] == []
    assert any(e.get('id') == 'm-fracture' for e in before['health']['effects'])
    delayed = next(e for e in before['health']['effects'] if e['type'] == 'delayed_limb_treatment')
    assert delayed['remaining_seconds'] == 60 and delayed['cure_fracture'] is True
    for elapsed, cured in [(59, False), (1, True)]:
        data = saved(pair)
        health = data['health']
        health['effects'] = advance_timed_effects(health, health['effects'], elapsed)
        store_data(pair, data)
        health = saved(pair)['health']
        assert any(e.get('id') == 'm-fracture' for e in health['effects']) is not cured
        assert any(e.get('id') == 'm-other' for e in health['effects'])
        assert health['zones']['leftArm']['current'] == before['health']['zones']['leftArm']['current']
        if not cured:
            assert next(e for e in health['effects'] if e['type'] == 'delayed_limb_treatment')['remaining_seconds'] == 1
        else:
            assert not any(e['type'] == 'delayed_limb_treatment' for e in health['effects'])
    assert draws == [20]
    assert_receipt(pair, 'm-chimera')


def surgery_application(effect, mode, uses, cost):
    return {'kind': 'injury', 'effect': deepcopy(effect), 'treatmentMode': mode,
            'application': {'item_uses': uses}, 'actionPoints': cost}


@pytest.mark.parametrize('area,uses', [('nose', 3), ('leftEye', 5)])
def test_full_kit_restores_lost_organ_inside_window_with_three_or_five_uses(client, pair, auth_headers, monkeypatch, area, uses):
    organ = {'id': 'm-organ', 'type': 'organ_loss', 'area': area, 'source': 'damage',
             'treatment_window_seconds': 1, 'treatment_window_expired': False}
    _, _, item = install_item(pair, *KITS['full'], quantity=1, medicine=10, effects=[organ])
    data = saved(pair)
    data['health']['organs'][area]['current'] = 0
    store_data(pair, data)
    draws = one_roll(monkeypatch, 20)
    application = surgery_application(organ, 'restore_lost_part', uses, 15)
    response = apply(client, pair, auth_headers, payload(pair, item, application, operation='m-organ'))
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['success'] is True and result['outcome']['uses'] == uses
    assert result['difficulty'] == 11
    data = saved(pair)
    assert not any(e.get('id') == 'm-organ' for e in data['health']['effects'])
    if uses == 5:
        assert data['inventory']['backpack'] == []
    else:
        assert data['inventory']['backpack'][0]['uses'] == 2
    assert draws == [20]
    assert_receipt(pair, 'm-organ')
    # Восстановление должно дать работающий орган, а не только убрать маркер утраты.
    assert data['health']['organs'][area]['current'] > 0


@pytest.mark.parametrize('kit,expected', [('surgeon', 30), ('full', 50)])
def test_ordinary_zero_limb_uses_one_charge_without_catastrophic_surgery(client, pair, auth_headers, monkeypatch, kit, expected):
    _, _, item = install_item(pair, *KITS[kit], quantity=1, medicine=10,
                              zones={'leftArm': {'current': 0, 'max': 50}})
    data = saved(pair)
    data['health']['current'] = 100
    data['health']['max'] = 700
    store_data(pair, data)
    draws = one_roll(monkeypatch, 20)
    selected = {'type': 'damaged_zone', 'area': 'leftArm'}
    application = surgery_application(selected, 'restore_disabled_limb', 1, 10 if kit == 'surgeon' else 15)
    response = apply(client, pair, auth_headers, payload(pair, item, application, operation='m-zero'))
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['success'] is True and result['outcome']['uses'] == 1
    data = saved(pair)
    assert data['health']['zones']['leftArm']['current'] == expected
    assert data['health']['current'] == 100 + expected
    assert data['inventory']['backpack'][0]['uses'] == 4
    assert draws == [20]
    assert_receipt(pair, 'm-zero')


@pytest.mark.parametrize('kit', ['surgeon', 'full'])
def test_ordinary_zero_limb_cannot_be_forged_into_amputation(client, pair, auth_headers, monkeypatch, kit):
    _, _, item = install_item(pair, *KITS[kit], quantity=1, medicine=10,
                              zones={'leftArm': {'current': 0, 'max': 50}})
    forged = {'id': 'not-a-real-injury', 'type': 'amputation', 'area': 'leftArm'}
    application = surgery_application(forged, 'restore_lost_part', 5, 15)
    body = payload(pair, item, application, operation='m-forgery')
    assert_rejected_unchanged(client, pair, auth_headers, body, monkeypatch, status=409)


def test_surgeon_real_unfixed_fracture_branch_consumes_three_of_five_uses(client, pair, auth_headers, monkeypatch):
    fracture = {'id': 'm-unfixed', 'type': 'fracture_unfixed', 'area': 'leftArm', 'source': 'injury'}
    _, _, item = install_item(pair, *KITS['surgeon'], quantity=1, medicine=10, effects=[fracture])
    draws = one_roll(monkeypatch, 20)
    application = surgery_application(fracture, 'treat_unfixed_fracture', 3, 10)
    response = apply(client, pair, auth_headers, payload(pair, item, application, operation='m-three'))
    assert response.status_code == 200, response.get_json()
    result = response.get_json()['medical_result']
    assert result['success'] is True and result['outcome']['uses'] == 3
    assert result['difficulty'] == 14
    data = saved(pair)
    assert data['inventory']['backpack'][0]['uses'] == 2
    delayed = next(e for e in data['health']['effects'] if e['type'] == 'delayed_limb_treatment')
    assert delayed['area'] == 'leftArm' and delayed['remaining_seconds'] == 259200
    assert delayed['cure_fracture'] is True
    assert draws == [20]
    assert_receipt(pair, 'm-three')
