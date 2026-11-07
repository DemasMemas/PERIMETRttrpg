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
