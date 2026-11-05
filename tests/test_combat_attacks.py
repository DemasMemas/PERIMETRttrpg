from types import SimpleNamespace
from copy import deepcopy

import pytest

import app.services.combat as combat_module
from app.services.combat import CombatService
from app.services.effects import sync_health_derived_statuses
from app.services.exceptions import ValidationError


def test_ranged_hit_zone_boundaries_follow_rules():
    assert CombatService._random_hit_zone(1) == "left_arm"
    assert CombatService._random_hit_zone(6) == "left_leg"
    assert CombatService._random_hit_zone(14) == "abdomen"
    assert CombatService._random_hit_zone(19) == "chest"
    assert CombatService._random_hit_zone(20) == "head"


@pytest.mark.parametrize('height,difficulty',[(.99,4),(1,8),(3.99,8),(4,10),(6.99,10),(7,12),
    (9.99,12),(10,14),(12.99,14),(13,16),(15.99,16),(16,18),(20,18),(20.99,18),(21,20),(21.99,20)])
def test_fall_fractional_height_uses_floor_and_correct_twenty_meter_boundary(monkeypatch,height,difficulty):
    monkeypatch.setattr(combat_module,'flag_modified',lambda *args:None)
    monkeypatch.setattr(combat_module.random,'randint',lambda *args:20)
    monkeypatch.setattr(CombatService,'_target_armor',staticmethod(lambda *args:(0,[])))
    monkeypatch.setattr(CombatService,'_apply_attack_damage',staticmethod(lambda *args,**kwargs:{'zones':{}}))
    target=SimpleNamespace(character=SimpleNamespace(data={'health':{'current':700,'effects':[]}}),posture='standing')
    result=CombatService.resolve_fall(target,height)
    assert result['height']==height and result['classified_height']==int(height)
    assert result['difficulty']==difficulty


def test_mutant_innate_protection_and_attack_cost_are_used():
    data = {
        'mutant': {'physical_protection': 35, 'anomaly_protection': 50},
        'equipment': {},
    }
    protection, details = CombatService._target_armor(data, 'chest')
    assert protection == 35
    assert details[0]['slot'] == 'mutant'
    assert CombatService._target_elemental_protection(data, 'thermal') == 50
    assert CombatService._melee_action_cost({'action_points': 5}, 'Укус') == 5


def test_mutant_traits_modify_additional_trauma_difficulty():
    data = {
        'is_mutant': True,
        'mutant': {
            'traits': ['Двужильность. Сложность для получения Доп. травмы +25'],
        },
    }

    assert CombatService._mutant_trauma_difficulty_modifier(data) == 25


def test_zhmerka_back_protection_is_exact_and_includes_mature_bonus():
    data = {
        'mutant': {
            'physical_protection': 60,
            'traits': [
                'Уязвимая спина. Физ.защита при атаках со спины по торсу - 25%',
            ],
            'variant': {
                'traits': [
                    'Матерый. Физическая защита увеличена на 10%.',
                ],
            },
        },
        'equipment': {},
    }

    front, _ = CombatService._target_armor(data, 'chest')

    assert front == 60
    assert CombatService._mutant_back_torso_protection(data) == 35


def test_improved_reflexes_only_discount_first_attack_of_round(monkeypatch):
    monkeypatch.setattr(combat_module, 'flag_modified', lambda *args, **kwargs: None)
    data = {
        'health': {'combatMeta': {}},
        'mutant': {
            'traits': [],
            'variant': {'traits': ['Улучшенные рефлексы. Первая атака дешевле на 1 ОД']},
        },
    }
    loc_char = SimpleNamespace(character=SimpleNamespace(data=data))
    profile = {'action_points': 1, 'raw_effect': '220 урона. 2 ОД'}

    assert CombatService._mutant_first_attack_cost(loc_char, profile, 3) == 1
    CombatService._mark_mutant_attack_used(loc_char, 3)
    assert CombatService._mutant_first_attack_cost(loc_char, profile, 3) == 2
    assert CombatService._mutant_first_attack_cost(loc_char, profile, 4) == 1


def test_ugly_appearance_and_ambush_modify_matching_skills_only():
    data = {
        'skills': {
            'physical': {'will': {'base': 10, 'bonus': 0}},
            'other': {'stealth': {'base': 10, 'bonus': 0}},
        },
        'health': {'combatMeta': {
            'uglyAppearanceWillPenalty': 4,
            'mutantAmbushActive': True,
        }},
    }

    assert CombatService._skill_modifier(data, 'skills.physical.will') == -4
    assert CombatService._skill_modifier(data, 'skills.other.stealth') == 5


def test_mutants_are_immune_to_stress_and_legacy_stress_is_removed(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            'is_mutant': True,
            'health': {
                'stress': 4,
                'effects': [
                    {'type': 'stress_effect', 'active': True},
                    {'type': 'bleeding_external_light', 'active': True},
                ],
            },
        }),
    )

    result = CombatService.apply_stress_trigger(target, 1, trigger='damage')

    assert result['immune'] is True
    assert result['after'] == 0
    assert target.character.data['health']['stress'] == 0
    assert [
        effect['type'] for effect in target.character.data['health']['effects']
    ] == ['bleeding_external_light']


def test_battle_cry_deafness_threshold_is_90():
    assert CombatService._deafness_level({
        'health': {'effects': [{'type': 'deafness', 'value': 89, 'active': True}]},
    }) == 89
    assert CombatService._deafness_level({
        'health': {'effects': [{'type': 'deafness', 'value': 90, 'active': True}]},
    }) == 90
    assert CombatService._deafness_level({
        'health': {'effects': [{'type': 'deafness', 'value': 100, 'active': False}]},
    }) == 0


def test_psi_defense_sums_will_armor_gas_mask_and_temporary_bonus(monkeypatch):
    monkeypatch.setattr(
        combat_module,
        'artifact_passive_profile',
        lambda data: {
            'protection': {'psi': 5},
            'skill_physical': 0, 'skill_other': 0,
            'skill_strength': 0, 'skill_will': 0,
            'skill_awareness': 0, 'skill_stealth': 0,
        },
    )
    data = {
        'skills': {'physical': {'will': {'base': 18, 'bonus': 0}}},
        'equipment': {
            'armor': {'name': 'Комбинезон Купол', 'durability': 100},
            'gasMask': {
                'name': 'Противогаз ГП-5', 'durability': 20,
                'category': 'gas_mask',
            },
        },
        'health': {'combatMeta': {'consumableModifiers': [
            {'stat': 'psy_defense', 'value': 10, 'remaining': 2},
        ]}},
    }

    profile = CombatService._psi_defense_profile(data)

    assert profile['will'] == 20
    assert profile['artifacts'] == 5
    assert profile['armor'] == 35
    assert profile['temporary'] == 10
    assert profile['total'] == 70


def test_incoming_psi_state_is_reduced_by_total_defense(monkeypatch):
    monkeypatch.setattr(
        CombatService,
        '_psi_defense_profile',
        staticmethod(lambda data: {
            'total': 40, 'will_bonus': 0, 'will': 0, 'artifacts': 0,
            'armor': 40, 'temporary': 0, 'equipment': [],
        }),
    )
    data = {'health': {'psyState': 5}}

    result = CombatService._increase_psy_state(data, 10, 'test')

    assert result['received'] == 6
    assert result['prevented'] == 4
    assert result['after'] == 11
    assert data['health']['psy_state'] == 11


@pytest.mark.parametrize(('psy_state', 'deafness'), [
    (19, 0), (20, 50), (39, 50), (40, 100),
])
def test_psi_threshold_sets_minimum_deafness(psy_state, deafness):
    assert CombatService._deafness_level({
        'health': {'psyState': psy_state, 'deafness': 0},
    }) == deafness


def test_psi_state_recovers_once_at_end_of_round(monkeypatch):
    monkeypatch.setattr(combat_module, 'flag_modified', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        CombatService,
        '_sync_location_effects_from_character',
        staticmethod(lambda loc_char: loc_char),
    )
    character = SimpleNamespace(
        id=10, name='Test', data={'health': {'psyState': 20}},
    )
    loc_char = SimpleNamespace(character=character, character_id=10)

    result = CombatService._apply_end_of_round_psi_recovery([loc_char])

    assert character.data['health']['psyState'] == 19
    assert character.data['health']['psy_state'] == 19
    assert result[0]['before'] == 20
    assert result[0]['after'] == 19


def test_psi_increase_waits_for_turn_end_and_reset_damage_causes_pain(monkeypatch):
    monkeypatch.setattr(
        CombatService,
        '_psi_defense_profile',
        staticmethod(lambda data: {
            'total': 0, 'will_bonus': 0, 'will': 0, 'artifacts': 0,
            'armor': 0, 'temporary': 0, 'equipment': [],
        }),
    )
    data = {
        'health': {
            'current': 700, 'max': 700, 'psyState': 49, 'effects': [],
            'zones': {'head': {'current': 50, 'max': 50}},
        },
    }

    result = CombatService._increase_psy_state(data, 1, 'test')
    assert result['crisis'] is None
    assert data['health']['psyState'] == 50
    assert data['health']['current'] == 700
    crisis = CombatService._resolve_psi_crisis(data,round_number=1)
    assert crisis['reset_damage'] == 500
    assert data['health']['psyState'] == 0
    assert data['health']['current'] == 200
    assert data['health']['painLevel'] == 10
    assert data['health']['zones']['head']['current'] == 50


def test_successful_psi_suicide_shot_kills_without_reset_damage(monkeypatch):
    monkeypatch.setattr(
        CombatService,
        '_psi_defense_profile',
        staticmethod(lambda data: {
            'total': 0, 'will_bonus': 0, 'will': 0, 'artifacts': 0,
            'armor': 0, 'temporary': 0, 'equipment': [],
        }),
    )
    monkeypatch.setattr(combat_module.random, 'randint', lambda start, end: 10)
    data = {
        'weapons': [{
            'name': 'Пистолет', 'category': 'firearm', 'ammo': 1,
            'durability': 100, 'maxDurability': 100,
            'attributes': {'damage': 50, 'armor_piercing': 20},
        }],
        'health': {'current': 700, 'max': 700, 'psyState': 49, 'effects': []},
    }
    loc_char = SimpleNamespace(
        character=SimpleNamespace(data=data), drawn_weapon_index=0,
        posture='standing', cover_object_id=None,
        weapon_braced=False, braced_weapon_index=None,
    )

    result = CombatService._increase_psy_state(
        data, 1, 'test', loc_char=loc_char,
    )

    assert result['crisis'] is None
    assert CombatService._character_condition(data)['state'] != 'dead'
    attempt = CombatService._resolve_psi_suicide_attempt(data,loc_char)
    assert attempt['success'] is True
    assert data['health']['psyState'] == 50
    assert CombatService._character_condition(data)['state'] == 'dead'
    assert data['weapons'][0]['ammo'] == 0


def test_psi_suicide_jam_causes_reset_instead_of_death(monkeypatch):
    monkeypatch.setattr(combat_module.random, 'randint', lambda start, end: 1)
    data = {
        'weapons': [{
            'name': 'Изношенный пистолет', 'category': 'firearm', 'ammo': 1,
            'durability': 90, 'maxDurability': 100,
            'attributes': {'damage': 50, 'armor_piercing': 20},
        }],
        'health': {'current': 700, 'max': 700, 'psyState': 50, 'effects': []},
    }
    loc_char = SimpleNamespace(
        character=SimpleNamespace(data=data), drawn_weapon_index=0,
        posture='standing', cover_object_id=None,
        weapon_braced=False, braced_weapon_index=None,
    )

    attempt = CombatService._resolve_psi_suicide_attempt(data,loc_char)
    assert attempt['failure_reason'] == 'weapon_jammed'
    assert data['health']['psyState'] == 50 and data['health']['current'] == 700
    crisis = CombatService._resolve_psi_crisis(data, loc_char)
    assert crisis['reset_damage'] == 500
    assert data['health']['psyState'] == 0
    assert data['weapons'][0]['ammo'] == 1


def test_psi_suicide_shot_fails_when_ear_protection_is_not_penetrated(monkeypatch):
    monkeypatch.setattr(combat_module.random, 'randint', lambda start, end: 10)
    data = {
        'activeWeaponIndex': 0,
        'weapons': [{
            'name': 'Пистолет', 'category': 'firearm', 'ammo': 1,
            'durability': 100, 'maxDurability': 100,
            'attributes': {'damage': 50, 'armor_piercing': 20},
        }],
        'equipment': {'helmet': {
            'name': 'Закрытый шлем', 'durability': 20,
            'protection': {'physical': 40},
            'attributes': {'protection_zones': ['ears']},
        }},
        'health': {'current': 700, 'max': 700, 'psyState': 50, 'effects': []},
    }

    attempt = CombatService._resolve_psi_suicide_attempt(data)

    assert attempt['success'] is False
    assert attempt['failure_reason'] == 'armor_not_penetrated'
    assert attempt['armor_zone'] == 'ears'
    assert attempt['armor'] == 40
    assert attempt['armor_piercing'] == 20
    assert data['weapons'][0]['ammo'] == 0


def test_death_from_psi_reset_transforms_character_into_zombie(monkeypatch):
    monkeypatch.setattr(combat_module, 'flag_modified', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        CombatService,
        '_psi_defense_profile',
        staticmethod(lambda data: {
            'total': 0, 'will_bonus': 0, 'will': 0, 'artifacts': 0,
            'armor': 0, 'temporary': 0, 'equipment': [],
        }),
    )
    data = {
        'health': {
            'current': 500, 'max': 700, 'psyState': 49, 'effects': [],
            'zones': {'head': {'current': 50, 'max': 50}},
        },
        'inventory': {'pockets': [{'name': 'КПК'}]},
    }
    character = SimpleNamespace(id=7, name='Test', data=data)
    loc_char = SimpleNamespace(
        character=character, character_id=7, drawn_weapon_index=None,
        location=SimpleNamespace(lobby=SimpleNamespace(gm_id=1)),
        controlled_by=2, posture='standing', cover_object_id=None,
        weapon_braced=False, braced_weapon_index=None,
    )

    result = CombatService._increase_psy_state(
        data, 1, 'test', loc_char=loc_char,
    )

    assert result['crisis'] is None
    crisis = CombatService._resolve_psi_crisis(data,loc_char)
    assert crisis['transformed']['mutant_type'] == 'Зомбированный'
    assert character.data['basic']['mutant_type'] == 'Зомбированный'
    assert character.data['inventory']['pockets'][0]['name'] == 'КПК'
    assert loc_char.controlled_by == 1
    assert loc_char.posture == 'prone'


@pytest.fixture
def psi_turn_game(client,create_user,auth_headers):
    from app.extensions import db
    from test_character_interactions import _setup_pair
    pair = _setup_pair(client,create_user,auth_headers)
    for actor in (pair['actor_location'],pair['target_location']):
        value = deepcopy(actor.character.data)
        value['health'] = {'current':700,'max':700,'psyState':0,'effects':[],
            'zones':{'head':{'current':50,'max':50},'chest':{'current':150,'max':150}}}
        actor.character.data = value
    db.session.commit()
    return pair


def _give_psi_weapon(actor,*,psy=50,attacks=None,damage=20,firearm=False):
    from app.extensions import db
    value = deepcopy(actor.character.data)
    value['health']['psyState'] = psy
    value['activeWeaponIndex'] = 0
    value['weapons'] = [{'name':'Проверочное оружие','category':'firearm' if firearm else 'melee_weapon',
        'ammo':2,'durability':100,'maxDurability':100,
        'attributes':{'damage':damage,'armor_piercing':20,'allowed_attacks':attacks or ['Рубящий']}}]
    actor.character.data = value
    actor.drawn_weapon_index = 0
    db.session.commit()


def _end_psi_turn(client,pair,auth_headers,actor):
    url = f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat/end_turn"
    response = client.post(url,headers=auth_headers(pair['actor_user']),json={'location_character_id':actor.id})
    assert response.status_code == 200,response.get_json()
    return response.get_json()


def test_entering_real_anomaly_causes_pain_including_extra_at_150(client,psi_turn_game,auth_headers):
    from app.extensions import db
    pair = psi_turn_game
    actor,target = pair['actor_location'],pair['target_location']
    target.pos_x = 4
    actor.facing_x,actor.facing_y = 1,0
    actor.movement_points_current = 10
    tiles = [[{'terrain':'grass','height':0,'objects':[]} for _ in range(6)] for _ in range(6)]
    tiles[1][2]['objects'] = [{'type':'anomaly','anomalyKey':'morozilnik'}]
    pair['location'].grid_width = pair['location'].grid_height = 6
    pair['location'].tiles_data = tiles
    db.session.commit()
    moved,_,_ = CombatService.move_character(pair['location'].id,pair['actor_user']['id'],
        actor.character_id,3,1,movement_mode='walk')
    assert (moved.pos_x,moved.pos_y) == (2,1)
    assert actor.character.data['health']['combatMeta']['activeAnomaly']['key'] == 'morozilnik'
    assert actor.character.data['health']['current'] == 700
    _end_psi_turn(client,pair,auth_headers,actor)
    db.session.expire_all()
    health = actor.character.data['health']
    assert health['current'] == 550
    assert health['painLevel'] == 4  # 2 за накопленные 150 и ещё 2 за одно попадание.
    assert health['combatMeta']['damageTakenThisRound'] == 150


