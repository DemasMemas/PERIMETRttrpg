"""Ледяные участки маршрута и вынужденное продолжение ходьбой."""
import math
from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified

EPS = 1e-8


def slippery_tiles(location, objects=(), ignores_terrain=False):
    from app.services.combat import CombatService
    from app.services.location_navigation import level_of
    if ignores_terrain:
        return set()
    ice, all_objects = set(), list(objects)
    for y, row in enumerate(getattr(location, 'tiles_data', None) or []):
        if not isinstance(row, list):
            continue
        for x, tile in enumerate(row):
            if not isinstance(tile, dict):
                continue
            if tile.get('terrain') == 'ice':
                ice.add((x, y, 0))
            all_objects.extend({**obj, 'tile_x':x, 'tile_y':y} for obj in tile.get('objects') or []
                               if isinstance(obj,dict) and obj.get('type') == 'floor')
    for obj in all_objects:
        kind = obj.get('type') if isinstance(obj, dict) else obj.type
        if kind == 'floor':
            ice.difference_update((x, y, level_of(obj)) for x, y in CombatService._object_footprint_tiles(obj))
    return ice


def terrain_segments(a, b, costs=None):
    """Делит непрерывный отрезок по границам клеток, как серверный поиск пути."""
    length = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
    vertical = not length
    if vertical:
        length = abs(b['height'] - a['height'])
    cuts = {0., 1.}
    for axis in ('x', 'y'):
        lo, hi = sorted((a[axis] + .5, b[axis] + .5))
        delta = b[axis] - a[axis]
        if not delta:
            continue
        for boundary in range(math.floor(lo) + 1, math.ceil(hi)):
            t = (boundary - a[axis] - .5) / delta
            if 0 < t < 1:
                cuts.add(t)
    ordered = sorted(cuts)
    for lo, hi in zip(ordered, ordered[1:]):
        def point(t):
            return {**b, **{key: a[key] + (b[key] - a[key]) * t for key in ('x', 'y', 'height')}}
        mid = point((lo + hi) / 2)
        cell = (math.floor(mid['x'] + .5), math.floor(mid['y'] + .5), b['level'])
        yield {'start': point(lo), 'end': point(hi), 'distance': length * (hi - lo),
               'cost': length * (hi - lo) * (1 if vertical else (costs or {}).get(cell, 1)), 'cell': cell,
               'level_transition': a['level'] != b['level'],
               't0': lo, 't1': hi}


def saved_state(actor):
    data = getattr(getattr(actor, 'character', None), 'data', None) or {}
    value = (data.get('health') or {}).get('combatMeta', {}).get('slipperyMovement')
    return value if isinstance(value, dict) and actor.movement_mode_this_turn else {}


def _check(actor):
    from app.services.combat import CombatService
    help_bonus = CombatService._consume_help_advantage(actor, 'skills.physical.agility')
    data = deepcopy(actor.character.data)
    check = CombatService._narrative_skill_check(data, 'skills.physical.agility', advantage=bool(help_bonus))
    check.update(difficulty=12, success=check['total'] >= 12)
    if help_bonus:
        check['help'] = help_bonus
    CombatService._apply_stress_check_consequences(data, 'skills.physical.agility', check['success'])
    actor.character.data = data
    flag_modified(actor.character, 'data')
    return check


def clip_route(path, point, edge_index):
    """Обрезает уже выбранный путь; поиск другого обхода после броска не выполняется."""
    result = deepcopy(path)
    result['path'] = list(path['path'][:edge_index + 1])
    xy = (point['x'], point['y'])
    if not result['path'] or tuple(result['path'][-1]) != xy:
        result['path'].append(xy)
    visual = path.get('surfaces') or []
    kept = []
    for i, before in enumerate(visual):
        kept.append(before)
        if i + 1 == len(visual):
            break
        after = visual[i + 1]
        dx, dy = after['x'] - before['x'], after['y'] - before['y']
        d2 = dx * dx + dy * dy
        if d2 <= EPS or point['level'] not in {before['level'], after['level']}:
            continue
        t = ((point['x'] - before['x']) * dx + (point['y'] - before['y']) * dy) / d2
        if (-EPS <= t <= 1 + EPS and abs(before['x'] + dx * t - point['x']) < EPS
                and abs(before['y'] + dy * t - point['y']) < EPS):
            end = {**point, 'height': before['height'] + (after['height'] - before['height']) * max(0, min(1, t))}
            if kept[-1] != end:
                kept.append(end)
            break
    result['surfaces'] = kept or [point]
    return result


