"""Подготовка партии у аппарата и проверка его занятости."""
from sqlalchemy.orm.attributes import flag_modified

from app.models import LobbyCharacter, LocationCombatState


PREPARATION_RECIPES = {'recipe-aa7', 'recipe-aa13', 'recipe-aa48'}
RECIPES = PREPARATION_RECIPES | {'recipe-aa29'}
STATION = 'recipe-m40'


def is_distillery(obj):
    from app.services.crafting import normalized
    props = obj.properties or {}
    return props.get('portable_station') == STATION or (
        obj.type in {'workbench', 'household_workbench'}
        and normalized(props.get('craft_workbench') or obj.name) == 'самогонный аппарат')


def occupied(lobby_id, station_id):
    current = LobbyCharacter.data['crafting']['current']
    return LobbyCharacter.query.filter(
        LobbyCharacter.lobby_id == lobby_id,
        current['station_usage']['object_id'].as_integer() == station_id,
    ).first() is not None


def placement_at(character, station_id):
    from app.services.item_controllers import _context
    from app.services.portable_stations import nearby
    placement = _context(character)
    if placement and any(obj.id == station_id and is_distillery(obj) for obj in nearby(character, placement=placement)):
        return placement
    return None


def advance_lobby(lobby, seconds, *, resting_ids=()):
    if seconds <= 0:
        return []
    from app.services.combat import CombatService
    from app.services.crafting import now
    remaining = LobbyCharacter.data['crafting']['current']['preparation_remaining'].as_integer()
    preparing = LobbyCharacter.query.filter(LobbyCharacter.lobby_id == lobby.id, remaining > 0)
    updated = []
    for character in preparing:
        if character.id in resting_ids or CombatService._character_condition(character.data)['state'] != 'active':
            continue
        job = character.data['crafting']['current']
        placement = placement_at(character, job['station_usage']['object_id'])
        if not placement or LocationCombatState.query.filter_by(location_id=placement.location_id, status='active').first():
            continue
        left = job['preparation_remaining']
        job['preparation_remaining'] = max(0, left-seconds)
        if not job['preparation_remaining']:
            job['ready_at'] = now(character)-seconds+left+job['processing_seconds']
        flag_modified(character, 'data')
        updated.append(character)
    return updated
