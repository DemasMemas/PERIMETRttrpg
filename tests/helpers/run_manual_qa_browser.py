"""Последовательный запуск утверждённых браузерных сценариев с контролем очистки."""
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
sys.path.insert(0, str(ROOT / 'tests/helpers'))
from isolation_guard import child_environment
SCENARIOS = (
    'trade', 'creation', 'traits', 'weapons', 'health-audit', 'morning-answers',
    'medical-retry', 'stations', 'covers', 'traps', 'improvised', 'controllers',
    'electronics', 'locks', 'equipment', 'lights', 'effects', 'artifacts',
    'fire-rate', 'magazine', 'skills', 'psi', 'movement', 'height', 'terrain',
    'walls', 'climb', 'jump', 'anomaly-boar', 'ice', 'slippery', 'grenades', 'weather',
    'world-loading', 'widgets', 'rotation', 'surface', 'gm-relocation',
    'durable-reload', 'damage-revoke',
    'building-shell', 'editor-history', 'fog-water', 'water-final',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-isolated-browser', action='store_true', required=True)
    parser.add_argument('--scenario', action='append', choices=SCENARIOS)
    args = parser.parse_args()
    selected = list(dict.fromkeys(args.scenario or SCENARIOS))
    folder = ROOT / '.private-testing' / 'reports' / ('browser-qa-' + str(uuid4()))
    folder.mkdir(parents=True)
    if folder.is_symlink() or folder.resolve() != folder:
        raise ValueError('Недопустимый каталог результатов')
    results = []
    print('Очередь браузера: ' + str(folder), flush=True)
    for scenario in selected:
        print('Сценарий: ' + scenario, flush=True)
        started = time.monotonic()
        run_id = None
        exception_count = 0
        with (folder / (scenario + '.log')).open('w', encoding='utf8') as log:
            # Дочерний запускатель сам очищает окружение до запуска сервера и Chrome.
            process = subprocess.Popen(
                ['node', str(ROOT / 'tests/helpers/run_isolated_browser.cjs'),
                 '--confirm-isolated-browser', scenario], cwd=ROOT, env=child_environment(os.environ),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding='utf8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
            for line in process.stdout:
                log.write(re.sub(r'eyJ[\w-]+\.[\w-]+\.[\w-]+', '[тестовый токен скрыт]', line))
                log.flush()
                match = re.search(r'Безопасный запуск ([0-9a-f-]{36});', line)
                if match:
                    run_id = match.group(1)
                if line.startswith('Browser exception: '):
                    exception_count += 1
            code = process.wait()
        resources = {}
        if run_id:
            resource_file = ROOT / '.private-testing/reports' / run_id / 'resources.json'
            if resource_file.is_file():
                resources = json.loads(resource_file.read_text(encoding='utf8'))
        cleanup = resources.get('cleanup') == 'confirmed'
        row = {'scenario': scenario, 'run_id': run_id, 'exit_code': code,
               'seconds': round(time.monotonic() - started, 3), 'cleanup_confirmed': cleanup,
               'unhandled_exception_count': exception_count}
        results.append(row)
        (folder / 'results.json').write_text(json.dumps(
            {'requested': selected, 'results': results, 'stopped_for_cleanup': not cleanup},
            ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if not cleanup:
            print('Очистка не подтверждена. Следующие сценарии не запущены.', flush=True)
            return 2
    return int(any(row['exit_code'] or row['unhandled_exception_count'] for row in results))


if __name__ == '__main__':
    sys.exit(main())
