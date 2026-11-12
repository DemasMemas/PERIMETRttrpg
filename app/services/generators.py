"""Заправка, зарядка аккумуляторов и питание станков на подлокации."""
from copy import deepcopy
from math import hypot, isfinite
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import Location, LocationObject
from app.services import power_cells
from app.services.combat import CombatService
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.liquid_containers import liquid_capacity, liquid_kind
from app.services.location_navigation import level_of, pose
from app.services.magazine import inventory_slot
from app.services.portable_lights import walk
from app.services.portable_stations import PROFILES, _actor
from app.services.transaction import atomic_operation
from app.services.workbenches import NAMES

ASSET = 'household_generator'
SECONDS_PER_LITER = 24 * 3600 // 5
FULL_CHARGE_SECONDS = 3600
EXOSKELETON_CHARGE_SECONDS = 4 * 3600
CONNECTION_RANGE = 3
CONNECTION_LIMIT = 4
DIFFICULTY_BONUS = 10


def is_generator(obj):
    props = obj.properties or {}
    return obj.type == ASSET or props.get('visual_asset') == ASSET


def _state(obj):
    return (obj.properties or {}).get('generator') or {}


def _station_name(obj):
    props = obj.properties or {}
    profile = PROFILES.get(props.get('portable_station'))
    name = profile[2] if profile else props.get('craft_workbench') or obj.name
    return name if name in NAMES[2:] else None


def _can_connect(generator, station):
    station_x, station_y = pose(station)[:2]
    generator_x, generator_y = pose(generator)[:2]
    return bool(station.location_id == generator.location_id
        and not (station.properties or {}).get('gm_hidden')
        and level_of(station) == level_of(generator)
        and hypot(station_x - generator_x, station_y - generator_y) <= CONNECTION_RANGE
        and _station_name(station))


def _linked_generator(station):
    identity = (station.properties or {}).get('power_generator_id')
    generator = db.session.get(LocationObject, identity) if type(identity) is int else None
    if generator and generator.location_id == station.location_id and is_generator(generator):
        return generator
    return None


def difficulty_bonus(station):
    if not station or not _station_name(station):
        return 0
    generator = _linked_generator(station)
    if (generator and not (generator.properties or {}).get('gm_hidden') and _can_connect(generator, station)
            and _state(generator).get('on') and _state(generator).get('fuel_seconds', 0) > 0):
        return DIFFICULTY_BONUS
    return 0


def _context(lobby_id, location_id, user_id, generator_id, character_id):
    if type(character_id) is not int:
        raise ValidationError('Выберите персонажа')
    obj = db.session.get(LocationObject, generator_id)
    if not obj or obj.location_id != location_id or not is_generator(obj) or (obj.properties or {}).get('gm_hidden'):
        raise NotFoundError('Генератор не найден')
    actor = _actor(lobby_id, location_id, user_id, character_id, action_name='Пользоваться генератором')
    x, y = pose(obj)[:2]
    if level_of(obj) != (actor.floor_level or 0) or hypot(x - actor.pos_x - .5, y - actor.pos_y - .5) > 1.5:
        raise ValidationError('Подойдите к генератору')
    return actor.character, obj


def _inputs(data):
    for path, item in walk(data, True):
        if path[0] not in {'equipment', 'inventory'} or 'installedModules' in path or type(path[-1]) is not int:
            continue
        if liquid_kind(item) == 'fuel' and path[-2] == 'contents':
            vessel = CombatService._inventory_item_at_path(data, path[:-2])
            amount = item.get('quantity', 1)
            if (type(amount) in (int, float) and 0 < amount <= liquid_capacity(vessel)
                    and round(amount * 10) == amount * 10):
                yield path, item, 'fuel', vessel.get('name')
        cell_kind = power_cells.kind(item)
        if cell_kind and item.get('quantity', 1) == 1 and (cell_kind != 'exoskeleton' or power_cells.capacity(item)):
            yield path, item, cell_kind, None


def snapshot(lobby_id, location_id, user_id, generator_id, character_id):
    character, obj = _context(lobby_id, location_id, user_id, generator_id, character_id)
    return _snapshot(character, obj)


def _snapshot(character, obj):
    state = _state(obj)
    stations = []
    for station in LocationObject.query.filter_by(location_id=obj.location_id):
        linked = _linked_generator(station)
        if (linked is obj and not (station.properties or {}).get('gm_hidden')
                or linked is None and _can_connect(obj, station)):
            stations.append({'id': station.id, 'name': station.name, 'revision': station.revision,
                'linked': linked is obj, 'powered': bool(difficulty_bonus(station))})
    return {'id': obj.id, 'name': obj.name, 'revision': obj.revision, 'character_revision': character.revision,
        'on': bool(state.get('on')), 'fuel_seconds': state.get('fuel_seconds', 0),
        'connection_limit': CONNECTION_LIMIT, 'connected_stations': _connected_stations(obj),
        'batteries': [{'id': cell['id'], 'name': cell.get('name'), 'charge': power_cells.percentage(cell)}
                      for cell in state.get('batteries', [])],
        'inputs': [{'id': item.get('id'), 'path': path, 'name': item.get('name'), 'kind': kind, 'vessel': vessel,
                    'quantity': item.get('quantity', 1), 'charge': power_cells.percentage(item) if kind != 'fuel' else None}
                   for path, item, kind, vessel in _inputs(character.data or {})],
        'stations': stations}


