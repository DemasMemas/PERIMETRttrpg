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
