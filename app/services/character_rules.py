"""События предысторий, требующие настоящего положения и участников боя."""
from copy import deepcopy
import math

from sqlalchemy.orm.attributes import flag_modified
from app.models import LocationCharacter,LocationCombatState,WorldGroup,LocationObject
from app.services.character_traits import background_is,trait_count,facial_profile
from app.services.exceptions import ValidationError


def meta(actor):
    return actor.character.data.setdefault('health',{}).setdefault('combatMeta',{})


def allied_ids(actor,people=None,groups=None):
    from app.services.combat import CombatService as C
    if people is None:people=LocationCharacter.query.filter_by(location_id=actor.location_id).all()
    if groups is None:groups=WorldGroup.query.filter_by(lobby_id=actor.character.lobby_id).all()
    party=set()
    for group in groups:
        members={int(value) for value in group.member_character_ids or [] if str(value).isdigit()}
        if actor.character_id in members:party.update(members)
    return {person.character_id for person in people if person.id!=actor.id and
            (not C._are_opponents(actor,person) or person.character_id in party and not (actor.team_name and person.team_name))}


def refresh_nearby(location_id,people=None):
    from app.services.combat import CombatService as C
    if people is None:people=LocationCharacter.query.filter_by(location_id=location_id).all()
    firefighter=next((person for person in people if person.character and background_is(person.character.data,'Пожарный')),None)
    groups=WorldGroup.query.filter_by(lobby_id=firefighter.character.lobby_id).all() if firefighter else []
    objects=LocationObject.query.filter_by(location_id=location_id).all() if firefighter else []
    from app.services.explosion_geometry import height
    heights={}
    def elevation(person):
        if person.id not in heights:heights[person.id]=height(person.location,objects,person.pos_x,person.pos_y,person.floor_level or 0)
        return heights[person.id]
    for actor in people:
        if not actor.character or not background_is(actor.character.data,'Пожарный'):continue
        friends=allied_ids(actor,people,groups)
        allies=[other for other in people if other.character and other.character_id in friends
                and C._character_condition(other.character.data)['state']!='dead'
                and math.hypot(other.pos_x-actor.pos_x,other.pos_y-actor.pos_y)<=15
                and math.sqrt((other.pos_x-actor.pos_x)**2+(other.pos_y-actor.pos_y)**2+(elevation(other)-elevation(actor))**2)<=15]
        meta(actor)['firefighterNearby']=len(allies)>=2
        flag_modified(actor.character,'data')


def evasion_penalty(data):
    return max(0,float(((data or {}).get('health') or {}).get('combatMeta',{}).get('standaloneEvasion') or 0))


def is_heavy_armor(data):
    from app.services.combat import CombatService as C
    armor=((data or {}).get('equipment') or {}).get('armor') or {}
    attrs=C._template_attributes(armor)
    category=armor.get('modificationCategory',armor.get('modification_category',attrs.get('modification_category','')))
    if not category and armor.get('name') in {'Бронекостюм','Бронекостюм Затвор','Комбинезон Страж','Бронекостюм Литейщик','Бронекостюм ПШНХ-2'}:category='Тяжелая'
    return str(category).strip().casefold().replace('ё','е') in {'тяжелая','экзоскелет'} or C._exoskeleton_power_profile(data)['is_exoskeleton']


