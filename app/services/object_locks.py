"""Замки объектов: правила автора, физические инструменты и атомарная попытка."""
from copy import deepcopy
import hashlib
import json
import random
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LobbyCharacter,LocationCharacter,LocationCombatState,LocationObject
from app.services.exceptions import ValidationError,PermissionDenied,ConflictError,NotFoundError
from app.services.transaction import atomic_operation
from app.services.action_journal import journaled

DIFFICULTIES={3:'Очень лёгкий',5:'Лёгкий',8:'Средний',10:'Тяжёлый',13:'Очень тяжёлый',15:'Безумный',18:'Невозможный'}
LOCK_FIELDS={'locked','lock_difficulty','lock_jammed'}


def supported(obj):
    props=obj.properties or {}
    return bool(obj.type in {'door','window','hatch','chest','box','military_crate','shelf'} or
        isinstance(props.get('contents'),list) and obj.type not in {'ground_item','corpse','body'}
        and not props.get('is_ground_item') and not props.get('is_corpse') and not props.get('portable_station'))


def validate_settings(obj,properties):
    if not LOCK_FIELDS.intersection(properties):return
    if not supported(obj):raise ValidationError('На этот объект нельзя установить замок')
    if 'locked' in properties and type(properties['locked']) is not bool:raise ValidationError('Укажите, закрыт ли замок')
    if 'lock_jammed' in properties and type(properties['lock_jammed']) is not bool:raise ValidationError('Укажите состояние замка')
    if 'lock_difficulty' in properties and (type(properties['lock_difficulty']) is not int or properties['lock_difficulty'] not in DIFFICULTIES):
        raise ValidationError('Выберите сложность замка из списка')
    if properties.get('locked') and properties.get('is_open',(obj.properties or {}).get('is_open')):
        raise ValidationError('Сначала закройте объект, затем заприте его')


def _tool(data,source,lobby_id,strength):
    from app.services.magazine import inventory_slot
    from app.services.item_template_lookup import resolve_item_template
    from app.services.equipment_module import _validate_source_path
    if source is None:return None,-10,None
    if not isinstance(source,dict):raise ValidationError('Выберите инструмент')
    path=source.get('path');_validate_source_path(path);parent,index=inventory_slot(data,path);item=parent[index]
    if item!=source.get('snapshot'):raise ConflictError('Инструмент изменился. Выберите его заново')
    template=resolve_item_template(item,lobby_id)
    if not template or item.get('name')!=template.name or item.get('category')!=template.category:
        raise ValidationError('Нет правил для выбранного инструмента')
    bonuses={'Отмычка':2,'Монтировка':3+strength,'Горелка':4,'Термический Заряд':6}
    if template.name not in bonuses:raise ValidationError('Этот инструмент не подходит для вскрытия замка')
    if type(item.get('quantity',1)) is not int or item.get('quantity',1)<1:raise ValidationError('Инструмент закончился')
    fuel=None
    if template.name=='Горелка':
        from app.services.portable_lights import walk
        for candidate_path,candidate in walk(data,True):
            if candidate_path[:2] not in (['inventory','pockets'],['inventory','backpack']) and candidate_path[:3] not in (
                    ['equipment','belt','pouches'],['equipment','vest','pouches']):continue
            definition=resolve_item_template(candidate,lobby_id)
            if definition and definition.name=='Баллон Горелки' and candidate.get('name')==definition.name and candidate.get('quantity',1)>0:
                fuel=candidate_path;break
        if fuel is None:raise ValidationError('Для горелки нужен Баллон Горелки')
    from app.services.character_traits import personal_tool_bonus
    return item,personal_tool_bonus(data,bonuses[template.name]),fuel


