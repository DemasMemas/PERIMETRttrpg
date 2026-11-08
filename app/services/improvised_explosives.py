"""Кустарная бомба: таблица книги и хрупкий корпус, без отдельного расчёта взрыва."""
from copy import deepcopy
from uuid import uuid4
from sqlalchemy import event
from sqlalchemy.orm import Session


@event.listens_for(Session,'after_rollback')
@event.listens_for(Session,'after_commit')
def _clear_cleanup(session):
    session.info.pop('fragile_ground_cleanup',None)
    session.info.pop('fragile_resolution_depth',None)

PROFILE={'label':'Кустарная бомба','fragment':800,'radius':4,'penetration':50,
    'blast_base':800,'blast_falloff':100,'projectile_range':None,'fuse':'contact',
    'contact_chance':50,'noise':20,'radial_first_distance':0,
    'radial_damage':{'fragments':[800,760,722,685.9,651.605],
        'penetration':[50,45,40,35,30],'blast':[800,700,600,500,400]}}


def templates():
    from app.services.catalog_import import template
    return [template('Кустарная бомба','grenade',0,.5,1,
        'При контакте с поверхностью взрывается с шансом 50%. Иначе остаётся целой: можно подобрать. '
        'Выстрел в корпус или воздействие другого взрыва вызывает подрыв. Радиус 4 м, в центре 800 осколочного урона и 800 урона волны. Шум 20.',
        'Ручные',{'not_for_sale':True,'craft_only':True,'source_recipe_id':'recipe-v20','fragile_explosive':True})]


def fragile_item(item):
    from app.services.combat import CombatService
    return isinstance(item,dict) and type(item.get('quantity',1)) is int and item.get('quantity',1)>0 and CombatService._explosive_key(item.get('name'))=='improvised_bomb'


def fragile_ground(obj):
    props=getattr(obj,'properties',None) or {}
    return bool(obj and (getattr(obj,'type',None)=='ground_item' or props.get('is_ground_item'))
        and any(fragile_item(item) for item in props.get('contents') or []))


def ground(location_id,x,y,level,item):
    from app.extensions import db
    from app.models import LocationObject
    saved=deepcopy(item)
    if saved.get('quantity',1)>1:saved['id']=str(uuid4())
    saved.setdefault('id',str(uuid4()));saved['quantity']=1
    obj=LocationObject(location_id=location_id,name='Кустарная бомба',type='ground_item',tile_x=x,tile_y=y,
        properties={'contents':[saved],'is_ground_item':True,'passable':True,'level':level,'floor_level':level,'interactions':['open_container']})
    db.session.add(obj);db.session.flush();return obj


def destroy(obj,source_character_id=None):
    """Сначала изъять заряд, затем дать общему движку разрешить последствия ровно один раз."""
    from app.extensions import db
    from sqlalchemy.orm.attributes import flag_modified
    from app.services.combat import CombatService
    from app.services.location_navigation import level_of
    props=deepcopy(obj.properties or {});bombs=[item for item in props.get('contents') or [] if fragile_item(item)]
    if not bombs:return None
    props['contents']=[item for item in props.get('contents') or [] if not fragile_item(item)]
    obj.properties=props;flag_modified(obj,'properties')
    deleted=not props['contents']
    # Пустая строка резервирует ID до завершения цепочки: новый труп не должен
    # получить ID бомбы, который клиенты затем удалят из сцены.
    if deleted:db.session.info.setdefault('fragile_ground_cleanup',{})[obj.id]=obj
    detonations=[]
    for item in bombs:
        profile=CombatService._explosive_profile(item)
        if source_character_id:profile['source_character_id']=source_character_id
        for _ in range(item.get('quantity',1)):
            detonations.append({'id':str(uuid4()),'item_name':item['name'],'location_id':obj.location_id,
                'profile':deepcopy(profile),'impact':{'x':obj.tile_x,'y':obj.tile_y},'floor_level':level_of(obj),
                'ground_object_id':obj.id,'ground_object_event':'deleted' if deleted else 'updated'})
    return {'destroyed':deleted,'class':'special','label':'Особое','hp':0,'max_hp':1,
        'base_physical_protection':0,'physical_protection':0,'mesh_hit_chance':100,
        'fragile_detonated':True,'secondary_explosions':detonations}


def pending(root):
    """Только непосредственные цепочки; вложенные взрывы обходятся собственным вызовом."""
    found=[];todo=[root]
    while todo:
        node=todo.pop()
        if isinstance(node,dict):
            for key,value in node.items():
                if key=='secondary_explosions':
                    if isinstance(value,list):found.extend(entry for entry in value if isinstance(entry,dict))
                else:todo.append(value)
        elif isinstance(node,list):todo.extend(node)
    return found


def resolve_pending(root,round_number=0):
    from app.services.combat import CombatService
    from app.extensions import db
    result=pending(root)
    db.session.info['fragile_resolution_depth']=db.session.info.get('fragile_resolution_depth',0)+1
    try:
        for node in result:
            if 'explosion' in node:continue
            node['explosion']=CombatService.resolve_explosion(node['location_id'],node['impact']['x'],node['impact']['y'],
                node['profile'],round_number=round_number,floor_level=node['floor_level'])
            node['detonated']=not node['explosion'].get('awaiting_gm',False)
    finally:
        db.session.info['fragile_resolution_depth']-=1
        if not db.session.info['fragile_resolution_depth']:
            for obj in db.session.info.pop('fragile_ground_cleanup',{}).values():
                if not (obj.properties or {}).get('contents'):db.session.delete(obj)
    return result
