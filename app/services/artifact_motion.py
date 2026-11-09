"""Принудительное перемещение от особых свойств артефактов."""
import math
from types import SimpleNamespace

from app.extensions import db
from app.models import LocationCharacter, LocationCombatState
from app.services.exceptions import ValidationError
from app.services.location_navigation import surface
from app.services.movement_geometry import CharacterCircle

TELEPORT = 'Жгут плазмы'
PULL = 'Глаз горизонта'
WAVES = {'Железное сердце': (5, 75, 25, 'slashing'),
         'Ядро возгорания': (5, 0, 0, 'thermal'),
         'Свет горизонта': (3, 50, 0, 'crushing')}


def _scene(actor):
    from app.services.character_jump import _scene as jump_scene
    return jump_scene(actor.location, actor)


def _round(actor):
    state = LocationCombatState.query.filter_by(location_id=actor.location_id, status='active').first()
    return (state.round_number or 0) if state else 0


def _position(actor, objects):
    from app.services.combat import CombatService
    return dict(x=actor.pos_x, y=actor.pos_y, level=actor.floor_level or 0,
                height=CombatService.physical_position(actor, objects=objects)['height'])


def _people_block(people, actor, start, end, level):
    return any(other.id != actor.id and (other.floor_level or 0) == level
        and CharacterCircle(other.pos_x+.5, other.pos_y+.5, level).intersects_sweep(start, end)
        for other in people)


def _place(actor, point, start, round_number):
    from app.services.character_jump import _place as place_after_motion
    from app.services.combat import CombatService
    if actor.grappled_by_id:
        holder = db.session.get(LocationCharacter, actor.grappled_by_id)
        if holder:
            CombatService._clear_grapple(holder, actor)
    if actor.grapple_target_id:
        CombatService._clear_grapple(actor)
    fall = place_after_motion(actor.location, actor, point, start, round_number)
    actor.last_action = db.func.now()
    CombatService._release_invalid_gunpoints(actor.location_id)
    return {'character_id':actor.character_id, 'x':point['x'], 'y':point['y'], 'height':point['height'],
            'floor_level':point['level'], 'fall':fall, 'trap_ids':getattr(actor, '_trap_ids', []),
            'gm_hidden':bool(actor.gm_hidden)}


def prepare_teleport(actor, data, point):
    from app.services.artifact_special import has_daylight_vision
    from app.services.character_traits import facial_profile
    from app.services.combat import CombatService
    from app.services.effects import normalize_effect_list
    from app.services.portable_lights import illumination
    if not isinstance(actor, LocationCharacter) or not isinstance(point, dict):
        raise ValidationError('Выберите точку на подлокации')
    x, y, level = point.get('x'), point.get('y'), point.get('floor_level')
    if (type(level) is not int or level != (actor.floor_level or 0) or
            any(type(value) not in {int,float} or not math.isfinite(value) for value in (x,y)) or
            not (0 <= x <= actor.location.grid_width-1 and 0 <= y <= actor.location.grid_height-1)):
        raise ValidationError('Выберите точку на текущем этаже')
    if facial_profile(data)['eyes'] == 2 or any(effect.get('active', True) and effect['type'] == 'blindness'
        and CombatService._coerce_float(effect.get('value'), 0) >= 100 for effect in normalize_effect_list((data.get('health') or {}).get('effects') or [])):
        raise ValidationError('Персонаж не видит точку')
    objects, geometry, people = _scene(actor)
    start = _position(actor, objects)
    landing = surface(actor.location, objects, x, y, level)
    if not landing or not geometry.can_traverse((x,y,level),(x,y,level)) or _people_block(people, actor, (x+.5,y+.5),(x+.5,y+.5),level):
        raise ValidationError('В точке нельзя приземлиться')
    distance = math.dist((x,y,landing['height']), (start['x'],start['y'],start['height']))
    if not 0 < distance <= 5 + 1e-9 or not CombatService._is_in_facing_arc(actor, x, y):
        raise ValidationError('Выберите видимую точку на расстоянии до 5 м')
    target = SimpleNamespace(id=None, character=None, location=actor.location, location_id=actor.location_id,
        pos_x=x, pos_y=y, floor_level=level, posture='prone')
    if CombatService._cover_analysis(actor.location_id, actor, target, objects=objects, point_height=.1)['zones'] or (
        not has_daylight_vision(data) and CombatService._is_dark_at_position(actor.location, math.floor(x), math.floor(y), objects=objects)
        and not illumination(target, actors=[actor,*people], objects=objects)['lit']):
        raise ValidationError('Персонаж не видит точку')
    return start, landing


