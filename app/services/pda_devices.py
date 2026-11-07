"""Физические КПК в руках носителей и внутри объектов: без выдачи чужого листа."""
import hashlib
import json
import math
from datetime import datetime,timezone
from types import SimpleNamespace
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import joinedload
from app.extensions import db
from app.models import LobbyCharacter,LocationObject,Location,LocationCharacter,WorldGroup
from app.services.portable_lights import walk,definition,pda_state
from app.services.pda_network import mods,distance,blocked,BASE_RANGE,ENHANCED_RANGE
from app.services.pda_accounts import account_for_device


def token(lobby_id,device_id):return hashlib.sha256(json.dumps([lobby_id,device_id]).encode()).hexdigest()


def registry(lobby_id):
    from app.services.pda_network import _position
    from app.services.location_navigation import ground,level_of
    result=[];session=db.session()
    people=list(LobbyCharacter.query.filter_by(lobby_id=lobby_id).all())
    people.extend(row for row in session.new if isinstance(row,LobbyCharacter) and row.lobby_id==lobby_id)
    groups=[row for row in WorldGroup.query.filter_by(lobby_id=lobby_id).order_by(WorldGroup.id).all() if row not in session.deleted]
    groups.extend(row for row in session.new if isinstance(row,WorldGroup) and row.lobby_id==lobby_id and row not in groups)
    models=[row for row in LocationCharacter.query.join(LobbyCharacter,LobbyCharacter.id==LocationCharacter.character_id).filter(
        LobbyCharacter.lobby_id==lobby_id).options(joinedload(LocationCharacter.location)).order_by(LocationCharacter.last_action.desc(),LocationCharacter.id.desc()).all() if row not in session.deleted]
    models=[row for row in session.new if isinstance(row,LocationCharacter) and row.character_id in {person.id for person in people}]+models
    def last_placement(row):
        stamp=row.last_action
        # До flush боевое действие может хранить SQL-выражение func.now(), а не дату.
        value=stamp.replace(tzinfo=timezone.utc).timestamp() if isinstance(stamp,datetime) else 0 if stamp is None else float('inf')
        return (float('inf') if row in session.new else value,row.id or 0)
    models.sort(key=last_placement,reverse=True)
    models=[SimpleNamespace(character_id=row.character_id,location_id=row.location_id,pos_x=row.pos_x,pos_y=row.pos_y,
        floor_level=row.floor_level,location=session.get(Location,row.location_id)) for row in models]
    models=[row for row in models if row.location and row.location.lobby_id==lobby_id]
    groups_by_member={}
    for group in groups:
        for identity in group.member_character_ids or []:
            if str(identity).isdigit():groups_by_member.setdefault(int(identity),group)
    for character in people:
        if character in session.deleted:continue
        devices=[(path,item) for path,item in walk(character.data or {}) if definition(item)=='pda' and item.get('quantity',1)==1 and isinstance(item.get('id'),str)]
        if not devices:continue
        placements=[model for model in models if model.character_id==character.id];group=groups_by_member.get(character.id)
        local=next((model for model in placements if not group or (model.location.world_tile_x,model.location.world_tile_z)==(group.tile_x,group.tile_y)),None)
        position=_position(character,local.location_id if local else None,groups,placements)
        for path,item in devices:
            result.append({'owner':character,'field':'data','character':character,'host':f'character:{character.id}',
                'item':item,'path':path,'position':position,'lobby_id':lobby_id,'lobby':character.lobby})
    objects=list(LocationObject.query.join(LocationObject.location).filter_by(lobby_id=lobby_id).all())
    objects.extend(row for row in session.new if isinstance(row,LocationObject) and row not in objects)
    for obj in objects:
        if obj in session.deleted:continue
        location=session.get(Location,obj.location_id)
        if not location or location.lobby_id!=lobby_id:continue
        devices=[(path[2:],item) for path,item in walk({'inventory':{'contents':(obj.properties or {}).get('contents') or []}})
            if definition(item)=='pda' and item.get('quantity',1)==1 and isinstance(item.get('id'),str)]
        if not devices:continue
        level=level_of(obj);position={'world_x':(location.world_tile_x+.5)*100-location.grid_width/2+obj.tile_x+.5,
            'world_y':(location.world_tile_z+.5)*100-location.grid_height/2+obj.tile_y+.5,
            'height':ground(location,obj.tile_x,obj.tile_y)+level*2.4,'location_id':location.id,
            'x':obj.tile_x,'y':obj.tile_y,'level':level,'tile_x':location.world_tile_x,'tile_y':location.world_tile_z}
        for path,item in devices:
            result.append({'owner':obj,'field':'properties','character':None,'host':f'object:{obj.id}',
                'item':item,'path':['contents',*path],'position':position,'lobby_id':lobby_id,'lobby':location.lobby})
    counts={}
    for record in result:counts[record['item']['id']]=counts.get(record['item']['id'],0)+1
    for record in result:
        record.update(id=record['item']['id'],token=token(lobby_id,record['item']['id']),mods=mods(record['item']),
            ambiguous=counts[record['item']['id']]>1)
        record['range']=ENHANCED_RANGE if 'pda-b10' in record['mods'] else BASE_RANGE
    return result


def save(record):flag_modified(record['owner'],record['field'])


def powered(record):
    state=pda_state(record['item'])
    return state['on'] and state['charge_percent']>0


def cipher(record):
    return max([0,*[level for rule,level in [('pda-b3',1),('pda-b12',2),('pda-b20',3)] if rule in record['mods']]])


def decipher(record):
    return max([0,*[level for rule,level in [('pda-b11',1),('pda-b19',2),('pda-b26',3)] if rule in record['mods']]])


def radio_device(record):
    return {**record,'character':record['character'] or SimpleNamespace(id=record['host'],lobby_id=record['lobby_id'],lobby=record['lobby'])}


def reaches(source,target,records):
    if not source['position'] or not target['position'] or not powered(source) or not powered(target):return False
    devices=[radio_device(record) for record in records if powered(record) and record['position'] and not record['ambiguous']]
    return not blocked(radio_device(source),devices) and not blocked(radio_device(target),devices) and distance(source['position'],target['position'])<=source['range']+1e-7


def cable_near(source,target):
    if source['host']==target['host']:return True
    first,last=source['position'],target['position']
    return bool(first and last and first['location_id'] and first['location_id']==last['location_id']
        and first['level']==last['level'] and math.hypot(first['x']-last['x'],first['y']-last['y'])<=1.5+1e-7)


def account(record):
    character=record['character'] or SimpleNamespace(id=None,lobby_id=record['lobby_id'])
    return account_for_device(character,record['item'],**({'legacy':{}} if not record['character'] else {}))


def label(record):
    logged=account(record)
    return f'КПК · {logged.login}' if logged else 'КПК · гость'
