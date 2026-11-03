"""Validated ammunition transfer. Combat payment and mutation share a transaction."""
from copy import deepcopy

from app.extensions import db
from app.services.exceptions import ConflictError, ValidationError, NotFoundError
from app.services.magazine import inventory_slot, _attributes, _caliber, _ammo_count, _template
from app.services.transaction import atomic_operation


def _item(data, path):
    parent, index = inventory_slot(data, path)
    return parent[index]


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValidationError('Некорректное количество патронов или стоимость зарядки')
    return value


def _device(item):
    attributes = _attributes(item)
    name = str(item.get('name') or '').lower()
    return item.get('category') == 'magazine' and (
        attributes.get('isLoader') or attributes.get('loadingDevice')
        or 'лента' in name or 'спидлоадер' in name)


def _feeder(item):
    return item.get('category') == 'magazine' and (
        _attributes(item).get('loadingTool') == 'feeder' or 'подавач' in str(item.get('name') or '').lower())


def _loading_caliber(item):
    value = _caliber(item)
    if value:
        return value
    template = _template(item)
    fallback = ((item.get('name') if item.get('category') == 'grenade' else None)
                or item.get('subcategory') or (template.subcategory if template else None)
                or _attributes(item).get('ammo_group'))
    return _caliber({'caliber': fallback})


def _combine_payments(groups):
    options = [(0, 0)]
    for group in groups:
        options = list({
            (ap + int(choice[0]), free + int(choice[1]))
            for ap, free in options for choice in group
        })
    return options


def _preparation_payments(data, path, item, role):
    from app.services.inventory_access import calculate_inventory_access

    access = calculate_inventory_access(data, path, item)
    quick = access['source'] in {'pockets', 'compatible_pouch'}
    if quick:
        return [(0, 1), (1, 0)]
    retrieval = max(1, int(access['retrieval_action_points']))
    if role == 'magazine':
        return [(retrieval, 0)]
    return [(max(0, retrieval - 1), 1), (retrieval, 0)]


def _fixed_weapon_loading_cost(data, target, quantity, prepared):
    template = _template(target)
    category = str(
        (template.subcategory if template else target.get('subcategory')) or ''
    ).lower()
    if 'дробов' in category:
        specialization = 'shotguns'
    elif 'снайпер' in category:
        specialization = 'sniperRifles'
    elif 'пистолет' in category and 'пулем' in category:
        specialization = 'smgs'
    elif 'пистолет' in category:
        specialization = 'pistols'
    elif 'штурм' in category or 'карабин' in category:
        specialization = 'assaultRifles'
    elif 'гранатом' in category:
        specialization = 'grenadeLaunchers'
    elif 'пулем' in category:
        specialization = 'machineGuns'
    else:
        specialization = None
    level = (((data.get('skills') or {}).get('specialized') or {}).get(specialization) or {}).get(
        'level', 'unfamiliar'
    ) if specialization else 'unfamiliar'
    tariff = 2 if level == 'unfamiliar' else 1
    duplex = int((_attributes(target).get('fire_modes') or {}).get('duplex_size') or 0)
    includes_start = not prepared and duplex >= 2 and quantity == 2
    return tariff + (0 if prepared or includes_start else 1)


def _validate_combat_payment(data, request, target, source, quantity, fixed, device):
    state = data.get('combatMagazineLoading') or {}
    source_id = source.get('id') or '.'.join(map(str, request['source_path']))
    if fixed:
        prepared = (
            state.get('targetType') == 'fixed'
            and state.get('weaponIndex') == request.get('weapon_index')
        )
        same_source = prepared and state.get('sourceId') == source_id
        groups = [] if same_source else [
            _preparation_payments(data, request['source_path'], source, 'ammo')
        ]
        if device:
            tariff = [(2, 0), (1, 1)]
        else:
            tariff = [(_fixed_weapon_loading_cost(data, target, quantity, prepared), 0)]
        from app.services.character_traits import action_cost
        tariff=[(action_cost(data,'reload',ap) if ap else 0,free) for ap,free in tariff]
        groups.append(tariff)
    else:
        target_id = target.get('id') or '.'.join(map(str, request['target_path']))
        same_target = state.get('targetType') == 'inventory' and state.get('targetId') == target_id
        same_source = same_target and state.get('sourceId') == source_id
        groups = []
        if not same_target:
            groups.append(_preparation_payments(data, request['target_path'], target, 'magazine'))
        if not same_source:
            groups.append(_preparation_payments(data, request['source_path'], source, 'ammo'))
        if request.get('feeder_path') is not None:
            feeder = _item(data, request['feeder_path'])
            feeder_id = feeder.get('id') or '.'.join(map(str, request['feeder_path']))
            if not (same_target and state.get('feederId') == feeder_id):
                groups.append(_preparation_payments(data, request['feeder_path'], feeder, 'ammo'))
        if device:
            tariff = [(2, 0), (1, 1)]
        elif 'клипс' in str(target.get('name') or '').lower():
            tariff = [(2, 0)]
        elif request.get('feeder_path') is not None:
            tariff = [(0, 1), (1, 0)] if quantity <= 10 else [(2, 0), (1, 1)]
        else:
            tariff = [(0, 1), (1, 0)] if quantity == 1 else [(2, 0), (1, 1)]
        groups.append(tariff)

    options = _combine_payments(groups)
    paid = (request['action_points'], request['free_actions'])
    if not any(paid[0] >= required[0] and paid[1] >= required[1] for required in options):
        raise ValidationError('Не оплачены доставание и зарядка патронов')
    return [{'action_points': ap, 'free_actions': free} for ap, free in sorted(options)]


