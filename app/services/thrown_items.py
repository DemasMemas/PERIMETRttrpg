"""Метание обычных предметов по правилам ручного броска."""
from copy import deepcopy
import math
import random
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.models import LocationCharacter
from app.services.exceptions import ValidationError


def weight_band(weight):
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(weight) or weight < 0:
        raise ValidationError('Масса предмета для метания не задана')
    for maximum, distance, difficulty, damage in (
        (.1, None, -5, 1), (.5, None, 0, 15), (2, 10, 3, 30),
        (5, 3, 6, 45), (15, 1, 8, 80),
    ):
        if weight <= maximum:
            return {'maximum_distance': distance, 'difficulty': difficulty, 'damage': damage}
    raise ValidationError('Предмет тяжелее 15 кг нельзя метнуть')


def prepare(location, actor, data, item_path, item_id, target_x, target_y, target_floor_level=None, attack_type=None):
    from app.services.combat import CombatService
    from app.services.inventory_access import calculate_inventory_access
    from app.services.weapon_grip import held
    if not isinstance(item_path, list) or len(item_path) < 2 or item_path[0] not in {'inventory', 'weapons'}:
        raise ValidationError('Выберите предмет из инвентаря или оружие ближнего боя')
    if item_path[0] == 'weapons':
        if len(item_path) != 2 or type(item_path[1]) is not int or not 0 <= item_path[1] < len(data.get('weapons') or []):
            raise ValidationError('Оружие не найдено')
        item = data['weapons'][item_path[1]]
        if not held(actor, item_path[1]):
            raise ValidationError('Перед броском возьмите оружие в руку')
        if item.get('category') != 'melee_weapon':
            raise ValidationError('Метать из списка оружия можно только оружие ближнего боя')
        attrs = CombatService._template_attributes(item)
        allowed = [str(value).casefold().replace('ё', 'е') for value in attrs.get('allowed_attacks') or []]
        chosen = str(attack_type or '').casefold().replace('ё', 'е')
        if not chosen or chosen not in allowed or 'дроб' in chosen or 'круг' in chosen:
            raise ValidationError('Выберите колющую или режущую атаку метаемого оружия')
        retrieval_cost = 0
    else:
        item = CombatService._inventory_item_at_path(data, item_path)
        if item.get('category') == 'grenade':
            raise ValidationError('Гранату метают через действие взрывной атаки')
        if item.get('category') == 'melee_weapon':
            raise ValidationError('Сначала возьмите оружие ближнего боя в руку')
        retrieval_cost = int(calculate_inventory_access(data, item_path, item)['retrieval_action_points'])
    if not isinstance(item, dict) or item.get('id') != item_id:
        raise ValidationError('Предмет перемещён. Выберите его заново')
    held_binding = next((binding for binding in
        (((data.get('health') or {}).get('combatMeta') or {}).get('portableLights') or [])
        if binding.get('id') == item_id and binding.get('mount') == 'hand'), None)
    already_held = bool(held_binding) or len(item_path) == 3 and item_path[:2] == ['inventory', 'held']
    if already_held:
        from app.services.weapon_grip import functional_arms
        if functional_arms(data) < 1:
            raise ValidationError('Для броска нужна действующая рука')
    if not CombatService._has_usable_free_hand(actor) and item_path[0] != 'weapons' and not already_held:
        raise ValidationError('Для броска нужна свободная действующая рука')
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in (target_x, target_y)):
        raise ValidationError('Выберите клетку броска')
    x, y = float(target_x), float(target_y)
    if not 0 <= x < location.grid_width or not 0 <= y < location.grid_height:
        raise ValidationError('Точка броска вне локации')
    floor = (actor.floor_level or 0) if target_floor_level is None else target_floor_level
    if type(floor) is not int or floor != (actor.floor_level or 0):
        raise ValidationError('Выберите клетку на этаже метающего')
    attrs = CombatService._template_attributes(item)
    weight = item.get('weight', attrs.get('weight'))
    band = weight_band(weight)
    distance = CombatService._point_distance(actor.pos_x, actor.pos_y, x, y)
    if band['maximum_distance'] is not None and distance > band['maximum_distance'] + 1e-9:
        raise ValidationError('Предмет слишком тяжёлый для выбранной дальности')
    if not CombatService._is_in_facing_arc(actor, x, y):
        raise ValidationError('Точка вне поля зрения персонажа')
    check = CombatService._hand_throw_difficulty(
        location.id, actor, data, x, y, distance, band['difficulty'])
    from app.services.weather_rules import at_character as weather_at
    weather = weather_at(location, actor)
    return {'item': item, 'path': item_path, 'item_name': item.get('name') or 'Предмет',
        'target_x': x, 'target_y': y, 'floor_level': floor, 'weight': weight, 'band': band,
        'difficulty': check['difficulty'] - weather['accuracy'], 'distance': distance,
        'disadvantage': weather['accuracy_disadvantage'], 'obstacle': check['obstacle'],
        'posture': check['posture'], 'action_points': 2 + retrieval_cost, 'attack_type': attack_type,
        'split_stack': item.get('quantity', 1) > 1, 'held_binding': bool(held_binding)}


