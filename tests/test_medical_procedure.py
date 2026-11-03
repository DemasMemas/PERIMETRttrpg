from copy import deepcopy

import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import ConsumableUse, DeferredCombatAction, LocationCombatState
from app.models.templates import ItemTemplate
from app.models.lobby_templates import LobbyItemTemplate
from app.services.consumable_effects import parse_consumable_effects
from app.services.effects import advance_timed_effects
from app.services.medical_procedure import (
    _blood_packet_from_template, _blood_packet_proof, _is_blood_packet, _verified_blood_donor,
)
from test_consumable_use import pair
from test_atomic_inventory import treatment_payload


def install_item(pair, name, description, *, quantity=2, medicine=20, effects=None, zones=None, pain=0):
    profile = parse_consumable_effects(f'{name}. {description}')
    template = ItemTemplate(
        name=name, category='consumable', description=description,
        attributes={'consumable': profile, 'uses': profile['direct'].get('uses')},
    )
    db.session.add(template)
    db.session.flush()
    item = {
        'id': f'item-{template.id}', 'templateId': template.id, 'name': name,
        'category': 'consumable', 'quantity': quantity,
    }
    data = deepcopy(pair['actor_character'].data)
    data['inventory']['backpack'] = [item]
    data.setdefault('skills', {}).setdefault('other', {})['medicine'] = {'base': medicine, 'bonus': 0}
    health = data.setdefault('health', {})
    health['effects'] = deepcopy(effects or [])
    health['zones'] = deepcopy(zones or {
        'leftArm': {'current': 50, 'max': 50}, 'rightArm': {'current': 50, 'max': 50},
        'leftLeg': {'current': 50, 'max': 50}, 'rightLeg': {'current': 50, 'max': 50},
    })
    health['painLevel'] = pain
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    return template, profile, item


def selected_bleeding(profile, effect, index=0):
    application = deepcopy(profile['direct']['applications'][index])
    return {'kind': 'bleeding', 'effect': deepcopy(effect), 'application': application,
            'actionPoints': application.get('action_points', 1)}


def payload(pair, item, application, *, operation='medical-1', target=None, combat=False, consent=None):
    return {
        'operation_id': operation,
        'target_character_id': (target or pair['actor_character']).id,
        'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
        'application': application,
        'combat_location_id': pair['location'].id if combat else None,
        'actor_location_character_id': pair['actor_location'].id if combat else None,
        'interaction_request_id': consent.id if consent else None,
    }


def apply(client, pair, auth_headers, body, user=None):
    return client.post(
        f"/lobbies/characters/{pair['actor_character'].id}/medical-procedure",
        headers=auth_headers(user or pair['actor_user']), json=body,
    )


def test_bandage_closes_light_bleeding_and_creates_untreated_wound(client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[effect])
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    response = apply(client, pair, auth_headers, payload(pair, item, selected_bleeding(profile, effect)))
    assert response.status_code == 200, response.json
    result = response.json['medical_result']
    assert result['success'] is True
    assert result['roll'] == 20
    effects = response.json['actor_data']['health']['effects']
    assert not any(entry['type'].startswith('bleeding_') for entry in effects)
    assert any(entry['type'] == 'untreated_wound' and entry['area'] == 'leftArm' for entry in effects)
    assert response.json['actor_data']['inventory']['backpack'][0]['quantity'] == 1
    assert ConsumableUse.query.count() == 1


