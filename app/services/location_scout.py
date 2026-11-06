"""Осмотр территории перед боем с сохранённым списком разведанных целей."""
from copy import deepcopy
import math

from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LocationCharacter,LocationCombatState
from app.services.character_traits import background_is,facial_profile
from app.services.exceptions import ValidationError


def observation_point(character_id,user_id,point=None):
    """Точку и направление задаёт ГМ; получить её может только управляющий листом."""
    from app.services.character import CharacterService
    from app.services.combat import CombatService as C
    from app.services.exceptions import PermissionDenied
    character=CharacterService.get_character(character_id,user_id)
    CharacterService.check_access(character,user_id,edit=True)
    from app.models import Lobby,Location
    if point is not None:
        if db.session.get(Lobby,character.lobby_id).gm_id!=user_id:raise PermissionDenied('Точку осмотра задаёт ГМ')
        if not isinstance(point,dict) or set(point)!={'location_id','x','y','floor_level','facing_x','facing_y'}:raise ValidationError('Некорректная точка осмотра')
        location=Location.query.filter_by(id=point['location_id'],lobby_id=character.lobby_id).first()
        if not location:raise ValidationError('Подлокация не найдена')
        if any(isinstance(point[key],bool) or not isinstance(point[key],(int,float)) or not math.isfinite(point[key]) for key in ('x','y','facing_x','facing_y')) or type(point['floor_level']) is not int:raise ValidationError('Некорректные координаты осмотра')
        if not (0<=point['x']<location.grid_width and 0<=point['y']<location.grid_height):raise ValidationError('Точка осмотра вне карты')
        state=LocationCombatState.query.filter_by(location_id=location.id).first()
        if state and state.status=='active':raise ValidationError('Осмотр территории выполняется до боя')
        from app.models import LocationObject
        from app.services.location_navigation import surface
        objects=LocationObject.query.filter_by(location_id=location.id).all()
        if surface(location,objects,point['x'],point['y'],point['floor_level']) is None:raise ValidationError('На выбранном этаже нет точки наблюдения')
        length=math.hypot(point['facing_x'],point['facing_y'])
        if length<1e-8:raise ValidationError('Укажите направление осмотра')
        point={**point,'facing_x':point['facing_x']/length,'facing_y':point['facing_y']/length}
        current=character.data.setdefault('health',{}).setdefault('combatMeta',{})
        current['locationScoutPoint']=point;current.pop('locationScout',None)
        flag_modified(character,'data');db.session.commit()
    point=deepcopy(((character.data.get('health') or {}).get('combatMeta') or {}).get('locationScoutPoint'))
    if not point:return {'point':None}
    location=Location.query.filter_by(id=point['location_id'],lobby_id=character.lobby_id).first()
    if not location:return {'point':None}
    return {'point':point,'location_name':location.name}


def remote_inspect(character_id,user_id,choice):
    from types import SimpleNamespace
    from app.services.character import CharacterService
    from app.services.combat import CombatService as C
    from app.models import Location
    point=observation_point(character_id,user_id)['point']
    if not point:raise ValidationError('Сначала ГМ должен задать точку осмотра')
    character=CharacterService.get_character(character_id,user_id)
    if not isinstance(choice,dict) or set(choice)-{'item_path','weapon_index'}:raise ValidationError('Некорректный предмет осмотра')
    index=choice.get('weapon_index')
    if index is not None and (type(index) is not int or not 0<=index<len(character.data.get('weapons') or [])):raise ValidationError('Оружие не найдено')
    actor=SimpleNamespace(id=-character.id,character_id=character.id,character=character,
        location_id=point['location_id'],location=db.session.get(Location,point['location_id']),
        pos_x=point['x'],pos_y=point['y'],floor_level=point['floor_level'],facing_x=point['facing_x'],facing_y=point['facing_y'],
        posture='standing',cover_object_id=None,drawn_weapon_index=index)
    result=inspect(actor,{key:value for key,value in choice.items() if key!='weapon_index'},remote=True)
    return {**result,'point':point}


