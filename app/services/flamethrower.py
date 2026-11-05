"""Двухресурсные огнемёты: реальные баки, залпы и существующий эффект горения."""
from copy import deepcopy
from math import atan2,cos,sin,hypot,floor,isfinite
from types import SimpleNamespace
from uuid import uuid4
import random
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LocationCharacter,LocationObject
from app.services.exceptions import ValidationError

FUELS={'Бензин':{'rounds':4,'damage':50,'extinguish':0,'threshold':40},
       'Фосфорное топливо':{'rounds':6,'damage':60,'extinguish':1,'threshold':65},
       'Напалм':{'rounds':8,'damage':85,'extinguish':3,'threshold':80}}


def templates():
    from app.services.catalog_import import template
    result=[]
    for kind,name,weight,capacity,accuracy,distance,ergo,strength in (
        ('jet','Струйный огнемёт',5,8,-1,25,20,14),('reactive','Реактивный огнемёт',2.5,16,6,10,50,12)):
        result.append(template(name,'weapon',0,weight,10,'Огнемёт использует топливо и сжатый воздух. Короткий залп расходует по одному заряду, длинный — по четыре.',
            'Огнеметы',{'flamethrower':kind,'accuracy':accuracy,'range':distance,'ergonomics':ergo,'min_strength':strength,
                'durability':100,'max_durability':100,'noise':3 if kind=='jet' else 4,'craft_only':True,'unspecified_fields':['price','item_class']}))
        tank_name='Бак для струйного огнемета' if kind=='jet' else 'Баллон для реактивного огнемета'
        for fuel in [None,*FUELS]:
            result.append(template(tank_name+(f' ({fuel})' if fuel else ' (пустой)'),'device',0,
                5 if fuel else 2.5 if kind=='jet' else .5,10 if kind=='jet' or fuel else 3,
                f'Топливная ёмкость. Зарядов: {capacity if fuel else 0}/{capacity}.',
                'flamethrower_tank',{'flamethrower_tank':kind,'fuel':fuel,'uses':capacity,'uses_remaining':capacity if fuel else 0,
                    'craft_only':True,'unspecified_fields':['price','item_class']}))
    result.append(template('Бак сжатого воздуха','device',0,.5,3,'Воздушный баллон: 16 зарядов. Накачка ручным насосом — 5 минут, электрическим — 30 секунд.',
        'flamethrower_air',{'flamethrower_air':True,'uses':16,'uses_remaining':16,'craft_only':True,'unspecified_fields':['price','item_class']}))
    return result


def profile(weapon):
    from app.services.combat import CombatService as C
    kind=C._template_attributes(weapon).get('flamethrower')
    if kind not in {'jet','reactive'}:raise ValidationError('Выберите струйный или реактивный огнемёт')
    return kind


def resources(data,weapon):
    from app.services.combat import CombatService as C
    kind=profile(weapon)
    modules=weapon.get('installedModules') or []
    air=next((m for m in modules if C._template_attributes(m).get('flamethrower_air')),None)
    fuel=next((m for m in modules if C._template_attributes(m).get('flamethrower_tank')==kind),None) if kind=='reactive' else next(
        (v for key,v in (data.get('equipment') or {}).items() if key in {'backpack','belt'} and isinstance(v,dict)
         and C._template_attributes(v).get('flamethrower_tank')=='jet'),None)
    return fuel,air


def charges(item):
    from app.services.combat import CombatService as C
    if not item:return 0
    attrs=C._template_attributes(item)
    return max(0,C._coerce_int(item.get('uses',attrs.get('uses_remaining',attrs.get('uses',0))),0))


def preserve(current,incoming):
    from app.services.portable_lights import walk
    previous={item.get('id'):item for _,item in walk(current or {},True) if item.get('id')}
    for _,item in walk(incoming,True):
        old=previous.get(item.get('id'));attrs=(old or {}).get('attributes') or {}
        if not old or not (attrs.get('flamethrower_tank') or attrs.get('flamethrower_air')):continue
        for key in ('uses','maxUses'):
            if key in old:item[key]=old[key]
        for key in ('uses_remaining','flamethrower_tank','flamethrower_air','fuel'):
            if key in attrs:item.setdefault('attributes',{})[key]=deepcopy(attrs[key])


