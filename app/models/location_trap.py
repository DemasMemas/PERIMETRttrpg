"""Скрытое игровое состояние ловушек отдельно от общедоступной геометрии."""
from app.extensions import db
from app.utils.defaults import empty_dict,empty_list


class LocationTrap(db.Model):
    __tablename__='location_traps'
    id=db.Column(db.Integer,primary_key=True)
    location_id=db.Column(db.Integer,db.ForeignKey('locations.id',ondelete='CASCADE'),nullable=False,index=True)
    x=db.Column(db.Integer,nullable=False)
    y=db.Column(db.Integer,nullable=False)
    level=db.Column(db.Integer,nullable=False,default=0)
    creator_id=db.Column(db.Integer,db.ForeignKey('lobby_characters.id',ondelete='SET NULL'),nullable=True)
    recipe_id=db.Column(db.String(40),nullable=False)
    status=db.Column(db.String(20),nullable=False,default='armed')
    detection_dc=db.Column(db.Integer,nullable=True)
    known_users=db.Column(db.JSON,nullable=False,default=empty_list)
    data=db.Column(db.JSON,nullable=False,default=empty_dict)
    revision=db.Column(db.Integer,nullable=False,default=1,server_default='1')
    __mapper_args__={'version_id_col':revision}
    location=db.relationship('Location',backref=db.backref('traps',cascade='all, delete-orphan',passive_deletes=True))
