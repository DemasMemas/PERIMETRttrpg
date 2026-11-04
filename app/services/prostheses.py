"""Протезы: экземпляры, совместимость, питание и обслуживание по игровому времени."""
from copy import deepcopy
import math
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState
from app.services.exceptions import ValidationError, ConflictError, NotFoundError
from app.services.transaction import atomic_operation
from app.services.prosthesis_catalog import PROFILES

ZONES={'leftArm','rightArm','leftLeg','rightLeg'}


def installed(data):
    return ((data or {}).get('health') or {}).get('prostheses') or {}


def profile(item):
    return PROFILES.get((item or {}).get('name'))


def usable(item):
    rule=profile(item)
    return bool(rule and item.get('durability',rule['durability'])>0 and not item.get('broken')
        and (not (rule['batteries'] or rule['artifact_class']) or item.get('energy_seconds',0)>0))


def limb(data,zone):
    parts=installed(data).get(zone) or {};base=parts.get('base');hand=parts.get('hand')
    if not parts or not loss(data,zone):return None
    if not usable(base):return None
    rule=profile(base)
    if rule['provides_base'] and not usable(hand):return None
    items=[base,hand] if rule['provides_base'] else [base]
    return {'efficiency':sum(profile(item)['efficiency']-item.get('missed_lubrications',0) for item in items),
        'items':items,'max_load':min((profile(item).get('max_load',float('inf')) for item in items))}


def preserve(current,incoming):
    health=incoming.setdefault('health',{})
    if 'prostheses' in ((current or {}).get('health') or {}):
        health['prostheses']=deepcopy(current['health']['prostheses'])
    else:health.pop('prostheses',None)
    sync_weapons(incoming)


def sync_weapons(data):
    weapons=data.get('weapons') or []
    ordinary=[item for item in weapons if not str(item.get('id') or '').startswith('prosthesis-attack:')]
    attacks=[]
    for zone,parts in installed(data).items():
        if not limb(data,zone):continue
        for item in parts.values():
            rule=profile(item);attack=rule.get('attack') if rule else None
            if not attack:continue
            attacks.append({'id':'prosthesis-attack:'+item['id'],'name':item['name'],'category':'melee_weapon',
                'naturalWeapon':True,'attributes':{'natural_weapon':True,'prosthesis_zone':zone,
                    'prosthesis_id':item['id'],'prosthesis_efficiency':rule['efficiency']-item.get('missed_lubrications',0),
                    'weight_class':'Легкое','allowed_attacks':attack['types'],'accuracy':attack['accuracy'],
                    'damage':attack['damage'],'armor_piercing':attack['armor_piercing'],
                    'bleeding':attack['bleeding'],'skip_strength_scaling':True}})
    if attacks or ordinary!=weapons:data['weapons']=[*ordinary,*attacks]


def missing_templates(existing,subcategory=None):
    if subcategory not in (None,'Протезы'):return []
    names={item.get('name') for item in existing}
    return [{'id':-33001-index,'name':name,'category':'misc','subcategory':'Протезы',
        'price':rule['price'],'weight':None,'volume':None,'compatible_ids':[],
        'description':rule['description']+f" Эффективность {rule['efficiency']}; прочность {rule['durability']}; физическая защита {rule['protection']}%. "
            +f"Крепление: {rule['mount_label']}, {rule['attachment']}. Питание: {rule['power_label']}. Смазка: {rule['lubrication_label']}. "
            +('Устойчив к электрическим аномалиям.' if rule['shielded'] else 'Электрическая аномалия разряжает протез.'),
        'attributes':{'prosthesis':deepcopy(rule)}} for index,(name,rule) in enumerate(PROFILES.items()) if name not in names]


def loss(data,zone):
    from app.services.effects import normalize_effect_list
    return next((effect for effect in normalize_effect_list((data.get('health') or {}).get('effects') or [])
        if effect.get('type')=='amputation' and effect.get('area')==zone and effect.get('active',True)),None)


def plan(data,operation,zone,path=None):
    from app.services.combat import CombatService
    from app.services.weapon_grip import functional_arms
    if zone not in ZONES:raise ValidationError('Выберите конечность')
    if functional_arms(data)<1:raise ValidationError('Нужна хотя бы одна действующая рука')
    parts=installed(data).get(zone) or {}
    if operation=='install':
        item=CombatService._inventory_item_at_path(data,path);rule=profile(item)
        if not rule:raise ValidationError('Выберите протез из каталога')
        if item.get('broken') or item.get('durability',rule['durability'])<=0:raise ValidationError('Сломанный протез не подлежит восстановлению')
        slot='hand' if rule['mount']=='base' else 'base'
        if parts.get(slot):raise ValidationError('Это место уже занято протезом')
        if rule['mount'] in {'clamp','integrated'}:
            raise ValidationError('Для этого крепления нужна отдельная операция; обычное надевание недоступно')
        if rule['kind']!=('arm' if zone.endswith('Arm') else 'leg'):raise ValidationError('Протез не подходит этой конечности')
        if slot=='hand':
            base=parts.get('base')
            if not base or profile(base)['provides_base']!=rule['base_type']:raise ValidationError('Нужна совместимая база кисти')
        else:
            injury=loss(data,zone)
            if not injury or injury.get('loss_extent')!=rule['extent']:
                raise ValidationError('Крепление не подходит степени утраты конечности; уточните её в ранении')
        cost=5 if slot=='hand' else 10
        identity=item.get('id')
    elif operation=='remove':
        slot='hand' if parts.get('hand') else 'base';item=parts.get(slot);rule=profile(item)
        if not rule:raise ValidationError('Протез не установлен')
        if rule['mount'] not in {'strap','base'}:raise ValidationError('Это крепление нельзя снять обычным действием')
        cost=5 if slot=='hand' else 3;identity=item.get('id')
    else:raise ValidationError('Неизвестное действие протеза')
    return {'operation':operation,'zone':zone,'slot':slot,'path':path,'item_id':identity,
        'name':item['name'],'action_points':cost}


