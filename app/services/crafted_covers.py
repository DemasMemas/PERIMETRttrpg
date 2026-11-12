"""Укрытия из рецептов: размещение, перенос и снятие с сохранением повреждений."""
from copy import deepcopy
import hashlib
import json
import math
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LocationCharacter,LocationCombatState,LocationObject
from app.services.exceptions import ValidationError,ConflictError,NotFoundError
from app.services.transaction import atomic_operation

# Размеры: ширина, толщина, высота. В ответах автора высота была второй.
PROFILES={
    'recipe-m18':dict(name='Занавеска',weight=1,volume=10,hp=200,protection=0,size=(1,.03,1),model='curtain_panel',recover=True),
    'recipe-m19':dict(name='Деревянная баррикада',weight=10,volume=20,hp=1000,protection=5,size=(1,.25,1),model='barricade_wood',recover=True),
    'recipe-m20':dict(name='Металлический лист',weight=20,volume=20,hp=5000,protection=40,size=(1,.25,1),model='barricade_metal',recover=True),
    'recipe-m21':dict(name='Прочная металлическая баррикада',weight=40,volume=20,hp=8500,protection=60,size=(1.5,.5,1.5),model='barricade_reinforced',recover=True),
    'recipe-m22':dict(name='Мешок с песком',weight=20,volume=20,hp=2500,protection=40,size=(1,.25,1),model='sandbag_wall',recover=True),
    'recipe-m23':dict(name='Кирпичная баррикада',weight=60,volume=20,hp=500,protection=40,size=(1,.25,1.5),model='barricade_brick',recover=False),
}


def templates():
    from app.services.catalog_import import template
    return [template(p['name'],'misc',0,p['weight'],p['volume'],
        f"Укрытие для установки на подлокации. ОЗ: {p['hp']}. Физическая защита: {p['protection']}%. Не продаётся.",
        'Укрытия',{'crafted_cover':key,'placement_asset':'crafted_cover_'+key.removeprefix('recipe-'),
            'craft_only':True,'not_for_sale':True,'source_recipe_id':key}) for key,p in PROFILES.items()]


def preserve(current,incoming):
    from app.services.portable_lights import walk
    previous={item.get('id'):item for _,item in walk(current or {},True) if item.get('id')}
    for _,item in walk(incoming,True):
        old=previous.get(item.get('id')) or {}
        if 'coverState' in old:item['coverState']=deepcopy(old['coverState'])
        else:item.pop('coverState',None)


def _near(actor,obj):
    from app.services.location_navigation import level_of,pose
    x,y,*_=pose(obj)
    return level_of(obj)==(actor.floor_level or 0) and math.hypot(x-actor.pos_x-.5,y-actor.pos_y-.5)<=1.5+1e-7


def _curtain_position(actor,payload,exclude_id=None):
    from app.services.location_navigation import pose
    if type(payload.get('anchor_id')) is not int:raise ValidationError('Выберите окно для занавески')
    anchor=LocationObject.query.filter_by(id=payload.get('anchor_id'),location_id=actor.location_id,type='window').first()
    if not anchor or not _near(actor,anchor) or (anchor.properties or {}).get('gm_hidden'):
        raise ValidationError('Выберите окно или оконный проём рядом на своём этаже')
    peers=LocationObject.query.filter_by(location_id=actor.location_id).all()
    if any(o.id!=exclude_id and (o.properties or {}).get('cover_anchor_id')==anchor.id for o in peers):
        raise ValidationError('На этом окне уже установлена занавеска')
    x,y,w,d,angle=pose(anchor);props=anchor.properties or {};dims=props.get('dimensions') or {}
    height=max(.1,float(dims.get('height') or 1));nx,ny=math.sin(angle),math.cos(angle)
    side=1 if (actor.pos_x+.5-x)*nx+(actor.pos_y+.5-y)*ny>=0 else -1
    x+=nx*side*(d/2+.04);y+=ny*side*(d/2+.04)
    tx,ty=math.floor(x-.5),math.floor(y-.5)
    return tx,ty,{'offset_x':x-tx-.5,'offset_z':y-ty-.5,'rotation':angle,
        'level':actor.floor_level or 0,'elevation':props.get('elevation',1),
        'dimensions':{'width':w,'depth':.03,'height':height},'cover_anchor_id':anchor.id}


