from datetime import datetime, timezone

from app.extensions import db


class WorldGroup(db.Model):
    __tablename__ = "world_groups"

    id = db.Column(db.Integer, primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey("lobbies.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    tile_x = db.Column(db.Integer, nullable=False)
    tile_y = db.Column(db.Integer, nullable=False)
    member_character_ids = db.Column(db.JSON, nullable=False, default=list)
    turn_active = db.Column(db.Boolean, nullable=False, default=True)
    turn_submitted_day = db.Column(db.Integer)
    turn_submitted_minutes = db.Column(db.Integer)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    lobby = db.relationship("Lobby", backref=db.backref("world_groups", cascade="all, delete-orphan"))


class WorldTravelEvent(db.Model):
    __tablename__ = "world_travel_events"

    id = db.Column(db.Integer, primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey("lobbies.id"), nullable=False, index=True)
    group_id = db.Column(
        db.Integer,
        db.ForeignKey("world_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    world_map_event_id = db.Column(
        db.Integer,
        db.ForeignKey("world_map_events.id", ondelete="SET NULL"),
        index=True,
    )
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    from_tile_x = db.Column(db.Integer, nullable=False)
    from_tile_y = db.Column(db.Integer, nullable=False)
    to_tile_x = db.Column(db.Integer, nullable=False)
    to_tile_y = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_at = db.Column(db.DateTime)
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    group = db.relationship(
        "WorldGroup",
        backref=db.backref("travel_events", cascade="all, delete-orphan"),
    )


class WorldMapEvent(db.Model):
    __tablename__ = "world_map_events"

    id = db.Column(db.Integer, primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey("lobbies.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    tile_x = db.Column(db.Integer, nullable=False)
    tile_y = db.Column(db.Integer, nullable=False)
    repeatable = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    lobby = db.relationship(
        "Lobby",
        backref=db.backref("world_map_events", cascade="all, delete-orphan"),
    )
    travel_events = db.relationship("WorldTravelEvent", backref="world_map_event")


class WorldStash(db.Model):
    __tablename__ = 'world_stashes'
    __table_args__ = (db.Index('ix_world_stashes_tile', 'lobby_id', 'tile_x', 'tile_y'),)

    id = db.Column(db.Integer, primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey('lobbies.id', ondelete='CASCADE'), nullable=False)
    tile_x = db.Column(db.Integer, nullable=False)
    tile_y = db.Column(db.Integer, nullable=False)
    properties = db.Column(db.JSON, nullable=False, default=dict)
    revision = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    __mapper_args__ = {'version_id_col':revision}

    lobby = db.relationship('Lobby', backref=db.backref('world_stashes', cascade='all, delete-orphan'))
