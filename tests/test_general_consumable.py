from copy import deepcopy
import pytest

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import ConsumableUse, DeferredCombatAction, LocationCombatState
from app.models.templates import ItemTemplate
from app.models.lobby_templates import LobbyItemTemplate
from app.services.consumable_effects import parse_consumable_effects
from app.services.general_consumable import _apply_profile
from app.services.effects import advance_timed_effects
from app.services.item_template_lookup import resolve_item_template
from app.services.medical_procedure import minimum_consumable_payment
from test_consumable_use import pair


def install_consumable(pair, name, description, *, quantity=2, health=None):
    profile = parse_consumable_effects(f"{name}. {description}")
    template = ItemTemplate(
        name=name,
        category="consumable",
        description=description,
        price=500,
        attributes={"consumable": profile, "uses": profile["direct"].get("uses")},
    )
    db.session.add(template)
    db.session.flush()
    item = {
        "id": f"general-{template.id}",
        "templateId": template.id,
        "name": name,
        "category": "consumable",
        "quantity": quantity,
    }
    data = deepcopy(pair["actor_character"].data)
    data["inventory"]["backpack"] = [item]
    if health:
        data.setdefault("health", {}).update(health)
    pair["actor_character"].data = data
    flag_modified(pair["actor_character"], "data")
    db.session.commit()
    return item, profile


def application():
    return {"kind": "self", "actionPoints": 0}


@pytest.mark.parametrize('explicit_effect', [False, True])
def test_hp_per_turn_creates_one_regeneration_and_heals_on_the_declared_turn(explicit_effect):
    from app.services.effects import apply_periodic_effects_to_health
    profile = {'direct': {'hp_per_turn': 10, 'duration': 3, 'duration_phase': 'turn_end'},
               'effects': [{'type': 'regeneration', 'value': 10, 'remaining': 3,
                            'tick': 'turn_end', 'source': None if explicit_effect else 'direct'}]}
    health = {'current': 100, 'max': 700, 'effects': []}
    assert _apply_profile(health, profile, profile['direct'], {'name': 'Препарат'}, in_combat=False)
    effects = [effect for effect in health['effects'] if effect['type'] == 'regeneration']
    assert len(effects) == 1
    assert effects[0]['remaining'] == 3
    assert health['current'] == 100
    apply_periodic_effects_to_health(health, effects, 'turn_end')
    assert health['current'] == 110


def test_stress_block_expires_after_three_turns_and_does_not_make_alcohol_deadly(monkeypatch):
    from app.services.effects import apply_effect_to_health, apply_intoxication_dose, sync_health_derived_statuses, tick_effects, health_blocks_new_stress
    def no_roll(*_):
        raise AssertionError('Защита от стресса не означает потерю сознания')
    monkeypatch.setattr('app.services.effects.random.randint', no_roll)
    health = {'stress': 0, 'intoxication': 0, 'effects': []}
    profile = {'direct': {'stress_block_turns': 3}, 'effects': []}
    _apply_profile(health, profile, profile['direct'], {'name':'Препарат'}, in_combat=True)
    apply_intoxication_dose(health, 20)
    apply_effect_to_health(health, {'type':'stress','value':2})
    assert health['stress'] == 0
    assert health_blocks_new_stress(health, include_medicine=False) is False
    side_effect = {'direct': {'stress_delta':2}, 'effects':[]}
    assert _apply_profile(health, side_effect, side_effect['direct'], {'name':'Другое средство'}, in_combat=True)
    assert health['stress'] == 0
    for _ in range(3):
        health['effects'] = tick_effects(health['effects'])
        sync_health_derived_statuses(health)
    assert health['combatMeta']['stressBlockTurns'] == 0
    apply_effect_to_health(health, {'type':'stress','value':2})
    assert health['stress'] == 2


def test_owl_legacy_sleep_timer_is_replaced_by_vision_and_sleep_is_immediate():
    health = {'effects': [], 'needs': {'sleptToday': False}}
    profile = {'direct': {'sleep_block_hours':8, 'satisfy_sleep':True}, 'effects':[]}
    _apply_profile(health, profile, profile['direct'], {'name':'Стимулятор Сова-Н'}, in_combat=False)
    assert not any(e['type'] == 'sleep_support' for e in health['effects'])
    modifiers = health['combatMeta']['consumableModifiers']
    assert all(m['remaining_seconds'] == 8 * 3600 for m in modifiers)
    assert {m['stat']:m['value'] for m in modifiers}['darkness_awareness'] == 5
    health['effects'] = advance_timed_effects(health, health['effects'], 8 * 3600)
    assert not any(e['type'] == 'sleep_support' for e in health['effects'])
    assert health['needs']['sleptToday'] is True
    assert not health['combatMeta']['consumableModifiers']


