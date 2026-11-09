"""Ферромагнит притягивает один видимый предмет с земли или из чужой руки."""
from copy import deepcopy
import math
import re
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCharacter, LocationCombatState, LocationObject
from app.services.artifact_targets import SIGHT_RADIUS
from app.services.character_traits import facial_profile
from app.services.effects import normalize_effect_list
from app.services.exceptions import ValidationError
from app.services.held_items import items as held_items
from app.services.item_template_lookup import resolve_item_template
from app.services.location_navigation import level_of, pose

MAGNET = 'Ферромагнит'
METAL_NAMES = {'металлические детали', 'метательный нож', 'метательные ножи', 'нож', 'молоток', 'кастет', 'томагавк',
               'лопатка саперная', 'кортик', 'меч', 'мультитул', 'ножницы', 'плоскогубцы', 'напильник'}
# В исходном каталоге этим ручным гранатам не указали массу. Они входят в примеры свойства.
LIGHT_METAL_GRENADES = {'ргд-5', 'ф-1', 'ргн', 'рго'}


def _eligible(item, lobby_id):
    from app.services.combat import CombatService
    from app.services.trade import _contains_individual_items
    if not isinstance(item, dict):
        return False
    template = resolve_item_template(item, lobby_id)
    attrs = {**(template.attributes or {} if template else {}), **(item.get('attributes') or {})}
    category = item.get('category') or (template.category if template else None)
    name = str(attrs.get('catalog_name') or (template.name if template else item.get('name')) or '').casefold().replace('ё', 'е')
    material = str(item.get('material') or attrs.get('material') or '').casefold()
    metallic = item.get('metallic', attrs.get('metallic'))
    light_grenade = category == 'grenade' and name.removeprefix('граната ') in LIGHT_METAL_GRENADES
    if metallic is None:
        metallic = (bool(re.search(r'металл|стал[ьи]|желез|чугун|алюмини|мед[ьи]|латун|бронз|\b(?:metal|steel|iron|aluminium|aluminum|brass|copper)\b', material))
                    if material else light_grenade or name in METAL_NAMES or category in {'weapon', 'ammo'})
    if metallic is not True or item.get('naturalWeapon') or attrs.get('natural_weapon'):
        return False
    quantity = item.get('quantity', 1)
    if type(quantity) is not int or quantity < 1 or quantity > 1 and _contains_individual_items(item):
        return False
    single = {**item, 'category': category, 'quantity': 1}
    if single.get('weight') is None and template:
        single['weight'] = template.weight
    weight = CombatService._item_total_weight(single)
    for key in ('installedMagazine', 'loadedGrenade'):
        weight += CombatService._item_total_weight(single.get(key))
    weight += sum(CombatService._item_total_weight(ammo) for ammo in single.get('fixedAmmo') or [])
    return 0 < weight <= 1 or light_grenade and weight == 0 and single.get('weight') in (None, 0)


def _hands(actor, data):
    from app.services.portable_lights import bindings, walk
    from app.services.weapon_grip import secondary_index
    weapons = data.get('weapons') or []
    for index in (actor.drawn_weapon_index, secondary_index(actor)):
        if type(index) is int and 0 <= index < len(weapons):
            yield ['weapons', index], weapons[index]
    for index, item in enumerate(held_items(data)):
        yield ['inventory', 'held', index], item
    meta = (data.get('health') or {}).get('combatMeta') or {}
    ids = {row.get('id') for row in bindings(data) if row.get('mount') == 'hand'}
    ids.update((meta.get(key) or {}).get('id') for key in ('heldPda', 'pdaDynamo', 'heldWaterItem'))
    ids.discard(None)
    if ids:
        for path, item in walk(data, all_items=True):
            if path[0] == 'inventory' and path[1] != 'held' and item.get('id') in ids:
                yield path, item


