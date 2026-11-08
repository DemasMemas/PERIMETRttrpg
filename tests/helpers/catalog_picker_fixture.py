"""Каталог и старые дубли из замечаний автора — только в собственной тестовой БД."""
from copy import deepcopy
import re
from uuid import UUID


def install(app,db,check_database,run_id,*,controlled_pepper=False,belt_artifact=False,controlled_artifacts=False):
    assert str(UUID(run_id))==run_id and app.testing and not app.debug
    check_database()
    from app.models.templates import ItemTemplate
    from app.services.equipment_catalog import load_catalog
    assert ItemTemplate.query.count()==0
    rows=load_catalog();grenade_index=0;legacy=[]
    for index,row in enumerate(rows):
        data=deepcopy(row);data['id']=5000+index
        if row['category']=='grenade':
            data['id']=1090+grenade_index
            short=re.sub(r'^(?:(?:подствольная|светошумовая|зажигательная|термобарическая|химическая|дымовая)\s+)?граната\s+','',row['name'],flags=re.I)
            legacy.append(ItemTemplate(id=156+grenade_index,name=short,category='grenade',price=1,attributes={}))
            grenade_index+=1
        db.session.add(ItemTemplate(**data))
    legacy.extend(ItemTemplate(id=9500+i,**row) for i,row in enumerate([
        {'name':'Вещевой подсумок — 1','category':'pouch','price':1,'attributes':{'capacity':8}},
        {'name':'Вещевой подсумок (старый)','category':'pouch','price':1,'attributes':{'capacity':8}},
        {'name':'Перчатки Медицинские','category':'gloves','price':1,'attributes':{}},
        {'name':'ДА-2','category':'device','price':1,'attributes':{}},
        {'name':'Налобный фонарик','category':'helmet_module','price':1,'attributes':{}},
        {'name':'Очки ПНВ-1п','category':'helmet_module','price':1,'attributes':{}},
        {'name':'Спортивные','category':'gloves','price':1,'attributes':{}},
        {'name':'Барьер','category':'container','price':1,'attributes':{'artifact_container_kind':'storage'}},
        {'name':'Контейнер для ношения Барьер','category':'container','price':1,'attributes':{}},
    ]))
    if belt_artifact:
        legacy.append(ItemTemplate(id=9600,name='Контрольный артефакт крепления',category='artifact',weight=2,volume=2,
            attributes={'positive_effect':'Защита от физического урона +15%',
                        'negative_effect':'Штраф перемещения +2 3 Радиации за перемещение'}))
    db.session.add_all(legacy);db.session.commit()
    # Для завершающего попадания в забрало: успешный к20 и известная лицевая зона.
    from types import SimpleNamespace
    from app.services import combat
    original=combat.random
    def roll(low,high):
        check_database()
        if (low,high)==(1,20):return 4
        if controlled_pepper and (low,high)==(1,100):return 99
        return original.randint(low,high)
    combat.random=SimpleNamespace(randint=roll,choice=original.choice,uniform=original.uniform,
        random=original.random,shuffle=original.shuffle)
    if controlled_artifacts:
        from app.services import artifact_effects
        from flask import request,jsonify
        from sqlalchemy.orm.attributes import flag_modified
        from app.models import LobbyCharacter,LocationCharacter

        @app.post('/__testing/artifact-preparation/'+run_id)
        def prepare_artifact_wearing():
            check_database()
            from app.services.artifact_special import advance,DAY
            character=db.session.get(LobbyCharacter,int(request.get_json()['character_id']))
            assert character and character.name=='Проверка особого свойства'
            data=deepcopy(character.data)
            if request.get_json().get('breathlessness'):
                model=LocationCharacter.query.filter_by(character_id=character.id).one()
                model.strenuous_movement_blocked_until_round=1000
                model.movement_mode_this_turn='walk'
            else:
                # Только подготовка собственного опыта: оставшиеся6с пройдут настоящим боевым раундом.
                advance(data,DAY-6)
            character.data=data;flag_modified(character,'data');db.session.commit()
            return jsonify(character.data_snapshot())

        def artifact_roll(low,high):
            check_database()
            assert (low,high)==(1,100)
            return 50
        artifact_effects.random=SimpleNamespace(randint=artifact_roll)
