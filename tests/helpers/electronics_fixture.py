"""Фиксированный к100 установки модификации только в собственной браузерной БД."""
from uuid import UUID


def install(app, db, check_database, run_id):
    assert str(UUID(run_id)) == run_id
    assert app.testing and not app.debug
    check_database()
    from app.services import crafting
    original=crafting.resolve_creation

    def resolve(quote,**kwargs):
        check_database()
        return original(quote,roll=100)

    crafting.resolve_creation=resolve
    from types import SimpleNamespace
    from app.services import pda_access
    def percent(low,high):
        check_database()
        assert (low,high)==(1,100)
        return 1
    pda_access.random=SimpleNamespace(randint=percent)
