"""Сохранённые результаты подрыва и добавление глобальных целей ГМом."""
from app.extensions import db
from app.utils.defaults import empty_list,empty_dict


class DetonationEvent(db.Model):
    __tablename__='detonation_events'
    id=db.Column(db.String(100),primary_key=True)
    lobby_id=db.Column(db.Integer,db.ForeignKey('lobbies.id',ondelete='CASCADE'),nullable=False,index=True)
    location_id=db.Column(db.Integer,db.ForeignKey('locations.id',ondelete='SET NULL'),nullable=True)
    profile=db.Column(db.JSON,nullable=False,default=empty_dict)
    results=db.Column(db.JSON,nullable=False,default=empty_list)
    affected_character_ids=db.Column(db.JSON,nullable=False,default=empty_list)
    carrier_id=db.Column(db.Integer,db.ForeignKey('lobby_characters.id',ondelete='SET NULL'),nullable=True)
    source_character_id=db.Column(db.Integer,db.ForeignKey('lobby_characters.id',ondelete='SET NULL'),nullable=True)
    game_time=db.Column(db.Integer,nullable=False)
    closed=db.Column(db.Boolean,nullable=False,default=False)
    revision=db.Column(db.Integer,nullable=False,default=1,server_default='1')
    __mapper_args__={'version_id_col':revision}
