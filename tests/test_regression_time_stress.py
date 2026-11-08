"""T+: новые проверки времени и стресса. До отдельного допуска NOT_RUN.

Источник: карточка jz0xjcFv и docs/REFACTORING_CHECKLIST.md, пункты91/93.
Fixtures создают только синтетические исходные health/effects в guarded app.
Ходы и глобальное время затем меняются штатными API, без подстановки таймеров.
"""
from collections import deque
from copy import deepcopy

import pytest
from sqlalchemy.orm.attributes import flag_modified

import app.services.combat as combat_module
from app.extensions import db
from app.models import LobbyCharacter
from app.services.combat import CombatService
from app.services.effects import advance_timed_effects
from test_character_interactions import _setup_pair


def test_global_pain_recovers_per_six_seconds_and_preserves_fractional_time():
    health={'painLevel':5,'effects':[],'combatMeta':{'painIncreased':True}}
    def tick(seconds):
        health['effects']=advance_timed_effects(health,health['effects'],seconds,advance_medication_turns=True,recover_pain=True)
    tick(5);assert health['painLevel']==5
    tick(1);assert health['painLevel']==4
    tick(12);assert health['painLevel']==2
    tick(600);assert health['painLevel']==0


def test_global_pain_recovery_respects_tough_nut_before_periodic_artifact_pulse():
    data={'health':{'painLevel':8,'effects':[],'combatMeta':{'toughNut':{'minimumPain':2}}},
        'equipment':{'armor':{'containers':[{'item':{'name':'Болевой артефакт','category':'artifact',
            'attributes':{'negative_effect':'Усиливает боль на 1 уровень за перемещение'}}}]}}}
    health=data['health']
    health['effects']=advance_timed_effects(health,health['effects'],599,advance_medication_turns=True,
        recover_pain=True,character_data=data)
    assert health['painLevel']==2
    health['effects']=advance_timed_effects(health,health['effects'],1,advance_medication_turns=True,
        recover_pain=True,character_data=data)
    assert health['painLevel']==3


def test_periodic_pain_prevents_recovery_in_same_interval_but_not_after_source_expires():
    health={'painLevel':5,'effects':[{'type':'periodic_adjustment','name':'Постоянная боль','remaining':3,
        'tick':'turn_end','source':'consumable-template:1','adjustments':[{'field':'painLevel','delta':1,'max':10}]}]}
    for expected in (6,7,8):
        health['effects']=advance_timed_effects(health,health['effects'],6,advance_medication_turns=True,recover_pain=True)
        assert health['painLevel']==expected
    health['effects']=advance_timed_effects(health,health['effects'],6,advance_medication_turns=True,recover_pain=True)
    assert health['painLevel']==7


def test_gm_clock_reduces_surgical_pain_without_changing_existing_shock_recovery(client,time_case,auth_headers):
    end_combat(client,time_case,auth_headers)
    character=time_case['actor_character']
    seed_initial_health(character,[{'type':'shock','source':'maximum_pain','tick':'manual'}])
    character.data['health']['painLevel']=10;flag_modified(character,'data');db.session.commit()
    advance_global_minutes(client,time_case,auth_headers,1)
    health=persisted_health(character.id)
    assert health['painLevel']==0
    assert any(e['type']=='shock' for e in health['effects'])


@pytest.mark.parametrize('before, after', [(0, 0), (1, 1), (2, 1), (5, 3), (9, 5), (10, 5)])
def test_daily_sleep_removes_half_exhaustion_rounded_down(before, after):
    from app.lobbies import _resolve_character_day
    health = {'exhaustion': before, 'needs': {'mealsToday': 3, 'drinksToday': 3}}
    _resolve_character_day(health, [], slept=True)
    assert health['exhaustion'] == after
    assert health['needs']['missedDays'] == {'food': 0, 'water': 0, 'sleep': 0}