def shotgun_effects(attacker,target,weapon,profile,zone,before,damage):
    from app.services.combat import CombatService as C
    data=attacker.character.data or {};target_data=target.character.data or {};results={}
    from app.services.inventory import normalize_caliber
    caliber=normalize_caliber(profile.get('caliber'))
    physical=str(profile.get('damage_type') or 'physical') in {'physical','ballistic','bullet','buckshot'}
    if trait_count(data,'Тяжелая навеска') and zone in {'chest','abdomen'} and caliber=='12x70' and physical and not is_heavy_armor(target_data):
        check=C._narrative_skill_check(target_data,'skills.physical.strength')
        check.update(difficulty=12,success=not check.get('automatic_failure') and check['total']>=12)
        if not check['success']:
            C._apply_fall_or_drop(target,target_data,'left_leg')
            C._apply_fall_or_drop(target,target_data,'left_arm')
        results['heavy_load']=check
    key={'left_arm':'leftArm','right_arm':'rightArm','left_leg':'leftLeg','right_leg':'rightLeg'}.get(zone)
    current=((target_data.get('health') or {}).get('zones') or {}).get(key,{})
    if key and damage>0 and before>0 and current.get('current',1)<=0 and trait_count(data,'Чернобыльский пыточник') and C._weapon_class(weapon)=='shotgun' and C._character_condition(target_data)['state'] not in {'dead','critical'}:
        heard=[]
        for other in C._unique_location_characters(LocationCharacter.query.filter_by(location_id=target.location_id).all()):
            if other.id==attacker.id or not other.character:continue
            listener=other.character.data or {}
            if C._character_condition(listener)['state'] in {'dead','critical'} or facial_profile(listener)['ears']==2 or C._deafness_level(listener)>=90:continue
            check=C._narrative_skill_check(listener,'skills.physical.will')
            check.update(character_id=other.character_id,difficulty=15,success=not check.get('automatic_failure') and check['total']>=15)
            if not check['success']:check['stress']=C.apply_stress_trigger(other,2,trigger='torturer_scream')
            heard.append(check)
        results['torturer_scream']=heard
    if results:
        target.character.data=target_data;flag_modified(target.character,'data')
    return results


def can_explode(actor):
    from app.services.combat import CombatService as C
    from app.services.character_traits import carried_weapons
    def visit(value):
        if isinstance(value,list):
            for item in value:yield from visit(item)
        elif isinstance(value,dict):
            yield value
            for key in ('contents','pouches','storedItem'):yield from visit(value.get(key))
    data=actor.character.data or {}
    roots=[data.get('inventory'),data.get('equipment')]
    for root in roots:
        for value in (root or {}).values():
            for item in visit(value):
                if item.get('category')=='grenade' and float(item.get('quantity',1) or 0)>0 and C._explosive_key(item.get('name')):return True
    for weapon in carried_weapons(data):
        if C._weapon_class(weapon)=='grenade_launcher' and C._weapon_loaded_shots(weapon)>0:return True
        if any(m.get('loadedGrenade') for m in weapon.get('installedModules') or []):return True
    return False


def start_turn(actor):
    from app.services.combat import CombatService as C
    if not getattr(actor,'location_id',None):return
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).first()
    if not state or state.status!='active':return
    current=meta(actor)
    if background_is(actor.character.data,'Гренадир') and not current.get('combatExplosionOccurred') and can_explode(actor):
        check=C._narrative_skill_check(actor.character.data,'skills.physical.will')
        check.update(difficulty=12,success=not check.get('automatic_failure') and check['total']>=12)
        current['grenadirCheck']=check;current['grenadirMustExplode']=not check['success']
    flag_modified(actor.character,'data')


def explosion_occurred(location_id):
    for actor in LocationCharacter.query.filter_by(location_id=location_id).all():
        if actor.character:
            meta(actor)['combatExplosionOccurred']=True;meta(actor).pop('grenadirMustExplode',None)
            flag_modified(actor.character,'data')


def harm_check(attacker,target,ranged):
    """Успешное разрешение хранится на цель до конца боя; провал можно повторить с новой оплатой."""
    from app.services.combat import CombatService as C
    data=attacker.character.data or {};other=target.character.data or {}
    if C._is_mutant_character(other):return None
    difficulty=15 if background_is(data,'Истинный пацифист') else 8 if ranged and background_is(data,'Контрактник') and 'Военный' in ((other.get('basic') or {}).get('tags') or []) else None
    if difficulty is None:return None
    allowed=meta(attacker).setdefault('harmAllowedTargets',[])
    if target.character_id in allowed:return None
    check=C._narrative_skill_check(data,'skills.physical.will')
    check.update(difficulty=difficulty,success=not check.get('automatic_failure') and check['total']>=difficulty)
    if check['success']:allowed.append(target.character_id)
    flag_modified(attacker.character,'data')
    return check


