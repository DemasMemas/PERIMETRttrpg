"""Переносной свет: питание, подготовка, оплачиваемое переключение и луч."""
from copy import deepcopy
import math
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
from app.services.exceptions import ValidationError, PermissionDenied, ConflictError, NotFoundError
from app.services.transaction import atomic_operation
from app.services.action_journal import journaled

NAMES={'фонарик':'hand','улучшенный фонарик':'improved','налобный фонарик':'head','налобный фонарь':'head'}


def definition(item):
    attrs=(item or {}).get('attributes') or {}
    kind=attrs.get('portable_light') or NAMES.get(str((item or {}).get('name') or '').strip().casefold())
    if attrs.get('flashlight') or (str((item or {}).get('name') or '').strip().casefold()=='фонарик'
            and ((item or {}).get('category')=='weapon_module' or (item or {}).get('slotType')=='handguard')):kind='module'
    return kind if kind in {'hand','improved','head','module'} else None


def walk(data,all_items=False):
    result=[]
    def visit(value,path):
        if isinstance(value,list):
            for index,item in enumerate(value):visit(item,path+[index])
        elif isinstance(value,dict):
            if definition(value) or all_items:result.append((path,value))
            for key in ('contents','pouches','installedModules'):
                if isinstance(value.get(key),list):visit(value[key],path+[key])
    for key,items in (data.get('inventory') or {}).items():visit(items,['inventory',key])
    for key,item in (data.get('equipment') or {}).items():visit(item,['equipment',key])
    visit(data.get('weapons') or [],['weapons'])
    return result


def bindings(data):
    return (((data or {}).get('health') or {}).get('combatMeta') or {}).get('portableLights') or []


def power(item):
    from app.services.combat import CombatService
    state=item.setdefault('lightPower',{'uses_left':10,'elapsed_seconds':0})
    state['uses_left']=max(0,min(10,CombatService._coerce_int(state.get('uses_left'),10)))
    state['elapsed_seconds']=max(0,min(599,CombatService._coerce_float(state.get('elapsed_seconds'),0)))
    return state


def preserve(current,incoming):
    old=(((current or {}).get('health') or {}).get('combatMeta') or {})
    meta=incoming.setdefault('health',{}).setdefault('combatMeta',{})
    if 'portableLights' in old:meta['portableLights']=deepcopy(old['portableLights'])
    else:meta.pop('portableLights',None)
    states={item.get('id'):item.get('lightPower') for _,item in walk(current or {}) if item.get('id')}
    for _,item in walk(incoming):
        if item.get('id') in states:
            if states[item['id']] is None:item.pop('lightPower',None)
            else:item['lightPower']=deepcopy(states[item['id']])


def held_hand(data):
    ids={item.get('id') for _,item in walk(data)}
    return any(binding.get('mount')=='hand' and binding.get('id') in ids for binding in bindings(data))


def advance(data,seconds=0,*,battle_end=False):
    items={item.get('id'):item for _,item in walk(data)}
    changed=False
    for binding in bindings(data):
        item=items.get(binding.get('id'))
        if not item:continue
        state=power(item);before=deepcopy((state,binding))
        if binding.get('on'):
            elapsed=max(0,float(state.get('elapsed_seconds') or 0))+max(0,seconds)
            used=int(elapsed//600)
            state['uses_left']=max(0,int(state.get('uses_left',10))-used)
            state['elapsed_seconds']=elapsed%600 if state['uses_left'] else 0
            if seconds>0:binding['period_used']=bool(state['elapsed_seconds']) if used else True
            if state['uses_left']==0:binding['on']=False
        if battle_end and (binding.get('battle_used') or binding.get('on')) and binding.get('period_used',True):
            state['uses_left']=max(0,int(state.get('uses_left',10))-1)
            state['elapsed_seconds']=0;binding['battle_used']=False;binding['period_used']=False
            if not state['uses_left']:binding['on']=False
        changed=changed or before!=(state,binding)
    return changed


def start_battle(data):
    changed=False
    for binding in bindings(data):
        if binding.get('on'):
            binding['battle_used']=binding['period_used']=True;changed=True
    return changed


def advance_lobby(lobby,seconds):
    updated=[]
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby.id).all():
        if not bindings(character.data):continue
        data=deepcopy(character.data or {})
        if advance(data,seconds):
            character.data=data;flag_modified(character,'data');updated.append(character)
    return updated