@journaled(kind='object_lock')
@atomic_operation
def attempt(lobby_id,object_id,character_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    from app.services.location_navigation import level_of,contains
    from app.services.deferred_action import ensure_no_deferred_action
    if not isinstance(payload,dict) or set(payload)-{'character_id','character_revision','object_revision','operation_id','mode','tool'}:
        raise ValidationError('Некорректная попытка вскрытия замка')
    obj=db.session.get(LocationObject,object_id);character=db.session.get(LobbyCharacter,character_id) if type(character_id) is int else None
    if not obj or obj.location.lobby_id!=lobby_id or not character or character.lobby_id!=lobby_id:
        raise NotFoundError('Объект или персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    actor=LocationCharacter.query.filter_by(location_id=obj.location_id,character_id=character.id).first()
    if not actor:raise ValidationError('Персонаж должен находиться в подлокации')
    identity=payload.get('operation_id')
    if not isinstance(identity,str) or not 1<=len(identity)<=120:raise ValidationError('Укажите идентификатор попытки')
    mode=payload.get('mode')
    if not isinstance(mode,str) or mode not in {'tools','force'}:raise ValidationError('Выберите способ вскрытия')
    signature=hashlib.sha256(json.dumps({'object_id':object_id,'mode':mode,'tool':payload.get('tool')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    data=deepcopy(character.data or {});receipts=data.setdefault('crafting',{}).setdefault('lock_receipts',[])
    previous=next((r for r in receipts if r['id']==identity),None)
    if previous:
        if previous['signature']!=signature:raise ConflictError('Идентификатор уже использован для другой попытки')
        return character,actor,obj,{**previous['result'],'duplicate':True}
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision or type(payload.get('object_revision')) is not int or payload['object_revision']!=obj.revision:
        raise ConflictError('Персонаж или объект изменился. Повторите выбор')
    if not supported(obj) or not (obj.properties or {}).get('locked'):raise ValidationError('На объекте нет закрытого замка')
    if (obj.properties or {}).get('gm_hidden') and user_id!=obj.location.lobby.gm_id:raise PermissionDenied('Объект не обнаружен')
    if (obj.properties or {}).get('lock_jammed'):raise ValidationError('Замок заклинило; открыть объект больше нельзя')
    footprint=CombatService._object_footprint_tiles(obj) or {(obj.tile_x,obj.tile_y)}
    actor_level=actor.floor_level or 0;level=level_of(obj)
    below_hatch=obj.type=='hatch' and actor_level==level-1
    if (actor_level!=level and not below_hatch) or min(max(abs(actor.pos_x-x),abs(actor.pos_y-y)) for x,y in footprint)>1:
        raise ValidationError('Подойдите к объекту на тот же этаж')
    if below_hatch and not any(o.type=='ladder' and level_of(o)==actor_level
            and (o.properties or {}).get('building_id')==(obj.properties or {}).get('building_id')
            and contains(obj,o.tile_x+.5+(o.properties or {}).get('offset_x',0),o.tile_y+.5+(o.properties or {}).get('offset_z',0))
            for o in LocationObject.query.filter_by(location_id=obj.location_id).all()):
        raise ValidationError('Чтобы достать замок люка снизу, нужна лестница')
    CombatService.ensure_character_can_act(actor);ensure_no_deferred_action(actor)
    state=LocationCombatState.query.filter_by(location_id=obj.location_id,status='active').first();cost=3 if state else 0
    if state and state.current_location_character_id!=actor.id:raise PermissionDenied('Сейчас не ход этого персонажа')
    if actor.action_points_current<cost:raise ValidationError('Для попытки вскрытия нужно 3 ОД')
    base=(obj.properties or {}).get('lock_difficulty',8)
    if type(base) is not int or base not in DIFFICULTIES:raise ValidationError('ГМ должен выбрать сложность замка')
    strength=CombatService._base_skill_modifier(data,'skills.physical.strength')
    engineering=CombatService._base_skill_modifier(data,'skills.other.engineering')
    agility=CombatService._base_skill_modifier(data,'skills.physical.agility')
    tool,bonus,fuel=_tool(data,payload.get('tool'),lobby_id,strength) if mode=='tools' else (None,0,None)
    if mode=='force' and payload.get('tool') is not None:raise ValidationError('При силовом вскрытии инструмент не выбирается')
    difficulty=max(1,base*1.5-strength if mode=='force' else base-agility-engineering-bonus)
    roll=random.randint(1,20);success=roll==20 or roll!=1 and roll>=difficulty
    broken=jammed=False;risk_roll=None;risk=0
    if not success:
        risk=max(0,min(100,60-strength*10)) if mode=='force' else max(0,min(100,50-engineering*10)) if tool and tool['name']=='Отмычка' else 0
        if risk:risk_roll=random.randint(1,100);jammed=mode=='force' and risk_roll<=risk;broken=mode=='tools' and risk_roll<=risk
    # Расходуется реальный инструмент/баллон; предметы на других путях не пересоздаются.
    consume=[]
    if tool and (broken or tool['name']=='Термический Заряд'):consume.append(payload['tool']['path'])
    if fuel:consume.append(fuel)
    # Сначала более поздний индекс в общем контейнере, чтобы не сместить второй источник.
    for path in sorted(consume,key=lambda p:(json.dumps(p[:-1]),p[-1]),reverse=True):CombatService._take_inventory_item(data,path,1)
    properties=deepcopy(obj.properties or {});properties['locked']=not success;properties['lock_jammed']=bool(jammed)
    obj.properties=properties;flag_modified(obj,'properties')
    if state:actor.action_points_current-=cost;CombatService._clear_aim(actor)
    result={'success':success,'roll':roll,'difficulty':difficulty,'base_difficulty':base,'mode':mode,'object_id':obj.id,'object_name':obj.name or 'Объект',
        'tool':tool.get('name') if tool else None,'tool_bonus':bonus,'strength_bonus':strength,'agility_bonus':agility,
        'engineering_bonus':engineering,'tool_broken':broken,'jammed':jammed,'risk_percent':risk,'risk_roll':risk_roll,
        'action_points':cost,'duplicate':False}
    receipts.append({'id':identity,'signature':signature,'result':deepcopy(result)});data['crafting']['lock_receipts']=receipts[-50:]
    character.data=data;flag_modified(character,'data')
    return character,actor,obj,result
