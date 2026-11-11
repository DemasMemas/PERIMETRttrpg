"""Маскировка и обнаружение обычных контейнеров на подлокации."""
import math

from app.extensions import db, socketio
from app.models import LobbyParticipant, LocationObject
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.location_navigation import level_of

CONTAINER_TYPES = {'box', 'military_crate', 'chest'}


def visible(obj, user_id):
    props = obj.properties or {}
    mask = props.get('stash_mask')
    return (not mask or user_id == obj.location.lobby.gm_id
            or not props.get('gm_hidden') and user_id in mask['known_users'])


def prepare(actor, payload, user_id):
    identity = payload.get('stash_id')
    if type(identity) is not int:
        raise ValidationError('Выберите контейнер для маскировки')
    obj = LocationObject.query.filter_by(id=identity, location_id=actor.location_id).with_for_update().first()
    if not obj or obj.type not in CONTAINER_TYPES or not visible(obj, user_id):
        raise NotFoundError('Контейнер недоступен')
    if (obj.properties or {}).get('gm_hidden') and user_id != actor.location.lobby.gm_id:
        raise NotFoundError('Контейнер недоступен')
    if payload.get('stash_revision') != obj.revision:
        raise ConflictError('Контейнер изменился')
    if level_of(obj) != (actor.floor_level or 0) or math.hypot(obj.tile_x-actor.pos_x, obj.tile_y-actor.pos_y) > 1.5:
        raise ValidationError('Подойдите к контейнеру на том же этаже')
    return obj


def conceal(obj, actor, user_id, difficulty):
    users = {user_id, actor.character.owner_id, actor.controlled_by} - {None}
    obj.properties = {**(obj.properties or {}), 'stash_mask':{'dc':difficulty, 'known_users':sorted(users)}}


def discover(actor, user_id, x, y, check):
    if check['roll'] == 1:
        return []
    found = []
    objects = LocationObject.query.filter_by(location_id=actor.location_id, tile_x=x, tile_y=y).all()
    for obj in objects:
        props = obj.properties or {}
        mask = props.get('stash_mask')
        if not mask or props.get('gm_hidden') or level_of(obj) != (actor.floor_level or 0):
            continue
        if user_id in mask['known_users'] or check['roll'] != 20 and check['total'] < mask['dc']:
            continue
        obj.properties = {**props, 'stash_mask':{**mask, 'known_users':[*mask['known_users'], user_id]}}
        found.append(obj.id)
    return found


def publish(location_id, ids):
    if not ids:
        return
    from app.schemas.location import LocationObjectSchema

    objects = LocationObject.query.filter(LocationObject.id.in_(set(ids)), LocationObject.location_id == location_id).all()
    for obj in objects:
        for participant in LobbyParticipant.query.filter_by(lobby_id=obj.location.lobby_id, is_banned=False).all():
            user_id = participant.user_id
            allowed = visible(obj, user_id)
            view = LocationObjectSchema(reveal_locked=user_id == obj.location.lobby.gm_id, reveal_stashes=True).dump(obj) if allowed else None
            socketio.emit('location_object_updated' if allowed else 'location_object_deleted',
                          {'location_id':location_id, 'object_id':obj.id, 'object':view},
                          room=f'user_{user_id}')