def teleport(actor, data, plan):
    actor.character.data = data
    moved = _place(actor, plan[1], plan[0], _round(actor))
    new_data = actor.character.data
    if new_data is not data:
        data.clear()
        data.update(new_data)
    return {'_moves':[moved], '_vision_location_id':actor.location_id}


def _displace(actor, center, *, towards=False, scene=None):
    objects, geometry, people = scene or _scene(actor)
    start = _position(actor, objects)
    x, y, level = start['x'], start['y'], start['level']
    dx, dy = x-center.pos_x, y-center.pos_y
    length = math.hypot(dx,dy)
    if length < 1e-9:
        if towards:
            return None
        dx, dy = center.facing_x, center.facing_y
        length = math.hypot(dx,dy) or 1
    limit = min(5, max(0, length-.5)) if towards else 5
    direction = -1 if towards else 1
    landing = start
    for step in range(1, math.ceil(limit*10)+1):
        offset = min(limit, step/10) * direction / length
        px, py = x+dx*offset, y+dy*offset
        if not (0 <= px <= actor.location.grid_width-1 and 0 <= py <= actor.location.grid_height-1):
            break
        a, b = (landing['x'],landing['y'],level),(px,py,level)
        node = surface(actor.location, objects, px, py, level)
        if (not node or node['height']-landing['height'] > .35 or not geometry.can_traverse(a,b)
                or geometry.climb_edge_events(a,b) or _people_block(people, actor, (a[0]+.5,a[1]+.5),(px+.5,py+.5),level)):
            break
        landing = node
    return _place(actor, landing, start, _round(actor)) if landing is not start else None


def pull(actor, target):
    moved = _displace(target, actor, towards=True)
    return {'_moves':[moved] if moved else [], '_affected_character_ids':[target.character_id],
            '_vision_location_id':actor.location_id}


def _hit_chest(target, incoming, penetration, attack_kind, round_number):
    from app.services.artifact_effects import artifact_passive_profile
    from app.services.combat import CombatService
    data = target.character.data
    armor, layers = CombatService._target_armor(data,'chest',target=target)
    for layer in layers:
        CombatService._damage_armor_item(layer['item'],layer['attributes'],incoming,character_data=data,raw_damage=incoming)
    profile = {'damage':incoming, 'damage_type':attack_kind, 'armor_piercing':penetration}
    multiplier = CombatService._penetration_damage_multiplier(penetration,armor)
    vulnerability = artifact_passive_profile(data)['incoming_physical_percent']
    damage = max(0, round(incoming * multiplier * (1 + vulnerability/100)))
    health = CombatService._apply_attack_damage(target, damage, 'chest', profile, round_number=round_number,
        allow_bleeding=attack_kind=='slashing' and armor<=penetration, armor_penetrated=armor<=penetration)
    return health.pop('_attackOutcome',{}).get('applied_damage',damage)


def wave(actor, data, name):
    from app.services.artifact_area import group_characters, location_members
    from app.services.combat import CombatService
    from app.services.effects import normalize_effect_list
    from app.services.health import health_zones_to_location
    radius, damage, penetration, kind = WAVES[name]
    local = isinstance(actor, LocationCharacter)
    if name == 'Ядро возгорания':
        data['health']['effects'] = [effect for effect in normalize_effect_list(data['health'].get('effects') or []) if effect['type'] != 'burning']
    if local:
        objects, geometry, people = _scene(actor)
        targets = [person for person in location_members(actor,radius,objects=objects) if person.character_id != actor.character_id]
        scene = objects, geometry, [actor,*people]
        round_number = _round(actor)
    else:
        owner = getattr(actor, 'character', None)
        targets = [LocationCharacter(id=-member.id,character_id=member.id,character=member,posture='standing',effects=[],
            hp_zones=health_zones_to_location((member.data or {}).get('health') or {}))
            for member in group_characters(owner) if member.id != owner.id]
        round_number = 0
    affected, moves, hits = [], [], []
    for target in targets:
        if CombatService._character_condition(target.character.data)['state'] == 'dead':
            continue
        if damage:
            dealt = _hit_chest(target,damage,penetration,kind,round_number)
            hits.append({'character_id':target.character_id,'damage':dealt})
            affected.append(target.character_id)
        if local:
            moved = _displace(target, actor, scene=scene)
            if moved:
                moves.append(moved)
                affected.append(target.character_id)
    result = {'hits':hits, '_moves':moves, '_affected_character_ids':list(dict.fromkeys(affected))}
    if local:
        result['_vision_location_id'] = actor.location_id
    return result