def test_daily_needs_accumulate_independently_and_reset_only_satisfied_need():
    from app.lobbies import _resolve_character_day
    health = {'exhaustion': 0, 'needs': {'mealsToday': 0, 'drinksToday': 3, 'sleptToday': True}}
    _resolve_character_day(health, [], slept=False)
    assert health['exhaustion'] == 1
    health['needs'].update(drinksToday=3, sleptToday=True)
    _resolve_character_day(health, [], slept=False)
    assert health['exhaustion'] == 3
    assert health['needs']['missedDays'] == {'food': 2, 'water': 0, 'sleep': 0}
    health['needs'].update(mealsToday=3, sleptToday=True)
    _resolve_character_day(health, [], slept=False)
    assert health['exhaustion'] == 4
    assert health['needs']['missedDays'] == {'food': 0, 'water': 1, 'sleep': 0}
    assert health['needs']['lastDay']['missed'] == ['вода']


def test_daily_needs_keep_independent_sleep_streak_and_exhaustion_cap():
    from app.lobbies import _resolve_character_day
    health = {'exhaustion': 0, 'needs': {}}
    _resolve_character_day(health, [], slept=False)
    assert health['exhaustion'] == 3
    _resolve_character_day(health, [], slept=False)
    assert health['exhaustion'] == 9
    _resolve_character_day(health, [], slept=False)
    assert health['exhaustion'] == 10
    assert health['needs']['missedDays'] == {'food': 3, 'water': 3, 'sleep': 3}


@pytest.mark.parametrize('value', [None, 'bad', float('inf'), {}, -2])
def test_daily_need_streak_normalization_accepts_legacy_and_malformed_values(value):
    from app.lobbies import _normalize_character_needs
    assert _normalize_character_needs({'needs': {'missedDays': {'food': value}}})['missedDays'] == {
        'food': 0, 'water': 0, 'sleep': 0,
    }


def test_sleep_api_persists_need_streak_across_reload(client, time_case, auth_headers):
    case = time_case
    character = case['actor_character']
    data = deepcopy(character.data)
    data['health'].update(exhaustion=5, needs={
        'mealsToday': 0, 'drinksToday': 3, 'missedDays': {'food': 1},
    })
    character.data = data
    character.time_active = True
    flag_modified(character, 'data')
    db.session.commit()
    response = client.post(
        f"/lobbies/{case['lobby'].id}/rest", headers=auth_headers(case['actor_user']),
        json={'type': 'sleep', 'character_ids': [character.id]},
    )
    assert response.status_code == 200, response.get_json()
    health = persisted_health(character.id)
    assert health['exhaustion'] == 5  # 5 − floor(5/2) + второй день без еды.
    assert health['needs']['missedDays'] == {'food': 2, 'water': 0, 'sleep': 0}


class ScriptedRandint:
    """Проверяет не только значение, но и диапазон/количество реальных бросков."""

    def __init__(self, draws):
        self.pending = deque(draws)
        self.calls = []

    def __call__(self, low, high):
        assert self.pending, f"Неожиданный бросок d{high}"
        expected_low, expected_high, value = self.pending.popleft()
        assert (low, high) == (expected_low, expected_high)
        assert low <= value <= high
        self.calls.append((low, high, value))
        return value

    def assert_exhausted(self):
        assert not self.pending, "Сервис пропустил ожидаемый бросок"


def minute_organ_effect():
    # Канонический минутный organ_failure из _apply_organ_damage для сердца.
    # Начальный эффект задаётся напрямую: тест времени не проверяет нанесение травмы.
    return {
        "id": "time-minute-organ", "type": "organ_failure", "area": "heart",
        "name": "Смертельное повреждение органа", "source": "organ_damage",
        "tick": "time_elapsed", "remaining": 1, "time_unit": "minute",
        "remaining_seconds": 60, "duration_seconds": 60, "death_on_expire": True,
    }


def resistance_effect(kind):
    assert kind in {"suppressor", "harmony"}
    effect = {
        "id": "time-" + kind, "type": "stress_resistance", "active": True,
        "source": "Подавитель эмоций" if kind == "suppressor" else "Гармония",
        "stress_advantage": True,
        "stress_attack_block_chance": 50 if kind == "suppressor" else 0,
    }
    if kind == "suppressor":
        effect.update(tick="movement_end", remaining=6)
    else:
        effect.update(tick="time_elapsed", time_unit="minute", remaining=30,
                      remaining_seconds=1800)
    return effect


