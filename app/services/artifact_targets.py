"""Проверка цели направленного особого свойства в подлокации."""
import math

from app.models import LocationCharacter, LocationCombatState, LocationObject
from app.services.character_traits import facial_profile
from app.services.effects import normalize_effect_list
from app.services.exceptions import ValidationError
from app.services.explosion_geometry import distance

ELECTRIC = {'Громоотвод': (25, 200), 'Проводник бури': (35, 350)}
SIGHT_RADIUS = 25


def _blind(data):
    from app.services.combat import CombatService
    effects = normalize_effect_list((data.get('health') or {}).get('effects') or [])
    return facial_profile(data)['eyes'] == 2 or any(
        effect.get('active', True) and effect['type'] == 'blindness'
        and CombatService._coerce_float(effect.get('value'), 0) >= 100
        for effect in effects
    )


def select_electric_target(actor, data, target_id, radius, *, shared_sight=False):
    from app.services.combat import CombatService
    if not isinstance(actor, LocationCharacter) or type(target_id) is not int or target_id <= 0:
        raise ValidationError('Выберите видимую цель на подлокации')
    target = LocationCharacter.query.filter_by(location_id=actor.location_id, character_id=target_id).order_by(
        LocationCharacter.last_action.desc(), LocationCharacter.id.desc()).first()
    state = LocationCombatState.query.filter_by(location_id=actor.location_id, status='active').first()
    round_number = (state.round_number or 0) if state else 0
    if (not target or not target.character or target.character_id == actor.character_id or target.gm_hidden
            or (target.floor_level or 0) != (actor.floor_level or 0)
            or CombatService._character_condition(target.character.data)['state'] == 'dead'
            or state and CombatService._mutant_camouflage_active(target.character.data, round_number)):
        raise ValidationError('Цель недоступна')
    if _blind(data):
        raise ValidationError('Персонаж не видит цель')
    objects = LocationObject.query.filter_by(location_id=actor.location_id).all()
    if distance(actor.location, objects, actor.pos_x, actor.pos_y, actor.floor_level or 0, target) >= radius:
        raise ValidationError(f'Цель должна быть ближе {radius} м')
    x, y = math.floor(target.pos_x), math.floor(target.pos_y)
    dark_tiles = (actor.location.lighting or {}).get('dark_tiles') or []
    from app.services.artifact_special import has_daylight_vision
    from app.services.portable_lights import illumination
    in_darkness = (any(tile.get('x') == x and tile.get('z') == y for tile in dark_tiles)
                   and CombatService._is_dark_at_position(actor.location, x, y, objects=objects)
                   and not illumination(target, objects=objects)['lit'])

    def visible_from(observer, observer_data):
        return (math.hypot(target.pos_x-observer.pos_x, target.pos_y-observer.pos_y) <= SIGHT_RADIUS
                and (not in_darkness or has_daylight_vision(observer_data))
                and CombatService._can_see_location_character(observer, target, max_distance=SIGHT_RADIUS))

    if visible_from(actor, data):
        return target, round_number
    if shared_sight and actor.team_name:
        team = CombatService._unique_location_characters(LocationCharacter.query.filter_by(
            location_id=actor.location_id, team_name=actor.team_name).all())
        for observer in team:
            if (observer.character_id in {actor.character_id, target.character_id} or not observer.character
                    or observer.gm_hidden or (observer.floor_level or 0) != (target.floor_level or 0)):
                continue
            observer_data = observer.character.data or {}
            if (CombatService._character_condition(observer_data)['state'] not in {'dead', 'critical'}
                    and not _blind(observer_data) and visible_from(observer, observer_data)):
                return target, round_number
    raise ValidationError('Персонаж не видит цель')


def electric_damage(target, damage, round_number):
    from app.services.combat import CombatService
    protection = CombatService._target_elemental_protection(target.character.data or {}, 'electric', target=target)
    damage = damage * max(0, 100 - protection) / 100
    result = CombatService._apply_general_damage(target, damage, round_number=round_number,
                                                source='artifact_electric', damage_type='electric')
    CombatService._sync_location_effects_from_character(target)
    return result['damage']
