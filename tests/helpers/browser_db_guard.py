"""Browser-only SQLite: путь/manifest принадлежат ровно одному запуску."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import stat
from uuid import UUID

from isolation_guard import IsolationError

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class OwnedDatabase:
    run_id: str
    run_dir: Path
    directory: Path
    path: Path
    manifest: Path

    @property
    def uri(self):
        return 'sqlite:///' + self.path.as_posix()


def owned_layout(run_id):
    try:
        if str(UUID(run_id)) != run_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise IsolationError('Неверный UUID браузерного запуска') from None
    run = ROOT / '.private-testing' / 'reports' / run_id
    directory = run / 'db'
    return OwnedDatabase(run_id, run, directory, directory / 'test.sqlite', directory / 'manifest.json')


def expected_manifest(run_id):
    return {'schema_version': 1, 'run_id': run_id, 'purpose': 'test',
            'mode': 'owned-file', 'database': 'test.sqlite'}


class PathIO:
    def lstat(self, path):
        return path.lstat()

    def resolve(self, path):
        return path.resolve()

    def junction(self, path):
        return path.is_junction()

    def exists(self, path):
        try:
            path.lstat()
            return True
        except FileNotFoundError:
            return False

    def read(self, path):
        return path.read_text(encoding='utf-8')


def validate_layout(owner, phase, io=None):
    io = io or PathIO()
    if owner != owned_layout(owner.run_id) or phase not in {'create', 'identity', 'cleanup'}:
        raise IsolationError('Неизвестный путь или этап браузерной БД')
    def check(path, directory=False):
        info = io.lstat(path)
        correct = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not correct or io.junction(path) or io.resolve(path) != path:
            raise IsolationError('Перенаправление/неверный тип принадлежащего запуску пути')
    for directory in (ROOT, ROOT / '.private-testing', ROOT / '.private-testing' / 'reports', owner.run_dir, owner.directory):
        check(directory, directory=True)
    check(owner.manifest)
    try:
        manifest = json.loads(io.read(owner.manifest))
    except (ValueError, TypeError):
        raise IsolationError('Недействительный manifest браузерной БД') from None
    if manifest != expected_manifest(owner.run_id):
        raise IsolationError('Manifest/run_id/purpose браузерной БД не совпадает')
    exists = io.exists(owner.path)
    if phase == 'create' and exists:
        raise IsolationError('Повторное использование существующей браузерной БД запрещено')
    if phase == 'identity' and not exists:
        raise IsolationError('Собственная браузерная БД ещё не создана')
    if exists:
        check(owner.path)
    return owner


def preflight_owned_database(run_id, io=None):
    return validate_layout(owned_layout(run_id), 'create', io)


def assert_owned_database(app, db, owner, phase='identity', io=None):
    validate_layout(owner, phase, io)
    config = app.config
    if (config.get('TESTING') is not True or config.get('SQLALCHEMY_DATABASE_URI') != owner.uri
            or config.get('SQLALCHEMY_BINDS')):
        raise IsolationError('Отказ: не собственная testing/file конфигурация')
    engine = db.engine
    url = engine.url
    if (engine.dialect.name != 'sqlite' or url.drivername not in {'sqlite', 'sqlite+pysqlite'}
            or url.database != owner.path.as_posix()
            or any((url.host, url.port, url.username, url.password, url.query))
            or set(db.engines) != {None} or db.engines[None] is not engine
            or type(engine.pool).__name__ != 'QueuePool'):
        raise IsolationError('Отказ: реальный engine/URL/pool не принадлежит браузерному запуску')
    return engine


@contextmanager
def owned_database_scope(app, db, owner, io=None):
    engine = assert_owned_database(app, db, owner, 'create', io)
    try:
        db.create_all()
        yield
    finally:
        try:
            db.session.remove()
            if assert_owned_database(app, db, owner, 'cleanup', io) is not engine:
                raise IsolationError('Движок собственной браузерной БД заменён')
            if engine.pool.checkedout() != 0:
                raise IsolationError('Соединения собственной браузерной БД ещё заняты')
        finally:
            try:
                db.session.remove()
            finally:
                engine.dispose()
    # Файл и таблицы намеренно остаются синтетическим приватным артефактом.
