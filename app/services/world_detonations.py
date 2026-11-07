"""Поражение на глобальной карте тем же расчётом взрыва, что и на подлокации."""
from types import SimpleNamespace
import math
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCharacter,DetonationEvent,LobbyCharacter,Lobby
from app.services.exceptions import ValidationError,NotFoundError,PermissionDenied,ConflictError
from app.services.transaction import atomic_operation
from app.services.health import health_zones_to_location


def apply_to_character(character,profile,distance=0,cover_percent=0):
    from app.services.combat import CombatService
    from app.services.character import CharacterService
    maximum=max(float(profile.get('radius',0)),float(profile.get('noise',0))/10)
    if type(distance) not in {int,float} or not math.isfinite(distance) or not 0<=distance<=maximum:
        raise ValidationError('Дистанция поражения вне радиуса взрыва')
    if type(cover_percent) not in {int,float} or not math.isfinite(cover_percent) or not 0<=cover_percent<=100:
        raise ValidationError('Защита укрытия должна быть от 0 до 100%')
    size=max(5,math.ceil(maximum)*2+5);center=size//2
    location=SimpleNamespace(id=None,lobby_id=character.lobby_id,lobby=character.lobby,
        grid_width=size,grid_height=size,tiles_data=[[{'height':0,'terrain':'dirt','objects':[]}]*size]*size)
    health=(character.data or {}).get('health') or {}
    actor=LocationCharacter(id=-character.id,character_id=character.id,character=character,location_id=None,
        pos_x=center+distance,pos_y=center,floor_level=0,posture='standing',facing_x=0,facing_y=1,
        action_points_current=0,movement_points_current=0,free_actions_current=0,hp_zones=health_zones_to_location(health),effects=[])
    context={'location':location,'objects':[],'people':[actor],'cover_percent':cover_percent}
    result=CombatService.resolve_explosion(None,center,center,profile,target_ids=[actor.id],damage_objects=False,scene_context=context)
    # Для носителя газового заряда сразу обрабатывается первое попадание в облако.
    area=result.get('area')
    if area and area.get('type')=='gas':
        exposure=CombatService._apply_area_effect_to_character(area,actor,0,geometry_context=(location,[]))
        if exposure:result['targets'].append(exposure)
    CharacterService.sync_location_health(character,character.data.setdefault('health',{}),notify_observed=False)
    flag_modified(character,'data')
    return result


def project(event):
    return {'id':event.id,'revision':event.revision,'name':event.profile.get('name','Заряд'),
        'game_time':event.game_time,'carrier_id':event.carrier_id,'closed':event.closed,
        'results':event.results,'affected_character_ids':event.affected_character_ids,
        'radius':max(float(event.profile.get('radius',0)),float(event.profile.get('noise',0))/10)}


def pending(lobby_id,user_id):
    lobby=db.session.get(Lobby,lobby_id)
    if not lobby:raise NotFoundError('Мир не найден')
    if lobby.gm_id!=user_id:raise PermissionDenied('Последствия взрыва выбирает ГМ')
    from sqlalchemy import inspect
    if not inspect(db.session.connection()).has_table('detonation_events'):return []
    return [project(event) for event in DetonationEvent.query.filter_by(lobby_id=lobby_id,location_id=None,closed=False).order_by(DetonationEvent.game_time,DetonationEvent.id).all()]


@atomic_operation
def add_target(lobby_id,user_id,event_id,payload):
    pending(lobby_id,user_id)
    event=DetonationEvent.query.filter_by(id=event_id,lobby_id=lobby_id,location_id=None).with_for_update().first()
    if not event:raise NotFoundError('Взрыв не найден')
    if not isinstance(payload,dict):raise ValidationError('Выберите последствие взрыва')
    target_id=payload.get('character_id')
    # Повторное подтверждение цели возвращает прежний результат без нового урона.
    if target_id in (event.affected_character_ids or []):return project(event)
    if event.closed:raise ConflictError('Последствия взрыва уже завершены')
    if payload.get('revision')!=event.revision:raise ConflictError('Список пострадавших изменился')
    if payload.get('operation')=='close':event.closed=True
    elif payload.get('operation')=='add_target':
        target=LobbyCharacter.query.filter_by(id=target_id,lobby_id=lobby_id).with_for_update().first()
        if not target:raise NotFoundError('Персонаж не найден в этом мире')
        result=apply_to_character(target,event.profile,payload.get('distance',0),payload.get('cover_percent',0))
        event.results=[*(event.results or []),{'character_id':target.id,'name':target.name,'result':result}]
        event.affected_character_ids=[*(event.affected_character_ids or []),target.id]
    else:raise ValidationError('Неизвестное действие с последствиями взрыва')
    db.session.flush()
    return project(event)