def test_local_clone_of_bandage_can_treat_without_matching_name(client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-local', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[effect])
    cloned = deepcopy(profile)
    cloned['editor_version'] = 2
    cloned['effects'] = [entry for entry in cloned['effects'] if entry.get('source') != 'direct']
    local = LobbyItemTemplate(
        lobby_id=pair['lobby'].id, created_by=pair['actor_user']['id'],
        name='Новая повязка', category='consumable',
        attributes={'uses': 1, 'consumable': cloned},
    )
    db.session.add(local)
    db.session.flush()
    item['templateId'] = local.id + 1_000_000
    item['name'] = 'Просто ткань'
    data = deepcopy(pair['actor_character'].data)
    data['inventory']['backpack'] = [item]
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    response = apply(client, pair, auth_headers, payload(pair, item, selected_bleeding(profile, effect)))
    assert response.status_code == 200, response.json
    assert response.json['medical_result']['success'] is True
    assert not any(entry['type'].startswith('bleeding_') for entry in response.json['actor_data']['health']['effects'])


def test_stronger_bleeding_is_weakened_one_stage(client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-1', 'type': 'bleeding_internal_extreme', 'area': 'chest', 'source': 'organ'}
    _, profile, item = install_item(
        pair, 'Гемостатик "Шов"',
        'Ампула. Останавливает Сильное внутреннее кровотечение. Не пригодно для внешних кровотечений. Бонус медикамента -8.',
        effects=[effect], medicine=20,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    response = apply(client, pair, auth_headers, payload(pair, item, selected_bleeding(profile, effect)))
    assert response.status_code == 200, response.json
    assert response.json['medical_result']['outcome']['mode'] == 'weaken'
    assert response.json['medical_result']['outcome']['result_stage'] == 'severe'
    assert response.json['actor_data']['health']['effects'][0]['type'] == 'bleeding_internal_severe'


def test_external_hemostatic_cannot_treat_internal_bleeding(client, pair, auth_headers):
    effect = {'id': 'bleed-1', 'type': 'bleeding_internal_light', 'area': 'chest', 'source': 'organ'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[effect])
    response = apply(client, pair, auth_headers, payload(pair, item, selected_bleeding(profile, effect)))
    assert response.status_code == 400
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2
    assert ConsumableUse.query.count() == 0


def test_medium_bleeding_uses_two_bandages(client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_medium', 'area': 'rightLeg', 'source': 'shot'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[effect])
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    response = apply(client, pair, auth_headers, payload(pair, item, selected_bleeding(profile, effect, 1)))
    assert response.status_code == 200, response.json
    assert response.json['actor_data']['inventory']['backpack'] == []


def test_wound_treatment_removes_selected_wound_and_applies_pain(client, pair, auth_headers, monkeypatch):
    selected = {'id': 'wound-1', 'type': 'untreated_wound', 'area': 'leftArm', 'source': 'cut'}
    untouched = {'id': 'wound-2', 'type': 'untreated_wound', 'area': 'rightLeg', 'source': 'shot'}
    _, _, item = install_item(
        pair, 'Спирт', 'Спирт. Обрабатывает раны. 10 использований.',
        effects=[selected, untouched], pain=2,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 1)
    application = {
        'kind': 'wound', 'effect': deepcopy(selected), 'actionPoints': 1,
    }
    response = apply(client, pair, auth_headers, payload(pair, item, application))
    assert response.status_code == 200, response.json
    assert response.json['medical_result']['success'] is True
    assert response.json['medical_result']['roll'] is None
    health = response.json['actor_data']['health']
    assert health['painLevel'] == 5
    assert not any(entry.get('id') == 'wound-1' for entry in health['effects'])
    assert any(entry.get('id') == 'wound-2' for entry in health['effects'])
    inventory_item = response.json['actor_data']['inventory']['backpack'][0]
    assert inventory_item['quantity'] == 2
    assert inventory_item['uses'] == 9


def test_viking_is_server_applied_to_every_limb_after_medicine_check(
        client, pair, auth_headers, monkeypatch):
    fracture = {'id': 'fracture-1', 'type': 'fracture', 'area': 'leftArm', 'source': 'shot'}
    zones = {
        'leftArm': {'current': 0, 'max': 50}, 'rightArm': {'current': 20, 'max': 50},
        'leftLeg': {'current': 50, 'max': 50}, 'rightLeg': {'current': 50, 'max': 50},
    }
    _, _, item = install_item(
        pair, 'Стимулятор Викинг',
        'Ампула. Действует 10 минут.',
        quantity=1, medicine=20, effects=[fracture], zones=zones, pain=7,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)

    response = apply(client, pair, auth_headers, payload(
        pair, item, {'kind': 'self', 'actionPoints': 1}, operation='viking-server',
    ))

    assert response.status_code == 200, response.json
    result = response.json['medical_result']
    assert result['success'] is True
    assert result['outcome']['kind'] == 'self'
    assert set(result['outcome']['temporary_limbs']) == {
        'leftArm', 'rightArm', 'leftLeg', 'rightLeg',
    }
    data = response.json['actor_data']
    assert data['health']['painLevel'] == 2
    assert data['health']['zones']['leftArm']['current'] == 1
    limb_effects = [
        effect for effect in data['health']['effects']
        if effect['type'] == 'temporary_limb_restoration'
    ]
    assert len(limb_effects) == 4
    assert all(effect['suppress_fracture'] is True for effect in limb_effects)
    assert all(effect['minimum_limb_health'] == 1 for effect in limb_effects)
    assert data['inventory']['backpack'] == []


def test_second_viking_ampoule_refreshes_limb_effect_without_losing_baseline(
        client, pair, auth_headers, monkeypatch):
    zones = {
        'leftArm': {'current': 0, 'max': 50}, 'rightArm': {'current': 20, 'max': 50},
        'leftLeg': {'current': 50, 'max': 50}, 'rightLeg': {'current': 50, 'max': 50},
    }
    _, _, item = install_item(
        pair, 'Стимулятор Викинг', 'Ампула. Действует 10 минут.',
        quantity=1, medicine=20, zones=zones, pain=10,
    )
    first_item = {**item, 'id': 'viking-dose-a'}
    second_item = {**item, 'id': 'viking-dose-b'}
    data = deepcopy(pair['actor_character'].data)
    data['inventory']['backpack'] = [first_item, second_item]
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)

    first = apply(client, pair, auth_headers, payload(
        pair, first_item, {'kind': 'self', 'actionPoints': 1}, operation='viking-dose-a',
    ))
    assert first.status_code == 200, first.json

    data = deepcopy(pair['actor_character'].data)
    for effect in data['health']['effects']:
        if effect.get('type') == 'temporary_limb_restoration':
            effect['remaining'] = 1
            effect['remaining_seconds'] = 60
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()

    second = apply(client, pair, auth_headers, payload(
        pair, second_item, {'kind': 'self', 'actionPoints': 1}, operation='viking-dose-b',
    ))

    assert second.status_code == 200, second.json
    health = second.json['actor_data']['health']
    limb_effects = [
        effect for effect in health['effects']
        if effect['type'] == 'temporary_limb_restoration'
    ]
    assert len(limb_effects) == 4
    assert all(effect['remaining'] == 10 for effect in limb_effects)
    assert all(effect['remaining_seconds'] == 600 for effect in limb_effects)
    restored_arm = next(effect for effect in limb_effects if effect['area'] == 'leftArm')
    assert restored_arm['previous_health'] == 0
    assert restored_arm['restore_on_expire'] is True


def test_cotton_closes_all_bleeding_but_preserves_other_injuries(
        client, pair, auth_headers, monkeypatch):
    effects = [
        {'id': 'external', 'type': 'bleeding_external_light', 'area': 'leftArm'},
        {'id': 'internal', 'type': 'bleeding_internal_extreme', 'area': 'chest'},
        {'id': 'fracture', 'type': 'fracture', 'area': 'rightLeg'},
    ]
    _, _, item = install_item(
        pair, 'Кровоостанавливающее «Хлопок»',
        'Ампула. Останавливает все кровотечения.',
        quantity=1, medicine=20, effects=effects,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)

    response = apply(client, pair, auth_headers, payload(
        pair, item, {'kind': 'self', 'actionPoints': 1}, operation='cotton-server',
    ))

    assert response.status_code == 200, response.json
    result = response.json['medical_result']
    assert result['outcome']['closed_bleeding'] == 2
    health = response.json['actor_data']['health']
    assert not any(
        effect['type'].startswith(('bleeding_external_', 'bleeding_internal_'))
        for effect in health['effects']
    )
    assert any(effect.get('id') == 'fracture' for effect in health['effects'])
    assert any(effect['type'] == 'bleeding_prevention' for effect in health['effects'])
    assert health['exhaustion'] == 1


def test_blood_packet_uses_server_checked_dropper_and_compatible_blood_type(
        client, pair, auth_headers, monkeypatch):
    _, _, item = install_item(
        pair, 'Пакет крови', 'Восстанавливает стадию кровопотери.',
        quantity=1, medicine=20,
    )
    data = deepcopy(pair['actor_character'].data)
    data['inventory']['backpack'][0]['attributes'] = {'bloodType': 1, 'bloodTypeKnown': True}
    data['inventory']['backpack'].append({
        'id': 'dropper', 'name': 'Капельница', 'category': 'consumable', 'quantity': 1,
    })
    data['health'].setdefault('combatMeta', {}).update(bloodType=2,bloodTypeKnown=True)
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    item = deepcopy(data['inventory']['backpack'][0])
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)

    response = apply(client, pair, auth_headers, payload(
        pair, item, {'kind': 'self', 'actionPoints': 0}, operation='blood-transfusion',
    ))

    assert response.status_code == 200, response.json
    outcome = response.json['medical_result']['outcome']
    assert outcome['infusion_bonus'] == 1
    recovery = next(
        effect for effect in response.json['actor_data']['health']['effects']
        if effect['type'] == 'blood_recovery'
    )
    assert recovery['remaining'] == 5
    assert [entry['name'] for entry in response.json['actor_data']['inventory']['backpack']] == [
        'Капельница',
    ]


def test_blood_packet_rejects_incompatible_type_before_consumption(
        client, pair, auth_headers):
    _, _, item = install_item(
        pair, 'Пакет крови', 'Восстанавливает стадию кровопотери.',
        quantity=1, medicine=20,
    )
    data = deepcopy(pair['actor_character'].data)
    data['inventory']['backpack'][0]['attributes'] = {'bloodType': 2, 'bloodTypeKnown': True}
    data['inventory']['backpack'].append({
        'id': 'dropper', 'name': 'Капельница', 'category': 'consumable', 'quantity': 1,
    })
    data['health'].setdefault('combatMeta', {})['bloodType'] = 1
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    item = deepcopy(data['inventory']['backpack'][0])

    response = apply(client, pair, auth_headers, payload(
        pair, item, {'kind': 'self', 'actionPoints': 0}, operation='bad-blood-transfusion',
    ))

    assert response.status_code == 400
    assert len(pair['actor_character'].data['inventory']['backpack']) == 2


def test_blood_type_kit_marks_selected_packet_without_client_side_mutation(
        client, pair, auth_headers):
    _, _, item = install_item(
        pair, 'Набор для определения группы крови',
        'Позволяет определить группу крови.', quantity=1,
    )
    data = deepcopy(pair['actor_character'].data)
    packet = {
        'id': 'unknown-packet', 'name': 'Пакет крови', 'category': 'consumable',
        'quantity': 1, 'attributes': {'bloodType': 3},
    }
    data['inventory']['backpack'].append(packet)
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()

    application = {
        'kind': 'blood_type_test', 'target': 'packet', 'actionPoints': 0,
        'entry': {
            'path': ['inventory', 'backpack', 1],
            'item': deepcopy(packet),
        },
    }
    response = apply(client, pair, auth_headers, payload(
        pair, item, application, operation='test-blood-packet',
    ))

    assert response.status_code == 200, response.json
    result = response.json['medical_result']
    assert result['roll'] is None
    assert result['outcome']['blood_type'] == 3
    stored = response.json['actor_data']['inventory']['backpack'][0]
    assert stored['id'] == 'unknown-packet'
    assert stored['attributes']['bloodTypeKnown'] is True


def test_blood_collection_creates_packet_and_worsens_donor_state(
        client, pair, auth_headers):
    packet_profile = parse_consumable_effects('Пакет крови. Восстанавливает стадию кровопотери.')
    db.session.add(ItemTemplate(
        name='Пакет крови', category='consumable',
        attributes={'consumable': packet_profile},
    ))
    _, _, item = install_item(
        pair, 'Набор для забора крови',
        'При использовании выкачивает кровь и преобразует набор в пакет крови.',
        quantity=1,
    )
    data = deepcopy(pair['actor_character'].data)
    data['health']['blood'] = 'normal'
    data['health']['bloodStage'] = 'normal'
    data['health']['exhaustion'] = 1
    data['health'].setdefault('combatMeta', {})['bloodType'] = 4
    data['health']['combatMeta']['bloodTypeKnown'] = True
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()

    response = apply(client, pair, auth_headers, payload(
        pair, item, {'kind': 'self', 'actionPoints': 0}, operation='collect-blood',
    ))

    assert response.status_code == 200, response.json
    result = response.json['medical_result']
    assert result['roll'] is None
    assert result['outcome']['blood_stage'] == 'medium'
    data = response.json['actor_data']
    assert data['health']['bloodStage'] == 'medium'
    assert data['health']['exhaustion'] == 3
    assert data['inventory']['backpack'] == []
    packet = data['inventory']['pockets'][-1]
    assert packet['name'] == 'Пакет крови'
    assert packet['attributes']['bloodType'] == 4
    assert packet['attributes']['bloodTypeKnown'] is True
    assert packet['attributes']['donorCharacterId'] == pair['actor_character'].id
    assert packet['isStackable'] is False


def test_blood_packet_keeps_type_after_template_and_item_rename(client, pair):
    profile = parse_consumable_effects('Пакет крови. Восстанавливает стадию кровопотери.')
    template = ItemTemplate(
        name='Медицинский контейнер', category='consumable',
        attributes={'consumable': profile},
    )
    db.session.add(template)
    db.session.flush()
    item = {'templateId': template.id, 'name': 'Совсем другое название', 'category': 'consumable'}
    assert _is_blood_packet(item, pair['lobby'].id)
    created = _blood_packet_from_template(2, True)
    assert created['templateId'] == template.id
    assert created['attributes']['bloodType'] == 2


def test_unknown_donor_and_collected_packet_keep_one_blood_type(
        client, pair, auth_headers, monkeypatch):
    packet_profile = parse_consumable_effects('Пакет крови. Восстанавливает стадию кровопотери.')
    db.session.add(ItemTemplate(
        name='Пакет крови', category='consumable',
        attributes={'consumable': packet_profile},
    ))
    _, _, collection_kit = install_item(
        pair, 'Набор для забора крови',
        'При использовании выкачивает кровь и преобразует набор в пакет крови.',
        quantity=1,
    )
    data = deepcopy(pair['actor_character'].data)
    data['health'].setdefault('combatMeta', {}).pop('bloodType', None)
    data['health']['combatMeta']['bloodTypeKnown'] = False
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 17)

    collected = apply(client, pair, auth_headers, payload(
        pair, collection_kit, {'kind': 'self', 'actionPoints': 0},
        operation='collect-unknown-blood',
    ))
    assert collected.status_code == 200, collected.json
    donor_meta = collected.json['actor_data']['health']['combatMeta']
    packet = collected.json['actor_data']['inventory']['pockets'][-1]
    assert donor_meta['bloodType'] == 3
    assert donor_meta['bloodTypeKnown'] is False
    assert packet['attributes']['bloodType'] == 3
    assert packet['attributes']['bloodTypeKnown'] is False
    assert packet['attributes']['donorCharacterId'] == pair['actor_character'].id

    _, _, test_kit = install_item(
        pair, 'Набор для определения группы крови',
        'Позволяет определить группу крови.', quantity=1,
    )
    packet_application = {
        'kind': 'blood_type_test', 'target': 'packet', 'actionPoints': 0,
        'entry': {'path': ['inventory', 'pockets', 0], 'item': deepcopy(packet)},
    }
    tested_packet = apply(client, pair, auth_headers, payload(
        pair, test_kit, packet_application, operation='test-collected-packet',
    ))
    assert tested_packet.status_code == 200, tested_packet.json
    assert tested_packet.json['medical_result']['outcome']['blood_type'] == 3
    assert tested_packet.json['actor_data']['inventory']['pockets'][0]['attributes']['bloodTypeKnown'] is True
    assert tested_packet.json['actor_data']['health']['combatMeta']['bloodTypeKnown'] is True

    _, _, test_kit = install_item(
        pair, 'Набор для определения группы крови',
        'Позволяет определить группу крови.', quantity=1,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 1)
    tested_donor = apply(client, pair, auth_headers, payload(
        pair, test_kit, {'kind': 'blood_type_test', 'target': 'character', 'actionPoints': 0},
        operation='test-collected-donor',
    ))
    assert tested_donor.status_code == 200, tested_donor.json
    assert tested_donor.json['medical_result']['outcome']['blood_type'] == 3
    assert tested_donor.json['actor_data']['health']['combatMeta']['bloodTypeKnown'] is True


def test_legacy_packet_without_group_can_be_tested(client, pair, auth_headers, monkeypatch):
    _, _, item = install_item(
        pair, 'Набор для определения группы крови',
        'Позволяет определить группу крови.', quantity=1,
    )
    data = deepcopy(pair['actor_character'].data)
    packet = {
        'id': 'legacy-packet', 'name': 'Пакет крови', 'category': 'consumable',
        'quantity': 1, 'attributes': {'bloodType': None},
    }
    data['inventory']['pockets'].append(packet)
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)

    response = apply(client, pair, auth_headers, payload(
        pair, item, {
            'kind': 'blood_type_test', 'target': 'packet', 'actionPoints': 0,
            'entry': {'path': ['inventory', 'pockets', 0], 'item': deepcopy(packet)},
        }, operation='test-legacy-packet',
    ))
    assert response.status_code == 200, response.json
    assert response.json['medical_result']['outcome']['blood_type'] == 4
    attributes = response.json['actor_data']['inventory']['pockets'][0]['attributes']
    assert attributes['bloodType'] == 4
    assert attributes['bloodTypeKnown'] is True


