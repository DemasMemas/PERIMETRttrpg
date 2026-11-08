"""Пять сигналов одному физическому КПК по существующим игровым часам."""
from hashlib import sha256
import json
from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy import event,cast,Text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db,socketio
from app.models import LobbyCharacter,LocationCharacter,Location,LocationObject
from app.services.exceptions import ValidationError,ConflictError

INTERVAL=6
ATTEMPTS=5


def charge_start(character,user_id,pda_id,payload,result):
    from app.services.pda_action_cost import charge
    return charge(character,user_id,pda_id,payload,result,'Спам-атака')


@event.listens_for(Session,'after_commit')
def publish(session):
    if session.in_nested_transaction():return
    queued=session.info.pop('pda_spam_notifications',[])
    for packet,recipients in queued:
        for identity in recipients:socketio.emit('pda_spam_received',packet,room=f'user_{identity}')
    for identity in session.info.pop('pda_spam_changed_lobbies',set()) | {packet['lobby_id'] for packet,_ in queued}:
        socketio.emit('pda_network_updated',{'lobby_id':identity},room=f'lobby_{identity}')


@event.listens_for(Session,'after_rollback')
def clear(session):
    session.info.pop('pda_spam_notifications',None);session.info.pop('pda_spam_changed_lobbies',None)


def start(source,devices,payload):
    from app.services.pda_network import _account,_signals
    from app.services.pda_identity import display
    if 'pda-b17' not in source['mods']:raise ValidationError('На КПК нет «Спам-атаки»')
    account=_account(source);text=payload.get('text');identity=payload.get('operation_id');target_id=payload.get('target_signal_id')
    if not isinstance(text,str) or not 1<=len(text.strip())<=2000:raise ValidationError('Сообщение: от 1 до 2000 символов')
    if not isinstance(identity,str) or not 1<=len(identity)<=100:raise ValidationError('Нужен идентификатор спам-атаки')
    if not isinstance(target_id,str):raise ValidationError('Выберите физический КПК в сети')
    signature=sha256(json.dumps([target_id,text],ensure_ascii=False).encode()).hexdigest()
    network=source['item'].setdefault('pdaNetwork',{})
    prior=next((r for r in network.get('spam_receipts') or [] if r['operation_id']==identity),None)
    if prior:
        if prior['signature']!=signature:raise ConflictError('Идентификатор спам-атаки уже использован')
        current=network.get('spam_job') or {}
        return {'operation':'spam','replayed':True,'remaining':current.get('remaining',0) if current.get('id')==prior['id'] else 0}
    if (network.get('spam_job') or {}).get('remaining',0)>0:raise ConflictError('Этот КПК уже выполняет спам-атаку')
    signals=_signals(source,devices)
    if not any(signal['signal_id']==target_id and signal['can_send'] for signal in signals):
        raise ValidationError('Целевой КПК недоступен для передачи')
    target=next(peer for peer in devices if peer['signal_id']==target_id)
    if not target['item'].get('id'):raise ValidationError('Сначала включите целевой КПК заново')
    job={'id':uuid4().hex,'target_device_id':target['item']['id'],'text':text.strip(),
        'sender':display(source['item'],account),'remaining':ATTEMPTS,'elapsed_seconds':0}
    network['spam_job']=job
    network['spam_receipts']=[*(network.get('spam_receipts') or [])[-127:],
        {'operation_id':identity,'signature':signature,'id':job['id']}]
    flag_modified(source.get('owner') or source['character'],source.get('field','data'))
    return {'operation':'spam','replayed':False,'remaining':ATTEMPTS}