@pytest.fixture
def time_case(client, create_user, auth_headers):
    return _setup_pair(client, create_user, auth_headers)


def test_weather_changes_once_per_game_hour_and_preserves_emission(client,time_case,auth_headers,monkeypatch):
    import app.services.weather_rules as weather
    end_combat(client,time_case,auth_headers)
    lobby=time_case['lobby']
    lobby.game_time_minutes=59
    lobby.weather_settings={'rain':{'enabled':False,'intensity':.2},'emission':{'enabled':True,'intensity':.8}}
    db.session.commit()
    choices=iter(['rain',1,'rain',1])
    monkeypatch.setattr(weather.random,'choice',lambda _:next(choices))
    advance_global_minutes(client,time_case,auth_headers,1)
    assert lobby.weather_settings['rain']=={'enabled':True,'intensity':.3}
    advance_global_minutes(client,time_case,auth_headers,59)
    assert lobby.weather_settings['rain']['intensity']==.3
    advance_global_minutes(client,time_case,auth_headers,1)
    assert lobby.weather_settings['rain']['intensity']==.4
    assert lobby.weather_settings['emission']=={'enabled':True,'intensity':.8}


def test_travel_weather_step_enables_one_parameter_and_clamps(time_case):
    from app.services.weather_rules import change
    class Choices:
        def __init__(self,*values):self.values=iter(values)
        def choice(self,_):return next(self.values)
    lobby=time_case['lobby']
    lobby.weather_settings={'sun':{'enabled':False,'intensity':.7},'rain':{'enabled':False,'intensity':.1}}
    changed=change(lobby,.5,rng=Choices('sun',1))
    assert changed=={'parameter':'sun','before':.7,'after':1,'delta':.3}
    assert lobby.weather_settings['sun']['enabled']
    assert lobby.weather_settings['rain']=={'enabled':False,'intensity':.1}


@pytest.mark.parametrize('key',['weather','weather_decrease'])
def test_actual_world_travel_event_changes_weather_instead_of_only_text(client,time_case,auth_headers,monkeypatch,key):
    from app.services.world_travel_events import OUTCOMES
    from app.models import WorldTravelEvent
    import app.services.weather_rules as weather
    end_combat(client,time_case,auth_headers)
    lobby=time_case['lobby'];headers=auth_headers(time_case['actor_user'])
    lobby.game_time_minutes=480
    lobby.weather_settings={'rain':{'enabled':False,'intensity':.1}}
    db.session.commit()
    group=client.post(f'/lobbies/{lobby.id}/world-groups',headers=headers,
        json={'name':'Погодный переход','tile_x':5,'tile_y':5}).get_json()
    monkeypatch.setattr('app.lobbies.roll_travel_event',lambda _:next(o for o in OUTCOMES if o.key==key))
    choices=iter(['rain',1]);monkeypatch.setattr(weather.random,'choice',lambda _:next(choices))
    response=client.post(f'/lobbies/{lobby.id}/world-groups/{group["id"]}/move',headers=headers,json={'tile_x':6,'tile_y':5})
    assert response.status_code==200,response.json
    db.session.refresh(lobby)
    assert lobby.weather_settings['rain']=={'enabled':True,'intensity':.6}
    event=WorldTravelEvent.query.filter_by(group_id=group['id']).one()
    assert 'Дождь: 0.1 → 0.6' in event.description


def test_rest_crosses_every_hour_for_weather(client,time_case,auth_headers,monkeypatch):
    import app.services.weather_rules as weather
    end_combat(client,time_case,auth_headers)
    lobby=time_case['lobby'];lobby.game_time_minutes=59
    lobby.weather_settings={'fog':{'enabled':False,'intensity':.5}};db.session.commit()
    choices=iter(['fog',1,'fog',-1]*4);monkeypatch.setattr(weather.random,'choice',lambda _:next(choices))
    response=client.post(f'/lobbies/{lobby.id}/rest',headers=auth_headers(time_case['actor_user']),
        json={'type':'sleep','character_ids':[time_case['actor_character'].id]})
    assert response.status_code==200,response.json
    assert lobby.weather_settings['fog']=={'enabled':True,'intensity':.5}
    assert list(choices)==[]  # Все восемь часовых границ обработаны, не только последняя.


