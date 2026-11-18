"""Физические предметы на отдельных креплениях пояса."""
from copy import deepcopy
import re
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LobbyCharacter
from app.services.exceptions import ValidationError, ConflictError, NotFoundError
from app.services.transaction import atomic_operation
from app.services.action_journal import journaled

SLOT = 'belt_tool'
CAPACITY = 4
MOUNTS = {
    'tool': {'slot': SLOT, 'rule': 'belt_tool_mounts', 'capacity': CAPACITY, 'category': 'tool', 'label': 'Инструмент'},
    'artifact': {'slot': 'belt_artifact', 'rule': 'belt_artifact_mount', 'capacity': 1, 'category': 'artifact', 'label': 'Артефакт'},
    'detector': {'slot': 'belt_detector', 'rule': 'belt_detector_mount', 'capacity': 1, 'label': 'Детектор'},
    'radio': {'slot': 'belt_radio', 'rule': 'belt_radio_mount', 'capacity': 1, 'category': 'misc', 'label': 'Рация'},
    'grenade': {'slot': 'belt_grenade', 'rule': 'belt_grenade_mount', 'capacity': 1, 'category': 'grenade', 'label': 'Граната'},
    'dressing': {'slot': 'belt_dressing', 'rule': 'belt_dressing_mount', 'capacity': 2, 'label': 'Бинт или жгут'},
    'syringe': {'slot': 'belt_syringe', 'rule': 'belt_syringe_mount', 'capacity': 2, 'label': 'Препарат в шприце'},
    'restraint': {'slot': 'belt_restraint', 'rule': 'belt_restraint_mount', 'capacity': 15, 'category': 'misc', 'label': 'Хомут или верёвка'},
    'cartridge': {'slot': 'belt_cartridge', 'rule': 'belt_cartridge_mount', 'capacity': 50, 'category': 'ammo', 'label': 'Патроны'},
}
QUICK_MOUNTS = {'grenade', 'dressing', 'syringe'}
QUICK_SLOTS = {MOUNTS[kind]['slot'] for kind in QUICK_MOUNTS}
COUNTED_MOUNTS = {'restraint', 'cartridge'}
COUNTED_SLOTS = {MOUNTS[kind]['slot']: kind for kind in COUNTED_MOUNTS}
KNIFE_NAME = re.compile(r'\b(?:[а-яё]*нож|кортик)\b', re.I)


def rope(item):
    return str(item.get('name') or '').casefold().replace('ё', 'е').startswith('веревка')


def available(belt,kind='tool'):
    from app.services.equipment_modifications import rule
    return bool(isinstance(belt, dict) and not belt.get('protectionDisabled') and rule(belt, MOUNTS[kind]['rule']))


def loaded(belt,kind='tool'):
    return [(index, item) for index, item in enumerate((belt or {}).get('installedModules') or [])
            if isinstance(item, dict) and item.get('slotType') == MOUNTS[kind]['slot']]


def attached(belt):
    slots={mount['slot'] for mount in MOUNTS.values()}
    return [(index,item) for index,item in enumerate((belt or {}).get('installedModules') or [])
            if isinstance(item,dict) and item.get('slotType') in slots]


def weight_items(belt):
    return [item for _,item in attached(belt) if item.get('slotType')!=SLOT or not available(belt)]


def tool_path(path):
    return (isinstance(path, list) and len(path) == 4
            and path[:3] == ['equipment', 'belt', 'installedModules'] and type(path[3]) is int)


def accessible(data, path, item):
    belt = (data.get('equipment') or {}).get('belt') or {}
    return bool(tool_path(path) and any(index == path[3] and entry == item for index, entry in loaded(belt)))


def accepts(kind, template):
    if kind == 'detector':
        return template.category == 'detector' or (
            template.category == 'device' and (template.attributes or {}).get('device_kind') == 'dosimeter')
    if kind == 'radio':
        return template.category == 'misc' and template.name == 'Рация'
    if kind == 'dressing':
        return template.category == 'consumable' and bool(re.search(r'\b(?:бинт|жгут|турникет)\b', template.name, re.I))
    if kind == 'syringe':
        direct = ((template.attributes or {}).get('consumable') or {}).get('direct') or {}
        return template.category == 'consumable' and direct.get('application_form') == 'injectable'
    if kind == 'restraint':
        return template.category == 'misc' and (template.name == 'Хомут пластиковый' or rope({'name': template.name}))
    return template.category == MOUNTS[kind]['category']