def loading_bindings(data, request):
    """Reject moved/replaced items rather than resolving an old index to a new item."""
    result = {}
    for key in ('source_path', 'target_path', 'feeder_path'):
        if request.get(key) is not None:
            result[key] = deepcopy(_item(data, request[key]))
    index = request.get('weapon_index')
    if index is not None:
        weapons = data.get('weapons') or []
        if type(index) is not int or not 0 <= index < len(weapons) or not isinstance(weapons[index], dict):
            raise ValidationError('Оружие не найдено')
        result['weapon'] = deepcopy(weapons[index])
    return result


def validate_loading(data, request, *, combat=False):
    if not isinstance(request, dict):
        raise ValidationError('Не выбран способ зарядки')
    bindings = loading_bindings(data, request)
    if request.get('selection') is not None and request['selection'] != bindings:
        raise ConflictError('Магазин или патроны изменились. Выберите их заново.')
    source = _item(data, request.get('source_path'))
    fixed = request.get('weapon_index') is not None
    if fixed:
        if request.get('target_path') is not None:
            raise ValidationError('Выберите один магазин')
        target = data['weapons'][request['weapon_index']]
        attrs = _attributes(target)
        if not attrs.get('fixedMagazine'):
            raise ValidationError('Отъёмный магазин нужно сначала снять с оружия')
        capacity = attrs.get('magazine_size', 0)
        stacks = target.get('fixedAmmo') or []
    else:
        target = _item(data, request.get('target_path'))
        if target is source or target.get('category') != 'magazine' or _feeder(target):
            raise ValidationError('Выберите магазин для зарядки')
        if target.get('quantity', 1) != 1:
            raise ValidationError('Магазины должны храниться отдельными предметами')
        capacity = _attributes(target).get('capacity', 30)
        stacks = target.get('ammo') or []
    if type(capacity) is float and capacity.is_integer():
        capacity = int(capacity)
    capacity = _integer(capacity, 1)
    count = _ammo_count({'ammo': stacks})
    device = bool(_device(source))
    if not device and source.get('category') not in {'ammo', 'grenade'}:
        raise ValidationError('Нужны патроны или снаряжённое устройство зарядки')
    source_stacks = (source.get('ammo') or []) if device else [source]
    available = _ammo_count({'ammo': source_stacks})
    caliber = _caliber(target)
    if not caliber and stacks:
        caliber = _caliber(stacks[0])
    source_caliber = next((_loading_caliber(stack) for stack in source_stacks if stack.get('quantity', 0) > 0), '')
    if not caliber and not fixed and _device(target):
        caliber = source_caliber
    if not caliber or not source_caliber or caliber != source_caliber:
        raise ValidationError('Калибр патронов не соответствует магазину')
    if any(_loading_caliber(stack) != caliber for stack in [*stacks, *source_stacks] if stack.get('quantity', 0) > 0):
        raise ValidationError('В источнике или магазине патроны другого калибра')
    if device:
        name = str(source.get('name') or '').lower()
        template = _template(target)
        weapon_name = str(template.name if template else target.get('name') or '').lower()
        category = str(template.subcategory if template else target.get('subcategory') or '').lower()
        if 'спидлоадер' in name:
            compatible = fixed and 'револьвер' in weapon_name
        else:
            compatible = 'лента' in name and (not fixed or 'снайпер' in category or 'снайпер' in weapon_name)
        if not compatible:
            raise ValidationError('Это устройство не подходит для зарядки выбранного магазина')
    quantity = min(capacity - count, available) if request.get('full') else _integer(request.get('quantity'), 1)
    if quantity <= 0 or quantity > available or count + quantity > capacity:
        raise ValidationError('Недостаточно патронов или места в магазине')
    if request.get('feeder_path') is not None:
        if fixed or device or not _feeder(_item(data, request['feeder_path'])):
            raise ValidationError('Подавач не найден или неприменим')
    if combat:
        if request.get('full'):
            raise ValidationError('Зарядка до полного без оплаты доступна только вне боя')
        ap = _integer(request.get('action_points'))
        free = _integer(request.get('free_actions'))
        if ap > 100 or free > 10:
            raise ValidationError('Некорректная стоимость зарядки')
        # Access costs remain supplied by the existing inventory UI. Validate the loading tariff here.
        minimum = 1
        if device:
            if quantity != min(capacity - count, available):
                raise ValidationError('Устройство заряжает все доступные патроны до заполнения магазина')
            minimum = 2
        elif 'клипс' in str(target.get('name') or '').lower():
            if quantity != capacity - count:
                raise ValidationError('Клипса заряжается целиком')
            minimum = 2
        elif request.get('feeder_path') is not None:
            if quantity > 20:
                raise ValidationError('Подавач заряжает не более 20 патронов за действие')
            minimum = 2 if quantity > 10 else 1
        elif fixed:
            if quantity > 2:
                raise ValidationError('Нельзя зарядить столько патронов за это действие')
        else:
            if quantity not in (1, 3):
                raise ValidationError('Россыпью заряжают по 1 или 3 патрона')
            minimum = 2 if quantity == 3 else 1
        if fixed:
            from app.services.character_traits import action_cost
            minimum=action_cost(data,'reload',minimum)
        if ap + free < minimum:
            raise ValidationError('Не оплачена зарядка патронов')
        _validate_combat_payment(data, request, target, source, quantity, fixed, device)
    return target, source, quantity, capacity, caliber, fixed, device