def resolve_route(location, actor, path, mode, posture, climb_payment, maximum_distance):
    """Вызывается после проверок прав/ресурсов, до списания; кубик только серверный."""
    from app.models import LocationObject
    from app.services.combat import CombatService
    objects = LocationObject.query.filter_by(location_id=location.id).all()
    ignores = CombatService._mutant_has_trait(actor.character.data or {}, 'Полёт')
    costs = CombatService._movement_surface_costs(location, objects, ignores_terrain=ignores)
    ice = slippery_tiles(location, objects, ignores)
    parts = deepcopy(path.get('movement_segments') or [])
    if not parts:
        visual = path.get('surfaces') or []
        nodes = [next((n for n in visual if n['x'] == x and n['y'] == y),
                      dict(x=x, y=y, height=1, level=actor.floor_level or 0)) for x, y in path['path']]
        for index, (a, b) in enumerate(zip(nodes, nodes[1:])):
            parts.extend({**p, 'edge_index': index} for p in terrain_segments(a, b, costs))
        if parts and path.get('climb_cost'):
            parts[0]['climbs'] = path.get('climbs') or [{'base_cost': path['climb_cost'], 'category': 'low'}]
    previous = saved_state(actor)
    position = [actor.pos_x, actor.pos_y, actor.floor_level or 0]
    checked = bool(previous.get('checked_on_ice') and previous.get('position') == position)
    forced_from = previous.get('forced_from')
    checks, spent, distance, climb_ap, raw_cost, climbs = [], 0., 0., 0, 0., []
    point = dict(x=actor.pos_x, y=actor.pos_y, level=actor.floor_level or 0, height=CombatService.physical_position(actor)['height'])
    last_index, complete = 0, True
    remaining_distance = max(0, maximum_distance - (actor.movement_distance_this_turn or 0))
    start_cell = (math.floor(actor.pos_x + .5), math.floor(actor.pos_y + .5), actor.floor_level or 0)
    if start_cell not in ice and not any(tuple(part['cell']) in ice for part in parts):
        profile = CombatService._movement_route_cost(path, mode, posture)
        fees = CombatService._climb_payment_profile(actor.character.data, path.get('climbs') or (
            [{'base_cost':path['climb_cost'], 'category':'high' if path['climb_cost'] >= 12 else 'low'}]
            if path.get('climb_cost') else []))
        last = path.get('surfaces', [])[-1] if path.get('surfaces') else dict(point, x=path['path'][-1][0], y=path['path'][-1][1])
        return dict(path=path, point=last, mode=mode,
                    cost=profile['movement_points']-profile['climb_cost']+(fees['movement_points'] if climb_payment=='movement' else 0),
                    climb_action_points=fees['action_points'] if climb_payment=='action' else 0,
                    distance=profile['distance'], checks=[], complete=True,
                    state={'position':[last['x'],last['y'],last['level']], 'checked_on_ice':False})
    # Выбор бега без смещения — тоже начало бега на льду.
    if start_cell in ice and mode in {'run', 'sprint'} and not checked:
        check = _check(actor)
        checks.append({**check, 'position': position})
        checked = check['success']
        if not checked:
            forced_from, mode = mode, 'walk'
    for part in parts:
        slippery = tuple(part['cell']) in ice and not part.get('level_transition')
        if not slippery:
            checked = False
        if slippery and mode in {'run', 'sprint'} and not checked:
            check = _check(actor)
            checks.append({**check, 'position': [part['start']['x'], part['start']['y'], part['start']['level']]})
            checked = check['success']
            if not checked:
                forced_from, mode = mode, 'walk'
        events = part.get('climbs') or []
        fees = CombatService._climb_payment_profile(actor.character.data, events)
        fee = fees['movement_points'] if climb_payment == 'movement' else 0
        travel = CombatService._movement_route_cost({'distance': part['distance'], 'cost': part['cost']}, mode, posture)['movement_points']
        available = max(0, actor.movement_points_current - spent - fee)
        take = min(1., available / travel if travel > EPS else 1.,
                   remaining_distance / part['distance'] if part['distance'] > EPS else 1.)
        if fee > actor.movement_points_current - spent + EPS or take <= EPS:
            complete = False
            break
        spent += fee + travel * take
        climb_ap += fees['action_points'] if climb_payment == 'action' else 0
        climbs.extend(events)
        raw_cost += part['cost'] * take + sum(e['base_cost'] for e in events)
        distance += part['distance'] * take
        remaining_distance -= part['distance'] * take
        point = {**part['end'], **{key: part['start'][key] + (part['end'][key] - part['start'][key]) * take
                                  for key in ('x', 'y', 'height')}}
        last_index = part.get('edge_index', 0)
        if take < 1 - EPS:
            complete = False
            break
    actual_path = path if complete else clip_route(path, point, last_index)
    if not complete:
        actual_path.update(distance=distance, cost=raw_cost, climbs=climbs,
                           climb_cost=sum(e['base_cost'] for e in climbs))
    end_cell = (math.floor(point['x'] + .5), math.floor(point['y'] + .5), point['level'])
    if complete and end_cell in ice and mode in {'run', 'sprint'} and not checked:
        check = _check(actor)
        checks.append({**check, 'position':[point['x'],point['y'],point['level']]})
        checked = check['success']
        if not checked:
            forced_from, mode = mode, 'walk'
    continuity = {'position': [point['x'], point['y'], point['level']],
                  'checked_on_ice': checked and end_cell in ice and mode in {'run', 'sprint'}}
    if forced_from:
        continuity['forced_from'] = forced_from
    return dict(path=actual_path, point=point, mode=mode, cost=spent, distance=distance,
                climb_action_points=climb_ap, checks=checks, complete=complete, state=continuity)
