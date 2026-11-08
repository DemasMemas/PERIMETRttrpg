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


def liquid_crystal_ready(item):
    from app.services.equipment_modifications import rule
    state=(item or {}).get('liquidCrystalState') or {}
    return bool(isinstance(item,dict) and rule(item,'liquid_crystal_layer')
                and state.get('charged') is not False)


def consume_liquid_crystal(item):
    if not liquid_crystal_ready(item):return False
    receipts=((item.get('liquidCrystalState') or {}).get('receipts') or [])
    item['liquidCrystalState']={'charged':False,'remaining_seconds':600,
                                **({'receipts':receipts} if receipts else {})}
    return True


def neck_brace_ready(item):
    from app.services.equipment_modifications import installed
    return bool(isinstance(item,dict) and not item.get('protectionDisabled')
        and any(mod.get('ruleId')=='armor-g37' for mod in installed(item))
        and (item.get('neckBraceState') or {}).get('charged') is not False)


def consume_neck_brace(item):
    receipts=(item.get('neckBraceState') or {}).get('receipts') or []
    item['neckBraceState']={'charged':False,'remaining_seconds':600,**({'receipts':receipts} if receipts else {})}
    return {'item_id':item.get('id'),'name':item.get('name') or 'Шлем','remaining_seconds':600}


def preserve(current,incoming):
    from app.services.portable_lights import walk
    previous={item.get('id'):item for _,item in walk(current or {},True) if item.get('id')}
    old_worn=((current or {}).get('equipment') or {}).get('gloves') or {}
    for path,item in walk(incoming,True):
        if item.get('category') in {'armor','helmet','gas_mask'} and item.get('id'):
            old=previous.get(item['id'])
            from app.services.auto_injector import loaded,SLOT
            old_injections=[deepcopy(module) for _,module in loaded(old)]
            if old_injections or loaded(item):
                item['installedModules']=[module for module in item.get('installedModules') or []
                    if not isinstance(module,dict) or module.get('slotType')!=SLOT]+old_injections
            if old and old.get('stage3DamageType') is not None:
                item['stage3DamageType']=old['stage3DamageType']
            else:item.pop('stage3DamageType',None)
            if old and 'reanimationCharge' in old:item['reanimationCharge']=old['reanimationCharge']
            else:item.pop('reanimationCharge',None)
            if old and 'liquidCrystalState' in old:item['liquidCrystalState']=deepcopy(old['liquidCrystalState'])
            else:item.pop('liquidCrystalState',None)
            if old and 'neckBraceState' in old:item['neckBraceState']=deepcopy(old['neckBraceState'])
            else:item.pop('neckBraceState',None)
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
        brace=item.get('neckBraceState')
        if seconds>0 and isinstance(brace,dict) and brace.get('charged') is False:
            before=brace.get('remaining_seconds',0);brace['remaining_seconds']=max(0,float(before)-seconds)
            changed=changed or before!=brace['remaining_seconds']
        crystal=item.get('liquidCrystalState') if isinstance(item,dict) else None
        if seconds>0 and isinstance(crystal,dict) and crystal.get('charged') is False:
            before=crystal.get('remaining_seconds',0)
            crystal['remaining_seconds']=max(0,float(before)-seconds)
            changed=changed or before!=crystal['remaining_seconds']
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
               or isinstance(item.get('neckBraceState'),dict) and item['neckBraceState'].get('remaining_seconds',0)>0
               or isinstance(item.get('liquidCrystalState'),dict) and item['liquidCrystalState'].get('remaining_seconds',0)>0
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


