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


def reanimation_ready(data):
    from app.services.equipment_modifications import installed
    armor=((data or {}).get('equipment') or {}).get('armor') or {}
    return (isinstance(armor,dict) and not armor.get('protectionDisabled')
        and armor.get('reanimationCharge') is not False
        and any(mod.get('ruleId')=='armor-d31' for mod in installed(armor)))


def consume_reanimation(data):
    if not reanimation_ready(data):return False
    armor=data['equipment']['armor'];health=data.setdefault('health',{})
    armor['reanimationCharge']=False
    health['painLevel']=0
    recovered=0
    for zone in (health.get('zones') or {}).values():
        if not isinstance(zone,dict):continue
        before=max(0,float(zone.get('current') or 0))
        maximum=max(0,float(zone.get('max') or 0))
        after=min(maximum,before+10)
        zone['current']=after
        zone['destructionDamage']=max(0,maximum-after)
        recovered+=after-before
    health['current']=min(float(health.get('max') or 700),float(health.get('current') or 0)+recovered)
    return True


def preserve(current,incoming):
    from app.services.portable_lights import walk
    previous={item.get('id'):item for _,item in walk(current or {},True) if item.get('id')}
    old_worn=((current or {}).get('equipment') or {}).get('gloves') or {}
    for path,item in walk(incoming,True):
        if item.get('category') in {'armor','helmet','gas_mask'} and item.get('id'):
            old=previous.get(item['id'])
            if old and old.get('stage3DamageType') is not None:
                item['stage3DamageType']=old['stage3DamageType']
            else:item.pop('stage3DamageType',None)
            if old and 'reanimationCharge' in old:item['reanimationCharge']=old['reanimationCharge']
            else:item.pop('reanimationCharge',None)
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


@atomic_operation
def use_spare_plates(character_id,user_id,payload):
    """Одноразово восстановить две стадии надетой тяжёлой брони."""
    from datetime import datetime,timezone
    from app.services.character import CharacterService
    from app.services.combat import CombatService,ARMOR_STAGE_LABELS
    from app.services.equipment_modifications import installed
    if not isinstance(payload,dict) or set(payload)!={'character_revision','item_id','operation_id'}:
        raise ValidationError('Некорректное использование запасных пластин')
    operation_id=payload['operation_id']
    if not isinstance(operation_id,str) or not 1<=len(operation_id)<=100:
        raise ValidationError('Укажите идентификатор действия')
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    previous=next((row for row in (((character.data or {}).get('health') or {}).get('combatMeta') or {}).get('actionJournal',[])
        if row.get('id')=='spare-plates:'+operation_id),None)
    if previous:
        if previous.get('item_id')!=payload['item_id']:
            raise ConflictError('Идентификатор действия уже использован для другой брони')
        return character,None,{'duplicate':True,'stage_after':previous.get('stage_after')}
    if type(payload['character_revision']) is not int or character.revision!=payload['character_revision']:
        raise ConflictError('Лист брони изменился. Откройте его заново')
    data=deepcopy(character.data or {});armor=(data.get('equipment') or {}).get('armor')
    if not isinstance(armor,dict) or armor.get('id')!=payload['item_id']:
        raise ConflictError('Броня перемещена. Откройте лист заново')
    record=next((entry for entry in installed(armor) if entry['ruleId']=='armor-a49'),None)
    if not record:raise ValidationError('Запасные пластины не установлены на этой броне')
    stage=CombatService._coerce_int(armor.get('stage'),1)
    if not 2<=stage<=5:raise ValidationError('Стадии брони уже целые')
    models=LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
        LocationCharacter.character_id==character_id,LocationCombatState.status=='active').all()
    if len(models)>1:raise ValidationError('Персонаж участвует в нескольких боях')
    actor=models[0] if models else None
    if actor:
        state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
        is_gm=CombatService._ensure_access(actor.location,user_id)
        if state.current_location_character_id!=actor.id or not CombatService._can_end_turn_for_character(actor,user_id,is_gm=is_gm):
            raise PermissionDenied('Использовать пластины можно только в свой ход')
        CombatService.ensure_character_can_act(actor)
        if actor.action_points_current<10:raise ValidationError('Для установки пластин нужно 10 ОД')
    elif CombatService._character_condition(data)['state']!='active':
        raise ValidationError('Персонаж не может использовать запасные пластины')
    new_stage=max(1,stage-2)
    armor['stage']=new_stage;armor['condition']=ARMOR_STAGE_LABELS[new_stage-1]
    if new_stage<3:armor.pop('stage3DamageType',None)
    template=CombatService._equipment_template(armor)
    attributes={**(template.attributes or {} if template else {}),**(armor.get('attributes') or {})}
    armor['stageDurability']=CombatService._armor_stage_capacity(armor,attributes)
    armor['currentStageDurability']=armor['stageDurability']
    armor['modifications']=[entry for entry in armor['modifications'] if entry is not record]
    if actor:actor.action_points_current-=10
    journal=data.setdefault('health',{}).setdefault('combatMeta',{}).setdefault('actionJournal',[])
    journal.append({'id':'spare-plates:'+operation_id,'timestamp':datetime.now(timezone.utc).isoformat(),
        'action':'spare_plates','kind':'equipment_care','label':'Запасные пластины',
        'status':'completed','location_id':actor.location_id if actor else None,
        'action_points':10 if actor else 0,'movement_points':0,'free_actions':0,
        'item_id':payload['item_id'],'stage_after':new_stage,
        'outcome':f'Броня: стадия {stage} → {new_stage}. Модификация израсходована.'})
    data['health']['combatMeta']['actionJournal']=journal[-100:]
    character.data=data;flag_modified(character,'data')
    return character,actor,{'duplicate':False,'stage_before':stage,'stage_after':new_stage,
        'action_points':10 if actor else 0}


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
