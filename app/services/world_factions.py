"""Группировки мира: пользовательские записи с устойчивыми ID."""
from copy import deepcopy
import re

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import LobbyCharacter, WorldFactionConfig
from app.services.exceptions import ConflictError, PermissionDenied, ValidationError
from app.services.world_factions_preset import ready_world_factions


def _require_gm(lobby,user_id):
    if lobby.gm_id!=user_id:raise PermissionDenied('Группировки мира настраивает только ГМ')


def _payload(config):
    return {'source':config.source,'groups':deepcopy(config.groups or []),
        'relations':deepcopy(config.relations or []),'revision':config.revision}


def get_config(lobby,user_id):
    _require_gm(lobby,user_id)
    config=db.session.get(WorldFactionConfig,lobby.id)
    return _payload(config) if config else {'source':'custom','groups':[],'relations':[],'revision':0}


def _identifier(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{8,80}',value))


def _name(value,label):
    if not isinstance(value,str) or not 1<=len(value.strip())<=100:
        raise ValidationError(f'{label}: укажите название до 100 символов')
    return value.strip()


def validate_config(payload):
    if not isinstance(payload,dict) or set(payload)!={'revision','source','groups','relations'}:
        raise ValidationError('Некорректный справочник группировок')
    source=payload['source']
    if source not in ('custom','ready','ready_customized'):
        raise ValidationError('Неизвестный источник группировок')
    groups=payload['groups'];relations=payload['relations']
    if not isinstance(groups,list) or len(groups)>100:
        raise ValidationError('Допустимо до 100 группировок')
    result=[];all_ids=set();group_ids=set();group_names=set()
    for group in groups:
        if not isinstance(group,dict) or set(group)!={'id','name','factions'} or not _identifier(group['id']):
            raise ValidationError('Некорректная группировка или её ID')
        name=_name(group['name'],'Группировка')
        if group['id'] in all_ids or name.casefold() in group_names:
            raise ValidationError('Группировки должны иметь разные ID и названия')
        all_ids.add(group['id']);group_ids.add(group['id']);group_names.add(name.casefold())
        factions=group['factions'];sub=[];subnames=set()
        if not isinstance(factions,list) or len(factions)>100:
            raise ValidationError('Допустимо до 100 фракций в одной группировке')
        for faction in factions:
            if not isinstance(faction,dict) or set(faction)!={'id','name'} or not _identifier(faction['id']):
                raise ValidationError('Некорректная фракция или её ID')
            faction_name=_name(faction['name'],'Фракция')
            if faction['id'] in all_ids or faction_name.casefold() in subnames:
                raise ValidationError('Фракции должны иметь разные ID и названия')
            all_ids.add(faction['id']);subnames.add(faction_name.casefold())
            sub.append({'id':faction['id'],'name':faction_name})
        result.append({'id':group['id'],'name':name,'factions':sub})
    if not isinstance(relations,list) or len(relations)>1000:
        raise ValidationError('Допустимо до 1000 отношений')
    normalized=[];pairs=set()
    for relation in relations:
        if not isinstance(relation,dict) or set(relation)!={'from_id','to_id','value'}:
            raise ValidationError('Некорректное отношение группировок')
        from_id,to_id=relation['from_id'],relation['to_id']
        if not _identifier(from_id) or not _identifier(to_id) or from_id not in group_ids or to_id not in group_ids or from_id==to_id:
            raise ValidationError('Отношение ссылается на удалённую или ту же группировку')
        pair=(from_id,to_id)
        if pair in pairs:raise ValidationError('Отношение группировок повторяется')
        pairs.add(pair)
        normalized.append({'from_id':from_id,'to_id':to_id,
            'value':_name(relation['value'],'Отношение')})
    if source=='ready' and (result!=ready_world_factions()['groups']
        or normalized!=ready_world_factions()['relations']):
        raise ValidationError('Чтобы изменить готовый набор, сначала создайте его копию')
    return {'source':source,'groups':result,'relations':normalized}


def _check_removed_references(lobby_id,old,new):
    old_groups={group['id'] for group in old};new_groups={group['id'] for group in new}
    old_factions={faction['id'] for group in old for faction in group['factions']}
    new_factions={faction['id'] for group in new for faction in group['factions']}
    removed_groups=old_groups-new_groups;removed_factions=old_factions-new_factions
    faction_groups={faction['id']:group['id'] for group in new for faction in group['factions']}
    old_faction_groups={faction['id']:group['id'] for group in old for faction in group['factions']}
    if not removed_groups and not removed_factions and old_faction_groups==faction_groups:return
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby_id).all():
        basic=(character.data or {}).get('basic') or {}
        group_id=basic.get('group_id');faction_id=basic.get('faction_id')
        if (isinstance(group_id,str) and group_id in removed_groups) or (isinstance(faction_id,str) and faction_id in removed_factions):
            raise ValidationError('Группировка или фракция используется персонажем: сначала переназначьте его')
        if isinstance(faction_id,str) and faction_id in faction_groups and group_id!=faction_groups[faction_id]:
            raise ValidationError('Фракция персонажа должна оставаться в его группировке')