@atomic_operation
def recharge_liquid_crystal(character_id,user_id,payload,*,kind='liquid_crystal'):
    """После ожидания персонаж рядом проверяет Инженерию СЛ 12."""
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    if kind not in {'liquid_crystal','neck_brace'}:raise ValidationError('Неизвестный вид перезарядки')
    slot,state_key,rule_id,label=('helmet','neckBraceState','armor-g37','Фиксатор шеи') if kind=='neck_brace' else ('armor','liquidCrystalState','armor-a39','Жидкокристаллический слой')
    if not isinstance(payload,dict) or set(payload)!={'character_revision','engineer_revision','engineer_id','item_id','operation_id'}:
        raise ValidationError('Некорректная проверка перезарядки')
    operation_id=payload['operation_id']
    if not isinstance(operation_id,str) or not 1<=len(operation_id)<=100:
        raise ValidationError('Укажите идентификатор действия')
    target=db.session.get(LobbyCharacter,character_id)
    engineer=db.session.get(LobbyCharacter,payload['engineer_id']) if type(payload['engineer_id']) is int else None
    if not target or not engineer or target.lobby_id!=engineer.lobby_id:
        raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(engineer,user_id,edit=True)
    target_data=deepcopy(target.data or {})
    armor=(target_data.get('equipment') or {}).get(slot)
    if not isinstance(armor,dict) or armor.get('id')!=payload['item_id']:
        raise ConflictError('Снаряжение перемещено. Откройте лист заново')
    state=armor.get(state_key) or {}
    receipt=next((entry for entry in state.get('receipts') or [] if entry.get('id')==operation_id),None)
    if receipt:return target,engineer,{**receipt['result'],'duplicate':True}
    if (type(payload['character_revision']) is not int or payload['character_revision']!=target.revision
            or type(payload['engineer_revision']) is not int or payload['engineer_revision']!=engineer.revision):
        raise ConflictError('Лист персонажа изменился. Откройте его заново')
    if (neck_brace_ready if kind=='neck_brace' else liquid_crystal_ready)(armor):raise ValidationError(f'{label} уже заряжен')
    if not any(mod.get('ruleId')==rule_id for mod in (armor.get('modifications') or []) if isinstance(mod,dict)):
        raise ValidationError(f'{label} не установлен')
    if armor.get('protectionDisabled'):raise ValidationError('Снаряжение отключено')
    if float(state.get('remaining_seconds',0))>0:
        raise ValidationError('До перезарядки ещё не прошло 10 игровых минут')
    engineer_data=target_data if engineer.id==target.id else deepcopy(engineer.data or {})
    if CombatService._character_condition(engineer_data)['state']!='active':
        raise ValidationError('Персонаж не может перезарядить броню')
    engineer_model=LocationCharacter.query.filter_by(character_id=engineer.id).first()
    target_model=LocationCharacter.query.filter_by(character_id=target.id).first()
    if engineer.id!=target.id:
        if (not engineer_model or not target_model or engineer_model.location_id!=target_model.location_id
                or (engineer_model.floor_level or 0)!=(target_model.floor_level or 0)
                or max(abs(engineer_model.pos_x-target_model.pos_x),abs(engineer_model.pos_y-target_model.pos_y))>1):
            raise ValidationError('Инженер должен находиться рядом с владельцем брони')
    action_points=0
    for model in (engineer_model,target_model):
        if model and LocationCombatState.query.filter_by(location_id=model.location_id,status='active').first():
            if kind!='neck_brace':raise ValidationError('Перезарядка доступна вне боя')
            state_model=LocationCombatState.query.filter_by(location_id=model.location_id,status='active').one()
            if not engineer_model or engineer_model.location_id!=model.location_id or state_model.current_location_character_id!=engineer_model.id:
                raise ValidationError('Перезарядка доступна в ход инженера')
            CombatService.ensure_character_can_act(engineer_model)
            if engineer_model.action_points_current<3:raise ValidationError('Для перезарядки нужно 3 ОД')
            action_points=3
    check=CombatService._narrative_skill_check(engineer_data,'skills.other.engineering')
    success=not check.get('automatic_failure') and check['total']>=12
    state['charged']=bool(success)
    state['remaining_seconds']=0 if success else 600
    result={'success':bool(success),'check':check,'remaining_seconds':state['remaining_seconds'],'duplicate':False,'action_points':action_points}
    state.setdefault('receipts',[]).append({'id':operation_id,'result':deepcopy(result)})
    state['receipts']=state['receipts'][-20:]
    armor[state_key]=state
    if action_points:engineer_model.action_points_current-=action_points
    if kind=='neck_brace':
        from datetime import datetime,timezone
        journal=engineer_data.setdefault('health',{}).setdefault('combatMeta',{}).setdefault('actionJournal',[])
        journal.append({'id':'neck-brace:'+operation_id,'timestamp':datetime.now(timezone.utc).isoformat(),
            'action':'neck_brace_recharge','kind':'equipment_care','label':'Перезарядка фиксатора шеи','status':'completed',
            'action_points':action_points,'movement_points':0,'free_actions':0,'item_id':payload['item_id'],
            'outcome':'Фиксатор шеи готов' if success else 'Проверка не удалась; ожидание 10 минут началось заново'})
        engineer_data['health']['combatMeta']['actionJournal']=journal[-100:]
    target.data=target_data;flag_modified(target,'data')
    if engineer.id!=target.id:
        engineer.data=engineer_data;flag_modified(engineer,'data')
    return target,engineer,result


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
