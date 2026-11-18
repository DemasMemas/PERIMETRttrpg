"""Персональные три секунды до взрыва, без добавления ресурсов хода."""
from copy import deepcopy
import math

from flask import has_app_context
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db

from app.models import LocationCharacter,LocationCombatState,Location,LocationObject
from app.services.exceptions import ValidationError


def window(actor):
    return (((actor.character.data or {}).get('health') or {}).get('combatMeta') or {}).get('grenadeReaction') or {}


def _meta(actor):
    return actor.character.data.setdefault('health',{}).setdefault('combatMeta',{})


def prepare_event(state,event):
    from app.services.combat import CombatService
    people=CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=state.location_id).all())
    from app.services.explosion_choice import candidates
    event.update(trigger='short_fuse',remaining_actor_ids=[c.id for c in people if c.id in (state.turn_order or [])])
    event['gm_choices']=candidates(state,event)
    return event


def _resolve_for_actor(state,event,actor):
    from app.services.combat import CombatService
    alive=CombatService._character_condition(actor.character.data or {})['state']!='dead'
    result=CombatService.resolve_explosion(state.location_id,event['x'],event['y'],event['profile'],
        round_number=state.round_number,floor_level=event.get('floor_level',0),target_ids=[actor.id],
        damage_objects=False,create_area=False,live_shield_ids=event.get('blast_casualties',[]))
    if alive and CombatService._character_condition(actor.character.data or {})['state']=='dead':
        event.setdefault('blast_casualties',[]).append(actor.id)
    return {'item_name': event['item_name'], 'detonated': True,
            'impact': {'x': event['x'], 'y': event['y']}, 'explosion': result}


def _finish_events(state,events):
    from app.services.combat import CombatService
    remaining=[];detonations=[];original_ids={event.get('id') for event in events}
    present={c.id for c in LocationCharacter.query.filter_by(location_id=state.location_id).all()}
    for event in events:
        if event.get('trigger')=='short_fuse':
            event['remaining_actor_ids']=[i for i in event.get('remaining_actor_ids',[]) if i in present]
        if event.get('trigger')!='short_fuse' or event.get('remaining_actor_ids') or event.get('gm_choices'):
            remaining.append(event);continue
        result=CombatService.resolve_explosion(state.location_id,event['x'],event['y'],event['profile'],
            round_number=state.round_number,floor_level=event.get('floor_level',0),target_ids=[])
        if result.get('area'):
            result['area']['excluded_actor_ids']=event.get('excluded_actor_ids',[])
            state.area_effects=[*(state.area_effects or []),result['area']]
        detonations.append({'item_name':event['item_name'],'detonated':True,
            'impact':{'x':event['x'],'y':event['y']},'explosion':result})
    state.pending_explosives=remaining+[event for event in state.pending_explosives or [] if event.get('id') not in original_ids]
    return detonations


def begin(actor,state=None):
    from app.services.combat import CombatService
    if not has_app_context() or not getattr(actor,'location_id',None):
        return []
    state=state or LocationCombatState.query.filter_by(location_id=actor.location_id).first()
    if not state or state.status!='active' or state.current_location_character_id!=actor.id or state.reaction_return_location_character_id is not None or window(actor):
        return []
    if any(e.get('gm_choices') for e in state.pending_explosives or []):
        return []
    events=deepcopy(state.pending_explosives or []);waiting=[];detonations=[]
    location=db.session.get(Location,state.location_id)
    objects=LocationObject.query.filter_by(location_id=state.location_id).all()
    for event in events:
        if event.get('trigger')!='short_fuse':
            continue
        for other_id in list(event.get('remaining_actor_ids',[])):
            if other_id in (state.turn_order or []):
                continue
            # Удалённый из инициативы участник также требует решения ГМа.
            event['remaining_actor_ids'].remove(other_id)
            other=LocationCharacter.query.filter_by(id=other_id,location_id=state.location_id).first()
            if other:
                from app.services.explosion_choice import candidates
                if other_id in candidates(state,event):
                    event.setdefault('gm_choices',[]).append(other_id)
        if event.get('gm_choices'):
            continue
        if actor.id not in event.get('remaining_actor_ids',[]):
            continue
        from app.services.noise import affected_ids
        within=actor.id in affected_ids(location,objects,event,[actor])
        if within and CombatService._can_take_combat_turn(actor):
            waiting.append(event['id']);continue
        event['remaining_actor_ids'].remove(actor.id)
        if within:
            detonations.append(_resolve_for_actor(state,event,actor))
        else:
            event.setdefault('skipped_actor_ids', []).append(actor.id)
    state.pending_explosives=events
    if waiting:
        held_ap=max(0,actor.action_points_current-2)
        held_op=max(0,actor.movement_points_current)/2
        _meta(actor)['grenadeReaction']={'event_ids':waiting,'held_action_points':held_ap,'held_movement_points':held_op}
        actor.action_points_current-=held_ap
        actor.movement_points_current-=held_op
        flag_modified(actor.character,'data')
    detonations.extend(_finish_events(state,events))
    return detonations


