"""Publish sheet changes only to subscribers who still have access."""

from app.extensions import db, socketio
from app.models import Lobby, LobbyCharacter, LocationCharacter
from app.services.character import CharacterService
from app.services.exceptions import ServiceError


_subscriptions = {}


def register_subscription(sid, character_id, user_id):
    _subscriptions.setdefault(sid, {})[character_id] = user_id


def forget_subscription(sid, character_id=None):
    if character_id is None:
        _subscriptions.pop(sid, None)
        return
    subscriptions = _subscriptions.get(sid, {})
    subscriptions.pop(character_id, None)
    if not subscriptions:
        _subscriptions.pop(sid, None)


def emit_character_update(payload, *, skip_sid=None, snapshot_projector=None):
    character_id = payload['character_id']
    character = db.session.get(LobbyCharacter, character_id)
    if character is None:
        return
    if 'data' in payload.get('updates', {}):
        snapshot = character.data_snapshot()
        if snapshot_projector is not None:
            snapshot = snapshot_projector(snapshot)
        payload = {**payload, 'updates': {**payload['updates'], 'data': snapshot}}
    room = f'character_{character_id}'
    participants = list(socketio.server.manager.get_participants('/', room))
    for sid, _ in participants:
        user_id = _subscriptions.get(sid, {}).get(character_id)
        try:
            CharacterService.check_access(character, user_id)
        except ServiceError:
            socketio.server.leave_room(sid, room, namespace='/')
            forget_subscription(sid, character_id)
    socketio.emit('character_data_updated', payload, room=room, skip_sid=skip_sid)
    data = character.data if isinstance(character.data, dict) else {}
    basic = data.get('basic') if isinstance(data.get('basic'), dict) else {}
    if 'data' not in payload.get('updates', {}) or not (
        data.get('is_mutant') or basic.get('is_mutant')
    ):
        return
    health = data.get('health') if isinstance(data.get('health'), dict) else {}
    lobby = db.session.get(Lobby, character.lobby_id)
    recipients = {character.owner_id, lobby.gm_id}
    recipients.update(character.visible_to or [])
    recipients.update(character.editable_to or [])
    recipients.update(
        model.controlled_by for model in LocationCharacter.query.filter_by(character_id=character.id).all()
        if model.controlled_by
    )
    health_update = {
        'lobby_id': character.lobby_id,
        'character_id': character.id,
        'health': {'current': health.get('current'), 'max': health.get('max')},
    }
    for user_id in recipients:
        try:
            CharacterService.check_access(character, user_id)
        except ServiceError:
            continue
        socketio.emit('mutant_health_updated', health_update, room=f'user_{user_id}')


def publish_character_save(result, updates, user_id, *, skip_sid=None):
    character = result.character
    saved_updates = {key: value for key, value in updates.items() if key not in {'_manual_fields', '_save_id', '_base_data'}}
    for key in ('data', 'visible_to', 'editable_to'):
        if key in saved_updates:
            saved_updates[key] = getattr(character, key)
    if 'editable_to' in saved_updates:
        saved_updates['visible_to'] = character.visible_to
    emit_character_update({
        'character_id': character.id,
        'updates': saved_updates,
        'updated_by': user_id,
        'save_id': updates.get('_save_id'),
    }, skip_sid=skip_sid)
    if 'data' not in updates:
        return

    from app.services.combat import CombatService

    lobby = db.session.get(Lobby, character.lobby_id)
    for posture_update in result.posture_updates:
        socketio.emit('location_character_posture_updated', posture_update,
                      room=f"location_{posture_update['location_id']}")
    for model in LocationCharacter.query.filter_by(character_id=character.id).all():
        socketio.emit('combat_state_updated',
                      CombatService.get_state(model.location_id, lobby.gm_id),
                      room=f'location_{model.location_id}')
