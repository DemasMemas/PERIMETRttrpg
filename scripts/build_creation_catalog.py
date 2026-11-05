"""Собрать игровые данные из исходной книги, не открывая БД и не меняя XLSX."""
import argparse
import json
import os
from pathlib import Path
import sys

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description='Рецепты и модификации из книги правил')
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'app/data/creation_rules.json')
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve() or args.output.suffix.lower() != '.json':
        parser.error('Результат должен быть отдельным JSON; исходная книга не перезаписывается')
    from app.services.crafting_catalog import parse_creation_catalog
    catalog = parse_creation_catalog(args.source)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Рецептов: {len(catalog['recipes'])}; модификаций: {len(catalog['modifications'])}; станков: {len(catalog['workbenches'])}. БД не открывалась.")


if __name__ == '__main__':
    main()
