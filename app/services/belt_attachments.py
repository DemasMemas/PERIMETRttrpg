"""Физические инструменты на креплениях пояса."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LobbyCharacter
from app.services.exceptions import ValidationError, ConflictError, NotFoundError
from app.services.transaction import atomic_operation
from app.services.action_journal import journaled

SLOT = 'belt_tool'
CAPACITY = 4


def available(belt):
    from app.services.equipment_modifications import rule
    return bool(isinstance(belt, dict) and not belt.get('protectionDisabled') and rule(belt, 'belt_tool_mounts'))


def loaded(belt):
    return [(index, item) for index, item in enumerate((belt or {}).get('installedModules') or [])
            if isinstance(item, dict) and item.get('slotType') == SLOT]


def tool_path(path):
    return (isinstance(path, list) and len(path) == 4
            and path[:3] == ['equipment', 'belt', 'installedModules'] and type(path[3]) is int)


def accessible(data, path, item):
    belt = (data.get('equipment') or {}).get('belt') or {}
    return bool(tool_path(path) and any(index == path[3] and entry == item for index, entry in loaded(belt)))


def preserve(current, incoming):
    """Сохранение листа не снимает, не копирует и не перезаправляет инструменты."""
    from app.services.portable_lights import walk
    old_belts = {item['id']: item for _, item in walk(current or {}, True)
                 if item.get('id') and loaded(item)}
    incoming_items = list(walk(incoming, True))
    for belt_id, old in old_belts.items():
        matches = [item for _, item in incoming_items if item.get('id') == belt_id]
        if len(matches) > 1:
            raise ValidationError('Пояс с инструментами повторяется в инвентаре')
        tool_ids = {item.get('id') for _, item in loaded(old)}
        for path, item in incoming_items:
            if item.get('id') not in tool_ids:
                continue
            # Единственное законное место — модуль того же физического пояса.
            parent_path = path[:-2] if len(path) >= 2 and path[-2] == 'installedModules' else None
            parent = incoming
            for key in parent_path or []:
                parent = parent[key]
            if parent_path is None or not isinstance(parent, dict) or parent.get('id') != belt_id:
                raise ValidationError('Снимите инструмент с пояса отдельным действием')
        if matches:
            belt = matches[0]
            belt['installedModules'] = [entry for entry in belt.get('installedModules') or []
                                       if not isinstance(entry, dict) or entry.get('slotType') != SLOT]
            belt['installedModules'].extend(deepcopy(item) for _, item in loaded(old))
    # Новые крепления создаются только серверным действием.
    allowed_ids = {item.get('id') for old in old_belts.values() for _, item in loaded(old)}
    for _, item in walk(incoming, True):
        if item.get('slotType') == SLOT and item.get('id') not in allowed_ids:
            raise ValidationError('Закрепите инструмент на поясе отдельным действием')


@journaled(kind='belt_attachment')
@atomic_operation
def operate(character_id, user_id, payload):
    from app.services.character import CharacterService
    from app.services.auto_injector import combat_actor
    from app.services.combat import CombatService
    from app.services.equipment_module import _validate_source_path
    from app.services.magazine import inventory_slot
    from app.services.item_template_lookup import resolve_item_template
    if not isinstance(payload, dict) or set(payload) - {'operation', 'character_revision', 'belt_id', 'source'}:
        raise ValidationError('Некорректное действие с креплением пояса')
    character = db.session.get(LobbyCharacter, character_id)
    if not character:
        raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character, user_id, edit=True)
    if type(payload.get('character_revision')) is not int or payload['character_revision'] != character.revision:
        raise ConflictError('Лист изменился. Откройте его заново')
    data = deepcopy(character.data or {})
    belt = (data.get('equipment') or {}).get('belt')
    if not isinstance(belt, dict) or not belt.get('id') or belt['id'] != payload.get('belt_id'):
        raise ConflictError('Пояс перемещён. Откройте лист заново')
    actor = combat_actor(character, user_id, action_name='Крепление инструмента')
    operation = payload.get('operation')
    source = payload.get('source') or {}
    path = source.get('path')
    _validate_source_path(path)
    parent, index = inventory_slot(data, path)
    item = parent[index]
    if item != source.get('snapshot'):
        raise ConflictError('Инструмент перемещён. Выберите его заново')
    if operation == 'mount':
        if not available(belt) or len(loaded(belt)) >= CAPACITY:
            raise ValidationError('На поясе нет свободного крепления для инструмента')
        if 'installedModules' in path:
            raise ValidationError('Этот инструмент уже закреплён')
        template = resolve_item_template(item, character.lobby_id)
        if not template or template.category != 'tool' or item.get('category') != 'tool' or item.get('name') != template.name:
            raise ValidationError('На это крепление можно повесить только инструмент')
        if type(item.get('quantity', 1)) is not int or item.get('quantity', 1) < 1:
            raise ValidationError('Некорректная стопка инструментов')
        if item.get('quantity', 1) > 1 and any(item.get(key) for key in ('contents', 'installedModules', 'pouches')):
            raise ValidationError('Сначала разделите заполненные инструменты на отдельные экземпляры')
        candidate = deepcopy(item)
        candidate.update(quantity=1, slotType=SLOT)
        if item.get('quantity', 1) > 1:
            item['quantity'] -= 1
            candidate['id'] = str(uuid4())
        else:
            parent.pop(index)
            if not candidate.get('id'):candidate['id'] = str(uuid4())
        belt.setdefault('installedModules', []).append(candidate)
    elif operation == 'unmount':
        if not accessible(data, path, item):
            raise ValidationError('Выберите инструмент на надетом поясе')
        parent.pop(index)
        item.pop('slotType', None)
        data.setdefault('inventory', {}).setdefault('pockets', []).append(item)
    else:
        raise ValidationError('Выберите установку или снятие инструмента')
    cost = 3 if actor else 0
    if actor:
        if actor.action_points_current < cost:
            raise ValidationError('Для этого действия нужно 3 ОД')
        actor.action_points_current -= cost
        CombatService._clear_aim(actor)
    character.data = data
    flag_modified(character, 'data')
    return character, actor, {'operation': operation, 'action_points': cost}
