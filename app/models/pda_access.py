"""Долговечный сеанс доступа к конкретному физическому КПК."""
from datetime import datetime,timezone
from sqlalchemy import event,inspect
from sqlalchemy.orm import Session
from app.extensions import db
from app.utils.defaults import empty_dict,empty_list


class PdaAccessSession(db.Model):
    __tablename__='pda_access_sessions'
    id=db.Column(db.String(100),primary_key=True)
    lobby_id=db.Column(db.Integer,db.ForeignKey('lobbies.id',ondelete='CASCADE'),nullable=False,index=True)
    source_character_id=db.Column(db.Integer,db.ForeignKey('lobby_characters.id',ondelete='SET NULL'),nullable=True)
    source_device_id=db.Column(db.String(100),nullable=False,index=True)
    target_device_id=db.Column(db.String(100),nullable=False)
    mode=db.Column(db.String(16),nullable=False)
    status=db.Column(db.String(16),nullable=False,default='working')
    remaining_seconds=db.Column(db.Float,nullable=False)
    created_game_time=db.Column(db.Integer,nullable=False)
    created_at=db.Column(db.DateTime,nullable=False,default=lambda:datetime.now(timezone.utc))
    notified=db.Column(db.Boolean,nullable=False,default=False)
    data=db.Column(db.JSON,nullable=False,default=empty_dict)
    receipts=db.Column(db.JSON,nullable=False,default=empty_list)
    revision=db.Column(db.Integer,nullable=False,default=1,server_default='1')
    __mapper_args__={'version_id_col':revision}


@event.listens_for(Session,'before_flush')
def check_changed_connections(session,flush_context,instances):
    from app.models import LobbyCharacter,LocationCharacter,LocationObject,WorldGroup,Location
    if session.info.get('pda_access_check'):return
    worlds=set()
    for row in session.dirty|session.deleted|session.new:
        if isinstance(row,LobbyCharacter):
            if row in session.deleted or inspect(row).attrs.data.history.has_changes():worlds.add(row.lobby_id)
        elif isinstance(row,WorldGroup):
            if row in session.deleted or any(inspect(row).attrs[key].history.has_changes() for key in ('tile_x','tile_y','member_character_ids')):worlds.add(row.lobby_id)
        elif isinstance(row,(LocationCharacter,LocationObject)):
            keys=('pos_x','pos_y','floor_level','location_id','last_action') if isinstance(row,LocationCharacter) else ('tile_x','tile_y','location_id','properties')
            if row not in session.deleted and row not in session.new and not any(inspect(row).attrs[key].history.has_changes() for key in keys):continue
            location=session.get(Location,row.location_id)
            if location:worlds.add(location.lobby_id)
    if not worlds:return
    from app.services.pda_access import invalidate_connections,enabled
    session.info['pda_access_check']=True
    try:
        with session.no_autoflush:
            if not enabled():return
            active={world for (world,) in session.query(PdaAccessSession.lobby_id).filter(PdaAccessSession.lobby_id.in_(worlds),PdaAccessSession.status.in_(['working','active'])).distinct()}
            for world in active:invalidate_connections(world)
    finally:session.info.pop('pda_access_check',None)
