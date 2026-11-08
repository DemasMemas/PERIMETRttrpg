"""Направленная радиосвязь физических КПК и контакты аккаунта внутри мира."""
import hashlib
import json
import math
import unicodedata

from sqlalchemy import inspect,or_,select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LobbyCharacter,LocationCharacter,WorldGroup,PdaAccount,PdaAccountLogin,PdaContact,PdaTransmission,PdaDelivery
from app.services.character import CharacterService
from app.services.equipment_modifications import installed
from app.services.portable_lights import walk,definition,pda_state
from app.services.transaction import atomic_operation
from app.services.exceptions import ValidationError,ConflictError,NotFoundError
from app.services.world_travel_events import TILE_METERS

BASE_RANGE=300
ENHANCED_RANGE=1000
JAMMER_RANGE=25


def enabled():
    connection=inspect(db.session.connection())
    return all(connection.has_table(name) for name in ('pda_contacts','pda_transmissions','pda_deliveries'))


def mods(item):return {entry['ruleId'] for entry in installed(item)}


def signal_id(character,path,item):
    return hashlib.sha256(json.dumps([character.lobby_id,character.id,item.get('id') or path],ensure_ascii=False).encode()).hexdigest()


def _position(character,location_id,groups,all_models):
    models=[model for model in all_models if model.character_id==character.id]
    local=next((model for model in models if model.location_id==location_id),None) if location_id else None
    group=next((group for group in groups
        if character.id in {int(value) for value in group.member_character_ids or [] if str(value).isdigit()}),None)
    if not local and group:return {'world_x':(group.tile_x+.5)*TILE_METERS,'world_y':(group.tile_y+.5)*TILE_METERS,
        'tile_x':group.tile_x,'tile_y':group.tile_y,'height':0,'location_id':None}
    local=local or (models[0] if models else None)
    if not local:return None
    from app.services.location_navigation import ground
    location=local.location
    return {'world_x':(location.world_tile_x+.5)*TILE_METERS-location.grid_width/2+local.pos_x+.5,
        'world_y':(location.world_tile_z+.5)*TILE_METERS-location.grid_height/2+local.pos_y+.5,
        'height':ground(location,local.pos_x,local.pos_y)+(local.floor_level or 0)*2.4,
        'tile_x':location.world_tile_x,'tile_y':location.world_tile_z,
        'location_id':local.location_id,'x':local.pos_x,'y':local.pos_y,'level':local.floor_level or 0}


def distance(first,last):
    vertical=first['height']-last['height'] if first['location_id'] and first['location_id']==last['location_id'] else 0
    return math.sqrt((first['world_x']-last['world_x'])**2+(first['world_y']-last['world_y'])**2+vertical**2)


def registry(lobby_id,location_id=None):
    sessions={row.character_id:account.id for row,account in db.session.query(PdaAccountLogin,PdaAccount).join(
        PdaAccount,PdaAccount.id==PdaAccountLogin.account_id).join(LobbyCharacter,LobbyCharacter.id==PdaAccountLogin.character_id).filter(LobbyCharacter.lobby_id==lobby_id).all()}
    accounts={account.id:account for account in PdaAccount.query.filter_by(lobby_id=lobby_id).all()}
    from app.services.pda_accounts import account_for_device
    groups=WorldGroup.query.filter_by(lobby_id=lobby_id).order_by(WorldGroup.id).all()
    all_models=LocationCharacter.query.join(LobbyCharacter,LobbyCharacter.id==LocationCharacter.character_id).filter(
        LobbyCharacter.lobby_id==lobby_id).order_by(LocationCharacter.last_action.desc(),LocationCharacter.id.desc()).all()
    result=[]
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby_id).all():
        devices=[(path,item) for path,item in walk(character.data or {}) if definition(item)=='pda'
            and item.get('quantity',1)==1 and pda_state(item)['on'] and pda_state(item)['charge_percent']>0]
        if not devices:continue
        position=_position(character,location_id,groups,all_models)
        if not position:continue
        for path,item in devices:
            account=account_for_device(character,item,accounts=accounts,legacy=sessions)
            rules=mods(item)
            result.append({'character':character,'item':item,'path':path,'position':position,'account':account,
                'signal_id':signal_id(character,path,item),'mods':rules,
                'range':ENHANCED_RANGE if 'pda-b10' in rules else BASE_RANGE})
    return result


