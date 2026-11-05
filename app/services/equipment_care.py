"""Стерильность экземпляра перчаток и оплаченная обработка без приёма алкоголя."""
from copy import deepcopy
import re
from uuid import uuid4
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LobbyCharacter,LocationCharacter,LocationCombatState
from app.services.exceptions import ValidationError,ConflictError,NotFoundError,PermissionDenied
from app.services.transaction import atomic_operation
from app.services.action_journal import journaled


def medical_gloves(item):
    return isinstance(item,dict) and bool((item.get('attributes') or {}).get('sterile_duration_seconds')
        or item.get('category')=='gloves' and str(item.get('name','')).casefold()=='медицинские')


def regenerating_armor(item):
    from app.services.equipment_modifications import rule
    if not isinstance(item,dict) or not rule(item,'polymer_regeneration'):return False
    try:stage=int(item.get('stage') or 1)
    except (TypeError,ValueError):return False
    if not 1<=stage<5:return False
    capacity=item.get('stageDurability')
    current=item.get('currentStageDurability')
    return (type(capacity) in {int,float} and capacity>0
        and type(current) in {int,float} and 0<=current<capacity)


def preserve(current,incoming):
    from app.services.portable_lights import walk
    previous={item.get('id'):item for _,item in walk(current or {},True) if item.get('id')}
    old_worn=((current or {}).get('equipment') or {}).get('gloves') or {}
    for path,item in walk(incoming,True):
        if not medical_gloves(item):continue
        old=previous.get(item.get('id'))
        if not old and not item.get('id') and path==['equipment','gloves'] and medical_gloves(old_worn):old=old_worn
        if old and old.get('sterility') is not None:item['sterility']=deepcopy(old['sterility'])
        else:item.pop('sterility',None)
        if path==['equipment','gloves']:
            item.setdefault('id',str(uuid4()))
            item.setdefault('sterility',{'remaining_seconds':3600})


def advance(data,seconds):
    from app.services.portable_lights import walk
    changed=False
    for _,item in walk(data,True):
        if seconds>0 and regenerating_armor(item):
            before=item['currentStageDurability']
            item['currentStageDurability']=min(item['stageDurability'],before+seconds/6)
            changed=changed or before!=item['currentStageDurability']
        state=item.get('sterility')
        if not medical_gloves(item) or not isinstance(state,dict):continue
        before=state.get('remaining_seconds',0)
        state['remaining_seconds']=max(0,float(before)-max(0,seconds))
        changed=changed or before!=state['remaining_seconds']
    return changed


def has_timers(data):
    from app.services.portable_lights import walk
    return any((medical_gloves(item) and item.get('sterility')) or regenerating_armor(item)
        for _,item in walk(data,True))


def infection_multiplier(data):
    gloves=((data or {}).get('equipment') or {}).get('gloves') or {}
    return .5 if medical_gloves(gloves) and (gloves.get('sterility') or {}).get('remaining_seconds',0)>0 else 1


@journaled(kind='equipment_care')
@atomic_operation
def sterilize(character_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.character_interaction import CharacterInteractionService
    from app.services.combat import CombatService
    from app.services.general_consumable import _consume_entry
    from app.services.item_template_lookup import resolve_item_template
    from app.services.magazine import inventory_slot
    if not isinstance(payload,dict) or set(payload)-{'character_revision','source_id','path'}:raise ValidationError('Некорректные параметры обработки')
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if type(payload.get('character_revision')) is not int or character.revision!=payload['character_revision']:raise ConflictError()
    data=deepcopy(character.data or {});gloves=(data.get('equipment') or {}).get('gloves')
    if not medical_gloves(gloves):raise ValidationError('Сначала наденьте медицинские перчатки')
    models=LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
        LocationCharacter.character_id==character_id,LocationCombatState.status=='active').all()
    if len(models)>1:raise ValidationError('Персонаж участвует в нескольких боях')
    actor=models[0] if models else None
    if actor:
        state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
        is_gm=CombatService._ensure_access(actor.location,user_id)
        if state.current_location_character_id!=actor.id or not CombatService._can_end_turn_for_character(actor,user_id,is_gm=is_gm):raise PermissionDenied('Обработка доступна в свой ход')
        CombatService.ensure_character_can_act(actor)
        if actor.action_points_current<3:raise ValidationError('Для обработки нужно 3 ОД')
    elif CombatService._character_condition(data)['state']!='active':raise ValidationError('Персонаж не может обработать перчатки')
    path=CharacterInteractionService._resolve_item_path(data,payload.get('source_id'),payload.get('path'))
    parent,index=inventory_slot(data,path);item=parent[index]
    template=resolve_item_template(item,character.lobby_id)
    if not re.search(r'\b(?:спирт|водка)\b',str(template.name if template else item.get('name','')),re.I):raise ValidationError('Выберите спирт или водку')
    if not _consume_entry(parent,index,1):raise ValidationError('Средство закончилось')
    gloves.setdefault('id',str(uuid4()));gloves['sterility']={'remaining_seconds':3600}
    if actor:actor.action_points_current-=3
    character.data=data;flag_modified(character,'data')
    return character,actor,{'action_points':3 if actor else 0,'sterile_seconds':3600}
