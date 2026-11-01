"""Discrete storeys and stair surfaces. Rendering/viewed floor is never an input."""
import heapq
import math

STOREY_HEIGHT = 2.4


def level_of(item):
    props = item.get('properties', {}) if isinstance(item, dict) else (getattr(item, 'properties', None) or {})
    value = props.get('level', item.get('level', 0) if isinstance(item, dict) else 0)
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number.is_integer() else 0
    except (ValueError, TypeError):
        return 0


def value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def number(raw, fallback):
    try:
        result = float(raw)
        return result if math.isfinite(result) else fallback
    except (ValueError, TypeError):
        return fallback


def pose(item):
    p = value(item, 'properties', {}) or {}
    d = p.get('dimensions') or {}
    angle = number(p.get('rotation'), 0)
    defaults = {'stairs':(1.2,3.6),'ladder':(.9,.9),'hatch':(1,1),'floor_opening':(1,1)}.get(value(item,'type'),(2,2))
    return (number(value(item, 'tile_x'), 0) + .5 + number(p.get('offset_x'), 0),
            number(value(item, 'tile_y'), 0) + .5 + number(p.get('offset_z'), 0),
            max(.01, number(d.get('width'), defaults[0])), max(.01, number(d.get('depth'), defaults[1])), angle)


def local_point(item, x, y):
    cx, cy, w, d, angle = pose(item)
    dx, dy = x - cx, y - cy
    return dx * math.cos(angle) - dy * math.sin(angle), dx * math.sin(angle) + dy * math.cos(angle), w, d


def contains(item, x, y):
    a, b, w, d = local_point(item, x, y)
    return abs(a) < w / 2 - .00001 and abs(b) < d / 2 - .00001


def ground(location, x, y):
    tiles = getattr(location, 'tiles_data', None) or []
    x,y = math.floor(x+.5),math.floor(y+.5)
    tile = tiles[y][x] if 0 <= y < len(tiles) and 0 <= x < len(tiles[y]) else {}
    return number((tile or {}).get('height'), 1)


def opening_at(objects, level, x, y, building_id=None):
    """Horizontal passage; a closed hatch seals even an existing stairwell."""
    peers = [o for o in objects if building_id is None or (value(o, 'properties', {}) or {}).get('building_id') == building_id]
    hatches = [o for o in peers if value(o, 'type') == 'hatch' and level_of(o) == level and contains(o, x, y)]
    if any(not (value(o, 'properties', {}) or {}).get('is_open') for o in hatches):
        return False
    return bool(hatches) or any(value(o,'type') == 'floor_opening' and level_of(o) == level and contains(o,x,y) for o in peers) or any(value(o, 'type') in {'stairs', 'ladder'} and level_of(o)+1 == level
                               and contains(o, x, y) for o in peers)


def drop_route(location, objects, start, portal, maps, geometry=None):
    """An explicit downward action, deliberately absent from ordinary pathfinding."""
    kind = value(portal,'type')
    if kind not in {'hatch','floor_opening','stairs','ladder'}:
        return None
    upper = level_of(portal) + int(kind in {'stairs','ladder'})
    if geometry is not None:
        return _continuous_drop_route(location, objects, start, portal, geometry)
    px, py, *_ = pose(portal)
    x,y = math.floor(px),math.floor(py)
    if start[2] != upper or max(abs(start[0]-x),abs(start[1]-y)) > 1:
        return None
    if not (0 <= x < location.grid_width and 0 <= y < location.grid_height) or not contains(portal,x+.5,y+.5):
        return None
    if not opening_at(objects,upper,x+.5,y+.5):
        return None
    origin = surface(location,objects,*start)
    if not origin:
        return None
    blocked,costs = maps(upper)
    if (x,y) in blocked or costs.get((x,y),0):
        return None
    levels = sorted({0} | {level_of(o) for o in objects},reverse=True)
    for level in levels:
        if level >= upper:
            continue
        blocked,costs = maps(level)
        if (x,y) in blocked or costs.get((x,y),0):
            return None
        if opening_at(objects,level,x+.5,y+.5):
            continue
        landing = surface(location,objects,x,y,level)
        if not landing:
            if any(value(o,'type') == 'hatch' and level_of(o) == level and contains(o,x+.5,y+.5)
                   and not (value(o,'properties',{}) or {}).get('is_open') for o in objects):
                return None
            continue
        height = origin['height']-landing['height']
        if height <= .01:
            return None
        points = [{k:origin[k] for k in ('x','y','level','height')}]
        if (start[0],start[1]) != (x,y):
            points.append({'x':x,'y':y,'level':upper,'height':origin['height']})
        steps = max(2,math.ceil(math.sqrt(2*height/9.81)/.18))
        for i in range(1,steps):
            fallen = height*(i/steps)**2
            points.append({'x':x,'y':y,'level':max(level,upper-max(1,math.ceil(fallen/STOREY_HEIGHT))),
                           'height':origin['height']-fallen})
        points.append({k:landing[k] for k in ('x','y','level','height')})
        return {'surfaces':points,'height':height,'landing':landing}
    return None


