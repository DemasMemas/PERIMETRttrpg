"""Установка, маскировка и обезвреживание игровых ловушек."""
from copy import deepcopy
import math
import random
import hashlib
import json
from uuid import uuid4
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db,socketio
from app.models import LocationTrap,LocationCharacter,LocationCombatState,LobbyParticipant,LocationObject
from app.services.trap_catalog import PROFILES
from app.services.exceptions import ValidationError,ConflictError,NotFoundError
from app.services.transaction import atomic_operation


def enabled():
    from sqlalchemy import inspect
    return inspect(db.session.connection()).has_table('location_traps')


def visual_id(trap):return -2000000000-trap.id


def visible(trap,user_id):
    return trap.status!='recovered' and (trap.detection_dc is None or user_id==trap.location.lobby.gm_id or user_id in (trap.known_users or []))


def project(trap,user_id):
    if not visible(trap,user_id):return None
    p=PROFILES[trap.recipe_id]
    return {'id':visual_id(trap),'revision':trap.revision,'type':'placed_trap','name':p['name'],
        'tile_x':trap.x,'tile_y':trap.y,'properties':{'trap_id':trap.id,'trap_kind':p['kind'],
            'trap_status':trap.status,'level':trap.level,'blocks_movement':False,'cover_enabled':False,
            'dimensions':{'width':.65,'depth':.65,'height':.08},
            'trap_victim_id':trap.data.get('victim_id'),
            'interactions':(['trap_defuse'] if p['difficulty'] is not None else [])+['trap_mask'] if trap.status=='armed' else ['trap_escape_net','trap_cut_net'] if p['kind']=='net' and trap.data.get('victim_id') else ['trap_recover'] if trap.status=='disarmed' and p['kind'] not in {'beartrap','caltrops'} else []}}


def projections(location_id,user_id):
    if not enabled():return []
    return [view for trap in LocationTrap.query.filter_by(location_id=location_id).all() if (view:=project(trap,user_id))]


def publish(location_id,ids):
    if not ids:return
    if not enabled():return
    traps=LocationTrap.query.filter(LocationTrap.id.in_(set(ids)),LocationTrap.location_id==location_id).all()
    for trap in traps:
        for participant in LobbyParticipant.query.filter_by(lobby_id=trap.location.lobby_id,is_banned=False).all():
            view=project(trap,participant.user_id)
            socketio.emit('location_object_updated' if view else 'location_object_deleted',
                {'location_id':location_id,'object':view,'object_id':visual_id(trap)},room=f'user_{participant.user_id}')


def publish_effects(result):
    from app.models import LobbyCharacter
    from app.services.character_events import emit_character_update
    ids=set();object_ids=set();todo=[result]
    while todo:
        value=todo.pop()
        if isinstance(value,dict):
            for entry in [*value.get('explosions',[]),*value.get('secondary_explosions',[])]:
                explosion=entry.get('explosion',entry);location_id=entry.get('location_id') or value.get('location_id')
                if location_id and not explosion.get('awaiting_gm'):
                    socketio.emit('combat_explosion',{'location_id':location_id,'explosive':{
                        'item_name':explosion.get('name'),'detonated':True,'impact':explosion.get('epicenter'),'explosion':explosion}},room=f"location_{location_id}")
            if value.get('ground_object_id') and value.get('ground_object_event')=='deleted':
                socketio.emit('location_object_deleted',{'location_id':value['location_id'],'object_id':value['ground_object_id']},room=f"location_{value['location_id']}")
            if type(value.get('object_id')) is int and 'state' in value:object_ids.add(value['object_id'])
            if value.get('audible_to_all') and value.get('location_id'):
                socketio.emit('trap_alarm',{'location_id':value['location_id'],'message':'Сработала звуковая ловушка'},room=f"location_{value['location_id']}")
            ids.update(v for v in value.get('affected_character_ids',[]) if type(v) is int)
            for key in ('victim_id','character_id'):
                if type(value.get(key)) is int:ids.add(value[key])
            todo.extend(value.values())
        elif isinstance(value,list):todo.extend(value)
    for identity in ids:
        character=db.session.get(LobbyCharacter,identity)
        if character:emit_character_update({'character_id':identity,'updates':{'data':character.data_snapshot()},'source':'trap'})
    from app.schemas.location import LocationObjectSchema
    for identity in object_ids:
        obj=db.session.get(LocationObject,identity)
        if obj:socketio.emit('location_object_updated',{'location_id':obj.location_id,'object':LocationObjectSchema().dump(obj)},room=f'location_{obj.location_id}')