def seed_initial_health(character, effects, *, stress=0, time_active=True):
    # Вызывается только для исходного состояния до тестируемых игровых действий.
    data = deepcopy(character.data)
    health = data.setdefault("health", {})
    health.update(effects=deepcopy(effects), stress=stress, combatMeta={})
    data.setdefault("skills", {}).setdefault("physical", {})["will"] = {"base": 10, "bonus": 0}
    character.data = data
    character.time_active = time_active
    flag_modified(character, "data")
    db.session.commit()


def persisted_health(character_id):
    db.session.expire_all()
    return deepcopy(db.session.get(LobbyCharacter, character_id).data["health"])


def effect_by_id(health, effect_id):
    return next((effect for effect in health.get("effects", []) if effect.get("id") == effect_id), None)


def assert_minute_remaining(health, seconds):
    effect = effect_by_id(health, "time-minute-organ")
    assert effect is not None
    assert effect["remaining_seconds"] == seconds
    assert not any(effect.get("type") == "death" for effect in health["effects"])


def assert_organ_expired(health):
    assert effect_by_id(health, "time-minute-organ") is None
    assert any(effect.get("type") == "death" and effect.get("source") == "organ_damage"
               for effect in health["effects"])


def combat_url(case):
    return f"/lobbies/{case['lobby'].id}/locations/{case['location'].id}/combat"