def test_three_real_psion_strikes_reset_only_at_victims_own_end(client,psi_turn_game,auth_headers,monkeypatch):
    from app.extensions import db
    from app.services.world_rules import mutant_character_data,mutant_profile
    pair = psi_turn_game
    psion,victim = pair['actor_location'],pair['target_location']
    psion.character.data = mutant_character_data(mutant_profile('Псионик'))
    psion.facing_x,psion.facing_y = 1,0
    _give_psi_weapon(victim,psy=0)
    # Начальное условие: ГМ добавил 4 ОД, чтобы оплатить три настоящие атаки по 3 ОД.
    CombatService.adjust_resources(pair['location'].id,pair['actor_user']['id'],psion.id,action_points=4)
    monkeypatch.setattr('app.services.combat.random.randint',lambda *_:20)
    url = f"/lobbies/{pair['lobby'].id}/locations/{pair['location'].id}/combat"
    for expected in (20,40,60):
        response = client.post(url+'/action',headers=auth_headers(pair['actor_user']),json={
            'location_character_id':psion.id,'action_key':'mutant_psy_attack',
            'weapon_index':0,'target_character_id':victim.character_id,
        })
        assert response.status_code == 200,response.get_json()
        db.session.expire_all()
        assert victim.character.data['health']['psyState'] == expected
        assert victim.character.data['health']['current'] == 700
        assert not victim.character.data['health'].get('combatMeta',{}).get('lastPsiTurnStart')
    assert psion.action_points_current == 0
    started = _end_psi_turn(client,pair,auth_headers,psion)
    assert started['current_location_character_id'] == victim.id
    assert victim.character.data['health']['psyState'] == 60
    assert victim.character.data['health']['current'] == 680
    assert victim.character.data['health']['zones']['head']['current'] == 30
    finished = _end_psi_turn(client,pair,auth_headers,victim)
    assert finished['psy_crisis']['reset_damage'] == 600
    db.session.expire_all()
    assert victim.character.data['health']['psyState'] == 0
    assert victim.character.data['health']['current'] == 80
    assert victim.character.data['health']['painLevel'] == 10


def test_psi_received_during_own_turn_resets_only_at_own_end_and_causes_pain(client,psi_turn_game,auth_headers):
    from app.extensions import db
    from sqlalchemy.orm.attributes import flag_modified
    pair = psi_turn_game
    actor = pair['actor_location']
    _give_psi_weapon(actor,psy=49)
    value = actor.character.data
    gained = CombatService._increase_psy_state(value,1,'test',loc_char=actor,apply_protection=False)
    actor.character.data = deepcopy(value)
    flag_modified(actor.character,'data')
    db.session.commit()
    assert gained['after'] == 50 and gained['crisis'] is None
    assert actor.character.data['health']['current'] == 700
    assert 'lastPsiTurnStart' not in actor.character.data['health'].get('combatMeta',{})
    result = _end_psi_turn(client,pair,auth_headers,actor)
    db.session.expire_all()
    health = actor.character.data['health']
    assert health['psyState'] == 0 and health['current'] == 200
    assert health['painLevel'] == 10
    assert health['zones']['head']['current'] == 50
    assert result['psy_crisis']['reset_damage'] == 500
    assert pair['target_character'].data['health']['current'] == 700


def test_psi_received_outside_turn_attacks_at_start_then_waits_until_own_end(client,psi_turn_game,auth_headers):
    from app.extensions import db
    pair = psi_turn_game
    target = pair['target_location']
    _give_psi_weapon(target,damage=20)
    result = _end_psi_turn(client,pair,auth_headers,pair['actor_location'])
    db.session.expire_all()
    health = target.character.data['health']
    assert health['psyState'] == 50 and health['current'] == 680
    assert health['zones']['head']['current'] == 30
    assert target.action_points_current == 5 and target.free_actions_current == 1
    assert result['psy_turn_start']['attack_type'] == 'Рубящий'
    # Чтение состояния и перезагрузка не являются началом нового хода.
    for _ in range(2):
        CombatService.get_state(pair['location'].id,pair['actor_user']['id'])
    assert target.character.data['health']['current'] == 680
    _end_psi_turn(client,pair,auth_headers,target)
    db.session.expire_all()
    assert target.character.data['health']['psyState'] == 0
    assert target.character.data['health']['current'] == 180
    assert target.character.data['health']['painLevel'] == 10


@pytest.mark.parametrize('attacks,selected,damage',[
    (['Вспарывающий','Режущий','Колющий','Рубящий'],'Рубящий',20),
    (['Режущий','Колющий'],'Колющий',25),
    (['Вспарывающий','Режущий'],'Режущий',15),
    (['Вспарывающий'],'Вспарывающий',27),
])
def test_psi_melee_uses_attack_priority_real_damage_and_ignores_helmet(psi_turn_game,attacks,selected,damage):
    from app.extensions import db
    actor = psi_turn_game['actor_location']
    _give_psi_weapon(actor,attacks=attacks)
    value = deepcopy(actor.character.data)
    helmet = {'name':'Шлем','durability':100,'protection':{'physical':100},'attributes':{'protection_zones':['head','ears']}}
    value['equipment'] = {'helmet':deepcopy(helmet)}
    actor.character.data = value
    db.session.commit()
    result = CombatService._resolve_psi_turn_start(actor,1)
    assert result['attack_type'] == selected and result['damage'] == damage
    assert result['ignores_armor'] is True and result['success'] is False
    assert actor.character.data['health']['zones']['head']['current'] == 50-damage
    assert actor.character.data['health']['current'] == 700-damage
    assert actor.character.data['equipment']['helmet'] == helmet


@pytest.mark.parametrize('damage,dead',[(49,False),(50,True),(100,True)])
def test_psi_melee_survival_depends_on_damage(psi_turn_game,damage,dead):
    actor = psi_turn_game['actor_location']
    _give_psi_weapon(actor,damage=damage)
    result = CombatService._resolve_psi_turn_start(actor,1)
    assert result['success'] is dead
    assert (CombatService._character_condition(actor.character.data)['state']=='dead') is dead


@pytest.mark.parametrize('drawn,attacks,natural',[(None,['Рубящий'],False),(0,['Дробящий','Круговая атака'],False),(0,['Рубящий'],True)])
def test_psi_does_not_draw_a_spare_weapon_or_use_natural_weapons(drawn,attacks,natural):
    value = {'weapons':[
        {'name':'Оружие в руке','category':'melee_weapon','attributes':{'allowed_attacks':attacks,'natural_weapon':natural}},
        {'name':'Нож в инвентаре','category':'melee_weapon','attributes':{'allowed_attacks':['Рубящий']}}]}
    assert CombatService._psi_suicide_weapon(value,SimpleNamespace(drawn_weapon_index=drawn)) == (None,None,None)


def test_psi_round_recovery_does_not_erase_pending_threshold(psi_turn_game):
    actor = psi_turn_game['actor_location']
    _give_psi_weapon(actor)
    assert CombatService._apply_end_of_round_psi_recovery([actor]) == []
    assert actor.character.data['health']['psyState'] == 50


@pytest.mark.parametrize('psy,damage',[(49,None),(50,500),(55,550),(51.25,512.5)])
def test_psi_reset_uses_all_current_points_and_preserves_fraction(psy,damage):
    value = {'health':{'current':700,'max':700,'psyState':psy,'effects':[]}}
    result = CombatService._resolve_psi_crisis(value,round_number=4)
    if damage is None:
        assert result is None and value['health']['current']==700
    else:
        assert result['reset_damage']==damage
        assert value['health']['current']==700-damage
        assert value['health']['psyState']==0
        assert value['health']['painLevel']==10


def test_psi_reset_shares_round_pain_accumulator_with_other_hits():
    value = {'health':{'current':700,'max':700,'psyState':50,'effects':[],'painLevel':1,
        'combatMeta':{'damagePainRound':4,'damageTakenThisRound':100,'damagePainAppliedThisRound':1}}}
    CombatService._resolve_psi_crisis(value,round_number=4)
    meta = value['health']['combatMeta']
    assert meta['damagePainRound']==4 and meta['damageTakenThisRound']==600
    assert meta['damagePainAppliedThisRound']==11


def test_psi_combat_start_attacks_only_first_actual_turn_and_jam_is_free(client,psi_turn_game,auth_headers,monkeypatch):
    from app.extensions import db
    pair = psi_turn_game
    CombatService.end_combat(pair['location'].id,pair['actor_user']['id'])
    for actor in (pair['actor_location'],pair['target_location']):
        _give_psi_weapon(actor,firearm=True)
    monkeypatch.setattr(CombatService,'_roll_weapon_jam',staticmethod(lambda *args:{'triggered':True}))
    actor,target = pair['actor_location'],pair['target_location']
    CombatService.start_combat(pair['location'].id,pair['actor_user']['id'],
        location_character_ids=[actor.id,target.id],initiator_location_character_id=actor.id,initiator_first=True)
    db.session.expire_all()
    assert actor.character.data['health']['combatMeta']['lastPsiTurnStart']['attempt']['failure_reason'] == 'weapon_jammed'
    assert actor.action_points_current == 5 and actor.free_actions_current == 1
    assert actor.character.data['health']['current'] == 700
    assert target.character.data['health']['current'] == 700
    assert 'lastPsiTurnStart' not in target.character.data['health'].get('combatMeta',{})
    assert actor.character.data['weapons'][0]['ammo'] == 2


def test_psi_reset_does_not_happen_when_reaction_ends(client,psi_turn_game,auth_headers):
    from app.extensions import db
    from app.models import LocationCombatState
    pair = psi_turn_game
    actor,target = pair['actor_location'],pair['target_location']
    _give_psi_weapon(actor)
    state = LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    state.reaction_return_location_character_id = target.id
    db.session.commit()
    result = _end_psi_turn(client,pair,auth_headers,actor)
    assert result['reaction_returned'] is True
    assert actor.character.data['health']['psyState'] == 50
    assert actor.character.data['health']['current'] == 700


def test_mutant_natural_hit_and_bite_are_opportunity_attack_options():
    mutant = SimpleNamespace(
        drawn_weapon_index=None,
        character=SimpleNamespace(data={
            'basic': {'is_mutant': True},
            'weapons': [
                {
                    'name': 'Удар',
                    'category': 'melee_weapon',
                    'attributes': {
                        'natural_weapon': True,
                        'allowed_attacks': ['Удар'],
                    },
                },
                {
                    'name': 'Укус',
                    'category': 'melee_weapon',
                    'attributes': {
                        'natural_weapon': True,
                        'allowed_attacks': ['Укус'],
                    },
                },
                {
                    'name': 'Боевое действие',
                    'category': 'melee_weapon',
                    'attributes': {
                        'natural_weapon': True,
                        'allowed_attacks': ['Боевой клич'],
                    },
                },
            ],
        }),
    )

    assert CombatService._opportunity_attack_options(mutant) == [
        {'weapon_index': 0, 'attack_type': 'Удар', 'label': 'Удар'},
        {'weapon_index': 1, 'attack_type': 'Укус', 'label': 'Укус'},
    ]


def test_aimed_shot_can_override_hit_zone():
    assert CombatService._random_hit_zone(1, "head") == "head"


@pytest.mark.parametrize(("roll", "zone"), [
    (1, "right_leg"),
    (2, "right_leg"),
    (3, "left_leg"),
    (4, "left_leg"),
    (5, "abdomen"),
    (8, "abdomen"),
    (9, "chest"),
    (12, "chest"),
    (13, "left_arm"),
    (16, "left_arm"),
    (17, "right_arm"),
    (20, "right_arm"),
])
def test_unaimed_melee_hit_zone_follows_d20_table_without_head(roll, zone):
    assert CombatService._random_hit_zone(roll, melee=True) == zone


def test_fall_uses_agility_roll_and_breaks_both_legs_on_four_meter_failure(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 1)
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "skills": {"physical": {"agility": {"base": 5, "bonus": 0}}},
            "health": {
                "current": 700,
                "max": 700,
                "zones": {
                    "leftLeg": {"current": 100, "max": 100},
                    "rightLeg": {"current": 100, "max": 100},
                },
                "effects": [],
            },
        }),
        hp_zones={},
        posture="standing",
        cover_object_id=None,
        weapon_braced=False,
        braced_weapon_index=None,
    )

    result = CombatService.resolve_fall(target, 4)

    assert result["difficulty"] == 10
    assert result["success"] is False
    assert [leg["damage"] for leg in result["legs"]] == [80, 80]
    assert result["fracture_both_legs"] is True
    assert target.posture == "prone"
    fractures = [effect for effect in target.character.data["health"]["effects"] if effect["type"] == "fracture"]
    assert {effect["area"] for effect in fractures} == {"leftLeg", "rightLeg"}


def test_successful_fall_stays_standing_and_reduces_damage_by_leg_protection(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 20)
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(CombatService, "_target_armor", lambda *_: (50, []))
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "skills": {"physical": {"agility": {"base": 10, "bonus": 0}}},
            "health": {
                "current": 700, "max": 700,
                "zones": {
                    "leftLeg": {"current": 100, "max": 100},
                    "rightLeg": {"current": 100, "max": 100},
                },
                "effects": [],
            },
        }),
        hp_zones={}, posture="standing", cover_object_id=None,
        weapon_braced=False, braced_weapon_index=None,
    )

    result = CombatService.resolve_fall(target, 4)

    assert result["success"] is True
    assert [leg["damage"] for leg in result["legs"]] == [22, 22]
    assert target.posture == "standing"


def test_fall_uses_armor_protection_without_damaging_armor(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 20)
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    armor = {
        "name": "Leg armor",
        "category": "armor",
        "durability": 10,
        "stage": 5,
        "currentStageDurability": 0,
        "brokenDamage": 49,
        "brokenProtectionLoss": 0,
        "protection": {"physical": 0.8},
    }
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "skills": {"physical": {"agility": {"base": 10, "bonus": 0}}},
            "equipment": {"armor": armor},
            "health": {
                "current": 700,
                "max": 700,
                "zones": {
                    "leftLeg": {"current": 100, "max": 100},
                    "rightLeg": {"current": 100, "max": 100},
                },
                "effects": [],
            },
        }),
        hp_zones={}, posture="standing", cover_object_id=None,
        weapon_braced=False, braced_weapon_index=None,
    )

    result = CombatService.resolve_fall(target, 4)

    assert [leg["protection"] for leg in result["legs"]] == [25, 25]
    assert armor["durability"] == 10
    assert armor["stage"] == 5
    assert armor["brokenDamage"] == 49
    assert armor["brokenProtectionLoss"] == 0


def test_stress_trigger_checks_will_and_persists_manifestation(monkeypatch):
    rolls = iter([1, 1])
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: next(rolls))
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "skills": {"physical": {"will": {"base": 5, "bonus": 0}}},
            "health": {"stress": 0, "effects": []},
        }),
        posture="standing",
    )

    result = CombatService.apply_stress_trigger(target, 1, trigger="fright")

    assert result["after"] == 1
    assert result["difficulty"] == 7
    assert result["manifested"] is True
    assert result["table"] == "concern"
    assert result["effect_roll"] == 1
    assert target.character.data["health"]["effects"][-1]["name"] == "Боязливость"
    assert target.character.data["health"]["stress"] == 1
    effect = target.character.data["health"]["effects"][-1]
    assert effect["id"]
    assert effect["gmPending"] is True

    CombatService.resolve_stress_effect(target, effect["id"], "approve")

    assert target.character.data["health"]["stress"] == 2
    assert target.character.data["health"]["effects"][-1]["gmApproved"] is True


def test_legacy_stress_manifestation_without_id_can_be_resolved(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "health": {
                "stress": 1,
                "effects": [{
                    "id": None,
                    "type": "generic",
                    "name": "Legacy stress requirement",
                    "source": "stress_manifestation",
                    "stress_table": "concern",
                    "stress_roll": 2,
                    "active": True,
                    "gmPending": True,
                }],
            },
        }),
        posture="standing",
    )

    CombatService.resolve_stress_effect(
        target,
        "null",
        "skip",
        effect_name="Legacy stress requirement",
        stress_table="concern",
        stress_roll=2,
    )

    effect = target.character.data["health"]["effects"][0]
    assert effect["type"] == "stress_effect"
    assert effect["gmPending"] is False
    assert effect["gmSkipped"] is True


def test_stress_decrease_expires_tension_effects(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "health": {
                "stress": 7,
                "effects": [{
                    "id": "stress-penalty", "type": "stress_effect", "active": True,
                    "expires_on_stress_decrease": True,
                }],
            },
        }),
        posture="standing",
    )

    CombatService.apply_stress_trigger(
        target, -1, trigger="inspiration", check_manifestation=False,
    )

    assert target.character.data["health"]["stress"] == 6
    assert target.character.data["health"]["effects"][0]["active"] is False


def test_emotion_suppressor_can_block_stress_from_attack(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 25)
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "skills": {"physical": {"will": {"base": 10, "bonus": 0}}},
            "health": {
                "stress": 0,
                "effects": [{
                    "type": "stress_resistance",
                    "active": True,
                    "remaining": 6,
                    "stress_advantage": True,
                    "stress_attack_block_chance": 50,
                }],
            },
        }),
        posture="standing",
    )

    result = CombatService.apply_stress_trigger(
        target, 1, trigger="direct_attack",
    )

    assert result["blocked"] is True
    assert result["attack_stress_block_chance"] == 50
    assert result["attack_stress_block_roll"] == 25
    assert result["after"] == 0
    assert "rolls" not in result


def test_stress_resistance_advantage_applies_to_manifestation_not_non_attack_block(monkeypatch):
    rolls = iter([2, 18])
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: next(rolls))
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "skills": {"physical": {"will": {"base": 10, "bonus": 0}}},
            "health": {
                "stress": 0,
                "effects": [{
                    "type": "stress_resistance",
                    "active": True,
                    "remaining": 6,
                    "stress_advantage": True,
                    "stress_attack_block_chance": 50,
                }],
            },
        }),
        posture="standing",
    )

    result = CombatService.apply_stress_trigger(target, 1, trigger="fright")

    assert result["blocked"] is False
    assert result["attack_stress_block_chance"] == 0
    assert result["after"] == 1
    assert result["rolls"] == [2, 18]
    assert result["roll"] == 18
    assert result["manifested"] is False


