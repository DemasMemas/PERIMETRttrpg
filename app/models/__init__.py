"""
Модели SQLAlchemy для таблиц базы данных.

- User           : пользователи
- Lobby          : игровые комнаты
- LobbyParticipant : связь пользователей с комнатами (участники)
- GameState      : состояние карты (маркеры)
- ChatMessage    : сообщения чата
- LobbyCharacter : персонажи в комнате
- MapChunk       : данные чанков карты
- ItemTemplate   : глобальные шаблоны предметов
- LobbyItemTemplate : локальные (кастомные) шаблоны комнаты
"""

from .user import User
from .lobby import Lobby
from .participant import LobbyParticipant
from .game_state import GameState
from .chat_message import ChatMessage
from .character import LobbyCharacter
from .map_chunk import MapChunk
from .location import Location
from .location_character import LocationCharacter
from .location_object import LocationObject
from .location_combat import LocationCombatState
from .character_interaction import CharacterInteractionRequest
from .deferred_action import DeferredCombatAction
from .consumable_use import ConsumableUse
from .world_travel import WorldGroup, WorldMapEvent, WorldTravelEvent
from .time_planning import TimePlanRequest, TimePlan
from .npc_generation import NpcGenerationConfig, NpcCreationReceipt
from .world_factions import WorldFactionConfig
from .pda_account import PdaAccount, PdaAccountLogin