def _item(data,path):
    from app.services.portable_lights import walk
    return next((item for current,item in walk(data,True) if current==path),None)


def _near(actor,trap):
    return trap.level==(actor.floor_level or 0) and math.hypot(trap.x-actor.pos_x,trap.y-actor.pos_y)<=1.5+1e-7


def _tool_bonus(item,profile,mask=False):
    from app.services.crafting import normalized
    if item and item.get('category') not in {'tool','misc','weapon','melee_weapon'}:return 0
    name=normalized((item or {}).get('name'));kind=profile['kind']
    if mask:return 3 if 'лопат' in name else 1 if 'нож' in name else 0
    if 'мультитул' in name:return 2
    if 'грузик' in name and kind=='beartrap':return 'automatic'
    if 'нож' in name and 'кустар' not in name:return 5 if kind in {'net','alarm'} else 1 if kind in {'mine','tripwire'} else 0
    if kind=='mine':return 5 if 'отвертка сапер' in name else 3 if 'отвертка' in name else 2 if 'лопат' in name else 0
    if kind=='tripwire' and 'кусач' in name:return 3
    return 0


def prepare(actor,data,payload,user_id):
    from app.services.combat import CombatService
    from app.services.item_template_lookup import resolve_item_template
    from app.services.location_navigation import surface
    if not enabled():raise ValidationError('Ловушки пока недоступны. Обновите сервер.')
    if not isinstance(payload,dict):raise ValidationError('Выберите действие ловушки')
    operation=payload.get('operation');trap=None;item=None;tool=None
    if operation in {'escape_net','cut_net'}:
        victim=actor if operation=='escape_net' else LocationCharacter.query.filter_by(location_id=actor.location_id,character_id=payload.get('target_character_id')).first()
        held=(((victim.character.data or {}).get('health') or {}).get('combatMeta') or {}).get('trappedNet') if victim else None
        if not held:raise ValidationError('Персонаж не удерживается сетью')
        if victim.id!=actor.id and ((victim.floor_level or 0)!=(actor.floor_level or 0) or math.hypot(victim.pos_x-actor.pos_x,victim.pos_y-actor.pos_y)>1.5):raise ValidationError('Подойдите к персонажу в сети')
        trap=db.session.get(LocationTrap,held['trap_id'])
        if not trap:raise ValidationError('Сеть больше недоступна')
        if operation=='cut_net':
            weapon=_item(data,payload.get('tool_path'))
            if not weapon:raise ValidationError('Выберите режущее или рубящее оружие')
            attrs=CombatService._template_attributes(weapon)
            allowed=[str(v).casefold() for v in attrs.get('allowed_attacks') or []]
            if not any(any(word in attack for word in ('реж','руб','cut','slash')) for attack in allowed):raise ValidationError('Это оружие не режет и не рубит')
        return {'operation':operation,'trap':trap,'profile':PROFILES[trap.recipe_id],'victim':victim,'action_points':2 if operation=='escape_net' else 3,'key':trap.recipe_id,'item':None,'tool':None}
    if operation=='place':
        item=_item(data,payload.get('item_path'))
        template=resolve_item_template(item,actor.character.lobby_id) if item else None
        key=(template.attributes or {}).get('trap_recipe') if template else None
        if key not in PROFILES or item.get('id')!=payload.get('item_id'):raise ValidationError('Выберите ловушку из инвентаря')
        if type(item.get('quantity',1)) is not int or item.get('quantity',1)<1:raise ValidationError('Нет экземпляра ловушки')
        x,y=payload.get('x'),payload.get('y');level=actor.floor_level or 0
        if any(type(v) is not int for v in (x,y)) or not (0<=x<actor.location.grid_width and 0<=y<actor.location.grid_height):
            raise ValidationError('Выберите клетку ловушки')
        if math.hypot(x-actor.pos_x,y-actor.pos_y)>1.5+1e-7:raise ValidationError('Поставьте ловушку рядом с персонажем')
        if any((c.floor_level or 0)==level and math.floor(c.pos_x+.5)==x and math.floor(c.pos_y+.5)==y
                for c in LocationCharacter.query.filter_by(location_id=actor.location_id).all()):raise ValidationError('Клетка занята персонажем')
        objects=LocationObject.query.filter_by(location_id=actor.location_id).all()
        if surface(actor.location,objects,x,y,level) is None:raise ValidationError('В клетке нет опоры')
        if LocationTrap.query.filter_by(location_id=actor.location_id,x=x,y=y,level=level,status='armed').first():raise ValidationError('В этой клетке нельзя установить ловушку')
        p=PROFILES[key]
        grenades=item.get('trapGrenades') or []
        if p['kind']=='tripwire' and (len(grenades)!=p['grenades'] or any(not CombatService._explosive_profile(g) for g in grenades)):
            raise ValidationError('В растяжке нет нужных гранат. Создайте её по рецепту с конкретными гранатами')
        from app.services.inventory_access import calculate_inventory_access
        cost=5+calculate_inventory_access(data,payload['item_path'],item)['retrieval_action_points']
    elif operation in {'defuse','recover','mask'}:
        if type(payload.get('trap_id')) is not int:raise ValidationError('Выберите ловушку')
        trap=LocationTrap.query.filter_by(id=payload['trap_id'],location_id=actor.location_id).with_for_update().first()
        if not trap or not visible(trap,user_id):raise NotFoundError('Ловушка недоступна')
        if payload.get('trap_revision')!=trap.revision:raise ConflictError('Ловушка изменилась')
        if not _near(actor,trap):raise ValidationError('Подойдите к ловушке на том же этаже')
        key=trap.recipe_id;p=PROFILES[key]
        if operation=='recover':
            if trap.status!='disarmed' or p['kind'] in {'beartrap','caltrops'}:raise ValidationError('Эту ловушку нельзя подобрать')
            cost=3
        else:
            if trap.status!='armed':raise ValidationError('Ловушка уже не активна')
            if operation=='defuse' and p['difficulty'] is None:raise ValidationError('Обезвреживание этой ловушки не предусмотрено')
            cost=2 if operation=='defuse' else 5
        if payload.get('tool_path') is not None:
            tool=_item(data,payload['tool_path'])
            if not tool or not _tool_bonus(tool,p,operation=='mask'):raise ValidationError('Выберите подходящий инструмент')
    elif operation=='detect':
        # Поиск задаётся клеткой: клиент не получает ID/координаты скрытых ловушек.
        x,y=payload.get('x'),payload.get('y')
        from app.services.character_traits import facial_profile
        if facial_profile(data)['eyes']==2 or any(e.get('type')=='blindness' and e.get('active',True) and CombatService._coerce_float(e.get('value'),0)>=100 for e in (data.get('health') or {}).get('effects') or []):raise ValidationError('Для осмотра нужно видеть')
        if any(type(v) is not int for v in (x,y)):raise ValidationError('Выберите клетку для осмотра')
        if not (0<=x<actor.location.grid_width and 0<=y<actor.location.grid_height):raise ValidationError('Клетка вне подлокации')
        from types import SimpleNamespace
        from app.services.location_navigation import ground
        target=SimpleNamespace(**{k:v for k,v in actor.__dict__.items() if not k.startswith('_')})
        target.id=-1;target.character_id=-1;target.character=actor.character;target.location=actor.location
        target.pos_x,target.pos_y=x,y;target.height=ground(actor.location,x,y)+(actor.floor_level or 0)*2.4;target.posture='prone'
        if not CombatService._can_see_location_character(actor,target):raise ValidationError('Эта клетка не видна персонажу')
        cost=0;key=None;p=None
    else:raise ValidationError('Неизвестное действие ловушки')
    return {'operation':operation,'trap':trap,'key':key,'profile':p,'item':item,'tool':tool,'action_points':cost}