def test_orel_crash_starts_after_buff_and_lasts_three_full_ticks():
    from app.services.combat import CombatService
    profile=parse_consumable_effects('Стимулятор Орёл. Ампула. Действует 10 ходов.')
    data={'health':{'current':300,'max':700,'exhaustion':0,'effects':[]}}
    health=data['health']
    _apply_profile(health,profile,profile['direct'],{'name':'Стимулятор Орёл','templateId':42},in_combat=False)
    assert health['exhaustion']==1
    assert CombatService._consumable_stat_value_bonus(data,'accuracy')==3
    health['effects']=advance_timed_effects(health,health['effects'],60,advance_medication_turns=True)
    assert CombatService._consumable_stat_value_bonus(data,'accuracy')==-5
    assert health['exhaustion']==1
    health['effects']=advance_timed_effects(health,health['effects'],12,advance_medication_turns=True)
    assert CombatService._consumable_stat_value_bonus(data,'accuracy')==-5
    health['effects']=advance_timed_effects(health,health['effects'],6,advance_medication_turns=True)
    assert CombatService._consumable_stat_value_bonus(data,'accuracy')==0


def test_bolt_applies_crash_after_five_turns_not_after_five_world_moves():
    profile=parse_consumable_effects('Стимулятор Б.О.Л.Т. Ампула. Штраф перемещения -3. Действует 5 ходов.')
    health={'current':300,'max':700,'exhaustion':0,'effects':[]}
    _apply_profile(health,profile,profile['direct'],{'name':'Б.О.Л.Т.','templateId':42},in_combat=False)
    health['effects']=advance_timed_effects(health,health['effects'],29,advance_medication_turns=True)
    assert health['current']==300 and health['exhaustion']==0
    health['effects']=advance_timed_effects(health,health['effects'],1,advance_medication_turns=True)
    assert health['current']==250 and health['exhaustion']==1


def test_moroz_lowers_temperature_on_four_ticks_not_immediately():
    profile=parse_consumable_effects('Жаропонижающее средство Мороз. Ампула. Понижает температуру тела на 1 градус. Работает 4 хода')
    health={'current':700,'max':700,'temperature':38,'exhaustion':1,'effects':[]}
    _apply_profile(health,profile,profile['direct'],{'name':'Жаропонижающее средство Мороз','templateId':52},in_combat=False)
    assert health['temperature']==38
    for expected in [37,36,35,34]:
        health['effects']=advance_timed_effects(health,health['effects'],6,advance_medication_turns=True)
        assert health['temperature']==expected
    health['effects']=advance_timed_effects(health,health['effects'],60,advance_medication_turns=True)
    assert health['temperature']==34


@pytest.mark.parametrize('temperature,expected',[(38,37.5),(34,34.5),(36.2,36),(35.8,36)])
def test_temperature_recovers_toward_36_only_each_complete_hour(temperature,expected):
    health={'temperature':temperature,'exhaustion':0,'infection':0,'effects':[]}
    advance_timed_effects(health,[],3599)
    assert health['temperature']==temperature
    advance_timed_effects(health,[],1)
    assert health['temperature']==expected


@pytest.mark.parametrize('blocker',[{'infection':1},{'exhaustion':.5},{'effects':[{'type':'infection','active':True}]}])
def test_infection_or_exhaustion_prevents_natural_temperature_recovery(blocker):
    health={'temperature':38,'effects':[],**blocker}
    advance_timed_effects(health,health['effects'],7200)
    assert health['temperature']==38


def test_skala_n_temporary_maximum_survives_recalculation_and_protects_organs():
    from app.services.combat import CombatService
    from app.services.health import apply_health_maximums, heal_character_fully
    data={'health':{'current':300,'max':700,'effects':[]}}
    profile=parse_consumable_effects('Стимулятор Скала-Н. Ампула. Действует 3 хода.')
    health=data['health']
    _apply_profile(health,profile,profile['direct'],{'name':'Стимулятор Скала-Н','templateId':42},in_combat=False)
    assert (health['current'],health['max'])==(500,900)
    apply_health_maximums(data)
    assert (health['current'],health['max'])==(500,900)
    organ=CombatService._apply_organ_damage(health,'heart',10,'chest')
    assert organ['current']==15  # Обычный орган теряет 10 из 20; со Скала-Н — 5.
    health['effects']=advance_timed_effects(health,health['effects'],18,advance_medication_turns=True)
    apply_health_maximums(data)
    assert (health['current'],health['max'])==(200,700)
    assert CombatService._apply_organ_damage(health,'heart',10,'chest')['current']==5
    _apply_profile(health,profile,profile['direct'],{'name':'Стимулятор Скала-Н','templateId':42},in_combat=False)
    heal_character_fully(data)
    assert (health['current'],health['max'])==(700,700)
    assert not health['combatMeta'].get('consumableModifiers')


@pytest.mark.parametrize('word',['Уровень','Уровня','Уровней'])
def test_signed_singular_and_spaced_exhaustion_are_parsed(word):
    profile=parse_consumable_effects(f'Препарат для проверки. + 1 {word} Истощения.')
    assert profile['direct']['exhaustion_delta']==1