def _candidates(actor, data):
    from app.services.artifact_special import has_daylight_vision
    from app.services.combat import CombatService
    from app.services.portable_lights import illumination
    if not isinstance(actor, LocationCharacter):
        raise ValidationError('Для Ферромагнита нужна подлокация')
    effects = normalize_effect_list((data.get('health') or {}).get('effects') or [])
    if facial_profile(data)['eyes'] == 2 or any(effect.get('active', True) and effect['type'] == 'blindness'
            and CombatService._coerce_float(effect.get('value'), 0) >= 100 for effect in effects):
        return
    location = actor.location
    objects = LocationObject.query.filter_by(location_id=location.id).all()
    people = CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=location.id).all())
    state = LocationCombatState.query.filter_by(location_id=location.id, status='active').first()
    level = actor.floor_level or 0
    daylight = has_daylight_vision(data)

    def in_view(target):
        x, y = target.pos_x, target.pos_y
        return ((target.floor_level or 0) == level and math.hypot(x - actor.pos_x, y - actor.pos_y) <= SIGHT_RADIUS
                and CombatService._is_in_facing_arc(actor, x, y))

    def unobstructed(target, height):
        if (not daylight and CombatService._is_dark_at_position(location, math.floor(target.pos_x), math.floor(target.pos_y), objects=objects)
                and not illumination(target, actors=people, objects=objects)['lit']):
            return False
        return not CombatService._cover_analysis(location.id, actor, target, objects=objects, point_height=height)['zones']

    for obj in objects:
        props = obj.properties or {}
        if not (obj.type == 'ground_item' or props.get('is_ground_item')) or props.get('gm_hidden') or props.get('locked'):
            continue
        x, y, *_ = pose(obj)
        point = SimpleNamespace(id=None, character=None, location=location, location_id=location.id,
                                pos_x=x-.5, pos_y=y-.5, floor_level=level_of(obj), posture='prone')
        if not in_view(point):
            continue
        matches = [(index, item) for index, item in enumerate(props.get('contents') or []) if _eligible(item, location.lobby_id)]
        if matches and unobstructed(point, .08):
            for index, item in matches:
                yield f'ground:{obj.id}:{obj.revision}:{index}', item, obj, index, f'земля · {obj.tile_x}, {obj.tile_y}'

    for person in people:
        if not person.character or person.character_id == actor.character_id or person.gm_hidden or not in_view(person):
            continue
        target_data = person.character.data or {}
        if (CombatService._character_condition(target_data)['state'] == 'dead' or
                state and CombatService._mutant_camouflage_active(target_data, state.round_number or 0)):
            continue
        matches = [(path, item) for path, item in _hands(person, target_data) if _eligible(item, location.lobby_id)]
        height = {'standing': 1.25, 'sitting': .8, 'prone': .3}[CombatService._posture_key(person)]
        if matches and unobstructed(person, height):
            for path, item in matches:
                key = '/'.join(map(str, path))
                yield f'held:{person.id}:{person.character.revision}:{key}', item, person, path, f'в руках · {person.character.name}'


def targets(actor, data):
    return [{'id': key, 'label': f"{item.get('name') or 'Предмет'} · {source}"}
            for key, item, _, _, source in _candidates(actor, data)]


def select(actor, data, identity):
    if isinstance(identity, str):
        for key, item, owner, path, _ in _candidates(actor, data):
            if key == identity:
                return item, owner, path
    raise ValidationError('Предмет недоступен. Выберите видимый металлический предмет заново')


def apply(actor, data, selection):
    from app.services.combat import CombatService
    from app.services.container_transfer import put_on_ground
    from app.services.weapon_grip import remove_drawn
    item, owner, path = selection
    split = item.get('quantity', 1) > 1
    affected, changes = [], {}
    if isinstance(owner, LocationObject):
        props = deepcopy(owner.properties)
        moved = CombatService._take_inventory_item({'inventory': props}, ['inventory', 'contents', path], 1)
        if props['contents']:
            owner.properties = props
            changes[owner.id] = 'updated'
        else:
            db.session.delete(owner)
            changes[owner.id] = 'deleted'
    else:
        target_data = deepcopy(owner.character.data)
        if path[0] == 'weapons':
            moved = remove_drawn(owner, target_data, path[1])
        else:
            moved = CombatService._take_inventory_item(target_data, path, 1)
            if not split:
                meta = target_data.setdefault('health', {}).setdefault('combatMeta', {})
                identity = moved.get('id')
                if identity:
                    for key in ('heldPda', 'pdaDynamo', 'heldWaterItem'):
                        if (meta.get(key) or {}).get('id') == identity:
                            meta.pop(key)
                    if meta.get('portableLights'):
                        meta['portableLights'] = [row for row in meta['portableLights'] if row.get('id') != identity]
        owner.character.data = target_data
        flag_modified(owner.character, 'data')
        affected.append(owner.character_id)
    if split or not isinstance(moved.get('id'), str) or not moved['id']:
        moved['id'] = str(uuid4())
    meta = (data.get('health') or {}).get('combatMeta') or {}
    reserved = int(bool(meta.get('bodyTransport') or actor.grapple_target_id))
    caught = CombatService._has_usable_free_hand(actor, reserved_hands=reserved)
    if caught:
        data.setdefault('inventory', {}).setdefault('held', []).append(moved)
    else:
        ground, created = put_on_ground(actor.location, actor.pos_x, actor.pos_y, actor.floor_level or 0, moved)
        changes[ground.id] = 'created' if created and changes.get(ground.id) != 'deleted' else 'updated'
    name = moved.get('name') or 'Предмет'
    destination = 'в руке' if caught else 'под ногами'
    return {'name': f'Ферромагнит · {name} {destination}', 'attracted_item': name,
            'at_feet': not caught, '_affected_character_ids': affected,
            '_magnet_ground_changes': changes, '_vision_location_id': actor.location_id}