def _continuous_drop_route(location, objects, start, portal, geometry):
    """Точка внутри отверстия и свободная вертикальная колонна без округления клетки."""
    from app.services.movement_geometry import CHARACTER_RADIUS, WALL_CLEARANCE
    upper = level_of(portal) + int(value(portal, 'type') in {'stairs','ladder'})
    if start[2] != upper:
        return None
    origin = surface(location, objects, *start)
    if not origin:
        return None
    cx, cy, width, depth, angle = pose(portal)
    margin = CHARACTER_RADIUS + WALL_CLEARANCE + .001
    half_w, half_d = width/2-margin, depth/2-margin
    if half_w < 0 or half_d < 0:
        return None
    a,b,_,_ = local_point(portal,start[0]+.5,start[1]+.5)
    if math.hypot(max(abs(a)-width/2,0),max(abs(b)-depth/2,0)) > 1+1e-8:
        return None
    # Сначала ближайшая безопасная точка; затем альтернативы в том же проёме.
    local = {(max(-half_w,min(half_w,a)),max(-half_d,min(half_d,b))), (0.,0.)}
    for i in range(math.ceil(2*half_w/.25)+1):
        for j in range(math.ceil(2*half_d/.25)+1):
            local.add((min(half_w,-half_w+i*.25),min(half_d,-half_d+j*.25)))
    candidates = [(cx+x*math.cos(angle)+y*math.sin(angle)-.5,
                   cy-x*math.sin(angle)+y*math.cos(angle)-.5) for x,y in local]
    candidates.sort(key=lambda p:(math.hypot(p[0]-start[0],p[1]-start[1]),p))
    levels = sorted({0}|{level_of(o) for o in objects},reverse=True)
    for x,y in candidates:
        if not (0<=x<=location.grid_width-1 and 0<=y<=location.grid_height-1):
            continue
        end = (x,y,upper)
        if not opening_at(objects,upper,x+.5,y+.5) or not geometry.can_traverse(start,end) or geometry.climb_edge_events(start,end):
            continue
        approach = math.hypot(x-start[0],y-start[1])
        if any(not surface(location,objects,start[0]+(x-start[0])*t,start[1]+(y-start[1])*t,upper)
               and not contains(portal,start[0]+(x-start[0])*t+.5,start[1]+(y-start[1])*t+.5)
               for t in (i/max(1,math.ceil(approach/.2)) for i in range(1,max(1,math.ceil(approach/.2))+1))):
            continue
        for level in levels:
            if level>=upper:
                continue
            point = (x,y,level)
            if not geometry.can_traverse(point,point) or geometry.climb_at(point):
                break
            if opening_at(objects,level,x+.5,y+.5):
                continue
            landing = surface(location,objects,x,y,level)
            if not landing:
                continue
            height = origin['height']-landing['height']
            if height<=.01:
                break
            points=[{k:origin[k] for k in ('x','y','level','height')}]
            if approach>1e-9:
                points.append(dict(x=x,y=y,level=upper,height=origin['height']))
            steps=max(2,math.ceil(math.sqrt(2*height/9.81)/.18))
            points.extend(dict(x=x,y=y,level=upper,height=origin['height']-height*(i/steps)**2) for i in range(1,steps))
            points.append({k:landing[k] for k in ('x','y','level','height')})
            return dict(surfaces=points,height=height,landing=landing)
    return None