def preserve_assignment(previous,incoming):
    old=(previous or {}).get('basic') or {}
    basic=incoming.setdefault('basic',{})
    for key in ('group_id','faction_id'):
        if key in old:basic[key]=old[key]
        else:basic.pop(key,None)
    if isinstance((previous or {}).get('npcGenerator'),dict):
        incoming['npcGenerator']=deepcopy(previous['npcGenerator'])
    else:incoming.pop('npcGenerator',None)


def assign_character(lobby,user_id,character_id,payload):
    _require_gm(lobby,user_id)
    if not isinstance(payload,dict) or set(payload)!={'character_revision','group_id','faction_id'}:
        raise ValidationError('Некорректная принадлежность персонажа')
    character=db.session.get(LobbyCharacter,character_id)
    if not character or character.lobby_id!=lobby.id:
        raise ValidationError('Персонаж не найден в этом мире')
    if type(payload['character_revision']) is not int or payload['character_revision']!=character.revision:
        raise ConflictError('Лист персонажа изменился. Откройте принадлежность заново')
    group_id=payload['group_id'];faction_id=payload['faction_id']
    if group_id is None:
        if faction_id is not None:raise ValidationError('Укажите группировку для фракции')
    else:
        config=db.session.get(WorldFactionConfig,lobby.id)
        group=next((group for group in (config.groups if config else []) if group['id']==group_id),None)
        if not group:raise ValidationError('Группировка не найдена в этом мире')
        if faction_id is not None and not any(faction['id']==faction_id for faction in group['factions']):
            raise ValidationError('Фракция не принадлежит выбранной группировке')
    data=deepcopy(character.data or {})
    basic=data.setdefault('basic',{})
    for key,value in (('group_id',group_id),('faction_id',faction_id)):
        if value is None:basic.pop(key,None)
        else:basic[key]=value
    if isinstance(data.get('npcGenerator'),dict):
        for key,value in (('group_id',group_id),('faction_id',faction_id)):
            if value is None:data['npcGenerator'].pop(key,None)
            else:data['npcGenerator'][key]=value
    from sqlalchemy.orm.attributes import flag_modified
    character.data=data;flag_modified(character,'data')
    try:db.session.commit()
    except StaleDataError as error:
        db.session.rollback()
        raise ConflictError('Лист персонажа изменился. Откройте принадлежность заново') from error
    return character


def save_config(lobby,user_id,payload):
    _require_gm(lobby,user_id)
    if not isinstance(payload,dict) or type(payload.get('revision')) is not int:
        raise ConflictError('Обновите справочник группировок')
    fields=validate_config(payload)
    config=db.session.get(WorldFactionConfig,lobby.id)
    revision=config.revision if config else 0
    if payload['revision']!=revision:
        if config and all(getattr(config,key)==value for key,value in fields.items()):
            return {**_payload(config),'replayed':True}
        raise ConflictError('Группировки изменились. Откройте настройки заново')
    if config and all(getattr(config,key)==value for key,value in fields.items()):
        return {**_payload(config),'replayed':True}
    _check_removed_references(lobby.id,config.groups or [] if config else [],fields['groups'])
    if not config:
        config=WorldFactionConfig(lobby_id=lobby.id,**fields)
        db.session.add(config)
    else:
        for key,value in fields.items():setattr(config,key,value)
    try:db.session.commit()
    except (StaleDataError,IntegrityError) as error:
        db.session.rollback()
        raise ConflictError('Группировки изменились. Откройте настройки заново') from error
    return {**_payload(config),'replayed':False}