def test_forced_stress_manifestation_uses_current_level_table(monkeypatch):
    rolls = iter([20, 12])
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: next(rolls))
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "skills": {"physical": {"will": {"base": 20, "bonus": 0}}},
            "health": {"stress": 7, "effects": []},
        }),
        posture="standing",
    )

    result = CombatService.apply_stress_trigger(target, 0, force_manifest=True)

    assert result["manifested"] is True
    assert result["table"] == "tension"
    assert result["effect"] == "Адреналин"


def test_repeated_stress_manifestations_keep_only_latest_strongest_pending(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 1)
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    target = SimpleNamespace(
        character=SimpleNamespace(data={
            "skills": {"physical": {"will": {"base": 5, "bonus": 0}}},
            "health": {"stress": 5, "effects": []},
        }),
        posture="standing",
    )

    first = CombatService.apply_stress_trigger(target, 1, trigger="damage")
    second = CombatService.apply_stress_trigger(target, 1, trigger="damage")
    third = CombatService.apply_stress_trigger(target, 1, trigger="damage")

    assert first["table"] == "stress"
    assert second["table"] == third["table"] == "tension"
    pending = [
        effect for effect in target.character.data["health"]["effects"]
        if effect.get("source") == "stress_manifestation" and effect.get("gmPending")
    ]
    assert len(pending) == 1
    assert pending[0]["stress_level"] == 8
    assert pending[0]["stress_table"] == "tension"


def test_ranged_targets_must_stay_inside_the_facing_arc():
    shooter = SimpleNamespace(pos_x=5, pos_y=5, facing_x=0, facing_y=1)

    assert CombatService._is_in_facing_arc(shooter, 5, 12)
    assert CombatService._is_in_facing_arc(shooter, 10, 10)
    assert not CombatService._is_in_facing_arc(shooter, 5, 1)
    assert not CombatService._is_in_facing_arc(shooter, 10, 4)


@pytest.mark.parametrize(
    ("shooter_mode", "target_mode", "penalty", "disadvantage"),
    [
        (None, None, 0, False),
        ("walk", None, 1, False),
        ("backward_sideways", None, 1, False),
        ("run", None, 2, False),
        ("sprint", None, 3, False),
        (None, "correction", 0, False),
        (None, "walk", 0, False),
        (None, "run", 1, False),
        (None, "sprint", 2, False),
        ("walk", "run", 2, False),
        ("sprint", "sprint", 5, False),
    ],
)
def test_shooting_movement_modifiers_follow_combat_rules(
    shooter_mode,
    target_mode,
    penalty,
    disadvantage,
):
    result = CombatService._shooting_movement_modifiers(shooter_mode, target_mode)

    assert result["difficulty_penalty"] == penalty
    assert result["disadvantage"] is disadvantage


def test_melee_attack_variants_change_damage_and_penetration():
    weapon = {
        "damage": 100,
        "armor_piercing": 20,
    }

    piercing = CombatService._weapon_damage_profile(weapon, "колющий")
    cutting = CombatService._weapon_damage_profile(weapon, "режущий")

    assert piercing["damage"] == 125
    assert piercing["armor_piercing"] == 30
    assert cutting["damage"] == 75
    assert cutting["armor_piercing"] == 10


def test_unarmed_damage_uses_strength_bonus_with_minimum_ten():
    weak = {"skills": {"physical": {"strength": {"base": 5, "bonus": 0}}}}
    strong = {"skills": {"physical": {"strength": {"base": 18, "bonus": 0}}}}

    assert CombatService._virtual_melee_profile("unarmed", character_data=weak)["damage"] == 10
    assert CombatService._virtual_melee_profile("unarmed", character_data=strong)["damage"] == 40


def test_firearm_butt_uses_grip_damage_for_weapons_up_to_one_kilogram():
    grip = CombatService._virtual_melee_profile("firearm_butt", {"weight": 0.5})
    boundary = CombatService._virtual_melee_profile("firearm_butt", {"weight": 1})
    butt = CombatService._virtual_melee_profile("firearm_butt", {"weight": 3.5})

    assert grip["damage"] == 25
    assert boundary["damage"] == 25
    assert CombatService._melee_action_cost(boundary, "firearm_butt") == 2
    assert butt["damage"] == 40
    assert grip["armor_piercing"] == 0
    assert grip["melee_damage_type"] == "crushing"
    assert butt["melee_damage_type"] == "crushing"


def test_crushing_damage_multiplier_uses_twenty_percent_plus_five_per_kilogram():
    assert CombatService._crushing_damage_multiplier({
        "melee_damage_type": "crushing",
        "weight": 0,
    }) == 0.20
    assert CombatService._crushing_damage_multiplier({
        "melee_damage_type": "Дробящий",
        "weight": 1,
    }) == 0.25
    assert CombatService._crushing_damage_multiplier({
        "melee_damage_type": "crushing",
        "weight": 3,
    }) == 0.35


def test_unarmed_crushing_attack_deals_twenty_percent_on_non_penetration(
    monkeypatch,
):
    monkeypatch.setattr(CombatService, "_target_armor", lambda data, zone: (100, []))
    monkeypatch.setattr(
        CombatService,
        "_apply_attack_damage",
        lambda *args, **kwargs: {
            "current": 698,
            "zones": {"chest": {"current": 148}},
        },
    )
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": []}))
    target = SimpleNamespace(character=SimpleNamespace(data={}), hp_zones={})

    result = CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": -1,
            "attack_type": "unarmed",
            "hit_difficulty": 1,
            "round_number": 1,
            "melee": True,
        },
        melee=True,
        attack_type="unarmed",
        aimed_zone="chest",
        forced_roll=20,
    )

    assert result["crushing_non_penetration"] is True
    assert result["crushing_damage_multiplier"] == 0.20
    assert result["base_damage"] == 10
    assert result["damage"] == 2


def test_melee_action_cost_uses_attack_and_weight_class_rules():
    assert CombatService._melee_action_cost(
        {"weight_class": "light"}, "unarmed"
    ) == 2
    assert CombatService._melee_action_cost({"weight_class": "light"}, "\u0440\u0443\u0431\u044f\u0449\u0438\u0439") == 2
    assert CombatService._melee_action_cost({"weight_class": "heavy"}, "\u0440\u0443\u0431\u044f\u0449\u0438\u0439") == 3
    assert CombatService._melee_action_cost({"weight_class": "heavy_plus"}, "\u0440\u0443\u0431\u044f\u0449\u0438\u0439") == 4
    assert CombatService._melee_action_cost({"weight_class": "heavy"}, "\u0440\u0435\u0436\u0443\u0449\u0438\u0439") == 2
    assert CombatService._melee_action_cost({"weight_class": "heavy"}, "\u043a\u043e\u043b\u044e\u0449\u0438\u0439") == 4
    assert CombatService._melee_action_cost({"weight_class": "heavy"}, "\u0432\u0441\u043f\u0430\u0440\u044b\u0432\u0430\u044e\u0449\u0438\u0439") == 5
    assert CombatService._melee_action_cost({"weight_class": "heavy"}, "\u043a\u0440\u0443\u0433\u043e\u0432\u043e\u0439") == 4


def test_block_converts_near_miss_to_half_damage_in_selected_arm(monkeypatch):
    monkeypatch.setattr(CombatService, "_target_armor", lambda *_: (0, []))
    monkeypatch.setattr(
        CombatService,
        "_apply_attack_damage",
        lambda *args, **kwargs: {
            "current": 695,
            "zones": {"leftArm": {"current": 85}},
        },
    )
    attacker = SimpleNamespace(
        character=SimpleNamespace(id=1, name="Attacker", data={"weapons": []}),
    )
    target = SimpleNamespace(
        character=SimpleNamespace(id=2, name="Defender", data={"health": {}}),
        hp_zones={},
    )

    result = CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": -1,
            "attack_type": "unarmed",
            "hit_difficulty": 12,
            "round_number": 1,
            "melee": True,
            "block_penalty": 3,
            "block_arm": "left_arm",
        },
        melee=True,
        attack_type="unarmed",
        forced_roll=10,
    )

    assert result["partial_block"] is True
    assert result["zone"] == "left_arm"
    assert result["base_damage"] == 5
    assert result["damage"] == 5


def test_deep_block_miss_can_trigger_counterattack(monkeypatch):
    counterattack = {"hit": True, "roll": 18, "difficulty": 10, "damage": 12}
    monkeypatch.setattr(
        CombatService, "_resolve_block_counterattack", lambda *args: counterattack,
    )
    attacker = SimpleNamespace(
        character=SimpleNamespace(id=1, name="Attacker", data={"weapons": []}),
    )
    target = SimpleNamespace(
        character=SimpleNamespace(id=2, name="Defender", data={"health": {}}),
        hp_zones={},
    )

    result = CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": -1,
            "attack_type": "unarmed",
            "hit_difficulty": 15,
            "round_number": 1,
            "melee": True,
            "block_penalty": 4,
            "block_counterattack": True,
        },
        melee=True,
        attack_type="unarmed",
        forced_roll=5,
    )

    assert result["hit"] is False
    assert result["block_counterattack_triggered"] is True
    assert result["counterattack_result"] is counterattack


def test_circular_attack_uses_slashing_or_crushing_damage_profile(app):
    with app.app_context():
        slashing = CombatService._weapon_damage_profile(
            {
                "attributes": {
                    "damage": 40,
                    "allowed_attacks": ["\u0420\u0443\u0431\u044f\u0449\u0438\u0439", "\u041a\u0440\u0443\u0433\u043e\u0432\u043e\u0439"],
                }
            },
            "\u041a\u0440\u0443\u0433\u043e\u0432\u043e\u0439",
        )
        crushing = CombatService._weapon_damage_profile(
            {
                "attributes": {
                    "damage": 40,
                    "allowed_attacks": ["\u0414\u0440\u043e\u0431\u044f\u0449\u0438\u0439", "\u041a\u0440\u0443\u0433\u043e\u0432\u043e\u0439"],
                }
            },
            "\u041a\u0440\u0443\u0433\u043e\u0432\u043e\u0439",
        )

    assert slashing["melee_damage_type"] == "slashing"
    assert crushing["melee_damage_type"] == "crushing"


def test_melee_adjacency_includes_diagonal_but_not_two_tiles():
    actor = SimpleNamespace(pos_x=5, pos_y=5)

    assert CombatService._is_adjacent(
        actor, SimpleNamespace(pos_x=6, pos_y=6)
    )
    assert not CombatService._is_adjacent(
        actor, SimpleNamespace(pos_x=7, pos_y=5)
    )


def test_attack_summary_lists_each_target_damage_bleeding_and_trauma():
    summary = CombatService.format_attack_summary({
        "character": {"name": "Атакующий"},
        "attack": {
            "hits": 1,
            "damage_total": 42,
            "results": [
                {
                    "target_name": "Первая цель",
                    "roll": 17,
                    "rolls": [17],
                    "difficulty": 12,
                    "hit": True,
                    "mode": "melee",
                    "zone": "right_arm",
                    "damage": 42,
                    "armor": 20,
                    "armor_piercing": 30,
                    "bleedings": [{
                        "kind": "external",
                        "stage": "medium",
                    }],
                    "additional_traumas": [{
                        "roll": 8,
                        "fracture": True,
                        "bleeding": None,
                        "pain": 1,
                        "shock": False,
                    }],
                },
                {
                    "target_name": "Вторая цель",
                    "roll": 4,
                    "rolls": [4],
                    "difficulty": 15,
                    "hit": False,
                    "mode": "melee",
                },
            ],
        },
    })

    assert "Первая цель: d20 17, СЛ 12 — попадание: правая рука, урон 42" in summary
    assert "Кровотечение: среднее внешнее." in summary
    assert "Доп. травма: d20 8 (перелом, боль +1)." in summary
    assert "Вторая цель: d20 4, СЛ 15 — промах." in summary
    assert "Итого: попаданий 1/2, урон 42." in summary


def test_attack_summary_explains_partial_block_arm_damage():
    summary = CombatService.format_attack_summary({
        "character": {"name": "Attacker"},
        "attack": {
            "results": [{
                "target_name": "Defender",
                "roll": 10,
                "difficulty": 12,
                "hit": True,
                "mode": "melee",
                "zone": "left_arm",
                "damage": 20,
                "partial_block": True,
                "block_arm": "left_arm",
            }],
        },
    })

    assert "частичный блок: половина урона приходится в левую руку" in summary


def test_attack_summary_reports_additional_trauma_organ_damage():
    summary = CombatService.format_attack_summary({
        "character": {"name": "Attacker"},
        "attack": {
            "results": [{
                "target_name": "Target",
                "roll": 20,
                "difficulty": 10,
                "hit": True,
                "mode": "melee",
                "zone": "chest",
                "damage": 35,
                "additional_traumas": [{
                    "roll": 1,
                    "organ": "heart",
                    "organ_damage": {
                        "organ": "heart",
                        "current_before": 100,
                        "current": 65,
                        "max": 100,
                        "disabled": False,
                    },
                }],
            }],
        },
    })

    assert "\u043e\u0440\u0433\u0430\u043d: \u0441\u0435\u0440\u0434\u0446\u0435 \u041e\u0417 100 -> 65/100" in summary
    assert "\u0431\u0435\u0437 \u0434\u043e\u043f. \u044d\u0444\u0444\u0435\u043a\u0442\u0430" not in summary


def test_cover_attack_summary_shows_protection_and_target_behind_cover():
    summary = CombatService.format_attack_summary({
        "character": {"name": "Стрелок"},
        "attack": {
            "results": [{
                "target_name": "Стул",
                "roll": 12,
                "rolls": [12],
                "difficulty": 5,
                "hit": True,
                "mode": "unaimed",
                "cover_hit": True,
                "cover_penetrated": True,
                "cover_protection": 20,
                "cover_damage": {
                    "physical_protection": 0,
                    "hp": 100,
                    "max_hp": 100,
                },
                "target_behind_cover_name": "Цель",
                "target_behind_cover_result": {
                    "hit": True,
                    "zone": "chest",
                    "damage": 15,
                },
            }],
        },
    })

    assert "защита 20% → 0%, ОЗ 100/100; пробитие: да" in summary
    assert "За укрытием: Цель, грудь, урон 15" in summary


@pytest.mark.parametrize(
    ("effects", "expected_state"),
    [
        ([{"type": "pain_shock", "name": "Болевой шок"}], "pain_shock"),
        ([{"type": "unconscious", "name": "Без сознания"}], "critical"),
        ([{"type": "critical_condition"}], "critical"),
        ([{"type": "death"}], "dead"),
    ],
)
def test_character_condition_recognizes_incapacitating_effects(effects, expected_state):
    condition = CombatService._character_condition({"health": {"current": 100, "effects": effects}})

    assert condition["state"] == expected_state
    assert condition["can_act"] is False


def test_pain_shock_only_allows_recovery_attempt():
    character = SimpleNamespace(
        character=SimpleNamespace(
            data={"health": {"current": 100, "effects": [{"type": "shock"}]}}
        )
    )

    with pytest.raises(ValidationError, match="Only an attempt"):
        CombatService.ensure_character_can_act(character, "attack")

    condition = CombatService.ensure_character_can_act(character, "recover_from_shock")
    assert condition["can_recover"] is True


def test_pain_shock_recovery_uses_only_will_bonus_and_ammonia_bonus():
    data = {
        "skills": {"physical": {"will": {"base": 5, "bonus": 0}}},
        "health": {
            "painLevel": 9,
            "exhaustion": 4,
            "bloodStage": "severe",
            "temperature": 40,
            "effects": [],
        },
    }

    difficulty, will_bonus = CombatService._pain_shock_recovery_difficulty(data)
    ammonia_difficulty, _ = CombatService._pain_shock_recovery_difficulty(data, 2)

    assert will_bonus == -3
    assert difficulty == 15
    assert ammonia_difficulty == 13


@pytest.mark.parametrize("effect_type", ["critical_condition", "death"])
def test_critical_and_dead_characters_cannot_use_recovery(effect_type):
    character = SimpleNamespace(
        character=SimpleNamespace(
            data={"health": {"current": 100, "effects": [{"type": effect_type}]}}
        )
    )

    with pytest.raises(ValidationError):
        CombatService.ensure_character_can_act(character, "recover_from_shock")


def test_end_turn_pain_shock_check_uses_pain_times_two_minus_will_bonus(monkeypatch):
    data = {
        "skills": {"physical": {"will": {"base": 14, "bonus": 0}}},
        "health": {"current": 100, "painLevel": 6, "effects": []},
    }
    character = SimpleNamespace(data=data)
    location_character = SimpleNamespace(
        character=character,
        posture="standing",
        cover_object_id=1,
        weapon_braced=True,
        braced_weapon_index=0,
    )
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: 9)
    monkeypatch.setattr(combat_module, "flag_modified", lambda instance, key: None)

    result = CombatService._resolve_pain_shock_check(location_character, 2)

    assert result["will_bonus"] == 2
    assert result["difficulty"] == 10
    assert result["success"] is False
    assert location_character.posture == "prone"
    assert CombatService._character_condition(data)["state"] == "pain_shock"
    assert data["health"]["painLevel"] == 6


def test_bleeding_check_keeps_base_will_bonus_separate_from_blood_penalty():
    data = {
        "skills": {"physical": {"will": {"base": 5, "bonus": 0}}},
        "health": {
            "blood": "light",
            "effects": [{
                "type": "bleeding_external_severe",
                "area": "rightArm",
            }],
        },
    }
    sync_health_derived_statuses(data["health"])

    profile = CombatService._bleeding_check_profile(data)

    assert profile["willBonus"] == -3
    assert profile["stateModifier"] == 0
    assert profile["severity"] == 5
    assert profile["stagePenalty"] == 1
    assert profile["difficulty"] == 12


