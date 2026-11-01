"""Fixed medical browser controls, installed only inside an owned test DB scope."""
from copy import deepcopy
import random
import sys
from threading import Lock
from uuid import UUID


def install(app, db, check_database, run_id):
    assert str(UUID(run_id)) == run_id
    assert app.testing and not app.debug
    check_database()
    import app.services.deferred_consumable as deferred
    import app.services.combat as combat
    from app.models import ConsumableUse, DeferredCombatAction, LobbyCharacter, LocationCharacter
    from app.services.character_interaction import CharacterInteractionService
    from app.services.consumable_effects import parse_consumable_effects
    draws = []
    mutex = Lock()
    expected = [('app.services.deferred_consumable', 'prepare_medicine_roll', 1),
                ('app.services.combat', '_narrative_skill_check', 20)]

    class ControlledRandom:
        def __getattr__(self, name):
            return getattr(random, name)

        def randint(self, low, high):
            caller = sys._getframe(1)
            if caller.f_code.co_name == '<listcomp>':
                caller = caller.f_back
            site = (caller.f_globals.get('__name__'), caller.f_code.co_name)
            if site not in [entry[:2] for entry in expected]:
                return random.randint(low, high)
            check_database()
            with mutex:
                assert len(draws) < len(expected), 'Unexpected repeated medical RNG'
                module, function, value = expected[len(draws)]
                assert site == (module, function) and (low, high) == (1, 20)
                draws.append(dict(module=module, function=function, low=low, high=high, value=value))
                return value

    deferred.random = ControlledRandom()
    combat.random = ControlledRandom()

    @app.get('/__testing_medical_evidence/' + run_id)
    def evidence():
        check_database()
        people = LobbyCharacter.query.filter(LobbyCharacter.name.in_(['MR01 Врач', 'MR01 Пациент'])).all()
        characters = []
        for person in people:
            placed = LocationCharacter.query.filter_by(character_id=person.id).first()
            characters.append(dict(id=person.id, name=person.name, revision=person.revision,
                data=deepcopy(person.data),
                movement_locked=CharacterInteractionService.movement_locked(placed.id) if placed else False))
        row = db.session.get(DeferredCombatAction, 'medical-retry-browser-01')
        return dict(run_id=run_id, testing=app.testing, draws=deepcopy(draws), characters=characters,
            receipt_count=ConsumableUse.query.count(),
            deferred=None if row is None else dict(id=row.id, status=row.status,
                remaining=row.remaining_action_points, payload=deepcopy(row.payload)),
            profile=parse_consumable_effects('Тестовая ампула. Ампула. +5 ХП.'))