def fixed_access(data, path, item):
    kind = COUNTED_SLOTS.get(item.get('slotType'))
    if kind is None:
        return False
    belt = (data.get('equipment') or {}).get('belt')
    if not tool_path(path) or not available(belt, kind) or not any(
            index == path[3] and entry is item for index, entry in loaded(belt, kind)):
        raise ValidationError('Наденьте и включите пояс с этим креплением')
    return True


def holstered(data, weapon):
    from app.services.equipment_modifications import rule

    belt = (data.get('equipment') or {}).get('belt') or {}
    return bool(weapon.get('id') and belt.get('holsterWeaponId') == weapon['id']
                and not belt.get('protectionDisabled') and rule(belt, 'belt_holster'))


def cartridge_visual(item):
    attrs = item.get('attributes') or {}
    caliber = str(item.get('caliber') or attrs.get('caliber') or item.get('name') or '').casefold()
    caliber = re.sub(r'\s+', '', caliber.replace(',', '.').replace('х', 'x').replace('×', 'x'))
    if caliber.startswith(('12x', '16x', '20x', '410')):
        return 'shell'
    if caliber.startswith(('7.62x51', '7.62x54', '7.62x63', '12.7x')):
        return 'rifle'
    if caliber.startswith(('5.45x39', '5.56x45', '7.62x39', '9x39')):
        return 'intermediate'
    return 'pistol'


def belt_visuals(data, held_indices=()):
    """Только силуэты, количества и заполненность, без идентификаторов предметов."""
    from app.services.character_appearance import equipment_visual
    from app.services.equipment_modifications import rule

    belt = (data.get('equipment') or {}).get('belt')
    if not isinstance(belt, dict) or not belt:
        return None
    result = {'pouches': min(7, sum(isinstance(pouch, dict) and bool(pouch) for pouch in belt.get('pouches') or []))}
    stored = belt.get('storedItem')
    stored = stored if isinstance(stored, dict) else {}
    attributes = stored.get('savedAttributes')
    primary = {**stored, **attributes} if isinstance(attributes, dict) else stored
    headgear = []
    for item, category in ((primary, stored.get('type')), (belt.get('extraStoredItem'), None)):
        if isinstance(item, dict) and (category or item.get('category')) in {'helmet', 'gas_mask'}:
            slot = 'helmet' if (category or item.get('category')) == 'helmet' else 'gasMask'
            if visual := equipment_visual(item, slot):
                headgear.append(visual)
    if headgear:
        result['headgear'] = headgear
    for kind in ('grenade', 'syringe', 'detector', 'radio', 'dressing', 'artifact', 'tool'):
        if count := min(MOUNTS[kind]['capacity'], len(loaded(belt, kind))):
            result[kind] = count
    restraints = [item for _, item in loaded(belt, 'restraint')]
    if any(rope(item) for item in restraints):
        result['rope'] = True
    elif restraints:
        result['ties'] = min(15, sum(_stack_quantity(item) for item in restraints))
    ammo = []
    for _, item in loaded(belt, 'cartridge'):
        ammo.extend([cartridge_visual(item)] * min(50 - len(ammo), _stack_quantity(item)))
    if ammo or rule(belt, 'belt_cartridge_mount'):
        result['ammo'] = ammo
        if (data.get('equipment') or {}).get('armor'):
            result['armor'] = True
    stowed = {item.get('id') for index, item in enumerate(data.get('weapons') or [])
              if index not in held_indices and isinstance(item, dict) and item.get('id')}
    if rule(belt, 'belt_holster'):
        result['holster'] = 'filled' if belt.get('holsterWeaponId') in stowed else 'empty'
    if 'sheathWeaponId' in belt and rule(belt, 'free_pouch_mounts'):
        result['sheath'] = 'filled' if belt.get('sheathWeaponId') in stowed else 'empty'
    return result


def _stack_quantity(item):
    value = item.get('quantity', 1)
    return max(0, value) if type(value) is int else 0


