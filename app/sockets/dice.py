# app/sockets/dice.py
import logging
import random
from datetime import datetime, timezone
from flask import request
from flask_socketio import emit
from app.extensions import socketio, db
from app.models import Lobby, LobbyParticipant, LobbyCharacter, ChatMessage
from app.utils.dice import roll_dice as roll_dice_util
from .utils import get_user_from_token

logger = logging.getLogger(__name__)

@socketio.on('roll_skill')
def handle_roll_skill(data):
    token = data.get('token')
    lobby_id = data.get('lobby_id')
    character_id = data.get('character_id')
    skill_name = data.get('skill_name')
    extra_modifier = data.get('extra_modifier', 0)

    if not all([token, lobby_id, character_id, skill_name]):
        emit('error', {'message': 'Missing data'}, room=request.sid)
        return

    user = get_user_from_token(token)
    if not user:
        emit('error', {'message': 'Invalid token'}, room=request.sid)
        return

    participant = LobbyParticipant.query.filter_by(lobby_id=lobby_id, user_id=user.id).first()
    if not participant:
        emit('error', {'message': 'You are not in this lobby'}, room=request.sid)
        return

    character = LobbyCharacter.query.get(character_id)
    if not character or character.lobby_id != lobby_id:
        emit('error', {'message': 'Character not found'}, room=request.sid)
        return

    lobby = Lobby.query.get(lobby_id)
    is_gm = (lobby.gm_id == user.id)
    if character.owner_id != user.id and not is_gm:
        emit('error', {'message': 'You cannot roll for this character'}, room=request.sid)
        return

    # Поиск навыка в данных персонажа (универсальный)
    char_data = character.data
    skill_bonus = None

    if isinstance(char_data, dict):
        if 'skills' in char_data:
            skill_bonus = char_data['skills'].get(skill_name)
        elif 'data' in char_data and isinstance(char_data['data'], dict):
            if 'skills' in char_data['data']:
                skill_bonus = char_data['data']['skills'].get(skill_name)
            else:
                for k, v in char_data['data'].items():
                    if isinstance(v, dict) and skill_name in v:
                        skill_bonus = v[skill_name]
                        break
        else:
            for k, v in char_data.items():
                if isinstance(v, dict) and skill_name in v:
                    skill_bonus = v[skill_name]
                    break

    if skill_bonus is None:
        logger.warning(f"Skill {skill_name} not found for character {character_id}")
        emit('error', {'message': f'Skill {skill_name} not found'}, room=request.sid)
        return

    d20 = random.randint(1, 20)
    total = d20 + skill_bonus + extra_modifier
    roll_description = f"1d20 ({d20}) + {skill_bonus} (навык) + {extra_modifier} (мод) = **{total}**"
    message = f"{character.name} ({user.username}) совершил бросок {skill_name}: {roll_description}"

    logger.info(f"Skill roll: {user.username} rolled {skill_name} for {character.name}, result {total}")

    stored=ChatMessage(lobby_id=lobby_id,user_id=user.id,username='System (Roll)',message=message)
    db.session.add(stored);db.session.commit()
    emit('new_message', {
        'id':stored.id,
        'username': 'System (Roll)',
        'message': message,
        'timestamp': stored.timestamp.isoformat()
    }, room=f"lobby_{lobby_id}")
