"""Явный запуск только согласованных Python-тестов в дочернем процессе."""
import argparse
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation_guard import child_environment, validated_modules


def main():
    parser = argparse.ArgumentParser(description='Изолированные тесты: только SQLite в памяти')
    parser.add_argument('--confirm-memory-tests', action='store_true', required=True,
                        help='Явно разрешить тесты с одноразовой SQLite в памяти')
    parser.add_argument('modules', nargs='+', metavar='SELECTOR',
                        help='Согласованный целый модуль либо точный file::test')
    args = parser.parse_args()
    modules = validated_modules(args.modules)
    root = Path(__file__).resolve().parents[2]
    python = root / '.venv' / 'Scripts' / 'python.exe'
    if not python.is_file():
        parser.error('Не найден Python локального .venv')
    import os
    env = child_environment(os.environ)
    bootstrap = (
        'import sys; from pathlib import Path; '
        'root=Path(sys.argv[1]); sys.path.insert(0,str(root)); '
        'sys.path.insert(0,str(root/"tests"/"helpers")); '
        'from isolation_guard import isolated_pytest_arguments; '
        'arguments=isolated_pytest_arguments(sys.argv[2:]); '
        'import pytest; '
        'raise SystemExit(pytest.main(arguments))'
    )
    print('Запуск согласованных селекторов; dotenv и автозагрузка сторонних плагинов отключены.')
    return subprocess.run([str(python), '-I', '-B', '-c', bootstrap, str(root), *modules],
                          cwd=root, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
