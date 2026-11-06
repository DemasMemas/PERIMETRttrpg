"""Настраиваемые ГМом источники имён и сочетания уровней для будущей генерации НПС."""
from app.extensions import db


class NpcGenerationConfig(db.Model):
    __tablename__='npc_generation_configs'

    lobby_id=db.Column(db.Integer,db.ForeignKey('lobbies.id',ondelete='CASCADE'),primary_key=True)
    names=db.Column(db.JSON,nullable=False,default=list)
    danger_levels=db.Column(db.JSON,nullable=False,default=list)
    wealth_levels=db.Column(db.JSON,nullable=False,default=list)
    combinations=db.Column(db.JSON,nullable=False,default=list)
    revision=db.Column(db.Integer,nullable=False,default=1)
    __mapper_args__={'version_id_col':revision}


class NpcCreationReceipt(db.Model):
    __tablename__='npc_creation_receipts'

    operation_id=db.Column(db.String(80),primary_key=True)
    lobby_id=db.Column(db.Integer,db.ForeignKey('lobbies.id',ondelete='CASCADE'),nullable=False)
    character_id=db.Column(db.Integer,db.ForeignKey('lobby_characters.id',ondelete='CASCADE'),nullable=False)
    payload_hash=db.Column(db.String(64),nullable=False)

    character=db.relationship('LobbyCharacter')