def preserve(current, incoming):
    """Сохранение листа не снимает, не копирует и не перезаправляет вложения."""
    from app.services.portable_lights import walk
    old_belts = {item['id']: item for _, item in walk(current or {}, True)
                 if item.get('id') and (attached(item) or item.get('holsterWeaponId') or 'sheathWeaponId' in item)}
    incoming_items = list(walk(incoming, True))
    old_holsters = {item['id']: item['holsterWeaponId'] for _, item in walk(current or {}, True)
                    if item.get('id') and item.get('holsterWeaponId')}
    old_sheaths = {item['id']: item['sheathWeaponId'] for _, item in walk(current or {}, True)
                   if item.get('id') and 'sheathWeaponId' in item}
    weapon_ids = {weapon.get('id') for weapon in incoming.get('weapons') or [] if isinstance(weapon, dict)}
    for _, item in incoming_items:
        bound = old_holsters.get(item.get('id'))
        if bound and bound in weapon_ids:
            item['holsterWeaponId'] = bound
        else:
            item.pop('holsterWeaponId', None)
        if item.get('id') in old_sheaths:
            sheath_weapon = old_sheaths[item['id']]
            item['sheathWeaponId'] = sheath_weapon if sheath_weapon in weapon_ids else None
        else:
            item.pop('sheathWeaponId', None)
    for belt_id, old in old_belts.items():
        matches = [item for _, item in incoming_items if item.get('id') == belt_id]
        if len(matches) > 1:
            raise ValidationError('Пояс с закреплёнными предметами повторяется в инвентаре')
        tool_ids = {item.get('id') for _, item in attached(old)}
        for path, item in incoming_items:
            if item.get('id') not in tool_ids:
                continue
            # Единственное законное место — модуль того же физического пояса.
            parent_path = path[:-2] if len(path) >= 2 and path[-2] == 'installedModules' else None
            parent = incoming
            for key in parent_path or []:
                parent = parent[key]
            if parent_path is None or not isinstance(parent, dict) or parent.get('id') != belt_id:
                raise ValidationError('Снимите предмет с пояса отдельным действием')
        if matches:
            belt = matches[0]
            belt['installedModules'] = [entry for entry in belt.get('installedModules') or []
                                       if not isinstance(entry, dict) or entry.get('slotType') not in {m['slot'] for m in MOUNTS.values()}
                                       and entry.get('id') not in tool_ids]
            belt['installedModules'].extend(deepcopy(item) for _, item in attached(old))
    # Новые крепления создаются только серверным действием.
    allowed_ids = {item.get('id') for old in old_belts.values() for _, item in attached(old)}
    for _, item in walk(incoming, True):
        if item.get('slotType') in {m['slot'] for m in MOUNTS.values()} and item.get('id') not in allowed_ids:
            raise ValidationError('Закрепите предмет на поясе отдельным действием')


