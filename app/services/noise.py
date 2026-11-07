"""Общее воздействие шума и штрафы слуховой Внимательности."""
import math
import uuid
from collections import deque
from types import SimpleNamespace

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCharacter, LocationObject
from app.services.effects import normalize_effect_list, apply_effect_to_health
from app.services.location_navigation import contains, level_of, opening_at


def hearing_modifier(value):
    return -5 if value>=70 else -3 if value>=50 else -1 if value>20 else 0


def space(location, objects, x, y, level):
    """Компонента помещения по существующим полам, стенам и проёмам."""
    from app.services.combat import CombatService
    from app.services.explosion_geometry import height
    floors=[o for o in objects if o.type=='floor' and level_of(o)==level and contains(o,x+.5,y+.5)]
    if not floors:return {'kind':'open','coefficient':.5,'cells':set()}
    building=(floors[0].properties or {}).get('building_id')
    peers=[o for o in objects if (o.properties or {}).get('building_id')==building]
    floor_cells=set()
    for floor in (o for o in peers if o.type=='floor'):
        floor_cells.update((a,b,level_of(floor)) for a,b in CombatService._object_footprint_tiles(floor)
                          if contains(floor,a+.5,b+.5))
    physical=[o for o in peers if o.type in {'wall','door','window'}]
    # Целая комната остаётся ×2 при открытых дверях/окнах: решение автора.
    # Для определения её границ рассматриваем эти проёмы как закрытые.
    closed=[SimpleNamespace(id=o.id,type=o.type,tile_x=o.tile_x,tile_y=o.tile_y,
                            properties={**(o.properties or {}),'is_open':False}) for o in physical]
    geometry=CombatService._movement_wall_geometry(location,closed)
    ids={o.id for o in physical}
    boxes=[b for b in [*geometry.boxes,*(v[0] for v in geometry.climb_boxes)] if b.object_id in ids]
    from app.services.movement_geometry import WallGeometry
    spatial=WallGeometry(boxes)
    start=(math.floor(x+.5),math.floor(y+.5),level)
    seen={start};queue=deque([start]);exposed=False;bounded=False;boundary_ids=set()
    while queue:
        a,b,l=queue.popleft();plane=height(location,objects,a,b,l)+1.4
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            point=(a+dx,b+dy,l)
            if point in seen:continue
            nearby=set(spatial.buckets.get((a,b,l),()))|set(spatial.buckets.get(point,()))
            barriers=[boxes[i] for i in nearby if boxes[i].bottom<=plane<=boxes[i].top
                      and boxes[i].segment_entry((a+.5,b+.5),(point[0]+.5,point[1]+.5)) is not None]
            if barriers:
                boundary_ids.update(w.object_id for w in barriers)
                bounded=True;continue
            if point not in floor_cells:
                exposed=True;continue
            seen.add(point);queue.append(point)
        for other in (l-1,l+1):
            point=(a,b,other)
            if point in floor_cells and point not in seen and opening_at(objects,max(l,other),a+.5,b+.5):
                seen.add(point);queue.append(point)
    # Общая пометка повреждений здания не делает каждую его комнату разрушенной.
    damaged=any(o.type=='wall' and o.id in boundary_ids
                and (o.properties or {}).get('structural_damage',{}).get('version')==1 for o in physical)
    if not bounded and not any(o.type in {'roof','ceiling'} and contains(o,x+.5,y+.5) for o in peers):
        return {'kind':'open','coefficient':.5,'cells':set()}
    # При явно отключённой автоматической крыше проверяем реальные перекрытия.
    explicit_roof=any((o.properties or {}).get('roof_enabled') is False for o in floors)
    if explicit_roof:
        tops=[o for o in peers if o.type in {'roof','ceiling','floor'}]
        exposed |= any(not any((level_of(o)>l if o.type=='floor' else level_of(o)>=l)
                               and contains(o,a+.5,b+.5) for o in tops) for a,b,l in seen)
    kind='semi' if exposed or damaged else 'closed'
    return {'kind':kind,'coefficient':1 if kind=='semi' else 2,'cells':seen}


def recipients(location,objects,x,y,level,volume,people,*,context=None):
    from app.services.explosion_geometry import distance
    context=context or space(location,objects,x,y,level)
    for target in people:
        same_room=(math.floor(target.pos_x+.5),math.floor(target.pos_y+.5),target.floor_level or 0) in context['cells']
        if same_room or distance(location,objects,x,y,level,target)<=volume/10:
            yield target


def affected_ids(location,objects,event,people):
    from app.services.explosion_geometry import distance
    profile=event['profile'];x,y,level=event['x'],event['y'],event.get('floor_level',0)
    result={p.id for p in people if distance(location,objects,x,y,level,p)<=profile.get('radius',0)}
    if profile.get('noise',0)>0:
        result.update(p.id for p in recipients(location,objects,x,y,level,profile['noise'],people))
    return result


def apply(location,x,y,level,volume,*,count=1,target_ids=None,objects=None,context=None,people=None):
    from app.services.combat import CombatService
    volume=max(0,CombatService._coerce_float(volume,0))
    if volume<=0 or count<=0:return []
    if objects is None:objects=LocationObject.query.filter_by(location_id=location.id).all()
    context=context or space(location,objects,x,y,level)
    if people is None:people=CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=location.id).all())
    results=[]
    for target in recipients(location,objects,x,y,level,volume,people,context=context):
        if target_ids is not None and target.id not in target_ids:continue
        if not target.character or CombatService._character_condition(target.character.data or {})['state']=='dead':continue
        data=target.character.data
        headphones=(data.get('equipment') or {}).get('headphones')
        headphones=headphones if isinstance(headphones,dict) else {}
        attrs=CombatService._template_attributes(headphones)
        coefficient=max(0,CombatService._coerce_float(headphones.get('deafeningCoef',attrs.get('deafening_coef')),1))
        absorption=max(0,CombatService._coerce_float(headphones.get('noiseAbsorption',attrs.get('noise_absorption')),0))
        received=max(0,volume*context['coefficient']-absorption)*coefficient*count
        if received<=0:continue
        before=CombatService._deafness_level(data)
        health=data.setdefault('health',{})
        effects=normalize_effect_list(health.get('effects') or [])
        previous=next((e for e in effects if e['type']=='deafness' and e.get('source')=='noise'),{})
        health['effects']=[e for e in effects if not(e['type']=='deafness' and e.get('source')=='noise')]
        apply_effect_to_health(health,{'id':previous.get('id') or uuid.uuid4().hex,
            'type':'deafness','value':before+received,'source':'noise','tick':'manual'})
        flag_modified(target.character,'data')
        CombatService._sync_location_effects_from_character(target)
        results.append({'character_id':target.character_id,'noise_received':received,'deafness':before+received,
                        'space':context['kind'],'space_coefficient':context['coefficient']})
    return results
