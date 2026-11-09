"""Личное ношение, общий суточный запас и реальные первые активации."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import LocationCombatState, LocationObject
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


@pytest.mark.parametrize('dose,condition', [(85, 'critical_condition'), (105, 'death')])
def test_sponge_blocks_new_radiation_sickness_until_four_hours_even_after_removal(dose, condition):
    data = ready(name='Чернобыльская губка')
    health = data['health']
    health.update(current=700, radiation=0)
    use(data)
    data['equipment'] = {}
    health['effects'] = advance_timed_effects(health, health['effects'], 4*3600-1, character_data=data)
    exposure = CombatService._apply_incoming_radiation(data, dose)
    assert exposure['after'] == dose and not exposure['critical'] and not exposure['death']
    interval = CombatService._apply_world_radiation_consequences(data)
    assert interval['radiation_at_start'] == dose and not interval['applied']
    assert health['current'] == 700 and not any(e.get('source') == 'radiation_sickness' for e in health['effects'])
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert any(e['type'] == condition and e['source'] == 'radiation_sickness' for e in health['effects'])


def test_sponge_immunity_keeps_its_own_radiation_removal_cost():
    data = ready(name='Чернобыльская губка')
    health = data['health']
    health.update(current=700, radiation=3)
    use(data)
    health['effects'] = advance_timed_effects(health, health['effects'], 60, character_data=data)
    assert health['radiation'] == 0 and health['current'] == 670
    assert any(e.get('artifact_stat') == 'radiation_sickness_immunity' for e in health['effects'])


@pytest.mark.parametrize('name,threshold,cost,protection,multiplier', [
    ('Хозяин свечения', 30, 20, 10, 2), ('Реактор', 40, 50, 20, 3),
])
def test_radiation_amplifier_doubles_or_triples_only_its_ready_positive_bonuses(name, threshold, cost, protection, multiplier):
    from app.services.artifact_effects import artifact_passive_profile
    data = ready(name=name)
    second = equipped(name)['equipment']['armor']['containers'][0]
    second['item']['id'] = 'second-artifact'
    data['equipment']['armor']['containers'].append(second)
    health = data['health']
    health['radiation'] = threshold-1
    baseline = artifact_passive_profile(data)
    assert baseline['movement_penalty'] == baseline['protection']['physical'] == 0
    use(data)
    assert health['radiation'] == threshold-1+cost
    enhanced = artifact_passive_profile(data)
    assert enhanced['movement_penalty'] == -2*(multiplier+1)
    assert all(value == protection*(multiplier+1) for value in enhanced['protection'].values())
    assert enhanced['radiation_per_movement'] == baseline['radiation_per_movement']
    assert use(data)['replayed'] and health['radiation'] == threshold-1+cost
    health['radiation'] = threshold-1
    assert not artifact_passive_profile(data)['protection']['physical']
    health['radiation'] = threshold
    health['effects'] = advance_timed_effects(health, health['effects'], 60, character_data=data)
    assert artifact_passive_profile(data)['protection']['physical'] == protection*2


@pytest.mark.parametrize('name,other,physical', [('Ядро мысли', 5, 3), ('Сердце Эха', 8, 4)])
def test_psi_amplifier_improves_skills_without_enhancing_passive_cost(name, other, physical):
    from app.services.artifact_effects import artifact_passive_profile
    data = ready(name=name)
    health = data['health']
    health['current'] = 700
    baseline = artifact_passive_profile(data)
    use(data)
    enhanced = artifact_passive_profile(data)
    assert enhanced['skill_other'] == other*2 and enhanced['skill_physical'] == physical*2
    assert enhanced['pain_per_movement'] == baseline['pain_per_movement']
    assert enhanced['health_per_movement'] == baseline['health_per_movement']
    health['effects'] = advance_timed_effects(health, health['effects'], 59, character_data=data)
    assert health['current'] == 700 and artifact_passive_profile(data)['skill_other'] == other*2
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert artifact_passive_profile(data)['skill_other'] == other
    if name == 'Ядро мысли':
        assert health['current'] == 550
    else:
        assert health['current'] == 700 and artifact_passive_profile(data)['movement_penalty'] == 3
        health['effects'] = advance_timed_effects(health, health['effects'], 600, character_data=data)
        assert artifact_passive_profile(data)['movement_penalty'] == 0


def test_burner_pays_exact_health_for_selected_penalty_and_expires():
    from app.services.artifact_effects import artifact_passive_profile
    data = ready(name='Горелка')
    health = data['health']
    payload = {'item_id': 'reactive-artifact', 'operation_id': 'burner-test', 'power': 5}
    for power in (None, True, 0, 6, 3.5):
        with pytest.raises(ValidationError):
            execute(None, data, {**payload, 'power': power}, 1)
    before = deepcopy(data)
    with pytest.raises(ValidationError, match='Недостаточно ОЗ'):
        execute(None, data, payload, 1)
    assert data == before
    health['current'] = 700
    baseline = artifact_passive_profile(data)['movement_penalty']
    execute(None, data, payload, 1)
    assert health['current'] == 450 and artifact_passive_profile(data)['movement_penalty'] == baseline-5
    assert execute(None, data, payload, 1)['replayed'] and health['current'] == 450
    with pytest.raises(ConflictError):
        execute(None, data, {**payload, 'power': 4}, 1)
    health['effects'] = advance_timed_effects(health, health['effects'], 599, character_data=data)
    assert artifact_passive_profile(data)['movement_penalty'] == baseline-5
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert health['current'] == 450 and artifact_passive_profile(data)['movement_penalty'] == baseline


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


@pytest.mark.parametrize('name,kind,seconds', [('Сердце жара', 'thermal', 6), ('Ядро пепелища', 'thermal', 18),
                                            ('Печенка', 'chemical', 6), ('Сердце притяжения', 'physical', 6)])
def test_temporary_immunity_blocks_only_named_damage_and_expires(grenade_game, name, kind, seconds):
    target = grenade_game['actor_location']
    data = ready(name=name)
    data['health'].update(current=700, radiation=0)
    use(data)
    target.character.data = data
    db.session.commit()
    assert CombatService._target_elemental_protection(data, kind) == 100
    immune = CombatService._apply_general_damage(target, 100, damage_type=kind)
    assert immune['damage'] == 0 and immune['current_health'] == 700
    assert not immune.get('artifact_reactions')
    other = CombatService._apply_general_damage(target, 10, damage_type='electric')
    assert other['damage'] == 10 and other['current_health'] == 690
    health = target.character.data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], seconds, character_data=target.character.data)
    expired = CombatService._apply_general_damage(target, 10, damage_type=kind)
    assert expired['damage'] == 10 and expired['current_health'] == 680


def test_physical_immunity_blocks_zone_damage_and_forced_trauma(grenade_game):
    target = grenade_game['actor_location']
    data = ready(name='Сердце притяжения')
    data['health'].update(current=700, radiation=0)
    use(data)
    target.character.data = data
    db.session.commit()
    health = CombatService._apply_attack_damage(target, 500, 'chest', {'damage_type': 'crushing'},
                                               force_trauma=True, trauma_checks=2)
    assert health['current'] == 700
    assert health['zones']['chest']['current'] == health['zones']['chest']['max']
    assert health['_attackOutcome']['additional_traumas'] == []


def test_heat_heart_protects_real_burning_for_one_round_then_expires(grenade_game):
    from app.services.burning import ignite
    pair = grenade_game
    actor, other = pair['actor_location'], pair['target_location']
    data = ready(name='Сердце жара')
    data['health'].update(current=700, radiation=0)
    use(data)
    actor.character.data = data
    ignite(actor, 3, source='Проверка Сердца жара')
    db.session.commit()
    user = pair['lobby'].gm_id
    CombatService.end_turn(actor.location_id, user, actor.id)
    CombatService.end_turn(actor.location_id, user, other.id)
    assert actor.character.data['health']['current'] == 700
    CombatService.end_turn(actor.location_id, user, actor.id)
    CombatService.end_turn(actor.location_id, user, other.id)
    assert actor.character.data['health']['current'] == 650


def test_fiery_vein_removes_existing_breathlessness_and_stops_new_block_for_three_rounds(grenade_game):
    actor = grenade_game['actor_location']
    actor.character.data = ready(name='Огненная вена')
    actor.strenuous_movement_blocked_until_round = 100
    db.session.commit()
    result = CombatService.perform_action(actor.location_id, grenade_game['lobby'].gm_id, actor.id, 'artifact_special',
        attribute_choice={'operation_id': 'vein-breath', 'item_id': 'reactive-artifact'})
    assert result['narrative_action']['action_points'] == 1
    assert actor.strenuous_movement_blocked_until_round == 0
    data = actor.character.data
    assert CombatService._consumable_stat_value_bonus(data, 'breathless_immunity') == 1
    actor.strenuous_movement_blocked_until_round = 100
    actor.movement_mode_this_turn = 'walk'
    assert not CombatService._strenuous_movement_is_blocked(actor, 'sprint', 1)
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 17, character_data=data)
    assert CombatService._consumable_stat_value_bonus(data, 'breathless_immunity') == 1
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert CombatService._consumable_stat_value_bonus(data, 'breathless_immunity') == 0
    assert CombatService._strenuous_movement_is_blocked(actor, 'sprint', 1)


def test_storm_heart_bonuses_end_in_one_ten_minute_sleep():
    from app.services.artifact_effects import artifact_passive_profile
    data = ready(name='Сердце грозы')
    data['skills']['physical'] = {'strength': {'base': 10}}
    baseline = artifact_passive_profile(data)['movement_penalty']
    use(data)
    assert artifact_passive_profile(data)['movement_penalty'] == baseline-20
    assert CombatService._skill_value(data, 'skills.physical.strength') == 15
    assert CombatService._consumable_stat_value_bonus(data, 'breathless_immunity') == 1
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 11, character_data=data)
    assert not any(effect['type'] == 'sleep' for effect in health['effects'])
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    sleeps = [effect for effect in health['effects'] if effect['type'] == 'sleep']
    assert len(sleeps) == 1 and sleeps[0]['remaining_seconds'] == 600
    assert not CombatService._character_condition(data)['can_act']
    assert artifact_passive_profile(data)['movement_penalty'] == baseline
    assert CombatService._skill_value(data, 'skills.physical.strength') == 10
    health['effects'] = advance_timed_effects(health, health['effects'], 599, character_data=data)
    assert any(effect['type'] == 'sleep' for effect in health['effects'])
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert not any(effect['type'] == 'sleep' for effect in health['effects'])
    assert CombatService._character_condition(data)['can_act']


def test_storm_heart_sleep_uses_remaining_time_in_one_large_clock_step():
    data = ready(name='Сердце грозы')
    use(data)
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 612, character_data=data, advance_medication_turns=True)
    assert not any(effect['type'] == 'sleep' for effect in health['effects'])
    assert CombatService._consumable_stat_value_bonus(data, 'breathless_immunity') == 0


def test_storm_heart_suppresses_existing_breathlessness_in_client_state(grenade_game):
    actor = grenade_game['actor_location']
    data = ready(name='Сердце грозы')
    use(data)
    actor.character.data = data
    actor.strenuous_movement_blocked_until_round = 100
    db.session.commit()
    assert actor.strenuous_movement_blocked_until_round == 100
    assert CombatService._serialize_character(actor)['strenuous_movement_blocked_until_round'] == 0


def test_sniper_reposition_uses_same_breathlessness_immunity_as_actual_movement(grenade_game):
    from app.services.sniper_position import status as sniper_status
    actor = grenade_game['actor_location']
    data = ready(name='Сердце грозы')
    data['health']['combatMeta'] = {'sniperOldCover': {'id': -1, 'expires_after_round': 2}}
    actor.character.data = data
    actor.strenuous_movement_blocked_until_round = 100
    actor.movement_mode_this_turn = 'walk'
    db.session.commit()
    assert sniper_status(actor)['mode'] == 'walk'
    use(actor.character.data)
    assert sniper_status(actor)['mode'] == 'run'


def armored_shell():
    data = ready(name='Панцирь')
    data['health'].update(current=700, radiation=0)
    data['equipment']['armor'].update(name='Проверочная броня', category='armor', stage=2,
        stageDurability=1000, currentStageDurability=300, protection={'physical': 10}, attributes={})
    return data


@pytest.mark.parametrize('stage', [1, 2, 5])
def test_shell_prevents_stage_and_broken_armor_wear_without_repairing_or_protecting_body(grenade_game, stage):
    target = grenade_game['actor_location']
    data = armored_shell()
    armor = data['equipment']['armor']
    armor.update(stage=stage, currentStageDurability=0 if stage == 5 else 300, durability=10,
                 brokenDamage=40, brokenProtectionLoss=2)
    use(data)
    before = deepcopy(armor)
    assert CombatService._damage_armor_item(armor, {}, 5000, character_data=data)['damage'] == 0
    assert armor == before
    target.character.data = data
    assert CombatService._apply_general_damage(target, 25)['current_health'] == 675
    health = target.character.data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 60, character_data=target.character.data)
    armor = target.character.data['equipment']['armor']
    assert CombatService._damage_armor_item(armor, {}, 100, character_data=target.character.data)['damage'] == 100
    assert armor != before


@pytest.mark.parametrize('source', ['bullet', 'fragment', 'blast', 'anomaly', 'burning'])
def test_shell_is_connected_to_real_armor_damage_sources(grenade_game, monkeypatch, source):
    target, attacker = grenade_game['actor_location'], grenade_game['target_location']
    target.character.data = armored_shell()
    use(target.character.data)
    before = target.character.data['equipment']['armor']['currentStageDurability']
    if source == 'bullet':
        attacker.character.data = {'weapons': [{}], 'health': {'current': 700, 'max': 700}}
        monkeypatch.setattr(CombatService, '_ranged_damage_profile', lambda weapon: ({
            'damage': 100, 'armor_piercing': 10, 'bleeding': '', 'effective_range': 100,
            'damage_type': 'bullet'}, None))
        result = CombatService._resolve_attack(target, attacker,
            {'weapon_index': 0, 'hit_difficulty': 1, 'fire_mode': 'single', 'round_number': 1},
            aimed_zone='chest', forced_roll=20)
        assert result['hit'] and result['armor_damage'][0]['damage'] == 0
    elif source == 'fragment':
        result = CombatService._explosion_fragment_hit(target, 100, 10, 'chest', {}, 1)
        assert result['armor_damage'][0]['damage'] == 0
    elif source == 'blast':
        from test_grenade_rules import quiet_explosion, profile
        quiet_explosion(monkeypatch)
        result = CombatService.resolve_explosion(target.location_id, target.pos_x-.5, target.pos_y,
                                                profile(blast_base=100))
        hit = next(row for row in result['targets'] if row['character_id'] == target.character_id)
        assert hit['blast_armor_damage'][0]['damage'] == 0
    elif source == 'anomaly':
        result = CombatService._apply_anomaly_equipment_damage(target.character.data, 100,
            {'category': 'thermal', 'armor_damage_multiplier': 2})
        assert result[0]['damage'] == 0
    else:
        from app.services.burning import ignite, tick
        ignite(target, 2, source='Проверка Панциря')
        tick(target, 1)
    assert target.character.data['equipment']['armor']['currentStageDurability'] == before


@pytest.mark.parametrize('name,seconds', [('Ментальный бастион', 30), ('Щит сознания', 60)])
def test_psi_immunity_only_blocks_directed_exposure_and_expires(name, seconds):
    data = ready(name=name)
    data['health']['psyState'] = 10
    use(data)
    direct = CombatService._increase_psy_state(data, 20, 'пси-удар', directed=True)
    assert direct['received'] == 0 and direct['after'] == 10 and direct['artifact_immune']
    field = CombatService._increase_psy_state(data, 20, 'psy_field')
    assert field['received'] > 0 and not field['artifact_immune']
    anomaly = CombatService._increase_psy_state(data, 20, 'anomaly')
    assert anomaly['received'] > 0
    reduction = CombatService._increase_psy_state(data, -5, 'medicine', directed=True)
    assert reduction['received'] == -5
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], seconds, character_data=data)
    assert CombatService._increase_psy_state(data, 20, 'пси-удар', directed=True)['received'] > 0


@pytest.mark.parametrize('name', ['Ментальный бастион', 'Щит сознания'])
@pytest.mark.parametrize('attack', ['Пси-удар', 'Зомбирование', 'Приказ смерти'])
def test_psi_immunity_blocks_real_psion_actions_and_secondary_effects(grenade_game, name, attack):
    from app.services.effects import apply_effect_to_health
    from app.services.world_rules import mutant_character_data, mutant_profile
    victim, psion = grenade_game['actor_location'], grenade_game['target_location']
    victim.character.data = ready(name=name)
    psion.character.data = mutant_character_data(mutant_profile('Псионик'))
    psion.pos_x, psion.pos_y = victim.pos_x+2, victim.pos_y
    psion.facing_x, psion.facing_y = -1, 0
    psion.action_points_current = 20
    db.session.commit()
    gm = grenade_game['lobby'].gm_id
    CombatService.perform_action(victim.location_id, gm, victim.id, 'artifact_special',
        attribute_choice={'operation_id': 'psi-protection', 'item_id': 'reactive-artifact'})
    CombatService.end_turn(victim.location_id, gm, victim.id)
    if attack == 'Приказ смерти':
        apply_effect_to_health(victim.character.data['health'],
            {'type': 'zombification', 'source': 'previous-zombification', 'active': True})
    before_psy = victim.character.data['health'].get('psyState', 0)
    before_controller = victim.controlled_by
    before_ap = psion.action_points_current
    index = next(i for i, item in enumerate(psion.character.data['weapons']) if item['name'].casefold() == attack.casefold())
    result = CombatService.perform_action(psion.location_id, gm, psion.id, 'mutant_psy_attack',
        weapon_index=index, target_character_id=victim.character_id)
    action = result['mutant_action']
    assert action['artifact_immune'] and action['save'] is None
    assert victim.character.data['health'].get('psyState', 0) == before_psy
    assert victim.controlled_by == before_controller
    assert psion.action_points_current == before_ap-action['action_points']
    effects = victim.character.data['health']['effects']
    assert not any(e['type'] in {'concussion', 'death_order'} or e.get('source') == 'psyionik' for e in effects)
    if attack == 'Приказ смерти':
        assert any(e.get('source') == 'previous-zombification' for e in effects), 'Иммунитет не лечит прежнее зомбирование'


def test_psi_immunity_blocks_clone_hit_but_not_ordinary_damage(grenade_game):
    from app.services.world_rules import mutant_character_data, mutant_profile
    victim, clone = grenade_game['actor_location'], grenade_game['target_location']
    victim.character.data = ready(name='Ментальный бастион')
    use(victim.character.data)
    clone.character.data = mutant_character_data(mutant_profile('Псионик'))
    clone.character.data['basic']['psi_clone_damage'] = 20
    result = {}
    CombatService._apply_mutant_melee_hit_effects(clone, victim, 'Удар', 10, result)
    assert victim.character.data['health']['psyState'] == 0
    assert CombatService._apply_general_damage(victim, 10)['current_health'] == 90


def test_mind_shield_blocks_new_stress_in_both_paths_but_allows_reduction_and_expires(grenade_game):
    from app.services.effects import apply_effect_to_health, health_blocks_new_stress
    actor = grenade_game['actor_location']
    actor.character.data = ready(name='Щит сознания')
    data = actor.character.data
    data['health']['stress'] = 5
    use(data)
    assert health_blocks_new_stress(data['health'], include_medicine=False)
    assert CombatService.apply_stress_trigger(actor, 3, check_manifestation=False)['blocked']
    apply_effect_to_health(data['health'], {'type': 'stress', 'value': 3}, character_data=data)
    assert data['health']['stress'] == 5
    apply_effect_to_health(data['health'], {'type': 'stress_effect', 'source': 'stress_manifestation'}, character_data=data)
    assert not any(e['type'] == 'stress_effect' for e in data['health']['effects'])
    apply_effect_to_health(data['health'], {'type': 'stress', 'value': -2}, character_data=data)
    assert data['health']['stress'] == 3
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 60, character_data=data)
    assert not health_blocks_new_stress(health)
    CombatService.apply_stress_trigger(actor, 1, check_manifestation=False)
    assert health['stress'] == 4


def test_fire_gift_protection_expires_with_exact_health_cost_and_no_repeat():
    from app.services.artifact_effects import artifact_passive_profile
    data = ready(name='Дар пламени')
    data['health']['current'] = 700
    baseline = artifact_passive_profile(data)['protection']['physical']
    use(data)
    assert artifact_passive_profile(data)['protection']['physical'] == baseline+30
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 17, character_data=data)
    assert health['current'] == 700
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert health['current'] == 450
    assert artifact_passive_profile(data)['protection']['physical'] == baseline
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert health['current'] == 450


def test_second_fire_gift_refreshes_bonus_but_does_not_erase_first_health_cost():
    data = ready(name='Дар пламени')
    data['health']['current'] = 700
    use(data)
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 6, character_data=data)
    use(data, 'use-two')
    assert use(data, 'use-two')['replayed']
    health['effects'] = advance_timed_effects(health, health['effects'], 12, character_data=data)
    assert health['current'] == 450
    health['effects'] = advance_timed_effects(health, health['effects'], 6, character_data=data)
    assert health['current'] == 200


@pytest.mark.parametrize('in_combat,ap,protected', [(False, 0, True), (True, 1, False), (True, 2, True)])
def test_gravity_sphere_is_optional_and_spent_only_on_impact(grenade_game, monkeypatch, in_combat, ap, protected):
    actor = grenade_game['actor_location']
    user_id = grenade_game['actor_user']['id']
    actor.character.data = ready(level=9, name='Сфера притяжения')
    actor.character.data['health']['current'] = 700
    actor.action_points_current = ap
    if not in_combat:
        LocationCombatState.query.filter_by(location_id=actor.location_id).first().status = 'ended'
    db.session.commit()

    def arm(value):
        return operate(actor.character_id, user_id, {'item_id': 'reactive-artifact', 'arm_fall': value,
                                                   'character_revision': actor.character.revision})[1]

    assert arm(True)['armed'] and not arm(False)['armed']
    assert arm(True)['action_points'] == 0
    data = actor.character.data
    assert snapshot(data)[0]['remaining_uses'] == 1
    assert snapshot(data)[0]['cooldown_seconds'] == 0
    assert actor.action_points_current == ap
    before = deepcopy(data['health'])
    monkeypatch.setattr('app.services.combat.random.randint', lambda low, high: low)
    result = CombatService.resolve_fall(actor, 16)
    if protected:
        assert result['artifact'] == 'Сфера притяжения' and result['roll'] is None
        assert result['legs'] == [] and not result['shock'] and not result['fracture_both_legs']
        assert actor.character.data['health'] == before and actor.posture != 'prone'
        assert actor.action_points_current == 0
        state = snapshot(actor.character.data)[0]
        assert not state['armed'] and state['remaining_uses'] == 0 and state['cooldown_seconds'] == DAY
        result = CombatService.resolve_fall(actor, 16)
    else:
        assert snapshot(actor.character.data)[0]['remaining_uses'] == 1
        assert actor.action_points_current == ap
    assert not result.get('artifact') and result['shock'] and result['fracture_both_legs']


@pytest.mark.parametrize('key', ['batut', 'kacheli'])
def test_gravity_sphere_prevents_bleeding_from_anomaly_fall(grenade_game, key):
    actor = grenade_game['actor_location']
    actor.character.data = ready(name='Сфера притяжения')
    db.session.commit()
    operate(actor.character_id, grenade_game['actor_user']['id'], {'item_id': 'reactive-artifact', 'arm_fall': True,
                                                                  'character_revision': actor.character.revision})
    result = CombatService._apply_anomaly_exposure(actor, {'key': key})
    assert result['fall']['artifact'] == 'Сфера притяжения' and result['damage'] == 0
    assert not actor.character.data['health']['effects']


@pytest.mark.parametrize('offset,level,in_range', [(3, 0, True), (3.05, 0, False), (3, 1, False)])
def test_leech_clears_other_creatures_within_three_spatial_metres(grenade_game, offset, level, in_range):
    actor, other = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name='Пиявка')
    other.pos_x, other.pos_y, other.floor_level = actor.pos_x+offset, actor.pos_y, level
    other.character.data = {'health': {'current':700, 'radiation':14, 'effects':[]}}
    db.session.commit()
    payload = {'item_id': 'reactive-artifact', 'operation_id': 'leech-area'}
    result = execute(actor, actor.character.data, payload, grenade_game['lobby'].gm_id)
    assert result['radiation_removed'] == 10 and actor.character.data['health']['radiation'] == 0
    assert actor.character.data['health']['current'] == 100
    assert other.character.data['health']['radiation'] == (0 if in_range else 14)
    assert result.get('_affected_character_ids', []) == ([other.character_id] if in_range else [])
    assert execute(actor, actor.character.data, payload, grenade_game['lobby'].gm_id)['replayed']


@pytest.mark.parametrize('offset,facex,cover,received', [(1, -1, False, 80), (1, 1, False, 0),
                                                        (1, -1, True, 0), (5, -1, False, 0)])
def test_static_crystal_uses_flash_attenuation_and_never_blinds_owner(grenade_game, monkeypatch, offset, facex, cover, received):
    actor, other = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name='Кристалл статики')
    other.pos_x, other.pos_y = actor.pos_x+offset, actor.pos_y
    other.facing_x, other.facing_y = facex, 0
    other.character.data = {'health': {'current':700, 'effects':[]}}
    db.session.commit()
    if cover:
        monkeypatch.setattr(CombatService, '_explosion_cover', lambda *args, **kwargs: {'zones': {'head': True}})
    execute(actor, actor.character.data, {'item_id': 'reactive-artifact', 'operation_id': 'crystal-area'}, grenade_game['lobby'].gm_id)
    assert not any(effect['type'] == 'blindness' for effect in actor.character.data['health']['effects'])
    blindness = [effect for effect in other.character.data['health']['effects'] if effect['type'] == 'blindness']
    assert sum(effect['value'] for effect in blindness) == received
    assert all(effect['source'] == 'artifact_static_crystal' for effect in blindness)


def test_area_artifact_outside_location_does_not_spend():
    data = ready(name='Пиявка')
    before = deepcopy(data)
    with pytest.raises(ValidationError, match='подлокации'):
        use(data)
    assert data == before


@pytest.mark.parametrize('damaged', [False, True])
def test_second_skin_suppresses_pain_and_heals_only_without_damage(grenade_game, damaged):
    actor = grenade_game['actor_location']
    data = ready(name='Вторая кожа')
    data['health'].update(current=400, painLevel=4, max=700, radiation=0)
    actor.character.data = data
    use(data)
    path = 'skills.physical.agility'
    assert CombatService._health_roll_modifier(data, path) == 0
    if damaged:
        CombatService._apply_general_damage(actor, 20, round_number=1)
    data = actor.character.data
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 17, character_data=data)
    assert health['current'] == (380 if damaged else 400)
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert health['current'] == (380 if damaged else 550)
    assert CombatService._health_roll_modifier(data, path) == -4


def test_crystal_cloud_follows_owner_and_reduces_real_incoming_damage(grenade_game):
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    target.pos_x, target.pos_y = actor.pos_x + 3, actor.pos_y
    actor.character.data = ready(name='Алхимический веер')
    target.character.data = {'health':{'current':700,'max':700,'radiation':0}}
    db.session.commit()
    use(actor.character.data)
    assert CombatService._apply_general_damage(target, 100, damage_type='chemical')['damage'] == 50
    assert CombatService._apply_incoming_radiation(target.character.data, 10, target=target)['received'] == 5
    target.pos_x += .1
    assert CombatService._apply_general_damage(target, 100, damage_type='chemical')['damage'] == 100
    actor.pos_x += 1
    assert CombatService._apply_general_damage(target, 100, damage_type='chemical')['damage'] == 50
    health = actor.character.data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 18, character_data=actor.character.data)
    assert CombatService._apply_general_damage(target, 100, damage_type='chemical')['damage'] == 100


def test_storm_burns_nearby_electronics_and_preserves_resistant_and_owned_items(grenade_game):
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name='Штормовой камень')
    actor.character.data['inventory'] = {'pockets':[{'id':'own','name':'КПК','category':'device'}]}
    target.pos_x, target.pos_y = actor.pos_x + 2, actor.pos_y
    target.character.data = {'health':{'current':700,'max':700},'inventory':{'pockets':[
        {'id':'pda','name':'КПК','category':'device'},
        {'id':'guarded','name':'КПК','category':'device','attributes':{'anomaly_immunities':['electric']}},
    ]}}
    near = LocationObject(location_id=actor.location_id, tile_x=4, tile_y=5, type='device', properties={'electronic':True})
    far = LocationObject(location_id=actor.location_id, tile_x=20, tile_y=20, type='device', properties={'electronic':True})
    floor = LocationObject(location_id=actor.location_id, tile_x=3, tile_y=5, type='ground_item',
                           properties={'contents':[{'id':'radio','name':'Рация','category':'device'}]})
    db.session.add_all([near, far, floor]); db.session.commit()
    payload = {'item_id':'reactive-artifact', 'operation_id':'storm'}
    result = execute(actor, actor.character.data, payload, grenade_game['lobby'].gm_id)
    assert result['_affected_character_ids'] == [target.character_id]
    assert result['_object_changes'] == {near.id:'updated', floor.id:'updated'}
    pda, guarded = target.character.data['inventory']['pockets']
    assert pda['pdaState']['broken'] and not guarded.get('electronicsState')
    assert near.properties['electronicsState']['burned'] and not far.properties.get('electronicsState')
    assert floor.properties['contents'][0]['electronicsState']['burned']
    assert not actor.character.data['inventory']['pockets'][0].get('electronicsState')


@pytest.mark.parametrize('name,penalty', [('Тень чужого',3),('Лик чужого',5)])
def test_forecast_targets_only_reasonable_opponents_and_uses_separate_timers(grenade_game, monkeypatch, name, penalty):
    from app.extensions import socketio
    from app.services.artifact_area import publish
    from app.services.artifact_special import forecast_accuracy, stat_value
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name=name)
    target.pos_x, target.pos_y = actor.pos_x + 8, actor.pos_y
    target.character.data = {'health':{'current':700,'max':700}}
    if name == 'Лик чужого':
        from app.services.world_rules import mutant_character_data, mutant_profile
        target.character.data = mutant_character_data(mutant_profile('Псионик'), 'Разумный Псионик')
    db.session.commit()
    payload = {'item_id':'reactive-artifact', 'operation_id':'forecast'}
    result = execute(actor, actor.character.data, payload, grenade_game['lobby'].gm_id)
    assert result['_gm_forecast']['targets'] == [target.character.name]
    assert '_gm_forecast' not in actor.character.data[STATE_KEY]['receipts'][-1]['result']
    notices = []
    monkeypatch.setattr(socketio, 'emit', lambda event, payload, room: notices.append((event, payload, room)))
    publish(result)
    assert len(notices) == 1 and notices[0][0] == 'artifact_forecast_requested'
    assert notices[0][1]['targets'] == [target.character.name] and notices[0][2] == f"user_{grenade_game['lobby'].gm_id}"
    assert stat_value(actor.character.data,'movement_penalty') == penalty
    expected = 4 if name == 'Лик чужого' else 0
    assert forecast_accuracy(actor.character.data, target.character_id) == expected
    assert forecast_accuracy(actor.character.data, actor.character_id) == 0
    data = actor.character.data
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 18, character_data=data)
    assert stat_value(data,'movement_penalty') == 0 and forecast_accuracy(data, target.character_id) == expected
    health['effects'] = advance_timed_effects(health, health['effects'], 42, character_data=data)
    assert forecast_accuracy(data, target.character_id) == 0


def test_heavy_attack_pays_ap_when_armed_and_only_spends_on_melee_hit(grenade_game, monkeypatch):
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name='Тяжесть')
    target.character.data = {'health':{'current':700, 'max':700}}
    db.session.commit()
    monkeypatch.setattr(CombatService, '_target_armor', lambda *args, **kwargs: (60, []))
    action = {'item_id':'reactive-artifact', 'operation_id':'prepare-heavy'}
    result = CombatService.perform_action(actor.location_id, grenade_game['lobby'].gm_id, actor.id, 'artifact_special', attribute_choice=action)
    assert result['narrative_action']['action_points'] == 1 and actor.action_points_current == 19
    assert snapshot(actor.character.data)[0]['remaining_uses'] == 2
    miss = CombatService._resolve_attack(target, actor, {'hit_difficulty':12}, melee=True, attack_type='Удар кулаком', forced_roll=1)
    assert not miss['hit'] and snapshot(actor.character.data)[0]['remaining_uses'] == 2
    hit = CombatService._resolve_attack(target, actor, {'hit_difficulty':12}, melee=True, attack_type='Удар кулаком', aimed_zone='chest', forced_roll=20)
    assert hit['hit'] and hit['ignores_armor'] and hit['armor'] == 0
    assert snapshot(actor.character.data)[0]['remaining_uses'] == 1


@pytest.mark.parametrize('blocked', [False, True])
def test_plasma_cord_teleports_to_visible_point_without_opportunity_attacks(grenade_game, blocked):
    actor = grenade_game['actor_location']
    grenade_game['lobby'].game_time_minutes = 720
    actor.character.data = ready(name='Жгут плазмы')
    if blocked:
        db.session.add(LocationObject(location_id=actor.location_id, tile_x=4, tile_y=5, type='wall',
            properties={'dimensions':{'width':1,'depth':1,'height':3}}))
    db.session.commit()
    x, y = actor.pos_x + 4, actor.pos_y
    payload = {'item_id':'reactive-artifact','operation_id':'plasma', 'point':{'x':x,'y':y,'floor_level':0}}
    if blocked:
        with pytest.raises(ValidationError):
            CombatService.perform_action(actor.location_id, grenade_game['lobby'].gm_id, actor.id, 'artifact_special', attribute_choice=payload)
        assert actor.pos_x == 2.1 and actor.action_points_current == 20
        assert snapshot(actor.character.data)[0]['remaining_uses'] == 2
    else:
        result = CombatService.perform_action(actor.location_id, grenade_game['lobby'].gm_id, actor.id, 'artifact_special', attribute_choice=payload)
        assert (actor.pos_x, actor.pos_y) == (x,y) and actor.action_points_current == 19
        assert result['narrative_action']['_moves'][0]['character_id'] == actor.character_id
        assert not result.get('opportunity_attacks')
        assert snapshot(actor.character.data)[0]['remaining_uses'] == 1


def test_horizon_pull_moves_five_metres_towards_owner(grenade_game):
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name='Глаз горизонта')
    target.pos_x, target.pos_y = actor.pos_x+8, actor.pos_y
    target.character.data = {'health':{'current':700,'max':700}}
    db.session.commit()
    result = execute(actor, actor.character.data, {'item_id':'reactive-artifact','operation_id':'pull',
                     'target_id':target.character_id}, grenade_game['lobby'].gm_id)
    assert target.pos_x == pytest.approx(actor.pos_x+3) and target.pos_y == actor.pos_y
    assert result['_moves'][0]['character_id'] == target.character_id
    assert target.character.data['health']['current'] == 700


@pytest.mark.parametrize('name,damage', [('Железное сердце',75),('Свет горизонта',50),('Ядро возгорания',0)])
def test_waves_stop_at_wall_and_apply_their_own_effects(grenade_game, name, damage):
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name=name)
    actor.character.data['health']['effects'] = [{'type':'burning','remaining':3,'source':'test-fire'}]
    target.pos_x, target.pos_y = 4, actor.pos_y
    target.character.data = {'health':{'current':700,'max':700,'zones':{'chest':{'current':150,'max':150}}}}
    db.session.add(LocationObject(location_id=actor.location_id, tile_x=8, tile_y=5, type='wall',
        properties={'dimensions':{'width':1,'depth':1,'height':3}}))
    db.session.commit()
    result = execute(actor, actor.character.data, {'item_id':'reactive-artifact','operation_id':'wave'}, grenade_game['lobby'].gm_id)
    assert 4 < target.pos_x < 8 and target.pos_y == actor.pos_y
    assert target.character.data['health']['current'] == 700-damage
    assert target.character.data['health']['zones']['chest']['current'] == 150-damage
    assert result['_moves'][0]['character_id'] == target.character_id
    assert any(effect['type'] == 'burning' for effect in actor.character.data['health']['effects']) == bool(damage)


def test_world_wave_hits_only_other_members_of_group_without_relocation(grenade_game):
    from app.models import WorldGroup
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name='Свет горизонта')
    target.character.data = {'health':{'current':700,'max':700,'zones':{'chest':{'current':150,'max':150}}}}
    group = WorldGroup(lobby_id=actor.character.lobby_id, name='Группа', tile_x=0, tile_y=0,
        member_character_ids=[actor.character_id,target.character_id], created_by=grenade_game['lobby'].gm_id)
    db.session.add(group); db.session.commit()
    before = target.pos_x, target.pos_y
    result = execute(SimpleNamespace(character=actor.character), actor.character.data,
        {'item_id':'reactive-artifact','operation_id':'world-wave'}, grenade_game['lobby'].gm_id)
    assert result['hits'] == [{'character_id':target.character_id,'damage':50}]
    assert not result['_moves'] and (target.pos_x,target.pos_y) == before
    assert target.character.data['health']['zones']['chest']['current'] == 100


@pytest.mark.parametrize('name', ['Пиявка','Кристалл статики'])
def test_world_area_effects_apply_to_group_with_owner_exceptions(grenade_game, name):
    from app.models import WorldGroup
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name=name)
    target.character.data = {'health':{'current':700,'max':700,'radiation':15}}
    group = WorldGroup(lobby_id=actor.character.lobby_id, name='Группа', tile_x=0, tile_y=0,
        member_character_ids=[actor.character_id,target.character_id], created_by=grenade_game['lobby'].gm_id)
    db.session.add(group); db.session.commit()
    result = execute(SimpleNamespace(character=actor.character), actor.character.data,
        {'item_id':'reactive-artifact','operation_id':'world-area'}, grenade_game['lobby'].gm_id)
    assert result['_affected_character_ids'] == [target.character_id]
    if name == 'Пиявка':
        assert actor.character.data['health']['radiation'] == target.character.data['health']['radiation'] == 0
    else:
        assert not any(effect['type']=='blindness' for effect in actor.character.data['health']['effects'])
        health = target.character.data['health']
        blindness, = [effect for effect in health['effects'] if effect['type']=='blindness']
        assert blindness['value'] == 100
        assert not advance_timed_effects(health, health['effects'], 30, character_data=target.character.data)


@pytest.mark.parametrize('name,damage', [('Громоотвод', 100), ('Проводник бури', 175)])
def test_electric_artifact_spends_once_and_applies_electric_protection(grenade_game, name, damage):
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name=name)
    target.pos_x, target.pos_y = actor.pos_x+4, actor.pos_y
    target.character.data = {'health': {'current':700, 'max':700, 'effects':[]},
                             'equipment': {'armor': {'name':'Изолированная броня', 'protection':{'electric':50}}}}
    db.session.commit()
    payload = {'item_id':'reactive-artifact', 'operation_id':'electric-rod', 'target_id':target.character_id}
    user_id = grenade_game['lobby'].gm_id
    result = CombatService.perform_action(actor.location_id, user_id, actor.id, 'artifact_special', attribute_choice=payload)
    assert result['narrative_action']['damage'] == damage and actor.action_points_current == 19
    assert target.character.data['health']['current'] == 700-damage
    repeated = CombatService.perform_action(actor.location_id, user_id, actor.id, 'artifact_special', attribute_choice=payload)
    assert repeated['narrative_action']['replayed'] and actor.action_points_current == 19
    assert target.character.data['health']['current'] == 700-damage


@pytest.mark.parametrize('name,reason', [
    ('Громоотвод', reason) for reason in ('distance', 'behind', 'cover', 'hidden', 'floor')
] + [('Проводник бури', 'sight')])
def test_electric_artifact_rejects_unseen_or_out_of_range_target_before_cost(grenade_game, monkeypatch, name, reason):
    actor, target = grenade_game['actor_location'], grenade_game['target_location']
    actor.character.data = ready(name=name)
    target.pos_x, target.pos_y = actor.pos_x+4, actor.pos_y
    if reason == 'distance':
        target.pos_x = actor.pos_x+25
    elif reason == 'sight':
        target.pos_x = actor.pos_x+28
    elif reason == 'behind':
        target.pos_x = actor.pos_x-1
    elif reason == 'cover':
        monkeypatch.setattr(CombatService, '_cover_analysis', lambda *args, **kwargs: {'grade':'full'})
    elif reason == 'hidden':
        target.gm_hidden = True
    elif reason == 'floor':
        target.floor_level = 1
    db.session.commit()
    payload = {'item_id':'reactive-artifact', 'operation_id':'invalid-rod', 'target_id':target.character_id}
    with pytest.raises(ValidationError):
        CombatService.perform_action(actor.location_id, grenade_game['lobby'].gm_id, actor.id, 'artifact_special', attribute_choice=payload)
    assert actor.action_points_current == 20
    assert snapshot(actor.character.data)[0]['remaining_uses'] == 2


def magnet_scene(game, level=15):
    actor, target = game['actor_location'], game['target_location']
    actor.character.data = ready(level, 'Ферромагнит')
    target.pos_x, target.pos_y = 6, 5
    game['lobby'].game_time_minutes = 12 * 60
    return actor, target


def test_magnet_targets_and_transfers_one_visible_unit_through_http(grenade_game, client, auth_headers):
    actor, _ = magnet_scene(grenade_game)
    LocationCombatState.query.filter_by(location_id=actor.location_id).first().status = 'inactive'
    items = [{'id':'metal-stack', 'name':'Металлические детали', 'weight':1, 'quantity':3},
             {'id':'glass', 'name':'Стекло', 'weight':.2},
             {'id':'heavy', 'name':'Тяжёлая сталь', 'weight':1.01, 'attributes':{'material':'Сталь'}},
             {'id':'rgd5', 'name':'Граната РГД-5', 'weight':0, 'category':'grenade'},
             {'id':'molotov', 'name':'Коктейль Молотова', 'weight':.5, 'category':'grenade'}]
    ground = LocationObject(location_id=actor.location_id, type='ground_item', tile_x=6, tile_y=5,
                            properties={'contents':items})
    db.session.add(ground)
    db.session.commit()
    url = f'/lobbies/characters/{actor.character_id}/artifact-special'
    headers = auth_headers(grenade_game['actor_user'])
    query = f'?location_id={actor.location_id}&item_id=reactive-artifact'
    choices = client.get(url+'/targets'+query, headers=headers).get_json()['targets']
    assert [choice['label'] for choice in choices] == ['Металлические детали · земля · 6, 5', 'Граната РГД-5 · земля · 6, 5']
    payload = {'item_id':'reactive-artifact', 'operation_id':'catch-stack', 'location_id':actor.location_id,
               'character_revision':actor.character.revision, 'magnet_target':choices[0]['id']}
    response = client.post(url, json=payload, headers=headers)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    caught, = body['data']['inventory']['held']
    assert caught['quantity'] == 1 and caught['id'] != 'metal-stack' and not body['result']['at_feet']
    assert ground.properties['contents'][0]['id'] == 'metal-stack' and ground.properties['contents'][0]['quantity'] == 2
    assert body['data']['_artifact_specials'][0]['remaining_uses'] == 1
    assert body['data']['_artifact_specials'][0]['cooldown_seconds'] == DAY
    assert client.post(url, json=payload, headers=headers).get_json()['result']['replayed']
    assert len(actor.character.data['inventory']['held']) == 1
    assert ground.properties['contents'][0]['quantity'] == 2
    moved = actor.character.data_snapshot()
    moved['inventory']['pockets'] = [moved['inventory']['held'].pop()]
    saved = client.put(f'/lobbies/characters/{actor.character_id}', json={'data':moved}, headers=headers)
    assert saved.status_code == 200, saved.get_json()
    assert actor.character.data['inventory']['pockets'][0]['id'] == caught['id']
    assert not actor.character.data['inventory']['held']


def test_magnet_takes_drawn_weapon_and_drops_it_by_owner_when_hands_are_full(grenade_game):
    from app.services.artifact_magnet import targets
    actor, target = magnet_scene(grenade_game, 9)
    actor.character.data['weapons'] = [{'id':'rifle', 'name':'Винтовка', 'category':'weapon', 'weight':4}]
    actor.drawn_weapon_index = 0
    pistol = {'id':'pistol', 'name':'Пистолет', 'category':'weapon', 'weight':.9,
              'installedMagazine':{'id':'mag', 'category':'magazine', 'emptyWeight':.1, 'ammo':[]}}
    target.character.data = {'health':{'current':700, 'max':700}, 'weapons':[pistol], 'activeWeaponIndex':0}
    target.drawn_weapon_index = 0
    db.session.commit()
    choice, = targets(actor, actor.character.data)
    assert 'Пистолет · в руках' in choice['label']
    result = CombatService.perform_action(actor.location_id, grenade_game['lobby'].gm_id, actor.id, 'artifact_special',
        attribute_choice={'item_id':'reactive-artifact', 'operation_id':'take-pistol', 'magnet_target':choice['id']})
    assert result['narrative_action']['at_feet'] and actor.action_points_current == 18
    assert target.drawn_weapon_index is None and target.character.data['weapons'] == []
    assert not (actor.character.data.get('inventory') or {}).get('held')
    ground = LocationObject.query.filter_by(location_id=actor.location_id, tile_x=2, tile_y=5, type='ground_item').one()
    assert ground.properties['contents'] == [pistol]


def test_magnet_takes_a_device_held_from_an_equipment_pouch(grenade_game):
    from app.services.artifact_magnet import targets
    actor, target = magnet_scene(grenade_game)
    lamp = {'id':'lamp', 'name':'Фонарик', 'weight':.5, 'attributes':{'material':'Алюминий'}}
    target.character.data = {'health':{'current':700, 'max':700,
        'combatMeta':{'portableLights':[{'id':'lamp','mount':'hand','on':True}]}},
        'equipment':{'vest':{'pouches':[{'contents':[lamp]}]}}}
    db.session.commit()
    choice, = targets(actor, actor.character.data)
    result = CombatService.perform_action(actor.location_id, grenade_game['lobby'].gm_id, actor.id, 'artifact_special',
        attribute_choice={'item_id':'reactive-artifact', 'operation_id':'take-lamp', 'magnet_target':choice['id']})
    assert not result['narrative_action']['at_feet']
    assert actor.character.data['inventory']['held'][0]['id'] == 'lamp'
    assert not target.character.data['equipment']['vest']['pouches'][0]['contents']
    assert not target.character.data['health']['combatMeta']['portableLights']


@pytest.mark.parametrize('blocked_by', ['wall', 'smoke', 'night', 'hidden', 'floor', 'turned', 'stale'])
def test_magnet_rechecks_visibility_and_item_state_before_spending(grenade_game, blocked_by):
    from app.services.artifact_magnet import targets
    actor, _ = magnet_scene(grenade_game)
    ground = LocationObject(location_id=actor.location_id, type='ground_item', tile_x=6, tile_y=5,
        properties={'contents':[{'id':'steel', 'name':'Образец', 'weight':.5, 'attributes':{'metallic':True}}]})
    db.session.add(ground)
    db.session.commit()
    choice, = targets(actor, actor.character.data)
    if blocked_by == 'wall':
        db.session.add(LocationObject(location_id=actor.location_id, type='wall', tile_x=4, tile_y=5,
            properties={'dimensions':{'width':1,'depth':1,'height':3}}))
    elif blocked_by == 'smoke':
        LocationCombatState.query.filter_by(location_id=actor.location_id).one().area_effects = [
            {'type':'smoke', 'x':4, 'y':5, 'radius':1}]
    elif blocked_by == 'night':
        grenade_game['lobby'].game_time_minutes = 0
    elif blocked_by == 'turned':
        actor.facing_x = -1
    else:
        key, value = {'hidden':('gm_hidden',True), 'floor':('floor_level',1), 'stale':('test_revision',1)}[blocked_by]
        ground.properties = {**ground.properties, key:value}
    db.session.commit()
    assert choice not in targets(actor, actor.character.data)
    with pytest.raises(ValidationError, match='Предмет недоступен'):
        CombatService.perform_action(actor.location_id, grenade_game['lobby'].gm_id, actor.id, 'artifact_special',
            attribute_choice={'item_id':'reactive-artifact', 'operation_id':'invalid-magnet', 'magnet_target':choice['id']})
    assert actor.action_points_current == 20 and snapshot(actor.character.data)[0]['remaining_uses'] == 2


def test_fiery_tear_removes_darkness_penalties_for_exactly_ten_minutes(monkeypatch):
    data = ready(name='Огненная слеза')
    monkeypatch.setattr('app.services.combat.random.randint', lambda *_: 10)
    visual = lambda: CombatService._narrative_skill_check(data, 'skills.physical.awareness.visual', darkness=True)
    shooting = lambda: CombatService._narrative_skill_check(data, 'skills.physical.shooting', darkness=True)
    assert visual()['darkness'] and visual()['equipment_modifier'] == shooting()['equipment_modifier'] == -5
    use(data)
    assert not visual()['darkness'] and visual()['equipment_modifier'] == shooting()['equipment_modifier'] == 0
    assert CombatService._narrative_skill_check(data, 'skills.physical.shooting', darkness=True, forced_disadvantage=True)['disadvantage']
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 599, character_data=data)
    assert visual()['equipment_modifier'] == 0
    health['effects'] = advance_timed_effects(health, health['effects'], 1, character_data=data)
    assert visual()['equipment_modifier'] == shooting()['equipment_modifier'] == -5


@pytest.mark.parametrize('key,restored', [('vspishka', 200), ('varevo', 150), ('glukhar', 10), ('batut', 140)])
def test_philosophers_leaf_converts_real_anomaly_damage_only_to_common_health(grenade_game, monkeypatch, key, restored):
    actor = grenade_game['actor_location']
    actor.character.data = ready(name='Философский лист')
    actor.character.data['health']['current'] = 300
    use(actor.character.data)
    monkeypatch.setattr('app.services.combat.random.randint', lambda low, high: high)
    result = CombatService._apply_anomaly_exposure(actor, {'key': key}, apply_category_effect=False)
    assert result['damage'] == 0 and result['anomaly_healing'] == restored
    health = actor.character.data['health']
    assert health['current'] == 300+restored
    assert CombatService._apply_general_damage(actor, 50, damage_type='electric')['damage'] == 50
    health = actor.character.data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 30, character_data=actor.character.data)
    outcome = CombatService._apply_anomaly_exposure(actor, {'key': 'vspishka'}, apply_category_effect=False)
    assert outcome['damage'] == 200 and 'anomaly_healing' not in outcome


def test_fire_gift_cost_survives_removal_and_cannot_be_forged_removed_or_reduced_by_healing_modifier():
    data = ready(name='Дар пламени')
    use(data)
    cost = next(e for e in data['health']['effects'] if e['type'] == 'delayed_adjustment')
    forged = deepcopy(data)
    forged['health']['effects'] = []
    preserve(data, forged)
    assert cost in forged['health']['effects']
    data['equipment'] = equipped('Чернобыльская губка')['equipment']
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 18, character_data=data)
    assert health['current'] == 0
    assert CombatService._character_condition(data)['state'] == 'dead'


def armored_live_shield():
    data = ready(name='Живой щит')
    data['health'].update(current=700, radiation=0)
    data['equipment']['armor'].update(name='Проверочная броня', category='armor', stage=2,
        stageDurability=1000, currentStageDurability=300, protection={'physical':50}, attributes={})
    return data


def test_live_shield_repairs_each_hit_only_within_current_stage_and_expires():
    data = armored_live_shield()
    armor = data['equipment']['armor']
    use(data)
    first = CombatService._damage_armor_item(armor, {}, 0, character_data=data, raw_damage=50)
    assert (first['damage'], first['restored'], armor['currentStageDurability']) == (0, 50, 350)
    assert use(data)['replayed']
    assert armor['currentStageDurability'] == 350
    second = CombatService._damage_armor_item(armor, {}, 5000, character_data=data)
    assert (second['damage'], second['restored'], armor['stage'], armor['currentStageDurability']) == (0, 650, 2, 1000)
    broken = {**armor, 'stage':5, 'currentStageDurability':0, 'durability':10, 'brokenDamage':40}
    before = deepcopy(broken)
    assert CombatService._damage_armor_item(broken, {}, 500, character_data=data)['damage'] == 0
    assert broken == before
    health = data['health']
    health['effects'] = advance_timed_effects(health, health['effects'], 60, character_data=data)
    after = CombatService._damage_armor_item(armor, {}, 80, character_data=data, raw_damage=150)
    assert after['damage'] == 80 and armor['currentStageDurability'] == 920


@pytest.mark.parametrize('source,repair', [('bullet',50), ('fragment',100), ('blast',100), ('anomaly',100), ('burning',50)])
def test_live_shield_restores_raw_damage_from_actual_armor_paths(grenade_game, monkeypatch, source, repair):
    target, attacker = grenade_game['actor_location'], grenade_game['target_location']
    target.character.data = armored_live_shield()
    use(target.character.data)
    if source == 'bullet':
        attacker.character.data = {'weapons':[{}], 'health':{'current':700, 'max':700}}
        monkeypatch.setattr(CombatService, '_ranged_damage_profile', lambda weapon: ({
            'damage':50, 'armor_piercing':55, 'bleeding':'', 'effective_range':100, 'damage_type':'bullet'}, None))
        result = CombatService._resolve_attack(target, attacker,
            {'weapon_index':0, 'hit_difficulty':1, 'fire_mode':'single', 'round_number':1},
            aimed_zone='chest', forced_roll=20)
        assert result['hit'] and result['armor_damage'][0]['restored'] == repair
        assert target.character.data['health']['current'] < 700
    elif source == 'fragment':
        result = CombatService._explosion_fragment_hit(target, 100, 10, 'chest', {}, 1)
        assert result['armor_damage'][0]['restored'] == repair
    elif source == 'blast':
        from test_grenade_rules import quiet_explosion, profile
        quiet_explosion(monkeypatch)
        result = CombatService.resolve_explosion(target.location_id, target.pos_x-.5, target.pos_y,
                                                 profile(blast_base=100))
        hit = next(row for row in result['targets'] if row['character_id'] == target.character_id)
        assert 0 < hit['blast_damage'] < repair
        assert hit['blast_armor_damage'][0]['restored'] == repair
    elif source == 'anomaly':
        result = CombatService._apply_anomaly_equipment_damage(target.character.data, 100,
            {'category':'thermal', 'armor_damage_multiplier':2})
        assert result[0]['restored'] == repair
    else:
        from app.services.burning import ignite, tick
        ignite(target, 2, source='Проверка Живого щита')
        tick(target, 1)
        assert target.character.data['health']['current'] == 650
    assert target.character.data['equipment']['armor']['currentStageDurability'] == 300 + repair
