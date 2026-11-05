from __future__ import annotations

import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def _find_default_workbook() -> Path | None:
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        return None
    candidates = sorted(
        [path for path in downloads.glob("*.xlsx") if "Снаряжение" in path.name],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description='Загрузка снаряжения из XLSX или опубликованного каталога JSON')
    parser.add_argument('source', nargs='?')
    parser.add_argument('--missing-only', action='store_true', help='Только отсутствующие записи, без перезаписи настроенных предметов')
    parser.add_argument('--dry-run', action='store_true', help='Прочитать источник без подключения к БД')
    parser.add_argument('--full-catalog', action='store_true', help='Включить расходники и принятые правила модулей')
    parser.add_argument('--export-json', type=Path, help='Сохранить отдельный игровой каталог JSON, без подключения к БД')
    args = parser.parse_args()
    if args.dry_run or args.export_json:
        import os
        os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    workbook_arg = Path(args.source).expanduser() if args.source else _find_default_workbook()
    if workbook_arg is None or not workbook_arg.exists():
        print('Источник не найден. Укажите путь к XLSX или JSON.')
        return 1
    from app.services.equipment_catalog import parse_catalog, load_catalog
    from app.services.equipment_importer import parse_equipment_templates, insert_missing_templates
    is_snapshot = workbook_arg.suffix.lower() == '.json'
    entries = load_catalog(workbook_arg) if is_snapshot else (
        parse_catalog(workbook_arg) if args.full_catalog else parse_equipment_templates(workbook_arg))
    if args.export_json:
        if args.export_json.suffix.lower() != '.json' or args.export_json.resolve() == workbook_arg.resolve():
            parser.error('Результат должен быть отдельным JSON; исходный файл не перезаписывается')
        import json
        from app.services.equipment_catalog import prepare_catalog
        args.export_json.write_text(json.dumps({'version':1, 'templates':prepare_catalog(entries)},
            ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Сохранено {len(entries)} предметов в JSON. БД не открывалась.')
        return 0
    if args.dry_run:
        from collections import Counter
        print(f'Прочитано предметов: {len(entries)}. БД не изменялась.')
        for category, count in sorted(Counter(item['category'] for item in entries).items()):
            print(f'{category}: {count}')
        return 0
    if (args.full_catalog or is_snapshot) and not args.missing_only:
        parser.error('Для полного каталога требуется --missing-only: существующие предметы сохраняются')
    from app import create_app
    from app.services.equipment_importer import upsert_equipment_templates
    app = create_app()
    with app.app_context():
        if args.missing_only:
            from app.extensions import db
            result = insert_missing_templates(entries)
            db.session.commit()
        else:
            result = upsert_equipment_templates(workbook_arg)
        print(
            f"Каталог загружен: прочитано {result['parsed']}, "
            f"добавлено {result['inserted']}, обновлено {result['updated']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
