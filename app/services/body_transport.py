"""Переноска недееспособной цели: реальные связи персонажей и общий маршрут."""
import math
from copy import deepcopy
from app.services.action_journal import journaled

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCharacter, LocationCombatState
from app.services.exceptions import ValidationError


def meta(character):
    owner = getattr(character, 'character', None)
    return (((owner.data if owner else {}) or {}).get('health') or {}).get('combatMeta') or {}


def preserve(current, incoming):
    old = ((current or {}).get('health') or {}).get('combatMeta') or {}
    new = incoming.setdefault('health', {}).setdefault('combatMeta', {})
    for key in ('bodyTransport', 'transportedBy'):
        if key in old:
            new[key] = deepcopy(old[key])
        else:
            new.pop(key, None)


def state(character):
    value = meta(character).get('bodyTransport')
    return value if isinstance(value, dict) else {}


def _write(character, key, value):
    data = deepcopy(character.character.data or {})
    values = data.setdefault('health', {}).setdefault('combatMeta', {})
    if value is None:
        values.pop(key, None)
    else:
        values[key] = value
    character.character.data = data
    flag_modified(character.character, 'data')


def eligible(character):
    from app.services.combat import CombatService
    return CombatService._location_character_condition(character)['state'] in {'dead', 'critical', 'pain_shock'}


def companion(character):
    link = state(character)
    target = LocationCharacter.query.filter_by(location_id=character.location_id,
                                              character_id=link.get('target_character_id')).first() if link else None
    if target and meta(target).get('transportedBy') == character.character_id:
        return target
    return None


def penalty(character):
    from app.services.combat import CombatService
    target = companion(character)
    if not target:
        return 0
    weight = CombatService._movement_penalty_breakdown(target.character.data)['weight']
    return weight if state(character)['mode'] == 'shoulder' else 1 + weight / 2 if state(character)['mode'] == 'rope' else 2 + weight


def has_rope(data):
    from app.lobbies import _iter_carried_items, BODY_CARRY_ROPE_NAME
    return any((str(item.get('name') or '').strip().casefold() == BODY_CARRY_ROPE_NAME
                or (item.get('attributes') or {}).get('body_carry_rope'))
               and float(item.get('quantity', 1) or 0) > 0 for item in _iter_carried_items(data))


def _release(actor, target):
    _write(actor, 'bodyTransport', None)
    if target and meta(target).get('transportedBy') == actor.character_id:
        _write(target, 'transportedBy', None)


def validate_move(actor):
    """Пробуждение не позволяет продолжить переноску сознательного персонажа."""
    carried_by = meta(actor).get('transportedBy')
    if carried_by and not eligible(actor):
        carrier = LocationCharacter.query.filter_by(location_id=actor.location_id, character_id=carried_by).first()
        if carrier and companion(carrier) is actor:
            _release(carrier, actor)
    target = companion(actor)
    if not target:
        if state(actor):
            _release(actor, None)
        return None
    if not eligible(target):
        _release(actor, target)
        return None
    if state(actor).get('mode') == 'rope' and not has_rope(actor.character.data):
        raise ValidationError('Канат для переноски больше недоступен. Сначала отсоедините тело.')
    return target


def ensure_firearm_allowed(actor):
    if state(actor).get('mode') == 'shoulder' and companion(actor):
        raise ValidationError('Нельзя пользоваться огнестрельным оружием, пока тело находится на плече.')


def move_plan(actor, path):
    """Тело идёт по уже проверенной траектории, а не срезает угол позади носильщика."""
    target = companion(actor)
    if not target:
        return None
    points = path.get('surfaces') or [{'x': x, 'y': y, 'level': actor.floor_level or 0} for x, y in path['path']]
    if state(actor)['mode'] == 'shoulder':
        return target, dict(points[-1]), points
    trail = state(actor).get('trail') or [
        {'x': target.pos_x, 'y': target.pos_y, 'level': target.floor_level or 0},
        {'x': actor.pos_x, 'y': actor.pos_y, 'level': actor.floor_level or 0},
    ]
    trail = trail + [dict(point) for point in points[1:]]
    gap = max(.5, float(state(actor).get('gap', 1)))
    lengths = [math.hypot(b['x'] - a['x'], b['y'] - a['y']) for a, b in zip(trail, trail[1:])]
    travel = max(0, sum(lengths) - gap)
    traversed = [trail[0]]
    for index, (a, b, length) in enumerate(zip(trail, trail[1:], lengths)):
        if travel >= length - 1e-8:
            traversed.append(b)
            travel -= length
            continue
        ratio = travel / length if length else 0
        point = {'x': a['x'] + (b['x'] - a['x']) * ratio,
                 'y': a['y'] + (b['y'] - a['y']) * ratio,
                 'level': a['level'] if ratio < 1 else b['level']}
        traversed.append(point)
        return target, point, [point, *trail[index + 1:]]
    return target, dict(trail[-1]), [dict(trail[-1])]


def finish_move(actor, plan, location):
    if not plan:
        return
    from app.services.combat import CombatService
    target, point, trail = plan
    previous = (target.pos_x, target.pos_y)
    target.pos_x, target.pos_y, target.floor_level = point['x'], point['y'], point['level']
    target.cover_object_id = None
    target.weapon_braced = False
    target.braced_weapon_index = None
    if state(actor).get('mode') != 'shoulder':
        _write(actor, 'bodyTransport', {**state(actor), 'trail': trail})
    CombatService._sync_anomaly_after_relocation(location, target, previous)
    CombatService._sync_location_effects_from_character(target)


