from app.services.water_profiles import WATER_PROFILES
import logging

from flask import has_request_context, request
from flask_socketio import emit, join_room, leave_room
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import socketio, db
from app.models import LobbyParticipant, LobbyCharacter
from app.models.location import Location
from app.models.location_character import LocationCharacter
from app.services.combat import CombatService
from app.services.location_water import freeze_water_offsets
from app.services.character_roles import is_npc
from app.schemas.location import LocationObjectSchema
from app.models.location_object import LocationObject
from app.services.exceptions import ServiceError
from app.services.effects import normalize_effect_list, sync_health_derived_statuses
from app.services.character_appearance import public_character_appearance
from app.sockets.utils import get_user_from_token

logger = logging.getLogger(__name__)


def _find_existing_location_character(location_id, character_id):
    if not character_id:
        return None
    return LocationCharacter.query.filter_by(
        location_id=location_id,
        character_id=character_id,
    ).order_by(
        LocationCharacter.last_action.desc().nullslast(),
        LocationCharacter.id.desc(),
    ).first()


@socketio.on('join_location')
def handle_join_location(data):
    token = data.get('token')
    location_id = data.get('location_id')
    character_id = data.get('character_id')
    if not token or not location_id:
        return

    user = get_user_from_token(token)
    if not user:
        emit('error', {'message': 'Invalid token'}, room=request.sid)
        return

    location = Location.query.get(location_id)
    if not location:
        emit('error', {'message': 'Location not found'}, room=request.sid)
        return

    participant = LobbyParticipant.query.filter_by(
        lobby_id=location.lobby_id,
        user_id=user.id,
    ).first()
    if not participant or participant.is_banned or not location.lobby.is_active:
        emit('error', {'message': 'Not in lobby'}, room=request.sid)
        return

    loc_char = None
    if character_id:
        character = LobbyCharacter.query.get(character_id)
        if not character:
            character_id = None
        else:
            loc_char = _find_existing_location_character(
                location_id,
                character_id,
            )

            is_gm = location.lobby.gm_id == user.id
            is_controller = bool(loc_char and loc_char.controlled_by == user.id)
            if loc_char and (
                character.owner_id == user.id
                or is_controller
                or is_gm
            ):
                if loc_char.character and isinstance(loc_char.character.data, dict):
                    health = loc_char.character.data.get('health')
                    if isinstance(health, dict):
                        loc_char.effects = normalize_effect_list(
                            health.get('effects') or []
                        )
                        sync_health_derived_statuses(health)
                        flag_modified(loc_char, 'effects')
                        db.session.commit()
            else:
                # Joining a sublocation never creates or claims a model.
                loc_char = None
                character_id = None

    join_room(f"location_{location_id}")
    emit(
        'joined_location',
        {
            'location_id': location_id,
            'character_id': character_id if loc_char else None,
            'x': loc_char.pos_x if loc_char else 0,
            'y': loc_char.pos_y if loc_char else 0,
            **(CombatService.physical_position(loc_char) if loc_char else {'floor_level': 0}),
        },
        room=request.sid,
    )

    all_chars = LocationCharacter.query.filter_by(location_id=location_id).all()
    state = []
    for lc in all_chars:
        character = lc.character
        if not character:
            continue
        state.append({
            'character_id': character.id,
            'name': CombatService._display_name(character),
            'owner_id': character.owner_id,
            'controlled_by': lc.controlled_by,
            'team_name': lc.team_name,
            'team_color': lc.team_color,
            'facing_x': lc.facing_x,
            'facing_y': lc.facing_y,
            'owner_username': character.owner.username if character.owner else None,
            'hp_zones': lc.hp_zones,
            'effects': lc.effects,
            'x': lc.pos_x,
            'y': lc.pos_y,
            **CombatService.physical_position(lc),
            'status': lc.status,
            'posture': lc.posture,
            'prone_on_back': bool(lc.prone_on_back),
            'condition_state': CombatService._location_character_condition(lc)['state'],
            'is_mutant': CombatService._is_mutant_character(
                character.data if isinstance(character.data, dict) else {}
            ),
            'mutant_type': CombatService._mutant_profile_name(character.data),
            'is_npc': is_npc(character),
            'gm_hidden': bool(lc.gm_hidden),
            **({'appearance': appearance} if not lc.gm_hidden and (appearance :=
                public_character_appearance(character.data)) else {}),
        })
    emit('location_state', state, room=request.sid)

    combat_state = CombatService.get_state(location_id, user.id)
    emit('combat_state', combat_state, room=request.sid)
    from .map_tools import sync_measurements
    sync_measurements(location.lobby_id, request.sid)