def mark_hunt(actor):
    if not actor or not actor.character or not background_is(actor.character.data,'Охотник'):return
    meta(actor)['hunterActivityDay']=actor.location.lobby.game_day or 1
    flag_modified(actor.character,'data')


def after_kill(attacker,target,was_alive):
    from app.services.combat import CombatService as C
    if not was_alive or C._character_condition(target.character.data)['state']!='dead':return
    if C._is_mutant_character(target.character.data):mark_hunt(attacker)
    if background_is(attacker.character.data,'Снайпер'):
        check=C._narrative_skill_check(attacker.character.data,'skills.physical.will')
        check.update(difficulty=8,success=not check.get('automatic_failure') and check['total']>=8)
        meta(attacker)['sniperPositionCheck']=check
        if not check['success']:
            state=LocationCombatState.query.filter_by(location_id=attacker.location_id).first()
            if state and state.status=='active':
                meta(attacker)['sniperOldCover']={'id':attacker.cover_object_id,'pending':True,'expires_after_round':max(1,state.round_number or 1)+1}
        flag_modified(attacker.character,'data')


def can_continue_moving(actor,mode=None,approach_only=False):
    from app.services.combat import CombatService as C
    mode=mode or actor.movement_mode_this_turn or 'walk'
    maximum=C._movement_distance_limit(mode,actor.posture or 'standing',C._movement_evasion_active(actor),C._movement_penalty(actor),actor.character.data)
    remaining=maximum-float(actor.movement_distance_this_turn or 0)
    if remaining<=1e-8 or actor.movement_points_current<=1e-8:return False
    enemies=[enemy for enemy in LocationCharacter.query.filter_by(location_id=actor.location_id).all() if enemy.character and C._are_opponents(actor,enemy) and C._character_condition(enemy.character.data)['state']!='dead'] if approach_only else []
    for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
        step=min(.5,remaining)
        x,y=actor.pos_x+dx*step,actor.pos_y+dy*step
        if not (0<=x<actor.location.grid_width and 0<=y<actor.location.grid_height):continue
        if approach_only and not any(math.hypot(x-enemy.pos_x,y-enemy.pos_y)<math.hypot(actor.pos_x-enemy.pos_x,actor.pos_y-enemy.pos_y)-1e-8 for enemy in enemies):continue
        try:
            route=C._find_movement_path(actor.location,actor.pos_x,actor.pos_y,x,y,
                moving_character_id=actor.character_id,start_level=actor.floor_level,end_level=actor.floor_level,movement_mode=None if approach_only else mode)
            if route and route['distance']<=remaining+1e-8 and C._movement_route_cost(route,mode,actor.posture,actor.character.data)['movement_points']<=actor.movement_points_current+1e-8:return True
        except ValidationError:continue
    return False


def record_approach(actor,path,spent_cost=0):
    from app.services.combat import CombatService as C
    if not background_is(actor.character.data,'Прорывник'):return
    points=path.get('points') or path.get('path') or []
    def point(value):
        return (value['x'],value['y']) if isinstance(value,dict) else (value[0],value[1])
    positions=[point(p) for p in points]
    enemies=[e for e in LocationCharacter.query.filter_by(location_id=actor.location_id).all() if e.character and C._are_opponents(actor,e) and C._character_condition(e.character.data)['state']!='dead']
    gain=0;length=0
    for start,end in zip(positions,positions[1:]):
        segment=math.hypot(end[0]-start[0],end[1]-start[1]);length+=segment
        approaching=any(math.hypot(end[0]-e.pos_x,end[1]-e.pos_y)<math.hypot(start[0]-e.pos_x,start[1]-e.pos_y)-1e-8 for e in enemies)
        if approaching:gain+=segment
    if length and path.get('distance') is not None:gain*=float(path['distance'])/length
    meta(actor)['breakthroughProgress']=float(meta(actor).get('breakthroughProgress') or 0)+gain
    if length:meta(actor)['breakthroughProgressOP']=float(meta(actor).get('breakthroughProgressOP') or 0)+float(spent_cost or 0)*gain/(float(path.get('distance') or length))