def end_turn(client, case, auth_headers, who):
    response = client.post(
        combat_url(case) + "/end_turn", headers=auth_headers(case[who + "_user"]),
        json={"location_character_id": case[who + "_location"].id},
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def end_combat(client, case, auth_headers):
    response = client.post(combat_url(case) + "/end", headers=auth_headers(case["actor_user"]))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["status"] == "idle"


def advance_global_minutes(client, case, auth_headers, minutes):
    assert type(minutes) is int and minutes > 0
    db.session.refresh(case["lobby"])
    lobby = case["lobby"]
    previous = (lobby.game_day or 1) * 1440 + (lobby.game_time_minutes or 0)
    day, minute = divmod(previous + minutes, 1440)
    response = client.patch(
        f"/lobbies/{lobby.id}/time", headers=auth_headers(case["actor_user"]),
        json={"game_day": day, "game_time_minutes": minute},
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json() == {"game_day": day, "game_time_minutes": minute}


def test_global_clock_completes_medication_without_a_combat_turn(client, time_case, auth_headers):
    character = time_case['actor_character']
    seed_initial_health(character, [
        {'type': 'regeneration', 'value': 10, 'remaining': 10, 'tick': 'turn_end', 'source': 'consumable-template:1'},
        {'type': 'radiation_treatment', 'value': -2.5, 'remaining': 10, 'tick': 'turn_end', 'source': 'consumable-template:2'},
    ])
    character.data['health'].update(current=100, max=700, radiation=25)
    flag_modified(character, 'data'); db.session.commit()
    advance_global_minutes(client, time_case, auth_headers, 10)
    health = persisted_health(character.id)
    assert health['current'] == 200
    assert health['radiation'] == 0
    assert not health['effects']
    advance_global_minutes(client, time_case, auth_headers, 10)
    assert persisted_health(character.id)['current'] == 200


def test_delayed_morphine_activates_in_real_turn_and_blocks_then_releases_pain(
        client,time_case,auth_headers):
    from app.services.consumable_effects import parse_consumable_effects
    from app.services.general_consumable import _apply_profile
    from app.services.effects import apply_effect_to_health
    character=time_case['actor_character']
    seed_initial_health(character,[])
    health=character.data['health']
    health.update(painLevel=5,stress=3,exhaustion=2)
    profile=parse_consumable_effects('Морфин. Ампула.')
    _apply_profile(health,profile,profile['direct'],{'name':'Морфин','templateId':42},in_combat=True)
    flag_modified(character,'data');db.session.commit()
    end_turn(client,time_case,auth_headers,'actor')
    health=persisted_health(character.id)
    assert health['painLevel']==2 and health['stress']==1
    blocks=[e for e in health['effects'] if e.get('blocks_new_pain')]
    assert blocks, health['effects']
    block=blocks[0]
    assert block['remaining']==3 and block['medication_clock'] is True
    apply_effect_to_health(health,{'type':'pain','value':2})
    assert health['painLevel']==2
    assert not health['combatMeta'].get('blockedPain')
    character.data={**character.data,'health':health};flag_modified(character,'data');db.session.commit()
    for _ in range(3):
        end_turn(client,time_case,auth_headers,'target')
        end_turn(client,time_case,auth_headers,'actor')
    health=persisted_health(character.id)
    assert not any(e.get('blocks_new_pain') for e in health['effects'])
    pain_before=health['painLevel']
    apply_effect_to_health(health,{'type':'pain','value':2})
    assert health['painLevel']==pain_before+2


def test_orel_buff_and_crash_each_last_full_character_turns(client,time_case,auth_headers):
    from app.services.consumable_effects import parse_consumable_effects
    from app.services.general_consumable import _apply_profile
    character=time_case['actor_character']
    seed_initial_health(character,[])
    profile=parse_consumable_effects('Стимулятор Орёл. Ампула. Действует 10 ходов.')
    _apply_profile(character.data['health'],profile,profile['direct'],{'name':'Стимулятор Орёл','templateId':42},in_combat=True)
    flag_modified(character,'data');db.session.commit()
    for turn in range(1,14):
        assert CombatService._consumable_stat_value_bonus(character.data,'accuracy')==(3 if turn<=10 else -5)
        end_turn(client,time_case,auth_headers,'actor')
        expected=3 if turn<10 else -5 if turn<13 else 0
        assert CombatService._consumable_stat_value_bonus(character.data,'accuracy')==expected
        end_turn(client,time_case,auth_headers,'target')
        assert CombatService._consumable_stat_value_bonus(character.data,'accuracy')==expected


def test_movement_modifier_counts_actual_moves_and_not_rounds(client,time_case,auth_headers):
    from app.services.general_consumable import _apply_profile
    character=time_case['actor_character'];actor=time_case['actor_location']
    seed_initial_health(character,[])
    profile={'direct':{},'modifiers':[{'stat':'awareness','value':6,'remaining':3,'tick':'movement_end'}]}
    _apply_profile(character.data['health'],profile,profile['direct'],{'name':'Проба','templateId':42},in_combat=True)
    flag_modified(character,'data');db.session.commit()
    end_turn(client,time_case,auth_headers,'actor');end_turn(client,time_case,auth_headers,'target')
    assert character.data['health']['combatMeta']['consumableModifiers'][0]['remaining']==3
    converted=client.post(combat_url(time_case)+'/action',headers=auth_headers(time_case['actor_user']),json={
        'location_character_id':actor.id,'action_key':'convert_free_action_to_movement'})
    assert converted.status_code==200,converted.json
    for move in range(1,4):
        CombatService.move_character(time_case['location'].id,time_case['actor_user']['id'],character.id,
            actor.pos_x,actor.pos_y+1,movement_mode='walk')
        assert CombatService._consumable_stat_value_bonus(character.data,'awareness')==(6 if move<3 else 0)
        if move<3:assert character.data['health']['combatMeta']['consumableModifiers'][0]['remaining']==3-move


def apply_scripted_stress(monkeypatch, placed, draws, **arguments):
    rng = ScriptedRandint(draws)
    with monkeypatch.context() as patcher:
        patcher.setattr(combat_module.random, "randint", rng)
        result = CombatService.apply_stress_trigger(placed, **arguments)
    rng.assert_exhausted()
    db.session.commit()
    return result, rng.calls


def test_minute_organ_timer_advances_once_per_complete_round_for_ten_rounds(
        client, time_case, auth_headers):
    case = time_case
    actor = case["actor_character"]
    seed_initial_health(actor, [minute_organ_effect()])
    assert CombatService._location_character_condition(case["actor_location"])["can_act"]
    assert_minute_remaining(persisted_health(actor.id), 60)

    # Двадцать реальных запросов end_turn; round/time/effect между ними не присваиваются.
    for round_number in range(1, 11):
        before = 60 - 6 * (round_number - 1)
        first = end_turn(client, case, auth_headers, "actor")
        assert first["round_number"] == round_number
        assert first["current_location_character_id"] == case["target_location"].id
        assert_minute_remaining(persisted_health(actor.id), before)

        second = end_turn(client, case, auth_headers, "target")
        assert second["round_number"] == round_number + 1
        if round_number < 10:
            assert second["current_location_character_id"] == case["actor_location"].id
            assert_minute_remaining(persisted_health(actor.id), 60 - 6 * round_number)
        else:
            # По текущему контракту таймер удаляется, а смерть активируется на истечении.
            assert_organ_expired(persisted_health(actor.id))


def test_end_combat_preserves_timer_then_global_time_advances_only_active_human(
        client, time_case, auth_headers):
    case = time_case
    actor_id, target_id = case["actor_character"].id, case["target_character"].id
    seed_initial_health(case["actor_character"], [minute_organ_effect()], time_active=True)
    seed_initial_health(case["target_character"], [minute_organ_effect()], time_active=False)
    end_combat(client, case, auth_headers)
    assert_minute_remaining(persisted_health(actor_id), 60)
    assert_minute_remaining(persisted_health(target_id), 60)

    advance_global_minutes(client, case, auth_headers, 1)
    assert_organ_expired(persisted_health(actor_id))
    assert_minute_remaining(persisted_health(target_id), 60)
    assert db.session.get(LobbyCharacter, actor_id).time_active is True
    assert db.session.get(LobbyCharacter, target_id).time_active is False


@pytest.mark.parametrize("block_die", [50, 51])
def test_suppressor_attack_block_boundary_and_actual_manifestation_advantage(
        time_case, monkeypatch, block_die):
    case = time_case
    seed_initial_health(case["target_character"], [resistance_effect("suppressor")])
    draws = [(1, 100, block_die)]
    if block_die == 51:
        draws += [(1, 20, 2), (1, 20, 18)]
    result, calls = apply_scripted_stress(
        monkeypatch, case["target_location"], draws, amount=1, trigger="direct_attack",
    )
    assert result["attack_stress_block_chance"] == 50
    assert result["attack_stress_block_roll"] == block_die
    if block_die == 50:
        assert result["blocked"] is True
        assert result["after"] == 0
        assert "rolls" not in result
        assert calls == [(1, 100, 50)]
    else:
        assert result["blocked"] is False
        assert result["after"] == 1
        assert result["rolls"] == [2, 18]
        assert result["roll"] == 18
        assert result["manifested"] is False
        assert calls == [(1, 100, 51), (1, 20, 2), (1, 20, 18)]
    assert persisted_health(case["target_character"].id)["stress"] == result["after"]


@pytest.mark.parametrize("kind", ["suppressor", "harmony"])
def test_fright_has_two_manifestation_dice_but_no_attack_block(time_case, monkeypatch, kind):
    case = time_case
    seed_initial_health(case["target_character"], [resistance_effect(kind)])
    result, calls = apply_scripted_stress(
        monkeypatch, case["target_location"], [(1, 20, 2), (1, 20, 18)],
        amount=1, trigger="fright",
    )
    assert result["blocked"] is False
    assert result["attack_stress_block_chance"] == 0
    assert result["attack_stress_block_roll"] is None
    assert result["after"] == 1
    assert result["rolls"] == [2, 18]
    assert result["roll"] == 18
    assert calls == [(1, 20, 2), (1, 20, 18)]


@pytest.mark.parametrize("kind", ["suppressor", "harmony"])
def test_failed_consolation_api_has_advantage_without_attack_block(
        client, time_case, auth_headers, monkeypatch, kind):
    case = time_case
    seed_initial_health(case["target_character"], [resistance_effect(kind)], stress=1)
    # Первый d20 — реальная проверка утешения. Следующие два — проявление,
    # затем d6 concern. Неожиданный d100 или ещё один бросок немедленно провалит тест.
    rng = ScriptedRandint([(1, 20, 1), (1, 20, 2), (1, 20, 18), (1, 6, 2)])
    with monkeypatch.context() as patcher:
        patcher.setattr(combat_module.random, "randint", rng)
        response = client.post(
            combat_url(case) + "/action", headers=auth_headers(case["actor_user"]),
            json={"location_character_id": case["actor_location"].id,
                  "action_key": "console_ally", "target_character_id": case["target_character"].id},
        )
    assert response.status_code == 200, response.get_json()
    rng.assert_exhausted()
    details = response.get_json()["consolation"]
    assert details["check"]["success"] is False
    assert details["check"]["roll"] == 1
    stress = details["stress"]
    assert stress["trigger"] == "failed_consolation"
    assert stress["blocked"] is False
    assert stress["attack_stress_block_chance"] == 0
    assert stress["attack_stress_block_roll"] is None
    assert stress["rolls"] == [2, 18]
    assert stress["roll"] == 18
    assert stress["manifested"] is True  # force_manifest текущего контракта, даже при18.
    assert stress["before"] == stress["after"] == 1
    db.session.refresh(case["actor_location"])
    assert case["actor_location"].action_points_current == 2
    assert persisted_health(case["target_character"].id)["stress"] == 1


def test_harmony_attack_never_rolls_d100_but_keeps_manifestation_advantage(time_case, monkeypatch):
    case = time_case
    seed_initial_health(case["target_character"], [resistance_effect("harmony")])
    result, calls = apply_scripted_stress(
        monkeypatch, case["target_location"], [(1, 20, 2), (1, 20, 18)],
        amount=1, trigger="direct_attack",
    )
    assert result["attack_stress_block_chance"] == 0
    assert result["attack_stress_block_roll"] is None
    assert result["blocked"] is False
    assert result["after"] == 1
    assert result["rolls"] == [2, 18]
    assert result["roll"] == 18
    assert calls == [(1, 20, 2), (1, 20, 18)]


def test_harmony_survives_1799_seconds_at_effect_layer_only():
    # PATCH /time принимает целые минуты;1799s сознательно проверяются ниже API.
    health = {"current": 100, "max": 100, "effects": [resistance_effect("harmony")]}
    effects = advance_timed_effects(health, health["effects"], 1799)
    harmony = effect_by_id({"effects": effects}, "time-harmony")
    assert harmony is not None
    assert harmony["remaining_seconds"] == 1
    assert harmony["stress_advantage"] is True
    assert harmony["stress_attack_block_chance"] == 0


def test_harmony_expires_at_1800_seconds_through_global_time_api(
        client, time_case, auth_headers, monkeypatch):
    case = time_case
    actor_id, target_id = case["actor_character"].id, case["target_character"].id
    seed_initial_health(case["actor_character"], [resistance_effect("harmony")], time_active=True)
    seed_initial_health(case["target_character"], [resistance_effect("harmony")], time_active=False)
    end_combat(client, case, auth_headers)
    assert effect_by_id(persisted_health(actor_id), "time-harmony")["remaining_seconds"] == 1800

    advance_global_minutes(client, case, auth_headers, 29)
    assert effect_by_id(persisted_health(actor_id), "time-harmony")["remaining_seconds"] == 60
    assert effect_by_id(persisted_health(target_id), "time-harmony")["remaining_seconds"] == 1800
    before, _ = apply_scripted_stress(
        monkeypatch, case["actor_location"], [(1, 20, 2), (1, 20, 18)],
        amount=1, trigger="direct_attack",
    )
    assert before["rolls"] == [2, 18]
    assert before["attack_stress_block_chance"] == 0

    advance_global_minutes(client, case, auth_headers, 1)
    assert effect_by_id(persisted_health(actor_id), "time-harmony") is None
    assert effect_by_id(persisted_health(target_id), "time-harmony")["remaining_seconds"] == 1800
    after, calls = apply_scripted_stress(
        monkeypatch, case["actor_location"], [(1, 20, 18)],
        amount=1, trigger="direct_attack",
    )
    assert after["rolls"] == [18]
    assert after["attack_stress_block_chance"] == 0
    assert calls == [(1, 20, 18)]
