"""Повторяемый запуск явного набора проверок с результатом каждого теста."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests' / 'helpers'))
from isolation_guard import child_environment, isolated_pytest_arguments, require_environment
from manual_qa_profile import PYTHON_MODULES, PYTHON_ADDITIONS, NODE_MODULES


def directory(identity):
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', identity):
        raise ValueError('Нужен UUID собственного прогона')
    path = ROOT / '.private-testing' / 'reports' / ('manual-qa-' + identity)
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('Недопустимый каталог отчёта')
    return path


class Results:
    def __init__(self, path):
        self.path, self.results, self.collection, self.count = path, {}, [], 0

    def pytest_collection_modifyitems(self, items):
        # Старый аудит писал в историческую папку; результаты нового прогона изолированы.
        for item in items:
            if item.path.name == 'test_catalog_sweep.py':
                item.module.REPORT = self.path / 'catalog-sweep'

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection.append({'nodeid': report.nodeid, 'error': redact(str(report.longrepr))})

    def pytest_collection_finish(self, session):
        (self.path / 'collected.json').write_text(json.dumps([item.nodeid for item in session.items],
                                                          ensure_ascii=False, indent=2), encoding='utf-8')

    def pytest_runtest_logreport(self, report):
        row = self.results.setdefault(report.nodeid, {'nodeid': report.nodeid, 'phases': [], 'seconds': 0})
        phase = {'when': report.when, 'outcome': report.outcome}
        if getattr(report, 'wasxfail', None):
            phase['expected_failure'] = report.wasxfail
        if report.failed or report.skipped:
            phase['detail'] = redact(str(report.longrepr))
        row['phases'].append(phase)
        row['seconds'] += report.duration
        if report.when == 'teardown':
            self.count += 1
            if self.count % 100 == 0:
                self.save(None)
                print('\nQA_PROGRESS ' + json.dumps({'completed': self.count}), flush=True)

    def pytest_sessionfinish(self, session, exitstatus):
        self.save(int(exitstatus))

    def save(self, exitstatus):
        result = {'exit_code': exitstatus, 'collection_errors': self.collection,
                  'tests': list(self.results.values())}
        (self.path / 'python.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def redact(value):
    return re.sub(r'eyJ[\w-]+\.[\w-]+\.[\w-]+', '[тестовый токен скрыт]', value)


def worker(identity, new_only=False, modules=None):
    require_environment(os.environ)
    if os.environ.get('TTRPG_MANUAL_QA') != 'approved-memory-01':
        raise ValueError('Нет допуска комплексной проверки')
    sys.path.insert(0, str(ROOT))
    path = directory(identity)
    import pytest
    modules = modules or (PYTHON_ADDITIONS if new_only else PYTHON_MODULES + PYTHON_ADDITIONS)
    return pytest.main(isolated_pytest_arguments(modules), plugins=[Results(path)])


def main():
    parser = argparse.ArgumentParser(description='Явный профиль проверок, только изолированные данные')
    parser.add_argument('--confirm-memory-tests', action='store_true', required=True)
    parser.add_argument('--new-only', action='store_true')
    parser.add_argument('--module', action='append', choices=PYTHON_MODULES + PYTHON_ADDITIONS)
    parser.add_argument('kind', choices=('python', 'node'))
    args = parser.parse_args()
    if args.new_only and args.kind != 'python':
        parser.error('--new-only применяется только к дополнительным Python-проверкам')
    if args.module and (args.kind != 'python' or args.new_only):
        parser.error('--module применяется к Python без --new-only')
    identity = str(uuid4())
    path = directory(identity)
    environment = child_environment(os.environ)
    if args.kind == 'python':
        environment['TTRPG_MANUAL_QA'] = 'approved-memory-01'
        bootstrap = ('import sys;sys.path.insert(0,sys.argv[1]);'
                     'import json;from run_manual_qa import worker;raise SystemExit(worker(sys.argv[2],bool(int(sys.argv[3])),json.loads(sys.argv[4])))')
        command = [str(ROOT / '.venv' / 'Scripts' / 'python.exe'), '-I', '-B', '-c', bootstrap,
                   str(ROOT / 'tests' / 'helpers'), identity, str(int(args.new_only)), json.dumps(args.module)]
    else:
        command = ['node', '--experimental-vm-modules', '--test', '--test-concurrency=1',
                   '--test-reporter=junit', *[str(ROOT / 'tests' / name) for name in NODE_MODULES]]
    print('Проверка ' + args.kind + ', результат: ' + str(path), flush=True)
    started = time.monotonic()
    log = path / ('node.junit.xml' if args.kind == 'node' else 'python.log')
    with log.open('w', encoding='utf-8') as output, (path / 'stderr.log').open('w', encoding='utf-8') as errors:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, encoding='utf-8', errors='replace')
        (path / 'run.json').write_text(json.dumps({'run_id': identity, 'kind': args.kind,
            'status': 'RUNNING', 'pid': process.pid}, ensure_ascii=False), encoding='utf-8')
        for line in process.stdout:
            output.write(redact(line))
            output.flush()
            if 'QA_PROGRESS ' in line:
                print(line[line.index('QA_PROGRESS '):].strip(), flush=True)
        code = process.wait()
    modules = (args.module or (PYTHON_ADDITIONS if args.new_only else PYTHON_MODULES + PYTHON_ADDITIONS)) if args.kind == 'python' else NODE_MODULES
    meta = {'run_id': identity, 'kind': args.kind, 'exit_code': code,
            'seconds': round(time.monotonic() - started, 3), 'modules': list(modules)}
    (path / 'run.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Завершено: ' + json.dumps({key: meta[key] for key in ('run_id', 'kind', 'exit_code', 'seconds')},
                                   ensure_ascii=False), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
