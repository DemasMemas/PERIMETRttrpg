# app/sockets/chat.py
import logging
from datetime import datetime, timezone
from flask import request
from flask_socketio import emit
from app.extensions import socketio, db
from app.models import ChatMessage
from .utils import get_user_from_token
from app.utils.dice import roll_dice as roll_dice_util
from app.services.character import CharacterService
from app.services.exceptions import ServiceError

logger = logging.getLogger(__name__)

@socketio.on('send_message')
def handle_message(data):
    token = data.get('token')
    lobby_id = data.get('lobby_id')
    raw_message = data.get('message')
    if not token or not lobby_id or not raw_message or not isinstance(raw_message,str):
        return

    user = get_user_from_token(token)
    if not user:
        logger.warning("Message send attempt with invalid token")
        emit('error', {'message': 'Invalid token'})
        return

    try:
        CharacterService.require_membership(lobby_id,user.id)
    except ServiceError:
        emit('error',{'message':'Нет доступа к чату комнаты'},room=request.sid)
        return

    # Проверяем, является ли сообщение командой
    final_text = raw_message
    if raw_message.startswith('/roll'):
        parts = raw_message.split(' ', 1)
        if len(parts) == 2:
            expression = parts[1]
            result, description = roll_dice_util(expression)
            if result is None:
                # Ошибка в выражении — показываем только отправителю
                emit('new_message', {
                    'username': 'System',
                    'message': description,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }, room=request.sid)
                logger.debug(f"Invalid dice expression: {expression}")
                return
            else:
                final_text = f"/roll {expression}: {description}"
        else:
            emit('new_message', {
                'username': 'System',
                'message': "Использование: /roll 2d6+3",
                'timestamp': datetime.now(timezone.utc).isoformat()
            }, room=request.sid)
            return

    # Сохраняем сообщение в БД
    msg = ChatMessage(
        lobby_id=lobby_id,
        user_id=user.id,
        username=user.username,
        message=final_text
    )
    db.session.add(msg)
    db.session.commit()
    logger.info(f"Message from {user.username} in lobby {lobby_id}: {final_text}")

    # Рассылаем всем в комнате
    emit('new_message', {
        'id': msg.id,
        'username': user.username,
        'message': final_text,
        'timestamp': msg.timestamp.isoformat()
    }, room=f"lobby_{lobby_id}")