def approach_satisfied(actor,mode=None):
    from app.services.combat import CombatService as C
    current=meta(actor)
    limit=C._movement_distance_limit(mode or actor.movement_mode_this_turn or 'walk',actor.posture,C._movement_evasion_active(actor),C._movement_penalty(actor),actor.character.data)
    return (float(current.get('breakthroughProgress') or 0)+1e-8>=limit/2
        or float(current.get('breakthroughProgressOP') or 0)+1e-8>=float(actor.movement_points_max or 0)/2)


def breakthrough_will(actor):
    from app.services.combat import CombatService as C
    current=meta(actor)
    if not current.get('breakthroughWillPassed') and not current.get('breakthroughWillFailed'):
        check=C._narrative_skill_check(actor.character.data,'skills.physical.will')
        check.update(difficulty=10,success=not check.get('automatic_failure') and check['total']>=10)
        current['breakthroughCheck']=check;current['breakthroughWillPassed']=check['success'];current['breakthroughWillFailed']=not check['success']
        flag_modified(actor.character,'data')
        from app.extensions import db
        db.session.commit()
    return bool(current.get('breakthroughWillPassed'))


def before_move(actor,path,mode):
    if not background_is(actor.character.data,'Прорывник') or meta(actor).get('breakthroughWillPassed') or approach_satisfied(actor,mode):return
    if not can_continue_moving(actor,mode,approach_only=True):
        meta(actor)['breakthroughMovementImpossible']=True;return
    saved=deepcopy(meta(actor))
    record_approach(actor,path)
    gain=meta(actor).get('breakthroughProgress',0)-saved.get('breakthroughProgress',0)
    actor.character.data['health']['combatMeta']=saved
    if gain+1e-8>=float(path.get('distance') or 0):return
    if not breakthrough_will(actor):raise ValidationError('Прорывник провалил Волю: сначала двигайтесь в сторону противника')


def before_end_turn(actor,state):
    from app.services.combat import CombatService as C
    if C._character_condition(actor.character.data)['state']!='active':return
    current=meta(actor);mode=actor.movement_mode_this_turn
    if current.get('grenadirMustExplode') and not current.get('combatExplosionOccurred') and can_explode(actor):
        raise ValidationError('Гренадир провалил Волю: сначала устройте взрыв')
    if background_is(actor.character.data,'Спринтер') and mode=='sprint' and can_continue_moving(actor):
        raise ValidationError('Спринтер должен потратить все ОП либо достичь предела спринта')
    if background_is(actor.character.data,'Прорывник') and not current.get('breakthroughWillPassed'):
        if not any(e.character and C._are_opponents(actor,e) and C._character_condition(e.character.data)['state']!='dead' for e in LocationCharacter.query.filter_by(location_id=actor.location_id).all()):return
        if approach_satisfied(actor) or current.get('breakthroughMovementImpossible') or not can_continue_moving(actor,approach_only=True):return
        if not breakthrough_will(actor):raise ValidationError('Прорывник провалил Волю: продолжите сближение с противником')


def advance_hunter_days(data,previous_day,current_day,character=None):
    if not background_is(data,'Охотник') or current_day<=previous_day:return
    health=data.setdefault('health',{});current=health.setdefault('combatMeta',{})
    start=max(previous_day,int(current.get('hunterProcessedDay',previous_day)))
    missed=max(0,current_day-start)
    if start<=int(current.get('hunterActivityDay',-1))<current_day:missed=max(0,missed-1)
    current['hunterProcessedDay']=current_day
    if missed:
        if character is not None:
            from types import SimpleNamespace
            from app.services.combat import CombatService as C
            character.data=data
            actor=LocationCharacter.query.filter_by(character_id=character.id).first() or SimpleNamespace(character=character,location_id=None)
            result=C.apply_stress_trigger(actor,missed,trigger='hunter_without_hunt')
            if result.get('blocked'):return
            data=character.data;health=data['health'];current=health['combatMeta']
            current['hunterStressDebt']=min(10,max(0,float(current.get('hunterStressDebt') or 0))+missed)
            return
        current['hunterStressDebt']=min(10,max(0,float(current.get('hunterStressDebt') or 0))+missed)
        health['stress']=min(10,max(0,float(health.get('stress') or 0))+missed)