def test_packet_donor_link_rejects_tampered_identity(client):
    packet = {
        'id': 'collected-packet',
        'attributes': {'bloodType': 3, 'donorCharacterId': 42},
    }
    packet['attributes']['bloodDonorProof'] = _blood_packet_proof(packet['id'], 42, 3)
    assert _verified_blood_donor(packet) == 42
    packet['attributes']['donorCharacterId'] = 43
    assert _verified_blood_donor(packet) is None
    packet['attributes']['donorCharacterId'] = 42
    packet['attributes']['bloodType'] = 4
    assert _verified_blood_donor(packet) is None


@pytest.mark.parametrize('donor_type, expected_type', [(2, 2), (None, 3)])
def test_out_of_combat_blood_collection_from_other_character_completes_consent(
        client, pair, auth_headers, monkeypatch, donor_type, expected_type):
    packet_profile = parse_consumable_effects('Пакет крови. Восстанавливает стадию кровопотери.')
    db.session.add(ItemTemplate(
        name='Пакет крови', category='consumable',
        attributes={'consumable': packet_profile},
    ))
    _, _, item = install_item(
        pair, 'Набор для забора крови',
        'При использовании выкачивает кровь и преобразует набор в пакет крови.',
        quantity=1,
    )
    combat = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    combat.status = 'idle'
    donor_data = deepcopy(pair['target_character'].data)
    donor_data['health']['blood'] = 'normal'
    donor_data['health']['bloodStage'] = 'normal'
    donor_data['health']['exhaustion'] = 0
    donor_data['health'].setdefault('combatMeta', {})['bloodType'] = donor_type
    donor_data['health']['combatMeta']['bloodTypeKnown'] = donor_type is not None
    pair['target_character'].data = donor_data
    flag_modified(pair['target_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 17)
    consent, _ = treatment_payload(pair)

    response = apply(client, pair, auth_headers, payload(
        pair, item, {'kind': 'self', 'actionPoints': 0}, operation='collect-other-blood',
        target=pair['target_character'], consent=consent,
    ))

    assert response.status_code == 200, response.json
    assert consent.status == 'completed'
    assert response.json['target_data']['health']['bloodStage'] == 'medium'
    assert response.json['target_data']['health']['exhaustion'] == 2
    assert response.json['target_data']['health']['combatMeta']['bloodType'] == expected_type
    packet = response.json['actor_data']['inventory']['pockets'][-1]
    assert packet['attributes']['bloodType'] == expected_type
    assert packet['attributes']['bloodTypeKnown'] is (donor_type is not None)
    assert packet['attributes']['donorCharacterId'] == pair['target_character'].id
    assert response.json['actor_data']['inventory']['backpack'] == []

    if donor_type is None:
        _, _, test_kit = install_item(
            pair, 'Набор для определения группы крови',
            'Позволяет определить группу крови.', quantity=1,
        )
        tested = apply(client, pair, auth_headers, payload(
            pair, test_kit, {
                'kind': 'blood_type_test', 'target': 'packet', 'actionPoints': 0,
                'entry': {'path': ['inventory', 'pockets', 0], 'item': deepcopy(packet)},
            }, operation='test-other-donor-packet',
        ))
        assert tested.status_code == 200, tested.json
        assert tested.json['medical_result']['outcome']['blood_type'] == expected_type
        assert pair['target_character'].data['health']['combatMeta']['bloodTypeKnown'] is True


def test_testing_donor_reveals_related_packet_held_by_doctor(
        client, pair, auth_headers, monkeypatch):
    packet_profile = parse_consumable_effects('Пакет крови. Восстанавливает стадию кровопотери.')
    db.session.add(ItemTemplate(
        name='Пакет крови', category='consumable',
        attributes={'consumable': packet_profile},
    ))
    _, _, collection_kit = install_item(
        pair, 'Набор для забора крови',
        'При использовании выкачивает кровь и преобразует набор в пакет крови.',
        quantity=1,
    )
    combat = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    combat.status = 'idle'
    donor_data = deepcopy(pair['target_character'].data)
    donor_data['health'].setdefault('combatMeta', {}).pop('bloodType', None)
    pair['target_character'].data = donor_data
    flag_modified(pair['target_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 17)
    consent, _ = treatment_payload(pair)
    collected = apply(client, pair, auth_headers, payload(
        pair, collection_kit, {'kind': 'self', 'actionPoints': 0},
        operation='collect-before-donor-test', target=pair['target_character'], consent=consent,
    ))
    assert collected.status_code == 200, collected.json
    assert collected.json['actor_data']['inventory']['pockets'][-1]['attributes']['bloodTypeKnown'] is False

    _, _, test_kit = install_item(
        pair, 'Набор для определения группы крови',
        'Позволяет определить группу крови.', quantity=1,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 1)
    consent, _ = treatment_payload(pair)
    tested = apply(client, pair, auth_headers, payload(
        pair, test_kit, {'kind': 'blood_type_test', 'target': 'character', 'actionPoints': 0},
        operation='test-donor-after-collection', target=pair['target_character'], consent=consent,
    ))
    assert tested.status_code == 200, tested.json
    assert tested.json['medical_result']['outcome']['blood_type'] == 3
    packet = tested.json['actor_data']['inventory']['pockets'][-1]
    assert packet['attributes']['bloodType'] == 3
    assert packet['attributes']['bloodTypeKnown'] is True


def test_consent_cannot_skip_combat_payment_by_omitting_location(
        client, pair, auth_headers):
    _, _, item = install_item(
        pair, 'Набор для забора крови',
        'При использовании выкачивает кровь и преобразует набор в пакет крови.',
        quantity=1,
    )
    consent, _ = treatment_payload(pair)
    response = apply(client, pair, auth_headers, payload(
        pair, item, {'kind': 'self', 'actionPoints': 0}, operation='unpaid-blood-collection',
        target=pair['target_character'], consent=consent,
    ))
    assert response.status_code == 400, response.json
    assert consent.status == 'accepted'
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 1


def test_ammonia_requires_shock_and_is_not_consumed(client, pair, auth_headers):
    shock = {'id': 'shock-1', 'type': 'shock', 'name': 'Болевой шок', 'active': True}
    _, _, item = install_item(
        pair, 'Бутылек нашатыря',
        'Даёт +2 к проверке выхода из болевого шока и преимущество. Не тратится.',
        quantity=1, effects=[shock],
    )

    response = apply(client, pair, auth_headers, payload(
        pair, item, {'kind': 'self', 'actionPoints': 0}, operation='ammonia',
    ))

    assert response.status_code == 200, response.json
    assert response.json['medical_result']['roll'] is None
    meta = response.json['actor_data']['health']['combatMeta']
    assert meta['willShockBonus'] == 2
    assert meta['willShockAdvantage'] is True
    assert response.json['actor_data']['inventory']['backpack'][0]['quantity'] == 1


def test_second_life_closes_bleeding_then_restores_limb_and_pool_after_minute(
        client, pair, auth_headers, monkeypatch):
    fracture = {'id': 'fracture-1', 'type': 'fracture', 'area': 'leftLeg', 'source': 'fall'}
    bleeding = {'id': 'bleed-1', 'type': 'bleeding_internal_severe', 'area': 'leftLeg', 'source': 'shot'}
    zones = {
        'leftArm': {'current': 50, 'max': 50}, 'rightArm': {'current': 50, 'max': 50},
        'leftLeg': {'current': 0, 'max': 70}, 'rightLeg': {'current': 50, 'max': 50},
    }
    _, _, item = install_item(
        pair, '«Вторая жизнь»',
        'Ампула. Излечивает перелом и восстанавливает конечность. Она имеет 50 здоровья. '
        'Срабатывает через 1 минуту. Бонус медикамента +4. Усиливает боль на 5 уровней.',
        quantity=1, effects=[fracture, bleeding], zones=zones, pain=2,
    )
    data = deepcopy(pair['actor_character'].data)
    data['health']['current'] = 30
    data['health']['max'] = 200
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    application = {
        'kind': 'injury', 'effect': {'type': 'body_zone', 'area': 'leftLeg'},
        'treatmentMode': 'second_life', 'actionPoints': 1,
    }
    response = apply(client, pair, auth_headers, payload(pair, item, application))
    assert response.status_code == 200, response.json
    health = response.json['actor_data']['health']
    assert health['painLevel'] == 7
    assert health['zones']['leftLeg']['current'] == 0
    assert health['current'] == 30
    assert any(effect['type'] == 'fracture' for effect in health['effects'])
    assert not any(effect['type'].startswith('bleeding_') for effect in health['effects'])
    assert any(effect['type'] == 'delayed_limb_treatment' for effect in health['effects'])

    health['effects'] = advance_timed_effects(health, health['effects'], 60)
    assert health['zones']['leftLeg']['current'] == 50
    assert health['current'] == 80
    assert not any(effect['type'] == 'fracture' for effect in health['effects'])


def test_chimera_without_fracture_damages_limb_and_health_pool(client, pair, auth_headers, monkeypatch):
    bleeding = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'rightArm', 'source': 'cut'}
    zones = {
        'leftArm': {'current': 50, 'max': 50}, 'rightArm': {'current': 40, 'max': 50},
        'leftLeg': {'current': 50, 'max': 50}, 'rightLeg': {'current': 50, 'max': 50},
    }
    _, _, item = install_item(
        pair, '«Химера»',
        'Ампула. Излечивает перелом. Срабатывает через 1 минуту. Бонус медикамента +2. '
        'При использовании на конечности без перелома -200 Здоровья в эту конечность.',
        quantity=1, effects=[bleeding], zones=zones,
    )
    data = deepcopy(pair['actor_character'].data)
    data['health']['current'] = 180
    data['health']['max'] = 200
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    application = {
        'kind': 'injury', 'effect': {'type': 'body_zone', 'area': 'rightArm'},
        'treatmentMode': 'chimera', 'actionPoints': 1,
    }
    response = apply(client, pair, auth_headers, payload(pair, item, application))
    assert response.status_code == 200, response.json
    health = response.json['actor_data']['health']
    assert health['zones']['rightArm']['current'] == 0
    assert health['current'] == 20
    assert not any(effect['type'].startswith('bleeding_') for effect in health['effects'])
    assert not any(effect['type'] == 'delayed_limb_treatment' for effect in health['effects'])


def test_chimera_applied_to_head_kills_target(client, pair, auth_headers, monkeypatch):
    zones = {
        'head': {'current': 50, 'max': 50},
        'leftArm': {'current': 50, 'max': 50}, 'rightArm': {'current': 50, 'max': 50},
        'leftLeg': {'current': 50, 'max': 50}, 'rightLeg': {'current': 50, 'max': 50},
    }
    _, _, item = install_item(
        pair, '«Химера»',
        'Ампула. Излечивает перелом. Срабатывает через 1 минуту. Бонус медикамента +2.',
        quantity=1, zones=zones,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    application = {
        'kind': 'injury', 'effect': {'type': 'body_zone', 'area': 'head'},
        'treatmentMode': 'chimera', 'actionPoints': 1,
    }
    response = apply(client, pair, auth_headers, payload(pair, item, application))
    assert response.status_code == 200, response.json
    assert any(effect['type'] == 'death' for effect in response.json['actor_data']['health']['effects'])


def test_full_restoration_kit_restores_mangled_limb_and_shared_health(client, pair, auth_headers, monkeypatch):
    mangled = {'id': 'mangled-1', 'type': 'mangled_limb', 'area': 'leftArm', 'source': 'damage'}
    zones = {
        'leftArm': {'current': 0, 'max': 60, 'destructionDamage': 180},
        'rightArm': {'current': 50, 'max': 50}, 'leftLeg': {'current': 50, 'max': 50},
        'rightLeg': {'current': 50, 'max': 50},
    }
    _, _, item = install_item(
        pair, 'Набор полного восстановления конечности',
        'Время использования - 15 ОД. Восстанавливает утерянный орган или искореженную конечность. '
        'Бонус медикамента +3. 5 использований. Усиливает боль на 3 уровня.',
        quantity=1, effects=[mangled], zones=zones, pain=1,
    )
    data = deepcopy(pair['actor_character'].data)
    data['health']['current'] = 40
    data['health']['max'] = 200
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    application = {
        'kind': 'injury', 'effect': deepcopy(mangled), 'treatmentMode': 'restore_mangled_limb',
        'application': {'item_uses': 1}, 'actionPoints': 15,
    }
    bypass = {
        'kind': 'injury', 'effect': {'type': 'damaged_zone', 'area': 'leftArm'},
        'treatmentMode': 'restore_disabled_limb', 'application': {'item_uses': 1},
        'actionPoints': 15,
    }
    rejected = apply(
        client, pair, auth_headers,
        payload(pair, item, bypass, operation='mangled-as-disabled'),
    )
    assert rejected.status_code == 400
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 1

    response = apply(client, pair, auth_headers, payload(pair, item, application))
    assert response.status_code == 200, response.json
    health = response.json['actor_data']['health']
    assert health['zones']['leftArm']['current'] == 60
    assert health['zones']['leftArm']['destructionDamage'] == 0
    assert health['current'] == 100
    assert health['painLevel'] == 4
    assert not any(effect['type'] == 'mangled_limb' for effect in health['effects'])
    assert response.json['actor_data']['inventory']['backpack'][0]['uses'] == 4


def test_surgeon_kit_uses_five_charges_and_restores_mangled_limb_to_thirty(
        client, pair, auth_headers, monkeypatch):
    mangled = {'id': 'mangled-1', 'type': 'mangled_limb', 'area': 'rightLeg', 'source': 'damage'}
    zones = {
        'leftArm': {'current': 50, 'max': 50}, 'rightArm': {'current': 50, 'max': 50},
        'leftLeg': {'current': 50, 'max': 50},
        'rightLeg': {'current': 0, 'max': 70, 'destructionDamage': 210},
    }
    _, _, item = install_item(
        pair, 'Хирургический набор «Хирург»',
        'Время использования - 10 ОД. Восстанавливает часть тела. Она имеет 30 здоровья. '
        '5 использований.',
        quantity=1, effects=[mangled], zones=zones,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    application = {
        'kind': 'injury', 'effect': deepcopy(mangled), 'treatmentMode': 'restore_mangled_limb',
        'application': {'item_uses': 5}, 'actionPoints': 10,
    }
    response = apply(client, pair, auth_headers, payload(pair, item, application))
    assert response.status_code == 200, response.json
    assert response.json['actor_data']['health']['zones']['rightLeg']['current'] == 30
    assert response.json['actor_data']['inventory']['backpack'] == []


def test_full_restoration_kit_rejects_expired_organ_window(client, pair, auth_headers, monkeypatch):
    organ = {
        'id': 'organ-1', 'type': 'organ_loss', 'area': 'leftEye', 'source': 'damage',
        'treatment_window_expired': True, 'treatment_window_seconds': 0,
    }
    _, _, item = install_item(
        pair, 'Набор полного восстановления конечности',
        'Время использования - 15 ОД. Восстанавливает утерянный орган или искореженную конечность. '
        'Бонус медикамента +3. 5 использований.',
        quantity=1, effects=[organ],
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    application = {
        'kind': 'injury', 'effect': deepcopy(organ), 'treatmentMode': 'restore_lost_part',
        'application': {'item_uses': 5}, 'actionPoints': 15,
    }
    response = apply(client, pair, auth_headers, payload(pair, item, application))
    assert response.status_code == 409
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 1


def test_aybolit_restores_only_knocked_out_limb_to_one(client, pair, auth_headers, monkeypatch):
    zones = {
        'leftArm': {'current': 0, 'max': 50}, 'rightArm': {'current': 20, 'max': 50},
        'leftLeg': {'current': 50, 'max': 50}, 'rightLeg': {'current': 50, 'max': 50},
    }
    _, _, item = install_item(
        pair, 'Кустарный набор «Айболит»',
        'Восстанавливает часть тела. Она имеет 1 здоровье. Время использования - 6 ОД.',
        quantity=2, zones=zones,
    )
    data = deepcopy(pair['actor_character'].data)
    data['health']['current'] = 40
    data['health']['max'] = 200
    pair['actor_character'].data = data
    flag_modified(pair['actor_character'], 'data')
    db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    application = {
        'kind': 'injury', 'effect': {'type': 'damaged_zone', 'area': 'leftArm'},
        'treatmentMode': 'restore_disabled_limb', 'actionPoints': 6,
    }
    response = apply(client, pair, auth_headers, payload(pair, item, application))
    assert response.status_code == 200, response.json
    health = response.json['actor_data']['health']
    assert health['zones']['leftArm']['current'] == 1
    assert health['current'] == 41

    current_item = response.json['actor_data']['inventory']['backpack'][0]
    forged = {
        'kind': 'injury', 'effect': {'type': 'damaged_zone', 'area': 'rightArm'},
        'treatmentMode': 'restore_disabled_limb', 'actionPoints': 6,
    }
    rejected = apply(
        client, pair, auth_headers,
        payload(pair, current_item, forged, operation='aybolit-positive-limb'),
    )
    assert rejected.status_code == 409
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 1


def test_failed_check_spends_item_but_keeps_injury_and_registers_must_do(client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_severe', 'area': 'rightArm', 'source': 'shot'}
    _, profile, item = install_item(
        pair, 'Пластырь с гемостатиком', 'Останавливает Сильное кровотечение.',
        effects=[effect], medicine=0,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 1)
    response = apply(client, pair, auth_headers, payload(pair, item, selected_bleeding(profile, effect, 1)))
    assert response.status_code == 200, response.json
    assert response.json['medical_result']['success'] is False
    data = response.json['actor_data']
    assert any(entry['id'] == 'bleed-1' for entry in data['health']['effects'])
    assert data['inventory']['backpack'][0]['quantity'] == 1
    public_retry = data['health']['combatMeta']['mustDoRetry']
    assert public_retry['kind'] == 'medical'
    assert 'medical_retry' not in public_retry
    assert 'attack_details' not in public_retry
    db.session.refresh(pair['actor_character'])
    retry = pair['actor_character'].data['health']['combatMeta']['mustDoRetry']
    assert retry['kind'] == 'medical'
    assert retry['medical_retry']['application']['effect']['id'] == 'bleed-1'
    assert retry['medical_retry']['server_authoritative'] is True
    assert public_retry == {key: value for key, value in retry.items()
                            if key not in {'medical_retry', 'attack_details'}}


def test_must_do_retry_applies_server_treatment_without_spending_item_twice(
        client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(
        pair, 'Бинт', 'Останавливает Слабое кровотечение.',
        quantity=2, medicine=0, effects=[effect],
    )
    application = selected_bleeding(profile, effect)
    selection = {
        'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
        'application': deepcopy(application), 'target_character_id': pair['actor_character'].id,
        'treatment_request_id': None, 'server_authoritative': True,
    }
    monkeypatch.setattr('app.services.deferred_consumable.random.randint', lambda *_: 1)
    pair['actor_location'].action_points_current = 7
    db.session.commit()
    paid = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/spend",
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id, 'action_points': 7,
            'allow_deferred': True, 'pending_action_id': 'must-do-medical',
            'pending_action_label': 'Бинт', 'consumable_selection': selection,
        },
    )
    assert paid.status_code == 200, paid.json
    body = payload(pair, item, application, operation='must-do-medical', combat=True)
    body['deferred_action_id'] = 'must-do-medical'
    failed = apply(client, pair, auth_headers, body)
    assert failed.status_code == 200, failed.json
    assert failed.json['medical_result']['success'] is False
    assert failed.json['actor_data']['inventory']['backpack'][0]['quantity'] == 1

    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 20)
    retried = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/action",
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id,
            'action_key': 'must_do_it',
        },
    )
    assert retried.status_code == 200, retried.json
    details = retried.json['must_do_it']
    assert details['check']['success'] is True
    assert details['medical_result']['outcome']['kind'] == 'bleeding'
    assert 'medical_retry' not in details
    data = pair['actor_character'].data
    assert data['inventory']['backpack'][0]['quantity'] == 1
    assert not any(entry['type'].startswith('bleeding_') for entry in data['health']['effects'])


def test_failed_server_medical_retry_does_not_expose_or_apply_procedure(
        client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(
        pair, 'Бинт', 'Останавливает Слабое кровотечение.',
        quantity=2, medicine=0, effects=[effect],
    )
    application = selected_bleeding(profile, effect)
    selection = {
        'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
        'application': deepcopy(application), 'target_character_id': pair['actor_character'].id,
        'treatment_request_id': None, 'server_authoritative': True,
    }
    monkeypatch.setattr('app.services.deferred_consumable.random.randint', lambda *_: 1)
    pair['actor_location'].action_points_current = 7
    db.session.commit()
    paid = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/spend",
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id, 'action_points': 7,
            'allow_deferred': True, 'pending_action_id': 'must-do-medical-failure',
            'pending_action_label': 'Бинт', 'consumable_selection': selection,
        },
    )
    assert paid.status_code == 200, paid.json
    body = payload(pair, item, application, operation='must-do-medical-failure', combat=True)
    body['deferred_action_id'] = 'must-do-medical-failure'
    assert apply(client, pair, auth_headers, body).status_code == 200

    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 1)
    retried = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/action",
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id,
            'action_key': 'must_do_it',
        },
    )
    assert retried.status_code == 200, retried.json
    details = retried.json['must_do_it']
    assert details['check']['success'] is False
    assert 'medical_retry' not in details
    assert 'medical_result' not in details
    data = pair['actor_character'].data
    assert data['inventory']['backpack'][0]['quantity'] == 1
    assert any(entry.get('id') == 'bleed-1' for entry in data['health']['effects'])


