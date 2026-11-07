"""«Чистая запись»: удаление данных устройства и его мировых аккаунтов."""
from sqlalchemy import or_,event
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db,socketio
from app.models import (LobbyCharacter,LocationObject,LocationTrap,PdaAccount,PdaAccountLogin,
    PdaContact,PdaTransmission,PdaDelivery,PdaAccessSession)
from app.services.exceptions import ValidationError,ConflictError
from app.services.portable_lights import walk,definition


@event.listens_for(Session,'after_rollback')
def _rollback(session):session.info.pop('pda_wipe_updates',None)


def _changed(lobby_id,owner):
    kind='characters' if isinstance(owner,LobbyCharacter) else 'objects' if isinstance(owner,LocationObject) else None
    if kind:db.session.info.setdefault('pda_wipe_updates',{}).setdefault(lobby_id,{}).setdefault(kind,set()).add(owner.id)


def publish(lobby_id):
    """Отправляется после фиксации, без служебных ID в ответе игровому клиенту."""
    updates=db.session.info.get('pda_wipe_updates',{}).pop(lobby_id,{})
    from app.services.character_events import emit_character_update
    from app.schemas.location import LocationObjectSchema
    for identity in updates.get('characters',[]):
        character=db.session.get(LobbyCharacter,identity)
        if character:emit_character_update({'character_id':identity,'updates':{'data':character.data_snapshot()},'source':'pda_wipe'})
    for identity in updates.get('objects',[]):
        obj=db.session.get(LocationObject,identity)
        if obj:socketio.emit('location_object_updated',{'location_id':obj.location_id,'object':LocationObjectSchema().dump(obj)},room=f'location_{obj.location_id}')


def validate(item):
    from app.services.equipment_modifications import installed
    if not any(mod['ruleId']=='pda-b14' for mod in installed(item)):
        raise ValidationError('На КПК нет «Чистой записи»')
    if item.get('quantity',1)!=1:raise ValidationError('Выберите один физический КПК')


def _roots(lobby_id):
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby_id).all():
        yield character,'data',walk(character.data or {},True)
    for obj in LocationObject.query.join(LocationObject.location).filter_by(lobby_id=lobby_id).all():
        yield obj,'properties',walk({'inventory':{'contents':(obj.properties or {}).get('contents') or []}},True)
    for trap in LocationTrap.query.join(LocationTrap.location).filter_by(lobby_id=lobby_id).all():
        yield trap,'data',walk({'inventory':{'contents':[(trap.data or {}).get('item') or {}]}},True)


def erase(lobby_id,item):
    validate(item)
    from app.services import pda_devices,pda_access
    from app.services.item_template_lookup import resolve_item_template
    records=pda_devices.registry(lobby_id)
    own=[record for record in records if record['id']==item.get('id')]
    if len(own)!=1 or own[0]['ambiguous']:raise ConflictError('Нельзя однозначно определить очищаемый КПК')
    account=pda_devices.account(own[0]);network=item.get('pdaNetwork') or {}
    identities={identity for identity in [network.get('account_id'),*(network.get('profile_accounts') or {}).values()]
        if type(identity) is int}
    if account:identities.add(account.id)
    accounts=PdaAccount.query.filter(PdaAccount.lobby_id==lobby_id,PdaAccount.id.in_(identities)).all()
    identities={account.id for account in accounts}
    # Удаляются цифровые книги, а не случайно сохранённые внутри физические предметы.
    files=[];kept=[]
    for file in item.get('contents') or []:
        template=resolve_item_template(file,lobby_id)
        attrs=(template.attributes or {}) if template else file.get('attributes') or {}
        (files if attrs.get('book_kind') in {'skill','equipment'} else kept).append(file)
    file_ids={file.get('id') for file in files if file.get('id')}
    if pda_access.enabled():
        for row in PdaAccessSession.query.filter_by(lobby_id=lobby_id).filter(
                or_(PdaAccessSession.source_device_id==item['id'],PdaAccessSession.target_device_id==item['id']),
                PdaAccessSession.status.in_(['working','active'])).all():
            pda_access._close(row,'Данные КПК удалены «Чистой записью»')
            for reader in pda_access._readers(row):_changed(lobby_id,reader)
    unpaired=0
    for owner,field,entries in _roots(lobby_id):
        changed=False
        for _,candidate in entries:
            state=candidate.get('controllerState') or {}
            if state.get('paired_pda_id')==item['id']:
                state.pop('paired_pda_id');changed=True;unpaired+=1
            if definition(candidate)=='pda':
                saved=candidate.get('pdaNetwork') or {}
                if saved.get('account_id') in identities:saved['account_id']=None;changed=True
                for profile,identity in list((saved.get('profile_accounts') or {}).items()):
                    if identity in identities:saved['profile_accounts'][profile]=None;changed=True
        if changed:flag_modified(owner,field);_changed(lobby_id,owner)
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby_id).all():
        data=character.data or {};crafting=data.get('crafting') or {};job=crafting.get('current') or {}
        if job.get('kind')=='book' and job.get('book_id') in file_ids and not job.get('remote_source'):
            result={'job_id':job['id'],'kind':'book','name':job['name'],'cancelled':True,'success':False,
                'roll':None,'difficulty':0,'lost':[],'lost_items':[],'output_count':0}
            crafting.pop('current');crafting['history']=[*(crafting.get('history') or []),result][-30:]
            from app.services.action_journal import append_work
            append_work(data,'wipe-book:'+job['id'],'cancel',result);flag_modified(character,'data');_changed(lobby_id,character)
        meta=(data.get('health') or {}).get('combatMeta') or {}
        if (meta.get('pdaDynamo') or {}).get('id')==item['id']:
            meta.pop('pdaDynamo');flag_modified(character,'data');_changed(lobby_id,character)
    # Явная очистка ссылок работает и со старыми БД без включённого FK cascade.
    sent=[row.id for row in PdaTransmission.query.filter(PdaTransmission.sender_account_id.in_(identities)).all()]
    PdaDelivery.query.filter(or_(PdaDelivery.account_id.in_(identities),PdaDelivery.transmission_id.in_(sent))).delete(synchronize_session='fetch')
    PdaTransmission.query.filter(PdaTransmission.id.in_(sent)).delete(synchronize_session='fetch')
    for message in PdaTransmission.query.filter_by(lobby_id=lobby_id).all():
        if identities.intersection(message.recipient_account_ids or []):
            message.recipient_account_ids=[identity for identity in message.recipient_account_ids if identity not in identities]
    PdaContact.query.filter(or_(PdaContact.account_id.in_(identities),PdaContact.target_account_id.in_(identities))).delete(synchronize_session='fetch')
    PdaAccountLogin.query.filter(PdaAccountLogin.account_id.in_(identities)).delete(synchronize_session='fetch')
    for account in accounts:db.session.delete(account)
    item['contents']=kept;item['pdaNetwork']={'account_id':None}
    pda_devices.save(own[0]);_changed(lobby_id,own[0]['owner']);pda_access._queue(lobby_id)
    return {'deleted_files':len(files),'deleted_accounts':len(accounts),'unpaired_controllers':unpaired}