@journaled(kind='body_transport',actor_key='actor_id')
def change(location_id, user_id, actor_id, target_character_id, mode):
    from app.services.character_interaction import CharacterInteractionService
    from app.services.character_traits import trait_count
    from app.services.combat import CombatService
    location, actor, target = CharacterInteractionService._pair(location_id, user_id, actor_id, target_character_id)
    # Единый порядок блокировки для двух одновременных попыток взять одно тело.
    LocationCharacter.query.filter(LocationCharacter.id.in_([actor.id, target.id])).order_by(LocationCharacter.id).with_for_update().populate_existing().all()
    db.session.refresh(actor.character)
    db.session.refresh(target.character)
    previous_carrier_id = meta(target).get('transportedBy')
    if previous_carrier_id:
        previous_carrier = LocationCharacter.query.filter_by(location_id=location_id, character_id=previous_carrier_id).first()
        if not previous_carrier or companion(previous_carrier) is not target:
            _write(target, 'transportedBy', None)
        elif eligible(previous_carrier):
            _release(previous_carrier, target)
    validate_move(actor)
    link = state(actor)
    if mode == 'stop':
        if not link:
            return actor, target, 0
        if companion(actor) is not target:
            raise ValidationError('Этот персонаж переносит другую цель.')
        cost = 1 if link['mode'] == 'rope' else 0
        drop = None
        if link['mode'] == 'shoulder':
            candidates = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
            candidates.sort(key=lambda p: -(p[0] * (actor.facing_x or 0) + p[1] * (actor.facing_y or 1)))
            for dx, dy in candidates:
                path = CombatService._find_movement_path(location, actor.pos_x, actor.pos_y,
                    actor.pos_x + dx, actor.pos_y + dy, actor.character_id,
                    ignored_character_ids=[actor.character_id, target.character_id])
                if path and not path.get('climb_cost') and path.get('distance', 0) <= 1.5:
                    drop = path['surfaces'][-1]
                    break
            if drop is None:
                raise ValidationError('Рядом нет свободного безопасного места, чтобы опустить тело.')
    else:
        if mode not in {'drag', 'rope', 'shoulder'}:
            raise ValidationError('Неизвестный способ переноски.')
        if link:
            if link.get('target_character_id') == target.character_id and link['mode'] == mode:
                return actor, target, 0
            raise ValidationError('Сначала закончите текущую переноску.')
        if meta(actor).get('transportedBy') or meta(target).get('transportedBy') or state(target):
            raise ValidationError('Персонаж уже участвует в переноске.')
        if actor.grapple_target_id or actor.grappled_by_id or target.grapple_target_id or target.grappled_by_id:
            raise ValidationError('Сначала завершите захват.')
        if not eligible(target):
            raise ValidationError('Можно переносить только мёртвого или бессознательного персонажа. Для сознательного нужен захват.')
        if not CombatService._is_adjacent(actor, target):
            raise ValidationError('Подойдите к цели на соседнюю клетку одного этажа.')
        bridge = CombatService._find_movement_path(location, target.pos_x, target.pos_y,
            actor.pos_x, actor.pos_y, target.character_id,
            ignored_character_ids=[actor.character_id, target.character_id])
        gap = math.hypot(target.pos_x - actor.pos_x, target.pos_y - actor.pos_y)
        if not bridge or bridge.get('climb_cost') or bridge.get('distance', 0) > gap + .05:
            raise ValidationError('Между персонажами находится препятствие.')
        if mode == 'shoulder' and not trait_count(actor.character.data, 'Санитар'):
            raise ValidationError('Для переноски на плече нужна черта «Санитар».')
        if mode == 'rope' and not has_rope(actor.character.data):
            raise ValidationError('Нужен канат для переноски.')
        cost = 1 if mode == 'rope' else 2
        drop = None
    combat = LocationCombatState.query.filter_by(location_id=location_id, status='active').first()
    if combat:
        if actor.action_points_current < cost:
            raise ValidationError('Не хватает ОД для переноски.')
        actor.action_points_current -= cost
    if mode == 'stop':
        if drop:
            target.pos_x, target.pos_y, target.floor_level = drop['x'], drop['y'], drop['level']
        _release(actor, target)
    else:
        gap = math.hypot(target.pos_x - actor.pos_x, target.pos_y - actor.pos_y)
        _write(actor, 'bodyTransport', {'mode': mode, 'target_character_id': target.character_id, 'gap': gap})
        _write(target, 'transportedBy', actor.character_id)
        if mode == 'shoulder':
            target.pos_x, target.pos_y, target.floor_level = actor.pos_x, actor.pos_y, actor.floor_level
    db.session.commit()
    return actor, target, cost if combat else 0


def missing_templates(existing, subcategory=None):
    if subcategory or any(t.get('category') == 'misc' and (
        str(t.get('name') or '').casefold() == 'канат для переноски'
        or (t.get('attributes') or {}).get('body_carry_rope')) for t in existing):
        return []
    return [{'id': -31001, 'name': 'Канат для переноски', 'category': 'misc', 'subcategory': None,
             'price': 1000, 'weight': .5, 'volume': 4, 'compatible_ids': [],
             'description': 'Позволяет тащить мёртвого или бессознательного персонажа. Штраф перемещения: 1 + половина Штрафа веса цели. Присоединить или отсоединить — 1 ОД.',
             'attributes': {'body_carry_rope': True}}]
