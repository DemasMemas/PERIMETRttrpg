"""Горение: общий эффект огненных гранат и последствий взрыва."""
import math
from sqlalchemy.orm.attributes import flag_modified
from app.services.effects import apply_effect_to_health,normalize_effect_list


def ignite(target,rounds,*,source,damage=50,threshold=35,inclusive=False,extinguish_difficulty_modifier=0):
    health=target.character.data.setdefault('health',{})
    previous=[e for e in normalize_effect_list(health.get('effects') or [])
              if e['type']=='burning' and e.get('source')==source and e.get('active',True)]
    rounds=max([rounds]+[e.get('remaining') or 0 for e in previous])
    apply_effect_to_health(health,{'type':'burning','name':'Горение','remaining':rounds,
        'tick':'round_end','source':source,'damage_per_round':damage,
        'thermal_threshold':threshold,'threshold_inclusive':inclusive,
        'extinguish_difficulty_modifier':extinguish_difficulty_modifier})
    flag_modified(target.character,'data')


def extinguish_in_water(target,*,include_traversed=True):
    from app.extensions import db
    from app.models import Location
    location=db.session.get(Location,target.location_id)
    if not location:return
    points=(getattr(target,'_movement_surfaces',[]) if include_traversed else []) or [{'x':target.pos_x,'y':target.pos_y,'level':target.floor_level or 0}]
    from app.services.combat import CombatService
    for point in points:
        x,y=int(point['x']+.5),int(point['y']+.5)
        if point.get('level',0)!=0 or not (0<=y<len(location.tiles_data) and 0<=x<len(location.tiles_data[y])):continue
        tile=location.tiles_data[y][x]
        if tile.get('terrain')!='water':continue
        physical=point.get('height',CombatService.physical_position(target)['height'])
        if physical>float(tile.get('height',1))+.2:continue
        target.character.data.setdefault('health',{})['effects']=[e for e in normalize_effect_list(target.character.data.get('health',{}).get('effects') or []) if e['type']!='burning']
        flag_modified(target.character,'data')
        return True
    return False


def extinguish_difficulty(data):
    fires=[e for e in normalize_effect_list(data.get('health',{}).get('effects') or []) if e['type']=='burning' and e.get('active',True)]
    return 12+float(data.get('health',{}).get('stress') or 0)+max([0]+[float(e.get('extinguish_difficulty_modifier') or 0) for e in fires])


def water_item(data,path):
    from app.services.combat import CombatService
    from app.services.general_consumable import _is_water_supply
    from app.services.exceptions import ValidationError
    item=CombatService._inventory_item_at_path(data,path)
    if not _is_water_supply(item):raise ValidationError('Выберите воду')
    attrs=CombatService._template_attributes(item)
    capacity=CombatService._coerce_float(attrs.get('water_litres',item.get('volume')),0)
    if capacity<=0:raise ValidationError('Для этой ёмкости не указан объём воды в литрах')
    maximum=max(1,CombatService._coerce_int(item.get('maxUses',attrs.get('uses')),1))
    uses=max(0,CombatService._coerce_int(item.get('uses',attrs.get('uses_remaining')),maximum))
    if uses<=0:raise ValidationError('В ёмкости не осталось воды')
    return item,capacity*uses/maximum


def pour(actor,target):
    from app.services.combat import CombatService
    from app.services.exceptions import ValidationError
    data=actor.character.data;meta=data.setdefault('health',{}).setdefault('combatMeta',{})
    held=meta.get('heldWaterItem') or {};path=held.get('path')
    if not path:raise ValidationError('Сначала возьмите воду в руку')
    item,litres=water_item(data,path)
    if item.get('id')!=held.get('id'):raise ValidationError('Вода в руке перемещена или заменена')
    CombatService._take_inventory_item(data,path,1)
    meta.pop('heldWaterItem',None)
    health=target.character.data.setdefault('health',{})
    effects=normalize_effect_list(health.get('effects') or [])
    for effect in effects:
        if effect['type']!='burning':continue
        reduction=2*litres+float(effect.get('water_reduction_remainder') or 0)
        whole=math.floor(reduction+1e-8)
        effect['water_reduction_remainder']=max(0,reduction-whole)
        effect['remaining']=max(0,(effect.get('remaining') or 0)-whole)
    health['effects']=[e for e in effects if e.get('remaining') is None or e['remaining']>0]
    flag_modified(actor.character,'data');flag_modified(target.character,'data')
    return litres


def tick(target,round_number):
    from app.services.combat import CombatService
    data=target.character.data
    fires=[e for e in normalize_effect_list(data.get('health',{}).get('effects') or [])
           if e['type']=='burning' and e.get('active',True) and (e.get('remaining') or 0)>0]
    if not fires:
        return
    from app.services.character_traits import background_is
    protection=CombatService._target_elemental_protection(data,'thermal',**({'target':target} if background_is(data,'Стойкий') else {}))
    harmful=[e for e in fires if not (protection>=e.get('thermal_threshold',35)
        if e.get('threshold_inclusive') else protection>e.get('thermal_threshold',35))]
    damage=max(e.get('damage_per_round') or 50 for e in fires)
    for slot in ('armor','helmet'):
        item=(data.get('equipment') or {}).get(slot)
        if isinstance(item,dict):
            CombatService._damage_armor_item(item,CombatService._template_attributes(item),damage)
    if harmful:
        body_damage=max(e.get('damage_per_round') or 50 for e in harmful)
        CombatService._apply_general_damage(target,body_damage,round_number=round_number,
            source='burning',cause_stress=False)
        apply_effect_to_health(target.character.data['health'],{'type':'pain','value':2,'source':'burning'})
        CombatService.apply_stress_trigger(target,1,trigger='burning')
    flag_modified(target.character,'data')