@socketio.on('leave_location')
def handle_leave_location(data):
    token = data.get('token')
    location_id = data.get('location_id')
    character_id = data.get('character_id')
    if not token or not location_id:
        return

    user = get_user_from_token(token)
    if not user:
        return

    room = f"location_{location_id}"
    leave_room(room)
    emit('left_location', {'character_id': character_id}, room=request.sid)


@socketio.on('move_in_location')
def handle_move_in_location(data):
    token = data.get('token')
    location_id = data.get('location_id')
    character_id = data.get('character_id')
    new_x = data.get('x')
    new_y = data.get('y')
    special_action = data.get('special_action')
    object_id = data.get('object_id')
    climb_mode = data.get('climb_mode')
    movement_mode = data.get('movement_mode')
    if not all([token, location_id, character_id, new_x is not None, new_y is not None]):
        return

    user = get_user_from_token(token)
    if not user:
        emit('error', {'message': 'Invalid token'}, room=request.sid)
        return

    try:
        moved_character, cost, combat_state = CombatService.move_character(
            location_id,
            user.id,
            character_id,
            new_x,
            new_y,
            special_action,
            object_id,
            climb_mode,
            movement_mode,
            evasion=data.get('evasion'),
            floor_level=data.get('floor_level'),
            climb_payment=data.get('climb_payment'),
        )
    except ServiceError as exc:
        db.session.rollback()
        current_character = LocationCharacter.query.filter_by(
            location_id=location_id,
            character_id=character_id,
        ).first()
        emit(
            'movement_rejected',
            {
                'character_id': character_id,
                'x': current_character.pos_x if current_character else None,
                'y': current_character.pos_y if current_character else None,
                **(CombatService.physical_position(current_character) if current_character else {}),
                'message': str(exc),
            },
            room=request.sid,
        )
        return

    emit(
        'character_moved',
        {
            'character_id': character_id,
            'x': moved_character.pos_x,
            'y': moved_character.pos_y,
            **CombatService.physical_position(moved_character),
            'path': getattr(moved_character, '_movement_surfaces', []),
            'fall': getattr(moved_character, '_movement_fall', None),
            'slip_checks': getattr(moved_character, '_movement_slip_checks', []),
            'slip_stopped': getattr(moved_character, '_movement_slip_stopped', False),
            'movement_cost': cost,
            'movement_mode': getattr(moved_character, '_movement_mode', movement_mode),
            'movement_animation': getattr(moved_character, '_movement_animation', None),
            'free_run': getattr(moved_character, '_movement_free_run', False),
            'facing_x': moved_character.facing_x,
            'facing_y': moved_character.facing_y,
        },
        room=f"location_{location_id}",
    )
    revealed = getattr(moved_character, '_revealed_anomaly', None)
    from app.services.traps import publish as publish_traps,publish_effects as publish_trap_effects
    publish_traps(location_id,getattr(moved_character,'_trap_ids',[]))
    publish_trap_effects(getattr(moved_character,'_trap_results',[]))
    if revealed:
        if revealed['object_id']:
            obj = db.session.get(LocationObject, revealed['object_id'])
            if obj:
                emit('location_object_updated', {
                    'location_id': location_id, 'object': LocationObjectSchema().dump(obj),
                }, room=f'location_{location_id}')
        else:
            location = db.session.get(Location, location_id)
            x, y = revealed['tile_x'], revealed['tile_y']
            tile = location.tiles_data[y][x]
            emit('location_tiles_updated', {
                'location_id': location_id,
                'updates': [{'x': x, 'z': y, 'objects': tile.get('objects', [])}],
            }, room=f'location_{location_id}')
    if moved_character.grapple_target_id:
        captive = db.session.get(
            LocationCharacter,
            moved_character.grapple_target_id,
        )
        if captive:
            emit(
                'character_moved',
                {
                    'character_id': captive.character_id,
                    'x': captive.pos_x,
                    'y': captive.pos_y,
                    **CombatService.physical_position(captive),
                    'movement_cost': 0,
                    'movement_mode': movement_mode,
                    'moved_with_grapple': True,
                },
                room=f"location_{location_id}",
            )

    body = getattr(moved_character, '_transported_body', None)
    if body:
        emit('character_moved', {'character_id': body.character_id, 'x': body.pos_x, 'y': body.pos_y,
             **CombatService.physical_position(body), 'movement_cost': 0, 'moved_with_transport': True},
             room=f'location_{location_id}')
    if combat_state:
        socketio.emit('combat_state_updated', combat_state, room=f"location_{location_id}")