def service_plan(actor,weapon_index,path,mount,operation=None):
    from app.services.combat import CombatService as C
    from app.services.crafting import entries
    from app.services.inventory_access import calculate_inventory_access
    data=actor.character.data or {};weapons=data.get('weapons') or []
    if type(weapon_index) is not int or not 0<=weapon_index<len(weapons):raise ValidationError('Огнемёт не найден')
    weapon=weapons[weapon_index];kind=profile(weapon)
    if operation=='detach':
        fuel,air=resources(data,weapon);item=air if mount=='air' else fuel if mount=='fuel' else None
        if not item:raise ValidationError('Эта ёмкость не установлена')
        return {'weapon_index':weapon_index,'remove':True,'mount':mount,'air':mount=='air','kind':kind,
            'item_id':item.get('id'),'action_points':4 if mount=='air' else 8}
    item=next((i for p,i in entries(data) if p==path),None)
    if not item:raise ValidationError('Выберите ёмкость в инвентаре')
    attrs=C._template_attributes(item);air=bool(attrs.get('flamethrower_air'))
    if not air and attrs.get('flamethrower_tank')!=kind:raise ValidationError('Эта ёмкость не подходит огнемёту')
    if not air and kind=='jet':
        if mount not in {'backpack','belt'}:raise ValidationError('Закрепите бак вместо рюкзака или пояса')
        other_slot='belt' if mount=='backpack' else 'backpack'
        if C._template_attributes((data.get('equipment') or {}).get(other_slot) or {}).get('flamethrower_tank')=='jet':
            raise ValidationError('Сначала снимите бак, закреплённый в другом месте')
        equipped=(data.get('equipment') or {}).get(mount)
        if equipped and not C._template_attributes(equipped).get('flamethrower_tank'):
            raise ValidationError('Сначала освободите выбранное место рюкзака или пояса')
    return {'weapon_index':weapon_index,'path':path,'mount':mount,'air':air,'kind':kind,
        'action_points':(4 if air else 8)+int(calculate_inventory_access(data,path,item)['retrieval_action_points'])}


def service(actor,plan):
    from app.services.magazine import inventory_slot
    if plan.get('remove'):
        data=actor.character.data;weapon=data['weapons'][plan['weapon_index']];fuel,air=resources(data,weapon)
        item=air if plan['air'] else fuel
        if plan['air'] or plan['kind']=='reactive':weapon['installedModules'].remove(item)
        else:
            slot=next(k for k,v in data['equipment'].items() if v is item);data['equipment'].pop(slot)
        data.setdefault('inventory',{}).setdefault('pockets',[]).append(item);flag_modified(actor.character,'data')
        return {'name':'Ёмкость огнемёта снята','action_points':plan['action_points'],'roll_required':False}
    data=actor.character.data;parent,index=inventory_slot(data,plan['path']);source=parent[index]
    item=deepcopy(source);item['quantity']=1;item.setdefault('id',str(uuid4()))
    if source.get('quantity',1)>1:source['quantity']-=1;item['id']=str(uuid4())
    else:parent.pop(index)
    weapon=data['weapons'][plan['weapon_index']]
    if plan['air'] or plan['kind']=='reactive':
        key='flamethrower_air' if plan['air'] else 'flamethrower_fuel'
        modules=weapon.setdefault('installedModules',[])
        old=next((m for m in modules if m.get('slotType')==key),None)
        if old:modules.remove(old)
        item['slotType']=key;modules.append(item)
    else:
        equipment=data.setdefault('equipment',{});old=equipment.get(plan['mount']);equipment[plan['mount']]=item
    if old:data.setdefault('inventory',{}).setdefault('pockets',[]).append(old)
    flag_modified(actor.character,'data')
    return {'name':'Ёмкость огнемёта заменена','action_points':plan['action_points'],'roll_required':False}