def test_bleeding_check_applies_exhaustion_without_changing_will_bonus():
    data = {
        "skills": {"physical": {"will": {"base": 5, "bonus": 0}}},
        "health": {
            "blood": "normal",
            "exhaustion": 1,
            "effects": [{
                "type": "bleeding_external_light",
                "area": "leftArm",
            }],
        },
    }
    sync_health_derived_statuses(data["health"])

    profile = CombatService._bleeding_check_profile(data)

    assert profile["willBonus"] == -3
    assert profile["stateModifier"] == -1
    assert profile["difficulty"] == 10


def test_pain_shock_check_is_skipped_in_recovery_round(monkeypatch):
    data = {
        "skills": {"physical": {"will": {"base": 10, "bonus": 0}}},
        "health": {
            "current": 100,
            "painLevel": 5,
            "effects": [],
            "combatMeta": {"painShockRecovered": True},
        },
    }
    location_character = SimpleNamespace(character=SimpleNamespace(data=data))
    monkeypatch.setattr(
        combat_module.random,
        "randint",
        lambda start, end: pytest.fail("No roll should be made"),
    )

    assert CombatService._resolve_pain_shock_check(location_character, 4) is None
    assert data["health"]["painLevel"] == 5


def test_pain_below_five_does_not_require_shock_check(monkeypatch):
    data = {"health": {"current": 100, "painLevel": 4, "effects": []}}
    location_character = SimpleNamespace(character=SimpleNamespace(data=data))
    monkeypatch.setattr(
        combat_module.random,
        "randint",
        lambda start, end: pytest.fail("No roll should be made"),
    )

    assert CombatService._resolve_pain_shock_check(location_character, 2) is None


def test_recovered_character_checks_again_after_running(monkeypatch):
    data = {
        "skills": {"physical": {"will": {"base": 10, "bonus": 0}}},
        "health": {
            "current": 100,
            "painLevel": 5,
            "effects": [],
            "combatMeta": {"painShockRecovered": True},
        },
    }
    location_character = SimpleNamespace(
        character=SimpleNamespace(data=data),
        movement_mode_this_turn="run",
        posture="standing",
        cover_object_id=None,
        weapon_braced=False,
        braced_weapon_index=None,
    )
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: 9)
    monkeypatch.setattr(combat_module, "flag_modified", lambda instance, key: None)

    result = CombatService._resolve_pain_shock_check(location_character, 3)

    assert result["difficulty"] == 10
    assert result["recovered_triggers"]["strenuous_movement"] is True
    assert result["success"] is False


def test_pain_ten_applies_guaranteed_shock_and_blocks_recovery():
    health = {"current": 100, "painLevel": 10, "effects": []}

    sync_health_derived_statuses(health)
    data = {"health": health}
    condition = CombatService._character_condition(data)

    assert condition["state"] == "pain_shock"
    assert condition["can_recover"] is False
    with pytest.raises(ValidationError, match="reduced below 10"):
        CombatService.ensure_character_can_act(
            SimpleNamespace(character=SimpleNamespace(data=data)),
            "recover_from_shock",
        )


def test_back_attack_uses_target_facing():
    target = SimpleNamespace(pos_x=5, pos_y=5, facing_x=0, facing_y=1)

    assert CombatService._is_behind(
        SimpleNamespace(pos_x=5, pos_y=4), target
    )
    assert not CombatService._is_behind(
        SimpleNamespace(pos_x=5, pos_y=6), target
    )


def test_back_melee_attack_ignores_block_and_aimed_penalty():
    attacker = SimpleNamespace(pos_x=5, pos_y=4)
    target = SimpleNamespace(
        pos_x=5,
        pos_y=5,
        facing_x=0,
        facing_y=1,
        posture="standing",
        melee_block_effectiveness=6,
        grappled_by_id=None,
        character=SimpleNamespace(data={"health": {"effects": []}}),
    )

    profile = CombatService._melee_target_profile(
        attacker,
        target,
        melee_bonus=2,
        accuracy=1,
        aimed=True,
        target_zone="head",
    )

    assert profile["from_behind"] is True
    assert profile["block_penalty"] == 0
    assert profile["aimed_penalty"] == 0
    assert profile["difficulty"] == 3


def test_prone_target_grants_melee_advantage():
    attacker = SimpleNamespace(pos_x=5, pos_y=6)
    target = SimpleNamespace(
        pos_x=5,
        pos_y=5,
        facing_x=0,
        facing_y=1,
        posture="prone",
        melee_block_effectiveness=0,
        grappled_by_id=None,
        character=SimpleNamespace(data={"health": {"effects": []}}),
    )

    profile = CombatService._melee_target_profile(attacker, target, melee_bonus=0)

    assert profile["from_behind"] is False
    assert profile["target_prone"] is True
    assert profile["advantage"] is True


def test_melee_attack_automatically_hits_unconscious_target(monkeypatch):
    monkeypatch.setattr(
        CombatService,
        "_virtual_melee_profile",
        lambda *args, **kwargs: {
            "damage": 10,
            "armor_piercing": 0,
            "accuracy": 0,
            "bleeding": "",
            "melee_damage_type": "crushing",
            "skip_strength_scaling": True,
        },
    )
    monkeypatch.setattr(CombatService, "_target_armor", lambda *args: (0, []))
    monkeypatch.setattr(
        CombatService,
        "_apply_attack_damage",
        lambda *args, **kwargs: {
            "current": 90,
            "zones": {"rightLeg": {"current": 90}},
            "_attackOutcome": {},
        },
    )
    attacker = SimpleNamespace(
        pos_x=0,
        pos_y=0,
        character=SimpleNamespace(data={"skills": {"physical": {}}}),
    )
    target = SimpleNamespace(
        pos_x=0,
        pos_y=1,
        facing_x=0,
        facing_y=-1,
        posture="prone",
        melee_block_effectiveness=4,
        character_id=2,
        character=SimpleNamespace(
            id=2,
            name="Target",
            data={"health": {"effects": [{"type": "unconsciousness"}]}},
        ),
        hp_zones={},
        grappled_by_id=None,
    )
    target_profile = CombatService._melee_target_profile(attacker, target, melee_bonus=0)

    result = CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": -1,
            "hit_difficulty": 99,
            "automatic_hit": target_profile["automatic_hit"],
            "target_unconscious": target_profile["target_unconscious"],
            "round_number": 1,
        },
        melee=True,
        attack_type="unarmed",
        forced_roll=1,
    )

    assert result["hit"] is True
    assert result["automatic_hit"] is True
    assert result["roll"] is None
    assert result["rolls"] == []


def test_failed_bleeding_check_at_critical_stage_causes_death(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: 1)
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    data = {
        "skills": {"physical": {"will": {"base": 5, "bonus": 0}}},
        "health": {
            "current": 300,
            "blood": "critical",
            "bloodStage": "critical",
            "effects": [{"type": "bleeding_external_light", "area": "leftArm"}],
        },
    }
    loc_char = SimpleNamespace(
        character=SimpleNamespace(data=data),
        posture="standing",
        cover_object_id=12,
        weapon_braced=True,
        braced_weapon_index=0,
    )

    result = CombatService._resolve_bleeding_check(loc_char)

    assert result["success"] is False
    assert result["bloodStage"] == "fatal"
    assert result["death"] is True
    assert data["health"]["bloodStage"] == "fatal"
    assert any(
        effect["type"] == "death" and effect["source"] == "blood_loss"
        for effect in data["health"]["effects"]
    )
    assert CombatService._character_condition(data)["state"] == "dead"
    assert loc_char.posture == "prone"
    assert loc_char.cover_object_id is None


def test_duplet_reuses_one_hit_roll_for_sequential_impacts(monkeypatch):
    forced_rolls = []

    def resolve(target, attacker, details, **kwargs):
        forced_rolls.append(kwargs.get("forced_roll"))
        return {
            "roll": kwargs.get("forced_roll") or 14,
            "hit": True,
            "damage": 10,
        }

    monkeypatch.setattr(CombatService, "_resolve_attack", resolve)
    results = CombatService._resolve_shot_sequence(
        [SimpleNamespace()],
        SimpleNamespace(),
        {"shot_count": 2},
        share_hit_roll=True,
    )

    assert forced_rolls == [None, 14]
    assert len(results) == 2
    assert all(result["shared_hit_roll"] for result in results)


def test_cover_continuation_only_accepts_three_cells_on_the_shot_line():
    shooter = SimpleNamespace(pos_x=0, pos_y=0)
    cover = SimpleNamespace(
        tile_x=2,
        tile_y=0,
        type="crate",
        properties={"dimensions": {"width": 1, "depth": 1}},
    )

    assert CombatService._cover_continuation_distance(
        shooter, cover, SimpleNamespace(pos_x=3, pos_y=0)
    ) == pytest.approx(0.5)
    assert CombatService._cover_continuation_distance(
        shooter, cover, SimpleNamespace(pos_x=5, pos_y=0)
    ) == pytest.approx(2.5)
    assert CombatService._cover_continuation_distance(
        shooter, cover, SimpleNamespace(pos_x=6, pos_y=0)
    ) is None
    assert CombatService._cover_continuation_distance(
        shooter, cover, SimpleNamespace(pos_x=3, pos_y=1)
    ) is None


@pytest.mark.parametrize(
    'shooter_position,target_position,on_ray',
    [
        ((0, 0), (4, 2), False),
        ((0, 0), (4, 4), True),
        ((0, 4), (4, 2), False),
        ((0, 4), (4, 0), True),
        ((2, 0), (4, 2), False),
        ((2, 0), (2, 4), True),
    ],
)
def test_cover_continuation_follows_shooter_direction_not_proximity(shooter_position, target_position, on_ray):
    shooter = SimpleNamespace(pos_x=shooter_position[0], pos_y=shooter_position[1])
    cover = SimpleNamespace(tile_x=2, tile_y=2, type='crate',
                            properties={'dimensions': {'width': 1, 'depth': 1}})
    target = SimpleNamespace(pos_x=target_position[0], pos_y=target_position[1])
    assert (CombatService._cover_continuation_distance(shooter, cover, target) is not None) is on_ray


def test_characters_behind_cover_include_second_and_third_cells(monkeypatch):
    shooter = SimpleNamespace(id=1, pos_x=0, pos_y=0)
    second_cell = SimpleNamespace(id=2, pos_x=4, pos_y=0)
    third_cell = SimpleNamespace(id=3, pos_x=5, pos_y=0)
    too_far = SimpleNamespace(id=4, pos_x=6, pos_y=0)
    cover = SimpleNamespace(
        tile_x=2,
        tile_y=0,
        type="crate",
        properties={"dimensions": {"width": 1, "depth": 1}},
    )

    class FakeQuery:
        def filter_by(self, **kwargs):
            return self

        def all(self):
            return [shooter, third_cell, too_far, second_cell]

    monkeypatch.setattr(
        combat_module,
        "LocationCharacter",
        SimpleNamespace(query=FakeQuery()),
    )

    assert CombatService._characters_behind_cover(1, shooter, cover) == [
        second_cell,
        third_cell,
    ]


def test_cover_attack_summary_reports_secondary_miss_without_unknown_zone():
    summary = CombatService.format_attack_summary({
        "character": {"name": "Стрелок"},
        "attack": {
            "results": [{
                "target_name": "Ящик",
                "roll": 15,
                "rolls": [15],
                "difficulty": 5,
                "hit": True,
                "mode": "unaimed",
                "cover_hit": True,
                "cover_penetrated": True,
                "cover_protection": 20,
                "cover_damage": {"physical_protection": 0, "hp": 0, "max_hp": 50},
                "target_behind_cover_name": "Цель",
                "target_behind_cover_result": {
                    "hit": False,
                    "roll": 4,
                    "rolls": [16, 4],
                    "difficulty": 12,
                    "damage": 0,
                },
            }],
        },
    })

    assert "За укрытием: Цель, d20 16/4, СЛ 12 — промах." in summary
    assert "неизвестная зона" not in summary


def test_unaimed_blind_fire_miss_can_damage_cover(monkeypatch):
    cover = SimpleNamespace(id=7, name="Ящик", type="crate")
    attacker = SimpleNamespace(
        character=SimpleNamespace(data={"weapons": [{}]}),
    )
    target = SimpleNamespace(
        character_id=2,
        character=SimpleNamespace(id=2, name="Цель", data={}),
        grapple_live_shield=False,
        grapple_target_id=None,
    )
    rolls = iter([3])
    cover_damage_calls = []

    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: next(rolls))
    monkeypatch.setattr(
        "app.services.combat.db.session.get",
        lambda model, object_id: cover if object_id == cover.id else None,
    )
    monkeypatch.setattr(
        CombatService,
        "_cover_profile",
        lambda obj: {"physical_protection": 20},
    )

    def apply_cover_damage(obj, damage, damage_type):
        cover_damage_calls.append((obj, damage, damage_type))
        return {
            "hp": 50,
            "max_hp": 100,
            "physical_protection": 10,
        }

    monkeypatch.setattr(CombatService, "apply_cover_damage", apply_cover_damage)

    result = CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": 0,
            "fire_mode": "unaimed",
            "hit_difficulty": 12,
            "shooting_disadvantage": False,
            "cover": {
                "blind_fire": True,
                "zones": {"chest": {"object_id": cover.id}},
            },
        },
        profile_override={
            "damage": 50,
            "armor_piercing": 30,
            "damage_type": "bullet",
        },
        profile_adjusted=True,
    )

    assert result["hit"] is False
    assert result["automatic_cover_hit"] is True
    assert result["cover_hit"]["object_id"] == cover.id
    assert result["cover_hit"]["penetrated"] is True
    assert cover_damage_calls == [(cover, 50, "bullet")]

    summary = CombatService.format_attack_summary({
        "character": {"name": "Стрелок"},
        "attack": {"results": [result]},
    })
    assert "Цель: d20 3, СЛ 12 — промах." in summary
    assert "Пуля попала в Ящик: без проверки" in summary
    assert "защита 20% → 10%, ОЗ 50/100" in summary


def test_penetrated_cover_hits_nearest_target_without_another_accuracy_check(monkeypatch):
    attacker = SimpleNamespace(
        pos_x=0,
        pos_y=0,
        character=SimpleNamespace(data={"weapons": [{}]}),
    )
    target = SimpleNamespace(
        character_id=2,
        pos_x=3,
        pos_y=0,
        movement_mode_this_turn="run",
        character=SimpleNamespace(name="Target", data={}),
    )
    cover = SimpleNamespace(id=7, name="Crate", type="crate")
    resolved = {}

    monkeypatch.setattr(
        CombatService,
        "_ranged_damage_profile",
        lambda weapon: ({"damage": 50, "armor_piercing": 40}, None),
    )
    monkeypatch.setattr(
        CombatService,
        "_cover_profile",
        lambda obj: {"physical_protection": 20},
    )
    monkeypatch.setattr(
        CombatService,
        "apply_cover_damage",
        lambda *args: {"hp": 50, "max_hp": 100, "physical_protection": 10},
    )
    monkeypatch.setattr(
        CombatService,
        "_characters_behind_cover",
        lambda *args: [target],
    )

    def resolve_attack(*args, **kwargs):
        resolved["details"] = args[2]
        resolved["kwargs"] = kwargs
        return {"hit": True, "automatic_hit": True, "damage": 10}

    monkeypatch.setattr(CombatService, "_resolve_attack", resolve_attack)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: 20)

    result = CombatService._resolve_cover_attack(
        1,
        cover,
        attacker,
        {
            "weapon_index": 0,
            "fire_mode": "unaimed",
            "hit_difficulty": 5,
            "continuation_hit_difficulty": 11,
            "shooting_disadvantage": False,
            "target_distance": 2,
            "weapon_range": 10,
        },
    )

    assert resolved["details"]["shooting_disadvantage"] is False
    assert resolved["details"]["automatic_firearm_hit"] is True
    assert resolved["kwargs"]["forced_roll"] == 20
    assert resolved["kwargs"]["profile_override"]["armor_piercing"] == 20
    assert "aimed_zone" not in resolved["kwargs"]
    assert result["automatic_cover_hit"] is True
    assert result["roll"] == 20
    assert result["rolls"] == [20]
    assert result["difficulty"] is None
    assert result["target_behind_cover_result"]["hit"] is True
    assert result["damage"] == 10


def test_aimed_head_miss_hits_live_shield_head(monkeypatch):
    shield = SimpleNamespace(
        character=SimpleNamespace(data={"marker": "shield"}),
        hp_zones={},
        grapple_live_shield=False,
        grapple_target_id=None,
    )
    holder = SimpleNamespace(
        character=SimpleNamespace(data={"marker": "holder"}),
        hp_zones={},
        grapple_live_shield=True,
        grapple_target_id=2,
    )
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": [{}]}))
    monkeypatch.setattr(CombatService, "_live_shield_target", lambda target: shield)
    monkeypatch.setattr(
        CombatService,
        "_ranged_damage_profile",
        lambda weapon: ({
            "damage": 100,
            "armor_piercing": 20,
            "bleeding": "",
            "effective_range": 0,
        }, None),
    )
    monkeypatch.setattr(CombatService, "_target_armor", lambda data, zone: (0, []))
    damaged = []

    def apply_damage(target, damage, zone, profile, **kwargs):
        damaged.append((target, damage, zone))
        return {"current": 600, "zones": {"head": {"current": 0}}}

    monkeypatch.setattr(CombatService, "_apply_attack_damage", apply_damage)
    result = CombatService._resolve_attack(
        holder,
        attacker,
        {
            "weapon_index": 0,
            "hit_difficulty": 10,
            "fire_mode": "aimed",
            "round_number": 1,
        },
        aimed_zone="head",
        forced_roll=1,
    )

    assert result["hit"] is False
    assert result["live_shield_reason"] == "aimed_head_miss"
    assert damaged == [(shield, 100, "head")]


