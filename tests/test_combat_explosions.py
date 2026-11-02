from app.extensions import db
from app.models import (
    Lobby,
    LobbyCharacter,
    Location,
    LocationCharacter,
    LocationCombatState,
    User,
)
from app.services.combat import CombatService, EXPLOSIVE_PROFILES


class _CombatEventState:
    location_id = 1
    round_number = 2
    area_effects = []

    def __init__(self, pending):
        self.pending_explosives = pending


def test_explosive_profiles_accept_inventory_and_caliber_aliases():
    assert CombatService._explosive_profile({"name": "Граната РГД-5"})["key"] == "rgd5"
    assert CombatService._explosive_profile({"name": "Подствольная граната ВОГ-25"})["key"] == "vog25"
    assert CombatService._explosive_profile({"name": "N-101-2"})["projectile_range"] == 65
    assert CombatService._explosive_profile({"name": "Дымовая граната РДГ-6"})["key"] == "rdg6"
    assert CombatService._explosive_profile({"name": "Химическая граната Черемуха"})["key"] == "cheremukha"


def test_round_fuse_waits_for_round_boundary(monkeypatch):
    state = _CombatEventState([{
        "id": "grenade-1",
        "item_name": "РГД-5",
        "x": 4,
        "y": 6,
        "profile": {**EXPLOSIVE_PROFILES["rgd5"], "key": "rgd5", "name": "РГД-5"},
        "actor_id": 10,
        "trigger": "round_start",
        "round": 2,
    }])
    monkeypatch.setattr(
        CombatService,
        "_activate_explosive_event",
        lambda *_: {"item_name": "РГД-5", "detonated": True},
    )

    assert CombatService._process_pending_explosives(
        state, phase="turn_end", actor_id=10,
    ) == []
    detonations = CombatService._process_pending_explosives(
        state, phase="round_start",
    )

    assert detonations == [{"item_name": "РГД-5", "detonated": True}]
    assert state.pending_explosives == []


def test_special_grenade_creates_persistent_area():
    profile = {
        **EXPLOSIVE_PROFILES["underbarrel_smoke"],
        "key": "underbarrel_smoke",
        "name": "Подствольный дымовой",
    }

    area = CombatService._area_from_profile(profile, 8, 9, 3)

    assert area["type"] == "smoke_growing"
    assert area["radius"] == 2
    assert area["max_radius"] == 4
    assert area["grow_rounds"] == 2


def test_flash_strength_uses_target_facing():
    target = type("Target", (), {
        "pos_x": 5,
        "pos_y": 5,
        "facing_x": 1,
        "facing_y": 0,
    })()

    assert CombatService._flash_facing_multiplier(target, 8, 5) == 1
    assert CombatService._flash_facing_multiplier(target, 2, 5) == 0.1


def test_projectile_detonates_at_ammunition_range():
    x, y, airburst = CombatService._clamp_projectile_point(0, 0, 100, 0, 65)

    assert (x, y) == (65, 0)
    assert airburst is True


def test_projectile_does_not_airburst_inside_its_range():
    x, y, airburst = CombatService._clamp_projectile_point(5, 5, 20, 10, 75)

    assert (x, y) == (20, 10)
    assert airburst is False


def test_scatter_uses_failure_margin_and_stays_inside_location(monkeypatch):
    monkeypatch.setattr("app.services.combat.random.random", lambda: 0)

    assert CombatService._scatter_point(8, 8, 4, 10, 10) == (9, 8)
    # Недобор любой величины даёт не более 10 м разлёта.
    assert CombatService._scatter_point(3, 3, 99, 30, 30) == (13, 3)


