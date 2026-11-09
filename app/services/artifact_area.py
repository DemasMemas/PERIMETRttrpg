"""Особые свойства артефактов с центром в позиции носителя."""
from copy import deepcopy
import math
from types import SimpleNamespace

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationObject, WorldGroup
from app.services.exceptions import ValidationError
from app.services.explosion_geometry import distance

LEECH = 'Пиявка'
FLASH = 'Кристалл статики'
STORM = 'Штормовой камень'
FAN = 'Алхимический веер'
AREA_EFFECTS = {LEECH, FLASH, STORM}


def group_characters(character):
    if character is None:
        raise ValidationError('Для этого особого свойства нужен персонаж на подлокации или в группе')
    for group in WorldGroup.query.filter_by(lobby_id=character.lobby_id).order_by(WorldGroup.id):
        ids = {int(value) for value in group.member_character_ids or [] if str(value).isdigit()}
        if character.id in ids:
            return LobbyCharacter.query.filter(LobbyCharacter.lobby_id == character.lobby_id,
                                               LobbyCharacter.id.in_(ids)).all()
    return []


def location_members(actor, radius, *, objects=None):
    from app.services.combat import CombatService
    if objects is None:
        objects = LocationObject.query.filter_by(location_id=actor.location_id).all()
    people = CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=actor.location_id).all())
    return [person for person in people if person.character and distance(actor.location, objects, actor.pos_x, actor.pos_y,
                     actor.floor_level or 0, person) <= radius + 1e-9]


def fan_protects(data, target=None, *, character=None):
    from app.services.artifact_special import stat_value
    if stat_value(data, 'crystal_cloud'):
        return True
    if target is not None and getattr(target, 'location_id', None):
        rows = LocationCharacter.query.filter_by(location_id=target.location_id).filter(
            LocationCharacter.character_id != target.character_id,
            LocationCharacter.pos_x.between(target.pos_x-3, target.pos_x+3),
            LocationCharacter.pos_y.between(target.pos_y-3, target.pos_y+3),
        ).all()
        carriers = [person for person in rows if person.character and stat_value(person.character.data, 'crystal_cloud')]
        if not carriers:
            return False
        objects = LocationObject.query.filter_by(location_id=target.location_id).all()
        return any(distance(target.location, objects, target.pos_x, target.pos_y, target.floor_level or 0, person) <= 3 + 1e-9
            for person in carriers)
    elif character is not None:
        carriers = (member for member in group_characters(character) if member.id != character.id)
    else:
        return False
    return any(stat_value(member.data, 'crystal_cloud') for member in carriers)


def _storm(actor, characters, objects):
    from app.services.electronics import expose_devices
    from app.services.location_navigation import level_of, pose
    affected, changes = [], {}
    for character in characters:
        data = deepcopy(character.data or {})
        if expose_devices(data, 0, forced=True):
            character.data = data
            flag_modified(character, 'data')
            affected.append(character.id)
    for obj in objects:
        props = obj.properties or {}
        stationary = props.get('electronic') is True or obj.type in {'electronic', 'device'}
        if not (stationary or props.get('contents') or isinstance(props.get('device'), dict)):
            continue
        x, y, *_ = pose(obj)
        point = SimpleNamespace(pos_x=x-.5, pos_y=y-.5, floor_level=level_of(obj))
        if distance(actor.location, objects, actor.pos_x, actor.pos_y, actor.floor_level or 0, point) > 3 + 1e-9:
            continue
        updated = deepcopy(props)
        items = updated.get('contents') or []
        if isinstance(updated.get('device'), dict):
            items = [*items, updated['device']]
        elif stationary:
            items = [*items, updated]
            updated['category'] = 'device'
        if expose_devices({'inventory': {'pockets': items}}, 0, forced=True):
            obj.properties = updated
            changes[obj.id] = 'updated'
    extra = {'_object_changes': changes, '_vision_location_id': actor.location_id} if objects else {}
    return 0, affected, extra


