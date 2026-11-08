"""Регрессии общих правил бросков из карточек ручной приёмки."""
from copy import deepcopy

from app.extensions import db
from app.services import combat as combat_module
from app.services.combat import CombatService
from test_quick_skill_checks import checks, run


def test_advantage_and_disadvantage_cancel_to_one_die(monkeypatch):
    draws = []

    def fixed_roll(low, high):
        draws.append((low, high))
        return 13

    monkeypatch.setattr(combat_module.random, "randint", fixed_roll)
    character_data = {
        "skills": {"physical": {"shooting": {"base": 10, "bonus": 0}}},
        "health": {"effects": []},
    }

    result = CombatService._narrative_skill_check(
        character_data,
        "skills.physical.shooting",
        advantage=True,
        forced_disadvantage=True,
    )

    assert draws == [(1, 20)]
    assert result["rolls"] == [13]
    assert result["roll"] == 13


def test_skill_check_natural_twenty_does_not_beat_unmet_difficulty(
    client, checks, auth_headers, monkeypatch,
):
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 20)

    response = run(
        client,
        checks,
        auth_headers,
        operation_id="manual-qa-natural-twenty-skill",
        difficulty=40,
    )

    assert response.status_code == 200, response.json
    check = response.json["check"]
    assert check["roll"] == 20
    assert check["total"] < check["difficulty"]
    assert check["success"] is False


def test_skill_check_natural_one_can_succeed_when_total_meets_difficulty(
    client, checks, auth_headers, monkeypatch,
):
    monkeypatch.setattr("app.services.combat.random.randint", lambda *_: 1)
    data = deepcopy(checks['actor_character'].data)
    data['skills']['physical']['strength'] = {'base': 20}
    data['health'].update(current=700, max=700, effects=[], painLevel=0, exhaustion=0)
    checks['actor_character'].data = data
    db.session.commit()

    response = run(
        client,
        checks,
        auth_headers,
        operation_id="manual-qa-natural-one-skill",
        skill_path='skills.physical.strength',
        difficulty=1,
    )

    assert response.status_code == 200, response.json
    check = response.json["check"]
    assert check["roll"] == 1
    assert check["total"] >= check["difficulty"]
    assert check["success"] is True
