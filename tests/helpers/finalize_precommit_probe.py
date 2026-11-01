"""Один opt-in owned-file overlap. Импорт: только чистые guards, без app/SQLAlchemy/threading."""
from pathlib import Path
import json
import os
import sys
import time
from copy import deepcopy

# -I исключает каталог script из sys.path: добавляется только его фиксированная папка.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_db_guard import (ROOT, owned_layout, preflight_owned_database,
                              assert_owned_database, owned_database_scope, validate_layout)
from isolation_guard import IsolationError

TOTAL_SECONDS = 60
PERMIT_SECONDS = 10
PASSWORD = 'synthetic-finalize-password'


def validate_invocation(args, env, cwd=None):
    if len(args) != 1:
        raise IsolationError('Нужен ровно один выданный parent UUID')
    owner = owned_layout(args[0])
    for key, value in {'VTT_FINALIZE_RUN_ID': owner.run_id, 'PYTHON_DOTENV_DISABLED': '1',
                       'PYTHONDONTWRITEBYTECODE': '1'}.items():
        if env.get(key) != value:
            raise IsolationError('Не подтверждено чистое окружение child')
    for key in env:
        if (key.startswith('VTT_') and key != 'VTT_FINALIZE_RUN_ID') or key in {
                'DATABASE_URL', 'DEV_DATABASE_URL', 'OPENAI_API_KEY', 'CODEX_HOME', 'PYTHONPATH'}:
            raise IsolationError('Запрещённая настройка child')
    if (cwd or Path.cwd()).resolve() != ROOT:
        raise IsolationError('Чужая рабочая папка')
    return owner


def validate_permit(value, run_id, pid):
    expected = {'schema_version', 'run_id', 'purpose', 'child_pid', 'created'}
    if (not isinstance(value, dict) or set(value) != expected or value['schema_version'] != 1
            or value['run_id'] != run_id or value['purpose'] != 'test'
            or type(value['child_pid']) is not int or value['child_pid'] != pid
            or not isinstance(value['created'], str) or not value['created'].isdigit()):
        raise IsolationError('Parent не подтвердил владение child')


def wait_permit(owner, pid, deadline):
    path = owner.run_dir / 'finalize-start.json'
    end = min(deadline, time.monotonic() + PERMIT_SECONDS)
    while time.monotonic() < end:
        if path.exists():
            if not path.is_file() or path.is_symlink() or path.is_junction() or path.resolve() != path:
                raise IsolationError('Перенаправленный permit')
            validate_permit(json.loads(path.read_text(encoding='utf-8')), owner.run_id, pid)
            return
        time.sleep(.05)
    raise IsolationError('Нет permit parent в пределах deadline')


def remaining(deadline, now, maximum=None):
    value = max(0., deadline - now)
    return value if maximum is None else min(value, maximum)


def trace_is_claim(statement):
    # Строка не сохраняется, параметры/полный SQL не попадают в отчёт.
    return statement.lstrip().upper().startswith('UPDATE DEFERRED_COMBAT_ACTIONS SET ')


def validate_overlap(w, c, phases):
    if not w.get('engine') or w.get('engine') != c.get('engine'):
        raise IsolationError('Соединения не доказаны как принадлежащие одному owned engine')
    for key in ('thread', 'context', 'session', 'connection', 'transaction', 'driver'):
        if not w.get(key) or not c.get(key) or w[key] == c[key]:
            raise IsolationError('Не доказаны независимые ' + key)
    if not w.get('in_transaction') or not c.get('in_transaction'):
        raise IsolationError('Нет двух начатых DBAPI транзакций')
    if not w.get('claim_flushed') or w.get('receipt_flushed') != 1 or c.get('before_flush_in_transaction') is not False:
        raise IsolationError('Не подтверждены реальные claim/receipt или режим чтения C')
    if c.get('read_revision') != w.get('initial_revision'):
        raise IsolationError('C не прочитал исходную ready revision')
    if not phases.get('winner_precommit') or not phases.get('contender_update'):
        raise IsolationError('Не достигнут precommit overlap')
    if phases.get('winner_committed') or phases['winner_precommit'] >= phases['contender_update']:
        raise IsolationError('Postcommit или неверный порядок барьера')