def test_splint_fix_and_restore_are_separate_procedures(client, pair, auth_headers, monkeypatch):
    fracture = {'id': 'fracture-1', 'type': 'fracture', 'area': 'leftLeg', 'source': 'fall'}
    zones = {
        'leftArm': {'current': 50, 'max': 50}, 'rightArm': {'current': 50, 'max': 50},
        'leftLeg': {'current': 0, 'max': 50}, 'rightLeg': {'current': 50, 'max': 50},
    }
    _, _, item = install_item(
        pair, 'Шина', 'Фиксирует перелом. Временно восстанавливает конечность до 1 ОЗ.',
        quantity=2, effects=[fracture], zones=zones, pain=3,
    )
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    restore = {'kind': 'injury', 'effect': {'type': 'damaged_zone', 'area': 'leftLeg'},
               'treatmentMode': 'restore_limb', 'actionPoints': 6}
    first = apply(client, pair, auth_headers, payload(pair, item, restore, operation='restore'))
    assert first.status_code == 200, first.json
    restored = first.json['actor_data']
    assert restored['health']['zones']['leftLeg']['current'] == 1
    assert restored['health']['painLevel'] == 3
    assert any(entry['id'] == 'fracture-1' for entry in restored['health']['effects'])
    assert any(entry['type'] == 'temporary_limb_restoration' for entry in restored['health']['effects'])

    current_item = restored['inventory']['backpack'][0]
    current_fracture = next(entry for entry in restored['health']['effects'] if entry.get('id') == 'fracture-1')
    fix = {'kind': 'injury', 'effect': deepcopy(current_fracture),
           'treatmentMode': 'fix_fracture', 'actionPoints': 6}
    second = apply(client, pair, auth_headers, payload(pair, current_item, fix, operation='fix'))
    assert second.status_code == 200, second.json
    health = second.json['actor_data']['health']
    assert health['painLevel'] == 2
    assert not any(entry.get('id') == 'fracture-1' for entry in health['effects'])
    fixed = next(entry for entry in health['effects'] if entry['type'] == 'fracture_fixed')
    assert fixed['remaining_seconds'] == 86400