def test_live_shield_takes_same_zone_before_remaining_penetration_hits_holder(
    monkeypatch,
):
    shield = SimpleNamespace(
        character=SimpleNamespace(data={"marker": "shield"}),
        hp_zones={},
        grapple_live_shield=False,
        grapple_target_id=None,
    )
    holder = SimpleNamespace(
        character=SimpleNamespace(data={"marker": "holder"}),
        hp_zones={},
        grapple_live_shield=True,
        grapple_target_id=2,
    )
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": [{}]}))
    monkeypatch.setattr(CombatService, "_live_shield_target", lambda target: shield)
    monkeypatch.setattr(
        CombatService,
        "_ranged_damage_profile",
        lambda weapon: ({
            "damage": 100,
            "armor_piercing": 40,
            "bleeding": "",
            "effective_range": 0,
        }, None),
    )
    monkeypatch.setattr(
        CombatService,
        "_target_armor",
        lambda data, zone: ((20, []) if data.get("marker") == "shield" else (10, [])),
    )
    damaged = []

    def apply_damage(target, damage, zone, profile, **kwargs):
        damaged.append((target, damage, zone, profile["armor_piercing"]))
        return {"current": 600, "zones": {"chest": {"current": 50}}}

    monkeypatch.setattr(CombatService, "_apply_attack_damage", apply_damage)
    result = CombatService._resolve_attack(
        holder,
        attacker,
        {
            "weapon_index": 0,
            "hit_difficulty": 1,
            "fire_mode": "unaimed",
            "round_number": 1,
        },
        aimed_zone="chest",
        forced_roll=20,
    )

    assert damaged[0] == (shield, 100, "chest", 40)
    assert damaged[1] == (holder, 100, "chest", 20)
    assert result["live_shield_blocked"] is False
    assert result["combined_damage"] == 200


def test_behind_armor_damage_uses_caliber_rules_and_ammo_exceptions():
    assert CombatService._behind_armor_damage_multiplier({"caliber": "7.62x39"}) == 0.20
    assert CombatService._behind_armor_damage_multiplier({"caliber": "9х39"}) == 0.25
    assert CombatService._behind_armor_damage_multiplier({"caliber": "12.7x55"}) == 0.25
    assert CombatService._behind_armor_damage_multiplier({
        "caliber": "7.62x39", "ammo_variant": "ep",
    }) == 0
    assert CombatService._behind_armor_damage_multiplier({
        "caliber": "12x70", "ammo_name": "12x70 Картечь",
    }) == 0


def test_12x70_slug_and_exoskeleton_use_special_behind_armor_damage():
    slug = {"caliber": "12x70", "ammo_name": "12х70 Пуля"}
    expanding_slug = {**slug, "ammo_variant": "ep"}
    exoskeleton = {
        "equipment": {
            "armor": {
                "name": "Экзоскелет",
                "attributes": {"is_exoskeleton": True},
            },
        },
    }

    assert CombatService._behind_armor_damage_multiplier(slug) == pytest.approx(1 / 3)
    assert CombatService._behind_armor_damage_multiplier(expanding_slug) == 0.10
    assert CombatService._behind_armor_damage_multiplier(
        slug, target_data=exoskeleton,
    ) == pytest.approx(1 / 6)


def test_buckshot_deals_half_damage_to_mutant_on_large_non_penetration():
    buckshot = {"caliber": "12x70", "ammo_name": "12х70 Картечь"}
    mutant = {"basic": {"species": "Мутант"}}

    assert CombatService._behind_armor_damage_multiplier(
        buckshot,
        target_data=mutant,
        penetration_deficit=10,
    ) == 0.50
    assert CombatService._behind_armor_damage_multiplier(
        buckshot,
        target_data={"basic": {"species": "Человек"}},
        penetration_deficit=20,
    ) == 0


def test_full_non_penetration_applies_behind_armor_damage(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        CombatService,
        "_ranged_damage_profile",
        lambda weapon: ({
            "damage": 100,
            "armor_piercing": 0,
            "bleeding": "",
            "caliber": "7.62x39",
            "effective_range": 0,
        }, None),
    )
    monkeypatch.setattr(CombatService, "_target_armor", lambda data, zone: (100, []))
    def capture_damage(*args, **kwargs):
        captured.update(kwargs)
        return {
            "current": 680,
            "zones": {"chest": {"current": 130}},
        }
    monkeypatch.setattr(CombatService, "_apply_attack_damage", capture_damage)
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": [{}]}))
    target = SimpleNamespace(character=SimpleNamespace(data={
        "is_mutant": True,
        "mutant": {
            "traits": ["Двужильность. Сложность для получения Доп. травмы +25"],
        },
    }), hp_zones={})

    result = CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": 0,
            "hit_difficulty": 1,
            "fire_mode": "single",
            "round_number": 1,
        },
        aimed_zone="chest",
        forced_roll=20,
    )

    assert result["full_non_penetration"] is True
    assert result["behind_armor_multiplier"] == 0.20
    assert result["damage"] == 20
    assert captured["trauma_difficulty_modifier"] == 25


def test_127x55_full_penetration_checks_additional_trauma_twice(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        CombatService,
        "_ranged_damage_profile",
        lambda weapon: ({
            "damage": 130,
            "armor_piercing": 60,
            "bleeding": "",
            "caliber": "12.7x55",
            "effective_range": 0,
        }, None),
    )
    monkeypatch.setattr(CombatService, "_target_armor", lambda data, zone: (40, []))

    def capture_damage(*args, **kwargs):
        captured.update(kwargs)
        return {"current": 570, "zones": {"chest": {"current": 20}}}

    monkeypatch.setattr(CombatService, "_apply_attack_damage", capture_damage)
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": [{}]}))
    target = SimpleNamespace(character=SimpleNamespace(data={}), hp_zones={})

    CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": 0,
            "hit_difficulty": 1,
            "fire_mode": "single",
            "round_number": 1,
        },
        aimed_zone="chest",
        forced_roll=20,
    )

    assert captured["trauma_checks"] == 2


