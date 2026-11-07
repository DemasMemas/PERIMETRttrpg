"""Регистрация и вход в КПК в пределах одного мира."""
import re
import unicodedata
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, PdaAccount, PdaAccountLogin
from app.services.character import CharacterService
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.transaction import atomic_operation


def _character(character_id, user_id, *, edit=False):
    character = db.session.get(LobbyCharacter, character_id)
    if character is None:
        raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character, user_id, edit=edit)
    return character


def _login(payload):
    login = unicodedata.normalize('NFKC', str(payload.get('login') or '')).strip()
    if not re.fullmatch(r'[\w.-]{3,32}', login, re.UNICODE):
        raise ValidationError('Логин должен содержать от 3 до 32 букв, цифр, точек, дефисов или подчёркиваний')
    return login, login.casefold()


def _password(payload):
    password = payload.get('password')
    if not isinstance(password, str) or not 6 <= len(password) <= 128:
        raise ValidationError('Пароль должен содержать от 6 до 128 символов')
    return password


def _devices(character):
    from app.services.portable_lights import walk,definition
    return [(path,item) for path,item in walk(character.data or {}) if definition(item)=='pda']


def _device(character,payload):
    if payload is None or 'item_path' not in payload:return None
    device=next((item for path,item in _devices(character) if path==payload['item_path']),None)
    if not device or payload.get('item_id') is not None and device.get('id')!=payload['item_id']:
        raise ConflictError('КПК перемещён. Откройте его заново.')
    if device.get('quantity',1)!=1:raise ValidationError('Выберите один физический КПК')
    return device


def account_for_device(character,item,*,accounts=None,legacy=None):
    network=item.get('pdaNetwork') or {}
    if not isinstance(network,dict):network={}
    if 'account_id' in network:identity=network['account_id']
    elif legacy is not None:identity=legacy.get(character.id)
    else:
        old=db.session.get(PdaAccountLogin,character.id);identity=old.account_id if old else None
    if type(identity) is not int:return None
    account=accounts.get(identity) if accounts is not None else db.session.get(PdaAccount,identity)
    return account if account and account.lobby_id==character.lobby_id else None


def _account(character,device):
    if device is None:device=next((item for _,item in _devices(character)),None)
    if device is not None:return account_for_device(character,device)
    old=db.session.get(PdaAccountLogin,character.id)
    return db.session.get(PdaAccount,old.account_id) if old else None


def _assign(character,device,account):
    def network(item):
        if not isinstance(item.get('pdaNetwork'),dict):item['pdaNetwork']={}
        return item['pdaNetwork']
    old=db.session.get(PdaAccountLogin,character.id)
    if device is not None:
        # Сохраняем прежний вход на остальных старых КПК до удаления общей записи.
        if old:
            for _,other in _devices(character):network(other).setdefault('account_id',old.account_id)
            db.session.delete(old)
        if not device.get('id'):device['id']=str(uuid4())
        network(device)['account_id']=account.id if account else None
    else:
        # Совместимость прежнего API без выбора устройства.
        if account:
            if old:old.account_id=account.id
            else:db.session.add(PdaAccountLogin(character_id=character.id,account_id=account.id))
        elif old:db.session.delete(old)
        for _,other in _devices(character):network(other)['account_id']=account.id if account else None
    flag_modified(character,'data')


def account_status(character_id, user_id,payload=None):
    character = _character(character_id, user_id, edit=True)
    account = _account(character,_device(character,payload))
    if not account or account.lobby_id != character.lobby_id:
        return {'account': None}
    return {'account': {'id': account.id, 'login': account.login, 'notes': account.notes or ''}}


@atomic_operation
def account_action(character_id, user_id, operation, payload):
    character = _character(character_id, user_id, edit=True)
    if not isinstance(payload,dict):raise ValidationError('Выберите действие с аккаунтом КПК')
    device=_device(character,payload)
    if device is not None and operation in {'login','register','save_notes'}:
        from app.services.portable_lights import pda_state
        power=pda_state(device)
        if not power['on'] or power['charge_percent']<=0:raise ValidationError('Включите исправный заряженный КПК')
    if operation == 'logout':
        _assign(character,device,None)
        return {'account': None}
    if operation == 'save_notes':
        account = _account(character,device)
        if not account or account.lobby_id != character.lobby_id:
            raise ValidationError('Сначала войдите в аккаунт КПК')
        notes = payload.get('notes') if isinstance(payload, dict) else None
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValidationError('Заметки должны содержать не более 10 000 символов')
        account.notes = notes
        return {'account': {'id': account.id, 'login': account.login, 'notes': account.notes}}
    if operation not in {'register', 'login'} or not isinstance(payload, dict):
        raise ValidationError('Неизвестное действие с аккаунтом КПК')
    login, login_key = _login(payload)
    password = _password(payload)
    account = PdaAccount.query.filter_by(lobby_id=character.lobby_id, login_key=login_key).first()
    if operation == 'register':
        if account:
            raise ConflictError('Этот логин уже занят в данном мире')
        account = PdaAccount(lobby_id=character.lobby_id, login=login, login_key=login_key)
        account.set_password(password)
        db.session.add(account)
        try:
            db.session.flush()
        except IntegrityError as error:
            db.session.rollback()
            raise ConflictError('Этот логин уже занят в данном мире') from error
    elif not account or not account.check_password(password):
        raise ValidationError('Неверный логин или пароль', code=401)
    _assign(character,device,account)
    return {'account': {'id': account.id, 'login': account.login, 'notes': account.notes or ''}}