def blocked(device,devices):
    from app.services.artifact_effects import device_block_reason
    character=device.get('character')
    if character:
        reason=device_block_reason(getattr(character,'data',None) or {},device.get('item'))
        if reason:return reason
    if 'pda-b5' in device['mods']:return None
    weather=device['character'].lobby.weather_settings or {}
    from app.services.weather_rules import profile
    if (weather.get('emission') or {}).get('enabled') or profile(weather)['storm']:return 'Сигнал подавлен погодой'
    if any('pda-b16' in peer['mods'] and (peer['item'].get('pdaNetwork') or {}).get('jammer_on')
            and distance(device['position'],peer['position'])<=JAMMER_RANGE+1e-7 for peer in devices):return 'Сигнал подавлен глушилкой'
    return None


def reaches(source,target,devices):
    return not blocked(source,devices) and not blocked(target,devices) and distance(source['position'],target['position'])<=source['range']+1e-7


def _source(character_id,user_id,payload,edit=False):
    if not enabled():raise ValidationError('Сеть КПК пока недоступна. Обновите сервер.')
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if edit:
        from app.services.combat import CombatService
        if CombatService._character_condition(character.data or {})['state']!='active':raise ValidationError('Персонаж не может использовать КПК')
    if not isinstance(payload,dict):raise ValidationError('Выберите КПК')
    path=payload.get('item_path')
    if not isinstance(path,list) or not path or any(type(part) not in {str,int} for part in path):raise ValidationError('Выберите КПК')
    item=next((item for current,item in walk(character.data or {}) if current==path and definition(item)=='pda'),None)
    if not item or item.get('id')!=payload.get('item_id'):raise ConflictError('КПК перемещён')
    if item.get('quantity',1)!=1:raise ValidationError('Разделите стопку КПК')
    power=pda_state(item)
    if not power['on'] or power['charge_percent']<=0:raise ValidationError('Для связи нужен включённый исправный КПК с зарядом')
    location_id=payload.get('location_id')
    if location_id is not None and type(location_id) is not int:raise ValidationError('Некорректная подлокация')
    if location_id is not None and not LocationCharacter.query.filter_by(location_id=location_id,character_id=character.id).first():
        raise ValidationError('Персонаж отсутствует на этой подлокации')
    devices=registry(character.lobby_id,location_id)
    source=next((entry for entry in devices if entry['signal_id']==signal_id(character,path,item)),None)
    if not source:raise ValidationError('Нет положения персонажа на карте')
    return character,item,source,devices


def _account(source):
    account=source['account']
    if not account:raise ValidationError('Войдите в аккаунт КПК')
    return account


def _signals(source,devices):
    character=source['character']
    account=source['account'];contacts=PdaContact.query.filter_by(account_id=account.id).all() if account else []
    friends={contact.target_account_id for contact in contacts if contact.friendly}
    signals=[]
    for peer in devices:
        if peer['character'].id==character.id or 'pda-b9' in peer['mods'] or not reaches(peer,source,devices):continue
        peer_account=peer['account'];entry={'signal_id':peer['signal_id'],'login':peer_account.login if peer_account else 'Неизвестный КПК',
            'account_id':peer_account.id if peer_account else None,'friendly':bool(peer_account and peer_account.id in friends),
            'can_send':reaches(source,peer,devices),'can_receive':True}
        if 'pda-b2' in source['mods']:
            position=peer['position']
            entry['position']=({key:position[key] for key in ('location_id','x','y','level')} if position['location_id']
                else {'tile_x':position['tile_x'],'tile_y':position['tile_y']})
        signals.append(entry)
    return signals


