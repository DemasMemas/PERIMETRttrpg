"""Регистрация и вход в КПК в пределах одного мира."""
import re
import unicodedata

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import LobbyCharacter, PdaAccount, PdaAccountLogin
from app.services.character import CharacterService
from app.services.exceptions import ConflictError, NotFoundError, ValidationError


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


def account_status(character_id, user_id):
    character = _character(character_id, user_id, edit=True)
    session = db.session.get(PdaAccountLogin, character.id)
    account = db.session.get(PdaAccount, session.account_id) if session else None
    if not account or account.lobby_id != character.lobby_id:
        return {'account': None}
    return {'account': {'id': account.id, 'login': account.login, 'notes': account.notes or ''}}


def account_action(character_id, user_id, operation, payload):
    character = _character(character_id, user_id, edit=True)
    if operation == 'logout':
        session = db.session.get(PdaAccountLogin, character.id)
        if session:
            db.session.delete(session)
            db.session.commit()
        return {'account': None}
    if operation == 'save_notes':
        session = db.session.get(PdaAccountLogin, character.id)
        account = db.session.get(PdaAccount, session.account_id) if session else None
        if not account or account.lobby_id != character.lobby_id:
            raise ValidationError('Сначала войдите в аккаунт КПК')
        notes = payload.get('notes') if isinstance(payload, dict) else None
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValidationError('Заметки должны содержать не более 10 000 символов')
        account.notes = notes
        db.session.commit()
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
    session = db.session.get(PdaAccountLogin, character.id)
    if session:
        session.account_id = account.id
    else:
        db.session.add(PdaAccountLogin(character_id=character.id, account_id=account.id))
    db.session.commit()
    return {'account': {'id': account.id, 'login': account.login, 'notes': account.notes or ''}}