def plan(actor,data,payload):
    from app.services.crafting import entries
    from app.services.item_template_lookup import resolve_item_template
    from app.services.portable_stations import _position
    if not isinstance(payload,dict):raise ValidationError('Выберите действие укрытия')
    operation=payload.get('operation')
    if operation not in {'place','move','recover'}:raise ValidationError('Неизвестное действие укрытия')
    obj=None;item=None
    if operation=='place':
        item=next((i for path,i in entries(data) if path==payload.get('item_path') and i.get('id')==payload.get('item_id')),None)
        template=resolve_item_template(item,actor.character.lobby_id) if item else None
        key=(template.attributes or {}).get('crafted_cover') if template else None
        if key not in PROFILES:raise ValidationError('Выберите укрытие из инвентаря')
        if type(item.get('quantity',1)) is not int or item.get('quantity',1)<1 or item.get('contents'):
            raise ValidationError('Нельзя разместить этот экземпляр укрытия')
        if int((item.get('coverState') or {}).get('hp',1))<=0:raise ValidationError('Разрушенное укрытие нельзя установить заново')
    else:
        if type(payload.get('object_id')) is not int:raise ValidationError('Выберите укрытие')
        obj=LocationObject.query.filter_by(id=payload.get('object_id'),location_id=actor.location_id).with_for_update().first()
        key=(obj.properties or {}).get('crafted_cover') if obj else None
        if key not in PROFILES or (obj.properties or {}).get('gm_hidden'):raise NotFoundError('Укрытие недоступно')
        if not isinstance((obj.properties or {}).get('placed_item'),dict):raise ValidationError('Это укрытие не размещено из предмета')
        if type(payload.get('object_revision')) is not int or payload['object_revision']!=obj.revision:
            raise ConflictError('Укрытие изменилось. Выберите его заново')
        if not _near(actor,obj):raise ValidationError('Подойдите к укрытию на том же этаже')
        if not PROFILES[key]['recover']:raise ValidationError('Кирпичную баррикаду нельзя перемещать или снимать')
    profile=PROFILES[key];position=None
    if operation!='recover':
        if key=='recipe-m18':position=_curtain_position(actor,payload,obj.id if obj else None)
        else:position=_position(actor.location,actor,(profile['name'],'',None,profile['size']),payload,exclude_id=obj.id if obj else None)
    return {'operation':operation,'key':key,'profile':profile,'item':item,'object':obj,'position':position,
        'action_points':3 if operation=='move' else 5}