def validate_cleanup(workers_alive, checkedout):
    if workers_alive or checkedout != 0:
        raise IsolationError('Нельзя dispose при живых workers/соединениях')


def failure(error):
    # SQLAlchemy exception str может содержать SQL/params: не сериализуем его.
    original = getattr(error, 'orig', None)
    return {'type': type(error).__name__, 'sqlite_code': getattr(original, 'sqlite_errorcode', None),
            'reason': str(error) if isinstance(error, IsolationError) else None}


def runtime(owner, deadline, report, save):
    # Все runtime imports только ПОСЛЕ preflight и permit; вызывается лишь main.
    sys.path.insert(0, str(ROOT))
    from app.config import TestingConfig
    from sqlalchemy.pool import QueuePool
    TestingConfig.SQLALCHEMY_DATABASE_URI = owner.uri
    TestingConfig.SQLALCHEMY_ENGINE_OPTIONS = {'poolclass': QueuePool, 'pool_size': 2, 'max_overflow': 0,
        'pool_timeout': 5, 'connect_args': {'check_same_thread': False, 'timeout': 30}}
    from app import create_app, socketio
    from app.extensions import db
    from app.models import DeferredCombatAction, ConsumableUse, LobbyCharacter, LocationCharacter
    from sqlalchemy import event
    import threading
    from unittest.mock import patch
    app = create_app('testing')
    app.debug = False
    if socketio.async_mode != 'threading':
        raise IsolationError('Требуется threading Socket.IO')
    observer = None
    workers = []
    control = {'release': threading.Event(), 'abort': threading.Event(),
               'winner': threading.Event(), 'contender': threading.Event()}
    phases = {}
    records = {}
    replies = {}
    errors = []
    listeners = []
    lock = threading.Lock()

    def mark(name):
        with lock:
            phases[name] = time.monotonic()

    def request(client, url, body=None, token=None, method='POST'):
        if time.monotonic() >= deadline:
            raise IsolationError('Общий deadline исчерпан')
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        response = client.open(url, method=method, json=body, headers=headers)
        if not 200 <= response.status_code < 300:
            raise IsolationError('Setup HTTP отказ: ' + str(response.status_code))
        return response.get_json()

    def drain():
        return [item['args'][0] for item in observer.get_received()
                if item['name'] == 'character_data_updated' and item['args'][0].get('character_id') == character_id]

    with app.app_context():
        engine = assert_owned_database(app, db, owner, 'create')
        with owned_database_scope(app, db, owner):
            report['identity'] = {'run_id': owner.run_id, 'purpose': 'test', 'database': 'owned-file',
                'path': owner.path.as_posix(), 'pool': type(engine.pool).__name__, 'testing': app.testing}
            save()
            try:
                client = app.test_client()
                def user(name):
                    request(client, '/auth/register', {'username': name, 'email': name+'@example.test', 'password': PASSWORD})
                    return request(client, '/auth/login', {'username': name, 'password': PASSWORD})
                gm, owner_user = user('finalize-gm'), user('finalize-owner')
                assert owner_user['user_id'] != gm['user_id']
                token = gm['access_token']
                owner_token = owner_user['access_token']
                lobby = request(client, '/lobbies/', {'name': 'Precommit isolation', 'map_type': 'empty', 'chunks_width': 1, 'chunks_height': 1}, token)
                assert lobby['gm_id'] == gm['user_id']
                api = '/lobbies/' + str(lobby['id'])
                request(client, api+'/join', {}, owner_token)
                template = request(client, api+'/templates', {'name': 'Состав завершения', 'category': 'consumable', 'weight': .1,
                    'attributes': {'uses': 2, 'consumable': {'editor_version': 1, 'direct': {'action_points_cost': 1, 'radiation_delta': 3},
                        'modifiers': [], 'effects': []}}}, token)
                item = {'id': 'finalize-item', 'templateId': template['id']+1000000, 'name': template['name'],
                    'category': 'consumable', 'quantity': 1, 'uses': 2, 'maxUses': 2}
                spare = {'id': 'finalize-spare', 'name': 'Невыбранный предмет', 'category': 'misc', 'quantity': 1}
                data = {'inventory': {'backpack': [item, spare], 'pockets': []}, 'equipment': {},
                    'health': {'current': 700, 'max': 700, 'radiation': 0, 'effects': []},
                    'skills': {'physical': {'strength': {'base': 10, 'bonus': 0}}}}
                actor = request(client, api+'/characters', {'name': 'Владелец операции', 'data': data}, owner_token)
                assert actor['owner_id'] == owner_user['user_id'] != lobby['gm_id']
                character_id = actor['id']
                loc = request(client, api+'/locations', {'name': 'Одна операция', 'grid_width': 6, 'grid_height': 6, 'world_tile_x': 0, 'world_tile_z': 0}, token)
                loc_url = api+'/locations/'+str(loc['id'])
                request(client, loc_url+'/spawn_character', {'character_id': character_id, 'tile_x': 2, 'tile_y': 2}, owner_token)
                state = request(client, loc_url+'/combat', token=owner_token, method='GET')
                model = next(c for c in state['characters'] if c['character_id'] == character_id)
                assert model['controlled_by'] == owner_user['user_id']
                actor_id = model['location_character_id']
                request(client, loc_url+'/combat/start', {'location_character_ids': [actor_id], 'initiator_location_character_id': actor_id, 'initiator_first': True}, token)
                before = request(client, '/lobbies/characters/'+str(character_id), token=owner_token, method='GET')['data']
                operation = 'one-owned-finalize'
                selection = {'source': {'path': ['inventory', 'backpack', 0], 'snapshot': deepcopy(before['inventory']['backpack'][0])},
                    'application': {'kind': 'self', 'actionPoints': 0}, 'target_character_id': character_id,
                    'treatment_request_id': None, 'server_authoritative': True, 'server_operation': 'general'}
                request(client, loc_url+'/combat/spend', {'location_character_id': actor_id, 'action_points': 3,
                    'allow_deferred': True, 'pending_action_id': operation, 'pending_action_label': 'Одна операция', 'consumable_selection': selection}, owner_token)
                recovered = request(client, loc_url+'/combat/deferred-consumable/'+operation, token=owner_token, method='GET')
                assert recovered['selection'] == selection and recovered['medicine_roll'] is None
                row = db.session.get(DeferredCombatAction, operation)
                assert row.status == 'ready' and row.remaining_action_points == 0
                initial_revision = row.revision
                baseline = request(client, '/lobbies/characters/'+str(character_id), token=owner_token, method='GET')['data']
                baseline_ap = db.session.get(LocationCharacter, actor_id).action_points_current
                assert baseline_ap == 2 and ConsumableUse.query.count() == 0
                assert baseline['inventory']['backpack'][0]['uses'] == 2 and baseline['health']['radiation'] == 0
                observer = socketio.test_client(app)
                observer.emit('authenticate', {'token': owner_token, 'lobby_id': lobby['id']})
                observer.get_received()
                joined = observer.emit('join_character', {'token': owner_token, 'character_id': character_id}, callback=True)
                assert joined['ok'] is True and drain() == []
                report['setup'] = {'operation_id': operation, 'character_id': character_id, 'actor_id': actor_id,
                    'initial_revision': initial_revision, 'ordinary_owner_id': owner_user['user_id'], 'gm_id': lobby['gm_id'], 'paid_action_points': 3, 'remaining_ap': baseline_ap, 'selection': selection}
                save()
                db.session.remove()
                body = {'operation_id': operation, 'deferred_action_id': operation, 'source': deepcopy(selection['source']),
                    'application': selection['application'], 'combat_location_id': loc['id'], 'actor_location_character_id': actor_id}
                url = '/lobbies/characters/'+str(character_id)+'/general-consumable'

                def run_worker(role):
                    stage = 'context'
                    with app.app_context() as context:
                        session = db.session()
                        connection = session.connection()
                        driver = connection.connection.driver_connection
                        record = records[role] = {'thread': threading.get_ident(), 'context': id(context),
                            'session': id(session), 'connection': id(connection),
                            'transaction': id(connection.get_transaction()), 'driver': id(driver), 'engine': id(engine),
                            'initial_revision': initial_revision, 'session_removed': False}
                        seen_receipts = set()
                        session_listener = None
                        try:
                            assert_owned_database(app, db, owner)
                            if connection.engine is not engine:
                                raise IsolationError('Чужой engine конкретного connection')
                            import sqlite3
                            if (getattr(driver, 'autocommit', None) != sqlite3.LEGACY_TRANSACTION_CONTROL
                                    or driver.isolation_level not in ('', 'DEFERRED') or driver.in_transaction):
                                raise IsolationError('BLOCKED_LOCKING: требуется наблюдаемый legacy read перед DML')
                            if role == 'W':
                                def after_flush(sess, flush_context):
                                    for entry in list(sess.new) + list(sess.dirty):
                                        if isinstance(entry, DeferredCombatAction) and entry.id == operation:
                                            record['claim_flushed'] = entry.status == 'completed'
                                        if isinstance(entry, ConsumableUse) and entry.id == operation:
                                            seen_receipts.add(entry.id)
                                    record['receipt_flushed'] = len(seen_receipts)
                                session_listener = ('after_flush', after_flush)
                                event.listen(session, *session_listener)
                                real_commit = session.commit
                                def commit_barrier():
                                    if threading.get_ident() != record['thread'] or phases.get('winner_precommit'):
                                        raise IsolationError('BLOCKED_INSTRUMENTATION: повторный/чужой commit hook')
                                    record['in_transaction'] = driver.in_transaction
                                    mark('winner_precommit')
                                    control['winner'].set()
                                    if not control['release'].wait(remaining(deadline, time.monotonic(), 12)) or control['abort'].is_set():
                                        raise IsolationError('BLOCKED_BARRIER: commit не разрешён контроллером')
                                    real_commit()
                                    mark('winner_committed')
                                stage = 'winner-post'
                                with patch.object(session, 'commit', commit_barrier):
                                    response = app.test_client().post(url, json=deepcopy(body), headers={'Authorization': 'Bearer '+owner_token})
                            else:
                                record['trace_cleared'] = False
                                def checkin(raw, connection_record):
                                    if raw is driver:
                                        try:
                                            driver.set_trace_callback(None)
                                            record['trace_cleared'] = True
                                        except Exception as exc:
                                            errors.append({'role': role, 'phase': 'trace-checkin', **failure(exc)})
                                event.listen(engine.pool, 'checkin', checkin)
                                listeners.append((engine.pool, 'checkin', checkin))
                                def before_flush(sess, flush_context, instances):
                                    for entry in list(sess.dirty):
                                        if isinstance(entry, DeferredCombatAction) and entry.id == operation and entry.status == 'completed':
                                            record['read_revision'] = entry.revision
                                            record['before_flush_in_transaction'] = driver.in_transaction
                                session_listener = ('before_flush', before_flush)
                                event.listen(session, *session_listener)
                                def trace(statement):
                                    # Callback не ждёт, не исполняет SQL, не бросает исключения и не хранит SQL.
                                    try:
                                        if (threading.get_ident() == record['thread'] and not control['contender'].is_set()
                                                and trace_is_claim(statement)):
                                            record['in_transaction'] = driver.in_transaction
                                            mark('contender_update')
                                            control['contender'].set()
                                    except Exception:
                                        record['trace_error'] = True
                                        control['contender'].set()
                                driver.set_trace_callback(trace)
                                stage = 'contender-post'
                                response = app.test_client().post(url, json=deepcopy(body), headers={'Authorization': 'Bearer '+owner_token})
                            replies[role] = {'status': response.status_code}
                            if response.status_code == 200:
                                replies[role]['data'] = response.get_json()['data']
                            mark(role+'_returned')
                        except Exception as exc:
                            errors.append({'role': role, 'phase': stage, **failure(exc)})
                        finally:
                            try:
                                if session_listener:
                                    event.remove(session, *session_listener)
                            finally:
                                try:
                                    try:
                                        session.rollback()
                                    finally:
                                        db.session.remove()
                                    record['session_removed'] = True
                                except Exception as exc:
                                    errors.append({'role': role, 'phase': 'session-remove', **failure(exc)})

                def worker(role):
                    try:
                        run_worker(role)
                    except Exception as exc:
                        errors.append({'role': role, 'phase': 'worker-context-checkout', **failure(exc)})

                try:
                    workers.append(threading.Thread(target=worker, args=('W',), name='owned-finalize-W', daemon=False))
                    workers[0].start()
                    if not control['winner'].wait(remaining(deadline, time.monotonic(), 5)):
                        raise IsolationError('BLOCKED_BARRIER: W не дошёл до настоящего precommit')
                    if drain():
                        raise IsolationError('Эффект опубликован до commit')
                    workers.append(threading.Thread(target=worker, args=('C',), name='owned-finalize-C', daemon=False))
                    workers[1].start()
                    if not control['contender'].wait(remaining(deadline, time.monotonic(), 5)):
                        raise IsolationError('BLOCKED_BARRIER: C не дошёл до UPDATE до commit W')
                    if records.get('C', {}).get('trace_error') or errors:
                        raise IsolationError('BLOCKED_INSTRUMENTATION: ошибка observer/worker')
                    validate_overlap(records['W'], records['C'], phases)
                    if engine.pool.checkedout() != 2:
                        raise IsolationError('Не удерживаются ровно два connection в момент overlap')
                    if drain():
                        raise IsolationError('Приватный эффект появился до release')
                    report['overlap'] = {'connections': deepcopy(records), 'phases': dict(phases), 'checkedout': engine.pool.checkedout()}
                    save()
                    control['release'].set()
                    for thread in workers:
                        thread.join(remaining(deadline, time.monotonic(), 35))
                    if any(thread.is_alive() for thread in workers):
                        raise IsolationError('BLOCKED_TIMEOUT: workers не завершились в общем deadline')
                    if errors:
                        raise IsolationError('BLOCKED_WORKER: см. безопасную диагностику ошибок')
                    if replies.get('W', {}).get('status') != 200 or replies.get('C', {}).get('status') != 409:
                        raise IsolationError('BLOCKED_LOCKING: нужны W200/C409 без retry')
                    if not records['C'].get('trace_cleared'):
                        raise IsolationError('BLOCKED_INSTRUMENTATION: trace не снят до checkin')
                    for obj, name, callback in listeners:
                        event.remove(obj, name, callback)
                    listeners.clear()
                    if not all(record['session_removed'] for record in records.values()):
                        raise IsolationError('Незавершённая worker session')
                    validate_cleanup(False, engine.pool.checkedout())
                    # Свежая независимая session только после завершения обоих requests.
                    with app.app_context():
                        assert_owned_database(app, db, owner)
                        actual = db.session.get(LobbyCharacter, character_id)
                        expected = actual.data_snapshot()
                        actual_row = db.session.get(DeferredCombatAction, operation)
                        receipts = ConsumableUse.query.all()
                        assert actual_row.status == 'completed' and actual_row.remaining_action_points == 0
                        assert actual_row.payload['selection'] == selection
                        assert len(receipts) == 1 and receipts[0].id == operation
                        assert receipts[0].actor_character_id == receipts[0].target_character_id == character_id
                        assert expected['health']['radiation'] == baseline['health']['radiation']+3
                        expected_item = deepcopy(baseline['inventory']['backpack'][0])
                        expected_item['uses'] = 1
                        expected_item.setdefault('attributes', {})['uses_remaining'] = 1
                        assert expected['inventory']['backpack'] == [expected_item, spare]
                        assert db.session.get(LocationCharacter, actor_id).action_points_current == baseline_ap
                        assert replies['W']['data'] == expected
                        db.session.remove()
                        fresh = request(app.test_client(), '/lobbies/characters/'+str(character_id), token=owner_token, method='GET')['data']
                        assert fresh == expected
                        db.session.remove()
                    events = drain()
                    assert len(events) == 1 and events[0]['source'] == 'general_consumable'
                    assert events[0]['updates']['data'] == expected
                    report['verification'] = {'winner': 200, 'contender': 409, 'receipt_count': 1,
                        'uses_before': 2, 'uses_after': 1, 'radiation_before': baseline['health']['radiation'],
                        'radiation_after': expected['health']['radiation'], 'ap': baseline_ap,
                        'revision': expected['_revision'], 'private_events': events, 'fresh_get_matches': True}
                    if time.monotonic() >= deadline:
                        raise IsolationError('BLOCKED_TIMEOUT: общий deadline результата исчерпан')
                    report['status'] = 'READY_FOR_REVIEW'
                finally:
                    control['abort'].set()
                    control['release'].set()
                    for thread in workers:
                        if thread.is_alive():
                            thread.join(remaining(deadline, time.monotonic(), 35))
                    alive = any(thread.is_alive() for thread in workers)
                    report['workers'] = {'alive': alive, 'connections': records, 'phases': phases, 'replies': replies, 'errors': errors}
                    if alive:
                        report['status'] = 'BLOCKED_TIMEOUT'
                        report['cleanup'] = {'status': 'unproven', 'workers_joined': False, 'engine_disposed': False}
                        save()
                        # Нельзя unwind owned scope: его finally делает dispose при живых workers.
                        os._exit(2)
            finally:
                for obj, name, callback in listeners:
                    event.remove(obj, name, callback)
                if observer is not None and observer.is_connected():
                    observer.disconnect()
                db.session.remove()
                validate_cleanup(any(t.is_alive() for t in workers), engine.pool.checkedout())
                report['cleanup'] = {'status': 'pending_scope_exit', 'workers_joined': True,
                    'sessions_removed': True, 'checkedout': engine.pool.checkedout()}
                save()
        report['cleanup'].update(status='confirmed', engine_disposed=True)
        save()


