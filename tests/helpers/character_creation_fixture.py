"""Фиксированный стартовый каталог только в одноразовой браузерной БД."""
from app.models.templates import ItemTemplate


def install(app, db, check_database, run_id):
    check_database()
    templates = [
        ItemTemplate(name='Пистолет для стартового закупа',category='weapon',subcategory='Пистолеты',item_class='1',price=6000,weight=1,volume=2,attributes={'caliber':'9x18','damage':30,'max_durability':100}),
        ItemTemplate(name='Паёк для стартового закупа',category='consumable',subcategory='Еда',item_class='1',price=100,weight=.2,volume=.2,attributes={'section':'Еда','uses':1}),
        ItemTemplate(name='9x18 базовый',category='ammo',item_class='2',price=2,volume=.02,attributes={'caliber':'9x18','ammo_variant':'base','damage':30}),
        ItemTemplate(name='Рюкзак для стартового закупа',category='backpack',price=500,weight=1,volume=3,attributes={'capacity':30}),
        ItemTemplate(name='9x18 запрещённый БП',category='ammo',price=2,attributes={'caliber':'9x18','ammo_variant':'bp'}),
        ItemTemplate(name='Недоступная броня 2',category='armor',item_class='2',price=1,attributes={}),
    ]
    db.session.add_all(templates);db.session.commit()
    ids={template.category:template.id for template in templates[:4]}

    @app.get(f'/__creation_fixture/{run_id}')
    def creation_fixture():
        check_database()
        return {'run_id':run_id,'templates':ids}

    @app.post(f'/__history_fixture/{run_id}')
    def history_fixture():
        check_database()
        from app.services import character_creation
        original=character_creation.generate_zone_history
        class ScriptedRng:
            def __init__(self):self.values=iter([3,8,1,10,2,2,11,2])
            def randint(self,low,high):
                value=next(self.values)
                assert low<=value<=high
                return value
        def once(data):
            character_creation.generate_zone_history=original
            return original(data,rng=ScriptedRng())
        character_creation.generate_zone_history=once
        return {'armed':True}