def inspect(actor,choice,*,remote=False):
    from app.services.combat import CombatService as C
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).first()
    if state and state.status=='active':raise ValidationError('Осмотр территории выполняется до начала боя')
    data=actor.character.data or {}
    if facial_profile(data)['eyes']==2 or any(effect.get('type')=='blindness' and effect.get('active',True) and C._coerce_float(effect.get('value'),0)>=100 for effect in (data.get('health') or {}).get('effects') or []):raise ValidationError('Для осмотра нужно видеть')
    choice=choice or {}
    if not isinstance(choice,dict) or set(choice)-{'item_path'}:raise ValidationError('Некорректный предмет осмотра')
    index=actor.drawn_weapon_index
    weapon=(data.get('weapons') or [])[index] if type(index) is int and 0<=index<len(data.get('weapons') or []) else {}
    from app.services.weapon_modules import weapon_stat
    item_bonus=math.floor(weapon_stat(weapon,'range',C._coerce_float(weapon.get('range',(weapon.get('attributes') or {}).get('range')),0))/5)
    night_optic=False
    if choice.get('item_path') is not None:
        from app.services.inventory_access import inventory_slot
        parent,index=inventory_slot(data,choice['item_path']);item=parent[index]
        from app.models.templates import ItemTemplate
        template=db.session.get(ItemTemplate,item.get('templateId')) if type(item.get('templateId')) is int else None
        if not (template and template.category=='misc' and template.name.startswith('Бинокль')
                and item.get('name')==template.name and item.get('category')==template.category):
            raise ValidationError('Выберите бинокль из каталога')
        item_bonus=(template.attributes or {}).get('scouting_bonus')
        if item_bonus not in {15,25}:raise ValidationError('Выберите бинокль с бонусом осмотра 15 или 25')
        night_optic=template.name in {'Бинокль Ночной','Бинокль с дальномером и увеличенной кратностью'}
    awareness=C._base_skill_modifier(data,'skills.physical.awareness');tactics=C._base_skill_modifier(data,'skills.other.tactics')
    from app.services.weather_rules import at_character
    check=C._narrative_skill_check(data,'skills.physical.awareness.visual',
        darkness=C._is_dark_at_character(actor.location,actor) and not night_optic,
        weather=at_character(actor.location,actor))
    difficulty=20-awareness-tactics-item_bonus
    total=check['total']-awareness
    success=not check.get('automatic_failure') and total>=difficulty
    targets=[]
    if success:
        for target in C._unique_location_characters(LocationCharacter.query.filter_by(location_id=actor.location_id).all()):
            if not target.character or target.character_id==actor.character_id or target.gm_hidden or target.cover_object_id:continue
            if remote and (math.hypot(target.pos_x-actor.pos_x,target.pos_y-actor.pos_y)>25 or target.floor_level!=actor.floor_level or C._is_dark_at_character(actor.location,target) and not night_optic):continue
            if C._can_see_location_character(actor,target,max_distance=max(actor.location.grid_width,actor.location.grid_height)) and C._cover_analysis(actor.location_id,actor,target).get('grade')=='none':
                targets.append({'character_id':target.character_id,'name':C._display_name(target.character),'x':target.pos_x,'y':target.pos_y,'floor_level':target.floor_level})
    result={'check':{**check,'difficulty':difficulty,'total':total,'success':success,'item_bonus':item_bonus},'targets':targets,'location_id':actor.location_id}
    data.setdefault('health',{}).setdefault('combatMeta',{})['locationScout']=deepcopy(result)
    flag_modified(actor.character,'data');db.session.commit()
    return result


def start_combat(location_id,participants):
    from app.services.combat import CombatService as C
    from app.services.character_rules import allied_ids
    scouts=[]
    for actor in participants:
        current=actor.character.data.setdefault('health',{}).setdefault('combatMeta',{})
        scouting=current.pop('locationScout',{})
        current.pop('observerTargets',None)
        if not background_is(actor.character.data,'Наблюдатель'):continue
        if scouting.get('location_id')==location_id and scouting.get('check',{}).get('success'):
            scouts.append((actor,{target['character_id'] for target in scouting.get('targets',[])},allied_ids(actor)))
        else:C.apply_stress_trigger(actor,2,trigger='observer_without_scout')
    for actor in participants:
        targets=set()
        for scout,seen,friends in scouts:
            if scout.id==actor.id or actor.character_id in friends:targets.update(seen)
        if targets:actor.character.data['health']['combatMeta']['observerTargets']=sorted(targets)
        flag_modified(actor.character,'data')