def jobs(lobby_id,location_id=None):
    from app.services.portable_lights import walk,definition
    allowed=None
    if location_id is not None:
        allowed={row.character_id for row in LocationCharacter.query.filter_by(location_id=location_id).all()}
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby_id).all():
        if allowed is not None and character.id not in allowed:continue
        for _,item in walk(character.data or {}):
            job=(item.get('pdaNetwork') or {}).get('spam_job') or {}
            if definition(item)=='pda' and job.get('remaining',0)>0:yield character,item,job
    query=LocationObject.query.join(LocationObject.location).filter(Location.lobby_id==lobby_id,
        cast(LocationObject.properties,Text).like('%"spam_job"%'))
    if location_id is not None:query=query.filter(LocationObject.location_id==location_id)
    for obj in query.all():
        for _,item in walk({'inventory':{'contents':(obj.properties or {}).get('contents') or []}}):
            job=(item.get('pdaNetwork') or {}).get('spam_job') or {}
            if definition(item)=='pda' and job.get('remaining',0)>0:yield obj,item,job


def next_due(lobby_id,maximum):
    deadlines=[max(.001,INTERVAL-float(job.get('elapsed_seconds') or 0)) for _,_,job in jobs(lobby_id)]
    return min([maximum,*deadlines])


def advance(lobby,seconds,location_id=None):
    from app.services.pda_access import _radio_sources
    from app.services.pda_devices import registry
    from app.services.pda_network import reaches
    if seconds<=0:return []
    pending=list(jobs(lobby.id,location_id))
    if not pending:return []
    devices=_radio_sources(registry(lobby.id));changed={}
    for owner,item,job in pending:
        elapsed=float(job.get('elapsed_seconds') or 0)+seconds
        count=min(int(elapsed//INTERVAL),job['remaining'])
        job['elapsed_seconds']=elapsed%INTERVAL
        for _ in range(count):
            index=ATTEMPTS-job['remaining']+1;job['remaining']-=1
            sources=[device for device in devices if device['item'].get('id')==item.get('id')]
            targets=[device for device in devices if device['item'].get('id')==job['target_device_id']]
            if len(sources)!=1 or len(targets)!=1 or not reaches(sources[0],targets[0],devices):continue
            target=targets[0];receiver=target['owner'];power=target['item'].setdefault('pdaNetwork',{})
            message={'id':f"{job['id']}:{index}",'sender':job['sender'],'text':job['text'],
                'sent_at':datetime.now(timezone.utc).timestamp(),
                'game_time':(lobby.game_day*1440+lobby.game_time_minutes)*60,'outgoing':False,'spam':True,'noise':10}
            inbox=power.setdefault('spam_messages',[])
            if any(old['id']==message['id'] for old in inbox):continue
            inbox.append(message);power['spam_messages']=inbox[-100:]
            flag_modified(receiver,target['field'])
            if isinstance(receiver,LobbyCharacter):changed[receiver.id]=receiver
            position=target['position'];noise=[]
            if position['location_id']:
                from app.services.noise import apply as apply_noise
                scene=db.session.get(Location,position['location_id'])
                noise=apply_noise(scene,position['x'],position['y'],position['level'],10)
                for result in noise:
                    person=db.session.get(LobbyCharacter,result['character_id']);changed[person.id]=person
            listeners={result['character_id'] for result in noise}
            if isinstance(receiver,LobbyCharacter):listeners.add(receiver.id)
            for listener_id in listeners:
                listener=db.session.get(LobbyCharacter,listener_id)
                packet={'lobby_id':lobby.id,'character_id':listener.id,
                    'device_id':target['item']['id'] if listener is receiver else None,
                    'message_id':message['id'],'noise':10,'local_noise_applied':bool(position['location_id'])}
                recipients={listener.owner_id,lobby.gm_id}
                db.session.info.setdefault('pda_spam_notifications',[]).append((packet,sorted(recipients)))
        if job['remaining']<=0:item['pdaNetwork'].pop('spam_job',None)
        flag_modified(owner,'data' if isinstance(owner,LobbyCharacter) else 'properties')
        if isinstance(owner,LobbyCharacter):changed[owner.id]=owner
        db.session.info.setdefault('pda_spam_changed_lobbies',set()).add(lobby.id)
    return list(changed.values())
