"""Решение ГМа о целях взрыва, отсутствующих в инициативе."""
from copy import deepcopy
import uuid

from app.extensions import db
from app.models import LocationCharacter, LocationCombatState, Location, LocationObject
from app.services.exceptions import ConflictError, PermissionDenied, ValidationError


def candidates(state, event):
    from app.services.combat import CombatService
    from app.services.explosion_geometry import distance
    location=db.session.get(Location,state.location_id)
    objects=LocationObject.query.filter_by(location_id=state.location_id).all()
    return [c.id for c in CombatService._unique_location_characters(
        LocationCharacter.query.filter_by(location_id=state.location_id).all())
        if c.id not in (state.turn_order or []) and c.character
        and CombatService._character_condition(c.character.data or {})['state']!='dead'
        and distance(location,objects,event['x'],event['y'],event.get('floor_level',0),c)<=event['profile'].get('radius',0)]


def require_resolved(state):
    if state and any(e.get('gm_choices') for e in state.pending_explosives or []):
        raise ConflictError('ГМ должен выбрать участников воздействия взрыва')


def hold(location_id,x,y,profile,floor_level):
    state=LocationCombatState.query.filter_by(location_id=location_id).first()
    if not state or state.status!='active':
        return False
    event={'id':uuid.uuid4().hex,'trigger':'gm_explosion','item_name':profile.get('name') or profile.get('label'),
           'x':x,'y':y,'profile':deepcopy(profile),'floor_level':floor_level}
    event['gm_choices']=candidates(state,event)
    if not event['gm_choices']:
        return False
    state.pending_explosives=[*(state.pending_explosives or []),event]
    return True


def decide(location_id,user_id,event_id,actor_id,choice):
    from app.services.combat import CombatService
    from app.services.grenade_reaction import begin
    location=CombatService._get_location(location_id)
    if location.lobby.gm_id!=user_id:
        raise PermissionDenied('Только ГМ выбирает участников взрыва')
    if choice not in {'include','exclude'}:
        raise ValidationError('Неизвестное решение об участии во взрыве')
    state=LocationCombatState.query.filter_by(location_id=location_id).first()
    if not state or state.status!='active':
        raise ConflictError('Бой уже завершён')
    expected=deepcopy(state.pending_explosives or [])
    events=deepcopy(expected)
    event=next((e for e in events if e.get('id')==event_id),None)
    if not event or actor_id not in event.get('gm_choices',[]):
        raise ConflictError('Решение уже принято или цель недоступна')
    actor=LocationCharacter.query.filter_by(id=actor_id,location_id=location_id).first()
    if choice=='include' and actor:
        if actor_id not in (state.turn_order or []):
            CombatService.add_combat_participant(location_id,user_id,actor_id,_commit=False,_allow_incapacitated=True)
        if event['trigger']=='short_fuse':
            event.setdefault('remaining_actor_ids',[]).append(actor_id)
    else:
        event.setdefault('excluded_actor_ids',[]).append(actor_id)
    event['gm_choices'].remove(actor_id)
    # Два окна ГМа не должны применить одно решение и один урон дважды.
    claimed=LocationCombatState.query.filter_by(id=state.id).filter(
        LocationCombatState.pending_explosives==expected).update(
            {LocationCombatState.pending_explosives:events},synchronize_session=False)
    if claimed!=1:
        raise ConflictError('Состав участников взрыва уже изменился; обновите состояние боя')
    state.pending_explosives=events
    detonations=[]
    for ready in list(events):
        if ready.get('trigger')!='gm_explosion' or ready.get('gm_choices'):
            continue
        events.remove(ready)
        result=CombatService.resolve_explosion(location_id,ready['x'],ready['y'],ready['profile'],
            floor_level=ready.get('floor_level',0),round_number=state.round_number,
            target_ids=[i for i in state.turn_order or [] if i not in ready.get('excluded_actor_ids',[])])
        if result.get('area'):
            result['area']['excluded_actor_ids']=ready.get('excluded_actor_ids',[])
            state.area_effects=[*(state.area_effects or []),result['area']]
        detonations.append({'item_name':ready['item_name'],'impact':{'x':ready['x'],'y':ready['y']},
                            'detonated':True,'explosion':result})
    state.pending_explosives=events
    current=db.session.get(LocationCharacter,state.current_location_character_id)
    if current:
        detonations.extend(begin(current,state))
    db.session.commit()
    payload=CombatService._serialize_state(location,state)
    payload['detonations']=detonations
    return payload
