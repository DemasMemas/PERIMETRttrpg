"""Обычный прыжок: серверный план, оплата через общий механизм и исход проверки."""
import math
from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCharacter, LocationObject
from app.services.exceptions import ValidationError
from app.services.location_navigation import ground, level_of, opening_at, surface

EPS = 1e-8


def _meta(actor):
    data = getattr(getattr(actor,'character',None),'data',None) or {}
    value = (data.get('health') or {}).get('combatMeta')
    return value if isinstance(value,dict) else {}


def balance_state(actor):
    value = _meta(actor).get('unstableJump')
    return value if isinstance(value, dict) and value.get('position') == [actor.pos_x,actor.pos_y,actor.floor_level or 0] else None


def require_balance(actor):
    if balance_state(actor):
        raise ValidationError('Сначала восстановите равновесие на краю: Ловкость СЛ12, бесплатно')


def _boxes(geometry):
    return geometry.boxes + [entry[0] for entry in geometry.climb_boxes]


def object_support_height(actor, fallback):
    """Опора на предмете сохраняется после reload и сверяется с самим предметом."""
    from app.services.combat import CombatService
    saved = _meta(actor).get('jumpSupport') or {}
    edge = balance_state(actor)
    if edge:
        return edge['height']
    if not isinstance(saved,dict) or saved.get('position') != [actor.pos_x, actor.pos_y, actor.floor_level or 0]:
        return fallback
    if saved.get('object_id'):
        obj = db.session.get(LocationObject, saved['object_id'])
        objects = [obj] if obj and obj.location_id == actor.location_id else []
    else:
        objects = LocationObject.query.filter_by(location_id=actor.location_id).all()
    geometry = CombatService._movement_wall_geometry(actor.location, objects)
    p = (actor.pos_x+.5,actor.pos_y+.5)
    return max((box.top for box in _boxes(geometry)
        if box.level == (actor.floor_level or 0) and box.intersects_sweep(p,p)
        and abs(box.top-saved.get('height',-math.inf)) < EPS), default=fallback)


def _scene(location, actor):
    from app.services.combat import CombatService
    objects = LocationObject.query.filter_by(location_id=location.id).all()
    geometry = CombatService._movement_wall_geometry(location, objects)
    others = [c for c in CombatService._unique_location_characters(
        LocationCharacter.query.filter_by(location_id=location.id).all()) if c.character_id != actor.character_id]
    return objects,geometry,others


def _landing(location, objects, geometry, x, y, maximum):
    candidates = []
    for level in {0} | {level_of(o) for o in objects}:
        node = surface(location,objects,x,y,level)
        if node and (node.get('stair') or not opening_at(objects,level,x+.5,y+.5)):
            candidates.append({k:node[k] for k in ('x','y','level','height')})
    p = (x+.5,y+.5)
    for box in _boxes(geometry):
        if box.intersects_sweep(p,p):
            candidates.append(dict(x=x,y=y,level=box.level,height=box.top,object_id=box.object_id))
    available = [n for n in candidates if n['height'] <= maximum+EPS]
    if not available:
        raise ValidationError('Нет поверхности приземления в допустимом перепаде высоты')
    landing = max(available,key=lambda n:n['height'])
    if any(box.intersects_sweep(p,p) and box.bottom < landing['height']+1.7-EPS
           and box.top > landing['height']+EPS for box in _boxes(geometry)):
        raise ValidationError('Место приземления перекрыто препятствием')
    return landing