def main(args=None):
    deadline = time.monotonic() + TOTAL_SECONDS
    owner = None
    preflight_ok = False
    report = {'task': 'FINALIZE-PRECOMMIT', 'status': 'IN_PROGRESS', 'cleanup': {'status': 'not-started'}}
    def save():
        if owner is not None and preflight_ok:
            validate_layout(owner, 'cleanup')
            output = owner.run_dir / 'finalize-precommit.json'
            if output.exists() and (not output.is_file() or output.is_symlink() or output.is_junction() or output.resolve() != output):
                raise IsolationError('Перенаправленный отчёт child')
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        owner = validate_invocation(sys.argv[1:] if args is None else args, os.environ)
        preflight_owned_database(owner.run_id)
        preflight_ok = True
        report['run_id'] = owner.run_id
        wait_permit(owner, os.getpid(), deadline)
        preflight_owned_database(owner.run_id)  # Не появился ли файл до app-import.
        save()
        runtime(owner, deadline, report, save)
        return 0 if report['status'] == 'READY_FOR_REVIEW' else 1
    except Exception as exc:
        report['status'] = 'BLOCKED'
        report['failure'] = failure(exc)
        try:
            save()
        except Exception:
            pass  # Небезопасный путь отчёта не используется даже при ошибке.
        print('Проверка заблокирована: ' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
