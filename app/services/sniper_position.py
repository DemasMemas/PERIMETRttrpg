"""Обязательная смена укрытия: срок, доступный путь и подсказка владельцу."""
import math

from sqlalchemy.orm.attributes import flag_modified
from app.models import LocationObject,LocationCombatState
from app.services.exceptions import ValidationError


def status(actor,state=None):
    from app.services.combat import CombatService as C
    from app.services.character_rules import meta
    current=meta(actor);pending=current.get('sniperOldCover')
    if not pending:return None
    state=state or LocationCombatState.query.filter_by(location_id=actor.location_id).first()
    if not state or state.status!='active' or (actor.cover_object_id and actor.cover_object_id!=pending.get('id')) or (state.round_number or 1)>pending.get('expires_after_round',0):
        current.pop('sniperOldCover',None);flag_modified(actor.character,'data');return None
    mode='walk' if (actor.strenuous_movement_blocked_until_round or 0)>=(state.round_number or 1) else 'run'
    try:
        C._validate_equipment_movement(actor.character.data,mode)
        C._validate_posture_movement(actor.posture,mode,actor.character.data)
    except ValidationError:mode='walk'
    maximum=C._movement_distance_limit(mode,actor.posture or 'standing',C._movement_evasion_active(actor),C._movement_penalty(actor),actor.character.data)
    # Считаем полную дальность режима, а не остаток ОП после других действий.
    from app.services.location_navigation import level_of
    candidates=[]
    for cover in LocationObject.query.filter_by(location_id=actor.location_id).all():
        if cover.id==pending.get('id') or not C._is_cover_object(cover) or C._cover_profile(cover)['hp']<=0:continue
        for x,y in C._object_footprint_tiles(cover):
            for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
                px,py=x+dx,y+dy
                if not (0<=px<actor.location.grid_width and 0<=py<actor.location.grid_height):continue
                candidates.append((math.hypot(px-actor.pos_x,py-actor.pos_y),cover.id,px,py,level_of(cover)))
    best=None;checked={};search_context={}
    for lower,cover_id,x,y,level in sorted(set(candidates)):
        if best and lower>=best['distance']-1e-8:break
        if lower>maximum+1e-8:break
        key=(x,y,level)
        if key not in checked:
            checked[key]=C._find_movement_path(actor.location,actor.pos_x,actor.pos_y,x,y,moving_character_id=actor.character_id,start_level=actor.floor_level,end_level=level,_search_context=search_context)
        route=checked[key]
        if route and route['distance']<=maximum+1e-8 and (best is None or route['distance']<best['distance']):
            best={'object_id':cover_id,'x':x,'y':y,'floor_level':level,'distance':route['distance']}
    return {'blocked':bool(best),'cover':best,'mode':mode,'maximum_distance':maximum,'expires_after_round':pending['expires_after_round']}


def require_reposition(actor,state=None):
    result=status(actor,state)
    if result and result['blocked']:
        raise ValidationError('Снайпер: перед выстрелом займите другое укрытие или дождитесь конца следующего раунда')


def expire(location_id,state):
    from app.models import LocationCharacter
    from app.services.character_rules import meta
    for actor in LocationCharacter.query.filter_by(location_id=location_id).all():
        if not actor.character:continue
        current=meta(actor);pending=current.get('sniperOldCover')
        if pending and (state.status!='active' or (state.round_number or 1)>pending.get('expires_after_round',0)):
            current.pop('sniperOldCover',None);flag_modified(actor.character,'data')