def test_deferred_treatment_uses_saved_roll_and_completes_once(client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[effect])
    pair['actor_location'].action_points_current = 1
    db.session.commit()
    application = selected_bleeding(profile, effect)
    selection = {
        'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
        'application': deepcopy(application), 'target_character_id': pair['actor_character'].id,
        'treatment_request_id': None,
    }
    start = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/spend",
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id, 'action_points': 6,
            'allow_deferred': True, 'pending_action_id': 'medical-deferred',
            'pending_action_label': 'Бинт', 'consumable_selection': selection,
        },
    )
    assert start.status_code == 200, start.json
    monkeypatch.setattr('app.services.deferred_consumable.random.randint', lambda *_: 20)
    other_end = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/end_turn",
        headers=auth_headers(pair['target_user']),
        json={'location_character_id': pair['target_location'].id},
    )
    assert other_end.status_code == 200, other_end.json
    row = db.session.get(DeferredCombatAction, 'medical-deferred')
    assert row.status == 'ready'
    assert row.payload['medicine_roll'] == 20
    body = payload(pair, item, application, operation='medical-deferred', combat=True)
    body['deferred_action_id'] = 'medical-deferred'
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == 200, response.json
    assert response.json['medical_result']['roll'] == 20
    assert db.session.get(DeferredCombatAction, 'medical-deferred').status == 'completed'
    assert apply(client, pair, auth_headers, body).status_code == 409