def apply(data,details):
    from app.services.combat import CombatService
    fresh=plan(data,details['operation'],details['zone'],details.get('path'))
    if fresh['item_id']!=details['item_id'] or fresh['name']!=details['name']:raise ConflictError('Протез изменён')
    parts=data.setdefault('health',{}).setdefault('prostheses',{}).setdefault(details['zone'],{})
    if details['operation']=='install':
        item=CombatService._take_inventory_item(data,details['path'],1);rule=profile(item)
        item['quantity']=1;item.setdefault('id',str(uuid4()));item.setdefault('durability',rule['durability'])
        item.setdefault('attributes',{})['prosthesis']=deepcopy(rule)
        item.setdefault('energy_seconds',0);item.setdefault('lubrication_elapsed',0);parts[details['slot']]=item
    else:
        item=parts.pop(details['slot']);data.setdefault('inventory',{}).setdefault('pockets',[]).append(item)
    sync_weapons(data)
    return details


def advance(data,seconds):
    seconds=float(seconds or 0)
    if not math.isfinite(seconds) or seconds<=0:return False
    changed=False
    for parts in installed(data).values():
        for item in parts.values():
            rule=profile(item)
            if not rule:continue
            if item.get('energy_seconds',0)>0:
                item['energy_seconds']=max(0,item['energy_seconds']-seconds);changed=True
            if rule['lubrication_days'] and not item.get('broken'):
                period=rule['lubrication_days']*86400
                remaining=item.get('lubrication_remaining',0)
                first=min(seconds,remaining) if remaining else seconds
                missed,elapsed=divmod(item.get('lubrication_elapsed',0)+first,period)
                if remaining:
                    item['lubrication_remaining']=max(0,remaining-seconds)
                    if seconds>=remaining:
                        later,elapsed=divmod(seconds-remaining,period)
                        missed+=later
                item['lubrication_elapsed']=elapsed
                item['missed_lubrications']=item.get('missed_lubrications',0)+int(missed)
                item['durability']=max(0,item.get('durability',rule['durability'])-50*int(missed))
                if item['durability']<=0:item['broken']=True
                changed=True
    if changed:sync_weapons(data)
    return changed


def discharge(data):
    changed=False
    for parts in installed(data).values():
        for item in parts.values():
            rule=profile(item)
            if rule and not rule['shielded'] and (rule['batteries'] or rule['artifact_class']):
                item['energy_seconds']=0;changed=True
    if changed:sync_weapons(data)
    return changed


def advance_lobby(lobby,seconds):
    changed=[]
    for character in LobbyCharacter.query.filter_by(lobby_id=lobby.id).all():
        if not installed(character.data):continue
        data=deepcopy(character.data)
        if advance(data,seconds):
            character.data=data;flag_modified(character,'data');changed.append(character)
    return changed


@atomic_operation
def operate(character_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    if not isinstance(payload,dict) or set(payload)-{'operation','zone','slot','path','character_revision'}:
        raise ValidationError('Некорректные параметры протеза')
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:raise ConflictError()
    if LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
            LocationCharacter.character_id==character_id,LocationCombatState.status=='active').first():
        raise ValidationError('В бою используйте действие установки или снятия протеза')
    data=deepcopy(character.data or {});operation=payload.get('operation');zone=payload.get('zone')
    if CombatService._character_condition(data)['state']!='active':raise ValidationError('Персонаж не может управлять протезом')
    if operation in {'install','remove'}:apply(data,plan(data,operation,zone,payload.get('path')))
    elif operation in {'power','lubricate'}:
        parts=installed(data).get(zone) or {};item=parts.get(payload.get('slot'));rule=profile(item)
        if not rule:raise ValidationError('Протез не установлен')
        if item.get('broken') or item.get('durability',rule['durability'])<=0:raise ValidationError('Сломанный протез не подлежит восстановлению')
        source=CombatService._inventory_item_at_path(data,payload.get('path'))
        if operation=='lubricate':
            from app.services.weapon_grip import functional_arms
            if functional_arms(data)<1:raise ValidationError('Для смазки нужна действующая рука')
            if not rule['lubrication_days']:raise ValidationError('Этому протезу смазка не требуется')
            if item.get('lubrication_remaining',0)>0:raise ValidationError('Смазка уже выполняется')
            if str(source.get('name') or '').casefold()!='набор смазочных приспособлений':raise ValidationError('Нужен набор смазочных приспособлений')
            CombatService._take_inventory_item(data,payload['path'],1);item['lubrication_remaining']=300
        else:
            if rule['batteries']:
                if 'батаре' not in str(source.get('name') or '').casefold():raise ValidationError('Нужны батарейки')
                quantity=rule['batteries'];duration=86400
            elif rule['artifact_class']:
                attrs=source.get('attributes') or {}
                if source.get('category')!='artifact' or CombatService._coerce_int(attrs.get('class',attrs.get('artifact_class')),0)!=rule['artifact_class']:
                    raise ValidationError('Нужен артефакт соответствующего класса')
                quantity=1;duration=604800
            else:raise ValidationError('Этот протез не требует питания')
            CombatService._take_inventory_item(data,payload['path'],quantity);item['energy_seconds']=duration
    else:raise ValidationError('Неизвестное действие протеза')
    sync_weapons(data)
    character.data=data;flag_modified(character,'data');return character
