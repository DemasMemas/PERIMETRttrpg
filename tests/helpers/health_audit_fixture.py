"""Управляемые броски опьянения только внутри собственной тестовой БД."""
from types import SimpleNamespace


def install(app, db, check_database, run_id):
    check_database()
    assert app.testing and not app.debug
    from flask import request
    from app.services import effects, medical_procedure
    from app.services.consumable_effects import parse_consumable_effects
    original = effects.random
    draws = []
    armed = []

    def randint(low, high):
        import sys
        if sys._getframe(1).f_code.co_name != '_intoxication_check':
            return original.randint(low, high)
        check_database()
        value = armed.pop(0) if armed else 100
        assert low <= value <= high
        draws.append(value)
        return value

    effects.random = SimpleNamespace(randint=randint)
    # Медицинские процедуры в этом отдельном сценарии проходят успешно.
    medical_procedure.random = SimpleNamespace(randint=lambda low, high: high)
    from app.services import combat
    combat_random = combat.random
    def attack_randint(low, high):
        import sys
        if sys._getframe(1).f_code.co_name in {'_resolve_attack','_resolve_cover_attack'} and (low, high) == (1, 20):
            return 15
        return combat_random.randint(low, high)
    combat.random = SimpleNamespace(randint=attack_randint)

    @app.route(f'/__health_audit/{run_id}', methods=['GET', 'POST'])
    def fixture_control():
        check_database()
        if request.method == 'POST':
            values = (request.get_json() or {}).get('rolls', [])
            assert isinstance(values, list) and all(type(v) is int and 1 <= v <= 100 for v in values)
            armed[:] = values
        descriptions = ['Препарат "Радист". Ампула.', 'Йод-плюс. Ампула. -2.5 радиации',
                        'Стимулятор Сова-Н. Действует 8 часов. +1 истощения', 'Стимулятор Воля. Ампула.']
        return {'run_id': run_id, 'draws': draws, 'pending': len(armed),
                'catalog_profiles': [{'name': text.split('.')[0], 'description': text,
                                      'profile': parse_consumable_effects(text)} for text in descriptions]}