def test_additional_trauma_report_is_not_persisted_as_generic_effect(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    character = SimpleNamespace(data={
        "health": {
            "current": 700,
            "max": 700,
            "zones": {"leftArm": {"current": 90, "max": 90}},
            "effects": [],
        }
    })
    target = SimpleNamespace(
        character=character,
        hp_zones={},
        posture="standing",
        cover_object_id=None,
        weapon_braced=False,
        braced_weapon_index=None,
    )

    health = CombatService._apply_attack_damage(
        target,
        20,
        "left_arm",
        {"bleeding": ""},
        force_trauma=True,
        allow_bleeding=False,
        round_number=1,
    )

    assert health["_attackOutcome"]["additional_traumas"]
    assert not any(
        effect.get("type") in {"generic", "additional_trauma"}
        for effect in health["effects"]
    )


@pytest.mark.parametrize(
    ("roll", "attacker_position", "armor_zone", "damages_head", "head_on_penetration", "primary", "secondary", "skull"),
    [
        (3, (0, 1), "crown", True, False, None, [], True),
        (7, (0, 1), "crown", True, False, None, [], False),
        (4, (0, 1), "face", True, False, "rightEye", [], False),
        (4, (1, 0), "ears", False, True, "rightEye", ["brain"], False),
        (4, (0, -1), "back", True, False, None, ["brain"], True),
        (6, (1, 0), "face", False, False, "nose", [], False),
        (2, (1, 0), "face", False, False, "jaw", [], False),
        (1, (1, 0), "face", False, False, "jaw", [], False),
        (8, (1, 0), "ears", False, True, "rightEar", ["brain"], False),
        (8, (0, 1), "ears", False, False, "rightEar", [], False),
        (8, (0, -1), "ears", False, False, "rightEar", [], False),
    ],
)
def test_head_impact_profile_uses_directional_helmet_zones_and_trajectories(
    roll,
    attacker_position,
    armor_zone,
    damages_head,
    head_on_penetration,
    primary,
    secondary,
    skull,
):
    attacker = SimpleNamespace(pos_x=attacker_position[0], pos_y=attacker_position[1])
    target = SimpleNamespace(pos_x=0, pos_y=0, facing_x=0, facing_y=1)

    profile = CombatService._head_impact_profile(roll, attacker, target)

    assert profile["armor_zone"] == armor_zone
    assert profile["damage_head"] is damages_head
    assert profile["damage_head_if_penetrated"] is head_on_penetration
    assert profile["primary_organ"] == primary
    assert profile["secondary_organs_if_penetrated"] == secondary
    assert profile["damage_skull_if_penetrated"] is skull


def test_head_subzone_only_uses_helmet_layer_that_covers_it():
    target_data = {
        "equipment": {
            "helmet": {
                "name": "Open helmet",
                "durability": 10,
                "protection": {"physical": 40},
                "attributes": {
                    "protection_zones": ["crown", "back"],
                },
            }
        }
    }

    crown_data = {**target_data, "_headProtectionZone": "crown"}
    face_data = {**target_data, "_headProtectionZone": "face"}

    assert CombatService._target_armor(crown_data, "head")[0] == 40
    assert CombatService._target_armor(face_data, "head")[0] == 0


def _head_trauma_target():
    return SimpleNamespace(
        character=SimpleNamespace(data={
            "health": {
                "current": 700,
                "max": 700,
                "zones": {"head": {"current": 50, "max": 50}},
                "organs": {
                    "rightEye": {"current": 15, "max": 15},
                    "brain": {"current": 1, "max": 1},
                    "skull": {"current": 50, "max": 50},
                },
                "effects": [],
            }
        }),
        hp_zones={},
        posture="standing",
        cover_object_id=None,
        weapon_braced=False,
        braced_weapon_index=None,
        drawn_weapon_index=None,
        aimed_target_character_id=None,
        aimed_weapon_index=None,
        aim_accuracy_bonus=0,
        location_id=None,
    )


def test_reinforced_frame_reduces_additional_trauma_chance(monkeypatch):
    from app.services.equipment_modifications import make_record, supported, definition
    assert supported(definition('armor-a24'))
    record=make_record('armor-a24')
    assert record['modifiers']=={'physical':5,'durability':5}
    target=_head_trauma_target()
    target.character.data['equipment']={'armor':{'name':'Броня',
        'modifications':[record]}}
    monkeypatch.setattr(combat_module,'flag_modified',lambda *args,**kwargs:None)
    rolls=iter([90,10])
    monkeypatch.setattr(combat_module.random,'randint',
        lambda low,high:next(rolls) if high==100 else high)
    health=CombatService._apply_attack_damage(target,20,'left_arm',{'bleeding':''},
        allow_bleeding=False,round_number=1)
    assert health['_attackOutcome']['additional_traumas']==[]


def test_side_eye_behind_armor_disables_eye_but_not_head_or_brain(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    target = _head_trauma_target()
    impact = CombatService._head_impact_profile(
        4,
        SimpleNamespace(pos_x=1, pos_y=0),
        SimpleNamespace(pos_x=0, pos_y=0, facing_x=0, facing_y=1),
    )

    health = CombatService._apply_attack_damage(
        target,
        20,
        "head",
        {"bleeding": ""},
        force_trauma=True,
        allow_bleeding=False,
        head_impact=impact,
        armor_penetrated=False,
        prepared_trauma_rolls=[4],
        round_number=1,
    )

    assert health["zones"]["head"]["current"] == 50
    assert health["organs"]["rightEye"]["current"] == 0
    assert health["organs"]["brain"]["current"] == 1
    assert health["organs"]["skull"]["current"] == 50
    assert not any(effect["type"] == "death" for effect in health["effects"])


def test_side_eye_penetration_reaches_head_and_brain(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    target = _head_trauma_target()
    impact = CombatService._head_impact_profile(
        4,
        SimpleNamespace(pos_x=1, pos_y=0),
        SimpleNamespace(pos_x=0, pos_y=0, facing_x=0, facing_y=1),
    )

    health = CombatService._apply_attack_damage(
        target,
        20,
        "head",
        {"bleeding": ""},
        force_trauma=True,
        allow_bleeding=False,
        head_impact=impact,
        armor_penetrated=True,
        prepared_trauma_rolls=[4],
        round_number=1,
    )

    assert health["zones"]["head"]["current"] == 30
    assert health["organs"]["rightEye"]["current"] == 0
    assert health["organs"]["brain"]["current"] == 0
    assert health["organs"]["skull"]["current"] == 50
    assert any(effect["type"] == "death" for effect in health["effects"])


def test_back_eye_penetration_uses_occiput_and_reaches_skull_and_brain(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    target = _head_trauma_target()
    impact = CombatService._head_impact_profile(
        4,
        SimpleNamespace(pos_x=0, pos_y=-1),
        SimpleNamespace(pos_x=0, pos_y=0, facing_x=0, facing_y=1),
    )

    health = CombatService._apply_attack_damage(
        target,
        20,
        "head",
        {"bleeding": ""},
        force_trauma=True,
        allow_bleeding=False,
        head_impact=impact,
        armor_penetrated=True,
        prepared_trauma_rolls=[4],
        round_number=1,
    )

    assert health["zones"]["head"]["current"] == 30
    assert health["organs"]["rightEye"]["current"] == 15
    assert health["organs"]["skull"]["current"] == 30
    assert health["organs"]["brain"]["current"] == 0


@pytest.mark.parametrize(
    ("roll", "attacker_position", "organ", "expected_head"),
    [
        (6, (1, 0), "nose", 50),
        (6, (0, 1), "nose", 30),
        (2, (1, 0), "jaw", 50),
        (1, (1, 0), "jaw", 50),
    ],
)
def test_face_trauma_routes_damage_without_inventing_head_damage(
    monkeypatch, roll, attacker_position, organ, expected_head,
):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    target = _head_trauma_target()
    impact = CombatService._head_impact_profile(
        roll,
        SimpleNamespace(pos_x=attacker_position[0], pos_y=attacker_position[1]),
        SimpleNamespace(pos_x=0, pos_y=0, facing_x=0, facing_y=1),
    )

    health = CombatService._apply_attack_damage(
        target,
        20,
        "head",
        {"bleeding": ""},
        force_trauma=True,
        allow_bleeding=False,
        head_impact=impact,
        armor_penetrated=True,
        prepared_trauma_rolls=[roll],
        round_number=1,
    )

    assert health["zones"]["head"]["current"] == expected_head
    assert health["organs"][organ]["current"] == 0


@pytest.mark.parametrize(
    ("penetrated", "expected_skull"),
    [(False, 50), (True, 30)],
)
def test_crown_hit_damages_head_but_skull_only_after_penetration(
    monkeypatch, penetrated, expected_skull,
):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    target = _head_trauma_target()
    impact = CombatService._head_impact_profile(3)

    health = CombatService._apply_attack_damage(
        target,
        20,
        "head",
        {"bleeding": ""},
        force_trauma=True,
        allow_bleeding=False,
        head_impact=impact,
        armor_penetrated=penetrated,
        prepared_trauma_rolls=[3],
        round_number=1,
    )

    assert health["zones"]["head"]["current"] == 30
    assert health["organs"]["skull"]["current"] == expected_skull


def test_limb_fall_trauma_knocks_character_prone(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    target = _head_trauma_target()

    health = CombatService._apply_attack_damage(
        target,
        20,
        "left_leg",
        {"bleeding": ""},
        force_trauma=True,
        allow_bleeding=False,
        prepared_trauma_rolls=[1],
        round_number=1,
    )

    trauma = health["_attackOutcome"]["additional_traumas"][0]
    assert target.posture == "prone"
    assert trauma["fall_or_drop"]["kind"] == "fall"


def test_arm_drop_trauma_removes_drawn_weapon_and_applies_fall_wear(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    target = _head_trauma_target()
    target.character.data["weapons"] = [{
        "name": "Test pistol",
        "durability": 100,
        "maxDurability": 100,
    }]
    target.character.data["activeWeaponIndex"] = 0
    target.drawn_weapon_index = 0

    health = CombatService._apply_attack_damage(
        target,
        20,
        "right_arm",
        {"bleeding": ""},
        force_trauma=True,
        allow_bleeding=False,
        prepared_trauma_rolls=[1],
        round_number=1,
    )

    trauma = health["_attackOutcome"]["additional_traumas"][0]
    assert target.character.data["weapons"] == []
    assert target.drawn_weapon_index is None
    assert "activeWeaponIndex" not in target.character.data
    assert trauma["fall_or_drop"]["weapon_wear"]["after"] == 97


def test_buckshot_loses_fixed_damage_and_penetration_after_five_meters(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        CombatService,
        "_ranged_damage_profile",
        lambda weapon: ({
            "damage": 250,
            "armor_piercing": 25,
            "bleeding": "",
            "caliber": "12x70",
            "ammo_name": "12х70 Картечь",
            "effective_range": 10,
        }, None),
    )
    monkeypatch.setattr(CombatService, "_target_armor", lambda data, zone: (0, []))

    def capture_damage(target, damage, zone, profile, **kwargs):
        captured.update({"damage": damage, "profile": profile, **kwargs})
        return {"current": 550, "zones": {"chest": {"current": 0}}}

    monkeypatch.setattr(CombatService, "_apply_attack_damage", capture_damage)
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": [{}]}))
    target = SimpleNamespace(character=SimpleNamespace(data={}), hp_zones={})

    CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": 0,
            "hit_difficulty": 1,
            "fire_mode": "rapid",
            "round_number": 1,
            "target_distance": 7,
        },
        aimed_zone="chest",
        forced_roll=20,
    )

    assert captured["damage"] == 150
    assert captured["profile"]["armor_piercing"] == 15
    assert captured["trauma_checks"] == 3
    assert captured["trauma_difficulty_modifier"] == -20


def test_buckshot_mutant_rule_overrides_partial_damage_at_ten_percent_deficit(
    monkeypatch,
):
    monkeypatch.setattr(
        CombatService,
        "_ranged_damage_profile",
        lambda weapon: ({
            "damage": 250,
            "armor_piercing": 5,
            "bleeding": "",
            "caliber": "12x70",
            "ammo_name": "12х70 Картечь",
            "effective_range": 10,
        }, None),
    )
    monkeypatch.setattr(CombatService, "_target_armor", lambda data, zone: (20, []))
    monkeypatch.setattr(
        CombatService,
        "_apply_attack_damage",
        lambda *args, **kwargs: {
            "current": 575,
            "zones": {"chest": {"current": 25}},
        },
    )
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": [{}]}))
    target = SimpleNamespace(
        character=SimpleNamespace(data={"basic": {"species": "Мутант"}}),
        hp_zones={},
    )

    result = CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": 0,
            "hit_difficulty": 1,
            "fire_mode": "single",
            "round_number": 1,
            "target_distance": 5,
        },
        aimed_zone="chest",
        forced_roll=20,
    )

    assert result["full_non_penetration"] is False
    assert result["buckshot_mutant_non_penetration"] is True
    assert result["damage_multiplier"] == 0.50
    assert result["damage"] == 125


def test_armor_is_reduced_by_ammo_penetration_before_damage():
    effective_armor = max(0, 60 - 20)
    damage_reduction_steps = (effective_armor + 4) // 5
    damage = round(100 * max(0, 1 - damage_reduction_steps * 0.25))

    assert effective_armor == 40
    assert damage == 0


def test_armor_damage_advances_stage_and_carries_over_damage():
    armor = {
        "name": "Test armor",
        "durability": 10,
        "material": "композит",
        "stage": 1,
        "stageDurability": 100,
        "currentStageDurability": 100,
    }

    result = CombatService._damage_armor_item(armor, {}, 150)

    assert result["stage_before"] == 1
    assert result["stage_after"] == 2
    assert armor["currentStageDurability"] == 50


def test_broken_armor_loses_durability_and_protection_per_50_damage():
    armor = {
        "durability": 10,
        "material": "композит",
        "stage": 5,
        "currentStageDurability": 0,
    }

    CombatService._damage_armor_item(armor, {}, 49)

    assert armor["durability"] == 10
    assert armor.get("brokenProtectionLoss", 0) == 0

    CombatService._damage_armor_item(armor, {}, 1)

    assert armor["durability"] == 9
    assert armor["brokenProtectionLoss"] == 1

    CombatService._damage_armor_item(armor, {}, 50)

    assert armor["durability"] == 8
    assert armor["brokenProtectionLoss"] == 2


def test_broken_armor_protection_loss_applies_to_every_damage_type():
    armor = {
        "name": "Test armor",
        "category": "armor",
        "durability": 8,
        "stage": 5,
        "brokenProtectionLoss": 2,
        "protection": {
            "physical": 0.6,
            "chemical": 0.6,
            "thermal": 0.6,
            "electric": 0.6,
            "radiation": 0.6,
        },
    }
    data = {"equipment": {"armor": armor}}

    physical, _ = CombatService._target_armor(data, "chest")

    assert physical == 13
    for damage_type in ("chemical", "thermal", "electric", "radiation"):
        assert CombatService._target_elemental_protection(data, damage_type) == 23


def test_armor_protection_never_becomes_negative():
    armor = {
        "name": "Ruined armor",
        "category": "armor",
        "durability": 0,
        "stage": 5,
        "brokenProtectionLoss": 100,
        "protection": {
            "physical": 0.1,
            "chemical": 0.1,
            "thermal": 0.1,
            "electric": 0.1,
            "radiation": 0.1,
        },
    }
    data = {"equipment": {"armor": armor}}

    physical, _ = CombatService._target_armor(data, "chest")

    assert physical == 0
    for damage_type in ("chemical", "thermal", "electric", "radiation"):
        assert CombatService._target_elemental_protection(data, damage_type) == 0


def test_gas_mask_has_no_stages_and_loses_fixed_durability():
    gas_mask = {
        "name": "Противогаз",
        "durability": 25,
        "stage": 3,
        "currentStageDurability": 4,
    }

    bullet = CombatService._damage_gas_mask(gas_mask, "bullet")
    anomaly = CombatService._damage_gas_mask(gas_mask, "anomaly")

    assert bullet["durability_after"] == 15
    assert anomaly["durability_after"] == 14
    assert gas_mask["durability"] == 14
    assert "stage" not in gas_mask
    assert "currentStageDurability" not in gas_mask


def test_broken_gas_mask_loses_all_protection():
    data = {
        "equipment": {
            "gasMask": {
                "name": "Противогаз",
                "category": "gas_mask",
                "durability": 0,
                "protection": {"physical": 0.75},
            },
        },
    }

    protection, layers = CombatService._target_armor(data, "head")

    assert protection == 0
    assert layers == []
    assert CombatService._functioning_gas_protection(data) is None


def test_functioning_gas_mask_fully_blocks_gas_projectile(monkeypatch):
    monkeypatch.setattr(
        CombatService,
        "_ranged_damage_profile",
        lambda weapon: ({
            "damage": 80,
            "armor_piercing": 100,
            "bleeding": "",
            "effective_range": 20,
            "damage_type": "chemical",
        }, None),
    )
    target_data = {
        "health": {
            "current": 700,
            "zones": {"head": {"current": 50, "max": 50}},
        },
        "equipment": {
            "gasMask": {
                "name": "Противогаз",
                "category": "gas_mask",
                "durability": 30,
                "protection": {"physical": 0.1},
            },
        },
    }
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": [{}]}))
    target = SimpleNamespace(character=SimpleNamespace(data=target_data), hp_zones={})

    result = CombatService._resolve_attack(
        target,
        attacker,
        {
            "weapon_index": 0,
            "hit_difficulty": 1,
            "fire_mode": "single",
            "round_number": 1,
        },
        aimed_zone="head",
        forced_roll=20,
    )

    assert result["gas_or_chemical_blocked"] is True
    assert result["damage"] == 0
    assert target_data["health"]["current"] == 700
    assert target_data["equipment"]["gasMask"]["durability"] == 30


def test_firearm_bleeding_uses_caliber_and_ammo_variant_modifiers():
    profile = {
        "caliber": "7.62x39",
        "ammo_variant": "ep",
        "armor_piercing": 50,
    }

    result = CombatService._roll_firearm_bleeding(profile, armor=20, forced_roll=3)

    assert result == {
        "roll": 3,
        "modifier": 4,
        "total": 7,
        "stage": "severe",
        "blocked_by_armor": False,
    }


def test_firearm_bleeding_is_blocked_when_armor_is_penetrated_by_less_than_ten():
    profile = {
        "caliber": "7.62x39",
        "ammo_variant": None,
        "armor_piercing": 35,
    }

    result = CombatService._roll_firearm_bleeding(profile, armor=30, forced_roll=6)

    assert result["stage"] is None
    assert result["roll"] is None
    assert result["blocked_by_armor"] is True


def test_blocked_penetration_prevents_profile_and_trauma_bleeding(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: end)
    character = SimpleNamespace(data={
        "health": {
            "current": 700,
            "max": 700,
            "zones": {"chest": {"current": 150, "max": 150}},
            "effects": [],
        },
    })
    target = SimpleNamespace(character=character, hp_zones={})

    CombatService._apply_attack_damage(
        target,
        20,
        "chest",
        {"bleeding": "Сильное"},
        bleeding_result={"stage": "extreme"},
        allow_bleeding=False,
        round_number=1,
    )

    effects = character.data["health"]["effects"]
    assert not any("bleeding" in str(effect.get("type")) for effect in effects)


def test_damage_pain_uses_round_total_and_large_single_hit_thresholds():
    assert CombatService._damage_pain_requirement(49, 49) == 0
    assert CombatService._damage_pain_requirement(50, 50) == 1
    assert CombatService._damage_pain_requirement(149, 100) == 1
    assert CombatService._damage_pain_requirement(150, 100) == 2
    assert CombatService._damage_pain_requirement(151, 151) == 4
    assert CombatService._damage_pain_requirement(201, 201) == 5


@pytest.mark.parametrize('damage, expected', [
    (0, 0), (49, 0), (50, 1), (149, 1), (150, 4), (151, 4),
    (200, 4), (201, 5), (249, 5), (250, 6), (300, 6), (301, 8), (350, 9),
])
def test_damage_pain_adds_round_total_and_only_the_highest_single_wound_tier(damage, expected):
    assert CombatService._damage_pain_requirement(damage, damage) == expected


@pytest.mark.parametrize('hits, expected', [
    ([25, 25], 1), ([50, 50, 50], 2), ([150], 4), ([150, 150], 7), ([151, 151], 7),
    ([250, 10], 6), ([10, 250], 6), ([350], 9),
])
def test_damage_pain_counts_each_wound_without_repaying_round_total(hits, expected):
    data = {'health': {'effects': []}}
    for damage in hits:
        CombatService._apply_damage_pain(data, damage, 1, 'combat_damage')
    assert data['health']['painLevel'] == expected
    assert data['health']['combatMeta']['damageTakenThisRound'] == sum(hits)
    assert data['health']['combatMeta']['damagePainAppliedThisRound'] == expected


def test_damage_pain_starts_new_round_without_removing_existing_pain():
    data = {'health': {'effects': []}}
    CombatService._apply_damage_pain(data, 50, 1, 'combat_damage')
    CombatService._apply_damage_pain(data, 50, 2, 'explosion')
    assert data['health']['painLevel'] == 2
    assert data['health']['combatMeta']['damageTakenThisRound'] == 50
    assert data['health']['combatMeta']['damagePainAppliedThisRound'] == 1


@pytest.mark.parametrize('trait, damage, expected', [
    ('Неуязвим к боли', 350, 0),
    ('Урон, необходимый для получения уровня боли 2х', 500, 6),
])
def test_damage_pain_preserves_mutant_immunity_and_thresholds(trait, damage, expected):
    data = {'mutant': {'traits': [trait]}, 'health': {'painLevel': 0, 'effects': []}}
    CombatService._apply_damage_pain(data, damage, 1, 'explosion')
    assert data['health']['painLevel'] == expected


def test_attack_and_general_damage_share_round_pain_accumulator(monkeypatch):
    monkeypatch.setattr(combat_module, 'flag_modified', lambda *args, **kwargs: None)
    monkeypatch.setattr(CombatService, 'apply_stress_trigger', lambda *args, **kwargs: None)
    target = SimpleNamespace(character=SimpleNamespace(data={
        'health': {'current': 700, 'effects': [], 'zones': {}},
    }), hp_zones={})
    CombatService._apply_attack_damage(
        target, 25, 'chest', {}, round_number=1, allow_bleeding=False, trauma_checks=0,
    )
    CombatService._apply_general_damage(target, 25, round_number=1)
    health = target.character.data['health']
    assert health['combatMeta']['damageTakenThisRound'] == 50
    assert health['combatMeta']['damagePainAppliedThisRound'] == 1
    assert health['painLevel'] == 1


def test_limb_damage_at_three_and_five_limits_creates_catastrophic_injuries(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: start)
    character = SimpleNamespace(data={
        "health": {
            "current": 700,
            "max": 700,
            "zones": {"leftArm": {"current": 100, "max": 100}},
            "effects": [],
        },
    })
    target = SimpleNamespace(character=character, hp_zones={})

    CombatService._apply_attack_damage(
        target, 300, "left_arm", {}, allow_bleeding=False, round_number=1
    )
    effects = character.data["health"]["effects"]
    assert any(effect["type"] == "mangled_limb" for effect in effects)
    assert any(effect["type"] == "shock" for effect in effects)

    CombatService._apply_attack_damage(
        target, 200, "left_arm", {}, allow_bleeding=False, round_number=1
    )
    effects = character.data["health"]["effects"]
    amputation = next(effect for effect in effects if effect["type"] == "amputation")
    assert not any(effect["type"] == "mangled_limb" for effect in effects)
    assert amputation["loss_roll"] == 1
    assert amputation["loss_extent"] == "entire_limb"
    assert character.data["health"]["zones"]["leftArm"]["destructionDamage"] == 500


def test_restored_limb_does_not_keep_catastrophic_overkill(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    character = SimpleNamespace(data={
        "health": {
            "current": 700,
            "max": 700,
            "zones": {
                "leftArm": {
                    "current": 100,
                    "max": 100,
                    "destructionDamage": 300,
                },
            },
            "effects": [],
        },
    })
    target = SimpleNamespace(character=character, hp_zones={})

    CombatService._apply_attack_damage(
        target, 100, "left_arm", {}, allow_bleeding=False, round_number=1,
    )

    health = character.data["health"]
    assert health["zones"]["leftArm"]["destructionDamage"] == 100
    assert not any(effect["type"] == "mangled_limb" for effect in health["effects"])


def test_positive_health_limb_cannot_receive_catastrophic_injury(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    character = SimpleNamespace(data={
        "health": {
            "current": 700,
            "max": 700,
            "zones": {
                "leftLeg": {
                    "current": 50,
                    "max": 100,
                    "destructionDamage": 500,
                },
            },
            "effects": [],
        },
    })
    target = SimpleNamespace(character=character, hp_zones={})

    CombatService._apply_attack_damage(
        target, 10, "left_leg", {}, allow_bleeding=False, round_number=1,
    )

    health = character.data["health"]
    assert health["zones"]["leftLeg"]["current"] == 40
    assert not any(
        effect["type"] in {"mangled_limb", "amputation"}
        for effect in health["effects"]
    )


def test_mangled_and_missing_legs_have_increased_penalties():
    mangled = {
        "health": {
            "zones": {"leftLeg": {"current": 0, "max": 100}},
            "effects": [{"type": "mangled_limb", "area": "leftLeg"}],
        },
    }
    missing = {
        "health": {
            "zones": {"leftLeg": {"current": 0, "max": 100}},
            "effects": [{"type": "amputation", "area": "leftLeg"}],
        },
    }

    assert CombatService._disabled_limb_penalties(mangled)["movement"] == 5
    assert CombatService._disabled_limb_penalties(missing)["movement"] == 6


def test_disabled_limbs_apply_combat_and_movement_penalties():
    data = {
        "health": {
            "zones": {
                "leftArm": {"current": 0, "max": 90},
                "rightArm": {"current": 90, "max": 90},
                "leftLeg": {"current": 0, "max": 100},
                "rightLeg": {"current": 100, "max": 100},
                "abdomen": {"current": 0, "max": 120},
            }
        }
    }

    penalties = CombatService._disabled_limb_penalties(data)

    assert penalties["all"] == 3
    assert penalties["shooting"] == 3
    assert penalties["melee"] == 3
    assert penalties["agility"] == 3
    assert penalties["movement"] == 3
    assert penalties["sprint_blocked"] is True


def test_fixed_fracture_penalties_are_lower_for_arms_and_legs():
    arm_data = {
        "health": {
            "effects": [
                {"type": "fracture_fixed", "area": "leftArm", "active": True},
            ],
        },
    }
    leg_data = {
        "health": {
            "effects": [
                {"type": "fracture_fixed", "area": "rightLeg", "active": True},
            ],
        },
    }

    assert CombatService._skill_modifier(arm_data, "skills.physical.shooting") == -1
    assert CombatService._movement_penalty_breakdown(leg_data)["injuries"] == 2


def test_systemic_limb_treatment_suppresses_regular_and_fixed_fracture_penalties():
    data = {
        "health": {
            "effects": [
                {"type": "fracture", "area": "leftArm", "active": True},
                {"type": "fracture_fixed", "area": "rightLeg", "active": True},
                {
                    "type": "temporary_limb_restoration",
                    "area": "leftArm",
                    "suppress_fracture": True,
                    "remaining": 10,
                    "active": True,
                },
                {
                    "type": "temporary_limb_restoration",
                    "area": "rightLeg",
                    "suppress_fracture": True,
                    "remaining": 10,
                    "active": True,
                },
            ],
        },
    }

    assert CombatService._skill_modifier(data, "skills.physical.shooting") == 0
    assert CombatService._movement_penalty_breakdown(data)["injuries"] == 0


def test_aimed_head_shot_adds_five_difficulty():
    assert CombatService._aimed_zone_difficulty_penalty("head") == 5
    assert CombatService._aimed_zone_difficulty_penalty("chest") == 0


def test_only_pistol_subcategory_gets_cheap_unaimed_shot():
    assert CombatService._is_pistol_weapon({"subcategory": "пистолеты"}) is True
    assert CombatService._is_pistol_weapon({"subcategory": "пистолеты-пулеметы"}) is False


@pytest.mark.parametrize("subcategory", [
    "Пистолеты", "Дробовики", "Пистолеты-пулеметы",
])
def test_close_range_bonus_applies_to_compact_weapon_classes(subcategory):
    weapon = {"subcategory": subcategory}
    assert CombatService._close_range_weapon_accuracy_bonus(weapon, 5) == 2
    assert CombatService._close_range_weapon_accuracy_bonus(weapon, 6) == 0


def test_sniper_close_range_penalty_grows_for_each_meter_and_short_barrel_ignores_it():
    weapon = {"subcategory": "Снайперские винтовки"}
    assert CombatService._sniper_close_range_penalty(weapon, 5) == 1
    assert CombatService._sniper_close_range_penalty(weapon, 3) == 3
    assert CombatService._sniper_close_range_penalty(weapon, 1) == 5
    shortened = {
        **weapon,
        "modifications": [{"name": "Укорочение ствола"}],
    }
    assert CombatService._sniper_close_range_penalty(shortened, 1) == 0


def test_submachine_gun_burst_costs_one_less_action_point():
    assert CombatService._burst_action_points(
        {"subcategory": "Пистолеты-пулеметы"}
    ) == 2
    assert CombatService._burst_action_points(
        {"subcategory": "Штурмовые винтовки и карабины"}
    ) == 3


@pytest.mark.parametrize(("subcategory", "penalty"), [
    ("Пистолеты-пулеметы", 2),
    ("Штурмовые винтовки и карабины", 4),
    ("Дробовики", 6),
    ("Пулеметы", 0),
])
def test_burst_followup_penalty_depends_on_weapon_class(subcategory, penalty):
    weapon = {"subcategory": subcategory}
    assert CombatService._burst_followup_accuracy_penalty(weapon, 0) == 0
    assert CombatService._burst_followup_accuracy_penalty(weapon, 1) == penalty


def test_rapid_fire_adds_four_difficulty_except_for_12x70_buckshot(app):
    regular_weapon = {
        "installedMagazine": {
            "ammo": [{
                "name": "9x19 обычный",
                "quantity": 1,
                "attributes": {"caliber": "9x19", "damage": 30},
            }],
        },
    }
    buckshot_weapon = {
        "fixedAmmo": [{
            "name": "12х70 Картечь",
            "quantity": 1,
            "attributes": {
                "caliber": "12x70", "ammo_variant": "buckshot", "damage": 50,
            },
        }],
    }

    with app.app_context():
        assert CombatService._rapid_fire_accuracy_penalty(regular_weapon) == 4
        assert CombatService._rapid_fire_accuracy_penalty(buckshot_weapon) == 0


@pytest.mark.parametrize(("subcategory", "penalty"), [
    ("Штурмовые винтовки и карабины", 4),
    ("Пистолеты-пулеметы", 2),
    ("Дробовики", 6),
    ("Пулемёты", -4),
])
def test_area_fire_penalty_depends_only_on_weapon_class(subcategory, penalty):
    assert CombatService._area_fire_accuracy_penalty(
        {"subcategory": subcategory},
    ) == penalty


def test_area_fire_spends_two_bursts_or_ten_machine_gun_rounds():
    assert CombatService._area_fire_shot_count({"burst_size": 4}) == 8
    assert CombatService._area_fire_shot_count({
        "machine_gun_burst": True,
    }) == 10


def test_area_fire_gains_one_hit_for_each_three_above_difficulty():
    assert CombatService._area_fire_hit_count(12, 12, 8) == 1
    assert CombatService._area_fire_hit_count(15, 12, 8) == 2
    assert CombatService._area_fire_hit_count(18, 12, 8) == 3
    assert CombatService._area_fire_hit_count(30, 1, 2) == 2


def test_fixed_magazine_uses_the_next_loaded_ammo_stack():
    weapon = {
        "fixedAmmo": [
            {
                "name": "standard",
                "quantity": 2,
                "attributes": {
                    "damage": 50,
                    "penetration": 0.2,
                    "caliber": "12x70",
                },
            },
            {
                "name": "RIP",
                "quantity": 1,
                "ammo_variant": "rip",
                "attributes": {
                    "damage": 150,
                    "penetration": 0.1,
                    "caliber": "12x70",
                    "ammo_variant": "rip",
                },
            },
        ]
    }

    profile, stack = CombatService._ranged_damage_profile(weapon)

    assert stack["name"] == "RIP"
    assert profile["damage"] == 150
    assert profile["armor_piercing"] == 10
    assert profile["ammo_variant"] == "rip"


def test_skill_rolls_use_blood_loss_stage_not_bleeding_severity():
    data = {
        "health": {
            "painLevel": 0,
            "exhaustion": 0,
            "bloodStage": "medium",
            "bleedingSeverity": 8,
            "temperature": 36.6,
            "zones": {},
            "effects": [],
        }
    }

    modifier = CombatService._health_roll_modifier(
        data,
        "skills.physical.shooting",
    )

    assert modifier == -3


def test_damage_adds_stress_once_per_round_and_pain_from_round_total(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: 1)
    character = SimpleNamespace(data={
        "health": {
            "current": 700,
            "max": 700,
            "painLevel": 0,
            "stress": 0,
            "zones": {"chest": {"current": 150, "max": 150}},
            "effects": [],
        }
    })
    target = SimpleNamespace(character=character, hp_zones={})
    profile = {"bleeding": ""}

    CombatService._apply_attack_damage(
        target, 25, "chest", profile, round_number=3
    )
    CombatService._apply_attack_damage(
        target, 25, "chest", profile, round_number=3
    )

    health = character.data["health"]
    assert health["stress"] == 1
    assert health["painLevel"] == 1

    CombatService._apply_attack_damage(
        target, 25, "chest", profile, round_number=4
    )

    assert health["stress"] == 2
    assert health["painLevel"] == 1


def test_disabling_arm_adds_zone_pain_only_once(monkeypatch):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: 1)
    character = SimpleNamespace(data={
        "health": {
            "current": 700,
            "max": 700,
            "painLevel": 0,
            "stress": 0,
            "zones": {"leftArm": {"current": 10, "max": 90}},
            "effects": [],
        }
    })
    target = SimpleNamespace(character=character, hp_zones={})

    CombatService._apply_attack_damage(
        target, 10, "left_arm", {"bleeding": ""}, round_number=1
    )
    CombatService._apply_attack_damage(
        target, 10, "left_arm", {"bleeding": ""}, round_number=1
    )

    assert character.data["health"]["painLevel"] == 4


@pytest.mark.parametrize("zone", ["head", "chest"])
def test_hitting_disabled_vital_zone_kills_character(monkeypatch, zone):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: 1)
    zone_max = 50 if zone == "head" else 150
    character = SimpleNamespace(data={
        "health": {
            "current": 500,
            "max": 700,
            "zones": {zone: {"current": 0, "max": zone_max}},
            "effects": [],
        }
    })
    target = SimpleNamespace(
        character=character,
        hp_zones={},
        posture="standing",
        cover_object_id=None,
        weapon_braced=False,
        braced_weapon_index=None,
    )

    health = CombatService._apply_attack_damage(
        target, 0, zone, {"bleeding": ""}, round_number=1
    )

    death = next(effect for effect in health["effects"] if effect["type"] == "death")
    assert death["source"] == "hit_disabled_vital_zone"
    assert death["area"] == zone
    assert health["_attackOutcome"]["death"] is True
    assert target.posture == "prone"


@pytest.mark.parametrize("zone", ["head", "chest"])
def test_disabling_vital_zone_makes_character_critical_and_prone(monkeypatch, zone):
    monkeypatch.setattr(combat_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(combat_module.random, "randint", lambda start, end: 1)
    zone_max = 50 if zone == "head" else 150
    character = SimpleNamespace(data={
        "health": {
            "current": 500,
            "max": 700,
            "zones": {zone: {"current": 1, "max": zone_max}},
            "effects": [],
        }
    })
    target = SimpleNamespace(
        character=character,
        hp_zones={},
        posture="standing",
        cover_object_id=42,
        weapon_braced=True,
        braced_weapon_index=0,
    )

    health = CombatService._apply_attack_damage(
        target, 1, zone, {"bleeding": ""}, round_number=1
    )

    assert CombatService._character_condition(character.data)["state"] == "critical"
    assert not any(effect["type"] == "death" for effect in health["effects"])
    assert target.posture == "prone"
    assert target.cover_object_id is None
    assert target.weapon_braced is False


def test_disabled_chest_is_critical_and_cannot_recover_from_shock():
    condition = CombatService._character_condition({
        "health": {
            "current": 500,
            "zones": {"chest": {"current": 0, "max": 150}},
            "effects": [{"type": "shock", "active": True}],
        }
    })

    assert condition["state"] == "critical"
    assert condition["can_recover"] is False


def test_disabled_head_is_critical_but_not_dead_without_brain_or_skull_damage():
    condition = CombatService._character_condition({
        "health": {
            "current": 500,
            "zones": {"head": {"current": 0, "max": 50}},
            "organs": {
                "brain": {"current": 1, "max": 1},
                "skull": {"current": 1, "max": 1},
            },
            "effects": [],
        }
    })

    assert condition["state"] == "critical"
    assert condition["can_recover"] is False


def test_destroyed_lung_gets_own_health_bleeding_and_pain():
    health = {"effects": [], "painLevel": 0}

    result = CombatService._apply_organ_damage(
        health, "rightLung", 50, "chest"
    )

    assert result["disabled"] is True
    assert health["organs"]["rightLung"]["current"] == 0
    assert health["painLevel"] == 5
    assert any(
        effect["type"] == "bleeding_internal_severe"
        and effect["area"] == "chest"
        for effect in health["effects"]
    )


def test_destroyed_heart_starts_one_minute_death_timer():
    health = {"effects": [], "painLevel": 0}

    result = CombatService._apply_organ_damage(health, "heart", 20, "chest")

    failure = next(
        effect for effect in health["effects"]
        if effect["type"] == "organ_failure"
    )
    assert result["death_in_seconds"] == 60
    assert failure["remaining_seconds"] == 60
    assert failure["death_on_expire"] is True


@pytest.mark.parametrize(
    ("durability", "chance", "die", "bonus"),
    [
        (100, 0, 0, 0),
        (90, 1, 4, 0),
        (75, 2, 6, 0),
        (60, 4, 8, 0),
        (45, 7, 10, 0),
        (30, 10, 12, 0),
        (10, 15, 12, 2),
        (0, 20, 12, 6),
    ],
)
def test_weapon_jam_thresholds_follow_equipment_rules(
    durability, chance, die, bonus
):
    assert CombatService._weapon_jam_profile(durability) == (chance, die, bonus)


def test_weapon_jam_is_saved_and_applies_its_durability_loss(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 4)
    weapon = {"durability": 90, "maxDurability": 100}

    result = CombatService._roll_weapon_jam(weapon, 1)

    assert result["triggered"] is True
    assert result["attack_roll"] == 1
    assert result["result"] == 4
    assert result["id"]
    assert result["durability_loss_applied"] == 3
    assert weapon["jam"]["accuracy_penalty"] == 2
    assert weapon["durability"] == 87


def test_non_blocking_weapon_jam_does_not_prevent_another_jam(monkeypatch):
    rolls = iter([4, 2])
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: next(rolls))
    weapon = {"durability": 90, "maxDurability": 100}

    first = CombatService._roll_weapon_jam(weapon, 1)
    second = CombatService._roll_weapon_jam(weapon, 1)

    assert first["result"] == 4
    assert not first.get("blocks_fire", False)
    assert second["result"] == 2
    assert second["blocks_fire"] is True
    assert [jam["result"] for jam in weapon["jams"]] == [4, 2]
    assert weapon["jam"]["result"] == 2


def test_weapon_jam_uses_attack_roll_without_separate_d20(monkeypatch):
    random_calls = []

    def roll_strength(start, end):
        random_calls.append((start, end))
        return 4

    monkeypatch.setattr(combat_module.random, "randint", roll_strength)
    weapon = {"durability": 75, "maxDurability": 100}

    no_jam = CombatService._roll_weapon_jam(weapon, 3)
    jam = CombatService._roll_weapon_jam(weapon, 2)

    assert no_jam["triggered"] is False
    assert jam["triggered"] is True
    assert jam["attack_roll"] == 2
    assert random_calls == [(1, 6)]


def test_must_do_retry_replaces_only_jam_caused_by_original_roll():
    old_jam = {"id": "old", "result": 4, "accuracy_penalty": 2}
    replaced_jam = {
        "id": "replaced", "result": 2, "blocks_fire": True,
        "durability_loss_applied": 3,
    }
    weapon = {
        "durability": 87,
        "maxDurability": 100,
        "jams": [old_jam, replaced_jam],
        "jam": replaced_jam,
    }

    result = CombatService._replace_must_do_weapon_jams(
        weapon,
        [{"id": "replaced", "durability_loss_applied": 3}],
        20,
    )

    assert result["triggered"] is False
    assert weapon["durability"] == 90
    assert weapon["jams"] == [old_jam]
    assert weapon["jam"] == old_jam


def test_blocking_weapon_jam_stops_remaining_burst(monkeypatch):
    attack_rolls = iter([20, 1, 20, 20])
    monkeypatch.setattr(
        CombatService,
        "_resolve_attack",
        lambda *args, **kwargs: {"roll": next(attack_rolls), "hit": False},
    )
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 1)
    weapon = {"durability": 75, "maxDurability": 100}
    attacker = SimpleNamespace(
        character=SimpleNamespace(data={"weapons": [weapon]}),
    )
    target = SimpleNamespace()
    attack_details = {
        "weapon_index": 0,
        "shot_count": 4,
        "requested_shot_count": 4,
        "hit_difficulty": 10,
        "hit_difficulty_without_weapon_jam": 10,
        "base_shooting_disadvantage": False,
    }

    results = CombatService._resolve_shot_sequence(
        [target], attacker, attack_details,
    )

    assert len(results) == 2
    assert attack_details["shot_count"] == 2
    assert attack_details["stopped_by_jam"] is True
    assert attack_details["weapon_jams"][0]["shot_number"] == 2
    assert attack_details["weapon_jams"][0]["attack_roll"] == 1


def test_non_blocking_jam_penalty_applies_to_next_attack_roll(monkeypatch):
    observed_difficulties = []
    attack_rolls = iter([1, 20])

    def resolve_attack(_target, _attacker, details, **_kwargs):
        observed_difficulties.append(details["hit_difficulty"])
        return {"roll": next(attack_rolls), "hit": False}

    monkeypatch.setattr(CombatService, "_resolve_attack", resolve_attack)
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 4)
    weapon = {"durability": 75, "maxDurability": 100}
    attacker = SimpleNamespace(
        character=SimpleNamespace(data={"weapons": [weapon]}),
    )
    attack_details = {
        "weapon_index": 0,
        "shot_count": 2,
        "requested_shot_count": 2,
        "hit_difficulty": 10,
        "hit_difficulty_without_weapon_jam": 10,
        "base_shooting_disadvantage": False,
    }

    CombatService._resolve_shot_sequence(
        [SimpleNamespace()], attacker, attack_details,
    )

    assert observed_difficulties == [10, 12]
    assert attack_details["shot_count"] == 2