def prepare(actor,weapon_index,mode,x,y,target_id=None,*,floor_level=None,is_gm=False):
    from app.services.combat import CombatService as C
    from app.services.weapon_grip import pair
    data=actor.character.data or {};weapons=data.get('weapons') or []
    if type(weapon_index) is not int or not 0<=weapon_index<len(weapons):raise ValidationError('Огнемёт не найден')
    weapon=weapons[weapon_index];kind=profile(weapon)
    if pair(actor):raise ValidationError('Огнемёт нужно держать двумя руками')
    from app.services.weapon_grip import hands,functional_arms
    if hands(data)!=2 or functional_arms(data)<2:raise ValidationError('Для огнемёта нужны две действующие руки')
    from app.services.body_transport import ensure_firearm_allowed
    ensure_firearm_allowed(actor)
    if actor.drawn_weapon_index!=weapon_index and C._persistent_weapon_index(actor)!=weapon_index:raise ValidationError('Сначала возьмите огнемёт в руки')
    if mode not in {'short','long'}:raise ValidationError('Выберите короткий или длинный залп')
    target=None
    if target_id is not None:
        target=LocationCharacter.query.filter_by(location_id=actor.location_id,character_id=target_id).first()
        if not target:raise ValidationError('Цель не найдена')
        if target.gm_hidden and not is_gm:raise ValidationError('Цель скрыта ГМом')
        x,y=target.pos_x,target.pos_y
    if kind=='reactive' and mode=='short' and target_id is None:raise ValidationError('Короткий реактивный залп направлен на конкретную цель')
    if type(x) not in {int,float} or type(y) not in {int,float} or not isfinite(x) or not isfinite(y):raise ValidationError('Выберите точку на карте')
    if not 0<=x<actor.location.grid_width or not 0<=y<actor.location.grid_height:raise ValidationError('Точка вне карты')
    if target and target.floor_level!=actor.floor_level:raise ValidationError('Цель находится на другом этаже')
    if floor_level is not None and floor_level!=(actor.floor_level or 0):raise ValidationError('Выберите точку на текущем этаже')
    if not C._is_in_facing_arc(actor,x,y):raise ValidationError('Точка вне направления взгляда')
    from app.services.weapon_modules import weapon_stat
    distance=hypot(x-actor.pos_x,y-actor.pos_y)
    if distance>weapon_stat(weapon,'range',25 if kind=='jet' else 10):raise ValidationError('Цель вне дальности огнемёта')
    if kind=='reactive' and mode=='long' and distance>8:raise ValidationError('Конус длинного залпа ограничен 8 метрами')
    fuel,air=resources(data,weapon);needed=4 if mode=='long' else 1
    if charges(fuel)<needed or charges(air)<needed:raise ValidationError('Не хватает топлива или сжатого воздуха')
    attrs=C._template_attributes(fuel);mixture=attrs.get('fuel')
    if mixture not in FUELS:raise ValidationError('Неизвестная зажигательная смесь')
    if C._weapon_jam_effects(weapon)['blocks_fire']:raise ValidationError('Сначала устраните задержку огнемёта')
    return {'weapon_index':weapon_index,'mode':mode,'kind':kind,'x':x,'y':y,'target_character_id':target_id,
        'fuel':mixture,'fuel_id':fuel.get('id'),'air_id':air.get('id'),'charges':needed,
        'action_points':3 if mode=='short' else 5 if kind=='jet' else 4}


def cells(location,objects,area):
    """Прямая полоса пламени; стены не обходятся, дерево и растения не экранируют."""
    from app.services.combat import CombatService as C
    from app.services.location_navigation import surface
    level=area.get('floor_level',0);dx,dy=area['direction'];length,width=area['length'],area['width']
    from app.services.burning import flammable_object
    barriers=[o for o in objects if not flammable_object(o)]
    geometry=C._movement_wall_geometry(location,barriers)
    identifiers={o.id for o in barriers}
    boxes=[b for b in [*geometry.boxes,*(b[0] for b in geometry.climb_boxes)] if b.object_id in identifiers]
    result=[]
    for x in range(max(0,floor(area['x']-length-width)),min(location.grid_width,int(area['x']+length+width)+1)):
        for y in range(max(0,floor(area['y']-length-width)),min(location.grid_height,int(area['y']+length+width)+1)):
            vx,vy=x-area['x'],y-area['y'];along=vx*dx+vy*dy;side=abs(vx*dy-vy*dx)
            halfwidth=width/2*(max(0,along)/length if area.get('shape')=='flame_cone' else 1)
            if not 0<=along<=length or side>halfwidth:continue
            node=surface(location,objects,x,y,level)
            if not node:continue
            plane=node['height']+1
            if any(b.level==level and b.bottom<=plane<=b.top and b.segment_entry((area['origin_x']+.5,area['origin_y']+.5),(x+.5,y+.5)) is not None for b in boxes):continue
            result.append([x,y,level])
    return result


def scatter_cell(location,objects,x,y,level,units):
    """Выбирает доступную клетку на текущем этаже в пределах единиц провала."""
    from app.services.location_navigation import surface
    radius=min(10,max(1,units))
    choices=[(cell_x,cell_y) for cell_x in range(max(0,floor(x-radius)),min(location.grid_width,floor(x+radius)+1))
             for cell_y in range(max(0,floor(y-radius)),min(location.grid_height,floor(y+radius)+1))
             if hypot(cell_x-x,cell_y-y)<=radius and surface(location,objects,cell_x,cell_y,level)]
    return random.choice(choices) if choices else (floor(x+.5),floor(y+.5))


