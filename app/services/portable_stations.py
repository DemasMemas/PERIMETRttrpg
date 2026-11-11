"""Созданные станки: экземпляр инвентаря, размещение и перемещение без потери состояния."""
from copy import deepcopy
import hashlib
import json
import math
import random
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import Location, LocationCharacter, LocationCombatState, LocationObject
from app.services.character import CharacterService
from app.services.exceptions import ValidationError, ConflictError, NotFoundError, PermissionDenied
from app.services.transaction import atomic_operation


PROFILES = {
    'recipe-m34': ('Аэрационная установка', 'station_aeration', None, (.9,.8,2)),
    'recipe-m40': ('Кустарный самогонный аппарат', 'station_distillery', 'Самогонный Аппарат', (1.6,.7,.9)),
    'recipe-m41': ('Кустарная печь', 'station_oven', 'Печь', (.65,.65,1)),
    'recipe-m42': ('Кустарный станок оружейника', 'station_gunsmith', 'Станок Оружейника', (1.6,.7,.9)),
    'recipe-m43': ('Кустарный станок бронника', 'station_armorer', 'Станок Бронника', (1.6,.7,.9)),
    'recipe-m44': ('Кустарный смесительный станок', 'station_mixer', 'Смесительный Станок', (1.6,.7,.9)),
    'recipe-m45': ('Кустарный станок электрика', 'station_electrician', 'Станок Электрика', (1.6,.7,.9)),
}

TOOL_WORDS = {
    'recipe-m40':'паяльные инструменты', 'recipe-m42':'инструментов оружейника',
    'recipe-m43':'инструментов бронника', 'recipe-m44':'смесительные инструменты',
    'recipe-m45':'паяльные инструменты',
}


def ready(obj):
    from app.services.general_consumable import _available_uses
    props=obj.properties or {};key=props.get('portable_station')
    return key in {'recipe-m34','recipe-m41'} or props.get('standard_distillery') is True or any(_available_uses(item)>0 for item in props.get('station_tools') or [])


def nearby(character, *, placement=None):
    from app.services.location_navigation import level_of,pose
    actor=placement or LocationCharacter.query.filter_by(character_id=character.id).order_by(
        LocationCharacter.last_action.desc().nullslast(),LocationCharacter.id.desc()).first()
    if not actor:return []
    return [obj for obj in LocationObject.query.filter_by(location_id=actor.location_id).all()
        if not (obj.properties or {}).get('gm_hidden') and level_of(obj)==(actor.floor_level or 0)
        and math.hypot(pose(obj)[0]-actor.pos_x-.5,pose(obj)[1]-actor.pos_y-.5)<=1.5]


def selected(character,recipe,option=0,*,preferred_id=None):
    from app.services.crafting import normalized
    from app.services.distillery import RECIPES,is_distillery,occupied
    if recipe['id'] in RECIPES:
        from app.services.item_controllers import _context
        placement=_context(character)
        if not placement:return None
        apparatus=[]
        for obj in nearby(character,placement=placement):
            if not is_distillery(obj) or (preferred_id is not None and obj.id!=preferred_id):continue
            busy=occupied(character.lobby_id,obj.id)
            if busy or not (obj.properties or {}).get('portable_station') or ready(obj):
                apparatus.append((busy,obj.id,obj))
        return min(apparatus)[2] if apparatus else None
    raw=recipe.get('tools_text') or ''
    if normalized(raw)=='обязателен' and recipe['group']=='Электроника':raw='Станок Электрика'
    if normalized(raw) in {'да','нужна'} and recipe['group']=='Ловушки':raw='Печь'
    count=max(1,len(recipe.get('difficulty_options') or []))
    pieces=[p.strip() for p in raw.split(',')]
    wanted=[]
    for piece in pieces:
        choices=piece.split('/')
        chosen=choices[option] if len(choices)==count and count>1 else piece
        wanted.extend(normalized(v) for v in chosen.split('/'))
    candidates=[]
    for obj in nearby(character):
        props=obj.properties or {};key=props.get('portable_station')
        name=(PROFILES[key][2] if key in PROFILES else props.get('craft_workbench') or obj.name)
        if key=='recipe-m34':name='Установка для Аэрации воды'
        if normalized(name) not in wanted or (key in PROFILES and not ready(obj)):continue
        candidates.append(obj)
    return min(candidates,key=lambda obj:(bool((obj.properties or {}).get('portable_station')),obj.id),default=None)


