"""Воспроизводимые исходы замков исключительно на собственной тестовой БД."""
def install(app,db,check_database,run_id):
    from catalog_picker_fixture import install as catalog
    catalog(app,db,check_database,run_id)
    from types import SimpleNamespace
    from app.services import object_locks
    values=iter([1,1,20,1,1,20])
    def roll(low,high):
        check_database();value=next(values)
        assert low<=value<=high
        return value
    object_locks.random=SimpleNamespace(randint=roll)
