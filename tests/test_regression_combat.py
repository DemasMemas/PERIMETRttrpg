"""Регрессии карточек №56/59 и специальных патронов по ответам автора и книге."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import app.services.combat as combat_module
from app.extensions import db
from app.models import Lobby, LobbyCharacter, Location, LocationCharacter, LocationObject, User
from app.services.combat import CombatService
from test_grenade_rules import grenade_game


def test_moroz_reduces_temperature_once_at_end_of_round(grenade_game):
    from app.services.consumable_effects import parse_consumable_effects
    from app.services.general_consumable import _apply_profile
    actor=grenade_game['actor_location'];health=actor.character.data['health']
    health.update(temperature=38,exhaustion=1)
    profile=parse_consumable_effects('Жаропонижающее средство Мороз. Ампула. Работает 4 хода.')
    _apply_profile(health,profile,profile['direct'],{'name':'Жаропонижающее средство Мороз','templateId':84},in_combat=True)
    for before in [38,37,36,35]:
        CombatService._apply_periodic_health_effects(actor,phase='turn_end')
        assert actor.character.data['health']['temperature']==before
        CombatService._apply_periodic_health_effects(actor,phase='round_end')
        assert actor.character.data['health']['temperature']==before-1
    CombatService._apply_periodic_health_effects(actor,phase='round_end')
    assert actor.character.data['health']['temperature']==34


@pytest.mark.parametrize('dx,dy,affected',[(2,0,True),(2,2,True),(2,2.01,False),(-2,0,False),(0,2,False),(5.51,0,False)])
def test_flash_cone_includes_45_degrees_and_excludes_side_or_back(grenade_game,dx,dy,affected):
    actor,target=grenade_game['actor_location'],grenade_game['target_location']
    target.pos_x,target.pos_y=actor.pos_x+dx,actor.pos_y+dy
    target.facing_x,target.facing_y=-dx,-dy
    db.session.flush()
    before=target.character.data['health']['current']
    result=CombatService._resolve_flash_round(target,actor,{'fire_mode':'unaimed'},
        {'caliber':'12x70','ammo_variant':'flashbang'})
    assert (result['blindness']>0)==affected
    assert target.character.data['health']['current']==before


def test_flash_ammunition_replaces_weapon_noise_including_mixed_shots(grenade_game):
    actor=grenade_game['actor_location'];location=grenade_game['location']
    weapon={'name':'Проверочный ствол','noise':40}
    volume,rows=CombatService._apply_fired_noise(location,actor,weapon,[{'flash_round':True,'flash_noise':15}],1)
    assert volume==15
    assert next(r for r in rows if r['character_id']==actor.character_id)['noise_received']==7.5
    volume,rows=CombatService._apply_fired_noise(location,actor,weapon,[{'flash_round':True,'flash_noise':15},{}],2)
    assert volume==40
    assert [r['noise_received'] for r in rows if r['character_id']==actor.character_id]==[7.5,20]


@pytest.mark.parametrize('caliber,variant,multiplier',[
    ('12x70 Пуля','ep',.1),('12х70 Пуля','rip',.1),('12×70 Пуля','ep',.1),
    ('12.7x55','ep',.1),('12.7х55','bp',.25),('7.62x39','ep',0),
])
def test_actual_caliber_suffix_keeps_special_nonpenetrating_damage(flash_round,caliber,variant,multiplier):
    _,stack,actor,target,details=flash_round
    target=LocationCharacter(character=target.character,hp_zones={})
    stack.update(name=caliber+' ('+variant+')',attributes={'caliber':caliber,'ammo_variant':variant,'damage':200,'armor_piercing':0})
    target.character.data['equipment']={'armor':{'name':'Проверочная броня','category':'armor','durability':100,
        'attributes':{'max_durability':100,'protection':{'physical':.9},'protection_zones':['torso']}}}
    result=CombatService._resolve_attack(target,actor,details,aimed_zone='chest',forced_roll=15)
    assert result['full_non_penetration']
    assert result['behind_armor_multiplier']==multiplier
    assert result['damage']==round(200*multiplier)


@pytest.mark.parametrize('category,slot,expected_wear',[('helmet','helmet',100),('gas_mask','gasMask',1)])
def test_filter_requirement_does_not_turn_helmet_into_simple_gas_mask(category,slot,expected_wear):
    item={'name':'Средство защиты','category':category,'durability':20,'maxDurability':20,
          'attributes':{'max_durability':20,'requires_filter':True,'protection':{'physical':.3},'material':'Композит'}}
    data={'equipment':{slot:item}}
    assert CombatService._is_gas_mask_item(slot,item,item['attributes'])
    result=CombatService._apply_anomaly_equipment_damage(data,50,{'armor_damage_multiplier':2})
    assert result[0]['damage']==expected_wear
    assert ('stage' in item)==(category=='helmet')


@pytest.fixture(params=["installedMagazine", "fixedAmmo"])
def flash_round(request, app):
    # Реальный стек патронов: профиль и выбор ammo_variant не подменяются.
    stack = {
        "id": "flash-round", "name": "Светошумовой патрон", "category": "ammo",
        "quantity": 2,
        "attributes": {"caliber": "12x70", "ammo_variant": "flashbang",
                       "damage": 50, "armor_piercing": 0},
    }
    weapon = {"name": "Ружьё", "ammo": 2}
    if request.param == "installedMagazine":
        weapon[request.param] = {"ammo": [stack]}
    else:
        weapon[request.param] = [stack]
    health = {"current": 700, "max": 700, "effects": [],
              "zones": {"chest": {"current": 100, "max": 100}}}
    attacker = SimpleNamespace(character=LobbyCharacter(name='Стрелок',data={"weapons": [weapon]}))
    target = SimpleNamespace(character=LobbyCharacter(name='Цель',data={"health": health}), hp_zones={})
    details = {"weapon_index": 0, "hit_difficulty": 1,
               "fire_mode": "unaimed", "round_number": 1, "target_distance": 2}
    return weapon, stack, attacker, target, details


def test_flash_round_is_selected_from_actual_loaded_stack(flash_round):
    weapon, stack, *_ = flash_round
    profile, selected = CombatService._ranged_damage_profile(weapon)
    assert selected is stack
    assert profile["ammo_variant"] == "flashbang"
    assert profile["caliber"] == "12x70"


def test_flash_round_does_not_roll_to_hit(flash_round, monkeypatch):
    _, _, attacker, target, details = flash_round

    # Обычный бросок 1 гарантирует промах и не маскирует проверку уроном.
    # Не запрещаем отдельные будущие проверки сопротивления эффектам.
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 1)
    result = CombatService._resolve_attack(target, attacker, details)
    assert result.get("roll") is None, "Патрону не нужна проверка попадания"
    assert result.get("rolls", []) == []


def test_flash_round_cannot_enter_ordinary_damage_or_trauma(flash_round, monkeypatch):
    _, _, attacker, target, details = flash_round
    before = deepcopy(target.character.data["health"])

    def check_damage_boundary(target_arg, damage, zone, profile, **kwargs):
        assert damage == 0, "Патрон не должен наносить обычный урон"
        assert kwargs.get("trauma_checks", 1) == 0, "Патрон не должен проверять доп. травму"
        return deepcopy(before)

    # Фиксируем успешный обычный бросок, чтобы промах не скрыл дефект урона.
    # Эта проверка отделена от запрета самого броска попадания выше.
    monkeypatch.setattr(combat_module.random, "randint", lambda *_: 20)
    monkeypatch.setattr(CombatService, "_apply_attack_damage", check_damage_boundary)
    monkeypatch.setattr(CombatService, "_roll_firearm_bleeding", lambda *_: None)
    result = CombatService._resolve_attack(
        target, attacker, details, aimed_zone="chest", forced_roll=20,
    )
    assert result.get("damage", 0) == 0
    after = target.character.data["health"]
    assert after["current"] == before["current"]
    assert after["zones"] == before["zones"]
    ordinary_effects = [effect for effect in after["effects"]
                        if effect.get("type") not in {"blindness", "deafness"}]
    assert ordinary_effects == before["effects"]


def test_flash_order_and_decimal_armor_boundaries():
    assert CombatService._received_flash({}, 100, 2, .5) == 10
    assert CombatService._protection_percent(.55) == 55
    assert CombatService._penetration_damage_multiplier(55,55.00000000000001) == 1
    assert CombatService._penetration_damage_multiplier(50,55.00000000000001) == .75
    assert CombatService._flash_round_profile({'caliber':'18×45 СЗ','ammo_name':'Переименованный патрон'})['blindness']==80
    assert CombatService._flash_round_profile({'caliber':'12x70 Картечь','ammo_name':'Светошумовой',
        'ammo_variant':'incendiary'}) is None


@pytest.mark.parametrize('caliber,extra_pain',[('18x45 Т',3),('18x45 И',2),('12x70 Газовый',3)])
def test_special_round_adds_declared_pain_only_after_penetration(flash_round,monkeypatch,caliber,extra_pain):
    _,stack,actor,target,details=flash_round
    target=LocationCharacter(character=target.character,hp_zones={},effects=[],posture='standing')
    stack['attributes'].update(caliber=caliber,ammo_variant=None,damage=30,armor_piercing=0)
    stack['name']=caliber
    initial=deepcopy(target.character.data)
    monkeypatch.setattr(combat_module.random,'randint',lambda low,high:min(high,max(low,10)))
    profile,_=CombatService._ranged_damage_profile(actor.character.data['weapons'][0])
    assert profile['pain_on_penetration']==extra_pain
    ordinary={k:v for k,v in profile.items() if k not in {'pain_on_penetration','pain_per_round','pain_rounds'}}
    CombatService._resolve_attack(target,actor,details,aimed_zone='chest',forced_roll=15,profile_override=ordinary)
    baseline=target.character.data['health'].get('painLevel',0)
    target.character.data=deepcopy(initial)
    result=CombatService._resolve_attack(target,actor,details,aimed_zone='chest',forced_roll=15)
    assert result['ammunition_pain']==extra_pain
    assert target.character.data['health']['painLevel']==min(10,baseline+extra_pain)
    if 'Газовый' in caliber:
        effect=next(e for e in target.character.data['health']['effects'] if e.get('source')=='ammunition_gas')
        assert effect['remaining']==5 and effect['tick']=='round_end'
    target.character.data=deepcopy(initial)
    target.character.data['equipment']={'armor':{'attributes':{'protection':{'physical':.05}}}}
    result=CombatService._resolve_attack(target,actor,details,aimed_zone='chest',forced_roll=15)
    assert not result.get('ammunition_pain')
    assert not any(e.get('source')=='ammunition_gas' for e in target.character.data['health']['effects'])


@pytest.mark.parametrize('caliber',['18x45 И','12x70 Газовый'])
def test_pepper_head_hit_requires_penetration_and_adds_combat_effects(flash_round,monkeypatch,caliber):
    _,stack,actor,target,details=flash_round
    target=LocationCharacter(character=target.character,hp_zones={},effects=[],posture='standing')
    stack['attributes'].update(caliber=caliber,ammo_variant=None,damage=5,armor_piercing=0)
    stack['name']=caliber
    monkeypatch.setattr(combat_module.random,'randint',lambda low,high: 99 if high==100 else 10)
    chest=CombatService._resolve_attack(target,actor,details,aimed_zone='chest',forced_roll=15)
    assert not chest.get('pepper_head_hit')
    head=CombatService._resolve_attack(target,actor,details,aimed_zone='head',forced_roll=15)
    assert head['pepper_head_hit']=={'hits':1,'shock_roll':99,'shock':False}
    health=target.character.data['health']
    assert health['combatMeta']['pepperSkipTurns']==1
    assert CombatService._health_roll_modifier(target.character.data,'skills.physical.will') <= -5
    assert any(e.get('source')=='pepper_head_pain' for e in health['effects'])
    target.character.data['equipment']={'helmet':{'attributes':{'protection':{'physical':.5}}}}
    blocked=CombatService._resolve_attack(target,actor,details,aimed_zone='head',forced_roll=15)
    assert not blocked.get('pepper_head_hit')
    assert health['combatMeta']['pepperHeadHits']==1


def test_third_pepper_head_hit_locks_shock_six_hours_and_penalizes_one_day(grenade_game,monkeypatch):
    target=grenade_game['target_location']
    data=target.character.data
    health=data.setdefault('health',{})
    health['painLevel']=0
    health['effects']=[]
    monkeypatch.setattr(combat_module.random,'randint',lambda low,high: 99)
    for count in (1,2,3):
        result=CombatService._apply_pepper_head_hit(health,data)
        assert result['hits']==count
    assert result['shock'] and result['shock_roll'] is None
    assert CombatService._character_condition(data)['can_recover'] is False
    assert CombatService._health_roll_modifier(data,'skills.physical.awareness') == -10
    assert CombatService._health_roll_modifier(data,'skills.physical.strength') == -5
    CombatService._apply_periodic_health_effects(target,phase='turn_end')
    assert health['painLevel']==1
    CombatService.end_combat(target.location_id,grenade_game['lobby'].gm_id)
    assert CombatService._character_condition(data)['can_recover'] is False
    assert CombatService._health_roll_modifier(data,'skills.physical.awareness') == -6
    assert CombatService._health_roll_modifier(data,'skills.physical.strength') == -1
    CombatService._advance_character_time(target,6*3600)
    current=target.character.data
    assert CombatService._character_condition(current)['state']=='active'
    assert CombatService._health_roll_modifier(current,'skills.physical.awareness') == -6
    CombatService._advance_character_time(target,18*3600)
    assert CombatService._health_roll_modifier(target.character.data,'skills.physical.awareness') == -1


def test_pepper_hit_skips_the_next_turn_without_stacking(grenade_game,monkeypatch):
    actor=grenade_game['actor_location']
    data=actor.character.data
    meta=data['health'].setdefault('combatMeta',{})
    monkeypatch.setattr(combat_module.random,'randint',lambda low,high: 99)
    CombatService._apply_pepper_head_hit(data['health'],data)
    CombatService._apply_pepper_head_hit(data['health'],data)
    assert meta['pepperSkipTurns']==1
    CombatService._prepare_character_for_turn(actor)
    assert actor.action_points_current==0
    assert actor.free_actions_current==0
    assert not CombatService._can_take_combat_turn(actor)
    assert meta['pepperSkipTurns']==0
    meta.pop('pepperSkipTurnActive')
    CombatService._prepare_character_for_turn(actor)
    assert CombatService._can_take_combat_turn(actor)


def test_pepper_skip_turn_advances_combat_automatically(grenade_game):
    actor=grenade_game['actor_location']
    target=grenade_game['target_location']
    target.character.data['health'].setdefault('combatMeta',{})['pepperSkipTurns']=1
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(target.character,'data')
    db.session.commit()
    result=CombatService.end_turn(actor.location_id,grenade_game['lobby'].gm_id,actor.id)
    assert any(row['location_character_id']==target.id for row in result.get('auto_skipped',[]))
    assert not target.character.data['health']['combatMeta'].get('pepperSkipTurnActive')
    assert target.character.data['health']['combatMeta']['pepperSkipTurns']==0


def test_zero_damage_signal_round_never_creates_wounds_or_armor_wear(flash_round,monkeypatch):
    _,stack,actor,target,details=flash_round
    stack['name']='18x45 СМ'
    stack['attributes'].update(caliber='18x45 СМ',ammo_variant=None,damage=0)
    target.character.data['equipment']={'armor':{'durability':100,'attributes':{'protection':{'physical':.5}}}}
    before=deepcopy(target.character.data)
    monkeypatch.setattr(combat_module.random,'randint',lambda *_:20)
    result=CombatService._resolve_attack(target,actor,details,aimed_zone='chest',forced_roll=15)
    assert result['non_damaging_round'] is True
    assert result['damage']==0 and not result['armor_damage']
    assert target.character.data==before


@pytest.mark.parametrize('thermal,ignites',[(.49,True),(.5,False),(.51,False)])
def test_incendiary_ignition_boundary_is_checked_before_armor_wear(flash_round,monkeypatch,thermal,ignites):
    _,stack,actor,target,details=flash_round
    target=LocationCharacter(character=target.character,hp_zones={},effects=[],posture='standing')
    stack['attributes'].update(caliber='12x70 Картечь',ammo_variant='incendiary',damage=30)
    stack['name']='12x70 Картечь · Зажигательный'
    target.character.data['equipment']={'armor':{'durability':100,'attributes':{
        'max_durability':100,'protection':{'thermal':thermal,'physical':.9}}}}
    monkeypatch.setattr(combat_module.random,'randint',lambda low,high:min(high,max(low,10)))
    result=CombatService._resolve_attack(target,actor,details,aimed_zone='chest',forced_roll=15)
    fires=[e for e in target.character.data['health']['effects'] if e['type']=='burning']
    assert bool(fires)==ignites
    if ignites:assert fires[0]['remaining']==3 and result['ignited_rounds']==3


@pytest.mark.parametrize('caliber,checks',[('9x19',2),('12.7x55',3)])
def test_explosive_round_has_extra_trauma_check_and_plus_fifteen_chance(flash_round,monkeypatch,caliber,checks):
    _,stack,actor,target,details=flash_round
    target=LocationCharacter(character=target.character,hp_zones={},effects=[],posture='standing')
    stack['attributes'].update(caliber=caliber,ammo_variant='explosive',damage=30)
    stack['name']=caliber+' · Разрывной'
    monkeypatch.setattr(combat_module.random,'randint',lambda low,high:40 if high==100 else min(high,max(low,10)))
    result=CombatService._resolve_attack(target,actor,details,aimed_zone='chest',forced_roll=15)
    # 40 не проходит обычный порог груди 50, но проходит 50−15.
    assert len(result['additional_traumas'])==checks
    assert all(t['chance_roll']==40 for t in result['additional_traumas'])


@pytest.fixture
def tree_location(app):
    # DDL/очистка принадлежат существующей guarded memory-фикстуре conftest.
    user = User(username="tree-regression", email="tree-regression@example.com", password_hash="x")
    db.session.add(user)
    db.session.flush()
    lobby = Lobby(name="Деревья", gm_id=user.id, invite_code="TREECHECK")
    db.session.add(lobby)
    db.session.flush()
    location = Location(lobby_id=lobby.id, name="Контрпример дерева",
                        grid_width=6, grid_height=6, world_tile_x=0, world_tile_z=0,
                        tiles_data=[[{"height": 1, "objects": []} for _ in range(6)] for _ in range(6)])
    db.session.add(location)
    db.session.flush()
    return location


@pytest.mark.parametrize("storage", ["LocationObject", "tiles_data.objects"])
@pytest.mark.parametrize("ray", ["centre", "beside", "above"])
def test_tree_shooting_distinguishes_storage_and_ray_height(tree_location, storage, ray, request):
    location = tree_location
    if storage == "LocationObject":
        # Явно заданная колонна дерева, не вся крона и не новое правило её размера.
        tree = LocationObject(location_id=location.id, name="Дерево", type="tree", tile_x=2, tile_y=2,
                              properties={"dimensions": {"width": .2, "depth": .2, "height": 2.5}})
        db.session.add(tree)
    else:
        tiles = deepcopy(location.tiles_data)
        tiles[2][2]["objects"] = [{"type": "tree", "x": 0, "z": 0, "scale": 1, "rotation": 0}]
        location.tiles_data = tiles
    db.session.flush()
    y = 2 if ray != "beside" else 3
    level = 1 if ray == "above" else 0
    shooter = SimpleNamespace(pos_x=0, pos_y=y, floor_level=level, posture="standing")
    target = SimpleNamespace(pos_x=4, pos_y=y, floor_level=level, posture="standing")
    analysis = CombatService._cover_analysis(location.id, shooter, target)
    if ray == "centre":
        assert analysis["grade"] == "full", "Центральный луч через дерево должен встретить укрытие"
        assert analysis['zones']['chest']['physical_protection'] == 20
        assert set(analysis["blocked_zones"]) == {
            "head", "chest", "abdomen", "left_arm", "right_arm", "left_leg", "right_leg",
        }
    else:
        assert analysis["grade"] == "none"
        assert analysis["blocked_zones"] == []