@journaled(kind='belt_attachment')
@atomic_operation
def operate(character_id, user_id, payload):
    from app.services.character import CharacterService
    from app.services.auto_injector import combat_actor
    from app.services.combat import CombatService
    from app.services.equipment_module import _validate_source_path
    from app.services.magazine import inventory_slot
    from app.services.item_template_lookup import resolve_item_template
    if not isinstance(payload, dict) or set(payload) - {'operation', 'kind', 'character_revision', 'belt_id', 'source', 'quantity', 'weapon_id'}:
        raise ValidationError('Некорректное действие с креплением пояса')
    kind=payload.get('kind','tool')
    if not isinstance(kind,str) or kind not in {*MOUNTS, 'holster', 'sheath'}:raise ValidationError('Неизвестное крепление пояса')
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
    actor = combat_actor(character, user_id, action_name='Крепление пояса')
    operation = payload.get('operation')
    if kind in {'holster', 'sheath'}:
        return change_weapon_mount(character, data, belt, actor, operation, payload.get('weapon_id'), kind)
    mount = MOUNTS[kind]
    source = payload.get('source') or {}
    if not isinstance(source,dict):raise ValidationError('Выберите предмет для крепления')
    path = source.get('path')
    _validate_source_path(path)
    parent, index = inventory_slot(data, path)
    item = parent[index]
    if item != source.get('snapshot'):
        raise ConflictError('Предмет перемещён. Выберите его заново')
    if operation == 'mount':
        if not available(belt,kind) or len(loaded(belt,kind)) >= mount['capacity']:
            raise ValidationError('На поясе нет свободного крепления для этого предмета')
        if 'installedModules' in path:
            raise ValidationError('Этот предмет уже закреплён')
        template = resolve_item_template(item, character.lobby_id)
        if not template or not accepts(kind, template) or item.get('category') != template.category or item.get('name') != template.name:
            raise ValidationError('На это крепление нужен предмет: '+mount['label'])
        if type(item.get('quantity', 1)) is not int or item.get('quantity', 1) < 1:
            raise ValidationError('Некорректная стопка предметов')
        if item.get('quantity', 1) > 1 and any(item.get(key) for key in ('contents', 'installedModules', 'pouches')):
            raise ValidationError('Сначала разделите заполненные предметы на отдельные экземпляры')
        quantity = payload.get('quantity', 1)
        if kind in COUNTED_MOUNTS:
            existing = [entry for _, entry in loaded(belt, kind)]
            capacity = mount['capacity'] - sum(entry.get('quantity', 1) for entry in existing)
            if kind == 'restraint':
                if any(rope(entry) for entry in existing) or rope(item) and existing:
                    raise ValidationError('На законном ремне помещаются либо хомуты, либо одна верёвка')
                if rope(item):
                    capacity = 1
            if type(quantity) is not int or not 1 <= quantity <= min(capacity, item.get('quantity', 1)):
                raise ValidationError('Количество превышает свободное место или остаток в стопке')
        elif type(quantity) is not int or quantity != 1:
            raise ValidationError('Закреплять можно по одному предмету')
        candidate = deepcopy(item)
        candidate.update(quantity=quantity, slotType=mount['slot'])
        if item.get('quantity', 1) > quantity:
            item['quantity'] -= quantity
            candidate['id'] = str(uuid4())
        else:
            parent.pop(index)
            if not candidate.get('id'):candidate['id'] = str(uuid4())
        belt.setdefault('installedModules', []).append(candidate)
    elif operation in ('unmount', 'retrieve'):
        if not tool_path(path) or not any(i==index and entry==item for i,entry in loaded(belt,kind)):
            raise ValidationError('Выберите предмет на соответствующем креплении надетого пояса')
        if operation == 'retrieve':
            if kind not in QUICK_MOUNTS | COUNTED_MOUNTS or not available(belt,kind):
                raise ValidationError('На поясе нет действующего крепления быстрого доступа')
            holder = actor or SimpleNamespace(character=character, drawn_weapon_index=data.get('activeWeaponIndex'))
            if not CombatService._has_usable_free_hand(holder):
                raise ValidationError('Чтобы достать предмет, нужна свободная действующая рука')
        parent.pop(index)
        item.pop('slotType', None)
        destination = 'held' if operation == 'retrieve' else 'pockets'
        data.setdefault('inventory', {}).setdefault(destination, []).append(item)
    else:
        raise ValidationError('Выберите действие с креплением')
    free = int(bool(actor and operation == 'retrieve' and kind in QUICK_MOUNTS and actor.free_actions_current > 0))
    cost = (1-free if operation == 'retrieve' else 3) if actor else 0
    if actor:
        if actor.action_points_current < cost:
            raise ValidationError('Чтобы достать предмет, нужен 1 ОД' if operation == 'retrieve' and kind in COUNTED_MOUNTS
                                  else 'Чтобы достать предмет, нужен 1 ОД или 1 СД' if operation == 'retrieve'
                                  else 'Для этого действия нужно 3 ОД')
        actor.action_points_current -= cost
        actor.free_actions_current -= free
        CombatService._clear_aim(actor)
    character.data = data
    from app.services.artifact_effects import needs_clocks,sync_clocks
    if needs_clocks(data):data.setdefault('health',{})['effects']=sync_clocks(data)
    flag_modified(character, 'data')
    return character, actor, {'operation': operation, 'kind':kind, 'action_points': cost, 'free_actions': free}