def fire(actor,state,plan):
    from app.services.combat import CombatService as C
    from app.services.weapon_modules import weapon_stat
    from app.services.burning import ignite,ignite_objects,ignite_tile_objects
    from app.services.weather_rules import at_character as weather_at
    data=actor.character.data;weapon=data['weapons'][plan['weapon_index']];fuel,air=resources(data,weapon)
    mixture=FUELS[plan['fuel']];kind=plan['kind'];mode=plan['mode']
    strength=C._weapon_strength_profile(actor,weapon)
    check=C._narrative_skill_check(data,'skills.physical.shooting',darkness=C._is_dark_at_position(actor.location,plan['x'],plan['y']),
        roll_bonus=weapon_stat(weapon,'accuracy',-1 if kind=='jet' else 6)-strength['accuracy_penalty']-C._equipment_accuracy_penalty(data),weather=weather_at(actor.location,actor))
    success=check['roll']==20 or check['roll']!=1 and check['total']>=12
    x,y=plan['x'],plan['y']
    objects=LocationObject.query.filter_by(location_id=actor.location_id).all()
    if kind=='jet' and not success:
        miss=max(1,12-check['total']);angle=random.uniform(0,6.283185307)
        x+=cos(angle)*miss;y+=sin(angle)*miss
    elif kind=='reactive' and not success:
        x,y=scatter_cell(actor.location,objects,x,y,actor.floor_level or 0,12-check['total'])
    vx,vy=x-actor.pos_x,y-actor.pos_y;distance=hypot(vx,vy)
    direction=[vx/distance,vy/distance] if distance else [actor.facing_x or 1,actor.facing_y or 0]
    targets=LocationCharacter.query.filter_by(location_id=actor.location_id).all();hits=[]
    area={'id':str(uuid4()),'type':'fire','shape':'flame_rectangle' if kind=='jet' else 'flame_cone','name':f'Огнемёт: {plan["fuel"]}',
        'x':x if kind=='jet' else actor.pos_x,'y':y if kind=='jet' else actor.pos_y,'origin_x':actor.pos_x,'origin_y':actor.pos_y,
        'direction':direction,'length':(12 if mode=='long' else 6) if kind=='jet' else 8,'width':(4 if mode=='long' else 2) if kind=='jet' else 3,
        'floor_level':actor.floor_level or 0,'radius':20,'duration_rounds':mixture['rounds'],'age':0,'created_round':state.round_number,
        'burn_rounds':mixture['rounds'],'burn_damage':mixture['damage'],'thermal_threshold':mixture['threshold'],'threshold_inclusive':False,
        'extinguish_difficulty_modifier':mixture['extinguish'],'source_character_id':actor.character_id}
    if kind=='jet':
        from app.services.cloud_geometry import prepare as prepare_area
        prepare_area(actor.location,objects,area);footprint=area['cells']
    else:footprint=cells(actor.location,objects,area)
    object_cells=footprint if kind=='jet' or mode=='long' else cells(actor.location,objects,
        {**area,'shape':'flame_rectangle','x':floor(x+.5),'y':floor(y+.5),'length':0,'width':0})
    object_hits=ignite_objects(objects,object_cells,rounds=max(1,mixture['rounds']-(kind=='reactive')),
        damage=mixture['damage'],source=area['name'])
    tile_object_hits=ignite_tile_objects(actor.location,object_cells,rounds=max(1,mixture['rounds']-(kind=='reactive')),
        damage=mixture['damage'],source=area['name'])
    for target in targets:
        if target.id==actor.id or not target.character:continue
        if kind=='jet' or mode=='long':hit=[floor(target.pos_x+.5),floor(target.pos_y+.5),target.floor_level or 0] in footprint
        else:
            aim_x,aim_y=floor(x+.5),floor(y+.5)
            probe={**area,'shape':'flame_rectangle','x':aim_x,'y':aim_y,'length':0,'width':0}
            hit=[floor(target.pos_x+.5),floor(target.pos_y+.5),target.floor_level or 0]==[aim_x,aim_y,area['floor_level']] and bool(cells(actor.location,objects,probe))
        if hit:
            rounds=max(1,mixture['rounds']-(kind=='reactive'))
            ignite(target,rounds,source=area['name'],damage=mixture['damage'],threshold=mixture['threshold'],inclusive=False,
                extinguish_difficulty_modifier=mixture['extinguish'],source_character_id=actor.character_id)
            C._sync_location_effects_from_character(target);hits.append({'character_id':target.character_id,'rounds':rounds})
    if kind=='jet':
        area['cells']=footprint;state.area_effects=[*(state.area_effects or []),area]
    for item in (fuel,air):
        item['uses']=charges(item)-plan['charges'];item.setdefault('attributes',{})['uses_remaining']=item['uses']
    wear=C._weapon_use_wear(weapon,fire_mode='burst' if mode=='long' else 'unaimed')
    from app.services import noise
    noise_targets=noise.apply(actor.location,actor.pos_x,actor.pos_y,actor.floor_level or 0,3 if kind=='jet' else 4)
    flag_modified(actor.character,'data')
    return {'name':('Струйный' if kind=='jet' else 'Реактивный')+' огнемёт','action_points':plan['action_points'],
        'roll_required':True,'check':check,'success':success,'target':{'x':x,'y':y},'hits':hits,
        'object_hits':object_hits,'tile_object_hits':tile_object_hits,
        'fuel_left':charges(fuel),'air_left':charges(air),'wear':wear,'noise_targets':noise_targets}
