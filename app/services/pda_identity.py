"""Подменяется отображаемое имя, а не учётная запись или её права."""
from copy import deepcopy
from hashlib import sha256
import unicodedata
from sqlalchemy import inspect
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import PdaTransmissionIdentity,PdaContactLabel
from app.services.equipment_modifications import installed
from app.services.exceptions import ValidationError,ConflictError


def enabled():return inspect(db.session.connection()).has_table('pda_transmission_identities')


def contact_labels_enabled():return inspect(db.session.connection()).has_table('pda_contact_labels')


def contact_labels(account_id):
    if not contact_labels_enabled():return {}
    return {row.target_account_id:{'name':row.observed_name,'signal_id':row.signal_id}
        for row in PdaContactLabel.query.filter_by(account_id=account_id).all()}


def save_contact_label(account_id,target_id,name,signal_id=None):
    if not contact_labels_enabled():return
    row=db.session.get(PdaContactLabel,(account_id,target_id))
    if row:row.observed_name=name;row.signal_id=signal_id
    else:db.session.add(PdaContactLabel(account_id=account_id,target_account_id=target_id,observed_name=name,signal_id=signal_id))


def remove_contact_label(account_id,target_id):
    if contact_labels_enabled():PdaContactLabel.query.filter_by(account_id=account_id,target_account_id=target_id).delete(synchronize_session='fetch')


def alias(item):
    if not any(mod['ruleId']=='pda-b21' for mod in installed(item)):return None
    value=(item.get('pdaNetwork') or {}).get('sender_name')
    return value if isinstance(value,str) and value else None


def display(item,account):return alias(item) or (account.login if account else 'Неизвестный КПК')


def history(identities):
    if not identities or not enabled():return {}
    return {row.transmission_id:{'sender':row.sender_name,'recipient':row.recipient_name} for row in PdaTransmissionIdentity.query.filter(
        PdaTransmissionIdentity.transmission_id.in_(identities)).all()}


def recipient_name(source,devices,target):
    if not target:return None
    from app.services.pda_network import _signals
    signal=next((s for s in reversed(_signals(source,devices)) if s['account_id']==target.id),None)
    if signal:return signal['login']
    saved=contact_labels(source['account'].id).get(target.id)
    if saved:return saved['name']
    masked=next((alias(peer['item']) for peer in devices if peer['account'] and peer['account'].id==target.id and alias(peer['item'])),None)
    return masked or target.login


def require_storage():
    if not enabled():raise ValidationError('Для подмены имени нужно обновить базу данных сервера')


def settings(source):
    from app.services.artifact_effects import device_block_reason
    has_alias='pda-b21' in source['mods']
    return {'has_alias':has_alias,'sender_name':alias(source['item']) or '',
        'alias_ready':has_alias and enabled(),
        'alias_blocked':device_block_reason(getattr(source.get('character'),'data',None) or {},source['item']) if has_alias else None}


def change(source,payload):
    if 'pda-b21' not in source['mods']:raise ValidationError('На КПК нет «Замены данных»')
    from app.services.artifact_effects import require_device_available
    require_device_available(getattr(source.get('character'),'data',None) or {},source['item'])
    require_storage()
    text=payload.get('sender_name');identity=payload.get('operation_id')
    if not isinstance(text,str):raise ValidationError('Введите имя или оставьте поле пустым')
    if any(unicodedata.category(char)=='Cc' for char in text):
        raise ValidationError('Имя: до 100 символов без переносов строк и управляющих знаков')
    text=unicodedata.normalize('NFKC',text).strip()
    if len(text)>100:
        raise ValidationError('Имя: до 100 символов без переносов строк и управляющих знаков')
    if not isinstance(identity,str) or not 1<=len(identity)<=100:raise ValidationError('Нужен идентификатор изменения имени')
    network=source['item'].setdefault('pdaNetwork',{});signature=sha256(text.encode()).hexdigest()
    prior=next((r for r in network.get('alias_receipts') or [] if r['id']==identity),None)
    if prior:
        if prior['signature']!=signature:raise ConflictError('Идентификатор изменения имени уже использован')
        return {**deepcopy(prior['result']),'replayed':True}
    changed=(network.get('sender_name') or '')!=text
    if text:network['sender_name']=text
    else:network.pop('sender_name',None)
    result={'operation':'sender_name','sender_name':text,'changed':changed,'replayed':False}
    network['alias_receipts']=[*(network.get('alias_receipts') or [])[-127:],{'id':identity,'signature':signature,'result':deepcopy(result)}]
    flag_modified(source.get('owner') or source['character'],source.get('field','data'))
    return result