def test_legacy_imported_stimulant_is_enriched_without_writing_catalog(client,pair,auth_headers):
    from app.services.medical_procedure import _profile
    description='Ампула. +6 Внимательности на 6 перемещений'
    template=ItemTemplate(name='Стимулятор Морковчатник',category='consumable',description=description,
        attributes={'catalog_name':'Стимулятор Морковчатник','raw_description':description,
            'consumable':{'direct':{'duration':6,'duration_phase':'movement_end','application_form':'injectable'},'effects':[]}})
    db.session.add(template);db.session.commit()
    original=deepcopy(template.attributes)
    view,direct=_profile({'templateId':template.id},pair['lobby'].id)
    assert view.attributes['consumable']['modifiers'][0]['value']==6
    assert template.attributes==original
    assert view.id==template.id


@pytest.mark.parametrize('name',['Сигареты Тест','Самокрутка','Сигары'])
@pytest.mark.parametrize('exhaustion',[0,.1,2])
def test_tobacco_counts_nominal_relief_even_at_zero_exhaustion(client,pair,auth_headers,monkeypatch,name,exhaustion):
    from app.services.addictions import initial_addiction
    LocationCombatState.query.one().status='idle'
    item,_=install_consumable(pair,name,'-0.25 Истощения. Снижение стресса через 5 минут.',quantity=1,health={'exhaustion':exhaustion})
    data=deepcopy(pair['actor_character'].data)
    initial_addiction(data.setdefault('health',{}),name,'Проверка')
    data['inventory']['pockets']=[{'id':'fire','name':'Спички','category':'misc','quantity':2}]
    pair['actor_character'].data=data;db.session.commit()
    monkeypatch.setattr('app.services.addictions.random.random',lambda:1)
    response=apply_general(client,pair,auth_headers,item,operation='zero-exhaustion-tobacco')
    assert response.status_code==200,response.json
    health=response.json['data']['health']
    assert health['exhaustion']==max(0,exhaustion-.25)
    assert health['addictions']['records']['nicotine']['daily_progress']['exhaustion_relief']==.25


def test_tobacco_daily_counter_keeps_counting_after_one_at_zero_exhaustion(client,pair,auth_headers):
    from app.services.addictions import initial_addiction
    LocationCombatState.query.one().status='idle'
    item,_=install_consumable(pair,'Сигареты Тест','-0.25 Истощения. Снижение стресса через 5 минут. 20 использований.',quantity=1,health={'exhaustion':0})
    data=deepcopy(pair['actor_character'].data)
    initial_addiction(data.setdefault('health',{}),'Сигареты Тест','Проверка')
    data['inventory']['pockets']=[{'id':'lighter','name':'Зажигалка','category':'misc','quantity':1}]
    pair['actor_character'].data=data;db.session.commit()
    for index in range(5):
        response=apply_general(client,pair,auth_headers,item,operation=f'tobacco-counter-{index}')
        assert response.status_code==200,response.json
        item=deepcopy(response.json['data']['inventory']['backpack'][0])
    health=response.json['data']['health']
    assert health['exhaustion']==0
    assert health['addictions']['records']['nicotine']['daily_progress']['exhaustion_relief']==1.25


def source(item):
    return {
        "path": ["inventory", "backpack", 0],
        "snapshot": deepcopy(item),
    }


def apply_general(client, pair, auth_headers, item, *, operation="general-1", deferred=None, combat=False):
    return client.post(
        f"/lobbies/characters/{pair['actor_character'].id}/general-consumable",
        headers=auth_headers(pair["actor_user"]),
        json={
            "operation_id": operation,
            "deferred_action_id": deferred,
            "source": source(item),
            "application": application(),
            "combat_location_id": pair["location"].id if combat else None,
            "actor_location_character_id": pair["actor_location"].id if combat else None,
        },
    )


