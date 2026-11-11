"""Раздельные отсеки, тайники в земле и прикопанные контейнеры."""
import math

from app.extensions import db, socketio
from app.models import LobbyParticipant, LocationObject
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.location_navigation import contains, level_of

CONTAINER_TYPES = {'box', 'military_crate', 'chest', 'household_wood_crate'}
HOST_TYPES = {'household_stump', 'household_hollow_tree', 'household_fallen_tree',
              'household_wood_stove', 'household_sauna_stove'}
DIGGABLE_TERRAIN = {'grass', 'sand', 'bare_dirt', 'dirt', 'mud', 'muddy_tracks',
                    'brown_mud', 'brown_mud_02', 'brown_mud_03', 'dry_mud_field_001'}


def digging_tool(item):
    return (isinstance(item, dict) and item.get('category') in {'tool', 'misc', 'weapon', 'melee_weapon'}
            and 'лопат' in str(item.get('name') or '').casefold()
            and type(item.get('quantity', 1)) is int and item.get('quantity', 1) > 0)


def masking_record(user_id, difficulty, owner_id, controlled_by=None):
    users = {user_id, owner_id, controlled_by} - {None}
    return {'dc':difficulty, 'known_users':sorted(users)}


def compartment_visible(obj, user_id):
    props = obj.properties or {}
    compartment = props.get('stash_compartment')
    return bool(compartment and (user_id == obj.location.lobby.gm_id
                or not props.get('gm_hidden') and user_id in compartment['known_users']))


def buried(obj):
    return ((obj.properties or {}).get('stash_mask') or {}).get('mode') == 'buried'


def require_ground(actor, x, y):
    location = actor.location
    if (type(x) is not int or type(y) is not int or not 0 <= x < location.grid_width
            or not 0 <= y < location.grid_height or (actor.floor_level or 0) != 0
            or math.hypot(x-actor.pos_x,y-actor.pos_y) > 1.5):
        raise ValidationError('Подойдите к клетке на уровне земли')
    tiles = location.tiles_data or []
    tile = tiles[y][x] if y < len(tiles) and x < len(tiles[y]) else {}
    if (tile or {}).get('terrain', 'grass') not in DIGGABLE_TERRAIN:
        raise ValidationError('Тайник можно закопать в грязи, песке или траве')
    if any(o.type == 'floor' and level_of(o) == 0 and contains(o,x+.5,y+.5)
           for o in LocationObject.query.filter_by(location_id=location.id)):
        raise ValidationError('Здесь настелен пол. Для тайника нужен открытый грунт')


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
    if not obj or not visible(obj, user_id):
        raise NotFoundError('Контейнер недоступен')
    if (obj.properties or {}).get('gm_hidden') and user_id != actor.location.lobby.gm_id:
        raise NotFoundError('Контейнер недоступен')
    if payload.get('stash_revision') != obj.revision:
        raise ConflictError('Контейнер изменился')
    if level_of(obj) != (actor.floor_level or 0) or math.hypot(obj.tile_x-actor.pos_x, obj.tile_y-actor.pos_y) > 1.5:
        raise ValidationError('Подойдите к контейнеру на том же этаже')
    if payload.get('operation') == 'dig_up_stash':
        if not buried(obj):
            raise ValidationError('Этот ящик не закопан')
        return obj, 'dig_up'
    if obj.type in HOST_TYPES:
        if (obj.properties or {}).get('stash_compartment') and not compartment_visible(obj,user_id):
            raise ValidationError('Не удалось устроить тайник в этом месте')
        return obj, 'compartment'
    if obj.type not in CONTAINER_TYPES or buried(obj):
        raise ValidationError('Здесь нельзя устроить тайник')
    require_ground(actor, obj.tile_x, obj.tile_y)
    return obj, 'buried'


def conceal(obj, actor, user_id, difficulty, mode):
    props = {**(obj.properties or {})}
    mask = masking_record(user_id, difficulty, actor.character.owner_id, actor.controlled_by)
    if mode == 'compartment':
        contents = (props.get('stash_compartment') or {}).get('contents', [])
        props['stash_compartment'] = {**mask, 'contents':contents}
    else:
        mask['mode'] = mode
        if mode == 'buried':
            keys = ('blocks_movement', 'passable', 'cover_enabled')
            mask['before'] = {key:props[key] for key in keys if key in props}
            props.update(blocks_movement=False, passable=True, cover_enabled=False)
        props['stash_mask'] = mask
    obj.properties = props


def ground_stash(actor, x, y):
    obj = LocationObject(location_id=actor.location_id, type='ground_stash', name='Тайник в земле',
        tile_x=x, tile_y=y, properties={'contents':[], 'level':0, 'passable':True, 'cover_enabled':False,
        'dimensions':{'width':.72, 'depth':.6, 'height':.035}})
    db.session.add(obj)
    return obj


def excavate(obj):
    props = dict(obj.properties)
    previous = props.pop('stash_mask')['before']
    for key in ('blocks_movement', 'passable', 'cover_enabled'):
        props.pop(key, None)
    obj.properties = {**props, **previous}


def discover(actor, user_id, x, y, check):
    if check['roll'] == 1:
        return []
    found = []
    objects = LocationObject.query.filter_by(location_id=actor.location_id, tile_x=x, tile_y=y).all()
    for obj in objects:
        props = obj.properties or {}
        if props.get('gm_hidden') or level_of(obj) != (actor.floor_level or 0):
            continue
        updates = {}
        for key in ('stash_mask', 'stash_compartment'):
            mask = props.get(key)
            if mask and user_id not in mask['known_users'] and (check['roll'] == 20 or check['total'] >= mask['dc']):
                updates[key] = {**mask, 'known_users':[*mask['known_users'], user_id]}
        if updates:
            obj.properties = {**props, **updates}
            found.append(obj.id)
    return found


def project(obj, user_id, *, reveal_locked=False):
    from app.schemas.location import LocationObjectSchema

    snapshot = LocationObjectSchema(reveal_locked=reveal_locked, reveal_stashes=True).dump(obj)
    if not reveal_locked and compartment_visible(obj, user_id):
        snapshot['properties']['stash_contents'] = obj.properties['stash_compartment']['contents']
    return snapshot


def publish(location_id, ids):
    if not ids:
        return
    objects = LocationObject.query.filter(LocationObject.id.in_(set(ids)), LocationObject.location_id == location_id).all()
    for obj in objects:
        for participant in LobbyParticipant.query.filter_by(lobby_id=obj.location.lobby_id, is_banned=False).all():
            user_id = participant.user_id
            allowed = visible(obj, user_id)
            view = project(obj, user_id, reveal_locked=user_id == obj.location.lobby.gm_id) if allowed else None
            socketio.emit('location_object_updated' if allowed else 'location_object_deleted',
                          {'location_id':location_id, 'object_id':obj.id, 'object':view},
                          room=f'user_{user_id}')