def consume_for_job(obj, *, can_save_tool_charge=False):
    from app.services.general_consumable import _available_uses
    from app.services.distillery import is_distillery,occupied
    key=(obj.properties or {}).get('portable_station')
    if is_distillery(obj):
        # В SQLite SELECT FOR UPDATE не блокирует строку. Запись сериализует запуск партий.
        db.session.execute(db.update(LocationObject).where(LocationObject.id==obj.id).values(revision=LocationObject.revision))
        db.session.refresh(obj)
        if not is_distillery(obj) or occupied(obj.location.lobby_id,obj.id):
            raise ConflictError('Самогонный аппарат уже занят или изменился')
        key=(obj.properties or {}).get('portable_station')
        if key not in PROFILES or (obj.properties or {}).get('standard_distillery') is True:
            return {'object_id':obj.id,'name':obj.name,'spoil_chance':0}
    if key not in PROFILES:return None
    usage = {'object_id': obj.id, 'name': obj.name, 'spoil_chance': 0 if key == 'recipe-m34' else 15}
    props=deepcopy(obj.properties or {})
    if key not in {'recipe-m34','recipe-m41'}:
        tool=next((item for item in props.get('station_tools') or [] if _available_uses(item)>0),None)
        if not tool:raise ConflictError('В станке закончились заряды инструмента')
        if can_save_tool_charge and random.randint(1, 100) <= 50:
            return {**usage, 'tool_use_saved': True}
        tool['uses']=_available_uses(tool)-1
        tool.setdefault('attributes',{})['uses_remaining']=tool['uses']
        obj.properties=props
    return usage


def templates():
    from app.services.catalog_import import template
    return [template(name,'tool',0,0,0,
        'Созданное устройство для размещения на подлокации. Не продаётся.', 'Кустарные устройства',
        {'portable_station':key,'station_asset':asset,'craft_only':True,'not_for_sale':True,
         'source_recipe_id':key}) for key,(name,asset,bench,size) in PROFILES.items()]


def _actor(lobby_id,location_id,user_id,character_id):
    from app.services.combat import CombatService
    actor=LocationCharacter.query.filter_by(location_id=location_id,character_id=character_id).first()
    if not actor or actor.character.lobby_id!=lobby_id:raise NotFoundError('Персонаж отсутствует на подлокации')
    CharacterService.check_access(actor.character,user_id,edit=True)
    CombatService.ensure_character_can_act(actor)
    state=LocationCombatState.query.filter_by(location_id=location_id).first()
    if state and state.status=='active':raise ValidationError('Размещать и переставлять станки можно вне боя')
    return actor


def _corners(box):
    c,s=math.cos(box.angle),math.sin(box.angle)
    return [(box.x+x*c+y*s,box.y-x*s+y*c) for x in (-box.width/2,box.width/2) for y in (-box.depth/2,box.depth/2)]


def _overlap(left,right):
    a,b=_corners(left),_corners(right)
    for box in (left,right):
        for x,y in ((math.cos(box.angle),-math.sin(box.angle)),(math.sin(box.angle),math.cos(box.angle))):
            ap=[px*x+py*y for px,py in a];bp=[px*x+py*y for px,py in b]
            if max(ap)<=min(bp)+1e-7 or max(bp)<=min(ap)+1e-7:return False
    return True