def proximity(character_id,user_id,location_id=None,lobby_id=None):
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if lobby_id is not None and character.lobby_id!=lobby_id:raise NotFoundError('Персонаж находится в другом мире')
    if location_id is not None and not LocationCharacter.query.filter_by(character_id=character_id,location_id=location_id).first():
        raise ValidationError('Персонаж отсутствует на подлокации')
    if not enabled() or not any(definition(item)=='pda' and 'pda-b8' in mods(item) and pda_state(item)['on'] and pda_state(item)['charge_percent']>0
            for _,item in walk(character.data or {})):return {'devices':[]}
    devices=registry(character.lobby_id,location_id);result=[]
    for source in devices:
        if source['character'].id!=character_id or 'pda-b8' not in source['mods']:continue
        result.append({'id':source['signal_id'],'name':source['item'].get('name') or 'КПК',
            'sound':(source['item'].get('pdaNetwork') or {}).get('proximity_sound',True),
            'signals':[{'id':signal['signal_id'],'login':signal['login']} for signal in _signals(source,devices) if not signal['friendly']]})
    return {'devices':result}


def snapshot(character_id,user_id,payload):
    character,item,source,devices=_source(character_id,user_id,payload)
    return snapshot_source(source,devices)


def snapshot_source(source,devices):
    character=source['character'];item=source['item']
    account=source['account'];contacts=PdaContact.query.filter_by(account_id=account.id).all() if account else []
    signals=_signals(source,devices)
    messages=[]
    if account:
        delivered=select(PdaDelivery.transmission_id).where(PdaDelivery.account_id==account.id)
        rows=PdaTransmission.query.filter(PdaTransmission.lobby_id==character.lobby_id,
            or_(PdaTransmission.sender_account_id==account.id,PdaTransmission.id.in_(delivered))).order_by(
                PdaTransmission.created_at.desc(),PdaTransmission.id.desc()).limit(100).all()
        for row in reversed(rows):
            if row.sender_account_id!=account.id and account.id not in row.recipient_account_ids:continue
            sender=db.session.get(PdaAccount,row.sender_account_id)
            messages.append({'id':row.id,'sender':sender.login if sender else 'Удалённый аккаунт','text':row.text,'game_time':row.game_time,
                'outgoing':row.sender_account_id==account.id,'recipient':row.target_login})
    saved=[]
    for contact in contacts:
        target=db.session.get(PdaAccount,contact.target_account_id)
        if not target or target.lobby_id!=character.lobby_id:continue
        saved.append({'account_id':target.id,'login':target.login,'friendly':contact.friendly,
            'can_send':any(peer['account'] and peer['account'].id==target.id and 'pda-b9' not in peer['mods'] and reaches(source,peer,devices) for peer in devices)})
    return {'account':{'id':account.id,'login':account.login} if account else None,'source':{'signal_id':source['signal_id'],
        'transmit_range':source['range'],'blocked':blocked(source,devices),'jammer_on':bool((item.get('pdaNetwork') or {}).get('jammer_on')),
        'has_jammer':'pda-b16' in source['mods'],'has_proximity':'pda-b8' in source['mods'],
        'proximity_sound':(item.get('pdaNetwork') or {}).get('proximity_sound',True)},'signals':signals,'contacts':saved,'messages':messages[-100:]}


@atomic_operation
def operate(character_id,user_id,payload):
    character,item,source,devices=_source(character_id,user_id,payload,edit=True)
    operation=payload.get('operation')
    if operation=='proximity_sound':
        if 'pda-b8' not in source['mods']:raise ValidationError('На КПК нет детектора приближения')
        if type(payload.get('enabled')) is not bool:raise ValidationError('Выберите состояние звукового сигнала')
        if (item.get('pdaNetwork') or {}).get('proximity_sound',True)!=payload['enabled']:
            if payload.get('character_revision')!=character.revision:raise ConflictError('Лист изменился')
            item.setdefault('pdaNetwork',{})['proximity_sound']=payload['enabled'];flag_modified(character,'data')
        return character,{'operation':operation}
    elif operation=='jammer':
        if 'pda-b16' not in source['mods']:raise ValidationError('На КПК нет глушилки')
        if type(payload.get('enabled')) is not bool:raise ValidationError('Выберите состояние глушилки')
        from app.services.portable_lights import operate as use_device
        character,actor,result=use_device(character_id,user_id,{'operation':'pda_jammer','path':payload['item_path'],
            'source_id':payload.get('item_id'),'character_revision':payload.get('character_revision'),'enabled':payload['enabled']})
        return character,result
    return character,operate_source(source,devices,payload)