def prepare_jump(location, actor, x, y, origin=None):
    from app.services.combat import CombatService
    from app.services.character_interaction import CharacterInteractionService
    CombatService.ensure_character_can_act(actor,'jump')
    require_balance(actor)
    if _meta(actor).get('trappedNet'):
        raise ValidationError('Сначала освободитесь из сети или разрежьте её')
    if CombatService._is_mutant_character(actor.character.data):
        raise ValidationError('Для мутанта используйте его специальную способность прыжка')
    if CombatService._posture_key(actor) != 'standing' or actor.grappled_by_id or actor.grapple_target_id:
        raise ValidationError('Для прыжка нужно встать и освободиться от захвата')
    if CharacterInteractionService.movement_locked(actor.id):
        raise ValidationError('Нельзя прыгать во время лечения')
    if _meta(actor).get('activeAnomaly'):
        raise ValidationError('Сначала выберитесь из аномалии')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (x,y)):
        raise ValidationError('Некорректная точка прыжка')
    if not (0 <= x <= location.grid_width-1 and 0 <= y <= location.grid_height-1):
        raise ValidationError('Точка прыжка за пределами локации')
    position = [actor.pos_x,actor.pos_y,actor.floor_level or 0]
    if origin and origin.get('position') != position:
        raise ValidationError('Точка начала оплаченного прыжка изменилась')
    mode = origin['movement_mode'] if origin else actor.movement_mode_this_turn or 'walk'
    profile = CombatService._jump_profile(actor.character.data,mode,actor.action_points_current)
    if profile['running']:
        CombatService._validate_equipment_movement(actor.character.data,mode)
    distance = math.hypot(x-actor.pos_x,y-actor.pos_y)
    if distance <= EPS or distance > profile['max_distance']+EPS:
        raise ValidationError('Выбранная дальность превышает максимальную дальность прыжка')
    objects,geometry,others = _scene(location,actor)
    start = dict(x=actor.pos_x,y=actor.pos_y,level=position[2],height=CombatService.physical_position(actor)['height'])
    landing = _landing(location,objects,geometry,x,y,start['height']+1)
    steps = max(1,math.ceil(distance/.2))
    if any(ground(location,start['x']+(x-start['x'])*i/steps,start['y']+(y-start['y'])*i/steps) > start['height']+1+EPS for i in range(1,steps+1)):
        raise ValidationError('Перепад рельефа на траектории превышает 1 м вверх')
    a,b = (start['x']+.5,start['y']+.5),(x+.5,y+.5)
    for box in _boxes(geometry):
        if box.intersects_sweep(a,b) and box.bottom < max(start['height'],landing['height'])+1.7-EPS and box.top > start['height']+1+EPS:
            raise ValidationError('Траектория прыжка перекрыта препятствием выше допустимого 1 м')
    for other in others:
        from app.services.movement_geometry import CharacterCircle
        height = CombatService.physical_position(other)['height']
        circle = CharacterCircle(other.pos_x+.5,other.pos_y+.5,other.floor_level or 0)
        blocks_air = (height+1.7 > start['height']+EPS and height < max(start['height'],landing['height'])+1.7-EPS)
        blocks_landing = height+1.7 > landing['height']+EPS and height < landing['height']+1.7-EPS
        if blocks_air and circle.intersects_sweep(a,b) or blocks_landing and circle.intersects_sweep(b,b):
            raise ValidationError('Траектория или место приземления заняты персонажем')
    return dict(origin=dict(position=position,movement_mode=mode),start=start,landing=landing,
                distance=distance,profile=profile)


def _place(location, actor, point, start, round_number, *, unstable=None):
    from app.services.combat import CombatService
    data = deepcopy(actor.character.data)
    meta = data.setdefault('health',{}).setdefault('combatMeta',{})
    meta.pop('unstableJump',None)
    meta.pop('jumpSupport',None)
    if unstable:
        meta['unstableJump'] = {**unstable,'position':[point['x'],point['y'],point['level']],'height':point['height']}
    if 'object_id' in point:
        meta['jumpSupport'] = dict(position=[point['x'],point['y'],point['level']],
                                   height=point['height'],object_id=point['object_id'])
    actor.character.data = data
    flag_modified(actor.character,'data')
    actor.pos_x,actor.pos_y,actor.floor_level = point['x'],point['y'],point['level']
    actor.cover_object_id = None
    actor.weapon_braced = False
    actor.braced_weapon_index = None
    CombatService._clear_aim(actor)
    fall_height = max(0,start['height']-point['height'])
    fall = CombatService.resolve_fall(actor,fall_height,round_number=round_number) if fall_height > EPS else None
    actor._revealed_anomaly = None
    CombatService._sync_anomaly_after_relocation(location,actor,(start['x'],start['y']))
    anomalies = CombatService._anomalies_at_tile(location,point['x'],point['y'],floor_level=point['level'])
    if anomalies and not _meta(actor).get('activeAnomaly'):
        CombatService._enter_anomaly(actor,anomalies[0],round_number,previous_position=(start['x'],start['y']))
    CombatService._apply_periodic_health_effects(actor,phase='movement_end')
    CombatService._sync_location_effects_from_character(actor)
    from app.models import LocationCombatState
    from app.services.traps import after_landing
    after_landing(actor,LocationCombatState.query.filter_by(location_id=actor.location_id).first())
    return fall