def test_custom_lobby_consumable_uses_profile_after_rename(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    profile = {
        "editor_version": 1,
        "direct": {"action_points_cost": 0, "stress_delta": -2},
        "modifiers": [{"stat": "strength", "value": 3, "remaining": 2, "tick": "turn_end"}],
        "effects": [{
            "type": "delayed_adjustment", "name": "Отложенное истощение", "area": "editor-2",
            "remaining": 2, "tick": "time_elapsed", "time_unit": "minute",
            "adjustments": [{"field": "exhaustion", "delta": 1, "min": 0, "max": 10}],
        }, {
            "type": "delayed_adjustment", "name": "Отложенная радиация", "area": "editor-3",
            "remaining": 2, "tick": "time_elapsed", "time_unit": "minute",
            "adjustments": [{"field": "radiation", "delta": 4, "min": 0}],
        }],
    }
    response = client.post(
        f"/lobbies/{pair['lobby'].id}/templates",
        headers=auth_headers(pair["actor_user"]),
        json={"name": "Состав А", "category": "consumable", "weight": 0.1, "volume": 0.1,
              "attributes": {"uses": 2, "consumable": profile}},
    )
    assert response.status_code == 201, response.json
    template_id = response.json["id"]
    renamed = client.put(
        f"/lobbies/{pair['lobby'].id}/templates/{template_id}",
        headers=auth_headers(pair["actor_user"]), json={"name": "Совсем другое название"},
    )
    assert renamed.status_code == 200, renamed.json
    item = {
        "id": "custom-1", "templateId": template_id + 1_000_000,
        "name": "Старое название в инвентаре", "category": "consumable",
        "quantity": 1, "maxUses": 2, "uses": 2,
    }
    data = deepcopy(pair["actor_character"].data)
    data["inventory"]["backpack"] = [item]
    data["health"]["stress"] = 5
    pair["actor_character"].data = data
    flag_modified(pair["actor_character"], "data")
    db.session.commit()
    applied = apply_general(client, pair, auth_headers, item)
    assert applied.status_code == 200, applied.json
    health = applied.json["data"]["health"]
    assert health["stress"] == 3
    assert any(entry.get("stat") == "strength" and entry.get("value") == 3
               for entry in health["combatMeta"]["consumableModifiers"])
    assert any(effect.get("type") == "delayed_adjustment" and effect.get("area") == "editor-2"
               for effect in health["effects"])
    assert any(effect.get("type") == "delayed_adjustment" and effect.get("area") == "editor-3"
               for effect in health["effects"])
    advance_timed_effects(health, health["effects"], 120)
    assert health["exhaustion"] == 1
    assert health["radiation"] == 4
    assert applied.json["data"]["inventory"]["backpack"][0]["uses"] == 1


def test_custom_profile_rejects_unknown_health_field(client, pair, auth_headers):
    profile = {"editor_version": 1, "direct": {"secret": 100}, "modifiers": [], "effects": []}
    response = client.post(
        f"/lobbies/{pair['lobby'].id}/templates",
        headers=auth_headers(pair["actor_user"]),
        json={"name": "Некорректный препарат", "category": "consumable",
              "attributes": {"uses": 1, "consumable": profile}},
    )
    assert response.status_code == 400
    assert LobbyItemTemplate.query.filter_by(lobby_id=pair["lobby"].id, name="Некорректный препарат").first() is None


def test_local_consumable_payment_uses_lobby_template_not_client_cost(client, pair):
    template = LobbyItemTemplate(
        lobby_id=pair["lobby"].id, created_by=pair["actor_user"]["id"],
        name="Безымянный состав", category="consumable",
        attributes={"uses": 1, "consumable": {
            "editor_version": 1, "direct": {"action_points_cost": 3, "stress_delta": -1},
            "modifiers": [], "effects": [],
        }},
    )
    db.session.add(template)
    db.session.flush()
    item = {"id": "payment-local", "templateId": template.id + 1_000_000,
            "name": "Не совпадает с шаблоном", "category": "consumable", "quantity": 1}
    actor_data = deepcopy(pair["actor_character"].data)
    actor_data["inventory"]["backpack"] = [item]
    details = minimum_consumable_payment(actor_data, actor_data, {
        "source": {"path": ["inventory", "backpack", 0], "snapshot": deepcopy(item)},
        "application": {"kind": "self", "actionPoints": 0},
        "server_authoritative": True, "server_operation": "general",
    }, pair["lobby"].id)
    assert details["treatment_action_points"] == 3
    assert resolve_item_template(item, pair["lobby"].id).id == template.id
    assert resolve_item_template(item, pair["lobby"].id + 1) is None


def test_advanced_editor_can_clone_official_stress_profile(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    profile = parse_consumable_effects("Подавитель эмоций. Действует 6 перемещений. -25 пси-состояний.")
    official = ItemTemplate(
        name="Подавитель эмоций", category="consumable",
        attributes={"uses": 1, "consumable": deepcopy(profile)},
    )
    db.session.add(official)
    db.session.flush()
    cloned = deepcopy(profile)
    cloned["editor_version"] = 2
    cloned["effects"] = [effect for effect in cloned["effects"] if effect.get("source") != "direct"]
    response = client.post(
        f"/lobbies/{pair['lobby'].id}/templates",
        headers=auth_headers(pair["actor_user"]),
        json={"name": "Безымянное средство", "category": "consumable",
              "attributes": {"uses": 1, "consumable": cloned}},
    )
    assert response.status_code == 201, response.json
    item = {"id": "renamed-suppressor", "templateId": response.json["id"] + 1_000_000,
            "name": "Никакой связи с оригиналом", "category": "consumable", "quantity": 1}
    data = deepcopy(pair["actor_character"].data)
    data["inventory"]["backpack"] = [item]
    data["health"].update({"stress": 4, "psyState": 30})
    pair["actor_character"].data = data
    flag_modified(pair["actor_character"], "data")
    db.session.commit()
    result = apply_general(client, pair, auth_headers, item)
    assert result.status_code == 200, result.json
    assert result.json["data"]["health"]["psyState"] == 5
    assert any(effect["type"] == "stress_resistance" and effect["stress_attack_block_chance"] == 50
               for effect in result.json["data"]["health"]["effects"])


def test_advanced_editor_rejects_unknown_effect_type(client, pair, auth_headers):
    profile = {"editor_version": 2, "direct": {"stress_delta": -1}, "modifiers": [],
               "effects": [{"type": "invented_server_effect", "value": 100}]}
    response = client.post(
        f"/lobbies/{pair['lobby'].id}/templates",
        headers=auth_headers(pair["actor_user"]),
        json={"name": "Неверный эффект", "category": "consumable",
              "attributes": {"uses": 1, "consumable": profile}},
    )
    assert response.status_code == 400


def test_renamed_official_water_satisfies_profile_requirement(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    water_profile = parse_consumable_effects("Вода. -1 радиации.")
    protein_profile = parse_consumable_effects("Протеин. Восстановление здоровья на отдыхе.")
    assert water_profile["direct"]["satisfy_water"] is True
    assert protein_profile["direct"]["requires_water_fraction"] > 0
    templates = [
        ItemTemplate(name="Переименованная жидкость", category="consumable",
                     attributes={"uses": 3, "consumable": water_profile}),
        ItemTemplate(name="Белковая смесь", category="consumable",
                     attributes={"uses": 1, "consumable": protein_profile}),
    ]
    db.session.add_all(templates)
    db.session.flush()
    water = {"id": "renamed-water", "templateId": templates[0].id,
             "name": "Канистра №1", "category": "consumable", "quantity": 1, "uses": 3, "maxUses": 3}
    protein = {"id": "protein", "templateId": templates[1].id,
               "name": "Не протеин", "category": "consumable", "quantity": 2}
    data = deepcopy(pair["actor_character"].data)
    data["inventory"]["backpack"] = [water, protein]
    pair["actor_character"].data = data
    flag_modified(pair["actor_character"], "data")
    db.session.commit()
    response = client.post(
        f"/lobbies/characters/{pair['actor_character'].id}/general-consumable",
        headers=auth_headers(pair["actor_user"]),
        json={"operation_id": "renamed-water-use",
              "source": {"path": ["inventory", "backpack", 1], "snapshot": deepcopy(protein)},
              "application": application()},
    )
    assert response.status_code == 200, response.json
    stored = response.json["data"]["inventory"]["backpack"]
    assert [item["id"] for item in stored] == ["renamed-water", "protein"]
    assert stored[0]["uses"] == 2


def test_edited_radiation_filter_uses_profile_duration_and_full_capacity():
    profile = {
        "direct": {"radiation_filter_percent": 50, "radiation_filter_capacity": 100},
        "effects": [{"type": "radiation_filter", "name": "Полевой фильтр", "value": 50,
                     "remaining": 12, "tick": "time_elapsed", "time_unit": "hour",
                     "capacity": 40, "remaining_capacity": 2}],
        "modifiers": [],
    }
    health = {"effects": [], "combatMeta": {}}
    assert _apply_profile(health, profile, profile["direct"],
                          {"name": "Другой препарат", "templateId": 42}, in_combat=False)
    effect = next(entry for entry in health["effects"] if entry["type"] == "radiation_filter")
    assert effect["remaining"] == 12
    assert effect["remaining_seconds"] == 12 * 3600
    assert effect["capacity"] == effect["remaining_capacity"] == 40


def test_food_and_drink_are_applied_and_consumed_on_server(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    item, _ = install_consumable(
        pair,
        "Банка пива",
        "-1 Радиация. -1 Уровень стресса. +20 Опьянения.",
        health={"radiation": 10, "stress": 4, "intoxication": 0},
    )

    response = apply_general(client, pair, auth_headers, item)

    assert response.status_code == 200, response.json
    health = response.json["data"]["health"]
    assert health["radiation"] == 9
    assert health["stress"] == 3
    assert health["intoxication"] == 20
    stored = response.json["data"]["inventory"]["backpack"][0]
    assert stored["quantity"] == 1
    assert stored["uses"] == 1
    assert ConsumableUse.query.count() == 1


def test_existing_template_gains_generic_darkness_modifier_without_reimport(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    item, profile = install_consumable(
        pair, "Ночной настой",
        "Убирает помехи на броски внимательности и точности в темноте. "
        "+5 на броски внимательности в темноте. Длится 8 часов",
        quantity=1,
    )
    template = db.session.get(ItemTemplate, item['templateId'])
    old_profile = {**profile, 'modifiers': []}
    template.attributes = {**template.attributes, 'consumable': old_profile}
    db.session.commit()

    response = apply_general(client, pair, auth_headers, item, operation='night-infusion')

    assert response.status_code == 200, response.json
    modifiers = response.json['data']['health']['combatMeta']['consumableModifiers']
    assert {entry['stat'] for entry in modifiers} == {
        'darkness_awareness', 'darkness_visual_immunity', 'darkness_shooting_immunity',
    }
    assert all(entry['remaining_seconds'] == 8 * 3600 for entry in modifiers)


def test_consumable_stress_reduction_expires_manifestation(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    item, _ = install_consumable(
        pair, "Успокоительный чай", "-1 Стресса.", quantity=1,
        health={
            "stress": 2,
            "effects": [{
                "type": "stress_effect", "name": "Временная паника",
                "active": True, "expires_on_stress_decrease": True,
            }],
        },
    )

    response = apply_general(client, pair, auth_headers, item, operation="stress-relief")

    assert response.status_code == 200, response.json
    assert response.json["data"]["health"]["stress"] == 1
    effect = next(
        entry for entry in response.json["data"]["health"]["effects"]
        if entry["type"] == "stress_effect"
    )
    assert effect["active"] is False


def test_delayed_tablet_effect_is_saved_by_server(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    item, _ = install_consumable(pair, "Анальгин", "Снижает боль после 2 ходов.", quantity=1)

    response = apply_general(client, pair, auth_headers, item)

    assert response.status_code == 200, response.json
    effects = response.json["data"]["health"]["effects"]
    assert any(effect["type"] == "delayed_treatment" for effect in effects)
    assert response.json["data"]["inventory"]["backpack"] == []


def test_smoking_consumes_match_and_keeps_lighter(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    cigarette, _ = install_consumable(
        pair, "Сигареты дешёвые", "Снижение стресса через 5 минут. 20 использований.", quantity=1,
    )
    data = deepcopy(pair["actor_character"].data)
    data["inventory"]["backpack"] = [
        {"id": "matches", "name": "Спички", "category": "misc", "quantity": 1},
        cigarette,
    ]
    pair["actor_character"].data = data
    flag_modified(pair["actor_character"], "data")
    db.session.commit()
    body = {
        "operation_id": "smoke-1",
        "source": {"path": ["inventory", "backpack", 1], "snapshot": deepcopy(cigarette)},
        "application": application(),
    }

    response = client.post(
        f"/lobbies/characters/{pair['actor_character'].id}/general-consumable",
        headers=auth_headers(pair["actor_user"]), json=body,
    )

    assert response.status_code == 200, response.json
    inventory = response.json["data"]["inventory"]["backpack"]
    assert len(inventory) == 1
    assert inventory[0]["name"] == "Сигареты дешёвые"
    assert inventory[0]["uses"] == 19
    assert response.json["consumable_result"]["requirements"]["fire_source"] == "Спички"


def test_water_requirement_is_spent_atomically(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    sugar, _ = install_consumable(pair, "Сахар", "Нужна 2/3 бутылки воды. -1 Стресса.", quantity=1)
    water = {
        "id": "water", "name": "Вода", "category": "consumable", "quantity": 1,
        "uses": 3, "maxUses": 3, "attributes": {"uses_remaining": 3, "uses": 3},
    }
    data = deepcopy(pair["actor_character"].data)
    data["inventory"]["backpack"] = [water, sugar]
    pair["actor_character"].data = data
    flag_modified(pair["actor_character"], "data")
    db.session.commit()

    response = client.post(
        f"/lobbies/characters/{pair['actor_character'].id}/general-consumable",
        headers=auth_headers(pair["actor_user"]),
        json={
            "operation_id": "sugar-1",
            "source": {"path": ["inventory", "backpack", 1], "snapshot": deepcopy(sugar)},
            "application": application(),
        },
    )

    assert response.status_code == 200, response.json
    inventory = response.json["data"]["inventory"]["backpack"]
    assert len(inventory) == 2
    assert inventory[0]["name"] == "Вода"
    assert inventory[0]["uses"] == 1
    assert inventory[1]["name"] == "Сахар"
    assert inventory[1]["uses"] == 2
    assert response.json["consumable_result"]["requirements"]["water_charges"] == 2


def test_missing_requirement_rolls_back_selected_item(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    cigarette, _ = install_consumable(
        pair, "Сигареты дешёвые", "Снижение стресса через 5 минут. 20 использований.", quantity=1,
    )
    before = deepcopy(pair["actor_character"].data)

    response = apply_general(client, pair, auth_headers, cigarette, operation="smoke-no-fire")

    assert response.status_code == 400
    history=pair['actor_character'].data['health']['combatMeta']['actionJournal']
    assert len(history)==1 and history[0]['status']=='rejected' and history[0]['action_points']==0
    before['health'].setdefault('combatMeta',{})['actionJournal']=deepcopy(history)
    assert pair["actor_character"].data == before
    assert ConsumableUse.query.count() == 0


def test_injectable_cannot_bypass_medicine_route(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    item, _ = install_consumable(pair, "Тестовая ампула", "Ампула. +5 ХП.", quantity=1)
    before = deepcopy(pair["actor_character"].data)

    response = apply_general(client, pair, auth_headers, item)

    assert response.status_code == 400
    history=pair['actor_character'].data['health']['combatMeta']['actionJournal']
    assert len(history)==1 and history[0]['status']=='rejected' and history[0]['action_points']==0
    before['health'].setdefault('combatMeta',{})['actionJournal']=deepcopy(history)
    assert pair["actor_character"].data == before
    assert ConsumableUse.query.count() == 0


def test_general_consumable_is_paid_with_backpack_access_in_combat(client, pair, auth_headers):
    item, _ = install_consumable(pair, "Хлеб", "Закрывает потребность в еде.", quantity=1)
    selection = {
        "source": source(item),
        "application": application(),
        "target_character_id": pair["actor_character"].id,
        "treatment_request_id": None,
        "server_authoritative": True,
        "server_operation": "general",
    }
    response = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/spend",
        headers=auth_headers(pair["actor_user"]),
        json={
            "location_character_id": pair["actor_location"].id,
            "action_points": 2,
            "allow_deferred": True,
            "pending_action_id": "general-combat-1",
            "pending_action_label": "Хлеб",
            "consumable_selection": selection,
        },
    )
    assert response.status_code == 200, response.json
    row = db.session.get(DeferredCombatAction, "general-combat-1")
    assert row.status == "ready"
    assert row.payload["required_action_points"] == 2
    assert "medicine_roll" not in row.payload

    response = apply_general(
        client, pair, auth_headers, item,
        operation=row.id, deferred=row.id, combat=True,
    )

    assert response.status_code == 200, response.json
    assert response.json["data"]["inventory"]["backpack"] == []
    assert response.json["data"]["health"]["needs"]["mealsToday"] == 1
    assert pair["actor_location"].action_points_current == 3
    assert db.session.get(DeferredCombatAction, row.id).status == "completed"


def test_general_consumable_rejects_client_underpayment(client, pair, auth_headers):
    item, _ = install_consumable(
        pair, "Медленная еда", "Закрывает потребность в еде. Время использования - 4 ОД.",
        quantity=1,
    )
    before_ap = pair["actor_location"].action_points_current
    selection = {
        "source": source(item),
        "application": application(),
        "target_character_id": pair["actor_character"].id,
        "treatment_request_id": None,
        "server_authoritative": True,
        "server_operation": "general",
    }

    response = client.post(
        f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/spend",
        headers=auth_headers(pair["actor_user"]),
        json={
            "location_character_id": pair["actor_location"].id,
            "action_points": 5,  # Server requires 4 use + 2 backpack access.
            "allow_deferred": True,
            "pending_action_id": "underpaid-general",
            "pending_action_label": "Медленная еда",
            "consumable_selection": selection,
        },
    )

    assert response.status_code == 400
    assert pair["actor_location"].action_points_current == before_ap
    assert db.session.get(DeferredCombatAction, "underpaid-general") is None
    assert pair["actor_character"].data["inventory"]["backpack"][0]["quantity"] == 1


def test_repeated_operation_does_not_apply_or_consume_twice(client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    item, _ = install_consumable(pair, "Хлеб", "Закрывает потребность в еде.", quantity=2)
    first = apply_general(client, pair, auth_headers, item)
    assert first.status_code == 200, first.json
    after_first = deepcopy(pair["actor_character"].data)

    repeated = apply_general(client, pair, auth_headers, item)

    assert repeated.status_code == 409
    assert pair["actor_character"].data == after_first
    assert ConsumableUse.query.count() == 1


def test_second_dose_refreshes_positive_duration_and_accumulates_costs(
        client, pair, auth_headers):
    LocationCombatState.query.one().status = "idle"
    item, _ = install_consumable(
        pair, "Тестовый стимулятор", "Временный тестовый эффект.", quantity=1,
        health={"current": 100, "max": 100, "exhaustion": 0},
    )
    template = db.session.get(ItemTemplate, item["templateId"])
    template.attributes = {
        "uses": 1,
        "consumable": {
            "effects": [],
            "modifiers": [],
            "direct": {
                "strength_delta": 4,
                "duration": 3,
                "duration_phase": "turn_end",
                "exhaustion_delta": 1,
                "hp": -5,
                "hp_max_delta": 20,
                "post_duration_hp_delta": -10,
            },
        },
    }
    first_item = {**item, "id": "dose-a"}
    second_item = {**item, "id": "dose-b"}
    data = deepcopy(pair["actor_character"].data)
    data["inventory"]["backpack"] = [first_item, second_item]
    pair["actor_character"].data = data
    flag_modified(pair["actor_character"], "data")
    db.session.commit()

    first = apply_general(
        client, pair, auth_headers, first_item, operation="refresh-dose-a",
    )
    assert first.status_code == 200, first.json

    data = deepcopy(pair["actor_character"].data)
    modifier = data["health"]["combatMeta"]["consumableModifiers"][0]
    modifier["remaining"] = 1
    modifier["remaining_seconds"] = 6
    crash = next(
        effect for effect in data["health"]["effects"]
        if effect["type"] == "stimulant_crash"
    )
    crash["remaining"] = 1
    crash["remaining_seconds"] = 6
    pair["actor_character"].data = data
    flag_modified(pair["actor_character"], "data")
    db.session.commit()

    second = apply_general(
        client, pair, auth_headers, second_item, operation="refresh-dose-b",
    )

    assert second.status_code == 200, second.json
    health = second.json["data"]["health"]
    modifiers = health["combatMeta"]["consumableModifiers"]
    assert len(modifiers) == 1
    assert modifiers[0]["value"] == 4
    assert modifiers[0]["remaining"] == 3
    assert modifiers[0]["remaining_seconds"] == 18
    assert health["exhaustion"] == 2
    assert health["current"] == 90
    assert health["max"] == 120
    crashes = [effect for effect in health["effects"] if effect["type"] == "stimulant_crash"]
    assert len(crashes) == 1
    assert crashes[0]["remaining"] == 3
    assert crashes[0]["remaining_seconds"] == 18
    current_crash = next(
        adjustment for adjustment in crashes[0]["onExpire"]
        if adjustment["field"] == "current"
    )
    assert current_crash["delta"] == -20


def test_stress_resistance_profiles_create_timed_effects():
    suppressor_profile = parse_consumable_effects(
        "Подавитель эмоций. Действует 6 перемещений. -25 Пси-состояний."
    )
    harmony_profile = parse_consumable_effects(
        "Стимулятор Гармония. -3 Стресса. -20 Пси-состояний. Действует 30 минут."
    )
    health = {"stress": 5, "psyState": 30, "effects": [], "combatMeta": {}}

    assert _apply_profile(
        health,
        suppressor_profile,
        suppressor_profile["direct"],
        {"id": "suppressor-1", "name": "Препарат спокойствия"},
        in_combat=True,
    ) is True
    suppressor = next(
        effect for effect in health["effects"]
        if effect["type"] == "stress_resistance"
    )
    assert suppressor["remaining"] == 6
    assert suppressor["tick"] == "movement_end"
    assert suppressor["stress_advantage"] is True
    assert suppressor["stress_attack_block_chance"] == 50

    assert _apply_profile(
        health,
        harmony_profile,
        harmony_profile["direct"],
        {"id": "harmony-1", "name": "Препарат равновесия"},
        in_combat=False,
    ) is True
    harmony = next(
        effect for effect in health["effects"]
        if effect["source"] == "Препарат равновесия"
    )
    assert harmony["remaining"] == 30
    assert harmony["remaining_seconds"] == 30 * 60
    assert harmony["tick"] == "time_elapsed"
    assert harmony["stress_advantage"] is True
    assert harmony["stress_attack_block_chance"] == 0


def test_custom_stress_profile_does_not_need_a_canonical_item_name():
    profile = {
        "effects": [], "modifiers": [],
        "direct": {
            "stress_advantage": True,
            "stress_attack_block_chance": 35,
            "duration": 12,
            "duration_phase": "time_elapsed",
        },
    }
    health = {"effects": [], "combatMeta": {}}

    assert _apply_profile(health, profile, profile["direct"], {"name": "Настой"}, in_combat=False)

    effect = next(effect for effect in health["effects"] if effect["type"] == "stress_resistance")
    assert effect["stress_advantage"] is True
    assert effect["stress_attack_block_chance"] == 35
    assert effect["remaining_seconds"] == 12 * 60


def test_radiation_reduction_ceiling_comes_from_profile_not_item_name():
    profile = {"effects": [], "modifiers": [], "direct": {"radiation_delta": -5, "radiation_reduction_ceiling": 50}}
    health = {"radiation": 60, "effects": [], "combatMeta": {}}

    _apply_profile(health, profile, profile["direct"], {"name": "Экстракт"}, in_combat=False)
    assert health["radiation"] == 60

    profile["direct"]["radiation_reduction_ceiling"] = 100
    _apply_profile(health, profile, profile["direct"], {"name": "Банка пива"}, in_combat=False)
    assert health["radiation"] == 55


def test_old_radiation_profiles_use_effect_metadata_without_item_name():
    old_profile = {"effects": [], "modifiers": [], "direct": {"radiation_delta": -1, "is_alcohol": True}}
    health = {"radiation": 51, "effects": [], "combatMeta": {}}

    _apply_profile(health, old_profile, old_profile["direct"], {"name": "Переименованный напиток"}, in_combat=False)

    assert health["radiation"] == 51


def test_radiation_ceiling_does_not_apply_to_cigars():
    profile = parse_consumable_effects("Сигары. -1 радиации")
    assert "radiation_reduction_ceiling" not in profile["direct"]
    health = {"radiation": 60, "effects": [], "combatMeta": {}}

    _apply_profile(health, profile, profile["direct"], {"name": "Переименованные сигары"}, in_combat=False)

    assert health["radiation"] == 59


def test_template_addiction_profile_is_used_after_item_rename(client, pair, auth_headers, monkeypatch):
    LocationCombatState.query.one().status = "idle"
    monkeypatch.setattr('app.services.addictions.random.random', lambda: 0)
    item, profile = install_consumable(pair, "Новый напиток", "-1 Радиации", quantity=1)
    profile["addiction"] = {
        "key": "test_custom", "label": "Проверочная зависимость", "chance": 100,
        "multipliers": {}, "kind": "medicine",
    }
    template = db.session.get(ItemTemplate, item["templateId"])
    template.attributes = {**template.attributes, "consumable": profile}
    db.session.commit()

    response = apply_general(client, pair, auth_headers, item, operation="custom-addiction")

    assert response.status_code == 200, response.json
    assert "test_custom" in response.json["data"]["health"]["addictions"]["records"]


def test_explicitly_nonaddictive_profile_overrides_legacy_name(client, pair, auth_headers, monkeypatch):
    LocationCombatState.query.one().status = "idle"
    monkeypatch.setattr('app.services.addictions.random.random', lambda: 0)
    item, profile = install_consumable(pair, "Банка пива", "-1 Радиации", quantity=1)
    profile["addiction"] = None
    template = db.session.get(ItemTemplate, item["templateId"])
    template.attributes = {**template.attributes, "consumable": profile}
    db.session.commit()

    response = apply_general(client, pair, auth_headers, item, operation="no-addiction")

    assert response.status_code == 200, response.json
    assert response.json["consumable_result"]["consumable"]["addiction"]["applicable"] is False
