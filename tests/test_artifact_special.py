"""Личное ношение, общий суточный запас и реальные первые активации."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import LocationCombatState
from app.services.artifact_special import DAY, STATE_KEY, advance, execute, operate, preserve, snapshot
from app.services.character import CharacterService
from app.services.character_merge import clean_snapshot
from app.services.combat import CombatService
from app.services.effects import advance_timed_effects
from app.services.exceptions import ConflictError, PermissionDenied, ValidationError
from test_artifact_reactions import equipped
from test_grenade_rules import grenade_game


def value(level=15, name='Ядро твари'):
    data = equipped(name)
    data['skills'] = {'other': {'survival': {'base': level}}}
    data['health'].update(current=100, radiation=10)
    return data


def ready(level=15, name='Ядро твари'):
    data = value(level, name)
    advance(data, DAY if level >= 15 else 2 * DAY)
    return data


def use(data, identity='use-one'):
    return execute(SimpleNamespace(), data, {'item_id': 'reactive-artifact', 'operation_id': identity}, 1)


@pytest.mark.parametrize('level,required,capacity,cost', [(8, 2*DAY, 0, 2), (9, 2*DAY, 1, 2),
                                                       (14, 2*DAY, 1, 2), (15, DAY, 2, 1), (20, DAY, 2, 1)])
def test_survival_thresholds_require_full_accumulated_time(level, required, capacity, cost):
    data = value(level)
    advance(data, required-1)
    state = snapshot(data)[0]
    assert not state['available']
    assert (state['capacity'], state['action_points']) == (capacity, cost)
    advance(data, 1)
    assert snapshot(data)[0]['available'] == bool(capacity)


def test_time_stops_in_pocket_resumes_on_same_carrier_and_is_not_transferred():
    first = value()
    advance(first, 300)
    equipment = first.pop('equipment')
    first['inventory'] = {'pockets': [equipment['armor']['containers'][0]['item']]}
    advance(first, 1000)
    assert first[STATE_KEY]['items']['reactive-artifact']['worn_seconds'] == 300
    second = value()
    second[STATE_KEY] = deepcopy(first[STATE_KEY])
    preserve({}, second)
    assert snapshot(second)[0]['worn_seconds'] == 0
    advance(second, 100)
    assert snapshot(second)[0]['worn_seconds'] == 100
    first['equipment'] = equipment
    first['inventory'] = {}
    advance(first, 20)
    assert snapshot(first)[0]['worn_seconds'] == 320


def test_segmented_effect_clock_counts_elapsed_only_once_and_no_hand_is_required():
    data = value()
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 1201, character_data=data)
    assert snapshot(data)[0]['worn_seconds'] == 1201
    assert not data.get('weapons')


def test_cooldown_returns_both_uses_after_first_not_second_even_while_unequipped():
    data = ready()
    use(data)
    assert snapshot(data)[0]['remaining_uses'] == 1
    advance(data, 8*3600)
    data['health']['radiation'] = 10
    use(data, 'use-two')
    assert snapshot(data)[0]['remaining_uses'] == 0
    assert snapshot(data)[0]['cooldown_seconds'] == 16*3600
    equipment = data.pop('equipment')
    advance(data, 16*3600-1)
    data['equipment'] = equipment
    assert snapshot(data)[0]['remaining_uses'] == 0
    advance(data, 1)
    assert snapshot(data)[0]['remaining_uses'] == 2


def test_replay_does_not_heal_or_spend_again_and_reuse_for_other_item_conflicts():
    data = ready()
    first = use(data)
    assert first['healed'] == 100 and data['health']['radiation'] == 0
    before = deepcopy(data)
    assert use(data)['replayed']
    assert data == before
    with pytest.raises(ConflictError):
        execute(None, data, {'item_id': 'other-item', 'operation_id': 'use-one'}, 1)


@pytest.mark.parametrize('name,radiation,removed,healed', [('Ядро твари', 60, 50, 500), ('Звезда гнили', 60, 60, 300)])
def test_conversion_limits_and_general_pool_without_healing_zones(name, radiation, removed, healed):
    data = ready(name=name)
    data['health'].update(radiation=radiation, zones={'chest': {'current': 50, 'max': 100}})
    result = use(data)
    assert result['radiation_removed'] == removed and result['healed'] == healed
    assert data['health']['radiation'] == radiation-removed
    assert data['health']['zones']['chest']['current'] == 50


def test_actual_healing_multiplier_and_no_gubka_damage_for_other_radiation_removal():
    data = ready()
    sponge = equipped('Чернобыльская губка')['equipment']['armor']['containers'][0]
    sponge['item']['id'] = 'own-sponge'
    data['equipment']['armor']['containers'].append(sponge)
    result = use(data)
    assert result['healed'] == 50 and data['health']['current'] == 150
    data['health']['effects'] = advance_timed_effects(data['health'], [], 60, character_data=data)
    assert data['health']['current'] == 150


def test_unready_unsupported_or_zero_radiation_does_not_spend():
    for data in [value(), ready(name='Ферромагнит'), ready()]:
        if data['health']['radiation'] == 10 and snapshot(data)[0]['available']:
            data['health']['radiation'] = 0
        before = deepcopy(data)
        with pytest.raises(ValidationError):
            use(data)
        assert data == before


def test_character_save_creation_and_transport_cannot_forge_progress(grenade_game):
    pair = grenade_game
    actor = pair['actor_location']
    original = ready()
    use(original)
    actor.character.data = original
    db.session.commit()
    incoming = deepcopy(actor.character.data)
    incoming[STATE_KEY]['items']['reactive-artifact'].update(worn_seconds=9999999, used=0)
    incoming['_artifact_specials'] = [{'available': True}]
    incoming['_revision'] = actor.character.revision
    CharacterService.update_character(actor.character_id, pair['lobby'].gm_id, {'data': incoming})
    assert snapshot(actor.character.data)[0]['worn_seconds'] == DAY
    assert snapshot(actor.character.data)[0]['remaining_uses'] == 1
    assert '_artifact_specials' not in clean_snapshot(incoming)
    created = CharacterService.create_character(pair['lobby'].id, pair['lobby'].gm_id, 'Новый носитель', incoming)
    assert STATE_KEY not in created.data and snapshot(created.data)[0]['worn_seconds'] == 0


@pytest.mark.parametrize('level,cost', [(9, 2), (15, 1)])
def test_actual_combat_action_cost_replay_and_free_route_cannot_bypass(grenade_game, level, cost):
    pair = grenade_game
    actor = pair['actor_location']
    actor.character.data = ready(level)
    actor.action_points_current = 5
    db.session.commit()
    user = pair['lobby'].gm_id
    payload = {'operation_id': 'combat-special', 'item_id': 'reactive-artifact',
               'character_revision': actor.character.revision}
    with pytest.raises(ValidationError, match='В бою'):
        operate(actor.character_id, user, payload)
    result = CombatService.perform_action(actor.location_id, user, actor.id, 'artifact_special', attribute_choice=payload)
    assert actor.action_points_current == 5-cost
    assert result['narrative_action']['healed'] == 100
    before = actor.character.data['health']['current']
    repeated = CombatService.perform_action(actor.location_id, user, actor.id, 'artifact_special', attribute_choice=payload)
    assert repeated['narrative_action']['replayed']
    assert actor.action_points_current == 5-cost and actor.character.data['health']['current'] == before


def test_http_permissions_revision_and_server_snapshot(grenade_game, client, auth_headers):
    pair = grenade_game
    character = pair['actor_character']
    character.data = ready()
    LocationCombatState.query.filter_by(location_id=pair['location'].id).one().status = 'inactive'
    db.session.commit()
    path = f'/lobbies/characters/{character.id}/artifact-special'
    payload = {'operation_id': 'http-artifact', 'item_id': 'reactive-artifact', 'character_revision': character.revision}
    response = client.post(path, headers=auth_headers(pair['target_user']), json=payload)
    assert response.status_code == 403
    response = client.post(path, headers=auth_headers(pair['actor_user']), json={**payload, 'character_revision': 0})
    assert response.status_code == 409
    response = client.post(path, headers=auth_headers(pair['actor_user']), json=payload)
    assert response.status_code == 200, response.json
    assert response.json['result']['healed'] == 100
    assert response.json['data']['_artifact_specials'][0]['remaining_uses'] == 1
    repeated = client.post(path, headers=auth_headers(pair['actor_user']), json=payload)
    assert repeated.status_code == 200 and repeated.json['result']['replayed']


def test_off_turn_and_missing_artifact_leave_usage_and_ap_unchanged(grenade_game):
    pair = grenade_game
    actor = pair['actor_location']
    actor.character.data = ready()
    state = LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    state.current_location_character_id = pair['target_location'].id
    db.session.commit()
    before = deepcopy(actor.character.data)
    with pytest.raises((ValidationError, PermissionDenied)):
        CombatService.perform_action(actor.location_id, pair['lobby'].gm_id, actor.id, 'artifact_special',
                                     attribute_choice={'item_id': 'reactive-artifact', 'operation_id': 'off-turn'})
    assert actor.character.data[STATE_KEY] == before[STATE_KEY]
    assert actor.character.data['health']['current'] == before['health']['current']
    assert actor.character.data['health']['radiation'] == before['health']['radiation']
    assert actor.action_points_current == 20
    data = ready()
    data['inventory'] = {'pockets': [data.pop('equipment')['armor']['containers'][0]['item']]}
    with pytest.raises(ValidationError, match='активный'):
        use(data)


def test_world_clock_respects_character_activity_and_separate_carriers(grenade_game, client, auth_headers):
    from test_regression_time_stress import advance_global_minutes
    pair = grenade_game
    LocationCombatState.query.filter_by(location_id=pair['location'].id).one().status = 'idle'
    first, second = pair['actor_character'], pair['target_character']
    first.data = value()
    second.data = value()
    first.time_active = False
    second.time_active = True
    db.session.commit()
    advance_global_minutes(client, pair, auth_headers, 1)
    assert snapshot(first.data)[0]['worn_seconds'] == 0
    assert snapshot(second.data)[0]['worn_seconds'] == 60
    first.time_active = True
    db.session.commit()
    advance_global_minutes(client, pair, auth_headers, 1)
    assert snapshot(first.data)[0]['worn_seconds'] == 60
    assert snapshot(second.data)[0]['worn_seconds'] == 120


@pytest.mark.parametrize('name,seconds,movement,strength,physical,chemical', [
    ('Искровая жилка', 6, -3, 0, 0, 0), ('Нервный катализатор', 12, -5, 2, 0, 0),
    ('Генератор', 600, -2, 0, 0, 0), ('Пылающий осколок', 18, -3, 0, 10, 0),
    ('Гнилая железа', 60, 0, 0, 0, 20),
])
def test_timed_abilities_change_actual_profile_then_expire(name, seconds, movement, strength, physical, chemical):
    from app.services.artifact_effects import artifact_passive_profile
    from app.services.artifact_special import is_special_effect
    data = ready(name=name)
    data['skills']['physical'] = {'strength': {'base': 10}}
    data['health']['radiation'] = 0
    before = artifact_passive_profile(data)
    use(data)
    active = artifact_passive_profile(data)
    assert active['movement_penalty']-before['movement_penalty'] == movement
    assert active['skill_strength']-before['skill_strength'] == strength
    assert active['protection']['physical']-before['protection']['physical'] == physical
    assert active['protection']['chemical']-before['protection']['chemical'] == chemical
    assert CombatService._skill_value(data, 'skills.physical.strength') == 10+strength
    health = data['health']
    # Изолируем срок способности от периодических пассивных свойств самого предмета.
    effects = [effect for effect in health['effects'] if is_special_effect(effect)]
    effects = advance_timed_effects(health, effects, seconds-1, character_data=data)
    assert any(is_special_effect(effect) for effect in effects)
    health['effects'] = advance_timed_effects(health, effects, 1, character_data=data)
    assert not any(is_special_effect(effect) for effect in health['effects'])
    expired = artifact_passive_profile(data)
    assert expired['movement_penalty'] == before['movement_penalty']
    assert expired['protection'] == before['protection']


def test_repeated_buff_refreshes_without_stacking_and_save_cannot_extend_or_forge():
    from app.services.artifact_special import is_special_effect
    from app.services.artifact_effects import artifact_passive_profile
    data = ready(name='Искровая жилка')
    before = artifact_passive_profile(data)['movement_penalty']
    use(data)
    data['health']['effects'] = advance_timed_effects(data['health'], data['health']['effects'], 2, character_data=data)
    use(data, 'second-buff')
    effects = [effect for effect in data['health']['effects'] if is_special_effect(effect)]
    assert len(effects) == 1 and effects[0]['remaining_seconds'] == 6
    assert artifact_passive_profile(data)['movement_penalty'] == before-3
    incoming = deepcopy(data)
    incoming['health']['effects'][0].update(value=-1000, remaining_seconds=999999)
    preserve(data, incoming)
    assert incoming['health']['effects'] == data['health']['effects']
    forged = deepcopy(data)
    preserve({}, forged)
    assert not any(is_special_effect(effect) for effect in forged['health']['effects'])


def test_activated_buff_keeps_its_fixed_duration_after_item_is_removed():
    from app.services.artifact_effects import artifact_passive_profile
    data = ready(name='Генератор')
    use(data)
    equipment = data.pop('equipment')
    assert artifact_passive_profile(data)['movement_penalty'] == -2
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 600, character_data=data)
    assert artifact_passive_profile(data)['movement_penalty'] == 0
    data['equipment'] = equipment
    assert artifact_passive_profile(data)['movement_penalty'] == -1
