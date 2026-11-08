"""Физический препарат в автоинъекторе; эффекты исполняет medical_procedure."""
from copy import deepcopy
from uuid import uuid4
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
from app.services.exceptions import ValidationError, ConflictError, NotFoundError, PermissionDenied
from app.services.transaction import atomic_operation
from app.services.action_journal import journaled

SLOT='auto_injector'


def available(armor):
    from app.services.equipment_modifications import installed
    return bool(isinstance(armor,dict) and not armor.get('protectionDisabled')
        and any(mod.get('ruleId') in {'armor-a15','armor-a31'} for mod in installed(armor)))


def loaded(armor):
    return [(i,item) for i,item in enumerate((armor or {}).get('installedModules') or [])
        if isinstance(item,dict) and item.get('slotType')==SLOT]


def validate_source(data,path,item,lobby_id):
    from app.services.medical_procedure import _profile,GENERIC_MEDICAL_BLOCKED_FIELDS
    armor=(data.get('equipment') or {}).get('armor')
    if not available(armor) or len(loaded(armor))!=1 or path!=['equipment','armor','installedModules',loaded(armor)[0][0]]:
        raise ValidationError('Выберите препарат в автоинъекторе надетой брони')
    if item.get('slotType')!=SLOT:raise ValidationError('Препарат не загружен в автоинъектор')
    _,direct=_profile(item,lobby_id)
    if direct.get('application_form')!='injectable' or any(direct.get(key) for key in
            GENERIC_MEDICAL_BLOCKED_FIELDS-{'applications','requires_injury','target_body_part','target_required'}):
        raise ValidationError('Для автоинъектора нужен инъекционный препарат')


def combat_actor(character,user_id):
    from app.services.combat import CombatService
    from app.services.deferred_action import ensure_no_deferred_action
    models=LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
        LocationCharacter.character_id==character.id,LocationCombatState.status=='active').all()
    if len(models)>1:raise ValidationError('Персонаж участвует в нескольких боях')
    actor=models[0] if models else None
    if actor:
        state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
        if state.current_location_character_id!=actor.id:raise PermissionDenied('Автоинъектор доступен в свой ход')
        CombatService.ensure_character_can_act(actor);ensure_no_deferred_action(actor)
    elif CombatService._character_condition(character.data or {})['state']!='active':
        raise ValidationError('Персонаж не может пользоваться автоинъектором')
    return actor


@journaled(kind='auto_injector')
@atomic_operation
def operate(character_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    from app.services.magazine import inventory_slot
    from app.services.equipment_module import _validate_source_path
    from app.services.medical_procedure import _profile,_validated_uses
    if not isinstance(payload,dict) or set(payload)-{'operation','character_revision','armor_id','source'}:
        raise ValidationError('Некорректное действие автоинъектора')
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:
        raise ConflictError('Лист изменился. Откройте его заново')
    data=deepcopy(character.data or {});armor=(data.get('equipment') or {}).get('armor')
    if not available(armor) or not armor.get('id') or armor['id']!=payload.get('armor_id'):
        raise ValidationError('Наденьте броню с автоинъектором')
    actor=combat_actor(character,user_id);operation=payload.get('operation')
    modules=armor.setdefault('installedModules',[])
    if operation=='load':
        if loaded(armor):raise ValidationError('Автоинъектор уже занят')
        source=payload.get('source') or {};path=source.get('path');_validate_source_path(path)
        parent,index=inventory_slot(data,path);item=parent[index]
        if item!=source.get('snapshot'):raise ConflictError('Препарат перемещён. Выберите его заново')
        template,direct=_profile(item,character.lobby_id)
        if type(item.get('quantity',1)) is not int or item.get('quantity',1)<1:raise ValidationError('Некорректная стопка препарата')
        candidate=deepcopy(item);candidate['slotType']=SLOT;candidate['quantity']=1
        modules.append(candidate)
        validate_source(data,['equipment','armor','installedModules',len(modules)-1],candidate,character.lobby_id)
        _validated_uses(candidate,1,template,actor_data=data)
        if item.get('quantity',1)>1:
            item['quantity']-=1;candidate['id']=str(uuid4())
        else:
            parent.pop(index);candidate.setdefault('id',str(uuid4()))
        cost=5 if actor else 0
    elif operation=='unload':
        entries=loaded(armor)
        if len(entries)!=1:raise ValidationError('В автоинъекторе нет препарата')
        index,item=entries[0];modules.pop(index);item.pop('slotType',None)
        data.setdefault('inventory',{}).setdefault('pockets',[]).append(item);cost=3 if actor else 0
    else:raise ValidationError('Выберите загрузку или извлечение препарата')
    if actor:
        if actor.action_points_current<cost:raise ValidationError(f'Для этого действия нужно {cost} ОД')
        actor.action_points_current-=cost;CombatService._clear_aim(actor)
    character.data=data;flag_modified(character,'data')
    return character,actor,{'operation':operation,'action_points':cost}
