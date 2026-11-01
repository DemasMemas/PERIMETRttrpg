"""Контрпримеры карточек №56/59. Запуск — только после отдельного допуска.

Намеренно без xfail: нарушение контракта карточки должно дать обычный FAIL.
Точные параметры светошумового эффекта и размеры реального ствола не задаются.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import app.services.combat as combat_module
from app.extensions import db
from app.models import Lobby, Location, LocationObject, User
from app.services.combat import CombatService


@pytest.fixture(params=["installedMagazine", "fixedAmmo"])
def flash_round(request):
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
    attacker = SimpleNamespace(character=SimpleNamespace(data={"weapons": [weapon]}))
    target = SimpleNamespace(character=SimpleNamespace(data={"health": health}), hp_zones={})
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
def test_tree_shooting_distinguishes_storage_and_ray_height(tree_location, storage, ray):
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
        assert set(analysis["blocked_zones"]) == {
            "head", "chest", "abdomen", "left_arm", "right_arm", "left_leg", "right_leg",
        }
    else:
        assert analysis["grade"] == "none"
        assert analysis["blocked_zones"] == []
