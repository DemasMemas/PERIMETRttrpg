"""Готовые группировки из авторской таблицы без неописанных отделений и связей."""
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


_DATA = json.loads((Path(__file__).resolve().parents[1] / 'data' /
    'world_factions_preset.json').read_text(encoding='utf-8'))


def ready_world_factions():
    entries = _DATA['groups']
    ids = {entry['name']: str(uuid5(NAMESPACE_URL,
        f'ttrpgsitezone:world-group:{entry["name"]}')) for entry in entries}
    groups = [{'id': ids[entry['name']], 'name': entry['name'], 'factions': []}
        for entry in entries]
    relations = [{'from_id': ids[entry['name']], 'to_id': ids[enemy], 'value': 'Враги'}
        for entry in entries for enemy in entry['enemies']]
    return {'groups': groups, 'relations': relations}