def test_explosion_applies_blast_and_posture_reduced_fragments(app, monkeypatch):
    with app.app_context():
        user = User(username="explosion-gm", email="explosion@example.com", password_hash="x")
        db.session.add(user)
        db.session.flush()
        lobby = Lobby(name="Explosion", gm_id=user.id, invite_code="BOOMTEST")
        db.session.add(lobby)
        db.session.flush()
        location = Location(
            lobby_id=lobby.id,
            name="Range",
            grid_width=20,
            grid_height=20,
            world_tile_x=0,
            world_tile_z=0,
        )
        db.session.add(location)
        db.session.flush()

        def add_target(name, x, posture):
            character = LobbyCharacter(
                lobby_id=lobby.id,
                owner_id=user.id,
                name=name,
                data={
                    "health": {
                        "current": 2000,
                        "max": 2000,
                        "painLevel": 0,
                        "stress": 0,
                        "effects": [],
                        "zones": {
                            "head": {"current": 500, "max": 500},
                            "chest": {"current": 500, "max": 500},
                            "abdomen": {"current": 500, "max": 500},
                            "leftArm": {"current": 500, "max": 500},
                            "rightArm": {"current": 500, "max": 500},
                            "leftLeg": {"current": 500, "max": 500},
                            "rightLeg": {"current": 500, "max": 500},
                        },
                    },
                    "equipment": {},
                },
            )
            db.session.add(character)
            db.session.flush()
            placed = LocationCharacter(
                location_id=location.id,
                character_id=character.id,
                pos_x=x,
                pos_y=5,
                posture=posture,
            )
            db.session.add(placed)
            return placed

        standing = add_target("Standing", 6, "standing")
        prone = add_target("Prone", 4, "prone")
        db.session.commit()
        monkeypatch.setattr(CombatService, "_explosion_cover", lambda *args: {"protection": 0, "objects": []})
        monkeypatch.setattr(CombatService, "_grenade_fragment_zone", lambda: "chest")

        result = CombatService.resolve_explosion(
            location.id,
            5,
            5,
            {**EXPLOSIVE_PROFILES["rgd5"], "key": "rgd5", "name": "РГД-5"},
            round_number=1,
        )

        by_name = {entry["name"]: entry for entry in result["targets"]}
        assert by_name["Standing"]["blast_damage"] == 700
        assert by_name["Standing"]["fragment_damage"] == 180
        assert by_name["Prone"]["fragment_damage"] == 18
        assert standing.character.data["health"]["current"] < 2000
        assert prone.character.data["health"]["current"] < 2000


def test_explosion_summary_contains_roll_impact_and_damage():
    summary = CombatService.format_explosion_summary({
        "explosive": {
            "item_name": "ВОГ-25",
            "roll": 13,
            "rolls": [13],
            "difficulty": 12,
            "success": True,
            "disadvantage": False,
            "airburst": False,
            "impact": {"x": 7, "y": 9},
            "explosion": {
                "targets": [{
                    "name": "Цель",
                    "blast_damage": 400,
                    "fragment_damage": 90,
                    "fragment_zone": "chest",
                    "cover_protection": 20,
                    "blast_trauma": {"type": "concussion"},
                }],
            },
        },
    })

    assert "d20 13" in summary
    assert "7, 9" in summary
    assert "волна 400" in summary
    assert "осколки 90" in summary


def test_hand_grenade_action_spends_two_ap_and_consumes_one_item(app, monkeypatch):
    with app.app_context():
        user = User(username="thrower", email="thrower@example.com", password_hash="x")
        db.session.add(user)
        db.session.flush()
        lobby = Lobby(name="Throw", gm_id=user.id, invite_code="THROW123")
        db.session.add(lobby)
        db.session.flush()
        location = Location(
            lobby_id=lobby.id,
            name="Throwing range",
            grid_width=30,
            grid_height=30,
            world_tile_x=0,
            world_tile_z=0,
        )
        db.session.add(location)
        db.session.flush()
        character = LobbyCharacter(
            lobby_id=lobby.id,
            owner_id=user.id,
            name="Thrower",
            data={
                "skills": {
                    "physical": {
                        "throwing": {"base": 10, "bonus": 0},
                        "strength": {"base": 10, "bonus": 0},
                        "agility": {"base": 10, "bonus": 0},
                    },
                    "other": {"tactics": {"base": 10, "bonus": 0}},
                },
                "health": {"current": 700, "max": 700, "effects": []},
                "inventory": {
                    "pockets": [{
                        "name": "Граната РГД-5",
                        "category": "grenade",
                        "quantity": 2,
                        "attributes": {},
                    }],
                },
                "weapons": [],
            },
        )
        db.session.add(character)
        db.session.flush()
        placed = LocationCharacter(
            location_id=location.id,
            character_id=character.id,
            controlled_by=user.id,
            pos_x=5,
            pos_y=5,
            facing_x=1,
            facing_y=0,
            action_points_current=5,
        )
        db.session.add(placed)
        db.session.flush()
        db.session.add(LocationCombatState(
            location_id=location.id,
            status="active",
            current_location_character_id=placed.id,
            turn_order=[placed.id],
            round_number=1,
        ))
        db.session.commit()
        monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)
        monkeypatch.setattr(
            CombatService,
            "resolve_explosion",
            lambda *args, **kwargs: {"targets": [], "objects": []},
        )

        result = CombatService.perform_action(
            location.id,
            user.id,
            placed.id,
            "explosive_attack",
            target_x=8,
            target_y=5,
            item_path=["inventory", "pockets", 0],
            explosive_source="hand",
        )

        assert result["explosive"]["success"] is True
        assert result["explosive"]["impact"] == {"x": 8, "y": 5}
        assert result["explosive"]["detonated"] is False
        assert result["explosive"]["pending"]["trigger"] == "actor_turn_start"
        assert placed.action_points_current == 2
        assert character.data["inventory"]["pockets"][0]["quantity"] == 1