def change_weapon_mount(character, data, belt, actor, operation, weapon_id, kind):
    from app.services.combat import CombatService
    from app.services.equipment_modifications import belt_general_pouch_limit, belt_pouch_limit, magazine_pouch, rule
    from app.services.item_template_lookup import resolve_item_template
    from app.services.weapon_grip import held

    sheath = kind == 'sheath'
    key = 'sheathWeaponId' if sheath else 'holsterWeaponId'
    weapons = data.get('weapons') or []
    if sheath:
        if operation == 'unmount' and key in belt:
            belt.pop(key)
        elif operation == 'mount' and rule(belt, 'free_pouch_mounts') and not belt.get('protectionDisabled'):
            if belt.get(key) and any(isinstance(item, dict) and item.get('id') == belt[key] for item in weapons):
                raise ValidationError('Сначала освободите ножны')
            proposed = {**belt, key: None}
            pouches = belt.get('pouches') or []
            if len(pouches) > belt_pouch_limit(proposed, character.lobby_id) or sum(
                    not magazine_pouch(pouch, character.lobby_id) for pouch in pouches
            ) > belt_general_pouch_limit(proposed, character.lobby_id):
                raise ValidationError('Освободите дополнительное крепление для ножен')
            if weapon_id is not None:
                if not isinstance(weapon_id, str) or not weapon_id:
                    raise ValidationError('Выберите нож для ножен')
                index = next((i for i, weapon in enumerate(weapons) if isinstance(weapon, dict)
                              and weapon.get('id') == weapon_id), None)
                template = resolve_item_template(weapons[index], character.lobby_id) if index is not None else None
                if (not template or template.category != 'melee_weapon' or not KNIFE_NAME.search(template.name)
                        or weapon_id == belt.get('holsterWeaponId')):
                    raise ValidationError('Для ножен выберите отдельный нож из экипированного оружия')
                in_hand = held(actor, index) if actor else data.get('activeWeaponIndex') == index
                if in_hand:
                    raise ValidationError('Сначала уберите нож из руки')
            belt[key] = weapon_id
        else:
            raise ValidationError('Для ножен необходимо дополнительное крепление пояса')
    elif operation == 'mount':
        if not isinstance(weapon_id, str) or not weapon_id or belt.get('protectionDisabled') or not rule(belt, 'belt_holster'):
            raise ValidationError('Выберите оружие для действующей кобуры')
        bound = belt.get('holsterWeaponId')
        if bound and any(item.get('id') == bound for item in data.get('weapons') or [] if isinstance(item, dict)):
            raise ValidationError('Сначала освободите кобуру')
        weapon = next((item for item in data.get('weapons') or [] if isinstance(item, dict) and item.get('id') == weapon_id), None)
        if not weapon:
            raise ConflictError('Оружие перемещено. Выберите его заново')
        if belt.get('sheathWeaponId') == weapon_id:
            raise ValidationError('Сначала освободите ножны')
        template = resolve_item_template(weapon, character.lobby_id)
        attributes = (template.attributes or {}) if template else {}
        size = CombatService._coerce_float(weapon.get('size', (weapon.get('attributes') or {}).get('size', attributes.get('size'))), 0)
        if weapon.get('naturalWeapon') or attributes.get('natural_weapon') or not 0 < size < 4:
            raise ValidationError('В кобуру помещается оружие размером меньше 4')
        index = data['weapons'].index(weapon)
        if (actor and held(actor, index)) or (not actor and data.get('activeWeaponIndex') == index):
            raise ValidationError('Сначала уберите оружие из рук')
        belt['holsterWeaponId'] = weapon_id
    elif operation == 'unmount' and belt.get('holsterWeaponId') == weapon_id and weapon_id:
        belt.pop('holsterWeaponId')
    else:
        raise ValidationError('Оружие в кобуре изменилось')
    cost = 3 if actor else 0
    if actor:
        if actor.action_points_current < cost:
            raise ValidationError('Для этого действия нужно 3 ОД')
        actor.action_points_current -= cost
        CombatService._clear_aim(actor)
    character.data = data
    flag_modified(character, 'data')
    return character, actor, {'operation': operation, 'kind': kind, 'action_points': cost, 'free_actions': 0}
