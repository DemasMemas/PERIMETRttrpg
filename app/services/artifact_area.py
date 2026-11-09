"""Особые свойства артефактов с центром в позиции носителя."""
from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationObject
from app.services.exceptions import ValidationError
from app.services.explosion_geometry import distance

LEECH = 'Пиявка'
FLASH = 'Кристалл статики'
AREA_EFFECTS = {LEECH, FLASH}


def apply(actor, data, name):
    from app.services.combat import CombatService
    if not isinstance(actor, LocationCharacter):
        raise ValidationError('Для этого особого свойства нужен персонаж на подлокации')
    people = CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=actor.location_id).all())
    targets = [person for person in people if person.character and person.character_id != actor.character_id
               and CombatService._character_condition(person.character.data)['state'] != 'dead']
    level = actor.floor_level or 0
    if name == FLASH:
        results = CombatService._resolve_flash(actor.location_id, actor.pos_x, actor.pos_y,
            {'blindness': 100, 'radius': 5, 'source': 'artifact_static_crystal'}, floor_level=level,
            target_ids={target.id for target in targets})
        return 0, [row['character_id'] for row in results if row['blindness'] > 0]

    objects = LocationObject.query.filter_by(location_id=actor.location_id).all()
    health = data.setdefault('health', {})
    removed = max(0, CombatService._coerce_float(health.get('radiation'), 0))
    health['radiation'] = 0
    affected = []
    for target in targets:
        if distance(actor.location, objects, actor.pos_x, actor.pos_y, level, target) > 3 + 1e-9:
            continue
        target_data = deepcopy(target.character.data or {})
        target_health = target_data.setdefault('health', {})
        if CombatService._coerce_float(target_health.get('radiation'), 0) <= 0:
            continue
        target_health['radiation'] = 0
        target.character.data = target_data
        flag_modified(target.character, 'data')
        affected.append(target.character_id)
    return removed, affected


def publish(result):
    from app.services.character_events import emit_character_update
    ids = result.pop('_affected_character_ids', [])
    ground_changes = result.pop('_magnet_ground_changes', {})
    if result.get('replayed'):
        return
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
            if event == 'deleted':
                payload['object_id'] = object_id
            else:
                obj = db.session.get(LocationObject, object_id)
                if not obj:
                    continue
                payload['object'] = LocationObjectSchema().dump(obj)
            socketio.emit(f'location_object_{event}', payload, room=f'location_{location_id}')