def ladder_connection(location, objects, a, b):
    if a['x'] != b['x'] or a['y'] != b['y'] or abs(a['level']-b['level']) != 1:
        return False
    low, high = sorted([a, b], key=lambda n: n['level'])
    x, y = a['x']+.5, a['y']+.5
    for ladder in objects:
        if value(ladder, 'type') != 'ladder' or level_of(ladder) != low['level'] or not contains(ladder, x, y):
            continue
        p = value(ladder, 'properties', {}) or {}
        base = ground(location, value(ladder, 'tile_x'), value(ladder, 'tile_y')) + low['level']*STOREY_HEIGHT + number(p.get('elevation'), 0)
        rise = number((p.get('dimensions') or {}).get('height'), STOREY_HEIGHT)
        if abs(low['height']-base) > .21 or abs(high['height']-(base+rise)) > .21:
            continue
        building_id = p.get('building_id')
        if all(n['level'] == 0 or any(value(f, 'type') == 'floor' and level_of(f) == n['level']
               and (value(f, 'properties', {}) or {}).get('building_id') == building_id and contains(f, x, y)
               for f in objects) for n in (low, high)) and opening_at(objects, high['level'], x, y, building_id):
            return True
    return False


def surface(location, objects, x, y, level):
    """One walkable surface per cell/storey. Stairs belong to their lower storey."""
    floors = [o for o in objects if value(o, 'type') == 'floor' and level_of(o) == level and contains(o, x + .5, y + .5)]
    height = ground(location, x, y) + level * STOREY_HEIGHT
    if floors:
        f = floors[0]
        height = ground(location, value(f, 'tile_x'), value(f, 'tile_y')) + level * STOREY_HEIGHT + number((value(f, 'properties', {}) or {}).get('elevation'), 0) + .035
    # Closed hatches remain walkable lids, but seal the vertical route.
    if any(value(o, 'type') == 'hatch' and level_of(o) == level and contains(o,x+.5,y+.5)
           and not (value(o,'properties',{}) or {}).get('is_open') for o in objects) and (floors or level == 0):
        return {'x':x,'y':y,'level':level,'height':height,'stair':None}
    # Match the conservative rectangular cut used by floorSlabParts for rotated flights.
    for stair in objects:
        if value(stair, 'type') != 'stairs' or level_of(stair)+1 != level:
            continue
        p = value(stair, 'properties', {}) or {}
        if floors and not any((value(f, 'properties', {}) or {}).get('building_id') == p.get('building_id') for f in floors):
            continue
        cx, cy, w, d, angle = pose(stair)
        # Floors and stairwells normally share the orthogonal building axes.
        for floor in [f for f in floors if (value(f, 'properties', {}) or {}).get('building_id') == p.get('building_id')] or [None]:
            floor_angle = pose(floor)[4] if floor else 0
            delta = angle - floor_angle
            width = abs(math.cos(delta))*w + abs(math.sin(delta))*d
            depth = abs(math.sin(delta))*w + abs(math.cos(delta))*d
            dx, dy = x+.5-cx, y+.5-cy
            a = dx*math.cos(floor_angle)-dy*math.sin(floor_angle)
            b = dx*math.sin(floor_angle)+dy*math.cos(floor_angle)
            if abs(a)<width/2-.00001 and abs(b)<depth/2-.00001:
                # A flight starting here can occupy the opening in a stacked stairwell.
                if not any(value(o,'type')=='stairs' and level_of(o)==level and contains(o,x+.5,y+.5) for o in objects):
                    return None
    stairs = [o for o in objects if value(o, 'type') == 'stairs' and contains(o, x + .5, y + .5)]
    stairs.sort(key=lambda o: level_of(o) != level)
    for stair in stairs:
        p = value(stair, 'properties', {}) or {}
        base = level_of(stair)
        if level != base:
            continue
        _, along, _, depth = local_point(stair, x + .5, y + .5)
        rise = max(.01, number((p.get('dimensions') or {}).get('height'), STOREY_HEIGHT))
        count = max(1, math.ceil(rise / .2))
        step = min(count, max(1, math.floor((along / depth + .5) * count) + 1))
        base_height = ground(location, value(stair, 'tile_x'), value(stair, 'tile_y')) + base * STOREY_HEIGHT + number(p.get('elevation'), 0)
        height = base_height + rise * step / count
        return {'x': x, 'y': y, 'level': level, 'height': height, 'stair': stair, 'base_height':base_height, 'rise':rise}
    if level != 0 and not floors:
        return None
    return {'x': x, 'y': y, 'level': level, 'height': height, 'stair': None}


