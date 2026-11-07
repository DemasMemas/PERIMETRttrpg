"""Установка каталожных модификаций на конкретный КПК."""
from copy import deepcopy

from app.services.combat import CombatService
from app.services.crafting_rules import catalog, creation_quote
from app.services.exceptions import ValidationError
from app.services.general_consumable import _available_uses
from app.services.portable_lights import definition, walk

GROUPS = {
    'Низкая': (40, 500, [('Электронные материалы', 1)]),
    'Средняя': (55, 750, [('Электронные материалы', 2)]),
    'Высокая': (70, 1500, [('Плата', 1), ('Электронные материалы', 1)]),
    'Очень высокая': (90, 4500, [('Плата', 2), ('Электронные материалы', 4)]),
}


def rule_by_id(rule_id):
    rule = next((entry for entry in catalog()['modifications']
                 if entry['kind'] == 'pda' and entry['id'] == rule_id), None)
    if not rule:raise ValidationError('Модификация КПК не найдена')
    return rule


def record(rule, installer_id):
    return {'ruleId': rule['id'], 'name': rule['name'], 'description': rule['description'], 'level': 1,
            'installerId': installer_id, 'modifiers': {}, 'rules': {'special_rule': rule['id']}}


def target(data, payload):
    path = payload.get('target_path')
    if not isinstance(path, list):raise ValidationError('Выберите КПК')
    found = next((item for current, item in walk(data, True)
                  if current == path and definition(item) == 'pda'), None)
    if not found or not isinstance(payload.get('item_id'), str) or found.get('id') != payload['item_id']:
        raise ValidationError('КПК перемещён. Откройте установку заново.')
    if found.get('quantity', 1) != 1:raise ValidationError('Разделите стопку КПК')
    return found


def plan(character, data, payload):
    from app.services.character_traits import trait_count
    from app.services.crafting import _allocate, _tools, entries
    from app.services.equipment_passives import craft_difficulty_modifier
    if not trait_count(data, 'Программист'):
        raise ValidationError('Для самостоятельной установки нужна черта «Программист»')
    rule = rule_by_id(payload.get('rule_id'))
    device = target(data, payload)
    if any(entry.get('ruleId') == rule['id'] for entry in device.get('modifications') or []
           if isinstance(entry, dict)):
        raise ValidationError('Модификация уже установлена на этот КПК')
    difficulty, equivalent, materials = GROUPS[rule['difficulty_group']]
    station_recipe = {'id': 'pda-install', 'group': 'Электроника', 'tools_text': 'Станок Электрика',
                      'difficulty_options': [difficulty], 'components': []}
    has_station = not _tools(character, data, station_recipe, 0)
    tool_path = payload.get('tool_path')
    tool = next((item for path, item in entries(data) if path == tool_path), None) if tool_path else None
    solder = bool(tool and 'паяльные инструменты' in str(tool.get('name') or '').casefold()
                  and _available_uses(tool) >= 1)
    from app.services.portable_stations import selected
    station = selected(character, station_recipe) if has_station and (difficulty > 55 or not solder) else None
    station_key = (station.properties or {}).get('portable_station') if station else None
    missing = []
    if not solder and not has_station:missing.append('Нужны паяльные инструменты с зарядом или Станок Электрика')
    if difficulty > 55 and not has_station:missing.append('Для этой сложности нужен Станок Электрика')
    recipe = {'id': 'pda-install', 'components': [{'name': name, 'quantity': count} for name, count in materials]}
    allocations, absent, status = _allocate(data, recipe, with_status=True)
    missing.extend(absent)
    level = CombatService._skill_value(data, 'skills.other.engineering')
    quote = creation_quote(level, difficulty, difficulty_modifier=craft_difficulty_modifier(data))
    return {**quote, 'rule': rule, 'target_id': device['id'], 'target_path': payload['target_path'],
            'tool_path': tool_path if solder else None, 'has_station': has_station,
            'station_id': station.id if station_key else None,
            'station_name': station.name if station_key else None,
            'spoil_chance': 15 if station_key else 0,
            'allocations': allocations, 'component_status': status, 'missing': missing,
            'duration_seconds': 3600, 'equivalent': equivalent}
