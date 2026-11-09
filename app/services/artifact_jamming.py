"""Подавление электроники в радиусе надетых артефактов."""
import math

from app.models import LocationCharacter, LocationObject
from app.services.artifact_effects import equipped_artifacts
from app.services.explosion_geometry import distance

AURAS = {'Железное сердце': 5, 'Полюс': 10}


def aura(data):
    names = {item.get('name') for item in equipped_artifacts(data)}
    return next(((name, AURAS[name]) for name in ('Полюс', 'Железное сердце') if name in names), (None, 0))


def local_sources(location_id, people=None):
    if people is None:
        from app.services.combat import CombatService
        people = CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=location_id).all())
    sources = []
    for model in people:
        if model.character:
            name, radius = aura(model.character.data)
            if radius:
                sources.append((model, name, radius))
    return sources


def for_actor(actor, *, sources=None, objects=None):
    if not getattr(actor, 'location_id', None) or not getattr(actor, 'location', None):
        return None
    if sources is None:
        sources = local_sources(actor.location_id)
    candidates = [(model, name, radius) for model, name, radius in sources
        if math.hypot(actor.pos_x-model.pos_x, actor.pos_y-model.pos_y) <= radius + 1e-9]
    if not candidates:
        return None
    if objects is None:
        objects = LocationObject.query.filter_by(location_id=actor.location_id).all()
    return next((name for model, name, radius in candidates if
        distance(actor.location, objects, actor.pos_x, actor.pos_y, actor.floor_level or 0, model) <= radius + 1e-9), None)


def for_character(character, *, actor=None, location_id=None):
    from app.services.artifact_area import group_characters
    from app.services.item_controllers import _context
    if actor is None:
        actor = _context(character, location_id)
    if actor is not None:
        return for_actor(actor)
    return next((name for member in group_characters(character) for name, radius in [aura(member.data)] if radius), None)


def position_sources(people, position):
    sources = []
    for character in people:
        name, radius = aura(character.data)
        if radius:
            point = position(character)
            if point:
                sources.append((point, name, radius))
    return sources


def lobby_sources(lobby_id):
    from app.models import LobbyCharacter
    from app.services.item_controllers import _position
    return position_sources(LobbyCharacter.query.filter_by(lobby_id=lobby_id), _position)


def for_position(point, sources):
    if not point:
        return None
    for origin, name, radius in sources:
        location_id = point.get('location_id')
        if location_id and origin.get('location_id') == location_id:
            if math.dist((point['world_x'], point['world_y'], point['height']),
                         (origin['world_x'], origin['world_y'], origin['height'])) <= radius + 1e-9:
                return name
        elif not location_id and not origin.get('location_id') and point.get('group_id') and point['group_id'] == origin.get('group_id'):
            return name
    return None