def execute(actor,data,payload,user_id,state=None):
    from app.services.combat import CombatService
    from app.services.magazine import inventory_slot
    from app.services.weather_rules import at_character
    from app.models import Location
    db.session.execute(db.update(Location).where(Location.id==actor.location_id).values(updated_at=Location.updated_at))
    plan=prepare(actor,data,payload,user_id);operation=plan['operation'];trap=plan['trap'];p=plan['profile']
    actor.character.data=data
    result={'name':{'place':'Установка ловушки','defuse':'Обезвреживание ловушки','mask':'Маскировка ловушки','recover':'Подбор ловушки','detect':'Поиск ловушек','escape_net':'Освободиться из сети','cut_net':'Разрезать сеть'}[operation],
        'action_points':plan['action_points'],'roll_required':False,'trap_ids':[]}
    if operation in {'escape_net','cut_net'}:
        check=CombatService._narrative_skill_check(data,'skills.physical.strength') if operation=='escape_net' else None
        success=operation=='cut_net' or check['roll']==20 or check['roll']!=1 and check['total']>=12
        result.update(check={**check,'success':success} if check else None,difficulty=12 if check else None,roll_required=bool(check))
        if success:
            victim=plan['victim'];victim_data=data if victim.id==actor.id else deepcopy(victim.character.data)
            victim_data.setdefault('health',{}).setdefault('combatMeta',{}).pop('trappedNet',None)
            victim.character.data=victim_data;flag_modified(victim.character,'data')
            result['affected_character_ids']=[victim.character_id]
            trap.data={k:v for k,v in trap.data.items() if k!='victim_id'}
    elif operation=='place':
        source=plan['item'];item=deepcopy(source);item['quantity']=1;item.setdefault('id',str(uuid4()))
        if source.get('quantity',1)>1:item['id']=str(uuid4())
        parent,index=inventory_slot(data,payload['item_path'])
        if source.get('quantity',1)==1:parent.pop(index)
        else:parent[index]['quantity']-=1
        trap=LocationTrap(location_id=actor.location_id,x=payload['x'],y=payload['y'],level=actor.floor_level or 0,
            creator_id=actor.character_id,recipe_id=plan['key'],known_users=list({v for v in [user_id,actor.controlled_by,actor.character.owner_id] if v}),status='armed',data={'item':item})
        db.session.add(trap);db.session.flush()
    elif operation=='recover':
        data.setdefault('inventory',{}).setdefault('backpack',[]).append(deepcopy(trap.data['item']));trap.status='recovered'
    elif operation=='mask':
        check=CombatService._narrative_skill_check(data,'skills.other.stealth')
        bonus=_tool_bonus(plan['tool'],p,True);trap.detection_dc=check['total']+bonus
        trap.known_users=list(set([*(trap.known_users or []),user_id]))
        result.update(check={**check,'total':check['total']+bonus,'tool_bonus':bonus},roll_required=True)
    elif operation=='defuse':
        bonus=_tool_bonus(plan['tool'],p)
        difficulty=p['difficulty']-CombatService._base_skill_modifier(data,'skills.other.engineering')-CombatService._base_skill_modifier(data,'skills.physical.awareness')-(bonus if isinstance(bonus,int) else 0)
        roll=random.randint(1,20);success=bonus=='automatic' or roll==20 or roll!=1 and roll>=difficulty
        result.update(check={'roll':roll,'total':roll,'success':success},difficulty=difficulty,roll_required=True)
        if success:trap.status='disarmed'
        else:
            result['trigger']=trigger(trap,actor,state)
            actor._trap_end_turn=bool(result['trigger'].get('end_turn'))
            live=deepcopy(actor.character.data);data.clear();data.update(live);actor.character.data=data
    else:
        check=CombatService._narrative_skill_check(data,'skills.physical.awareness.visual',darkness=CombatService._is_dark_at_character(actor.location,actor),weather=at_character(actor.location,actor))
        found=[]
        for candidate in LocationTrap.query.filter_by(location_id=actor.location_id,x=payload['x'],y=payload['y'],level=actor.floor_level or 0).all():
            if candidate.status=='recovered' or candidate.detection_dc is None:continue
            if check['roll']==20 or check['roll']!=1 and check['total']>=candidate.detection_dc:
                candidate.known_users=list(set([*(candidate.known_users or []),user_id]));found.append(candidate.id)
        result.update(check=check,roll_required=True,trap_ids=found,found=len(found))
        return result
    result['trap_ids']=[trap.id]
    return result


