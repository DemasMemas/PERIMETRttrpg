"""Непрерывная проверка круглого контура на отрезке между узлами маршрута."""
import math
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.location_navigation import level_of, number, value

CHARACTER_RADIUS = .25  # Согласованный пробный диаметр 0,5 м.
WALL_CLEARANCE = .01
EPSILON = 1e-9


def point_segment_distance_squared(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = dx * dx + dy * dy
    t = max(0, min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length)) if length else 0
    return (point[0] - start[0] - t * dx) ** 2 + (point[1] - start[1] - t * dy) ** 2


@dataclass(frozen=True)
class WallBox:
    x: float
    y: float
    width: float
    depth: float
    angle: float
    level: int
    bottom: float = -math.inf
    top: float = math.inf
    object_id: int | None = None

    def local(self, point):
        dx, dy = point[0] - self.x, point[1] - self.y
        c, s = math.cos(self.angle), math.sin(self.angle)
        return dx * c - dy * s, dx * s + dy * c

    def bounds(self, padding=0):
        c, s = abs(math.cos(self.angle)), abs(math.sin(self.angle))
        rx, ry = (self.width * c + self.depth * s) / 2 + padding, (self.width * s + self.depth * c) / 2 + padding
        return self.x - rx, self.y - ry, self.x + rx, self.y + ry

    def segment_entry(self, start, end):
        """Первая точка пересечения отрезка без радиуса персонажа."""
        a, b = self.local(start), self.local(end)
        w, d = self.width / 2, self.depth / 2
        entered, leaving = 0, 1
        for origin, target, half in zip(a, b, (w, d)):
            delta = target - origin
            if abs(delta) < EPSILON:
                if abs(origin) > half:
                    return None
            else:
                first, last = sorted(((-half - origin) / delta, (half - origin) / delta))
                entered, leaving = max(entered, first), min(leaving, last)
                if entered > leaving:
                    return None
        return entered

    def intersects_sweep(self, start, end, radius=CHARACTER_RADIUS + WALL_CLEARANCE):
        a, b = self.local(start), self.local(end)
        w, d = self.width / 2, self.depth / 2
        if self.segment_entry(start,end) is not None:
            return True
        # Точное расстояние до прямоугольника сохраняет круглые углы контура;
        # расширенный AABB здесь не является окончательной проверкой.
        endpoint_distance = min(max(abs(x) - w, 0) ** 2 + max(abs(y) - d, 0) ** 2 for x, y in (a, b))
        corner_distance = min(point_segment_distance_squared((x, y), a, b) for x in (-w, w) for y in (-d, d))
        return min(endpoint_distance, corner_distance) < radius * radius - EPSILON


@lru_cache(maxsize=1)
def tree_movement_profiles():
    return json.loads((Path(__file__).resolve().parents[1]/'static/data/treeMovementProfiles.json').read_text(encoding='utf-8'))['profiles']


def tree_trunk_box(obj, size):
    """Проекция настоящего ствола после общей нормализации модели с кроной."""
    kind=value(obj,'type','')
    model='oak_green' if kind=='tree' else kind.removeprefix('nature_')
    profile=tree_movement_profiles().get(model) if kind=='tree' or kind.startswith('nature_') else None
    if profile is None:
        return None
    props=value(obj,'properties',{}) or {}
    lo,hi=profile['min'],profile['max']
    width,depth=size
    angle=number(props.get('rotation'),0)
    dx,dz=(lo[0]+hi[0])*width/2,(lo[2]+hi[2])*depth/2
    x=number(value(obj,'tile_x'),0)+.5+number(props.get('offset_x'),0)
    y=number(value(obj,'tile_y'),0)+.5+number(props.get('offset_z'),0)
    return WallBox(x+dx*math.cos(angle)+dz*math.sin(angle),
                   y-dx*math.sin(angle)+dz*math.cos(angle),
                   (hi[0]-lo[0])*width,(hi[2]-lo[2])*depth,angle,level_of(obj))


def wall_boxes(wall, doors, size=None):
    """doors уже отобраны существующим контрактом принадлежности проёма стене."""
    p = value(wall, 'properties', {}) or {}
    dimensions = p.get('dimensions') or {}
    width = max(.01, number(dimensions.get('width', p.get('width')), 1.5))
    depth = max(.01, number(dimensions.get('depth', p.get('depth')), .2))
    if size is not None:
        width, depth = size
    angle = number(p.get('rotation'), 0)
    x = number(value(wall, 'tile_x'), 0) + .5 + number(p.get('offset_x'), 0)
    y = number(value(wall, 'tile_y'), 0) + .5 + number(p.get('offset_z'), 0)
    whole = WallBox(x, y, width, depth, angle, level_of(wall))
    openings = []
    for door in doors:
        props = value(door, 'properties', {}) or {}
        center = (number(value(door, 'tile_x'), 0) + .5 + number(props.get('offset_x'), 0),
                  number(value(door, 'tile_y'), 0) + .5 + number(props.get('offset_z'), 0))
        along = whole.local(center)[0]
        door_width = max(.01, number((props.get('dimensions') or {}).get('width', props.get('width')), .9))
        # Ширина и ось отверстия совпадают с doorOnWall/wallParts в renderer.
        openings.append((max(-width / 2, along - door_width / 2), min(width / 2, along + door_width / 2)))
    cursor = -width / 2
    spans = []
    for start, end in sorted(openings):
        if end <= start:
            continue
        if start > cursor:
            spans.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < width / 2:
        spans.append((cursor, width / 2))
    return [WallBox(x + (a + b) / 2 * math.cos(angle), y - (a + b) / 2 * math.sin(angle),
                    b - a, depth, angle, whole.level) for a, b in spans]