def test_treating_conscious_other_character_requires_and_completes_consent(client, pair, auth_headers, monkeypatch):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.')
    target_data = deepcopy(pair['target_character'].data)
    target_data['health']['effects'] = [effect]
    target_data['health']['zones'] = {'leftArm': {'current': 40, 'max': 50}}
    pair['target_character'].data = target_data
    flag_modified(pair['target_character'], 'data')
    db.session.commit()
    consent, _ = treatment_payload(pair)
    monkeypatch.setattr('app.services.medical_procedure.random.randint', lambda *_: 20)
    application = selected_bleeding(profile, effect)
    selection = {
        'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
        'application': deepcopy(application), 'target_character_id': pair['target_character'].id,
        'treatment_request_id': consent.id, 'server_authoritative': True,
    }
    paid = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/spend",
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id, 'action_points': 5,
            'allow_deferred': True, 'pending_action_id': 'other-treatment',
            'pending_action_label': 'Бинт', 'consumable_selection': selection,
        },
    )
    assert paid.status_code == 200, paid.json
    body = payload(
        pair, item, application, operation='other-treatment', target=pair['target_character'],
        combat=True, consent=consent,
    )
    body['deferred_action_id'] = 'other-treatment'
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == 200, response.json
    assert consent.status == 'completed'
    assert response.json['target_data']['health']['effects'][0]['type'] == 'untreated_wound'


