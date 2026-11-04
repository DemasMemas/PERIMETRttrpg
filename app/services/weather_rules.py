"""Согласованные состояния погоды, наружные эффекты и изменение мирового времени."""
from copy import deepcopy
import math
import random


def intensity(settings,key):
    value=(settings or {}).get(key) or {}
    try:number=float(value.get('intensity',.5))
    except (TypeError,ValueError):number=.5
    return bool(value.get('enabled')),max(0,min(1,number if math.isfinite(number) else .5))


def profile(settings):
    clouds,clarity=intensity(settings,'sun');rain,water=intensity(settings,'rain');fog,density=intensity(settings,'fog')
    storm=rain and water>.5
    cloudy=clouds and clarity<=.5
    fog_penalty=(-4 if density>.5 else -2) if fog else 0
    return {'cloudy':cloudy,'rain':rain and not storm,'storm':storm,'fog':fog,
        'accuracy':fog_penalty+(-2 if rain and not storm else 0),
        'awareness':fog_penalty,'visual_awareness':fog_penalty-int(cloudy),
        'accuracy_disadvantage':storm,'awareness_disadvantage':storm,
        'radiation_multiplier':2 if storm else 1.5 if rain else 1,
        'revealed_anomalies':['electric']+(['gravity'] if not storm else []) if rain else []}


def outdoors(location,x,y,level=0,objects=None):
    from app.models import LocationObject
    from app.services.location_navigation import contains,level_of
    objects=LocationObject.query.filter_by(location_id=location.id).all() if objects is None else objects
    # Та же граница помещения, что у взрыва: пол здания на соответствующем этаже.
    return not any(o.type=='floor' and (o.properties or {}).get('building_id')
        and level_of(o)==level and contains(o,x+.5,y+.5) for o in objects)


def at_character(location,actor,objects=None):
    if location is None or getattr(location,'lobby',None) is None:
        return {**profile({}),'outdoors':False}
    result=profile(location.lobby.weather_settings)
    exposed=outdoors(location,actor.pos_x,actor.pos_y,getattr(actor,'floor_level',0) or 0,objects)
    if not exposed:result=profile({})
    return {**result,'outdoors':exposed}


def change(lobby,amount,*,rng=None):
    rng=rng or random
    key=rng.choice(('sun','rain','fog'));direction=rng.choice((-1,1))
    settings=deepcopy(lobby.weather_settings or {});_,before=intensity(settings,key)
    after=round(max(0,min(1,before+direction*amount)),1)
    settings[key]={**(settings.get(key) or {}),'enabled':True,'intensity':after}
    lobby.weather_settings=settings
    lobby._weather_changed=True
    reveal(lobby)
    return {'parameter':key,'before':before,'after':after,'delta':round(after-before,1)}


def advance(lobby,before_minutes,after_minutes):
    """Проверка ровно на каждой пересечённой границе игрового часа, включая отдых."""
    changes=[change(lobby,.1) for _ in range(max(0,int(after_minutes//60)-int(before_minutes//60)))]
    return changes


def emit(lobby,*,force=False):
    if not force and not getattr(lobby,'_weather_changed',False):return
    from app import socketio
    from app.models import Location,LocationObject
    from app.extensions import db
    from app.schemas.location import LocationObjectSchema
    for object_id in getattr(lobby,'_weather_revealed_objects',set()):
        obj=db.session.get(LocationObject,object_id)
        if obj:socketio.emit('location_object_updated',{'location_id':obj.location_id,'object':LocationObjectSchema().dump(obj)},room=f'location_{obj.location_id}')
    for location_id,x,y in getattr(lobby,'_weather_revealed_tiles',set()):
        loc=db.session.get(Location,location_id)
        if loc:socketio.emit('location_tiles_updated',{'location_id':location_id,'updates':[{'x':x,'z':y,'objects':loc.tiles_data[y][x].get('objects',[])}]},room=f'location_{location_id}')
    lobby._weather_revealed_objects=set();lobby._weather_revealed_tiles=set()
    lobby._weather_changed=False
    socketio.emit('weather_updated',lobby.weather_settings,room=f'lobby_{lobby.id}')


def should_reveal(location,obj,x=None,y=None,objects=None):
    from app.services.combat import CombatService
    from app.services.anomaly_profiles import anomaly_profile
    from app.services.location_navigation import level_of
    families=profile(location.lobby.weather_settings)['revealed_anomalies']
    rule=anomaly_profile(CombatService._anomaly_key_from_object(obj)) or {}
    if rule.get('category') not in families:return False
    if not isinstance(obj,dict):x,y=obj.tile_x,obj.tile_y
    return outdoors(location,x,y,level_of(obj),objects)


def unhide(location,obj,x=None,y=None,objects=None):
    props=obj if isinstance(obj,dict) else obj.properties or {}
    kind=obj.get('type',obj.get('object_type')) if isinstance(obj,dict) else obj.type
    if kind!='anomaly' or not props.get('gm_hidden') or not should_reveal(location,obj,x,y,objects):return False
    if isinstance(obj,dict):obj['gm_hidden']=False
    else:obj.properties={**props,'gm_hidden':False}
    return True


def unhide_tile(location,tile,x,y,objects=None):
    changed=False
    for obj in tile.get('objects') or []:
        if isinstance(obj,dict):changed=unhide(location,obj,x,y,objects) or changed
    return changed


def reveal(lobby):
    from app.models import Location,LocationObject
    from app.services.combat import CombatService
    from sqlalchemy.orm.attributes import flag_modified
    if not profile(lobby.weather_settings)['revealed_anomalies']:return
    object_ids=getattr(lobby,'_weather_revealed_objects',set());tiles=getattr(lobby,'_weather_revealed_tiles',set())
    for location in Location.query.filter_by(lobby_id=lobby.id).all():
        objects=LocationObject.query.filter_by(location_id=location.id).all()
        for obj,x,y,entry in CombatService._anomaly_candidates(location):
            props=obj if isinstance(obj,dict) else obj.properties or {}
            if not props.get('gm_hidden') or not should_reveal(location,obj,x,y,objects):continue
            if isinstance(obj,dict):
                obj['gm_hidden']=False;flag_modified(location,'tiles_data');tiles.add((location.id,x,y))
            else:obj.properties={**props,'gm_hidden':False};object_ids.add(obj.id)
    lobby._weather_revealed_objects=object_ids;lobby._weather_revealed_tiles=tiles