def _position(location,actor,profile,payload,exclude_id=None):
    from app.services.combat import CombatService
    from app.services.location_navigation import surface,level_of
    from app.services.movement_geometry import WallBox
    x,y,rotation=(payload.get(k) for k in ('x','y','rotation'))
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (x,y,rotation)):
        raise ValidationError('Выберите точку и угол размещения')
    if math.hypot(x-actor.pos_x,y-actor.pos_y)>1.5+1e-7:
        raise ValidationError('Место должно быть рядом с персонажем')
    level=actor.floor_level or 0
    requested_level=payload.get('floor_level',level)
    if type(requested_level) is not int or requested_level!=level:raise ValidationError('Размещать устройство можно только на своём этаже')
    rotation=rotation%(2*math.pi);w,d,h=profile[3]
    box=WallBox(x+.5,y+.5,w,d,rotation,level)
    objects=[o for o in LocationObject.query.filter_by(location_id=location.id).all() if o.id!=exclude_id]
    for wx,wy in _corners(box):
        if not (0<=wx<location.grid_width and 0<=wy<location.grid_height):raise ValidationError('Устройство выходит за границы карты')
        if surface(location,objects,wx-.5,wy-.5,level) is None:raise ValidationError('Под устройством нет опоры')
        tiles=location.tiles_data or []
        row=tiles[math.floor(wy)] if math.floor(wy)<len(tiles) else []
        tile=row[math.floor(wx)] if math.floor(wx)<len(row) else {}
        if tile.get('terrain')=='water':raise ValidationError('Нельзя размещать устройство в воде')
    geometry=CombatService._movement_wall_geometry(location,objects)
    obstacles=[*geometry.boxes,*[entry[0] for entry in geometry.climb_boxes]]
    if any(other.level==level and _overlap(box,other) for other in obstacles):
        raise ValidationError('Место занято объектом')
    for character in LocationCharacter.query.filter_by(location_id=location.id).all():
        if (character.floor_level or 0)==level and box.intersects_sweep(
                (character.pos_x+.5,character.pos_y+.5),(character.pos_x+.5,character.pos_y+.5)):
            raise ValidationError('Место занято персонажем')
    return math.floor(x),math.floor(y),{'offset_x':x-math.floor(x),'offset_z':y-math.floor(y),
        'rotation':rotation,'level':level}


