"""Смена позиции снайпера после проваленной проверки Воли."""
import math

from sqlalchemy.orm.attributes import flag_modified
from app.models import LocationCombatState
from app.services.exceptions import ValidationError


def status(actor, state=None):
    from app.services.character_rules import meta

    pending = meta(actor).get('sniperOldCover')
    if not pending or pending.get('location_id', actor.location_id) != actor.location_id:
        return None
    state = state or LocationCombatState.query.filter_by(location_id=actor.location_id).first()
    if not state or state.status != 'active' or (state.round_number or 1) > pending.get('expires_after_round', 0):
        return None
    return {'blocked': True, 'expires_after_round': pending['expires_after_round']}


def require_reposition(actor, state=None):
    if status(actor, state):
        raise ValidationError('Снайпер: потратьте все ОП хода на движение в одном направлении или дождитесь конца следующего раунда')


def record_move(actor, state, path, mode, cost):
    from app.services.character_rules import meta

    if not status(actor, state):
        return
    points = path.get('path') or []
    current = meta(actor)
    pending = current['sniperOldCover']
    round_number = state.round_number or 1
    progress = pending.get('movement') or {}
    if progress.get('round') != round_number:
        progress = {'round': round_number, 'mode': mode, 'spent': 0}
        pending['movement'] = progress
    if progress.get('mode') != mode or mode == 'correction':
        progress['interrupted'] = True
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        dx, dy = dx / length, dy / length
        ax, ay = progress.setdefault('direction', [dx, dy])
        if abs(ax * dy - ay * dx) > 1e-6 or ax * dx + ay * dy <= 0:
            progress['interrupted'] = True
            break
    progress['spent'] += cost
    maximum = actor.movement_points_max or 0
    if not progress.get('interrupted') and progress.get('direction') and maximum > 0 and progress['spent'] + 1e-8 >= maximum:
        current.pop('sniperOldCover', None)
    flag_modified(actor.character, 'data')


def expire(location_id,state):
    from app.models import LocationCharacter
    from app.services.character_rules import meta
    for actor in LocationCharacter.query.filter_by(location_id=location_id).filter(
        LocationCharacter.id.in_(state.turn_order or []),
    ).all():
        if not actor.character:continue
        current=meta(actor);pending=current.get('sniperOldCover')
        if pending and pending.get('location_id',location_id)==location_id and (state.status!='active' or (state.round_number or 1)>pending.get('expires_after_round',0)):
            current.pop('sniperOldCover',None);flag_modified(actor.character,'data')
