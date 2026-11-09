"""Смена хвата одного активного оружия с серверной оплатой и сохранением."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
from app.services.exceptions import ValidationError, PermissionDenied, NotFoundError, ConflictError
from app.services.transaction import atomic_operation
from app.services.action_journal import journaled


def hands(data):
    value = (((data or {}).get('health') or {}).get('combatMeta') or {}).get('weaponGripHands')
    return 1 if type(value) is int and value == 1 else 2


def functional_arms(data):
    return sum(functional_hand(data,side) for side in ('left','right'))


def primary_hand(data):
    value=(((data or {}).get('health') or {}).get('combatMeta') or {}).get('weaponPrimaryHand')
    return value if value in {'left','right'} else 'right'


def functional_hand(data,side):
    from app.services.combat import CombatService
    from app.services.prostheses import limb
    zones = ((data or {}).get('health') or {}).get('zones') or {}
    from app.services.effects import normalize_effect_list
    missing={effect.get('area') for effect in normalize_effect_list(((data or {}).get('health') or {}).get('effects') or [])
        if effect.get('active',True) and effect.get('type') in {'amputation','mangled_limb'}}
    aliases=(side+'Arm',side+'_arm')
    zone = next((zones.get(key) for key in aliases if isinstance(zones.get(key), dict)), None)
    return bool(limb(data,aliases[0])) or not any(key in missing for key in aliases) and (zone is None or CombatService._coerce_float(zone.get('current'),0) > 0)


def validate_load(data,weapon,sides):
    from app.services.combat import CombatService
    from app.services.prostheses import limb
    template=CombatService._weapon_template(weapon)
    weight=CombatService._item_total_weight({**weapon,'weight':weapon.get('weight',template.weight if template else 0)})
    for side in sides:
        if not functional_hand(data,side):raise ValidationError('Выбранная рука не действует')
        prosthesis=limb(data,side+'Arm')
        if prosthesis and weight/len(sides)>prosthesis['max_load']:
            raise ValidationError('Вес оружия превышает допустимую нагрузку протеза')


def draw_plan(data,weapon,choice=None,*,extra_occupied=False):
    from app.services.combat import CombatService
    from app.services.portable_lights import held_hand
    from app.services.held_items import items as held_items
    if weapon.get('naturalWeapon') or CombatService._template_attributes(weapon).get('natural_weapon'):return None
    choices={'left_hand':(1,'left'),'right_hand':(1,'right'),'both_hands':(2,primary_hand(data))}
    if choice is not None and choice not in choices:raise ValidationError('Выберите руку для оружия')
    meta=((data.get('health') or {}).get('combatMeta') or {})
    occupied=int(bool(meta.get('heldWaterItem')))+int(bool(held_hand(data)))+int(bool(extra_occupied or meta.get('bodyTransport')))+len(held_items(data))
    candidates=[choices[choice]] if choice else [(hands(data),primary_hand(data)),(1,primary_hand(data)),(1,'left' if primary_hand(data)=='right' else 'right')]
    error=None
    for count,side in candidates:
        try:
            if count+occupied>functional_arms(data):raise ValidationError('Для оружия не хватает свободных действующих рук')
            validate_load(data,weapon,['left','right'] if count==2 else [side])
            return {'hands':count,'hand':side}
        except ValidationError as rejected:error=rejected
    raise error


def preserve(current, incoming):
    old = (((current or {}).get('health') or {}).get('combatMeta') or {})
    new = incoming.setdefault('health', {}).setdefault('combatMeta', {})
    for key in ('weaponGripHands','weaponPrimaryHand','secondaryWeaponId'):
        if key in old:new[key]=old[key]
        else:new.pop(key,None)


def secondary_index(actor):
    character=getattr(actor,'character',None)
    data=character.data if character and isinstance(character.data,dict) else {};weapons=data.get('weapons') or []
    identity=((data.get('health') or {}).get('combatMeta') or {}).get('secondaryWeaponId')
    indices=[index for index,weapon in enumerate(weapons) if identity and isinstance(weapon,dict) and weapon.get('id')==identity]
    primary=getattr(actor,'drawn_weapon_index',data.get('activeWeaponIndex'))
    return indices[0] if len(indices)==1 and primary is not None and indices[0]!=primary else None


def held(actor,index):
    return index is not None and index in (getattr(actor,'drawn_weapon_index',None),secondary_index(actor))


def remove_drawn(actor, data, index):
    secondary = secondary_index(actor)
    primary = actor.drawn_weapon_index
    item = data['weapons'].pop(index)
    meta = data.setdefault('health', {}).setdefault('combatMeta', {})
    meta.pop('secondaryWeaponId', None)
    if primary == index:
        actor.drawn_weapon_index = secondary - 1 if secondary is not None and secondary > index else secondary
    elif primary is not None and primary > index:
        actor.drawn_weapon_index = primary - 1
    if actor.drawn_weapon_index is None:
        data.pop('activeWeaponIndex', None)
    else:
        data['activeWeaponIndex'] = actor.drawn_weapon_index
    return item


def pair(actor):
    character=getattr(actor,'character',None)
    data=character.data if character and isinstance(character.data,dict) else {};weapons=data.get('weapons') or []
    primary=getattr(actor,'drawn_weapon_index',None);secondary=secondary_index(actor)
    return [weapons[primary],weapons[secondary]] if secondary is not None and type(primary) is int and 0<=primary<len(weapons) else []


def melee(weapon):
    from app.services.combat import CombatService
    template=CombatService._weapon_template(weapon)
    return (weapon.get('category') or (template.category if template else ''))=='melee_weapon'


def validate_secondary(actor,index):
    from app.services.combat import CombatService
    data=actor.character.data or {};weapons=data.get('weapons') or []
    primary=getattr(actor,'drawn_weapon_index',None)
    if type(primary) is not int or not 0<=primary<len(weapons) or index==primary:
        raise ValidationError('Сначала возьмите первое оружие одной рукой')
    if secondary_index(actor) is not None or hands(data)!=1 or not CombatService._has_usable_free_hand(actor):
        raise ValidationError('Для второго оружия нужна свободная действующая рука и одноручный хват первого')
    if ((data.get('health') or {}).get('combatMeta') or {}).get('bodyTransport') or actor.grapple_target_id:
        raise ValidationError('Рука занята переноской или захватом')
    selected=[weapons[primary],weapons[index]]
    validate_load(data,selected[0],[primary_hand(data)])
    validate_load(data,selected[1],['left' if primary_hand(data)=='right' else 'right'])
    if any(weapon.get('naturalWeapon') or CombatService._template_attributes(weapon).get('natural_weapon') for weapon in selected):
        raise ValidationError('Врождённую атаку нельзя взять во вторую руку')
    if all(melee(weapon) for weapon in selected):
        for weapon in selected:
            template=CombatService._weapon_template(weapon)
            weight=CombatService._coerce_float(weapon.get('weight',template.weight if template else None),-1)
            if weight<0 or weight>1:raise ValidationError('В паре каждое холодное оружие должно весить не более 1 кг')


def take_secondary(actor,index):
    validate_secondary(actor,index)
    data=deepcopy(actor.character.data or {});weapon=data['weapons'][index]
    weapon.setdefault('id',str(uuid4()))
    data.setdefault('health',{}).setdefault('combatMeta',{})['secondaryWeaponId']=weapon['id']
    actor.character.data=data;flag_modified(actor.character,'data')


def firearm_penalty(actor,weapon):
    from app.services.combat import CombatService
    weapons=pair(actor)
    if not weapons or any(melee(item) for item in weapons):return 0
    if ignores_pair_penalties(actor,weapon):return 0
    if revolver(weapon):return 3
    return {'pistol':2,'submachine_gun':4}.get(CombatService._weapon_class(weapon),5)


def revolver(weapon):
    from app.services.combat import CombatService
    return 'револьвер' in (CombatService._weapon_subcategory(weapon)+' '+str(weapon.get('name') or '')).casefold()


def ignores_pair_penalties(actor,weapon):
    from app.services.character_traits import trait_count,background_is
    from app.services.combat import CombatService
    data=actor.character.data or {}
    return bool(trait_count(data,'Амбидекстр') or background_is(data,'Гангстер')
                and CombatService._weapon_class(weapon)=='submachine_gun')


def minimum_strength(weapon):
    from app.services.combat import CombatService
    attrs=CombatService._template_attributes(weapon)
    base=weapon.get('minStrength',weapon.get('min_strength',attrs.get('min_strength',0)))
    from app.services.equipment_modifications import stat as modification_stat
    result=max(0,modification_stat(weapon,'min_strength',CombatService._coerce_int(base,0)))
    for module in weapon.get('installedModules') or []:
        modifiers=module.get('modifiers') or (module.get('attributes') or {}).get('modifiers') or {}
        result=CombatService._apply_numeric_modifier(result,modifiers.get('min_strength',modifiers.get('required_strength')))
    return result


def block_bonus(actor):
    from app.services.combat import CombatService
    weapons=pair(actor)
    if not weapons or not all(melee(weapon) for weapon in weapons):return 0
    result=0
    for weapon in weapons:
        attrs=CombatService._template_attributes(weapon)
        size=CombatService._coerce_float(weapon.get('volume',attrs.get('size',0)),0)
        result+=3 if size>=7 else 2 if size>=5 else 1 if size>=2 else 0
    return result


def validate_saved_pair(current,incoming):
    meta=((current or {}).get('health') or {}).get('combatMeta') or {}
    if not meta.get('secondaryWeaponId'):return
    previous=(current or {}).get('weapons') or [];following=(incoming or {}).get('weapons') or []
    if not any(weapon.get('id')==meta['secondaryWeaponId'] for weapon in previous):return
    if (current or {}).get('activeWeaponIndex')!=(incoming or {}).get('activeWeaponIndex'):
        raise ValidationError('Сначала уберите второе оружие или смените оружие боевым действием')
    for index,weapon in enumerate(previous):
        next_weapon=next((item for item in following if weapon.get('id') and item.get('id')==weapon['id']),None)
        if next_weapon is None and not weapon.get('id') and index<len(following):next_weapon=following[index]
        if next_weapon is None:continue
        if any(weapon.get(key)!=next_weapon.get(key) for key in ('ammo','fixedAmmo','installedMagazine')) or (
                weapon.get('requiresManualCycle') and not next_weapon.get('requiresManualCycle')):
            raise ValidationError('При двух оружиях в руках нельзя перезаряжать или передёргивать затвор. Сначала освободите руку.')


def ensure_reload_allowed(data):
    identity=(((data or {}).get('health') or {}).get('combatMeta') or {}).get('secondaryWeaponId')
    if identity and any(item.get('id')==identity for item in (data or {}).get('weapons') or []):
        raise ValidationError('При двух оружиях в руках нельзя перезаряжать. Сначала освободите руку.')


@journaled(kind='weapon_pair')
@atomic_operation
def prepare_pair(character_id,user_id,payload):
    from types import SimpleNamespace
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    if not isinstance(payload,dict) or set(payload)-{'operation','weapon_index','character_revision'}:
        raise ValidationError('Некорректные параметры пары оружия')
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:raise ConflictError()
    active=LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
            LocationCharacter.character_id==character_id,LocationCombatState.status=='active').all()
    if active and payload.get('operation')!='stow':
        raise ValidationError('В бою второе оружие нужно доставать отдельным действием')
    if len(active)>1:raise ValidationError('Персонаж участвует в нескольких боях')
    if active:
        actor=active[0];state=LocationCombatState.query.filter_by(location_id=actor.location_id,status='active').one()
        if state.current_location_character_id!=actor.id:raise PermissionDenied('Убрать оружие можно только в свой ход')
        CombatService.ensure_character_can_act(actor)
    data=character.data or {}
    from app.services.prosthesis_surgery import busy
    if busy(data):raise ValidationError('Персонаж участвует в операции установки протеза')
    if CombatService._character_condition(data)['state']!='active':raise ValidationError('Персонаж не может менять оружие')
    index=payload.get('weapon_index');weapons=data.get('weapons') or []
    if payload.get('operation')=='take':
        if type(index) is not int or not 0<=index<len(weapons):raise ValidationError('Оружие не найдено')
        actor=SimpleNamespace(character=character,drawn_weapon_index=data.get('activeWeaponIndex'),grapple_target_id=None)
        take_secondary(actor,index)
    elif payload.get('operation')=='stow':
        data=deepcopy(data);data.setdefault('health',{}).setdefault('combatMeta',{}).pop('secondaryWeaponId',None)
        character.data=data;flag_modified(character,'data')
    else:raise ValidationError('Неизвестное действие пары оружия')
    return character


def ensure_usable(actor, weapon):
    from app.services.combat import CombatService
    data = actor.character.data or {}
    attrs = CombatService._template_attributes(weapon)
    if attrs.get('prosthesis_zone'):
        from app.services.prostheses import limb
        current=limb(data,attrs['prosthesis_zone'])
        if not current or not any(item.get('id')==attrs.get('prosthesis_id') for item in current['items']):
            raise ValidationError('Протез для этой атаки не действует')
    if weapon.get('naturalWeapon') or attrs.get('natural_weapon'):
        return
    side=primary_hand(data)
    if secondary_index(actor) is not None and pair(actor)[1].get('id')==weapon.get('id'):side='left' if side=='right' else 'right'
    validate_load(data,weapon,['left','right'] if hands(data)==2 and not pair(actor) else [side])


@journaled(kind='weapon_grip')
@atomic_operation
def change(character_id, user_id, payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    if not isinstance(payload, dict) or set(payload) - {'hands', 'hand', 'draw', 'weapon_index', 'character_revision'}:
        raise ValidationError('Некорректные параметры хвата')
    requested, index = payload.get('hands'), payload.get('weapon_index')
    if 'draw' in payload and type(payload['draw']) is not bool:raise ValidationError('Некорректный выбор оружия')
    if type(requested) is not int or requested not in {1, 2} or type(index) is not int:
        raise ValidationError('Выберите хват одной или двумя руками')
    character = LobbyCharacter.query.filter_by(id=character_id).with_for_update().populate_existing().first()
    if not character:
        raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character, user_id, edit=True)
    if type(payload.get('character_revision')) is not int or payload['character_revision'] != character.revision:
        raise ConflictError()
    data = deepcopy(character.data or {})
    from app.services.prosthesis_surgery import busy
    if busy(data):raise ValidationError('Персонаж участвует в операции установки протеза')
    side=payload.get('hand',primary_hand(data))
    if side not in {'left','right'}:raise ValidationError('Выберите левую или правую руку')
    weapons = data.get('weapons') or []
    if not 0 <= index < len(weapons) or not isinstance(weapons[index], dict):
        raise ValidationError('Оружие не найдено')
    weapon = weapons[index]
    if not payload.get('draw') and (weapon.get('naturalWeapon') or CombatService._template_attributes(weapon).get('natural_weapon')):
        raise ValidationError('У врождённой атаки нет оружейного хвата')
    active = (LocationCharacter.query.join(LocationCombatState,
        LocationCombatState.location_id == LocationCharacter.location_id).filter(
        LocationCharacter.character_id == character_id, LocationCombatState.status == 'active')
        .with_for_update().populate_existing().all())
    actor = active[0] if active else None
    if len(active) > 1:
        raise ValidationError('Персонаж участвует в нескольких боях; сначала завершите лишний бой')
    if actor:
        state = LocationCombatState.query.filter_by(location_id=actor.location_id, status='active').one()
        is_gm = CombatService._ensure_access(actor.location, user_id)
        if not CombatService._can_end_turn_for_character(actor, user_id, is_gm=is_gm) or state.current_location_character_id != actor.id:
            raise PermissionDenied('Сменить хват можно только в свой ход')
        CombatService.ensure_character_can_act(actor)
        active_index = actor.drawn_weapon_index
    else:
        active_index = data.get('activeWeaponIndex')
        if CombatService._character_condition(data)['state'] != 'active':
            raise ValidationError('Персонаж не может менять хват')
    drawing=payload.get('draw',False)
    if drawing:
        if actor:raise ValidationError('В бою доставайте оружие боевым действием')
        choice=('both_hands' if requested==2 else side+'_hand') if 'hand' in payload else None
        plan=draw_plan(data,weapon,choice)
        data['activeWeaponIndex']=index
        data.setdefault('health',{}).setdefault('combatMeta',{}).pop('secondaryWeaponId',None)
        for placed in LocationCharacter.query.filter_by(character_id=character.id).all():placed.drawn_weapon_index=index
        if plan is None:
            character.data=data;flag_modified(character,'data')
            return character,None,{'hands':hands(data),'hand':primary_hand(data),'weapon_index':index,'action_points':0,'natural':True}
        requested,side=plan['hands'],plan['hand']
    elif active_index is None or int(active_index) != index:
        raise ValidationError('Сначала возьмите это оружие в руки')
    before = hands(data)
    if not drawing and before == requested and (requested==2 or primary_hand(data)==side):
        return character, actor, {'hands':before, 'hand':primary_hand(data), 'weapon_index':index, 'action_points':0}
    meta = data.setdefault('health', {}).setdefault('combatMeta', {})
    from app.services.portable_lights import held_hand
    from app.services.held_items import items as held_items
    paired=bool(meta.get('secondaryWeaponId'))
    occupied = bool(meta.get('heldWaterItem')) or bool(meta.get('bodyTransport')) or held_hand(data) or bool(held_items(data)) or bool(actor and actor.grapple_target_id)
    if paired:raise ValidationError('Сначала освободите вторую руку')
    if requested==1 and before==1 and primary_hand(data)!=side and occupied:
        raise ValidationError('Выбранная рука занята')
    if functional_arms(data) < requested or requested == 2 and occupied:
        raise ValidationError('Для двуручного хвата нужна свободная действующая вторая рука' if requested == 2 else 'Нет действующей руки для оружия')
    validate_load(data,weapon,['left','right'] if requested==2 else [side])
    if actor and actor.action_points_current < 1:
        raise ValidationError('Для смены хвата нужен 1 ОД')
    meta['weaponGripHands'] = requested
    meta['weaponPrimaryHand'] = side
    character.data = data
    flag_modified(character, 'data')
    if actor:
        actor.action_points_current -= 1
    return character, actor, {'hands':requested, 'hand':side, 'weapon_index':index, 'action_points':1 if actor else 0}
