import os
from pathlib import Path
import subprocess
import sys

from app import create_app, socketio


config_name = os.getenv('FLASK_ENV') or 'development'
app = create_app(config_name)


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    if (
        config_name in {'development', 'default'}
        and os.name == 'nt'
        and os.environ.get('TTRPG_SKIP_UAT_REFRESH') != '1'
        and os.environ.get('WERKZEUG_RUN_MAIN') != 'true'
        and (root / '.private-testing' / 'uat' / 'enabled').is_file()
    ):
        refresh = subprocess.run(
            [sys.executable, str(root / 'scripts' / 'local_uat.py'), 'refresh'], cwd=root,
        )
        if refresh.returncode:
            app.logger.warning('Не удалось обновить локальную копию UAT. Сервер продолжает запуск.')

    debug = os.getenv('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes', 'on'}
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', '5000'))
    if os.name == 'nt' and socketio.async_mode == 'eventlet':
        from errno import WSAECONNABORTED

        from eventlet import wsgi

        # При отмене запроса Windows может вернуть 10053 при сбросе буфера ответа.
        wsgi.BROKEN_SOCK.add(WSAECONNABORTED)
    socketio.run(app, host=host, port=port, debug=debug)