def _connected_stations(obj):
    return LocationObject.query.filter(
        LocationObject.location_id == obj.location_id,
        LocationObject.properties['power_generator_id'].as_integer() == obj.id,
    ).count()


def _refuel(data, state, path, item, liters):
    available = item.get('quantity', 1)
    if (type(liters) not in (int, float) or not isfinite(liters)
            or not 0 < liters <= available or round(liters * 10) != liters * 10):
        raise ValidationError('Укажите доступное количество топлива с шагом 0,1 л')
    vessel = CombatService._inventory_item_at_path(data, path[:-2])
    vessel['fuelUsed'] = True
    if liters == available:
        parent, index = inventory_slot(data, path)
        parent.pop(index)
    else:
        item['quantity'] = round(available - liters, 1)
    state['fuel_seconds'] = state.get('fuel_seconds', 0) + round(liters * SECONDS_PER_LITER)


@atomic_operation
def operate(lobby_id, location_id, user_id, generator_id, payload):
    if not isinstance(payload, dict):
        raise ValidationError('Выберите действие с генератором')
    character, obj = _context(lobby_id, location_id, user_id, generator_id, payload.get('character_id'))
    for field, expected in (('revision', obj.revision), ('character_revision', character.revision)):
        if type(payload.get(field)) is not int or payload[field] != expected:
            raise ConflictError('Генератор или инвентарь изменились. Обновите окно')
    operation = payload.get('operation')
    data = deepcopy(character.data or {})
    state = deepcopy(_state(obj))
    changed_station = None
    changed_inventory = operation in ('refuel', 'load', 'unload')
    batteries = state.setdefault('batteries', [])
    if operation in ('refuel', 'load'):
        source = next((entry for entry in _inputs(data) if entry[0] == payload.get('path')
            and entry[1].get('id') == payload.get('item_id')), None)
        if not source:
            raise ConflictError('Предмет перемещён. Выберите его заново')
        path, item, kind, _ = source
        if operation == 'refuel' and kind == 'fuel':
            _refuel(data, state, path, item, payload.get('liters', item.get('quantity', 1)))
        elif operation == 'load' and kind != 'fuel':
            parent, index = inventory_slot(data, path)
            if not item.get('id'):
                item['id'] = uuid4().hex
            batteries.append(parent.pop(index))
        else:
            raise ValidationError('Выберите топливо или отдельный аккумулятор')
    elif operation == 'unload':
        index = next((index for index, item in enumerate(batteries) if item['id'] == payload.get('item_id')), None)
        if index is None:
            raise NotFoundError('Аккумулятора больше нет в генераторе')
        data.setdefault('inventory', {}).setdefault('pockets', []).append(batteries.pop(index))
    elif operation == 'toggle':
        if not state.get('on') and state.get('fuel_seconds', 0) <= 0:
            raise ValidationError('Сначала заправьте генератор')
        state['on'] = not state.get('on')
    elif operation in ('connect', 'disconnect'):
        identity = payload.get('station_id')
        changed_station = db.session.get(LocationObject, identity) if type(identity) is int else None
        if (not changed_station or changed_station.location_id != location_id or not _station_name(changed_station)
                or (changed_station.properties or {}).get('gm_hidden')):
            raise NotFoundError('Станок не найден')
        if type(payload.get('station_revision')) is not int or payload['station_revision'] != changed_station.revision:
            raise ConflictError('Станок изменился. Обновите окно')
        props = dict(changed_station.properties or {})
        linked = _linked_generator(changed_station)
        if operation == 'connect':
            if linked or not _can_connect(obj, changed_station):
                raise ValidationError('Выберите свободный станок рядом с генератором на этом этаже')
            if _connected_stations(obj) >= CONNECTION_LIMIT:
                raise ValidationError('К генератору уже подключены четыре станка')
            props['power_generator_id'] = obj.id
        elif linked is obj:
            props.pop('power_generator_id')
        else:
            raise ValidationError('Станок не подключён к этому генератору')
        changed_station.properties = props
    else:
        raise ValidationError('Выберите действие с генератором')
    if changed_inventory:
        character.data = data
    obj.properties = {**(obj.properties or {}), 'generator': state}
    if changed_station is not None:
        # Одновременные подключения конкурируют за одну ревизию генератора.
        flag_modified(obj, 'properties')
    db.session.flush()
    return character, obj, changed_station, changed_inventory, _snapshot(character, obj)


def advance_lobby(lobby, seconds):
    if seconds <= 0:
        return
    objects = LocationObject.query.join(Location).filter(Location.lobby_id == lobby.id, or_(
        LocationObject.type == ASSET, LocationObject.properties['visual_asset'].as_string() == ASSET))
    for obj in objects:
        state = _state(obj)
        if not state.get('on'):
            continue
        state = deepcopy(state)
        fuel = state.get('fuel_seconds', 0)
        elapsed = min(seconds, fuel)
        state['fuel_seconds'] = fuel - elapsed
        state['on'] = state['fuel_seconds'] > 0
        for cell in state.get('batteries', []):
            duration = EXOSKELETON_CHARGE_SECONDS if power_cells.kind(cell) == 'exoskeleton' else FULL_CHARGE_SECONDS
            power_cells.set_percentage(cell, min(100, power_cells.percentage(cell) + 100 * elapsed / duration))
        obj.properties = {**(obj.properties or {}), 'generator': state}
