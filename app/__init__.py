# app/__init__.py
"""
Главный модуль приложения TTRPG.

Структура бэкенда:
- auth/         : эндпоинты регистрации, логина, профиля
- lobbies/      : все REST-эндпоинты для комнат (создание, карта, персонажи, шаблоны)
- models/       : SQLAlchemy модели таблиц БД
- schemas/      : Marshmallow схемы для валидации и сериализации
- services/     : бизнес-логика (создание комнат, управление участниками, карта, персонажи)
- sockets/      : обработчики WebSocket событий (чат, маркеры, игральные кости)
- utils/        : вспомогательные функции и декораторы (@requires_participant, @requires_gm)
- extensions.py : инициализация Flask-расширений (db, migrate, jwt, socketio)
- config.py     : конфигурация приложения (development, production)
- constants.py  : общие константы (CHUNK_SIZE, типы тайлов и аномалий)
"""

import json
import logging
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, render_template, jsonify, send_from_directory
from flask_jwt_extended import JWTManager
from flask_socketio import SocketIO
from app.extensions import db, migrate, jwt, socketio
from app.config import config_by_name
from app.services.exceptions import (
    ServiceError, ValidationError, NotFoundError, PermissionDenied, ConflictError
)
from sqlalchemy.orm.exc import StaleDataError
from marshmallow import ValidationError as MarshmallowValidationError


_PROJECT_LOG_HANDLER = '_ttrpg_file_handler'


def _is_project_log_handler(handler):
    if getattr(handler, _PROJECT_LOG_HANDLER, False):
        return True
    return (
        isinstance(handler, RotatingFileHandler)
        and Path(getattr(handler, 'baseFilename', '')).name == 'ttrpg.log'
    )


def _remove_log_handler(logger, handler):
    logger.removeHandler(handler)
    handler.close()


def _configure_logging(app):
    logger = app.logger
    level_name = str(app.config.get('LOG_LEVEL', 'INFO')).upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    project_handlers = [
        handler for handler in logger.handlers
        if _is_project_log_handler(handler)
    ]
    if not app.config.get('LOG_TO_FILE', True):
        for handler in project_handlers:
            _remove_log_handler(logger, handler)
        return None

    configured_path = app.config.get('LOG_FILE')
    log_path = (
        Path(configured_path)
        if configured_path
        else Path(app.root_path).parent / 'logs' / 'ttrpg.log'
    )
    fallback_path = Path(tempfile.gettempdir()) / 'ttrpg.log'
    max_bytes = int(app.config.get('LOG_MAX_BYTES', 5 * 1024 * 1024))
    backup_count = int(app.config.get('LOG_BACKUP_COUNT', 3))

    file_handler = next((
        handler for handler in project_handlers
        if Path(handler.baseFilename).resolve() == log_path.resolve()
    ), None)
    for handler in project_handlers:
        if handler is not file_handler:
            _remove_log_handler(logger, handler)

    if file_handler is None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8',
            )
        except OSError:
            file_handler = RotatingFileHandler(
                fallback_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8',
            )
        setattr(file_handler, _PROJECT_LOG_HANDLER, True)
        logger.addHandler(file_handler)

    file_handler.maxBytes = max_bytes
    file_handler.backupCount = backup_count
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s [%(name)s]: %(message)s '
        '[in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(level)
    return file_handler

def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    # Настройка логирования в файл
    file_handler = _configure_logging(app)
    if file_handler and not getattr(app.logger, '_ttrpg_startup_logged', False):
        app.logger.info('TTRPG application startup')
        app.logger._ttrpg_startup_logged = True

    # Инициализация расширений
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    socketio.init_app(
        app,
        cors_allowed_origins="*",
        async_mode=app.config['SOCKETIO_ASYNC_MODE'],
    )

    # Регистрация blueprint'ов
    from app.auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from app.lobbies import lobbies_bp
    app.register_blueprint(lobbies_bp, url_prefix='/lobbies')

    # Импорт сокет-обработчиков
    from app.sockets import register_handlers
    register_handlers()

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/licenses')
    def licenses():
        registry_path = Path(app.config.get(
            'ASSET_CREDITS_PATH', Path(app.root_path) / 'data' / 'asset_credits.json',
        ))
        with registry_path.open(encoding='utf-8') as registry:
            assets = json.load(registry)
        return render_template(
            'licenses.html',
            used_assets=[asset for asset in assets if asset['status'] == 'in_use'],
            planned_assets=[asset for asset in assets if asset['status'] == 'planned'],
        )

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(
            app.static_folder,
            'assets/pictures/bg.png',
            mimetype='image/png',
        )

    # ---- Централизованная обработка ошибок ----
    @app.errorhandler(StaleDataError)
    def handle_stale_write(error):
        db.session.rollback()
        return handle_service_error(ConflictError())

    @app.errorhandler(ConflictError)
    @app.errorhandler(ValidationError)
    @app.errorhandler(NotFoundError)
    @app.errorhandler(PermissionDenied)
    def handle_service_error(error):
        db.session.rollback()
        response = jsonify({
            'error': {
                'code': getattr(error, 'code', 400),
                'message': str(error)
            }
        })
        response.status_code = error.code
        return response

    @app.errorhandler(MarshmallowValidationError)
    def handle_marshmallow_error(error):
        details = {}
        for field, messages in error.messages.items():
            details[field] = messages if isinstance(messages, list) else [messages]
        return jsonify({
            'error': {
                'code': 400,
                'message': 'Validation error',
                'details': details
            }
        }), 400

    @app.errorhandler(404)
    def handle_404(error):
        return jsonify({
            'error': {
                'code': 404,
                'message': 'Resource not found'
            }
        }), 404

    @app.errorhandler(500)
    def handle_500(error):
        app.logger.exception("Unhandled exception")
        return jsonify({
            'error': {
                'code': 500,
                'message': 'Internal server error'
            }
        }), 500

    return app