def _magazine_weight(item):
    item['currentAmmo'] = _ammo_count(item)
    item['weight'] = (item.get('loadedWeight') or 0.25) if item['currentAmmo'] else (item.get('emptyWeight') or 0)


def load_ammunition(data, request, *, combat=False):
    target, source, quantity, capacity, caliber, fixed, device = validate_loading(data, request, combat=combat)
    feeder = _item(data, request['feeder_path']) if request.get('feeder_path') else None
    ammo_key = 'fixedAmmo' if fixed else 'ammo'
    ammo = target[ammo_key] = target.get(ammo_key) or []
    remaining = quantity
    while remaining:
        stack = source['ammo'][-1] if device else source
        moved = min(remaining, int(stack['quantity']))
        if moved:
            # Keep each variant's complete ballistic data; never combine unlike ammunition.
            loaded = deepcopy(stack)
            for key in ('id', 'weight', 'volume', 'ammo'):
                loaded.pop(key, None)
            loaded['quantity'] = moved
            last = ammo[-1] if ammo else None
            if last and {k: v for k, v in last.items() if k != 'quantity'} == {k: v for k, v in loaded.items() if k != 'quantity'}:
                last['quantity'] += moved
            else:
                ammo.append(loaded)
            stack['quantity'] -= moved
            remaining -= moved
        if device and stack['quantity'] <= 0:
            source['ammo'].pop()
    source_key = source.get('id') or '.'.join(map(str, request['source_path']))
    if device:
        _magazine_weight(source)
        empty_source = not source['currentAmmo']
    else:
        empty_source = not source['quantity']
        if empty_source:
            parent, index = inventory_slot(data, request['source_path'])
            parent.pop(index)
        else:
            source['weight'] = 0.1 if (source.get('volume') or 0.02) * source['quantity'] < 0.5 else 0.25
    if fixed:
        target['ammo'] = _ammo_count({'ammo': ammo})
        state = {'targetType': 'fixed', 'weaponIndex': request['weapon_index'], 'sourceId': source_key}
        count = target['ammo']
    else:
        if not _caliber(target):
            target.setdefault('attributes', {})['caliber'] = caliber
        _magazine_weight(target)
        count = target['currentAmmo']
        state = {'targetType': 'inventory', 'targetId': target.get('id') or '.'.join(map(str, request['target_path'])),
                 'sourceId': source_key, 'feederId': None}
        if request.get('feeder_path'):
            state['feederId'] = feeder.get('id') or '.'.join(map(str, request['feeder_path']))
        data.pop('activeWeaponIndex', None)
    if combat and count < capacity and not empty_source:
        data['combatMagazineLoading'] = state
    else:
        data.pop('combatMagazineLoading', None)
    return {'quantity': quantity, 'ammo': count}


@atomic_operation
def load_outside_combat(character_id, user_id, payload):
    from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
    from app.services.character import CharacterService
    character = db.session.get(LobbyCharacter, character_id)
    if not character:
        raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character, user_id, edit=True)
    if LocationCombatState.query.join(LocationCharacter, LocationCharacter.location_id == LocationCombatState.location_id).filter(
        LocationCharacter.character_id == character_id, LocationCombatState.status == 'active',
    ).first():
        raise ValidationError('В бою требуется оплата зарядки')
    if type(payload.get('character_revision')) is not int or payload['character_revision'] != character.revision:
        raise ConflictError()
    data = deepcopy(character.data or {})
    result = load_ammunition(data, payload.get('loading'))
    character.data = data
    return character, result