def ordinary_height_transition_allowed(before, after):
    # От 1 м требуется отдельное действие подъёма/спуска, не обычный шаг.
    return math.isfinite(before) and math.isfinite(after) and abs(after - before) < 1


def connects(a, b):
    if not a['stair'] and not b['stair']:
        return a['level'] == b['level'] and ordinary_height_transition_allowed(a['height'], b['height'])
    if abs(a['level'] - b['level']) > 1:
        return False
    for on, other in [(a, b), (b, a)]:
        stair = on['stair']
        if not stair:
            continue
        _, along, _, depth = local_point(stair, other['x'] + .5, other['y'] + .5)
        across, _, width, _ = local_point(stair, other['x'] + .5, other['y'] + .5)
        if other['stair'] is not stair:
            # Enter and leave through the ends, never through a side of a tall step.
            if abs(across) >= width / 2 or abs(along) < depth / 2 - .00001:
                return False
            expected = level_of(stair) + (1 if along > 0 else 0)
            if other['level'] != expected:
                return False
            landing_height = on['base_height'] + (on['rise'] if along > 0 else 0)
            if abs(other['height'] - landing_height) > .21:
                return False
    rise = max(number((value(n['stair'], 'properties', {}) or {}).get('dimensions', {}).get('height'), 2.4) /
               pose(n['stair'])[3] for n in (a, b) if n['stair'])
    distance = math.hypot(a['x'] - b['x'], a['y'] - b['y'])
    return abs(a['height'] - b['height']) <= rise * distance + .21