@atomic_operation
def operate(lobby_id,location_id,user_id,payload):
    from app.services.crafting import entries
    from app.services.item_template_lookup import resolve_item_template
    from app.services.magazine import inventory_slot
    from app.services.location_navigation import level_of,pose
    if not isinstance(payload,dict):raise ValidationError('Неверное действие устройства')
    operation=payload.get('operation');operation_id=payload.get('operation_id')
    if operation not in {'place','move','load','unload'} or not isinstance(operation_id,str) or not 1<=len(operation_id)<=100:
        raise ValidationError('Неверное действие устройства')
    actor=_actor(lobby_id,location_id,user_id,payload.get('character_id'))
    character=actor.character;data=deepcopy(character.data or {})
    signature=hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k not in
        {'character_revision','object_revision'}},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    receipts=data.setdefault('crafting',{}).setdefault('station_receipts',[])
    previous=next((entry for entry in receipts if entry['id']==operation_id),None)
    if previous:
        if previous['signature']!=signature:raise ConflictError('Идентификатор действия уже использован')
        obj=db.session.get(LocationObject,previous['object_id'])
        if not obj or obj.location_id!=location_id:raise ConflictError('Размещённое устройство изменилось')
        return character,obj,False
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:
        raise ConflictError('Лист изменился. Обновите инвентарь')
    location=Location.query.filter_by(id=location_id,lobby_id=lobby_id).with_for_update().first()
    if not location:raise NotFoundError('Подлокация недоступна')
    # SQLite не поддерживает FOR UPDATE: запись той же величины сериализует размещения.
    db.session.execute(db.update(Location).where(Location.id==location.id).values(updated_at=Location.updated_at))
    if operation=='place':
        from app.services.crafting import normalized
        from app.services.distillery import STATION
        path=payload.get('item_path')
        item=next((item for current,item in entries(data) if current==path and item.get('id')==payload.get('item_id')),None)
        template=resolve_item_template(item,lobby_id)
        key=(template.attributes or {}).get('portable_station') if template else None
        legacy_distillery=bool(item and item.get('category')=='tool' and not key and
            normalized(template.name if template else item.get('name'))=='самогонный аппарат')
        if legacy_distillery:key=STATION
        if key not in PROFILES:raise ValidationError('Выберите созданный станок или установку из инвентаря')
        quantity=item.get('quantity',1)
        if type(quantity) is not int or quantity<1 or item.get('contents'):raise ValidationError('Нельзя разместить этот экземпляр')
        snapshot=deepcopy(item);snapshot['quantity']=1
        if quantity>1:snapshot['id']=str(uuid4())
        profile=PROFILES[key];tx,ty,position=_position(location,actor,profile,payload)
        parent,index=inventory_slot(data,path)
        if quantity==1:parent.pop(index)
        else:parent[index]['quantity']-=1
        obj=LocationObject(location_id=location_id,name=profile[0],type='station_aeration' if key=='recipe-m34' else 'household_sauna_stove' if key=='recipe-m41' else 'household_workbench',
            tile_x=tx,tile_y=ty,properties={**position,'portable_station':key,'station_item':snapshot,
                'visual_asset':profile[1],'dimensions':dict(zip(('width','depth','height'),profile[3])),
                'craft_workbench':profile[2],'aeration_unit':key=='recipe-m34','traversal_category':'blocked',
                'interactions':['move']})
        if legacy_distillery:
            obj.name=item['name']
            obj.properties['standard_distillery']=True
        db.session.add(obj)
    else:
        obj=LocationObject.query.filter_by(id=payload.get('object_id'),location_id=location_id).with_for_update().first()
        key=(obj.properties or {}).get('portable_station') if obj else None
        if key not in PROFILES or (obj.properties or {}).get('gm_hidden'):raise NotFoundError('Устройство недоступно')
        if type(payload.get('object_revision')) is not int or payload['object_revision']!=obj.revision:
            raise ConflictError('Устройство уже переместили')
        cx,cy,*_=pose(obj)
        if level_of(obj)!=(actor.floor_level or 0) or math.hypot(cx-actor.pos_x-.5,cy-actor.pos_y-.5)>1.5:
            raise ValidationError('Подойдите вплотную к устройству на том же этаже')
        if operation=='move':
            from app.services.distillery import STATION,occupied
            if key==STATION and occupied(lobby_id,obj.id):raise ConflictError('Сначала завершите или отмените работу в аппарате')
            tx,ty,position=_position(location,actor,PROFILES[key],payload,exclude_id=obj.id)
            obj.tile_x,obj.tile_y=tx,ty;obj.properties={**obj.properties,**position}
        else:
            if key not in TOOL_WORDS:raise ValidationError('Это устройство не требует заправки инструментами')
            props=deepcopy(obj.properties);tools=props.setdefault('station_tools',[])
            if operation=='load':
                from app.services.crafting import normalized
                path=payload.get('item_path')
                tool=next((item for current,item in entries(data) if current==path and item.get('id')==payload.get('item_id')),None)
                if not tool or tool.get('category')!='tool' or TOOL_WORDS[key] not in normalized(tool.get('name')):
                    raise ValidationError('Выберите подходящий инструмент для этого станка')
                if tool.get('quantity',1)!=1:raise ValidationError('Сначала разделите стопку инструментов')
                tool.setdefault('id',str(uuid4()))
                if any(item.get('id')==tool.get('id') for item in tools):raise ConflictError('Инструмент уже установлен')
                tools.append(deepcopy(tool));parent,index=inventory_slot(data,path);parent.pop(index)
            else:
                tool=next((item for item in tools if item.get('id')==payload.get('item_id')),None)
                if not tool:raise ConflictError('Инструмент уже извлечён')
                tools.remove(tool);data.setdefault('inventory',{}).setdefault('pockets',[]).append(tool)
            obj.properties=props
    db.session.flush()
    receipts.append({'id':operation_id,'signature':signature,'object_id':obj.id})
    data['crafting']['station_receipts']=receipts[-50:]
    character.data=data;flag_modified(character,'data')
    return character,obj,operation=='place'