def ready_sources(actor):
    from app.services.combat import CombatService
    data=(actor.character.data if actor.character else {}) or {};items={item.get('id'):item for _,item in walk(data)}
    result=[];weapons=data.get('weapons') or [];primary=actor.drawn_weapon_index
    for binding in bindings(data):
        item=items.get(binding.get('id'))
        if not item or not binding.get('on') or (item.get('lightPower') or {}).get('uses_left',10)<=0:continue
        mount=binding.get('mount')
        if mount in {'weapon','taped'} and not (type(primary) is int and 0<=primary<len(weapons)
                and weapons[primary].get('id')==binding.get('weapon_id')):continue
        facing=math.hypot(actor.facing_x or 0,actor.facing_y or 0) or 1
        result.append({'id':binding['id'],'mount':mount,'improved':definition(item)=='improved',
            'range':35,'end_width':5,'x':actor.pos_x+.5+.35*(actor.facing_x or 0)/facing,'z':actor.pos_y+.5+.35*(actor.facing_y or 0)/facing,
            'y':CombatService.physical_position(actor)['height']+1.4,
            'facing_x':actor.facing_x,'facing_y':actor.facing_y,'floor_level':actor.floor_level or 0})
    return result


def covers(actor,target,*,wide=False):
    from app.services.combat import CombatService
    sources=ready_sources(actor)
    if not sources or actor.id==target.id:return []
    # Проверка тела и перекрытий использует действующую геометрию света/взрывов.
    dx=target.pos_x-actor.pos_x;dz=target.pos_y-actor.pos_y
    dy=CombatService.physical_position(target)['height']-CombatService.physical_position(actor)['height']
    length=math.sqrt(dx*dx+dz*dz+dy*dy)
    facing=math.hypot(actor.facing_x or 0,actor.facing_y or 0)
    if not facing:return []
    angle=math.cos(math.pi/4 if wide else math.atan(2.5/35))
    dot=(dx*(actor.facing_x or 0)+dz*(actor.facing_y or 0))/(length*facing) if length>1e-9 else 1
    if length>35 or dot<angle-1e-9:return []
    if CombatService._cover_analysis(actor.location_id,actor,target).get('zones',{}).get('head'):
        return []
    return sources


def illumination(target):
    from app.services.combat import CombatService
    if not getattr(target,'location_id',None) or not getattr(target,'location',None):
        return {'lit':False,'penalty':None,'base_dark':False}
    lights=[]
    for actor in LocationCharacter.query.filter_by(location_id=target.location_id).all():lights.extend(covers(actor,target))
    return {'lit':bool(lights),'penalty':0 if any(light['improved'] for light in lights) else 3 if lights else None,
        'base_dark':CombatService._is_dark_at_character(target.location,target)}


def shooting(actor,target,fire_mode):
    from app.services.combat import CombatService
    from app.services.weapon_modules import uses_nvg
    nvg=uses_nvg(actor.character.data or {});bonus=0;improved=False
    for source in LocationCharacter.query.filter_by(location_id=actor.location_id).all():
        lit=covers(source,target)
        if lit:bonus=2;improved=improved or any(item['improved'] for item in lit)
    facing=covers(target,actor,wide=True)
    penalty=(1 if fire_mode=='aimed' else 3) if facing else 0
    from app.services.lighting_rules import world_is_dark
    dark=world_is_dark(actor.location.lobby.game_time_minutes) or CombatService._is_dark_at_character(actor.location,actor) or CombatService._is_dark_at_character(actor.location,target)
    if not dark:bonus=penalty=0
    return {'accuracy':(bonus-penalty)*(2 if nvg else 1), 'lit':bool(bonus), 'improved':improved,
        'aimed_blocked':bool(dark and nvg and facing and fire_mode=='aimed')}


def ergonomics(actor,weapon):
    from app.services.combat import CombatService
    character=getattr(actor,'character',None)
    data=character.data if character and isinstance(character.data,dict) else {}
    if any(binding.get('mount')=='taped' and binding.get('weapon_id')==weapon.get('id') for binding in bindings(data)):return -15
    if not held_hand(data):return 0
    return 0 if CombatService._is_pistol_weapon(weapon) else -25


def missing_templates(existing,subcategory=None):
    if subcategory:return []
    names={str(item.get('name') or '').strip().casefold() for item in existing if item.get('category')!='weapon_module'}
    return [{'id':-32001-index,'name':name,'category':'misc','subcategory':None,'price':price,'weight':.5,'volume':1,
        'compatible_ids':[],'description':'Конус света: 35 м, ширина на конце 5 м. Батарейка: 10 использований. Включить или выключить — 1 ОД.',
        'attributes':{'portable_light':kind}}
        for index,(name,kind,price) in enumerate([('Фонарик','hand',400),('Улучшенный фонарик','improved',1500),('Налобный фонарик','head',1500)])
        if name.casefold() not in names]