def test_machine_gun_burst_difficulty_increases_every_two_shots(monkeypatch):
    observed_difficulties = []

    def resolve_attack(_target, _attacker, details, **_kwargs):
        observed_difficulties.append(details["hit_difficulty"])
        return {"roll": 20, "hit": True}

    monkeypatch.setattr(CombatService, "_resolve_attack", resolve_attack)
    weapon = {"durability": 100, "maxDurability": 100}
    attacker = SimpleNamespace(
        character=SimpleNamespace(data={"weapons": [weapon]}),
    )
    attack_details = {
        "weapon_index": 0,
        "shot_count": 6,
        "requested_shot_count": 6,
        "hit_difficulty": 10,
        "hit_difficulty_without_weapon_jam": 10,
        "base_shooting_disadvantage": False,
        "machine_gun_burst": True,
    }

    CombatService._resolve_shot_sequence(
        [SimpleNamespace()], attacker, attack_details,
    )

    assert observed_difficulties == [10, 10, 11, 11, 12, 12]


@pytest.mark.parametrize('machine_gun,shots,expected',[
    (False,3,[10,12,12]),
    (True,8,[10,10,10,10,10,10,11,11]),
])
def test_iron_grip_reduces_real_burst_penalty(monkeypatch,machine_gun,shots,expected):
    from app.services.equipment_modifications import make_record
    seen=[]
    def resolve_attack(_target,_attacker,details,**_kwargs):
        seen.append(details['hit_difficulty'])
        return {'roll':20,'hit':True}
    monkeypatch.setattr(CombatService,'_resolve_attack',resolve_attack)
    weapon={'durability':100,'maxDurability':100,
        'subcategory':'Пулеметы' if machine_gun else 'Штурмовые винтовки и карабины'}
    data={'weapons':[weapon],
        'equipment':{'armor':{'name':'Экзоскелет','modifications':[make_record('armor-d30')]}}}
    attacker=SimpleNamespace(character=SimpleNamespace(data=data))
    details={'weapon_index':0,'shot_count':shots,'requested_shot_count':shots,
        'hit_difficulty':10,'hit_difficulty_without_weapon_jam':10,
        'base_shooting_disadvantage':False,'machine_gun_burst':machine_gun,
        'fire_mode':'burst'}

    CombatService._resolve_shot_sequence([SimpleNamespace()],attacker,details)

    assert seen==expected


@pytest.mark.parametrize(("subcategory", "expected"), [
    ("Пистолеты-пулеметы", [10, 12, 12]),
    ("Штурмовые винтовки и карабины", [10, 14, 14]),
    ("Дробовики", [10, 16, 16]),
])
def test_regular_burst_applies_class_penalty_after_first_shot(
    monkeypatch, subcategory, expected,
):
    observed_difficulties = []

    def resolve_attack(_target, _attacker, details, **_kwargs):
        observed_difficulties.append(details["hit_difficulty"])
        return {"roll": 20, "hit": True}

    monkeypatch.setattr(CombatService, "_resolve_attack", resolve_attack)
    weapon = {
        "durability": 100,
        "maxDurability": 100,
        "subcategory": subcategory,
    }
    attacker = SimpleNamespace(
        character=SimpleNamespace(data={"weapons": [weapon]}),
    )
    attack_details = {
        "weapon_index": 0,
        "fire_mode": "burst",
        "shot_count": 3,
        "requested_shot_count": 3,
        "hit_difficulty": 10,
        "hit_difficulty_without_weapon_jam": 10,
        "base_shooting_disadvantage": False,
        "machine_gun_burst": False,
    }

    CombatService._resolve_shot_sequence(
        [SimpleNamespace()], attacker, attack_details,
    )

    assert observed_difficulties == expected


