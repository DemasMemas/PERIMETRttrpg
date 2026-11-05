"""Область дыма/газа обходит стены через существующие открытые проёмы."""
import math
from collections import deque
from copy import deepcopy
from types import SimpleNamespace
from app.extensions import db
from app.models import Location,LocationObject
from app.services.location_navigation import level_of,contains,opening_at
from app.services.explosion_geometry import height


def cells(location,objects,area):
    from app.services.combat import CombatService
    physical=[o for o in objects if o.type in {'wall','door','window'}]
    # Проходимость персонажа не разрешает ходить в открытое окно;
    # для облака тот же проём является проходом на высоте окна.
    geometry_objects=[]
    for obj in objects:
        props=obj.properties or {}
        bottom=float(props.get('elevation') or 0)
        top=bottom+CombatService._object_height(obj)
        if obj.type=='window' and props.get('is_open') and bottom<=1.4<=top:
            wall=next((w for w in objects if CombatService._door_is_in_wall(obj,w)),None)
            passage_props={**props,'elevation':(wall.properties or {}).get('elevation',0) if wall else 0}
            geometry_objects.append(SimpleNamespace(id=obj.id,type='door',tile_x=obj.tile_x,tile_y=obj.tile_y,properties=passage_props))
        else:geometry_objects.append(obj)
    geometry=CombatService._movement_wall_geometry(location,geometry_objects)
    physical_ids={o.id for o in physical}
    boxes=[b for b in [*geometry.boxes,*(v[0] for v in geometry.climb_boxes)] if b.object_id in physical_ids]
    level=area.get('floor_level',0);radius=area.get('radius',0)
    start=(math.floor(area['x']+.5),math.floor(area['y']+.5),level)
    source_height=height(location,objects,area['x'],area['y'],level)
    levels={0,level}|{level_of(o) for o in objects if o.type=='floor'}
    def eligible(p):
        x,y,l=p
        if not (0<=x<location.grid_width and 0<=y<location.grid_height):return False
        if math.sqrt((x-area['x'])**2+(y-area['y'])**2+(height(location,objects,x,y,l)-source_height)**2)>radius+.71:return False
        return l==0 or any(o.type=='floor' and level_of(o)==l and contains(o,x+.5,y+.5) for o in objects)
    seen={start};queue=deque([start])
    while queue:
        x,y,l=queue.popleft()
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            p=(x+dx,y+dy,l)
            if p in seen or not eligible(p):continue
            plane=height(location,objects,x,y,l)+1.4
            if any(b.level==l and b.bottom<=plane<=b.top and b.segment_entry((x+.5,y+.5),(p[0]+.5,p[1]+.5)) is not None for b in boxes):continue
            seen.add(p);queue.append(p)
        if area.get('type')=='fire':continue
        for other in (l-1,l+1):
            p=(x,y,other)
            if other not in levels or p in seen or not eligible(p):continue
            if opening_at(objects,max(l,other),x+.5,y+.5):
                seen.add(p);queue.append(p)
    return sorted(seen)


def prepare(location,objects,area):
    if area.get('shape') in {'flame_rectangle','flame_cone'}:
        from app.services.flamethrower import cells as flame_cells
        signature=[area.get('x'),area.get('y'),area.get('direction'),area.get('length'),area.get('width'),[[o.id,o.revision] for o in objects]]
        if area.get('cells_signature')!=signature:
            area['cells']=flame_cells(location,objects,area);area['cells_signature']=signature
        return area
    signature=[area.get('radius'),[[o.id,o.revision] for o in objects]]
    if area.get('cells_signature')!=signature:
        area['cells']=[list(p) for p in cells(location,objects,area)]
        area['cells_signature']=signature
    return area


def refresh(state):
    if not state or not state.area_effects:return
    location=db.session.get(Location,state.location_id)
    objects=LocationObject.query.filter_by(location_id=state.location_id).all()
    areas=deepcopy(state.area_effects)
    for area in areas:
        if str(area.get('type','')).startswith('smoke') or area.get('type') in {'gas','fire'}:
            prepare(location,objects,area)
    if areas!=state.area_effects:state.area_effects=areas