def _check(actor, difficulty):
    from app.services.combat import CombatService
    help_bonus = CombatService._consume_help_advantage(actor,'skills.physical.agility')
    data = deepcopy(actor.character.data)
    check = CombatService._narrative_skill_check(data,'skills.physical.agility',advantage=bool(help_bonus))
    if help_bonus:
        check['help'] = help_bonus
    check.update(difficulty=difficulty,success=check['total'] >= difficulty)
    actor.character.data = data
    flag_modified(actor.character,'data')
    return check


def execute_jump(location, actor, plan, round_number):
    objects,geometry,_ = _scene(location,actor)
    start,planned = plan['start'],plan['landing']
    check = _check(actor,plan['profile']['difficulty'])
    deficit = check['difficulty']-check['total']
    outcome = 'success' if deficit <= 0 else ('unstable_edge' if deficit <= 3 and not plan['profile']['running'] else 'midpoint')
    point = planned
    unstable = None
    if outcome == 'midpoint':
        x,y = (start['x']+planned['x'])/2,(start['y']+planned['y'])/2
        point = _landing(location,objects,geometry,x,y,start['height']+1)
    elif outcome == 'unstable_edge':
        unstable = dict(start=start,landing=planned)
    fall = _place(location,actor,point,start,round_number,unstable=unstable)
    return dict(outcome=outcome,check=check,profile=plan['profile'],start=start,
                planned_landing=planned,landing=point,fall=fall,distance=plan['distance'],path=animation_path(start,point),
                traps=getattr(actor,'_trap_results',[]),trap_ids=getattr(actor,'_trap_ids',[]),
                revealed_anomaly=getattr(actor,'_revealed_anomaly',None))


def recover_balance(location, actor, round_number):
    from app.services.combat import CombatService
    saved = balance_state(actor)
    if not saved:
        raise ValidationError('Персонаж уже находится в устойчивом положении')
    check = _check(actor,12)
    start,planned = saved['start'],saved['landing']
    current = dict(x=actor.pos_x,y=actor.pos_y,level=actor.floor_level or 0,
                   height=CombatService.physical_position(actor)['height'])
    x,y = planned['x'],planned['y']
    if not check['success']:
        distance = math.hypot(x-start['x'],y-start['y'])
        x,y = x-.5*(x-start['x'])/distance,y-.5*(y-start['y'])/distance
    if not (0<=x<=location.grid_width-1 and 0<=y<=location.grid_height-1):
        raise ValidationError('Точка оступания за пределами локации')
    objects,geometry,_ = _scene(location,actor)
    point = _landing(location,objects,geometry,x,y,current['height']+1)
    fall = _place(location,actor,point,current,round_number)
    return dict(outcome='balanced' if check['success'] else 'stumbled',check=check,
                start=current,landing=point,fall=fall,action_points=0,path=animation_path(current,point),
                traps=getattr(actor,'_trap_results',[]),trap_ids=getattr(actor,'_trap_ids',[]),
                revealed_anomaly=getattr(actor,'_revealed_anomaly',None))


def animation_path(start, landing):
    """Дуга — только визуализация; игровые ограничения проверяются до броска."""
    points = [{**start}]
    for i in range(1,9):
        t = i/8
        points.append(dict(x=start['x']+(landing['x']-start['x'])*t,
            y=start['y']+(landing['y']-start['y'])*t,level=start['level'],
            height=start['height']+max(0,landing['height']-start['height'])*t+.5*math.sin(math.pi*t)))
    if landing['height'] < start['height']-EPS:
        points.extend({**landing,'height':start['height']+(landing['height']-start['height'])*i/4} for i in range(1,4))
    points.append({**landing})
    return points
