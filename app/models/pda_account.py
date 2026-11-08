"""Учётные записи КПК отдельного игрового мира."""
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class PdaAccount(db.Model):
    __tablename__ = 'pda_accounts'
    __table_args__ = (db.UniqueConstraint('lobby_id', 'login_key', name='uq_pda_account_world_login'),)

    id = db.Column(db.Integer, primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey('lobbies.id', ondelete='CASCADE'), nullable=False)
    login = db.Column(db.String(32), nullable=False)
    login_key = db.Column(db.String(32), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class PdaAccountLogin(db.Model):
    __tablename__ = 'pda_account_logins'

    character_id = db.Column(db.Integer, db.ForeignKey('lobby_characters.id', ondelete='CASCADE'), primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('pda_accounts.id', ondelete='CASCADE'), nullable=False)
    account = db.relationship('PdaAccount')


class PdaContact(db.Model):
    __tablename__='pda_contacts'
    account_id=db.Column(db.Integer,db.ForeignKey('pda_accounts.id',ondelete='CASCADE'),primary_key=True)
    target_account_id=db.Column(db.Integer,db.ForeignKey('pda_accounts.id',ondelete='CASCADE'),primary_key=True)
    friendly=db.Column(db.Boolean,nullable=False,default=True)


class PdaTransmission(db.Model):
    __tablename__='pda_transmissions'
    id=db.Column(db.String(100),primary_key=True)
    lobby_id=db.Column(db.Integer,db.ForeignKey('lobbies.id',ondelete='CASCADE'),nullable=False,index=True)
    sender_account_id=db.Column(db.Integer,db.ForeignKey('pda_accounts.id',ondelete='CASCADE'),nullable=False)
    source_signal_id=db.Column(db.String(64),nullable=False)
    recipient_account_ids=db.Column(db.JSON,nullable=False,default=list)
    text=db.Column(db.Text,nullable=False)
    target_login=db.Column(db.String(32),nullable=True)
    game_time=db.Column(db.Integer,nullable=False)
    signature=db.Column(db.String(64),nullable=False)
    created_at=db.Column(db.DateTime,nullable=False,default=lambda:datetime.now(timezone.utc))


class PdaDelivery(db.Model):
    __tablename__='pda_deliveries'
    account_id=db.Column(db.Integer,db.ForeignKey('pda_accounts.id',ondelete='CASCADE'),primary_key=True)
    transmission_id=db.Column(db.String(100),db.ForeignKey('pda_transmissions.id',ondelete='CASCADE'),primary_key=True)


class PdaTransmissionIdentity(db.Model):
    """Имя в момент передачи, независимо от последующей настройки устройства."""
    __tablename__='pda_transmission_identities'
    transmission_id=db.Column(db.String(100),db.ForeignKey('pda_transmissions.id',ondelete='CASCADE'),primary_key=True)
    sender_name=db.Column(db.String(100),nullable=False)
    recipient_name=db.Column(db.String(100),nullable=True)


class PdaContactLabel(db.Model):
    """Увиденное имя контакта, общее для устройств одного аккаунта."""
    __tablename__='pda_contact_labels'
    __table_args__=(db.ForeignKeyConstraint(['account_id','target_account_id'],
        ['pda_contacts.account_id','pda_contacts.target_account_id'],ondelete='CASCADE'),)
    account_id=db.Column(db.Integer,primary_key=True)
    target_account_id=db.Column(db.Integer,primary_key=True)
    observed_name=db.Column(db.String(100),nullable=False)
    signal_id=db.Column(db.String(64),nullable=True)