def execute(location, actor, data, plan, round_number):
    from app.services.combat import CombatService
    from app.services.brigadier_order import boost_roll
    rolls, roll, order_bonus = boost_roll(data, [], disadvantage=plan['disadvantage'])
    success = roll + order_bonus >= plan['difficulty']
    failure = max(0, plan['difficulty'] - roll - order_bonus) if not success else 0
    x, y = (plan['target_x'], plan['target_y']) if success else CombatService._scatter_point(
        plan['target_x'], plan['target_y'], failure, location.grid_width, location.grid_height,
        location=location, floor_level=plan['floor_level'])
    path = plan['path']
    if path[0] == 'weapons':
        from app.services.weapon_grip import remove_drawn
        item = remove_drawn(actor, data, path[1])
    else:
        item = CombatService._take_inventory_item(data, path, 1)
        if plan['held_binding']:
            bindings = data.setdefault('health', {}).setdefault('combatMeta', {}).get('portableLights') or []
            bindings[:] = [binding for binding in bindings if binding.get('id') != plan['item'].get('id')]
    item = deepcopy(item)
    if plan['split_stack'] or not item.get('id'):
        item['id'] = str(uuid4())
    from app.services.container_transfer import put_on_ground
    ground, created = put_on_ground(location, x, y, plan['floor_level'], item)
    tile_x, tile_y = ground.tile_x, ground.tile_y
    target = next((other for other in LocationCharacter.query.filter_by(location_id=location.id).all()
        if other.id != actor.id and (other.floor_level or 0) == plan['floor_level']
        and other.pos_x == tile_x and other.pos_y == tile_y), None)
    hit = None
    if success and target:
        zone = {1: 'left_leg', 2: 'right_leg', 3: 'left_arm', 4: 'abdomen',
            5: 'chest', 6: 'head'}[random.randint(1, 6)]
        damage = plan['band']['damage']
        profile = {'damage': damage, 'armor_piercing': 0, 'bleeding': '', 'accuracy': 0,
            'damage_type': 'physical', 'melee_damage_type': 'crushing', 'weight': plan['weight']}
        if path[0] == 'weapons':
            attrs = CombatService._template_attributes(item)
            agility = CombatService._base_skill_modifier(data, 'skills.physical.agility')
            weapon_bonus = CombatService._coerce_int(attrs.get('throwing_bonus', item.get('throwing_bonus', 0)), 0)
            edge_roll = random.randint(1, 20)
            if edge_roll >= 10 - agility - weapon_bonus:
                primary = CombatService._weapon_damage_profile(item, plan['attack_type'])
                profile.update(damage=primary['damage'], armor_piercing=primary['armor_piercing'],
                    melee_damage_type=primary['melee_damage_type'], bleeding=primary.get('bleeding', ''))
        hit = CombatService._resolve_attack(target, actor,
            {'automatic_hit': True, 'round_number': round_number},
            melee=True, aimed_zone=zone, profile_override=profile, profile_adjusted=True)
        if hit.get('hit') and zone == 'head' and plan['weight'] > .1:
            from app.services.effects import apply_effect_to_health
            health = target.character.data.setdefault('health', {})
            apply_effect_to_health(health, {'type': 'concussion', 'name': 'Контузия',
                'remaining': 5, 'tick': 'round_end', 'source': 'thrown_item', 'roll_modifier': -3})
            flag_modified(target.character, 'data')
    return {'item_name': plan['item_name'], 'rolls': rolls, 'roll': roll,
        'difficulty': plan['difficulty'], 'success': success, 'failure': failure,
        'target': {'x': plan['target_x'], 'y': plan['target_y']},
        'landing': {'x': tile_x, 'y': tile_y}, 'damage': hit,
        'ground_object_id': ground.id, 'ground_object_event': 'created' if created else 'updated',
        'action_points': plan['action_points']}