@socketio.on('update_location_tiles')
def handle_update_location_tiles(data):
    token = data.get('token')
    location_id = data.get('location_id')
    updates = data.get('updates', [])
    if not token or not location_id or not updates:
        return

    user = get_user_from_token(token)
    if not user:
        return

    location = Location.query.get(location_id)
    if not location:
        return

    if location.lobby.gm_id != user.id:
        emit('error', {'message': 'Only GM can edit location tiles'}, room=request.sid)
        return

    if any('water_profile' in u and (not isinstance(u['water_profile'], str) or u['water_profile'] not in WATER_PROFILES) for u in updates):
        emit('error', {'message': 'Invalid water profile'}, room=request.sid)
        return

    tiles = location.tiles_data
    if not isinstance(tiles, list):
        tiles = []
    freeze_water_offsets(tiles)
    changed = []

    for upd in updates:
        x = upd.get('x')
        z = upd.get('z')
        if x is None or z is None:
            continue
        if 0 <= z < len(tiles) and 0 <= x < len(tiles[0]):
            tile = dict(tiles[z][x])
            terrain_changed = False
            subtype_changed = False
            height_changed = False
            objects_changed = False
            radiation_changed = False
            profile_changed = False

            if 'terrain' in upd and tile.get('terrain') != upd['terrain']:
                tile.pop('water_profile', None)
                tile['terrain'] = upd['terrain']
                if tile['terrain'] == 'water':
                    tile['water_level_offset'] = 0
                else:
                    tile.pop('water_level_offset', None)
                terrain_changed = True
            if ('water_profile' in upd and tile.get('terrain') == 'water'
                    and tile.get('water_profile', 'auto') != upd['water_profile']):
                tile['water_profile'] = upd['water_profile']
                profile_changed = True
            offset = upd.get('water_level_offset')
            if (tile.get('terrain') == 'water' and type(offset) in (int, float)
                    and offset in (0, -.2) and tile.get('water_level_offset') != offset):
                tile['water_level_offset'] = offset
                terrain_changed = True
            if 'grass_subtype' in upd and tile.get('terrain') == 'grass':
                subtype = 'forest' if upd['grass_subtype'] == 'forest' else 'plain'
                if tile.get('grass_subtype') != subtype:
                    tile['grass_subtype'] = subtype
                    subtype_changed = True
            elif tile.get('terrain') != 'grass' and 'grass_subtype' in tile:
                del tile['grass_subtype']
                subtype_changed = True
            if 'height' in upd and tile.get('height') != upd['height']:
                tile['height'] = upd['height']
                height_changed = True
            if 'objects' in upd and tile.get('objects') != upd['objects']:
                tile['objects'] = upd['objects']
                objects_changed = True
            if 'radiation' in upd and tile.get('radiation') != upd['radiation']:
                tile['radiation'] = upd['radiation']
                radiation_changed = True

            if terrain_changed or subtype_changed or height_changed or objects_changed or radiation_changed or profile_changed:
                tiles[z][x] = tile
                changed.append({
                    'x': x,
                    'z': z,
                    'terrain': tile.get('terrain'),
                    'grass_subtype': tile.get('grass_subtype'),
                    'height': tile.get('height'),
                    'water_level_offset': tile.get('water_level_offset'),
                    'water_profile': tile.get('water_profile', 'auto'),
                    'objects': tile.get('objects'),
                    'radiation': tile.get('radiation'),
                })

    if changed:
        location.tiles_data = tiles
        from app.services.weather_rules import unhide_tile
        weather_revealed=False
        for cell in changed:
            weather_revealed=unhide_tile(location,tiles[cell['z']][cell['x']],cell['x'],cell['z']) or weather_revealed
            cell['objects']=tiles[cell['z']][cell['x']].get('objects')
        flag_modified(location, "tiles_data")
        db.session.commit()
        socketio.emit('location_tiles_updated', {
            'location_id': location_id,
            'updates': changed,
        }, room=f"location_{location_id}",
            skip_sid=getattr(request, 'sid', None) if has_request_context() and not weather_revealed else None)
        logger.info(f"Location {location_id} tiles updated by GM {user.id}")
