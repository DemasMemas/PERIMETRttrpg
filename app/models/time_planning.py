"""Запрос игрового времени и независимые планы персонажей."""
from datetime import datetime, timezone

from app.extensions import db


class TimePlanRequest(db.Model):
    __tablename__ = 'time_plan_requests'

    id = db.Column(db.String(80), primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey('lobbies.id', ondelete='CASCADE'), nullable=False, index=True)
    gm_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    start_minute = db.Column(db.Integer, nullable=False)
    window_minutes = db.Column(db.Integer, nullable=False)
    owner_ids = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class TimePlan(db.Model):
    __tablename__ = 'time_plans'
    __table_args__ = (db.UniqueConstraint('request_id', 'character_id', name='uq_time_plan_character'),)

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.String(80), db.ForeignKey('time_plan_requests.id', ondelete='CASCADE'), nullable=False, index=True)
    character_id = db.Column(db.Integer, db.ForeignKey('lobby_characters.id', ondelete='CASCADE'), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    activities = db.Column(db.JSON, nullable=False, default=list)
    confirmed = db.Column(db.Boolean, nullable=False, default=False)
    overflow_state = db.Column(db.String(12), nullable=False, default='none')
    revision = db.Column(db.Integer, nullable=False, default=1)
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    request = db.relationship('TimePlanRequest', backref=db.backref('plans', cascade='all, delete-orphan'))
    character = db.relationship('LobbyCharacter')
    __mapper_args__ = {'version_id_col': revision}