def test_combat_endpoint_cannot_bypass_action_point_payment(client, pair, auth_headers):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[effect])
    body = payload(pair, item, selected_bleeding(profile, effect), combat=True)
    before_ap = pair['actor_location'].action_points_current
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == 400
    assert pair['actor_location'].action_points_current == before_ap
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2


def test_combat_procedure_rejects_underpayment(client, pair, auth_headers):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(
        pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[effect], medicine=20,
    )
    application = selected_bleeding(profile, effect)
    selection = {
        'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
        'application': deepcopy(application), 'target_character_id': pair['actor_character'].id,
        'treatment_request_id': None, 'server_authoritative': True,
    }
    paid = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/spend",
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id, 'action_points': 1,
            'allow_deferred': True, 'pending_action_id': 'underpaid-treatment',
            'pending_action_label': 'Бинт', 'consumable_selection': selection,
        },
    )
    assert paid.status_code == 400, paid.json
    assert 'требуется 5' in paid.json['error']['message']
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2


def test_combat_procedure_cannot_change_target_injury_after_payment(client, pair, auth_headers):
    first = {'id': 'bleed-1', 'type': 'bleeding_external_light', 'area': 'leftArm', 'source': 'cut'}
    second = {'id': 'bleed-2', 'type': 'bleeding_external_light', 'area': 'rightArm', 'source': 'cut'}
    _, profile, item = install_item(
        pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[first, second], medicine=20,
    )
    selected = selected_bleeding(profile, first)
    selection = {
        'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(item)},
        'application': deepcopy(selected), 'target_character_id': pair['actor_character'].id,
        'treatment_request_id': None, 'server_authoritative': True,
    }
    paid = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/spend",
        headers=auth_headers(pair['actor_user']), json={
            'location_character_id': pair['actor_location'].id, 'action_points': 5,
            'allow_deferred': True, 'pending_action_id': 'swapped-treatment',
            'pending_action_label': 'Бинт', 'consumable_selection': selection,
        },
    )
    assert paid.status_code == 200, paid.json
    changed = selected_bleeding(profile, second)
    body = payload(pair, item, changed, operation='swapped-treatment', combat=True)
    body['deferred_action_id'] = 'swapped-treatment'
    response = apply(client, pair, auth_headers, body)
    assert response.status_code == 409
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2


def test_forged_application_is_rejected_without_consumption(client, pair, auth_headers):
    effect = {'id': 'bleed-1', 'type': 'bleeding_external_extreme', 'area': 'leftArm', 'source': 'cut'}
    _, profile, item = install_item(pair, 'Бинт', 'Останавливает Слабое кровотечение.', effects=[effect])
    forged = selected_bleeding(profile, effect)
    forged['application']['max_stage'] = 'extreme'
    response = apply(client, pair, auth_headers, payload(pair, item, forged))
    assert response.status_code == 400
    assert pair['actor_character'].data['inventory']['backpack'][0]['quantity'] == 2
    assert ConsumableUse.query.count() == 0