def trigger(trap,victim,state=None):
    """Эффекты находятся здесь; повторное попадание в обработанную ловушку ничего не даёт."""
    from app.services.combat import CombatService
    from app.services.effects import apply_effect_to_health
    p=PROFILES[trap.recipe_id]
    if trap.status!='armed':return None
    trap.status='spent';trap.detection_dc=None
    result={'trap_id':trap.id,'location_id':trap.location_id,'name':p['name'],'victim_id':victim.character_id,'kind':p['kind']}
    if p['kind'] in {'beartrap','caltrops'}:
        zone=random.choice(['left_leg','right_leg'])
        CombatService._apply_attack_damage(victim,p['damage'],zone,{'damage_type':'cutting'},allow_bleeding=False,round_number=state.round_number if state else 0,trauma_checks=0)
        health=victim.character.data.setdefault('health',{})
        apply_effect_to_health(health,{'type':'bleeding_external_'+p['bleeding'],'areas':[zone],'source':'trap'},character_data=victim.character.data)
        apply_effect_to_health(health,{'type':'pain','value':p['pain'],'source':'trap'},character_data=victim.character.data)
        flag_modified(victim.character,'data');result.update(zone=zone,damage=p['damage'],stop=p['kind']=='beartrap',end_turn=p['kind']=='beartrap')
    elif p['kind']=='net':
        data=victim.character.data;data.setdefault('health',{}).setdefault('combatMeta',{})['trappedNet']={'trap_id':trap.id}
        flag_modified(victim.character,'data');result['stop']=True
        trap.data={**trap.data,'victim_id':victim.character_id}
    elif p['kind']=='alarm':
        from app.services import noise
        result['noise']=noise.apply(victim.location,trap.x,trap.y,trap.level,20)
        result['audible_to_all']=True;result['location_id']=trap.location_id;trap.status='armed'
    else:
        profiles=[]
        if p['kind']=='mine':
            profiles=[{'key':'trap-'+trap.recipe_id,'name':p['name'],'radius':len(p['blast']),
                'fragment':p['fragments'][0],'penetration':p['penetration'][0],'blast_base':p['blast'][0],'blast_falloff':0,
                'radial_damage':{k:p[k] for k in ('fragments','penetration','blast')},'fuse':'turn_end'}]
        else:profiles=[CombatService._explosive_profile(g) for g in trap.data['item'].get('trapGrenades') or []]
        result['explosions']=[]
        for profile in profiles:
            profile=deepcopy(profile);profile['source_character_id']=trap.creator_id
            if state and state.status=='active' and (p['kind']=='mine' or profile.get('fuse')=='round'):
                event={'id':str(uuid4()),'item_name':profile['name'],'profile':profile,'x':trap.x,'y':trap.y,'floor_level':trap.level,
                    'actor_id':victim.id,'trigger':'turn_end' if p['kind']=='mine' else 'short_fuse'}
                if p['kind']!='mine':
                    from app.services.grenade_reaction import prepare_event
                    prepare_event(state,event)
                state.pending_explosives=[*(state.pending_explosives or []),event]
            else:result['explosions'].append(CombatService.resolve_explosion(trap.location_id,trap.x,trap.y,profile,floor_level=trap.level,round_number=state.round_number if state else 0))
    if state and state.status=='active':
        from app.services.grenade_reaction import begin
        begin(victim,state)
    CombatService._sync_location_effects_from_character(victim)
    return result


