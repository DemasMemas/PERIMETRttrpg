"""Общая пространственная геометрия взрыва и его эпицентра."""
import math

from app.services.location_navigation import surface,ground,STOREY_HEIGHT


def landing_level(location,objects,x,y,level):
    """В проломе граната падает до ближайшей нижней поверхности."""
    from app.services.location_navigation import level_of,opening_at
    levels={0,level}|{level_of(o) for o in objects if o.type=='floor'}
    for candidate in sorted((v for v in levels if v<=level),reverse=True):
        node=surface(location,objects,x,y,candidate)
        if node is not None and (node.get('stair') or not opening_at(objects,candidate,x+.5,y+.5)):
            return candidate
    return None


def height(location,objects,x,y,level):
    node=surface(location,objects,x,y,level)
    return node['height'] if node else ground(location,x,y)+level*STOREY_HEIGHT


def distance(location,objects,x,y,level,target):
    return math.sqrt((target.pos_x-x)**2+(target.pos_y-y)**2+
        (height(location,objects,target.pos_x,target.pos_y,target.floor_level or 0)-height(location,objects,x,y,level))**2)


def stop_at_wall(location, objects, x, y, end_x, end_y, level):
    from app.services.combat import CombatService

    geometry = CombatService._movement_wall_geometry(location, objects)
    start, end = (x + .5, y + .5), (end_x + .5, end_y + .5)
    dx, dy = end_x - x, end_y - y
    length = math.hypot(dx, dy)
    if not length:
        return end_x, end_y
    first = None
    for box in [*geometry.boxes, *(box for box, _, _ in geometry.climb_boxes)]:
        if box.level != level:
            continue
        entry = box.segment_entry(start, end)
        if entry is None or (first is not None and entry >= first):
            continue
        elevation = height(location, objects, x + dx * entry, y + dy * entry, level)
        if box.bottom - 1e-9 <= elevation <= box.top + 1e-9:
            first = entry
    if first is None:
        return end_x, end_y
    # Оставляем гранату снаружи коллизии, чтобы следующий пинок от стены был возможен.
    fraction = max(0, first - .01 / length)
    return x + dx * fraction, y + dy * fraction
