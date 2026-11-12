# app/sockets/auth.py
import logging
import threading
from flask import request
from flask_socketio import join_room, leave_room, emit
from app.extensions import socketio
from app.models import LobbyParticipant, ChatMessage
from .utils import get_user_from_token

logger = logging.getLogger(__name__)

sid_to_user = {}
sid_to_username = {}
sid_to_lobby = {}
pending_auth = {}
AUTH_TIMEOUT = 10


def lobby_has_online_users(lobby_id):
    return any(
        str(connected_lobby_id) == str(lobby_id) and sid in sid_to_user
        for sid, connected_lobby_id in sid_to_lobby.items()
    )


def user_is_online_in_lobby(user_id, lobby_id):
    return any(
        connected_user_id == user_id and str(sid_to_lobby.get(sid)) == str(lobby_id)
        for sid, connected_user_id in sid_to_user.items()
    )

@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')

    def timeout_disconnect(sid):
        if sid in pending_auth:
            logger.warning(f"Client {sid} timed out waiting for authentication")
            socketio.server.disconnect(sid, namespace='/')
            pending_auth.pop(sid, None)

    timer = threading.Timer(AUTH_TIMEOUT, timeout_disconnect, args=[request.sid])
    timer.daemon = True
    pending_auth[request.sid] = timer
    timer.start()

@socketio.on('disconnect')
def handle_disconnect(reason=None):
    from .map_tools import clear_measurement
    clear_measurement(request.sid)
    from app.services.character_events import forget_subscription
    forget_subscription(request.sid)
    # Отменяем таймер аутентификации, если он ещё не сработал
    if request.sid in pending_auth:
        pending_auth[request.sid].cancel()
        del pending_auth[request.sid]

    user_id = sid_to_user.pop(request.sid, None)
    username = sid_to_username.pop(request.sid, None)
    lobby_id = sid_to_lobby.pop(request.sid, None)
    if user_id:
        still_online = any(
            other_user_id == user_id and sid_to_lobby.get(sid) == lobby_id
            for sid, other_user_id in sid_to_user.items()
        )
        if lobby_id and not still_online:
            emit('user_left', {
                'user_id': user_id,
                'username': username,
            }, room=f"lobby_{lobby_id}")
            logger.info(f"User {user_id} left lobby {lobby_id}")
    logger.info('Client disconnected (reason=%s)', reason or 'unknown')

@socketio.on('authenticate')
def handle_authenticate(data):
    token = data.get('token')
    lobby_id = data.get('lobby_id')
    if not token or not lobby_id:
        return

    # Отменяем таймер аутентификации, если клиент успел прислать данные
    if request.sid in pending_auth:
        pending_auth[request.sid].cancel()
        del pending_auth[request.sid]

    user = get_user_from_token(token)
    if not user:
        logger.warning("Authentication failed: invalid token")
        emit('error', {'message': 'Invalid token'})
        return

    # Проверяем, не забанен ли пользователь
    participant = LobbyParticipant.query.filter_by(lobby_id=lobby_id, user_id=user.id).first()
    if not participant:
        logger.warning(f"User {user.id} tried to authenticate in lobby {lobby_id} but is not a participant")
        emit('error', {'message': 'You are not in this lobby'})
        return
    if participant.is_banned:
        logger.warning(f"Banned user {user.id} tried to authenticate in lobby {lobby_id}")
        emit('error', {'message': 'You are banned from this lobby'})
        return

    # Сохраняем информацию о подключении
    from .map_tools import clear_measurement, sync_measurements
    if sid_to_lobby.get(request.sid) not in (None, lobby_id):
        clear_measurement(request.sid)
    sid_to_user[request.sid] = user.id
    sid_to_username[request.sid] = user.username
    sid_to_lobby[request.sid] = lobby_id

    join_room(f"lobby_{lobby_id}")
    join_room(f"user_{user.id}")  # личная комната для кика

    emit('authenticated', {
        'username': user.username,
        'is_gm': participant.lobby.gm_id == user.id,
    }, room=request.sid)
    logger.info(f"User {user.id} ({user.username}) authenticated in lobby {lobby_id}")

    # Оповещаем всех в комнате о новом участнике
    emit('user_joined', {
        'user_id': user.id,
        'username': user.username,
        'color': user.color,
    }, room=f"lobby_{lobby_id}")

    # Отправляем новому участнику список текущих онлайн-пользователей
    online_users = sorted({
        sid_to_user[sid]
        for sid, connected_lobby_id in sid_to_lobby.items()
        if connected_lobby_id == lobby_id and sid in sid_to_user
    })
    emit('online_users', online_users, room=request.sid)
    sync_measurements(lobby_id, request.sid)

    # Загружаем историю чата
    messages = ChatMessage.query.filter_by(lobby_id=lobby_id).order_by(ChatMessage.timestamp.desc()).limit(50).all()
    messages.reverse()
    history = [{'id': msg.id, 'username': msg.username, 'message': msg.message, 'timestamp': msg.timestamp.isoformat()} for msg in messages]
    emit('chat_history', history, room=request.sid)