@dataclass(frozen=True)
class CharacterCircle:
    x: float
    y: float
    level: int

    def bounds(self, padding=0):
        r = CHARACTER_RADIUS + padding
        return self.x-r,self.y-r,self.x+r,self.y+r

    def intersects_sweep(self,start,end,radius=CHARACTER_RADIUS+WALL_CLEARANCE):
        return point_segment_distance_squared((self.x,self.y),start,end) < (CHARACTER_RADIUS+radius)**2-EPSILON


class WallGeometry:
    def __init__(self, boxes, handled_objects=(), climb_boxes=(), portals=()):
        self.boxes = list(boxes)
        self.handled_objects = set(handled_objects)
        self.climb_boxes = [(item[0],item[1],item[2] if len(item)>2 else {'base_cost':item[1],
            'category':'high' if item[1]>=12 else 'low'}) for item in climb_boxes]
        self.portals = list(portals)
        self.buckets = {}
        self.climb_buckets = {}
        self.edges = {}
        self.rebuild_index()

    def rebuild_index(self):
        self.buckets.clear()
        self.climb_buckets.clear()
        self.edges.clear()
        for index, box in enumerate(self.boxes):
            left, top, right, bottom = box.bounds(CHARACTER_RADIUS + WALL_CLEARANCE)
            for x in range(math.floor(left), math.floor(right) + 1):
                for y in range(math.floor(top), math.floor(bottom) + 1):
                    self.buckets.setdefault((x, y, box.level), set()).add(index)
        for index,(box,_,_) in enumerate(self.climb_boxes):
            left,top,right,bottom = box.bounds(CHARACTER_RADIUS+WALL_CLEARANCE)
            for x in range(math.floor(left),math.floor(right)+1):
                for y in range(math.floor(top),math.floor(bottom)+1):
                    self.climb_buckets.setdefault((x,y,box.level),set()).add(index)

    def add_characters(self,characters):
        self.boxes.extend(CharacterCircle(c.pos_x+.5,c.pos_y+.5,getattr(c,'floor_level',0) or 0) for c in characters)
        self.rebuild_index()

    def anchor_points(self):
        points = set(self.portals)
        padding = CHARACTER_RADIUS+WALL_CLEARANCE+.02
        for box in self.boxes:
            if isinstance(box,CharacterCircle):
                r = CHARACTER_RADIUS+padding
                for step in range(8):
                    angle = step*math.pi/4
                    points.add((box.x+math.cos(angle)*r-.5,box.y+math.sin(angle)*r-.5,box.level))
            else:
                for dx in (-box.width/2-padding,box.width/2+padding):
                    for dy in (-box.depth/2-padding,box.depth/2+padding):
                        points.add((box.x+dx*math.cos(box.angle)+dy*math.sin(box.angle)-.5,
                                    box.y-dx*math.sin(box.angle)+dy*math.cos(box.angle)-.5,box.level))
        return points

    def climb_at(self,point):
        center = (point[0]+.5,point[1]+.5)
        candidates = self.climb_buckets.get((math.floor(center[0]),math.floor(center[1]),point[2]),())
        return max((self.climb_boxes[i][1] for i in candidates if self.climb_boxes[i][0].intersects_sweep(center,center)),default=0)

    def climb_height_at(self,point):
        center=(point[0]+.5,point[1]+.5)
        candidates=self.climb_buckets.get((math.floor(center[0]),math.floor(center[1]),point[2]),())
        heights=[self.climb_boxes[i][2]['top_height'] for i in candidates
                 if 'top_height' in self.climb_boxes[i][2] and self.climb_boxes[i][0].intersects_sweep(center,center)]
        return max(heights,default=None)

    def climb_edge_cost(self,start,end):
        return sum(item['base_cost'] for item in self.climb_edge_events(start,end))

    def climb_edge_events(self,start,end):
        a,b = (start[0]+.5,start[1]+.5),(end[0]+.5,end[1]+.5)
        candidates = set()
        for level in {start[2],end[2]}:
            for x in range(math.floor(min(a[0],b[0])),math.floor(max(a[0],b[0]))+1):
                for y in range(math.floor(min(a[1],b[1])),math.floor(max(a[1],b[1]))+1):
                    candidates.update(self.climb_buckets.get((x,y,level),()))
        return [event for i in sorted(candidates) for box,_,event in [self.climb_boxes[i]]
                if not box.intersects_sweep(a,a) and box.intersects_sweep(a,b)]

    def handles(self, obj):
        return id(obj) in self.handled_objects

    def can_traverse(self, start, end):
        key = tuple(sorted((tuple(start), tuple(end))))
        if key in self.edges:
            return self.edges[key]
        # Сохраняется действующий контракт: целая координата означает центр клетки.
        a, b = (start[0] + .5, start[1] + .5), (end[0] + .5, end[1] + .5)
        candidates = set()
        for level in {start[2], end[2]}:
            for x in range(math.floor(min(a[0], b[0])), math.floor(max(a[0], b[0])) + 1):
                for y in range(math.floor(min(a[1], b[1])), math.floor(max(a[1], b[1])) + 1):
                    candidates.update(self.buckets.get((x, y, level), ()))
        result = not any(self.boxes[i].intersects_sweep(a, b) for i in candidates)
        self.edges[key] = result
        return result

    def blocked_cells(self, level, width=None, height=None):
        return {(x, y) for x, y, floor in self.buckets if floor == level
                and (width is None or 0 <= x < width) and (height is None or 0 <= y < height)
                and not self.can_traverse((x, y, level), (x, y, level))}
