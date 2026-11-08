"""Взлом КПК: время дешифровки и доступ только при живом соединении."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import json
import random
from sqlalchemy import inspect,event,or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db,socketio
from app.models import PdaAccessSession,LobbyCharacter,LocationCharacter,LocationCombatState
from app.services import pda_devices as devices
from app.services.character import CharacterService
from app.services.exceptions import ValidationError,ConflictError,NotFoundError,PermissionDenied
from app.services.portable_lights import walk,definition,pda_state
from app.services.transaction import atomic_operation

DURATIONS={1:7200,2:3600,3:1800}
LABELS={'start':'Начать взлом КПК','disconnect':'Отключить соединение КПК','counter':'Контратака КПК',
    'power':'Питание подключённого КПК','jammer':'Глушилка подключённого КПК','detonate':'Дистанционный подрыв через КПК',
    'wipe':'Очистка подключённого КПК','switch_profile':'Переключить профиль подключённого КПК'}


def enabled():return inspect(db.session.connection()).has_table('pda_access_sessions')


def _queue(lobby_id,alert=None):
    info=db.session.info.setdefault('pda_access_notifications',{'worlds':set(),'alerts':[]})
    info['worlds'].add(lobby_id)
    if alert:info['alerts'].append(alert)


@event.listens_for(Session,'after_commit')
def _publish(session):
    if session.in_nested_transaction():return
    info=session.info.pop('pda_access_notifications',{})
    for world in info.get('worlds',[]):socketio.emit('pda_access_updated',{'lobby_id':world},room=f'lobby_{world}')
    for alert in info.get('alerts',[]):
        for user_id in alert['users']:socketio.emit('pda_access_alert',alert['payload'],room=f'user_{user_id}')


@event.listens_for(Session,'after_rollback')
def _rollback(session):session.info.pop('pda_access_notifications',None)


def _record(records,identity):
    values=[record for record in records if record['id']==identity]
    return values[0] if len(values)==1 and not values[0]['ambiguous'] else None


def _readers(row):
    readers=set(row.data.get('reading_character_ids') or [])
    if row.source_character_id:readers.add(row.source_character_id)
    return LobbyCharacter.query.filter(LobbyCharacter.lobby_id==row.lobby_id,LobbyCharacter.id.in_(readers)).all() if readers else []


def _cancel_reading(character,row,reason):
    data=character.data or {};crafting=data.get('crafting') or {};job=crafting.get('current') or {}
    if job.get('kind')!='book' or (job.get('remote_source') or {}).get('session_id')!=row.id:return
    result={'job_id':job['id'],'kind':'book','name':job['name'],'cancelled':True,'success':False,
        'roll':None,'difficulty':0,'lost':[],'lost_items':[],'output_count':0,'reason':reason}
    crafting.pop('current');crafting['history']=[*(crafting.get('history') or []),result][-30:]
    from app.services.action_journal import append_work
    append_work(data,'remote-book-lost:'+job['id'],'cancel',result);flag_modified(character,'data')


def _close(row,reason):
    if row.status=='closed':return
    row.status='closed';row.data={**row.data,'closed_reason':reason};_queue(row.lobby_id)
    for character in _readers(row):_cancel_reading(character,row,reason)


def connection_problem(row,records):
    source=_record(records,row.source_device_id);target=_record(records,row.target_device_id)
    if not source or not target:return 'Устройство перемещено, удалено или не определено однозначно'
    if not devices.powered(source) or not devices.powered(target):return 'КПК выключен, разряжен или неисправен'
    if devices.decipher(source)<max(1,devices.cipher(target)):return 'Нет подходящего дешифратора'
    required='pda-b18' if row.mode=='physical' else 'pda-b25'
    if required not in source['mods']:return 'Нет программы выбранного способа взлома'
    if row.mode=='physical':
        if source['host']!=row.data.get('source_host') or target['host']!=row.data.get('target_host'):return 'Кабель отключён при передаче устройства'
        if not devices.cable_near(source,target):return 'Кабельное соединение разорвано'
    elif 'pda-b9' in target['mods'] or not devices.reaches(source,target,records) or not devices.reaches(target,source,records):
        return 'Потеряна двусторонняя радиосвязь'
    return None


def invalidate_connections(lobby_id,records=None):
    if not enabled():return []
    rows=PdaAccessSession.query.filter_by(lobby_id=lobby_id).filter(PdaAccessSession.status.in_(['working','active'])).order_by(PdaAccessSession.id).all()
    if not rows:return []
    records=records if records is not None else devices.registry(lobby_id);closed=[]
    for row in rows:
        if row.status=='closed':continue
        reason=connection_problem(row,records)
        if reason:_close(row,reason);closed.append(row.id)
        elif row.data.get('reading_character_ids'):
            from app.services.exceptions import ServiceError
            for character in _readers(row):
                job=(character.data.get('crafting') or {}).get('current') or {}
                if job.get('kind')!='book' or (job.get('remote_source') or {}).get('session_id')!=row.id:continue
                try:readable_book(character,character.data,job['remote_source'],job['book_id'])
                except ServiceError:_cancel_reading(character,row,'Файл или исходный КПК больше не доступен читателю')
    return closed


def check_route(actor,path):
    """Провод или радиосвязь не восстанавливают старый доступ после обхода препятствия."""
    if not enabled():return
    rows=PdaAccessSession.query.filter_by(lobby_id=actor.character.lobby_id).filter(PdaAccessSession.status.in_(['working','active'])).all()
    if not rows:return
    records=devices.registry(actor.character.lobby_id)
    carried=[record for record in records if record['character'] and record['character'].id==actor.character_id
        and record['position'] and record['position']['location_id']==actor.location_id]
    if not carried:return
    from app.services.location_navigation import ground
    origin=carried[0]['position'];surfaces=path.get('surfaces') or []
    for index,(x,y) in enumerate(path.get('path') or []):
        level=surfaces[index].get('level',actor.floor_level or 0) if index<len(surfaces) else actor.floor_level or 0
        position={**origin,'x':x,'y':y,'level':level,'world_x':origin['world_x']+x-origin['x'],
            'world_y':origin['world_y']+y-origin['y'],'height':ground(actor.location,x,y)+level*2.4}
        for record in carried:record['position']=position
        for row in rows:
            if row.status=='closed':continue
            reason=connection_problem(row,records)
            if reason:_close(row,reason)


def _selected(character,data,payload,records=None):
    if not isinstance(payload,dict):raise ValidationError('Выберите КПК')
    path=payload.get('item_path');identity=payload.get('item_id')
    item=next((item for current,item in walk(data) if current==path and definition(item)=='pda'),None)
    if not item or item.get('id')!=identity or not isinstance(identity,str):raise ConflictError('КПК перемещён. Откройте его заново.')
    records=records if records is not None else devices.registry(character.lobby_id)
    source=_record(records,identity)
    if not source or source['character'] is None or source['character'].id!=character.id:raise ConflictError('Нельзя однозначно определить свой КПК')
    return source,records


def _physical_access(source,target,user_id):
    if not devices.cable_near(source,target):return False
    if source['host']==target['host']:return True
    if target['character']:
        try:CharacterService.check_access(target['character'],user_id,edit=True)
        except PermissionDenied:return False
        return True
    props=target['owner'].properties or {}
    return not props.get('locked') and not props.get('gm_hidden')


def project(row):
    return {'id':row.id,'revision':row.revision,'mode':row.mode,'status':row.status,
        'remaining_seconds':max(0,row.remaining_seconds),'name':row.data.get('target_label','КПК'),
        'closed_reason':row.data.get('closed_reason'),'notified':row.notified}


def _active_target(source,records,payload):
    row=PdaAccessSession.query.filter_by(id=payload.get('session_id'),lobby_id=source['lobby_id']).with_for_update().first()
    if not row or row.source_device_id!=source['id']:raise NotFoundError('Подключение не найдено на этом КПК')
    if row.status!='active':raise ConflictError('Доступ ещё не получен или соединение разорвано')
    reason=connection_problem(row,records)
    if reason:raise ConflictError(reason)
    return row,_record(records,row.target_device_id)


def _radio_sources(records):
    from app.services.pda_network import signal_id
    result=[]
    for record in records:
        if record['ambiguous'] or not devices.powered(record) or not record['position']:continue
        source=devices.radio_device(record)
        source.update(account=devices.account(record),signal_id=signal_id(source['character'],record['path'],record['item']))
        result.append(source)
    return result


def _charge_reaches(target,host,records):
    from app.services.item_controllers import _radio
    return _radio(devices.radio_device(target)['character'],target['item'],host,
        source_position=target['position'],devices=_radio_sources(records)) if target['position'] else False


def readable_book(character,data,remote,book_id,*,register=False):
    """Возвращает единственный файл на целевом КПК, не переносит и не копирует его."""
    if not isinstance(remote,dict):raise ValidationError('Выберите подключённый КПК')
    source_id=remote.get('source_id')
    matches=[(path,item) for path,item in walk(data) if definition(item)=='pda' and item.get('id')==source_id]
    if len(matches)!=1:raise ConflictError('Исходный КПК больше не доступен читателю')
    source,records=_selected(character,data,{'item_path':matches[0][0],'item_id':source_id})
    row,target=_active_target(source,records,remote)
    books=[item for _,item in walk({'inventory':{'device':target['item']}},True)
        if item is not target['item'] and item.get('id')==book_id and isinstance(book_id,str)]
    if len(books)!=1 or books[0].get('quantity',1)!=1:raise ConflictError('Файл книги перемещён или удалён')
    if register:row.data={**row.data,'reading_character_ids':sorted({*(row.data.get('reading_character_ids') or []),character.id})}
    return books[0]


@atomic_operation
def target_snapshot(character_id,user_id,payload):
    from app.services.pda_identity import display
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    source,records=_selected(character,character.data or {},payload)
    row,target=_active_target(source,records,payload)
    account=devices.account(target);item=target['item']
    from app.services.pda_network import snapshot_source
    from app.services.item_controllers import _host_items
    radios=_radio_sources(records);radio=next((entry for entry in radios if entry['id']==target['id']),None)
    charges=[]
    for host in _host_items(character.lobby_id):
        for _,charge in host['items']:
            state=charge.get('controllerState') or {}
            if state.get('kind')=='remote_detonator' and state.get('paired_pda_id')==target['id']:
                charges.append({'id':state['id'],'label':state.get('label','Заряд'),'armed':bool(state.get('armed')),
                    'reachable':_charge_reaches(target,host,records)})
    from app.services.item_template_lookup import resolve_item_template
    files=[]
    for _,entry in walk({'inventory':{'device':item}},True):
        if entry is item:continue
        template=resolve_item_template(entry,character.lobby_id)
        attrs=(template.attributes or {}) if template else entry.get('attributes') or {}
        if attrs.get('book_kind') in {'skill','equipment'}:
            files.append({'id':entry.get('id'),'name':template.name if template else entry.get('name'),'attributes':deepcopy(attrs)})
    from app.services.pda_profiles import public,file_profile
    for file in files:file['profile']=file_profile(item,file['id'])
    return {'session':project(row),**public(item),'device':{'name':item.get('name','КПК'),'power':pda_state(item),
        'modifications':[entry.get('name') or entry.get('ruleId') for entry in item.get('modifications') or []],
        'has_jammer':'pda-b16' in target['mods'],'has_wipe':'pda-b14' in target['mods'],
        'jammer_on':bool((item.get('pdaNetwork') or {}).get('jammer_on'))},
        'account':{'id':account.id,'login':display(item,account),'notes':account.notes or ''} if account else None,
        'network':snapshot_source(radio,radios,masked_account=True) if radio else None,'files':files,'charges':charges}


@atomic_operation
def target_action(character_id,user_id,payload):
    """Программные операции не предоставляют права на инвентарь носителя."""
    from app.services.combat import CombatService
    from app.services.pda_accounts import authenticate
    from app.services.pda_network import operate_source
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if CombatService._character_condition(character.data or {})['state']!='active':raise ValidationError('Персонаж не может пользоваться КПК')
    source,records=_selected(character,character.data or {},payload)
    row,target=_active_target(source,records,payload)
    identity=payload.get('operation_id')
    if not isinstance(identity,str) or not 1<=len(identity)<=100:raise ValidationError('Нужен идентификатор действия')
    signature=_signature(payload);prior=next((receipt for receipt in row.receipts or [] if receipt['id']==identity),None)
    if prior:
        if prior['signature']!=signature:raise ConflictError('Идентификатор действия уже использован')
        result=deepcopy(prior['result'])
        if result.get('operation')=='sender_name':result.update(replayed=True,action_points=0)
        return character,result
    operation=payload.get('operation');account=devices.account(target);result={'operation':operation}
    if operation in {'login','register','logout'}:
        account=None if operation=='logout' else authenticate(character.lobby_id,operation,payload)
        if target['character']:
            from app.services.pda_accounts import _assign
            _assign(target['character'],target['item'],account)
        else:
            from app.services.pda_profiles import assign
            assign(target['item'],account.id if account else None);devices.save(target)
    elif operation=='save_notes':
        if not account:raise ValidationError('Сначала войдите в аккаунт подключённого КПК')
        notes=payload.get('notes')
        if not isinstance(notes,str) or len(notes)>10000:raise ValidationError('Заметки должны содержать не более 10 000 символов')
        account.notes=notes
    elif operation=='proximity_sound':
        if 'pda-b8' not in target['mods'] or type(payload.get('enabled')) is not bool:raise ValidationError('Недоступна настройка звука детектора')
        target['item'].setdefault('pdaNetwork',{})['proximity_sound']=payload['enabled'];devices.save(target)
    elif operation in {'send','contact','remove_contact','spam','sender_name'}:
        radios=_radio_sources(records);radio=next((entry for entry in radios if entry['id']==target['id']),None)
        if not radio:raise ValidationError('Нет положения подключённого КПК на карте')
        result.update(operate_source(radio,radios,payload))
        if operation=='spam':
            from app.services.pda_spam import charge_start
            result['action_points']=charge_start(character,user_id,source['id'],payload,result)
        elif operation=='sender_name':
            from app.services.pda_action_cost import charge
            result['action_points']=charge(character,user_id,source['id'],payload,result,'Замена имени') if result['changed'] else 0
    else:raise ValidationError('Неизвестное программное действие КПК')
    result['session']=project(row)
    row.receipts=[*(row.receipts or []),{'id':identity,'signature':signature,'result':deepcopy(result)}][-50:]
    _queue(character.lobby_id)
    return character,result


def publish(lobby_id,result):
    from app.services.pda_wipe import publish as publish_wipe
    publish_wipe(lobby_id)
    from app.services.item_controllers import publish as publish_controller
    from app.services.character_events import emit_character_update
    publish_controller(lobby_id,result)
    # Обновления листа отправляются существующим частным механизмом прав доступа.
    row=db.session.get(PdaAccessSession,(result.get('session') or {}).get('id'))
    identities={row.source_device_id,row.target_device_id} if row and row.lobby_id==lobby_id else set()
    characters={record['character'].id:record['character'] for record in devices.registry(lobby_id) if record['id'] in identities and record['character']}
    for character in characters.values():
        emit_character_update({'character_id':character.id,'updates':{'data':character.data_snapshot()},'source':'pda_access'})
    socketio.emit('pda_network_updated',{'lobby_id':lobby_id},room=f'lobby_{lobby_id}')


@atomic_operation
def snapshot(character_id,user_id,payload):
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if not enabled():raise ValidationError('Сеансы КПК пока недоступны. Обновите сервер.')
    source,records=_selected(character,character.data or {},payload)
    invalidate_connections(character.lobby_id,records)
    targets=[]
    if devices.powered(source):
        for target in records:
            if target['id']==source['id'] or target['ambiguous'] or not devices.powered(target):continue
            methods=[]
            if 'pda-b18' in source['mods'] and _physical_access(source,target,user_id):methods.append('physical')
            if 'pda-b25' in source['mods'] and 'pda-b9' not in target['mods'] and devices.reaches(target,source,records) and devices.reaches(source,target,records):methods.append('remote')
            if methods:targets.append({'token':target['token'],'name':devices.label(target),'methods':methods})
    own=[];incoming=[]
    rows=PdaAccessSession.query.filter_by(lobby_id=character.lobby_id).filter(
        or_(PdaAccessSession.source_device_id==source['id'],PdaAccessSession.target_device_id==source['id'])).order_by(PdaAccessSession.created_at.desc()).limit(30).all()
    for row in rows:
        if row.source_device_id==source['id']:own.append(project(row))
        elif row.mode=='physical' or row.notified or character.lobby.gm_id==user_id:incoming.append(project(row))
    from app.services.pda_network import blocked
    radio_blocked=blocked(devices.radio_device(source),_radio_sources(records)) if source['position'] else 'Нет положения КПК на карте'
    return {'source':{'decipher':devices.decipher(source),'cipher':devices.cipher(source),'blocked':radio_blocked,
        'physical':'pda-b18' in source['mods'],'remote':'pda-b25' in source['mods'],'counter':'pda-b24' in source['mods']},
        'duration_seconds':DURATIONS.get(devices.decipher(source)),'targets':targets,'sessions':own,'incoming':incoming}


def prepare(actor,data,payload,user_id):
    if not enabled():raise ValidationError('Сеансы КПК пока недоступны. Обновите сервер.')
    source,records=_selected(actor.character,data,payload)
    if not devices.powered(source):raise ValidationError('Включите исправный заряженный КПК')
    operation=payload.get('operation')
    if operation not in LABELS:raise ValidationError('Неизвестное действие взлома КПК')
    if operation in {'power','jammer','detonate','wipe','switch_profile'}:
        row,target=_active_target(source,records,payload)
        if operation=='wipe':
            from app.services.pda_wipe import validate
            validate(target['item'])
        elif operation=='switch_profile':
            from app.services.pda_profiles import enabled as has_profiles,PROFILES
            if not has_profiles(target['item']) or payload.get('profile') not in PROFILES:raise ValidationError('Выберите доступный профиль КПК')
        elif operation in {'power','jammer'}:
            if type(payload.get('enabled')) is not bool:raise ValidationError('Выберите состояние устройства')
            if operation=='jammer' and 'pda-b16' not in target['mods']:raise ValidationError('На КПК нет глушилки')
        else:
            from app.services.portable_lights import held_pda
            from app.services.item_controllers import _find
            if (held_pda(data) or {}).get('id')!=source['id']:raise ValidationError('Возьмите свой включённый КПК в руку')
            host,_,item=_find(actor.character.lobby_id,payload.get('attachment_id'));state=item['controllerState']
            if state.get('kind')!='remote_detonator' or state.get('paired_pda_id')!=target['id']:raise ValidationError('Заряд не привязан к подключённому КПК')
            if not state.get('armed'):raise ValidationError('Провод взрывателя отсоединён')
            if not _charge_reaches(target,host,records):raise ValidationError('Нет связи от подключённого КПК до заряда: дальность 250 м')
        cost=3 if operation in {'wipe','switch_profile'} else 1
        if operation=='switch_profile':
            from app.services.pda_profiles import active
            if active(target['item'])==payload['profile']:cost=0
        if operation in {'power','jammer'}:
            current=pda_state(target['item'])['on'] if operation=='power' else bool((target['item'].get('pdaNetwork') or {}).get('jammer_on'))
            cost=int(current!=payload['enabled'])
        return {'operation':operation,'source':source,'target':target,'records':records,'session':row,'action_points':cost}
    if operation=='start':
        mode=payload.get('mode')
        if mode not in {'physical','remote'}:raise ValidationError('Выберите физическое или дистанционное подключение')
        target=next((record for record in records if record['token']==payload.get('target_token') and not record['ambiguous']),None)
        if not target or target['id']==source['id']:raise NotFoundError('Целевой КПК недоступен')
        if not devices.powered(target):raise ValidationError('Целевой КПК выключен, разряжен или неисправен')
        required='pda-b18' if mode=='physical' else 'pda-b25'
        if required not in source['mods']:raise ValidationError('На КПК нет программы выбранного способа взлома')
        level=devices.decipher(source)
        if not level or level<devices.cipher(target):raise ValidationError('Нужен дешифратор соответствующего или более высокого порядка')
        if mode=='physical' and not _physical_access(source,target,user_id):raise ValidationError('Нет физического доступа к КПК и его проводам')
        if mode=='remote' and ('pda-b9' in target['mods'] or not devices.reaches(source,target,records) or not devices.reaches(target,source,records)):
            raise ValidationError('Нет известного сигнала и двусторонней радиосвязи')
        existing=PdaAccessSession.query.filter_by(lobby_id=actor.character.lobby_id,source_device_id=source['id'],target_device_id=target['id']).filter(PdaAccessSession.status.in_(['working','active'])).first()
        if existing:raise ConflictError('Подключение к этому устройству уже существует')
        return {'operation':operation,'source':source,'target':target,'records':records,'mode':mode,'duration':DURATIONS[level],'action_points':3}
    row=PdaAccessSession.query.filter_by(id=payload.get('session_id'),lobby_id=actor.character.lobby_id).with_for_update().first()
    if not row or source['id'] not in {row.source_device_id,row.target_device_id}:raise NotFoundError('Подключение не найдено на этом КПК')
    if row.status=='closed':raise ConflictError('Соединение уже разорвано')
    reason=connection_problem(row,records)
    if reason:raise ConflictError(reason)
    if operation=='counter':
        if source['id']!=row.target_device_id or row.mode!='remote' or row.status!='active' or 'pda-b24' not in source['mods']:
            raise ValidationError('Контратака доступна «Убийце взломщиков» против действующего удалённого доступа')
    return {'operation':operation,'source':source,'records':records,'session':row,'action_points':3}


def execute(actor,data,payload,user_id,state=None):
    actor.character.data=data
    plan=prepare(actor,data,payload,user_id);operation=plan['operation']
    result={'name':LABELS[operation],'action_points':plan['action_points'],'roll_required':False}
    if operation=='start':
        from app.services.crafting import now
        source,target=plan['source'],plan['target'];identity=payload.get('operation_id') or str(uuid4())
        row=PdaAccessSession(id=identity,lobby_id=actor.character.lobby_id,source_character_id=actor.character.id,
            source_device_id=source['id'],target_device_id=target['id'],mode=plan['mode'],status='working',
            remaining_seconds=plan['duration'],created_game_time=now(actor.character),
            data={'source_host':source['host'],'target_host':target['host'],'target_label':devices.label(target)})
        # Версия носителя сериализует одновременные попытки начать одинаковый сеанс.
        flag_modified(actor.character,'data')
        db.session.add(row);db.session.flush();result.update(session=project(row))
    elif operation=='disconnect':
        row=plan['session'];_close(row,'Соединение отключено');result.update(session=project(row))
    elif operation=='counter':
        row=plan['session'];roll=random.randint(1,100);success=roll<=25
        if success:
            attacker=_record(plan['records'],row.source_device_id)
            power=pda_state(attacker['item']);power.update(on=False,broken=True);attacker['item']['pdaState']=power;devices.save(attacker)
            _close(row,'КПК взломщика выведен из строя контратакой')
        result.update(roll=roll,success=success,session=project(row))
    else:
        row=plan['session'];target=plan['target']
        if operation=='power':
            from app.services.electronics import switch
            switch(target['item'],target['character'].data if target['character'] else {},payload['enabled']);devices.save(target)
        elif operation=='switch_profile':
            from app.services.pda_profiles import switch
            if target['character']:
                from app.services.pda_accounts import _assign
                _assign(target['character'],target['item'],devices.account(target))
            switch(target['item'],payload['profile']);devices.save(target)
        elif operation=='wipe':
            from app.services.pda_wipe import erase
            result.update(erase(actor.character.lobby_id,target['item']))
        elif operation=='jammer':
            target['item'].setdefault('pdaNetwork',{})['jammer_on']=payload['enabled'];devices.save(target)
        else:
            from app.services.item_controllers import fire
            result.update(fire(actor.character.lobby_id,payload['attachment_id']))
            live=deepcopy(actor.character.data);data.clear();data.update(live)
        invalidate_connections(actor.character.lobby_id)
        result['session']=project(row)
    _queue(actor.character.lobby_id)
    return result


def _signature(payload):return hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k!='character_revision'},sort_keys=True).encode()).hexdigest()


@atomic_operation
def operate(character_id,user_id,payload):
    from app.services.combat import CombatService
    from app.services.item_controllers import _context
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if not isinstance(payload,dict):raise ValidationError('Выберите действие с КПК')
    identity=payload.get('operation_id')
    if not isinstance(identity,str) or not 1<=len(identity)<=100:raise ValidationError('Нужен идентификатор действия')
    source,records=_selected(character,character.data or {},payload)
    signature=_signature(payload)
    if payload.get('operation')=='start':
        prior=db.session.get(PdaAccessSession,identity)
        if prior:
            if prior.lobby_id!=character.lobby_id or prior.source_device_id!=source['id'] or prior.data.get('request_signature')!=signature:raise ConflictError('Идентификатор подключения уже использован')
            return character,{'session':project(prior),'replayed':True}
    else:
        row=db.session.get(PdaAccessSession,payload.get('session_id'))
        if row and row.lobby_id==character.lobby_id and source['id'] in {row.source_device_id,row.target_device_id}:
            prior=next((r for r in row.receipts or [] if r['id']==identity),None)
            if prior:
                if prior['signature']!=signature:raise ConflictError('Идентификатор действия уже использован')
                return character,deepcopy(prior['result'])
    if LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(LocationCharacter.character_id==character_id,LocationCombatState.status=='active').first():
        raise ValidationError('В бою используйте боевое действие КПК')
    if CombatService._character_condition(character.data or {})['state']!='active':raise ValidationError('Персонаж не может пользоваться КПК')
    if payload.get('character_revision')!=character.revision:raise ConflictError('Лист изменился')
    actor=_context(character,payload.get('location_id')) or SimpleNamespace(character=character,character_id=character.id,location_id=None)
    result=execute(actor,character.data,payload,user_id)
    row=db.session.get(PdaAccessSession,result['session']['id'])
    if payload['operation']=='start':row.data={**row.data,'request_signature':signature}
    else:row.receipts=[*(row.receipts or []),{'id':identity,'signature':signature,'result':deepcopy(result)}][-50:]
    return character,result


def advance(lobby_id,seconds,location_id=None):
    if seconds<=0 or not enabled():return []
    rows=PdaAccessSession.query.filter_by(lobby_id=lobby_id,status='working').order_by(PdaAccessSession.id).all()
    if not rows:return []
    records=devices.registry(lobby_id);changed=[]
    for row in rows:
        source=_record(records,row.source_device_id)
        if location_id and (not source or not source['position'] or source['position']['location_id']!=location_id):continue
        reason=connection_problem(row,records)
        if reason:_close(row,reason);continue
        row.remaining_seconds=max(0,row.remaining_seconds-seconds);changed.append(row.id)
        if row.remaining_seconds==0:
            row.status='active';target=_record(records,row.target_device_id)
            if row.mode=='remote' and 'pda-b24' in target['mods']:
                row.notified=random.randint(1,100)<=75
                if row.notified:
                    users={target['lobby'].gm_id};holder=target['character']
                    if holder:
                        users.add(holder.owner_id);users.update(holder.editable_to or [])
                        users.update(model.controlled_by for model in LocationCharacter.query.filter_by(character_id=holder.id).all() if model.controlled_by)
                    _queue(lobby_id,{'users':sorted(users),'payload':{'lobby_id':lobby_id,'device_id':target['id'],'session_id':row.id,'message':'КПК: обнаружено удалённое подключение'}})
        _queue(lobby_id)
    return changed


def next_due(lobby_id,seconds):
    if not enabled():return seconds
    values=[max(.001,row.remaining_seconds) for row in PdaAccessSession.query.filter_by(lobby_id=lobby_id,status='working').all()]
    return min([seconds,*values])