def split_movement_gain(actor,gain):
    active=window(actor)
    if not active:
        return gain
    active['held_movement_points']+=gain/2
    flag_modified(actor.character,'data')
    return gain/2


def redirect(actor, state, choice, target_x, target_y, target_floor_level):
    from app.services.artifact_effects import artifact_passive_profile
    from app.services.brigadier_order import boost_roll
    from app.services.character_traits import trait_count
    from app.services.combat import CombatService as C
    from app.services.explosion_geometry import distance, landing_level, stop_at_wall
    from app.services.weather_rules import at_character

    if not isinstance(choice, dict) or set(choice) != {'event_id', 'mode'} or choice['mode'] not in ('kick', 'throw'):
        raise ValidationError('Выберите гранату и способ перемещения')
    mode = choice['mode']
    active = window(actor)
    events = deepcopy(state.pending_explosives or [])
    event = next((item for item in events if item['id'] == choice['event_id']), None)
    if not event or event['trigger'] not in {'short_fuse', 'actor_turn_start', 'round_start'} or (
        event['trigger'] == 'short_fuse' and event['id'] not in active.get('event_ids', [])
    ):
        raise ValidationError('Эту гранату сейчас нельзя переместить')
    if actor.id in event.get('redirect_refused_actor_ids', []):
        raise ValidationError('После провала Воли эту гранату переместить нельзя')
    if actor.action_points_current < 2:
        raise ValidationError('Для перемещения гранаты нужно 2 ОД')

    location = actor.location
    objects = LocationObject.query.filter_by(location_id=location.id).all()
    level = actor.floor_level or 0
    if (event.get('floor_level', 0) != level
            or distance(location, objects, event['x'], event['y'], level, actor) > 1.5 + 1e-7):
        raise ValidationError('Граната должна быть на том же этаже в пределах 1,5 м')
    if (target_floor_level not in (None, level)
            or type(target_x) not in (int, float) or type(target_y) not in (int, float)
            or not (0 <= target_x <= location.grid_width - 1 and 0 <= target_y <= location.grid_height - 1)):
        raise ValidationError('Выберите направление на своём этаже')

    data = actor.character.data
    stopped_at_wall = False
    if mode == 'kick':
        dx, dy = target_x - event['x'], target_y - event['y']
        length = math.hypot(dx, dy)
        if length <= 1e-7:
            raise ValidationError('Выберите направление от гранаты')
        strength = max(0, C._skill_value(data, 'skills.physical.strength'))
        x, y = event['x'] + dx / length * strength, event['y'] + dy / length * strength
        stopped_x, stopped_y = stop_at_wall(location, objects, event['x'], event['y'], x, y, level)
        stopped_at_wall = (stopped_x, stopped_y) != (x, y)
        x, y = stopped_x, stopped_y
        if not (0 <= x <= location.grid_width - 1 and 0 <= y <= location.grid_height - 1):
            raise ValidationError('В выбранном направлении граната вылетит за пределы подлокации')
    else:
        if not C._has_usable_free_hand(actor):
            raise ValidationError('Чтобы откинуть гранату, нужна свободная действующая рука')
        maximum = C._coerce_float(event['profile'].get('projectile_range'), 0) + artifact_passive_profile(data)['throw_range_bonus']
        x, y, _ = C._clamp_projectile_point(actor.pos_x, actor.pos_y, target_x, target_y, maximum)
        throw_distance = math.hypot(x - actor.pos_x, y - actor.pos_y)
        weather = at_character(location, actor)
        throw_difficulty = C._hand_throw_difficulty(location.id, actor, data, x, y, throw_distance)['difficulty'] - weather['accuracy']
    if landing_level(location, objects, x, y, level) is None:
        raise ValidationError('На выбранной траектории нет поверхности для гранаты')

    actor.action_points_current -= 2
    bonus = 3 * trait_count(data, 'Вернуть отправителю')
    checks = []
    for skill, label, difficulty in (('will', 'Воля', 10), ('agility', 'Ловкость', 14)):
        check = C._narrative_skill_check(data, f'skills.physical.{skill}', roll_bonus=bonus)
        check.update(label=label, difficulty=difficulty, success=not check.get('automatic_failure') and check['total'] >= difficulty)
        checks.append(check)
        if not check['success']:
            if skill == 'will':
                event.setdefault('redirect_refused_actor_ids', []).append(actor.id)
            break

    moved = len(checks) == 2 and checks[-1]['success']
    if moved and mode == 'throw':
        rolls, roll, order_bonus = boost_roll(data, [], disadvantage=weather['accuracy_disadvantage'])
        total = roll + order_bonus + bonus
        failure = max(0, throw_difficulty - total)
        checks.append({'label': 'Метание', 'rolls': rolls, 'roll': roll, 'total': total,
                       'difficulty': throw_difficulty, 'success': failure == 0})
        if failure:
            x, y = C._scatter_point(x, y, failure, location.grid_width, location.grid_height,
                                   location=location, floor_level=level)
    immediate_targets = []
    if moved:
        impact_level = landing_level(location, objects, x, y, level)
        if impact_level is not None:
            event.update(x=x, y=y, floor_level=impact_level)
        else:
            moved = False
    if moved and event['trigger'] == 'short_fuse':
        from app.services.explosion_choice import candidates
        from app.services.noise import affected_ids

        excluded = set(event.get('excluded_actor_ids', []))
        event['gm_choices'] = [identity for identity in candidates(state, event) if identity not in excluded]
        skipped = event.get('skipped_actor_ids', [])
        eligible = set(skipped).intersection(state.turn_order or []) - excluded - set(event['remaining_actor_ids'])
        if eligible:
            people = {person.id: person for person in LocationCharacter.query.filter_by(location_id=state.location_id)
                      .filter(LocationCharacter.id.in_(eligible))}
            affected = affected_ids(location, objects, event, people.values())
            event['skipped_actor_ids'] = [identity for identity in skipped if identity not in affected]
            immediate_targets = [people[identity] for identity in skipped if identity in affected]
    state.pending_explosives = events
    detonations = [_resolve_for_actor(state, event, person) for person in immediate_targets]
    if detonations:
        flag_modified(state, 'pending_explosives')
    flag_modified(actor.character, 'data')
    return {'name': 'Пнуть гранату' if mode == 'kick' else 'Откинуть гранату', 'action_points': 2,
            'checks': checks, 'moved': moved, 'stopped_at_wall': moved and stopped_at_wall,
            'event_id': event['id'], 'bonus': bonus,
            'impact': {'x': event['x'], 'y': event['y'], 'floor_level': event.get('floor_level', 0)}}, detonations


def finish(actor,state):
    active=window(actor)
    if not active:
        return []
    events=deepcopy(state.pending_explosives or []);detonations=[]
    for event in events:
        if event.get('id') not in active['event_ids'] or actor.id not in event.get('remaining_actor_ids',[]):
            continue
        event['remaining_actor_ids'].remove(actor.id)
        detonations.append(_resolve_for_actor(state,event,actor))
    actor.action_points_current+=active['held_action_points']
    actor.movement_points_current+=active['held_movement_points']
    _meta(actor).pop('grenadeReaction',None)
    flag_modified(actor.character,'data')
    detonations.extend(_finish_events(state,events))
    return detonations
