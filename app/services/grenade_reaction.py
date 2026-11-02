"""Персональные три секунды до взрыва, без добавления ресурсов хода."""
from copy import deepcopy

from flask import has_app_context
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db

from app.models import LocationCharacter,LocationCombatState,Location,LocationObject


def window(actor):
    return (((actor.character.data or {}).get('health') or {}).get('combatMeta') or {}).get('grenadeReaction') or {}


def _meta(actor):
    return actor.character.data.setdefault('health',{}).setdefault('combatMeta',{})


def prepare_event(state,event):
    from app.services.combat import CombatService
    people=CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=state.location_id).all())
    from app.services.explosion_choice import candidates
    event.update(trigger='short_fuse',remaining_actor_ids=[c.id for c in people if c.id in (state.turn_order or [])])
    event['gm_choices']=candidates(state,event)
    return event


def _resolve_for_actor(state,event,actor):
    from app.services.combat import CombatService
    alive=CombatService._character_condition(actor.character.data or {})['state']!='dead'
    result=CombatService.resolve_explosion(state.location_id,event['x'],event['y'],event['profile'],
        round_number=state.round_number,floor_level=event.get('floor_level',0),target_ids=[actor.id],
        damage_objects=False,create_area=False,live_shield_ids=event.get('blast_casualties',[]))
    if alive and CombatService._character_condition(actor.character.data or {})['state']=='dead':
        event.setdefault('blast_casualties',[]).append(actor.id)
    return result


def _finish_events(state,events):
    from app.services.combat import CombatService
    remaining=[];detonations=[]
    present={c.id for c in LocationCharacter.query.filter_by(location_id=state.location_id).all()}
    for event in events:
        if event.get('trigger')=='short_fuse':
            event['remaining_actor_ids']=[i for i in event.get('remaining_actor_ids',[]) if i in present]
        if event.get('trigger')!='short_fuse' or event.get('remaining_actor_ids') or event.get('gm_choices'):
            remaining.append(event);continue
        result=CombatService.resolve_explosion(state.location_id,event['x'],event['y'],event['profile'],
            round_number=state.round_number,floor_level=event.get('floor_level',0),target_ids=[])
        if result.get('area'):
            result['area']['excluded_actor_ids']=event.get('excluded_actor_ids',[])
            state.area_effects=[*(state.area_effects or []),result['area']]
        detonations.append({'item_name':event['item_name'],'detonated':True,
            'impact':{'x':event['x'],'y':event['y']},'explosion':result})
    state.pending_explosives=remaining
    return detonations


def begin(actor,state=None):
    from app.services.combat import CombatService
    if not has_app_context() or not getattr(actor,'location_id',None):
        return []
    state=state or LocationCombatState.query.filter_by(location_id=actor.location_id).first()
    if not state or state.status!='active' or state.current_location_character_id!=actor.id or state.reaction_return_location_character_id is not None or window(actor):
        return []
    if any(e.get('gm_choices') for e in state.pending_explosives or []):
        return []
    events=deepcopy(state.pending_explosives or []);waiting=[];detonations=[]
    from app.services.explosion_geometry import distance
    location=db.session.get(Location,state.location_id)
    objects=LocationObject.query.filter_by(location_id=state.location_id).all()
    for event in events:
        if event.get('trigger')!='short_fuse':
            continue
        for other_id in list(event.get('remaining_actor_ids',[])):
            if other_id in (state.turn_order or []):
                continue
            # Удалённый из инициативы участник также требует решения ГМа.
            event['remaining_actor_ids'].remove(other_id)
            other=LocationCharacter.query.filter_by(id=other_id,location_id=state.location_id).first()
            if other:
                from app.services.explosion_choice import candidates
                if other_id in candidates(state,event):
                    event.setdefault('gm_choices',[]).append(other_id)
        if event.get('gm_choices'):
            continue
        if actor.id not in event.get('remaining_actor_ids',[]):
            continue
        within=distance(location,objects,event['x'],event['y'],event.get('floor_level',0),actor)<=event['profile'].get('radius',0)
        if within and CombatService._can_take_combat_turn(actor):
            waiting.append(event['id']);continue
        event['remaining_actor_ids'].remove(actor.id)
        if within:
            result=_resolve_for_actor(state,event,actor)
            detonations.append({'item_name':event['item_name'],'detonated':True,
                'impact':{'x':event['x'],'y':event['y']},'explosion':result})
    state.pending_explosives=events
    if waiting:
        held_ap=max(0,actor.action_points_current-2)
        held_op=max(0,actor.movement_points_current)/2
        _meta(actor)['grenadeReaction']={'event_ids':waiting,'held_action_points':held_ap,'held_movement_points':held_op}
        actor.action_points_current-=held_ap
        actor.movement_points_current-=held_op
        flag_modified(actor.character,'data')
    detonations.extend(_finish_events(state,events))
    return detonations


def split_movement_gain(actor,gain):
    active=window(actor)
    if not active:
        return gain
    active['held_movement_points']+=gain/2
    flag_modified(actor.character,'data')
    return gain/2


def finish(actor,state):
    active=window(actor)
    if not active:
        return []
    events=deepcopy(state.pending_explosives or []);detonations=[]
    for event in events:
        if event.get('id') not in active['event_ids'] or actor.id not in event.get('remaining_actor_ids',[]):
            continue
        event['remaining_actor_ids'].remove(actor.id)
        result=_resolve_for_actor(state,event,actor)
        detonations.append({'item_name':event['item_name'],'detonated':True,
            'impact':{'x':event['x'],'y':event['y']},'explosion':result})
    actor.action_points_current+=active['held_action_points']
    actor.movement_points_current+=active['held_movement_points']
    _meta(actor).pop('grenadeReaction',None)
    flag_modified(actor.character,'data')
    detonations.extend(_finish_events(state,events))
    return detonations
