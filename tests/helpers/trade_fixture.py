"""Настоящий каталог в собственной браузерной БД; без доступа к рабочей игре."""


def install(app, db, check_database, run_id):
    check_database()
    assert app.testing and not app.debug
    from app.services.equipment_catalog import load_catalog
    from app.services.equipment_importer import insert_missing_templates
    insert_missing_templates(load_catalog(), db.session)
    db.session.commit()
