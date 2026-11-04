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
    from app.services.combat import CombatService
    from app.services.prostheses import limb
    zones = ((data or {}).get('health') or {}).get('zones') or {}
    from app.services.effects import normalize_effect_list
    missing={effect.get('area') for effect in normalize_effect_list(((data or {}).get('health') or {}).get('effects') or [])
        if effect.get('active',True) and effect.get('type') in {'amputation','mangled_limb'}}
    values = []
    for aliases in (('leftArm', 'left_arm'), ('rightArm', 'right_arm')):
        zone = next((zones.get(key) for key in aliases if isinstance(zones.get(key), dict)), None)
        values.append(bool(limb(data,aliases[0])) or not any(key in missing for key in aliases) and (zone is None or CombatService._coerce_float(zone.get('current'),0) > 0))
    return sum(values)


def preserve(current, incoming):
    old = (((current or {}).get('health') or {}).get('combatMeta') or {})
    new = incoming.setdefault('health', {}).setdefault('combatMeta', {})
    for key in ('weaponGripHands','secondaryWeaponId'):
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
    if any(weapon.get('naturalWeapon') or CombatService._template_attributes(weapon).get('natural_weapon') for weapon in selected):
        raise ValidationError('Врождённую атаку нельзя взять во вторую руку')
    if melee(selected[0])!=melee(selected[1]):
        raise ValidationError('Смешанная пара огнестрельного и холодного оружия пока недоступна')
    if melee(selected[0]):
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
    if not pair(actor) or melee(weapon):return 0
    if revolver(weapon):return 3
    return {'pistol':2,'submachine_gun':4}.get(CombatService._weapon_class(weapon),5)


def revolver(weapon):
    from app.services.combat import CombatService
    return 'револьвер' in (CombatService._weapon_subcategory(weapon)+' '+str(weapon.get('name') or '')).casefold()


def minimum_strength(weapon):
    from app.services.combat import CombatService
    attrs=CombatService._template_attributes(weapon)
    base=weapon.get('minStrength',weapon.get('min_strength',attrs.get('min_strength',0)))
    result=CombatService._coerce_int(base,0)
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
    if LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
            LocationCharacter.character_id==character_id,LocationCombatState.status=='active').first():
        raise ValidationError('В бою второе оружие нужно доставать отдельным действием')
    data=character.data or {}
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
    if functional_arms(data) < (2 if pair(actor) else hands(data)):
        raise ValidationError('Для этого хвата не хватает действующих рук. Смените хват или уберите оружие.')


@journaled(kind='weapon_grip')
@atomic_operation
def change(character_id, user_id, payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    if not isinstance(payload, dict) or set(payload) - {'hands', 'weapon_index', 'character_revision'}:
        raise ValidationError('Некорректные параметры хвата')
    requested, index = payload.get('hands'), payload.get('weapon_index')
    if type(requested) is not int or requested not in {1, 2} or type(index) is not int:
        raise ValidationError('Выберите хват одной или двумя руками')
    character = LobbyCharacter.query.filter_by(id=character_id).with_for_update().populate_existing().first()
    if not character:
        raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character, user_id, edit=True)
    if type(payload.get('character_revision')) is not int or payload['character_revision'] != character.revision:
        raise ConflictError()
    data = deepcopy(character.data or {})
    weapons = data.get('weapons') or []
    if not 0 <= index < len(weapons) or not isinstance(weapons[index], dict):
        raise ValidationError('Оружие не найдено')
    weapon = weapons[index]
    if weapon.get('naturalWeapon') or CombatService._template_attributes(weapon).get('natural_weapon'):
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
    if active_index is None or int(active_index) != index:
        raise ValidationError('Сначала возьмите это оружие в руки')
    before = hands(data)
    if before == requested:
        return character, actor, {'hands':before, 'weapon_index':index, 'action_points':0}
    meta = data.setdefault('health', {}).setdefault('combatMeta', {})
    occupied = bool(meta.get('heldWaterItem')) or bool(meta.get('bodyTransport')) or bool(actor and (actor.grapple_target_id or secondary_index(actor) is not None))
    if functional_arms(data) < requested or requested == 2 and occupied:
        raise ValidationError('Для двуручного хвата нужна свободная действующая вторая рука' if requested == 2 else 'Нет действующей руки для оружия')
    if actor and actor.action_points_current < 1:
        raise ValidationError('Для смены хвата нужен 1 ОД')
    meta['weaponGripHands'] = requested
    character.data = data
    flag_modified(character, 'data')
    if actor:
        actor.action_points_current -= 1
    return character, actor, {'hands':requested, 'weapon_index':index, 'action_points':1 if actor else 0}
