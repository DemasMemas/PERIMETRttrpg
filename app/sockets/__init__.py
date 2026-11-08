# app/sockets/__init__.py
"""
Обработчики WebSocket событий.

- auth.py        : подключение, аутентификация, выход
- chat.py        : отправка сообщений, команды /roll
- dice.py        : броски навыков персонажа
- markers.py     : создание, редактирование, перемещение маркеров на карте
- character.py   : обновление данных персонажа в реальном времени
- kick.py        : вспомогательная функция для кика пользователя
- utils.py       : получение пользователя из JWT токена
"""

from . import auth
from . import chat
from . import dice
from . import markers
from . import character
from . import location
from . import network
from . import map_tools

from app.extensions import socketio

# Повторный create_app создаёт новый сервер; Python не выполняет декораторы импорта заново.
_event_handlers = {(namespace or '/', event): handler
                   for event, handler, namespace in socketio.handlers}
if socketio.server is not None:
    _event_handlers.update({(namespace, event): handler
                            for namespace, events in socketio.server.handlers.items()
                            for event, handler in events.items()})


def register_handlers():
    for (namespace, event), handler in _event_handlers.items():
        socketio.server.on(event, handler, namespace=namespace)
