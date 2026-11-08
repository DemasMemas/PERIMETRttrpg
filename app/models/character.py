# app/models/character.py
from datetime import datetime, timezone
from copy import deepcopy
from app.extensions import db
from app.utils.defaults import empty_dict, empty_list

class LobbyCharacter(db.Model):
    __tablename__ = 'lobby_characters'
    id = db.Column(db.Integer, primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey('lobbies.id'), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    data = db.Column(db.JSON, nullable=False, default=empty_dict)
    visible_to = db.Column(db.JSON, nullable=False, default=empty_list)
    editable_to = db.Column(db.JSON, nullable=False, default=empty_list)
    time_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, onupdate=lambda: datetime.now(timezone.utc))
    revision = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    __mapper_args__ = {'version_id_col': revision}

    lobby = db.relationship('Lobby', backref='characters')
    owner = db.relationship('User', foreign_keys=[owner_id])

    def data_snapshot(self):
        data = deepcopy(self.data or {})
        from app.services.weapon_catalog import normalize_character_weapons
        normalize_character_weapons(data)
        from app.services.prostheses import normalize_dimensions
        normalize_dimensions(data)
        from app.services.consumable_effects import remove_stale_movement_modifiers
        remove_stale_movement_modifiers(data,self.lobby_id)
        from app.services.character_traits import sync_pocket_capacity
        sync_pocket_capacity(self.data,data)
        from app.services.artifact_effects import artifact_passive_profile,needs_radiation_clocks,sync_radiation_clocks
        if needs_radiation_clocks(data):
            data.setdefault('health',{})['effects']=sync_radiation_clocks(data)
        data['_artifact_profile'] = artifact_passive_profile(data)
        data['_revision'] = self.revision
        data['_character_id'] = self.id
        return data
