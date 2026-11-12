"""Тайники доступны только из текущей клетки группы."""
from copy import deepcopy
from app.extensions import db
from app.models import LobbyCharacter
from app.models.world_travel import WorldGroup, WorldStash
from app.services.character import CharacterService
from app.services.character_roles import can_join_world_group
from app.services.character_traits import personal_tool_bonus, trait_count
from app.services.combat import CombatService
from app.services.container_transfer import ContainerTransferService
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.map import MapService
from app.services.magazine import inventory_slot
from app.services.stashes import DIGGABLE_TERRAIN, digging_tool, knows, learn, masking_record
from app.services.transaction import atomic_operation


def _available(stash, user_id, character):
    return knows(stash.properties, user_id, character.id)


def _view(stash):
    return {'id':stash.id, 'revision':stash.revision, 'contents':stash.properties['contents']}


@atomic_operation
def operate(lobby, group_id, user_id, payload):
    group = WorldGroup.query.filter_by(lobby_id=lobby.id, id=group_id).with_for_update().first()
    if not group:
        raise NotFoundError('Группа не найдена')
    if 'tile_x' in payload and (payload['tile_x'],payload.get('tile_y')) != (group.tile_x,group.tile_y):
        raise ConflictError('Группа ушла с этой клетки')
    identity = payload.get('character_id')
    character = LobbyCharacter.query.filter_by(id=identity,lobby_id=lobby.id).first() if type(identity) is int else None
    if (not character or not can_join_world_group(character)
            or str(character.id) not in {str(value) for value in group.member_character_ids or []}):
        raise ValidationError('Выберите персонажа из группы')
    CharacterService.check_access(character, user_id, edit=True)
    data = deepcopy(character.data or {})
    action = payload.get('operation')
    query = WorldStash.query.filter_by(lobby_id=lobby.id, tile_x=group.tile_x, tile_y=group.tile_y)
    if action == 'list':
        return character, {'stashes':[_view(stash) for stash in query.order_by(WorldStash.id) if _available(stash,user_id,character)]}
    if CombatService._character_condition(data)['state'] != 'active':
        raise ValidationError('Персонаж не может действовать')
    # Координаты проверяются под блокировкой записи и на SQLite.
    locked = db.session.execute(db.update(WorldGroup).where(WorldGroup.id==group.id,
        WorldGroup.tile_x==group.tile_x,WorldGroup.tile_y==group.tile_y).values(updated_at=WorldGroup.updated_at))
    if locked.rowcount != 1:
        raise ConflictError('Группа ушла с этой клетки')

    check = None
    found = None
    if action == 'bury':
        operation_id = payload.get('operation_id')
        if not isinstance(operation_id,str) or not 1 <= len(operation_id) <= 100:
            raise ValidationError('Нужен идентификатор действия')
        existing = next((stash for stash in query
                         if stash.properties.get('creation') == [character.id,operation_id]),None)
        if existing:
            return character, {'stashes':[_view(stash) for stash in query.order_by(WorldStash.id) if _available(stash,user_id,character)]}
        tile = MapService.get_tile_data(lobby.id, group.tile_x, group.tile_y)
        if not tile or tile.get('terrain','grass') not in DIGGABLE_TERRAIN:
            raise ValidationError('Тайник можно закопать в грязи, песке или траве')
        parent,index = inventory_slot(data,payload.get('tool_path'))
        if not digging_tool(parent[index]):
            raise ValidationError('Для тайника нужна лопата или сапёрная лопатка')
        check = CombatService._narrative_skill_check(data,'skills.other.stealth',
            roll_bonus=4*trait_count(data,'Мастер маскировки') + personal_tool_bonus(data,3))
        mask = masking_record(user_id,check['total'],character.owner_id,character.id)
        db.session.add(WorldStash(lobby_id=lobby.id,tile_x=group.tile_x,tile_y=group.tile_y,
            properties={**mask,'contents':[],'creation':[character.id,operation_id]}))
    elif action == 'search':
        check = CombatService._narrative_skill_check(data,'skills.physical.awareness.visual')
        found = 0
        for stash in query.with_for_update():
            props = stash.properties
            if check['roll']==1 or check['roll']!=20 and check['total']<props['dc']:
                continue
            if not _available(stash,user_id,character):
                stash.properties = learn(props,user_id,character.id,character.owner_id)
            found += 1
    elif action == 'transfer':
        stash = query.filter_by(id=payload.get('stash_id')).with_for_update().first()
        if not stash or not _available(stash,user_id,character):
            raise NotFoundError('Тайник не найден в клетке группы')
        if payload.get('stash_revision')!=stash.revision or payload.get('character_revision')!=character.revision:
            raise ConflictError('Тайник или инвентарь изменились')
        props = deepcopy(stash.properties)
        ContainerTransferService.exchange_items(data,props,payload)
        stash.properties = props
        character.data = data
    else:
        raise ValidationError('Неизвестное действие с тайником')
    db.session.flush()
    return character, {'stashes':[_view(stash) for stash in query.order_by(WorldStash.id) if _available(stash,user_id,character)],
                       'check':check, 'found':found}
