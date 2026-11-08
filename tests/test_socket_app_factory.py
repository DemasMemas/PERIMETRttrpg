"""Каждое изолированное приложение должно получать игровые обработчики Socket.IO."""
from app import create_app
from app.extensions import socketio


def test_repeated_factory_keeps_game_socket_handlers(app):
    expected = {'join_location', 'update_location_tiles', 'send_message'}
    first = set(socketio.server.handlers.get('/', {}))
    assert expected <= first
    second = create_app('testing')
    assert second.testing
    assert expected <= set(socketio.server.handlers.get('/', {}))
