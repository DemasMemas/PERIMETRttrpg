# app/services/lobby.py
import logging
import random
import string
from app.extensions import db
from app.models import Lobby, LobbyParticipant, MapChunk, WorldFactionConfig
from app.constants import MAX_CHUNKS_WIDTH, MAX_CHUNKS_HEIGHT
from app.services.exceptions import ValidationError, NotFoundError, PermissionDenied

logger = logging.getLogger(__name__)

def generate_invite_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

class LobbyService:
    @staticmethod
    def create_lobby(user_id, name, map_type, chunks_width=16, chunks_height=16, import_data=None, faction_source='custom'):
        """
        Создаёт новую комнату.
        - user_id: ID создателя (GM)
        - name: название комнаты
        - map_type: тип карты ('empty', 'random', 'predefined', 'imported')
        - chunks_width, chunks_height: размер в чанках (для не-imported)
        - import_data: если map_type='imported', словарь с данными импорта
        """
        if not name:
            raise ValidationError("Lobby name is required")
        if map_type not in ['empty', 'random', 'predefined', 'imported']:
            raise ValidationError("Invalid map type")
        if faction_source not in ('custom','ready','ready_customized'):
            raise ValidationError('Неизвестный источник группировок')

        if map_type == 'imported':
            if not import_data:
                raise ValidationError("Import data required for imported map")
            # Импортируем размеры из файла, если они там есть
            chunks_width = import_data.get('chunks_width', 16)
            chunks_height = import_data.get('chunks_height', 16)
        else:
            if not isinstance(chunks_width, int) or chunks_width < 1 or chunks_width > MAX_CHUNKS_WIDTH:
                raise ValidationError(f"chunks_width must be 1-{MAX_CHUNKS_WIDTH}")
            if not isinstance(chunks_height, int) or chunks_height < 1 or chunks_height > MAX_CHUNKS_HEIGHT:
                raise ValidationError(f"chunks_height must be 1-{MAX_CHUNKS_HEIGHT}")

        # Генерация уникального кода
        code = generate_invite_code()
        while Lobby.query.filter_by(invite_code=code).first():
            code = generate_invite_code()

        lobby = Lobby(
            name=name,
            gm_id=user_id,
            invite_code=code,
            map_type=map_type,
            chunks_width=chunks_width,
            chunks_height=chunks_height
        )

        lobby.weather_settings = {
            'sun': {'enabled': True, 'intensity': 0.7},
            'fog': {'enabled': False, 'intensity': 0.5},
            'rain': {'enabled': False, 'intensity': 0.5},
            'emission': {'enabled': False, 'intensity': 0.5}
        }

        db.session.add(lobby)
        db.session.flush()  # чтобы получить id
        if faction_source!='custom':
            from app.services.world_factions_preset import ready_world_factions
            preset=ready_world_factions()
            db.session.add(WorldFactionConfig(lobby_id=lobby.id,source=faction_source,
                groups=preset['groups'],relations=preset['relations']))

        # Добавляем создателя как участника
        participant = LobbyParticipant(lobby_id=lobby.id, user_id=user_id)
        db.session.add(participant)

        # Если это импорт, создаём чанки
        if map_type == 'imported' and import_data and 'chunks' in import_data:
            for chunk_item in import_data['chunks']:
                chunk_x = chunk_item.get('chunk_x')
                chunk_y = chunk_item.get('chunk_y')
                data = chunk_item.get('data')
                if None in (chunk_x, chunk_y, data):
                    continue  # пропускаем некорректные записи
                if chunk_x < 0 or chunk_x >= chunks_width or chunk_y < 0 or chunk_y >= chunks_height:
                    continue
                chunk = MapChunk(
                    lobby_id=lobby.id,
                    chunk_x=chunk_x,
                    chunk_y=chunk_y,
                    data=data
                )
                db.session.add(chunk)

        db.session.commit()
        logger.info(f"Lobby created: '{name}' (id={lobby.id}) by user {user_id}, code={code}")
        return lobby

    @staticmethod
    def get_lobby(lobby_id, user_id):
        """Получение информации о комнмате (с проверкой участия)."""
        lobby = db.session.get(Lobby, lobby_id)
        if not lobby or not lobby.is_active:
            raise NotFoundError("Lobby not found")

        participant = LobbyParticipant.query.filter_by(lobby_id=lobby_id, user_id=user_id).first()
        if not participant:
            raise PermissionDenied("You are not in this lobby")

        return lobby

    @staticmethod
    def delete_lobby(lobby_id, gm_id):
        """Мягкое удаление комнаты (только GM)."""
        lobby = db.session.get(Lobby, lobby_id)
        if not lobby or not lobby.is_active:
            raise NotFoundError("Lobby not found")

        if lobby.gm_id != gm_id:
            raise PermissionDenied("Only GM can delete lobby")

        lobby.is_active = False
        db.session.commit()
        logger.info(f"Lobby {lobby_id} deactivated by GM {gm_id}")

    @staticmethod
    def get_my_lobbies(user_id, limit=None, offset=0):
        """Возвращает список комнат, созданных пользователем, с пагинацией."""
        query = Lobby.query.filter_by(gm_id=user_id, is_active=True).order_by(Lobby.created_at.desc())
        if limit is not None:
            query = query.limit(limit).offset(offset)
        lobbies = query.all()
        logger.debug(f"User {user_id} has {len(lobbies)} lobbies (limit={limit}, offset={offset})")
        return [{
            'id': l.id,
            'name': l.name,
            'created_at': l.created_at,
            'invite_code': l.invite_code
        } for l in lobbies]

    @staticmethod
    def join_by_code(user_id, code):
        """Присоединение к комнате по коду."""
        lobby = Lobby.query.filter_by(invite_code=code, is_active=True).first()
        if not lobby:
            raise NotFoundError("Invalid or inactive code")

        # Используем ParticipantService для присоединения
        from app.services.participant import ParticipantService
        ParticipantService.join_lobby(user_id, lobby.id)
        logger.info(f"User {user_id} joined lobby {lobby.id} via code {code}")
        return lobby

    @staticmethod
    def list_active_lobbies():
        """Возвращает список всех активных комнат (для общего списка)."""
        lobbies = Lobby.query.filter_by(is_active=True).all()
        return [{
            'id': l.id,
            'name': l.name,
            'gm_id': l.gm_id,
            'participants_count': len(l.participants)
        } for l in lobbies]

    @staticmethod
    def get_joined_lobbies(user_id, limit=None, offset=0):
        """
        Возвращает список комнат, в которых пользователь участвует (но не создал),
        с пагинацией. Комнаты должны быть активны, пользователь не забанен.
        """
        from app.models import LobbyParticipant
        query = (db.session.query(Lobby)
                 .join(LobbyParticipant, Lobby.id == LobbyParticipant.lobby_id)
                 .filter(LobbyParticipant.user_id == user_id,
                         LobbyParticipant.is_banned == False,
                         Lobby.is_active == True,
                         Lobby.gm_id != user_id)
                 .order_by(Lobby.created_at.desc()))
        if limit is not None:
            query = query.limit(limit).offset(offset)
        lobbies = query.all()
        logger.debug(f"User {user_id} has {len(lobbies)} joined lobbies (limit={limit}, offset={offset})")
        return [{
            'id': l.id,
            'name': l.name,
            'created_at': l.created_at,
            'invite_code': l.invite_code
        } for l in lobbies]