def operate_source(source,devices,payload):
    character=source['character'];operation=payload.get('operation')
    if operation=='contact':
        account=_account(source);login=unicodedata.normalize('NFKC',str(payload.get('login') or '')).strip().casefold()
        target=PdaAccount.query.filter_by(lobby_id=character.lobby_id,login_key=login).first()
        if not target:raise NotFoundError('Аккаунт в этом мире не найден')
        if target.id==account.id:raise ValidationError('Это ваш аккаунт')
        if type(payload.get('friendly',True)) is not bool:raise ValidationError('Выберите отношение к контакту')
        contact=db.session.get(PdaContact,(account.id,target.id))
        if contact:contact.friendly=payload.get('friendly',True)
        else:db.session.add(PdaContact(account_id=account.id,target_account_id=target.id,friendly=payload.get('friendly',True)))
    elif operation=='remove_contact':
        account=_account(source)
        if type(payload.get('target_account_id')) is not int:raise ValidationError('Выберите контакт')
        contact=db.session.get(PdaContact,(account.id,payload['target_account_id']))
        if contact:db.session.delete(contact)
    elif operation=='send':
        account=_account(source)
        text=payload.get('text');identity=payload.get('operation_id')
        if not isinstance(text,str) or not 1<=len(text.strip())<=2000:raise ValidationError('Сообщение: от 1 до 2000 символов')
        if not isinstance(identity,str) or not 1<=len(identity)<=100:raise ValidationError('Нужен идентификатор сообщения')
        target_id=payload.get('target_account_id')
        if target_id is not None and type(target_id) is not int:raise ValidationError('Выберите контакт')
        target=PdaAccount.query.filter_by(id=target_id,lobby_id=character.lobby_id).first() if target_id is not None else None
        if target_id is not None and not target:raise NotFoundError('Аккаунт в этом мире не найден')
        signature=hashlib.sha256(json.dumps([source['signal_id'],text,target_id],ensure_ascii=False).encode()).hexdigest()
        prior=db.session.get(PdaTransmission,identity)
        if prior:
            if prior.sender_account_id!=account.id or prior.signature!=signature:raise ConflictError('Идентификатор сообщения уже использован')
            return {'message_id':prior.id,'replayed':True}
        reason=blocked(source,devices)
        if reason:raise ValidationError(reason)
        recipients={peer['account'].id for peer in devices if peer['account'] and peer['account'].id!=account.id
            and (target_id is None or peer['account'].id==target_id) and reaches(source,peer,devices)}
        message=PdaTransmission(id=identity,lobby_id=character.lobby_id,sender_account_id=account.id,
            source_signal_id=source['signal_id'],recipient_account_ids=sorted(recipients),text=text.strip(),target_login=target.login if target else None,
            game_time=(character.lobby.game_day*1440+character.lobby.game_time_minutes)*60,signature=signature)
        db.session.add(message)
        try:db.session.flush()
        except IntegrityError:
            db.session.rollback();prior=db.session.get(PdaTransmission,identity)
            if prior and prior.sender_account_id==account.id and prior.signature==signature:return {'message_id':identity,'replayed':True}
            raise ConflictError('Идентификатор сообщения уже использован')
        db.session.add_all(PdaDelivery(account_id=account_id,transmission_id=identity) for account_id in recipients)
        return {'message_id':identity,'replayed':False}
    else:raise ValidationError('Неизвестное действие сети КПК')
    return {'operation':operation}
