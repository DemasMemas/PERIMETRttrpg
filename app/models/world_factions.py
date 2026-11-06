"""Справочник группировок и фракций конкретного игрового мира."""
from app.extensions import db


class WorldFactionConfig(db.Model):
    __tablename__='world_faction_configs'

    lobby_id=db.Column(db.Integer,db.ForeignKey('lobbies.id',ondelete='CASCADE'),primary_key=True)
    source=db.Column(db.String(20),nullable=False,default='custom')
    groups=db.Column(db.JSON,nullable=False,default=list)
    relations=db.Column(db.JSON,nullable=False,default=list)
    revision=db.Column(db.Integer,nullable=False,default=1)
    __mapper_args__={'version_id_col':revision}