def apply(actor,data,prepared,payload):
    from app.services.combat import CombatService
    from app.services.magazine import inventory_slot
    from app.models import Location
    db.session.execute(db.update(Location).where(Location.id==actor.location_id).values(updated_at=Location.updated_at))
    prepared=plan(actor,data,payload)
    operation,key,profile=prepared['operation'],prepared['key'],prepared['profile'];obj=prepared['object']
    if operation=='recover':
        item=deepcopy(obj.properties['placed_item']);state=CombatService._cover_profile(obj)
        item['coverState']={k:state[k] for k in ('hp','max_hp','base_physical_protection','physical_protection')}
        data.setdefault('inventory',{}).setdefault('backpack',[]).append(item)
        LocationCharacter.query.filter_by(cover_object_id=obj.id).update({'cover_object_id':None,'weapon_braced':False})
        db.session.delete(obj)
    else:
        tx,ty,position=prepared['position']
        if operation=='place':
            source=prepared['item'];item=deepcopy(source);item['quantity']=1
            item.setdefault('id',str(uuid4()))
            if source.get('quantity',1)>1:item['id']=str(uuid4())
            saved=item.get('coverState') or {};maximum=max(1,int(saved.get('max_hp',profile['hp'])))
            hp=max(0,min(maximum,int(saved.get('hp',maximum))))
            if hp==0:raise ValidationError('Разрушенное укрытие нельзя установить заново')
            protection=max(0,min(100,int(saved.get('base_physical_protection',profile['protection']))))
            properties={'crafted_cover':key,'placed_item':item,'visual_asset':'crafted_cover_'+key.removeprefix('recipe-'),
                'dimensions':dict(zip(('width','depth','height'),profile['size'])),
                'cover_max_hp':maximum,'cover_hp':hp,'cover_base_physical_protection':protection,
                'cover_physical_protection':min(protection,int(saved.get('physical_protection',round(protection*hp/maximum)))),
                'cover_class':'medium','blocks_movement':key!='recipe-m18','traversal_category':'passable' if key=='recipe-m18' else 'climbable',
                'interactions':['move_crafted_cover','recover_crafted_cover'] if profile['recover'] else [],**position}
            parent,index=inventory_slot(data,payload['item_path'])
            if source.get('quantity',1)==1:parent.pop(index)
            else:parent[index]['quantity']-=1
            obj=LocationObject(location_id=actor.location_id,name=profile['name'],type='cover',tile_x=tx,tile_y=ty,properties=properties)
            db.session.add(obj)
        else:obj.tile_x,obj.tile_y=tx,ty;obj.properties={**obj.properties,**position}
    db.session.flush()
    return obj,operation=='place',operation=='recover'


@atomic_operation
def operate(lobby_id,location_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    from app.models import Location
    if not isinstance(payload,dict):raise ValidationError('Выберите действие укрытия')
    actor=LocationCharacter.query.filter_by(location_id=location_id,character_id=payload.get('character_id')).first()
    if not actor or actor.character.lobby_id!=lobby_id:raise NotFoundError('Персонаж отсутствует на подлокации')
    CharacterService.check_access(actor.character,user_id,edit=True);CombatService.ensure_character_can_act(actor)
    battle=LocationCombatState.query.filter_by(location_id=location_id).first()
    if battle and battle.status=='active':raise ValidationError('В бою установите укрытие через боевое действие')
    character=actor.character;data=deepcopy(character.data or {});identity=payload.get('operation_id')
    if not isinstance(identity,str) or not 1<=len(identity)<=100:raise ValidationError('Нужен идентификатор действия')
    signature=hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k not in {'character_revision','object_revision'}},sort_keys=True).encode()).hexdigest()
    receipts=data.setdefault('crafting',{}).setdefault('cover_receipts',[])
    prior=next((r for r in receipts if r['id']==identity),None)
    if prior:
        if prior['signature']!=signature:raise ConflictError('Идентификатор действия уже использован')
        if prior['location_id']!=location_id:raise ConflictError('Действие относится к другой подлокации')
        if prior.get('removed'):return character,LocationObject(**prior['object']),False,True
        obj=db.session.get(LocationObject,prior['object_id'])
        if not obj:raise ConflictError('Укрытие уже снято или разрушено')
        return character,obj,False,False
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:raise ConflictError('Лист изменился')
    db.session.execute(db.update(Location).where(Location.id==location_id).values(updated_at=Location.updated_at))
    prepared=plan(actor,data,payload)
    obj,created,removed=apply(actor,data,prepared,payload)
    receipt={'id':identity,'signature':signature,'object_id':obj.id,'location_id':location_id,'removed':removed}
    if removed:receipt['object']={k:deepcopy(getattr(obj,k)) for k in ('id','location_id','name','type','tile_x','tile_y','properties','revision')}
    receipts.append(receipt);data['crafting']['cover_receipts']=receipts[-50:]
    character.data=data;flag_modified(character,'data')
    return character,obj,created,removed