@journaled(kind='portable_light')
@atomic_operation
def operate(character_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    from app.services.inventory_access import calculate_inventory_access
    if not isinstance(payload,dict) or set(payload)-{'operation','path','mount','weapon_index','character_revision','battery_path','source_id'}:
        raise ValidationError('Некорректные параметры фонаря')
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:raise ConflictError()
    data=deepcopy(character.data or {});path=payload.get('path')
    selected=next((item for item_path,item in walk(data) if item_path==path),None)
    if not selected:raise ValidationError('Фонарь перемещён или отсутствует')
    if 'source_id' in payload and selected.get('id')!=payload['source_id']:raise ConflictError('Фонарь перемещён. Выберите его заново.')
    models=LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
        LocationCharacter.character_id==character_id,LocationCombatState.status=='active').all()
    if len(models)>1:raise ValidationError('Персонаж участвует в нескольких боях')
    actor=models[0] if models else None
    if actor:
        state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
        is_gm=CombatService._ensure_access(actor.location,user_id)
        if state.current_location_character_id!=actor.id or not CombatService._can_end_turn_for_character(actor,user_id,is_gm=is_gm):
            raise PermissionDenied('Фонарём можно управлять только в свой ход')
        CombatService.ensure_character_can_act(actor)
    elif CombatService._character_condition(data)['state']!='active':raise ValidationError('Персонаж не может управлять фонарём')
    selected.setdefault('id',str(uuid4()));states=data.setdefault('health',{}).setdefault('combatMeta',{}).setdefault('portableLights',[])
    binding=next((item for item in states if item['id']==selected['id']),None)
    operation=payload.get('operation');cost=0
    if operation=='prepare':
        mount=payload.get('mount') or ('head' if definition(selected)=='head' else 'weapon' if definition(selected)=='module' else 'hand')
        if binding:raise ValidationError('Фонарь уже подготовлен')
        if mount not in {'hand','head','weapon','taped'}:raise ValidationError('Выберите крепление фонаря')
        if actor and mount in {'head','taped'}:raise ValidationError('Пока подготовьте это крепление до боя')
        if mount=='head' and definition(selected)!='head':raise ValidationError('Для головы нужен налобный фонарь')
        if definition(selected)=='module' and mount!='weapon':raise ValidationError('Оружейный модуль крепится к оружию')
        binding={'id':selected['id'],'mount':mount,'on':False}
        if mount in {'weapon','taped'}:
            index=payload.get('weapon_index')
            weapons=data.get('weapons') or []
            if type(index) is not int or not 0<=index<len(weapons):raise ValidationError('Выберите оружие для фонаря')
            if mount=='weapon' and path[:3]!=['weapons',index,'installedModules']:raise ValidationError('Фонарь должен быть установлен модулем этого оружия')
            weapons[index].setdefault('id',str(uuid4()));binding['weapon_id']=weapons[index]['id']
        if mount=='hand':
            from app.services.weapon_grip import functional_arms
            primary=actor.drawn_weapon_index if actor else data.get('activeWeaponIndex')
            armed=type(primary) is int and 0<=primary<len(data.get('weapons') or [])
            meta=data.get('health',{}).get('combatMeta',{})
            if meta.get('secondaryWeaponId') and any(item.get('id')==meta['secondaryWeaponId'] for item in data.get('weapons') or []):
                raise ValidationError('Обе руки заняты оружием')
            if held_hand(data) or functional_arms(data)<(2 if armed or meta.get('heldWaterItem') else 1) or meta.get('bodyTransport') or actor and actor.grapple_target_id:
                raise ValidationError('Для ручного фонаря нужна свободная действующая рука')
            if armed and meta.get('heldWaterItem'):raise ValidationError('Руки заняты оружием и водой')
            if actor:cost=max(1,int(calculate_inventory_access(data,path,selected)['retrieval_action_points']))
        states.append(binding);power(selected)
    elif operation=='toggle':
        if not binding:raise ValidationError('Сначала подготовьте фонарь')
        if not binding.get('on') and power(selected)['uses_left']<=0:raise ValidationError('Батарейка фонаря разряжена')
        cost=1 if actor else 0;binding['on']=not binding.get('on')
        if binding['on']:binding['period_used']=True
        if actor:binding['battle_used']=True
    elif operation in {'stow','battery'}:
        if actor:raise ValidationError('Пока выполняйте смену батарейки и крепления вне боя')
        if operation=='stow':states[:]=[item for item in states if item['id']!=selected['id']]
        else:
            battery=CombatService._inventory_item_at_path(data,payload.get('battery_path'))
            if 'батаре' not in str(battery.get('name') or '').casefold():raise ValidationError('Выберите батарейку')
            CombatService._take_inventory_item(data,payload['battery_path'],1)
            selected['lightPower']={'uses_left':10,'elapsed_seconds':0}
    else:raise ValidationError('Неизвестное действие фонаря')
    if actor and actor.action_points_current<cost:raise ValidationError('Недостаточно ОД для фонаря')
    if actor:actor.action_points_current-=cost
    character.data=data;flag_modified(character,'data')
    return character,actor,{'operation':operation,'action_points':cost}