def test_weapon_wear_is_once_per_combat_plus_each_duplet():
    weapon = {
        "durability": 100,
        "maxDurability": 100,
        "subcategory": "Пистолеты",
    }

    first = CombatService._weapon_use_wear(weapon, fire_mode="unaimed")
    second = CombatService._weapon_use_wear(weapon, fire_mode="unaimed")
    duplet = CombatService._weapon_use_wear(
        weapon, fire_mode="unaimed", shot_count=2
    )

    assert first["loss"] == 2
    assert second["loss"] == 0
    assert duplet["loss"] == 2
    assert weapon["durability"] == 96


def test_nonstandard_ammunition_doubles_weapon_wear_again():
    weapon = {"durability": 100, "maxDurability": 100}

    wear = CombatService._weapon_use_wear(
        weapon,
        fire_mode="burst",
        volley_count=1,
        ammo_profile={"ammo_variant": "bp"},
    )

    assert wear["loss"] == 4
    assert weapon["durability"] == 96


def test_weapon_ammo_is_consumed_from_next_stack_on_server():
    weapon = {
        "installedMagazine": {
            "ammo": [
                {"name": "9x19", "quantity": 2},
                {"name": "9x19 БП", "quantity": 3},
            ]
        }
    }

    CombatService._consume_weapon_ammo(weapon, 4)

    assert weapon["installedMagazine"]["ammo"] == [
        {"name": "9x19", "quantity": 1}
    ]
    assert weapon["ammo"] == 1


def test_repair_clears_catastrophic_jam_only_at_required_durability():
    weapon = {
        "durability": 99,
        "maxDurability": 100,
        "jam": {"result": 12, "repair_required": "full"},
    }
    CombatService._weapon_durability(weapon)
    assert "jam" in weapon

    weapon["durability"] = 100
    CombatService._weapon_durability(weapon)
    assert "jam" not in weapon


def test_catastrophic_jam_uses_repair_reduced_maximum_durability():
    weapon = {
        "durability": 59,
        "maxDurability": 60,
        "jam": {"result": 12, "repair_required": "full"},
    }
    CombatService._weapon_durability(weapon)
    assert "jam" in weapon

    weapon["durability"] = 60
    CombatService._weapon_durability(weapon)
    assert "jam" not in weapon


def test_barrel_rupture_clears_after_at_least_one_point_is_repaired():
    weapon = {
        "durability": 20,
        "maxDurability": 100,
        "jam": {
            "result": 11,
            "repair_required": "increase",
            "durability_after": 20,
        },
    }
    CombatService._weapon_durability(weapon)
    assert "jam" in weapon

    weapon["durability"] = 21
    CombatService._weapon_durability(weapon)
    assert "jam" not in weapon


def test_narrative_skill_check_uses_conditions_and_disadvantage(monkeypatch):
    rolls = iter([18, 4])
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: next(rolls))
    character_data = {
        "skills": {"physical": {"shooting": {"base": 10, "bonus": 0}}},
        "health": {"painLevel": 2, "exhaustion": 0, "psyState": 30, "effects": []},
    }

    check = CombatService._narrative_skill_check(
        character_data, "skills.physical.shooting"
    )

    assert check["rolls"] == [18, 4]
    assert check["roll"] == 4
    assert check["disadvantage"] is True
    assert check["status_modifier"] == -2
    assert check["total"] == 2


def test_narrative_skill_check_uses_help_advantage(monkeypatch):
    rolls = iter([3, 17])
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: next(rolls))
    character_data = {
        "skills": {"physical": {"shooting": {"base": 10, "bonus": 0}}},
        "health": {"effects": []},
    }

    check = CombatService._narrative_skill_check(
        character_data, "skills.physical.shooting", advantage=True
    )

    assert check["rolls"] == [3, 17]
    assert check["roll"] == 17
    assert check["advantage"] is True


def test_narrative_social_check_adds_charisma_modifier(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 10)
    character_data = {
        "skills": {
            "social": {
                "persuasion": {"base": 12, "bonus": 0},
                "charisma": {"base": 14, "bonus": 0},
            }
        },
        "health": {"effects": []},
    }

    check = CombatService._narrative_skill_check(
        character_data, "skills.social.persuasion"
    )

    assert check["skill_modifier"] == 1
    assert check["related_modifier"] == 2
    assert check["total"] == 13


def test_awareness_channels_use_only_their_matching_bonuses(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 10)
    character_data = {
        "skills": {"physical": {"awareness": {"base": 10, "bonus": 0}}},
        "equipment": {
            "headphones": {"awarenessBonus": 3},
            "detector": {"bonus": 2},
        },
        "health": {
            "effects": [],
            "combatMeta": {"consumableModifiers": [{
                "stat": "vision_awareness", "value": 4, "remaining": 3,
            }]},
        },
    }

    visual = CombatService._narrative_skill_check(
        character_data, "skills.physical.awareness.visual",
    )
    hearing = CombatService._narrative_skill_check(
        character_data, "skills.physical.awareness.hearing",
    )

    assert visual["awareness_mode"] == "visual"
    assert visual["equipment_modifier"] == 4
    assert visual["total"] == 14
    assert hearing["awareness_mode"] == "hearing"
    assert hearing["equipment_modifier"] == 3
    assert hearing["total"] == 13


def test_hearing_awareness_automatically_fails_at_ninety_deafness(monkeypatch):
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 20)
    character_data = {
        "skills": {"physical": {"awareness": {"base": 20, "bonus": 0}}},
        "equipment": {"headphones": {"awarenessBonus": 10}},
        "health": {
            "effects": [{"type": "deafness", "value": 90, "active": True}],
        },
    }

    check = CombatService._narrative_skill_check(
        character_data, "skills.physical.awareness.hearing",
    )

    assert check["automatic_failure"] is True
    assert check["failure_reason"] == "Персонаж не слышит"
    assert check["rolls"] == [0]
    assert check["roll"] == 0


def test_weapon_fire_rate_counts_shots_per_weapon_and_resets_each_round():
    meta = {}
    weapon = {"fireRate": 3}

    first = CombatService._validate_weapon_fire_rate(meta, 1, 0, weapon, 2)
    assert first == {"fire_rate": 3, "fired": 0, "remaining": 3}
    assert CombatService._record_weapon_shots(meta, 1, 0, 2) == 2

    with pytest.raises(ValidationError, match="осталось выстрелов.*1"):
        CombatService._validate_weapon_fire_rate(meta, 1, 0, weapon, 2)

    assert CombatService._validate_weapon_fire_rate(
        meta, 1, 1, weapon, 3
    )["fired"] == 0
    assert CombatService._validate_weapon_fire_rate(
        meta, 2, 0, weapon, 3
    )["fired"] == 0


def test_mutant_acceleration_uses_value_from_each_trait():
    kabar = {
        "mutant": {
            "traits": [
                "Возможность разгона. На следующий ход он получает +1 Перемещение."
            ]
        }
    }
    zhmerka = {
        "mutant": {
            "traits": [
                "Возможность разгона. На следующий ход она получает +2 Перемещения."
            ]
        }
    }

    assert CombatService._mutant_acceleration_bonus(kabar) == 1
    assert CombatService._mutant_acceleration_bonus(zhmerka) == 2


def test_mutant_reinforced_forehead_adds_head_protection_only():
    data = {
        "mutant": {
            "physical_protection": 20,
            "traits": [
                "Усиленное лобовое бронирование. Физ. защита в голове увеличена на 15%"
            ],
        },
        "equipment": {},
    }

    head, _ = CombatService._target_armor(data, "head")
    chest, _ = CombatService._target_armor(data, "chest")

    assert head == 35
    assert chest == 20


def test_mutant_camouflage_is_active_only_through_configured_round():
    data = {
        "health": {
            "combatMeta": {
                "mutantCamouflageActive": True,
                "mutantCamouflageUntilRound": 5,
            }
        }
    }

    assert CombatService._mutant_camouflage_active(data, 5) is True
    assert CombatService._mutant_camouflage_active(data, 6) is False


def test_mutant_opening_ambush_grants_attack_advantage():
    data = {
        "health": {
            "combatMeta": {
                "mutantOpeningAttackAdvantage": True,
            }
        }
    }

    assert CombatService._has_roll_advantage(
        data, "skills.physical.shooting"
    ) is True
    assert CombatService._has_roll_advantage(
        data, "skills.physical.melee"
    ) is True
    assert CombatService._has_roll_advantage(
        data, "skills.physical.will"
    ) is False


def test_regular_ambush_does_not_grant_opening_combat_bonuses():
    data = {
        'health': {'combatMeta': {'mutantAmbushActive': True}},
        'mutant': {
            'traits': [
                'Засада. Сложность обнаружения существа в засаде увеличена на 5.',
            ],
            'variant': {'traits': []},
        },
    }

    assert CombatService._mutant_opening_ambush_eligible(data) is False
    assert CombatService._skill_modifier(
        data, 'skills.other.stealth',
    ) == 5


def test_swamp_bloodsucker_ambush_grants_opening_combat_bonuses():
    data = {
        'health': {'combatMeta': {'mutantAmbushActive': True}},
        'mutant': {
            'traits': [],
            'variant': {'traits': [
                'Атака из засады. Если начинает бой первым, получает бонусы.',
            ]},
        },
    }

    assert CombatService._mutant_opening_ambush_eligible(data) is True


def test_anomalous_shield_raises_physical_protection_to_eighty():
    data = {
        "mutant": {"physical_protection": 5},
        "health": {"combatMeta": {"mutantAnomalyShield": True}},
        "equipment": {},
    }

    protection, layers = CombatService._target_armor(data, "chest")

    assert protection == 80
    assert any(layer["slot"] == "mutant_anomaly_shield" for layer in layers)


def test_electrocrow_can_only_steal_supported_devices():
    assert CombatService._is_stealable_electronic(
        {"name": "Детектор аномалий Медведь"}
    ) is True
    assert CombatService._is_stealable_electronic(
        {"name": "ПНВ армейский"}
    ) is True
    assert CombatService._is_stealable_electronic(
        {"name": "Аптечка"}
    ) is False


def test_throwable_object_weight_uses_explicit_value_then_type_default():
    explicit = type("Object", (), {
        "properties": {"weight_kg": 12.5}, "type": "chair",
    })()
    default = type("Object", (), {
        "properties": {}, "type": "barrel",
    })()

    assert CombatService._throwable_object_weight(explicit) == 12.5
    assert CombatService._throwable_object_weight(default) == 40


def test_zombification_controller_overrides_character_owner():
    loc_char = SimpleNamespace(
        controlled_by=5,
        character=SimpleNamespace(owner_id=5, data={
            "health": {
                "combatMeta": {"zombificationController": 9},
            }
        }),
    )

    assert CombatService._can_end_turn_for_character(loc_char, 5) is False
    assert CombatService._can_end_turn_for_character(loc_char, 9) is True
    assert CombatService._can_end_turn_for_character(loc_char, 5, is_gm=True) is True


@pytest.mark.parametrize('condition', [
    {'effects': [{'type': 'death'}]},
    {'effects': [{'type': 'unconsciousness'}]},
    {'current': 0, 'max': 100},
    {'organs': {'brain': {'current': 0}}},
    {'intoxication': 100},
])
@pytest.mark.parametrize('amount,force_manifest', [(1, False), (1, True), (0, True)])
def test_dead_or_unconscious_stress_trigger_returns_before_rng(monkeypatch, condition, amount, force_manifest):
    from copy import deepcopy

    def unexpected_call(*args, **kwargs):
        pytest.fail('Заблокированный стресс не должен менять данные или бросать RNG')

    monkeypatch.setattr(combat_module.random, 'randint', unexpected_call)
    monkeypatch.setattr(combat_module, 'flag_modified', unexpected_call)
    health = {'stress': 4, **deepcopy(condition)}
    health.setdefault('effects', []).append({'id': 'old', 'type': 'stress_effect'})
    target = SimpleNamespace(character=SimpleNamespace(data={'health': health}))
    before = deepcopy(target.character.data)
    result = CombatService.apply_stress_trigger(target, amount, trigger='damage', force_manifest=force_manifest)
    assert result['blocked'] is True
    assert result['manifested'] is False
    assert result['before'] == result['after'] == 4
    assert target.character.data == before


@pytest.mark.parametrize('condition_type', ['death', 'unconsciousness'])
def test_unconscious_stress_reduction_ignores_force_manifest(monkeypatch, condition_type):
    def unexpected_rng(*args):
        pytest.fail('Снижение стресса не должно вызывать проявление')

    monkeypatch.setattr(combat_module.random, 'randint', unexpected_rng)
    monkeypatch.setattr(combat_module, 'flag_modified', lambda *args: None)
    target = SimpleNamespace(character=SimpleNamespace(data={'health': {
        'stress': 4, 'effects': [{'type': condition_type}, {'id': 'old', 'type': 'stress_effect'}],
    }}))
    result = CombatService.apply_stress_trigger(target, -1, force_manifest=True)
    assert result['before'] == 4
    assert result['after'] == 3
    assert not result.get('manifested')
    assert any(item.get('id') == 'old' and item.get('active', True)
               for item in target.character.data['health']['effects'])


@pytest.mark.parametrize('effect', [
    {'type': 'death', 'active': False},
    {'type': 'unconsciousness', 'active': False},
    {'type': 'death', 'remaining': 0},
    {'type': 'unconsciousness', 'remaining': -1},
    {'type': 'shock'}, {'type': 'stress_stupor'},
])
def test_inactive_conditions_and_stupor_do_not_block_new_stress(monkeypatch, effect):
    monkeypatch.setattr(combat_module, 'flag_modified', lambda *args: None)
    target = SimpleNamespace(character=SimpleNamespace(data={'health': {'stress': 4, 'effects': [effect]}}))
    result = CombatService.apply_stress_trigger(target, 1, check_manifestation=False)
    assert result['blocked'] is False
    assert result['after'] == 5


def test_conscious_character_can_still_have_forced_stress_manifestation(monkeypatch):
    monkeypatch.setattr(combat_module.random, 'randint', lambda *args: 1)
    monkeypatch.setattr(combat_module, 'flag_modified', lambda *args: None)
    target = SimpleNamespace(character=SimpleNamespace(data={'health': {'stress': 0, 'effects': []}}))
    result = CombatService.apply_stress_trigger(target, 1, force_manifest=True)
    assert result['after'] == 1
    assert result['manifested'] is True
    assert target.character.data['health']['effects'][-1]['source'] == 'stress_manifestation'


@pytest.mark.parametrize('condition_type', ['death', 'unconsciousness'])
@pytest.mark.parametrize('action', ['approve', 'replace'])
def test_pending_stress_manifestation_cannot_be_applied_while_unconscious(monkeypatch, condition_type, action):
    from copy import deepcopy

    def unexpected_rng(*args):
        pytest.fail('Отложенное проявление не должно бросать RNG')

    monkeypatch.setattr(combat_module.random, 'randint', unexpected_rng)
    target = SimpleNamespace(posture='standing', character=SimpleNamespace(data={'health': {
        'stress': 4, 'effects': [{'type': condition_type}, {
            'id': 'pending', 'type': 'stress_effect', 'source': 'stress_manifestation',
            'gmPending': True, 'stress_table': 'stress', 'stress_roll': 1,
        }],
    }}))
    before = deepcopy(target.character.data)
    with pytest.raises(ValidationError, match='Нельзя применить'):
        CombatService.resolve_stress_effect(target, 'pending', action, replacement='Укрыться')
    assert target.character.data == before
    assert target.posture == 'standing'



def test_mutant_stress_immunity_still_precedes_forced_manifestation(monkeypatch):
    def unexpected_rng(*args):
        pytest.fail('Иммунитет мутанта не должен вызывать RNG')

    monkeypatch.setattr(combat_module.random, 'randint', unexpected_rng)
    monkeypatch.setattr(combat_module, 'flag_modified', lambda *args: None)
    target = SimpleNamespace(character=SimpleNamespace(data={
        'is_mutant': True, 'health': {'stress': 4, 'effects': [{'type': 'stress_effect'}]},
    }))
    result = CombatService.apply_stress_trigger(target, 1, force_manifest=True)
    assert result['immune'] is True
    assert result['blocked'] is True
    assert target.character.data['health']['stress'] == 0
    assert not any(item['type'] == 'stress_effect'
                   for item in target.character.data['health']['effects'])


@pytest.mark.parametrize("requests", [(1, 2), (2, 1), (1, 1, 1)])
def test_fire_rate_exact_cumulative_boundary_is_atomic_and_next_round_resets(requests):
    from copy import deepcopy

    meta = {}
    weapon = {"fireRate": 3}
    for shots in requests:
        CombatService._validate_weapon_fire_rate(meta, 7, 0, weapon, shots)
        CombatService._record_weapon_shots(meta, 7, 0, shots)
    assert CombatService._weapon_round_shots(meta, 7, 0) == 3
    before_rejection = deepcopy(meta)
    with pytest.raises(ValidationError, match="скорострельность"):
        CombatService._validate_weapon_fire_rate(meta, 7, 0, weapon, 1)
    assert meta == before_rejection
    assert CombatService._validate_weapon_fire_rate(meta, 7, 1, weapon, 3)["fired"] == 0
    assert CombatService._validate_weapon_fire_rate(meta, 8, 0, weapon, 3)["fired"] == 0
    assert CombatService._record_weapon_shots(meta, 8, 0, 1) == 1
    assert CombatService._weapon_round_shots(meta, 8, 0) == 1