def find_path(location, objects, start, end, maps, terrain_costs=None, can_traverse=None, geometry=None, continuous=False):
    """maps(level) returns the established blocked cells and climb costs."""
    levels = {0, start[2], end[2]} | {level_of(o) for o in objects}
    # Surface geometry only uses these structures. Collision maps still include
    # every object. Snapshot ORM fields once rather than reading them per node.
    surface_objects = [dict(type=value(o, 'type'), level=level_of(o), tile_x=value(o, 'tile_x'),
                     tile_y=value(o, 'tile_y'), properties=value(o, 'properties', {}) or {})
                for o in objects if value(o, 'type') in {'floor', 'stairs', 'ladder', 'hatch', 'floor_opening'}]
    surface_geometry = [o for o in surface_objects if o['type'] in {'floor', 'stairs', 'hatch'}]
    # Ищем поверхности только рядом с узлом, сохраняя порядок перекрытий.
    surface_buckets = {}
    for item in surface_geometry:
        cx,cy,w,d,angle = pose(item)
        rx=(abs(math.cos(angle))*w+abs(math.sin(angle))*d)/2
        ry=(abs(math.sin(angle))*w+abs(math.cos(angle))*d)/2
        for x in range(math.floor(cx-rx),math.floor(cx+rx)+1):
            for y in range(math.floor(cy-ry),math.floor(cy+ry)+1):
                for level in ({level_of(item),level_of(item)+1} if item['type']=='stairs' else {level_of(item)}):
                    surface_buckets.setdefault((x,y,level),[]).append(item)
    cache = {}
    anchor_buckets = {}
    if continuous:
        anchors = set(geometry.anchor_points() if geometry else ()) | {start,end}
        for x,y,level in anchors:
            if 0 <= x < location.grid_width and 0 <= y < location.grid_height:
                anchor_buckets.setdefault((math.floor(x),math.floor(y),level),set()).add((x,y,level))

    def cell(key):
        return math.floor(key[0]+.5),math.floor(key[1]+.5)

    def weighted_segment(a,b):
        length = math.hypot(b['x']-a['x'],b['y']-a['y'])
        if not length:
            return abs(b['height']-a['height'])
        cuts = {0.,1.}
        for axis in ('x','y'):
            lo,hi = sorted((a[axis]+.5,b[axis]+.5))
            delta = b[axis]-a[axis]
            if not delta:
                continue
            for boundary in range(math.floor(lo)+1,math.ceil(hi)):
                t = (boundary-a[axis]-.5)/delta
                if 0 < t < 1:
                    cuts.add(t)
        ordered = sorted(cuts)
        result = 0
        for lo,hi in zip(ordered,ordered[1:]):
            t = (lo+hi)/2
            x,y = math.floor(a['x']+(b['x']-a['x'])*t+.5),math.floor(a['y']+(b['y']-a['y'])*t+.5)
            result += length*(hi-lo)*(terrain_costs or {}).get((x,y,b['level']),1)
        return result

    def node(key):
        if key not in cache:
            x, y, level = key
            nearby = surface_buckets.get((math.floor(x+.5),math.floor(y+.5),level),())
            cache[key] = surface(location, nearby, x, y, level)
        return cache[key]

    source, target = node(start), node(end)
    if not source or not target:
        return None
    blocked, costs = maps(end[2])
    if (end[0], end[1]) in blocked and end != start or costs.get(cell(end) if continuous else end[:2], 0) > 0:
        return None
    if geometry and (not geometry.can_traverse(end,end) or geometry.climb_at(end)>0):
        return None
    def estimate(key):
        return math.hypot(end[0]-key[0],end[1]-key[1]) if continuous else max(abs(end[i] - key[i]) for i in range(3))

    def distance(key):
        return sum((end[i] - key[i]) ** 2 for i in range(3))

    # Each edge costs at least one and changes each coordinate by at most one.
    # Chebyshev distance is admissible, including stairs and vertical ladders.
    queue = [(estimate(start), distance(start), 0, start)]
    best, previous, previous_climb, previous_climbs = {start: 0}, {}, {}, {}
    while queue:
        _, _, cost, key = heapq.heappop(queue)
        if cost != best[key]:
            continue
        if key == end:
            keys = [key]
            while key in previous:
                key = previous[key]
                keys.append(key)
            keys_in_order = list(reversed(keys))
            nodes = [node(k) for k in keys_in_order]
            surfaces = []
            for n in nodes:
                if surfaces and surfaces[-1]['x'] == n['x'] and surfaces[-1]['y'] == n['y'] and surfaces[-1]['level'] != n['level']:
                    a = surfaces[-1]
                    steps = max(1, math.ceil(abs(n['height']-a['height'])/.3))
                    surfaces.extend({'x':n['x'],'y':n['y'],'level':min(a['level'],n['level']),
                                     'height':a['height']+(n['height']-a['height'])*i/steps} for i in range(1,steps))
                surfaces.append({k:n[k] for k in ('x','y','level','height')})
            if continuous and geometry:
                raised=[]
                previous_raised=False
                edge_tops={(tuple(before),tuple(after)):max((event['top_height'] for event in previous_climbs.get(after,[])
                    if 'top_height' in event),default=None) for before,after in zip(keys_in_order,keys_in_order[1:])}
                before=None
                for point in surfaces:
                    top=geometry.climb_height_at((point['x'],point['y'],point['level']))
                    elevated=top is not None and top>point['height']
                    key=(point['x'],point['y'],point['level'])
                    edge_top=edge_tops.get((before,key))
                    if raised and edge_top is not None:
                        if edge_top>raised[-1]['height']:
                            raised.append({**raised[-1],'height':edge_top})
                        raised.append({**point,'height':max(point['height'],edge_top,top or point['height'])})
                    elif raised and elevated and top>raised[-1]['height']:
                        # Подъём до входа контура в препятствие.
                        raised.append({**raised[-1],'height':top})
                    elif raised and previous_raised and not elevated:
                        # Опускаемся после выхода контура за препятствие.
                        raised.append({**point,'height':raised[-1]['height']})
                    current={**point,'height':max(point['height'],top) if top is not None else point['height']}
                    if not raised or raised[-1]!=current:
                        raised.append(current)
                    previous_raised=elevated
                    before=key
                surfaces=raised
            climb_cost = sum(previous_climb.get(k,0) for k in keys_in_order[1:]) if continuous else sum(maps(n['level'])[1].get((n['x'], n['y']), 0) for n in nodes[1:])
            return {'path': [(n['x'], n['y']) for n in nodes], 'cost': cost,
                    'distance': sum(math.hypot(b['x']-a['x'],b['y']-a['y']) or abs(b['height']-a['height']) for a,b in zip(nodes,nodes[1:])) if continuous else len(nodes)-1,
                    'climb_cost': climb_cost,
                    'climbs': [event for key in keys_in_order[1:] for event in previous_climbs.get(key,[])],
                    'surfaces': surfaces}
        a = node(key)
        positions = {(key[0]+dx,key[1]+dy) for dx,dy in [(0,0),(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]} if not continuous else {
            (x,y) for x in range(math.floor(key[0])-1,math.ceil(key[0])+2) for y in range(math.floor(key[1])-1,math.ceil(key[1])+2)
            if math.hypot(x-key[0],y-key[1]) <= 1.5}
        if continuous:
            for level in levels:
                for bx in range(math.floor(key[0])-1,math.floor(key[0])+2):
                    for by in range(math.floor(key[1])-1,math.floor(key[1])+2):
                        positions.update((x,y) for x,y,_ in anchor_buckets.get((bx,by,level),()) if math.hypot(x-key[0],y-key[1])<=1.5)
        for x,y in sorted(positions):
            if not (0 <= x <= location.grid_width-1 and 0 <= y <= location.grid_height-1):
                continue
            for level in sorted(level for level in levels if abs(level-key[2])<=1):
                candidate = (x, y, level)
                b = node(candidate)
                vertical = x == key[0] and y == key[1]
                if not b or not (ladder_connection(location, surface_objects, a, b) if vertical else connects(a, b)):
                    continue
                blocked, costs = maps(level)
                if (x,y) in blocked or (continuous and cell(candidate) in blocked):
                    continue
                if can_traverse is not None and not can_traverse(key, candidate):
                    continue
                edge_cost = weighted_segment(a,b) if continuous else (max(1,math.ceil(abs(a['height']-b['height']))) if vertical else (terrain_costs or {}).get((x,y,level),1))
                events = geometry.climb_edge_events(key,candidate) if continuous and geometry else []
                climb = ((sum(event['base_cost'] for event in events) if geometry else 0)
                         + (costs.get(cell(candidate),0) if cell(key)!=cell(candidate) else 0)) if continuous else costs.get((x,y),0)
                remainder = climb-sum(event['base_cost'] for event in events)
                if remainder > 0:
                    events.append({'base_cost':remainder,'category':'high' if remainder>=12 else 'low'})
                new_cost = cost+edge_cost+climb
                if new_cost < best.get(candidate, math.inf):
                    best[candidate], previous[candidate] = new_cost, key
                    previous_climb[candidate] = climb
                    previous_climbs[candidate] = events
                    heapq.heappush(queue, (new_cost + estimate(candidate), distance(candidate), new_cost, candidate))
    return None
