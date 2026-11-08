"""Физические взрыватели: привязка, установка, перенос и однократный подрыв."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import json
import math

from sqlalchemy import inspect,event
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db,socketio
from app.models import LobbyCharacter,LocationCharacter,LocationObject,LocationTrap,LocationCombatState,WorldGroup,DetonationEvent
from app.services.exceptions import ValidationError,ConflictError,NotFoundError
from app.services.transaction import atomic_operation
from app.services.portable_lights import walk,definition,pda_state,held_pda

LABELS={'install':'Установка взрывателя','bind':'Привязка взрывателя','disarm':'Оторвать провод','detonate':'Дистанционный подрыв','timer':'Настройка таймера','activate':'Нажать кнопку таймера'}


@event.listens_for(Session,'after_commit')
def _notify_pending(session):
    if session.in_nested_transaction():return
    for lobby_id,gm_id in session.info.pop('controller_notifications',{}).items():
        socketio.emit('detonation_pending',{'lobby_id':lobby_id},room=f'user_{gm_id}')


@event.listens_for(Session,'after_rollback')
def _clear_notifications(session):session.info.pop('controller_notifications',None)


def enabled():return inspect(db.session.connection()).has_table('detonation_events')


def kind(item,lobby_id):
    from app.services.item_template_lookup import resolve_item_template
    template=resolve_item_template(item,lobby_id)
    value=(template.attributes or {}).get('device_kind') if template else None
    return value if value in {'remote_detonator','timer'} else None


def profiles(item,lobby_id):
    from app.services.combat import CombatService
    profile=CombatService._explosive_profile(item)
    if profile:return [profile]
    from app.services.item_template_lookup import resolve_item_template
    from app.services.trap_catalog import PROFILES
    template=resolve_item_template(item,lobby_id)
    key=(template.attributes or {}).get('trap_recipe') if template else None
    p=PROFILES.get(key,{})
    if p.get('kind')=='mine':
        return [{'key':'trap-'+key,'name':p['name'],'radius':len(p['blast']),
            'fragment':p['fragments'][0],'penetration':p['penetration'][0],'blast_base':p['blast'][0],'blast_falloff':0,
            'radial_damage':{k:p[k] for k in ('fragments','penetration','blast')},'fuse':'instant'}]
    if p.get('kind')=='tripwire':
        result=[CombatService._explosive_profile(g) for g in item.get('trapGrenades') or []]
        if len(result)==p['grenades'] and all(result):return result
    return []


def preserve(current,incoming):
    old={item.get('id'):item.get('controllerState') for _,item in walk(current or {},True) if item.get('id')}
    seen=set()
    for _,item in walk(incoming,True):
        previous=old.get(item.get('id'))
        if previous is None:item.pop('controllerState',None)
        else:
            item['controllerState']=deepcopy(previous)
            if previous.get('kind') in {'remote_detonator','timer'}:
                if previous['id'] in seen or item.get('quantity',1)!=1:raise ValidationError('Заряд со взрывателем нельзя дублировать или объединять в стопку')
                seen.add(previous['id'])
    old_receipts=(current.get('crafting') or {}).get('controller_receipts')
    target=incoming.setdefault('crafting',{})
    if old_receipts is None:target.pop('controller_receipts',None)
    else:target['controller_receipts']=deepcopy(old_receipts)


def _get(data,path,identity=None):
    if not isinstance(path,list) or not path or any(type(key) not in {str,int} for key in path):raise ValidationError('Выберите предмет')
    item=next((item for current,item in walk(data,True) if current==path),None)
    if not item or identity is not None and item.get('id')!=identity:raise ConflictError('Предмет перемещён')
    return item


def _context(character,location_id=None):
    """Не используем оставленную модель на старой подлокации после ухода группы."""
    group=next((row for row in WorldGroup.query.filter_by(lobby_id=character.lobby_id).order_by(WorldGroup.id).all()
        if character.id in {int(v) for v in row.member_character_ids or [] if str(v).isdigit()}),None)
    models=LocationCharacter.query.filter_by(character_id=character.id).order_by(LocationCharacter.last_action.desc(),LocationCharacter.id.desc()).all()
    if location_id:
        model=next((row for row in models if row.location_id==location_id),None)
        if not model:raise ValidationError('Персонаж отсутствует на подлокации')
        return model
    for model in models:
        if not group or (group.tile_x,group.tile_y)==(model.location.world_tile_x,model.location.world_tile_z):return model
    return None


def _position(character,location_id=None):
    from app.services.pda_network import _position as position
    local=_context(character,location_id)
    groups=WorldGroup.query.filter_by(lobby_id=character.lobby_id).order_by(WorldGroup.id).all()
    return position(character,local.location_id if local else None,groups,[local] if local else [])


def _host_items(lobby_id):
    """Каждый JSON-хозяин копируется один раз, включая вещи внутри ящиков и оружия."""
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby_id).all():
        root=deepcopy(character.data or {})
        if any(item.get('controllerState',{}).get('kind') in {'remote_detonator','timer'} for _,item in walk(root,True)):
            yield {'owner':character,'field':'data','root':root,'items':walk(root,True),'carrier':character,'local':_context(character)}
    for obj in LocationObject.query.join(LocationObject.location).filter_by(lobby_id=lobby_id).all():
        root=deepcopy(obj.properties or {})
        items=[(path[2:],item) for path,item in walk({'inventory':{'contents':root.get('contents') or []}},True)]
        # walk добавляет inventory + contents; реальный корень здесь properties.
        items=[(['contents',*path],item) for path,item in items]
        if any(item.get('controllerState',{}).get('kind') in {'remote_detonator','timer'} for _,item in items):
            yield {'owner':obj,'field':'properties','root':root,'items':items,'carrier':None,'local':obj}
    if inspect(db.session.connection()).has_table('location_traps'):
        for trap in LocationTrap.query.join(LocationTrap.location).filter_by(lobby_id=lobby_id).filter(LocationTrap.status.in_(['armed','disarmed'])).all():
            root=deepcopy(trap.data or {});item=root.get('item')
            if item and item.get('controllerState',{}).get('kind') in {'remote_detonator','timer'}:
                yield {'owner':trap,'field':'data','root':root,'items':[(['item'],item)],'carrier':None,'local':trap}


def _save(host):
    setattr(host['owner'],host['field'],host['root']);flag_modified(host['owner'],host['field'])


def _remove(host,path):
    parent=host['root']
    for part in path[:-1]:parent=parent[part]
    key=path[-1]
    if isinstance(parent,list):parent.pop(key)
    else:parent[key]=None
    if isinstance(host['owner'],LocationTrap):host['owner'].status='spent';host['owner'].detection_dc=None
    _save(host)
    if isinstance(host['owner'],LocationObject) and (host['owner'].type=='ground_item' or host['root'].get('is_ground_item')) and not host['root'].get('contents'):
        db.session.delete(host['owner']);host['deleted']=True


def _find(lobby_id,attachment_id):
    found=[(host,path,item) for host in _host_items(lobby_id) for path,item in host['items']
        if (item.get('controllerState') or {}).get('id')==attachment_id]
    if len(found)!=1:raise ConflictError('Заряд уже сработал или перемещён')
    return found[0]


def _receiver(host):
    local=host['local']
    if host['carrier']:return _position(host['carrier'])
    if not local:return None
    from app.services.location_navigation import ground
    loc=local.location
    x,y=(local.x,local.y) if isinstance(local,LocationTrap) else (local.tile_x,local.tile_y)
    level=local.level if isinstance(local,LocationTrap) else (local.properties or {}).get('floor_level',0)
    return {'world_x':(loc.world_tile_x+.5)*100-loc.grid_width/2+x+.5,
        'world_y':(loc.world_tile_z+.5)*100-loc.grid_height/2+y+.5,'height':ground(loc,x,y)+level*2.4,
        'tile_x':loc.world_tile_x,'tile_y':loc.world_tile_z,'location_id':loc.id,'x':x,'y':y,'level':level}


def _radio(character,pda,host,location_id=None,*,source_position=None,devices=None):
    from app.services.pda_network import registry,blocked,distance,mods
    source=source_position if source_position is not None else _position(character,location_id);target=_receiver(host)
    if not source or not target:return False
    devices=devices if devices is not None else registry(character.lobby_id,location_id)
    sender={'character':character,'item':pda,'position':source,'mods':mods(pda)}
    receiver={'character':character,'item':{},'position':target,'mods':set()}
    return not blocked(sender,devices) and not blocked(receiver,devices) and distance(source,target)<=250+1e-7


def snapshot(character,user_id,payload):
    from app.services.character import CharacterService
    CharacterService.check_access(character,user_id,edit=True)
    if not enabled():raise ValidationError('Управление взрывателями пока недоступно. Обновите сервер.')
    if payload.get('item_path') is None:
        from app.services.electronics import switchable
        items=[(path,item) for path,item in walk(character.data or {},True) if path[0] in {'inventory','equipment'}]
        def entry(path,item):return {'path':path,'id':item.get('id'),'name':item.get('name'),'kind':kind(item,character.lobby_id),
            'controller':deepcopy(item.get('controllerState') or {}),'explosive':bool(profiles(item,character.lobby_id))}
        return {'revision':character.revision,
            'controllers':[entry(path,item) for path,item in items if kind(item,character.lobby_id)],
            'pdas':[entry(path,item) for path,item in items if definition(item)=='pda' and item.get('quantity',1)==1],
            'charges':[entry(path,item) for path,item in items if profiles(item,character.lobby_id)],
            'devices':[entry(path,item) for path,item in items if switchable(item) and not kind(item,character.lobby_id)]}
    pda=_get(character.data or {},payload.get('item_path'),payload.get('item_id'))
    if definition(pda)!='pda':raise ValidationError('Выберите КПК')
    power=pda_state(pda)
    if not power['on'] or power['charge_percent']<=0:raise ValidationError('Нужен включённый исправный КПК')
    result=[]
    for host in _host_items(character.lobby_id):
        for path,item in host['items']:
            state=item.get('controllerState') or {}
            if state.get('kind')=='remote_detonator' and state.get('paired_pda_id')==pda.get('id'):
                result.append({'id':state['id'],'label':state['label'],'armed':state.get('armed',False),
                    'reachable':_radio(character,pda,host,payload.get('location_id'))})
    return {'charges':result}


def prepare(actor,data,payload,user_id):
    from app.services.combat import CombatService
    if not enabled():raise ValidationError('Управление взрывателями пока недоступно. Обновите сервер.')
    if not isinstance(payload,dict):raise ValidationError('Выберите действие взрывателя')
    operation=payload.get('operation');cost={'install':5,'disarm':3,'detonate':1,'bind':0,'timer':3,'activate':3}.get(operation)
    if cost is None:raise ValidationError('Неизвестное действие взрывателя')
    character=actor.character
    if operation!='disarm':
        from app.services.artifact_effects import require_device_available
        require_device_available(data,{'name':'Электронное устройство управления','attributes':{'device_kind':'timer'}})
    if operation=='disarm' and payload.get('attachment_id'):
        host,path,item=_find(character.lobby_id,payload['attachment_id']);local=host['local']
        if host['carrier']:
            raise ValidationError('Выберите взрыватель в собственном инвентаре')
        if not getattr(actor,'location_id',None) or local.location_id!=actor.location_id:raise ValidationError('Подойдите к заряду')
        position=_receiver(host)
        if position['level']!=(actor.floor_level or 0) or math.hypot(position['x']-actor.pos_x,position['y']-actor.pos_y)>1.5+1e-7:raise ValidationError('Подойдите к заряду на том же этаже')
        if isinstance(local,LocationTrap):
            from app.services.traps import visible
            if not visible(local,user_id):raise NotFoundError('Ловушка не обнаружена')
        elif (local.properties or {}).get('locked') or (local.properties or {}).get('gm_hidden'):raise ValidationError('Нет доступа к содержимому')
        if not item['controllerState'].get('armed'):raise ValidationError('Провод уже отсоединён')
        if not CombatService._has_usable_free_hand(actor):raise ValidationError('Нужна свободная действующая рука')
        return {'operation':operation,'action_points':cost,'host':host,'path':path,'item':item}
    if operation=='detonate':
        pda=held_pda(data)
        if not pda:raise ValidationError('Возьмите привязанный КПК в руку')
        power=pda_state(pda)
        if not power['on'] or power['charge_percent']<=0:raise ValidationError('КПК выключен, разряжен или неисправен')
        host,path,item=_find(character.lobby_id,payload.get('attachment_id'));state=item['controllerState']
        if state.get('kind')!='remote_detonator' or state.get('paired_pda_id')!=pda.get('id'):raise ValidationError('Взрыватель не привязан к этому КПК')
        if not state.get('armed'):raise ValidationError('Провод взрывателя отсоединён')
        if not _radio(character,pda,host,getattr(actor,'location_id',None)):raise ValidationError('Нет связи с зарядом: дальность до 250 м, возможны помехи')
        return {'operation':operation,'action_points':cost,'host':host,'path':path,'item':item}
    item=_get(data,payload.get('item_path'),payload.get('item_id'))
    if operation=='bind':
        if kind(item,character.lobby_id)!='remote_detonator':raise ValidationError('Выберите дистанционный взрыватель')
        pda=_get(data,payload.get('pda_path'),payload.get('pda_id'))
        if definition(pda)!='pda' or pda.get('quantity',1)!=1:raise ValidationError('Выберите один физический КПК')
    elif operation=='install':
        from app.services.electronics import switchable
        is_explosive=bool(profiles(item,character.lobby_id))
        if not is_explosive and (kind(item,character.lobby_id) or not switchable(item)):raise ValidationError('Выберите взрывчатку, гранату или включаемый прибор')
        if item.get('controllerState',{}).get('kind') in {'remote_detonator','timer'}:raise ValidationError('Взрыватель уже установлен')
        controller=_get(data,payload.get('controller_path'),payload.get('controller_id'));controller_kind=kind(controller,character.lobby_id)
        if not controller_kind:raise ValidationError('Выберите дистанционный взрыватель или таймер')
        if controller_kind=='remote_detonator' and not is_explosive:raise ValidationError('Дистанционный взрыватель устанавливается на взрывчатку')
        if controller_kind=='remote_detonator' and not (controller.get('controllerState') or {}).get('paired_pda_id'):
            raise ValidationError('Сначала бесплатно привяжите взрыватель к физическому КПК')
        if controller_kind=='timer':_seconds(payload);_mode(item,character.lobby_id,payload)
        if not is_explosive:cost=3
    else:
        state=item.get('controllerState') or {}
        if state.get('kind') not in {'remote_detonator','timer'}:raise ValidationError('На предмете нет взрывателя')
        if operation=='disarm' and not state.get('armed'):raise ValidationError('Провод уже отсоединён')
        if operation=='timer':
            if state.get('kind')!='timer':raise ValidationError('На заряде нет таймера')
            _seconds(payload);_mode(item,character.lobby_id,payload)
        if operation=='activate':
            if state.get('kind')!='timer':raise ValidationError('На предмете нет таймера')
            if state.get('disconnected') or state.get('mode','explode')=='explode' and not state.get('armed'):raise ValidationError('Провод отсоединён')
            desired=_mode(item,character.lobby_id,{'mode':payload.get('mode',state.get('mode'))})
            if desired in {'on','off'}:
                from app.services.electronics import switch
                switch(deepcopy(item),deepcopy(data),desired=='on')
    if getattr(actor,'location_id',None) and not CombatService._has_usable_free_hand(actor):raise ValidationError('Для работы нужна свободная действующая рука')
    if operation=='install':
        from app.services.inventory_access import calculate_inventory_access
        cost+=calculate_inventory_access(data,payload['item_path'],item)['retrieval_action_points']
        cost+=calculate_inventory_access(data,payload['controller_path'],controller)['retrieval_action_points']
    return {'operation':operation,'action_points':cost,'item':item,**({'mode':desired} if operation=='activate' else {})}


def _seconds(payload):
    value=payload.get('seconds')
    if type(value) is not int or not 1<=value<=86400:raise ValidationError('Таймер: от 1 секунды до 24 часов')
    return value


def _mode(item,lobby_id,payload):
    if profiles(item,lobby_id):return 'explode'
    mode=payload.get('mode')
    if mode not in {'on','off'}:raise ValidationError('Выберите включение или выключение прибора')
    return mode


def _single(data,path):
    from app.services.magazine import inventory_slot
    item=_get(data,path);parent,index=inventory_slot(data,path)
    quantity=item.get('quantity',1)
    if type(quantity) is not int or quantity<1:raise ValidationError('Нет экземпляра предмета')
    if quantity==1:item.setdefault('id',str(uuid4()));return item
    item['quantity']-=1;single=deepcopy(item);single.update(id=str(uuid4()),quantity=1);parent.insert(index+1,single)
    return single


def execute(actor,data,payload,user_id,state=None):
    from app.services.combat import CombatService
    plan=prepare(actor,data,payload,user_id);operation=plan['operation'];item=plan['item']
    result={'name':LABELS[operation],'action_points':plan['action_points'],'roll_required':False}
    if operation in {'detonate','activate'}:
        if operation=='activate':item['controllerState']['mode']=plan['mode']
        # Снимок листа исполнителя сначала становится текущим: взрыв может поразить его самого.
        actor.character.data=data
        result.update(fire(actor.character.lobby_id,payload['attachment_id'] if operation=='detonate' else item['controllerState']['id']))
        live=deepcopy(actor.character.data);data.clear();data.update(live)
    elif operation=='bind':
        pda=_get(data,payload['pda_path'],payload.get('pda_id'));pda.setdefault('id',str(uuid4()))
        item['controllerState']={'paired_pda_id':pda['id']}
    elif operation=='install':
        # Сначала разделяем заряд, затем заново находим контроллер: индексы могли сдвинуться.
        controller=deepcopy(_get(data,payload['controller_path'],payload.get('controller_id')))
        from app.services.magazine import inventory_slot
        parent,index=inventory_slot(data,payload['controller_path'])
        if parent[index].get('quantity',1)>1:parent[index]['quantity']-=1
        else:parent.pop(index)
        # Исходный заряд находился в том же списке после контроллера — ищем его по физическому ID.
        path=next((path for path,value in walk(data,True) if value is item),None)
        if path is None:raise ConflictError('Заряд перемещён')
        single=_single(data,path);controller['quantity']=1;controller['id']=str(uuid4())
        controller_kind=kind(controller,actor.character.lobby_id)
        single['controllerState']={'id':str(uuid4()),'kind':controller_kind,'controller':controller,
            'paired_pda_id':(controller.get('controllerState') or {}).get('paired_pda_id'),
            'source_character_id':actor.character_id,'label':str(payload.get('label') or single.get('name') or 'Заряд')[:100],
            'armed':True,**({'remaining_seconds':_seconds(payload),'mode':_mode(single,actor.character.lobby_id,payload)} if controller_kind=='timer' else {})}
        from app.services.item_template_lookup import resolve_item_template
        base=resolve_item_template(single,actor.character.lobby_id)
        for field,default in [('weight',0),('volume',0),('price',0)]:
            single[field]=float(single.get(field,getattr(base,field,default)) or 0)+float(controller.get(field,1000 if field=='price' else .1) or 0)
        result['attachment_id']=single['controllerState']['id']
    elif operation=='disarm':
        item['controllerState'].update(armed=False,disconnected=True)
        if plan.get('host'):
            host=plan['host'];_save(host)
            result.update(location_id=host['local'].location_id)
            if isinstance(host['owner'],LocationObject):result['object_id']=host['owner'].id
            elif isinstance(host['owner'],LocationTrap):result['trap_ids']=[host['owner'].id]
    else:
        item['controllerState'].update(armed=True,disconnected=False,remaining_seconds=_seconds(payload),mode=_mode(item,actor.character.lobby_id,payload))
        item['controllerState'].pop('error',None)
    return result


def fire(lobby_id,attachment_id):
    from app.services.combat import CombatService
    from app.services.crafting import now
    if DetonationEvent.query.filter(DetonationEvent.id.like(attachment_id+':%')).first():return {'fired':True,'replayed':True}
    host,path,item=_find(lobby_id,attachment_id);saved=item['controllerState']
    if saved.get('kind')=='timer' and saved.get('mode') in {'on','off'}:
        from app.services.electronics import switch
        saved['armed']=False;saved['remaining_seconds']=0;saved.pop('error',None)
        try:switch(item,host['root'] if host['carrier'] else {},saved['mode']=='on')
        except ValidationError as error:saved['error']=str(error)
        _save(host)
        position=_receiver(host)
        return {'switched':not saved.get('error'),'error':saved.get('error'),'device_name':item.get('name'),
            'affected_character_ids':[host['carrier'].id] if host['carrier'] else [],
            'location_id':position['location_id'] if position else None,
            'object_id':host['owner'].id if isinstance(host['owner'],LocationObject) else None}
    if not saved.get('armed'):raise ValidationError('Взрыватель обезврежен')
    blast_profiles=saved.get('explosive_profiles') or profiles(item,lobby_id)
    if not blast_profiles:raise ValidationError('У заряда нет рабочего эффекта')
    position=_receiver(host);carrier=host['carrier'];local=host['local']
    _remove(host,path);db.session.flush()
    results=[];affected=set();events=[]
    for index,profile in enumerate(blast_profiles):
        profile=deepcopy(profile);profile['source_character_id']=saved.get('source_character_id')
        event=DetonationEvent(id=f'{attachment_id}:{index}',lobby_id=lobby_id,
            location_id=position['location_id'] if position else None,profile=profile,results=[],affected_character_ids=[],
            carrier_id=carrier.id if carrier else None,source_character_id=saved.get('source_character_id'),
            game_time=now(carrier) if carrier else ((local.location.lobby.game_day or 1)*1440+local.location.lobby.game_time_minutes)*60,
            closed=bool(position and position['location_id']))
        db.session.add(event);db.session.flush()
        if position and position['location_id']:
            combat=CombatService._get_or_create_state(position['location_id'])
            explosion=CombatService.resolve_explosion(position['location_id'],position['x'],position['y'],profile,
                floor_level=position['level'],round_number=combat.round_number if combat else 0)
            area=explosion.get('area')
            if area and combat:combat.area_effects=[*(combat.area_effects or []),area]
            results.append(explosion)
            affected.update(row.character_id for row in LocationCharacter.query.filter_by(location_id=position['location_id']).all())
            event.results=[explosion]
        elif carrier:
            from app.services.world_detonations import apply_to_character
            explosion=apply_to_character(carrier,profile)
            event.results=[{'character_id':carrier.id,'name':carrier.name,'result':explosion}];event.affected_character_ids=[carrier.id];affected.add(carrier.id)
        events.append(event.id)
    return {'fired':True,'event_ids':events,'explosions':results,'affected_character_ids':sorted(affected),
        'location_id':position['location_id'] if position else None,
        'trap_ids':[host['owner'].id] if isinstance(host['owner'],LocationTrap) else [],
        'object_id':host['owner'].id if isinstance(host['owner'],LocationObject) else None,
        'ground_object_event':'deleted' if host.get('deleted') else 'updated'}


@atomic_operation
def operate(character_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if not isinstance(payload,dict):raise ValidationError('Выберите действие взрывателя')
    identity=payload.get('operation_id')
    if not isinstance(identity,str) or not 1<=len(identity)<=100:raise ValidationError('Нужен идентификатор действия')
    data=deepcopy(character.data or {});receipts=data.setdefault('crafting',{}).setdefault('controller_receipts',[])
    signature=hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k!='character_revision'},sort_keys=True).encode()).hexdigest()
    prior=next((row for row in receipts if row['id']==identity),None)
    if prior:
        if prior['signature']!=signature:raise ConflictError('Идентификатор уже использован')
        return character,prior['result']
    local=_context(character,payload.get('location_id'))
    active=LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
        LocationCharacter.character_id==character_id,LocationCombatState.status=='active').first()
    if active:raise ValidationError('В бою используйте боевое действие взрывателя')
    if CombatService._character_condition(character.data or {})['state']!='active':raise ValidationError('Персонаж не может действовать')
    if payload.get('character_revision')!=character.revision:raise ConflictError('Лист изменился')
    actor=local or SimpleNamespace(character=character,character_id=character.id,location_id=None)
    result=execute(actor,data,payload,user_id)
    receipts=data.setdefault('crafting',{}).setdefault('controller_receipts',[])
    receipts.append({'id':identity,'signature':signature,'result':deepcopy(result)})
    data['crafting']['controller_receipts']=receipts[-50:];character.data=data;flag_modified(character,'data')
    return character,result


def advance(lobby_id,seconds,location_id=None):
    """Время устройства идёт у всех хозяев, независимо от участия персонажа в ходе."""
    if seconds<=0 or not enabled():return []
    due=[]
    for host in _host_items(lobby_id):
        position=_receiver(host)
        if location_id and (not position or position['location_id']!=location_id):continue
        changed=False
        for path,item in host['items']:
            state=item.get('controllerState') or {}
            if state.get('kind')!='timer' or not state.get('armed'):continue
            state['remaining_seconds']=max(0,state.get('remaining_seconds',0)-seconds);changed=True
            if state['remaining_seconds']==0:due.append(state['id'])
        if changed:_save(host)
    db.session.flush()
    results=[]
    for identity in due:
        result=fire(lobby_id,identity);results.append(result);publish(lobby_id,result,after_commit=True)
    return results


def next_due(lobby_id,seconds):
    if not enabled():return seconds
    return min([seconds,*[max(.001,item['controllerState'].get('remaining_seconds',0)) for host in _host_items(lobby_id)
        for _,item in host['items'] if (item.get('controllerState') or {}).get('kind')=='timer' and item['controllerState'].get('armed')]])


def publish(lobby_id,result,*,after_commit=False):
    from app.services.traps import publish_effects,publish as publish_traps
    from app.models import Lobby
    publish_effects(result)
    if result.get('trap_ids') and result.get('location_id'):publish_traps(result['location_id'],result['trap_ids'])
    lobby=db.session.get(Lobby,lobby_id)
    if result.get('event_ids') and lobby:
        if after_commit:db.session.info.setdefault('controller_notifications',{})[lobby_id]=lobby.gm_id
        else:socketio.emit('detonation_pending',{'lobby_id':lobby_id},room=f'user_{lobby.gm_id}')
    if result.get('object_id'):
        from app.schemas.location import LocationObjectSchema
        if result.get('ground_object_event')=='deleted':
            socketio.emit('location_object_deleted',{'location_id':result['location_id'],'object_id':result['object_id']},room=f"location_{result['location_id']}")
            return
        obj=db.session.get(LocationObject,result['object_id'])
        if obj:socketio.emit('location_object_updated',{'location_id':obj.location_id,'object':LocationObjectSchema().dump(obj)},room=f'location_{obj.location_id}')