def apply(actor, data, name):
    from app.services.combat import CombatService
    local = isinstance(actor, LocationCharacter)
    owner = getattr(actor, 'character', None)
    objects = LocationObject.query.filter_by(location_id=actor.location_id).all() if local else []
    models = location_members(actor, 5 if name == FLASH else 3, objects=objects) if local else []
    characters = [target.character for target in models] if local else group_characters(owner)
    targets = [character for character in characters if character.id != owner.id]
    if name == STORM:
        return _storm(actor, targets, objects)
    targets = [character for character in targets if CombatService._character_condition(character.data)['state'] != 'dead']
    if name == FLASH and local:
        results = CombatService._resolve_flash(actor.location_id, actor.pos_x, actor.pos_y,
            {'blindness': 100, 'radius': 5, 'source': 'artifact_static_crystal'}, floor_level=actor.floor_level or 0,
            target_ids={model.id for model in models if model.character in targets})
        return 0, [row['character_id'] for row in results if row['blindness'] > 0], {}
    health = data.setdefault('health', {})
    removed = max(0, CombatService._coerce_float(health.get('radiation'), 0)) if name == LEECH else 0
    if name == LEECH:
        health['radiation'] = 0
    affected = []
    for target in targets:
        target_data = deepcopy(target.data or {})
        target_health = target_data.setdefault('health', {})
        if name == FLASH:
            from app.services.effects import apply_effect_to_health
            blindness = CombatService._received_flash(target_data, 100, 0, 1)
            if blindness <= 0:
                continue
            seconds = math.ceil(blindness/20)*6
            apply_effect_to_health(target_health, {'type':'blindness', 'value':blindness, 'tick':'time_elapsed',
                'time_unit':'second', 'remaining':seconds, 'remaining_seconds':seconds,
                'source':'artifact_static_crystal'}, character_data=target_data)
        else:
            if CombatService._coerce_float(target_health.get('radiation'), 0) <= 0:
                continue
            target_health['radiation'] = 0
        target.data = target_data
        flag_modified(target, 'data')
        affected.append(target.id)
    return removed, affected, {}


def publish(result):
    from app.services.character_events import emit_character_update
    ids = result.pop('_affected_character_ids', [])
    ground_changes = result.pop('_object_changes', {})
    forecast = result.pop('_gm_forecast', None)
    moves = result.pop('_moves', [])
    if result.get('replayed'):
        return
    if forecast:
        from app.extensions import socketio
        gm_id = forecast.pop('gm_id')
        socketio.emit('artifact_forecast_requested', forecast, room=f'user_{gm_id}')
    if moves:
        from app.extensions import socketio
        from app.services.traps import publish as publish_traps
        location_id = result['_vision_location_id']
        for moved in moves:
            publish_traps(location_id, moved.pop('trap_ids', []))
            if not moved.pop('gm_hidden'):
                socketio.emit('character_moved', {'location_id':location_id, **moved}, room=f'location_{location_id}')
    if ids:
        for character in LobbyCharacter.query.filter(LobbyCharacter.id.in_(ids)).all():
            emit_character_update({'character_id': character.id, 'updates': {'data': character.data_snapshot()},
                                   'source': 'artifact_special'})
    if ground_changes:
        from app.extensions import socketio
        from app.schemas import LocationObjectSchema
        location_id = result['_vision_location_id']
        for object_id, event in ground_changes.items():
            payload = {'location_id': location_id}
            room = f'location_{location_id}'
            if event == 'deleted':
                payload['object_id'] = object_id
            else:
                obj = db.session.get(LocationObject, object_id)
                if not obj:
                    continue
                payload['object'] = LocationObjectSchema().dump(obj)
                if (obj.properties or {}).get('gm_hidden'):
                    room = f'user_{obj.location.lobby.gm_id}'
            socketio.emit(f'location_object_{event}', payload, room=room)