@atomic_operation
def operate(lobby_id,location_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    if not isinstance(payload,dict):raise ValidationError('Выберите действие ловушки')
    actor=LocationCharacter.query.filter_by(location_id=location_id,character_id=payload.get('character_id')).first()
    if not actor or actor.character.lobby_id!=lobby_id:raise NotFoundError('Персонаж отсутствует')
    CharacterService.check_access(actor.character,user_id,edit=True);CombatService.ensure_character_can_act(actor)
    state=LocationCombatState.query.filter_by(location_id=location_id).first()
    if state and state.status=='active':raise ValidationError('В бою используйте боевое действие ловушки')
    identity=payload.get('operation_id')
    if not isinstance(identity,str) or not 1<=len(identity)<=100:raise ValidationError('Нужен идентификатор действия')
    data=deepcopy(actor.character.data or {});receipts=data.setdefault('crafting',{}).setdefault('trap_receipts',[])
    signature=hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k not in {'character_revision','trap_revision'}},sort_keys=True).encode()).hexdigest()
    prior=next((r for r in receipts if r['id']==identity),None)
    if prior:
        if prior['signature']!=signature or prior['location_id']!=location_id:raise ConflictError('Идентификатор действия уже использован')
        return actor.character,prior['result']
    if payload.get('character_revision')!=actor.character.revision:raise ConflictError('Лист изменился')
    result=execute(actor,data,payload,user_id,state);receipts.append({'id':identity,'signature':signature,'location_id':location_id,'result':deepcopy(result)})
    data['crafting']['trap_receipts']=receipts[-50:];actor.character.data=data;flag_modified(actor.character,'data')
    return actor.character,result


