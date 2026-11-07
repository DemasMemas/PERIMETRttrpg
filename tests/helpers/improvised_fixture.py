"""Только собственная браузерная БД: успешный крафт и воспроизводимый отказ контактного запала."""
from uuid import UUID
from types import SimpleNamespace


def install(app,db,check_database,run_id):
    assert str(UUID(run_id))==run_id and app.testing and not app.debug
    check_database()
    from app.models.templates import ItemTemplate
    from app.services.equipment_catalog import load_catalog
    assert ItemTemplate.query.count()==0
    names={'Кустарная бомба','Металлические детали','Химические материалы','Вода'}
    rows=[row for row in load_catalog() if row['name'] in names]
    assert len(rows)==4
    db.session.add_all(ItemTemplate(**row) for row in rows);db.session.commit()
    from app.services import crafting,combat
    resolve=crafting.resolve_creation;original=combat.random
    def finish(quote,**kwargs):
        check_database();return resolve(quote,roll=100)
    def randint(low,high):
        check_database()
        return 20 if (low,high)==(1,20) else 51 if (low,high)==(1,100) else original.randint(low,high)
    crafting.resolve_creation=finish
    combat.random=SimpleNamespace(randint=randint,random=original.random,uniform=original.uniform,
        choice=original.choice,shuffle=original.shuffle)
