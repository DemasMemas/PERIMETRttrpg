from copy import deepcopy
from math import isfinite
from uuid import uuid4

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
from app.models.location_object import LocationObject
from app.services.character import CharacterService
from app.services.combat import CombatService
from app.services.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from app.services.inventory import normalize_inventory_ammo_stacks
from app.services.transaction import atomic_operation


def put_on_ground(location, x, y, level, item):
    return put_items_on_ground(location, x, y, level, [item])


def put_items_on_ground(location, x, y, level, items):
    tile_x = min(location.grid_width - 1, max(0, int(x + .5)))
    tile_y = min(location.grid_height - 1, max(0, int(y + .5)))
    ground = next((obj for obj in LocationObject.query.filter_by(location_id=location.id,
        type='ground_item', tile_x=tile_x, tile_y=tile_y).all()
        if (obj.properties or {}).get('floor_level', 0) == level and not (obj.properties or {}).get('gm_hidden')
        and not (obj.properties or {}).get('locked')), None)
    created = ground is None
    if created:
        ground = LocationObject(location_id=location.id, name='Пол', type='ground_item',
            tile_x=tile_x, tile_y=tile_y, properties={'contents': [], 'is_ground_item': True,
                'passable': True, 'floor_level': level, 'interactions': ['open_container']})
        db.session.add(ground)
    props = deepcopy(ground.properties or {})
    props.setdefault('contents', []).extend(items)
    ground.properties = props
    db.session.flush()
    return ground, created


class ContainerTransferService:
    @staticmethod
    @atomic_operation
    def transfer(lobby_id, object_id, user_id, payload):
        return ContainerTransferService._transfer(lobby_id, object_id, user_id, payload)

    @staticmethod
    @atomic_operation
    def drop(lobby_id, location_id, user_id, payload):
        actor = LocationCharacter.query.filter_by(location_id=location_id, character_id=payload.get('character_id')).first()
        if not actor or actor.character.lobby_id != lobby_id:
            raise NotFoundError('Character is not in this location')
        CharacterService.check_access(actor.character, user_id, edit=True)
        obj = next((ground for ground in LocationObject.query.filter_by(
            location_id=location_id, type='ground_item', tile_x=actor.pos_x, tile_y=actor.pos_y).all()
            if (ground.properties or {}).get('floor_level', 0) == (actor.floor_level or 0)), None)
        created = obj is None
        if created:
            obj = LocationObject(location_id=location_id, name='Пол', type='ground_item', tile_x=actor.pos_x,
                                 tile_y=actor.pos_y, properties={'contents': [], 'is_ground_item': True,
                                 'passable': True, 'level':actor.floor_level or 0,'floor_level': actor.floor_level or 0,
                                 'interactions': ['open_container']})
            db.session.add(obj)
            db.session.flush()
        character, obj, _ = ContainerTransferService._transfer(lobby_id, obj.id, user_id, {
            **payload, 'direction': 'to_container', 'object_revision': obj.revision,
        }, dropped=True)
        return character, obj, created

    @staticmethod
    def _transfer(lobby_id, object_id, user_id, payload, *, dropped=False):
        obj = db.session.get(LocationObject, object_id)
        if not obj or obj.location.lobby_id != lobby_id:
            raise NotFoundError('Container not found')
        character = db.session.get(LobbyCharacter, payload.get('character_id'))
        if not character or character.lobby_id != lobby_id:
            raise NotFoundError('Character not found')
        CharacterService.check_access(character, user_id, edit=True)
        actor = LocationCharacter.query.filter_by(location_id=obj.location_id, character_id=character.id).first()
        if not actor:
            raise NotFoundError('Character is not in this location')
        CombatService.ensure_character_can_act(actor)
        state = LocationCombatState.query.filter_by(location_id=obj.location_id).first()
        if state and state.status == 'active':
            if state.current_location_character_id != actor.id:
                raise PermissionDenied("It is not this character's turn")
            if max(abs(actor.pos_x - obj.tile_x), abs(actor.pos_y - obj.tile_y)) > 1:
                raise ValidationError('Контейнер должен быть на соседней клетке')
        if (obj.properties or {}).get('locked'):
            raise ValidationError('Контейнер закрыт')
        for key, expected in [('character_revision', character.revision), ('object_revision', obj.revision)]:
            if type(payload.get(key)) is not int or payload[key] != expected:
                raise ConflictError()
        direction = payload.get('direction')
        if direction not in {'to_container', 'to_character'}:
            raise ValidationError('Invalid transfer direction')
        path = payload.get('item_path')
        amount = payload.get('amount')
        if type(amount) is not int or amount < 1:
            raise ValidationError('Invalid item quantity')
        if not isinstance(path, list) or len(path) < 2:
            raise ValidationError('Invalid item path')
        allowed = {'inventory', 'equipment'} if direction == 'to_container' else {'contents'}
        if not isinstance(path[0], str) or path[0] not in allowed:
            raise ValidationError('Invalid item path')
        data = deepcopy(character.data or {})
        properties = deepcopy(obj.properties or {})
        contents = properties.setdefault('contents', [])
        if not isinstance(contents, list):
            raise ValidationError('Invalid container contents')
        root = data if direction == 'to_container' else properties
        parent = root
        for key in path[:-1]:
            if isinstance(parent, dict) and isinstance(key, str) and key in parent:
                parent = parent[key]
            elif isinstance(parent, list) and type(key) is int and 0 <= key < len(parent):
                parent = parent[key]
            else:
                raise ConflictError()
        index = path[-1]
        if not isinstance(parent, list) or type(index) is not int or not 0 <= index < len(parent):
            raise ConflictError()
        item = parent[index]
        if not isinstance(item, dict) or item != payload.get('expected_item'):
            raise ConflictError()
        quantity = item.get('quantity', 1)
        if type(quantity) not in (int, float) or not isfinite(quantity) or quantity < amount:
            raise ConflictError()
        moved = deepcopy(item)
        if amount == quantity:
            parent.pop(index)
        else:
            from app.services.trade import _contains_individual_items
            if _contains_individual_items(item):
                raise ValidationError('Cannot split a filled container')
            item['quantity'] = quantity - amount
            moved['quantity'] = amount
            moved['id'] = f'item_{uuid4().hex}'
        if dropped and moved.get('category') in {'weapon', 'melee_weapon'}:
            maximum = moved.get('maxDurability') or (moved.get('attributes') or {}).get('max_durability') or 100
            moved['maxDurability'] = maximum
            moved['durability'] = max(0, float(moved.get('durability', maximum)) - 3)
        if direction == 'to_container':
            contents.append(moved)
            lights = data.setdefault('health', {}).setdefault('combatMeta', {}).get('portableLights')
            if isinstance(lights, list):
                lights[:] = [binding for binding in lights if binding.get('id') != moved.get('id')]
        else:
            backpack = data.setdefault('inventory', {}).setdefault('backpack', [])
            if not isinstance(backpack, list):
                raise ValidationError('Invalid character inventory')
            backpack.append(moved)
        normalize_inventory_ammo_stacks(data)
        character.data = data
        obj.properties = properties
        deleted = not contents and (obj.type == 'ground_item' or properties.get('is_ground_item'))
        if deleted:
            db.session.delete(obj)
        # Both version-checked writes (including deletion of a ground item) commit together.
        return character, obj, deleted