def path_traps(actor,path):
    if not enabled():return []
    from app.services.slippery_movement import terrain_segments
    surfaces=path.get('surfaces') or []
    if len(surfaces)<2:return []
    start=surfaces[0];initial=(math.floor(start['x']+.5),math.floor(start['y']+.5),start['level'])
    by_cell={(t.x,t.y,t.level):t for t in LocationTrap.query.filter_by(location_id=actor.location_id,status='armed').all()}
    result=[];seen=set()
    for first,last in zip(surfaces,surfaces[1:]):
        for segment in terrain_segments(first,last):
            cell=segment['cell']
            if cell==initial or cell in seen:continue
            seen.add(cell)
            if cell in by_cell:result.append(by_cell[cell])
    return result


def after_move(actor,path,state):
    results=[]
    for trap in path_traps(actor,path):
        value=trigger(trap,actor,state)
        if value:results.append(value)
    actor._trap_ids=[r['trap_id'] for r in results]
    actor._trap_results=results
    actor._trap_end_turn=any(r.get('end_turn') for r in results)
    if state and state.status=='active':
        from app.services.grenade_reaction import begin
        begin(actor,state)
    return results


def after_landing(actor,state=None):
    """Прыжок минует землю на траектории; контакт происходит в клетке приземления."""
    if not enabled():return []
    traps=LocationTrap.query.filter_by(location_id=actor.location_id,x=math.floor(actor.pos_x+.5),
        y=math.floor(actor.pos_y+.5),level=actor.floor_level or 0,status='armed').all()
    results=[result for trap in traps if (result:=trigger(trap,actor,state))]
    actor._trap_ids=[r['trap_id'] for r in results];actor._trap_results=results
    actor._trap_end_turn=any(r.get('end_turn') for r in results)
    if state and state.status=='active':
        from app.services.grenade_reaction import begin
        begin(actor,state)
    return results
