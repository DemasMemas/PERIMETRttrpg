"""Протезы: экземпляры, совместимость, питание и обслуживание по игровому времени."""
from copy import deepcopy
import math
import random
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LobbyCharacter, LocationCharacter, LocationCombatState, DeferredCombatAction
from app.services import power_cells
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
    old=((current or {}).get('health') or {}).get('combatMeta') or {}
    meta=health.setdefault('combatMeta',{})
    for key in ('prosthesisSurgeryId','prosthesisSurgeryPatientId','prosthesisSurgeryResult','prosthesisSurgeryBackup'):
        if key in old:meta[key]=deepcopy(old[key])
        else:meta.pop(key,None)
    sync_weapons(incoming)


def normalize_dimensions(data):
    def visit(value):
        if isinstance(value,list):
            for item in value:visit(item)
        elif isinstance(value,dict):
            rule=profile(value)
            if rule:
                value.update(weight=rule['weight'],volume=rule['volume'])
                value.setdefault('attributes',{})['prosthesis']=deepcopy(rule)
            for key,child in value.items():
                if key!='attributes':visit(child)
    for root in (data.get('inventory'),data.get('equipment'),installed(data)):visit(root)


def sync_weapons(data):
    normalize_dimensions(data)
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
        'price':rule['price'],'weight':rule['weight'],'volume':rule['volume'],'compatible_ids':[],
        'description':rule['description']+f" Эффективность {rule['efficiency']}; прочность {rule['durability']}; физическая защита {rule['protection']}%. "
            +f"Крепление: {rule['mount_label']}, {rule['attachment']}. Питание: {rule['power_label']}. Смазка: {rule['lubrication_label']}. "
            +('Устойчив к электрическим аномалиям.' if rule['shielded'] else 'Электрическая аномалия разряжает протез.'),
        'attributes':{'prosthesis':deepcopy(rule)}} for index,(name,rule) in enumerate(PROFILES.items()) if name not in names]


def loss(data,zone):
    from app.services.effects import normalize_effect_list
    return next((effect for effect in normalize_effect_list((data.get('health') or {}).get('effects') or [])
        if effect.get('type')=='amputation' and effect.get('area')==zone and effect.get('active',True)),None)


def hit_part(data,zone,roll=None):
    """Один бросок на попадание; контекст передаётся и защите, и урону."""
    zone={'left_arm':'leftArm','right_arm':'rightArm','left_leg':'leftLeg','right_leg':'rightLeg'}.get(zone,zone)
    if zone not in ZONES or not loss(data,zone):return None
    roll=random.randint(1,6) if roll is None else roll
    part=(roll-1)//2
    labels=('плечо','предплечье','кисть') if zone.endswith('Arm') else ('бедро','голень','стопа')
    injury=loss(data,zone);parts=installed(data).get(zone) or {};base=parts.get('base');rule=profile(base)
    extent=(rule or injury).get('extent',(injury or {}).get('loss_extent'))
    first={'entire_limb':0,'elbow_or_knee':1,'hand_or_foot':2}.get(extent,3)
    result={'zone':zone,'roll':roll,'part':part,'label':labels[part],'mechanical':False}
    if part<first:return result
    item=parts.get('hand') if part==2 and rule and rule.get('provides_base') else base
    result.update(mechanical=True,item=item,protection=(profile(item) or {}).get('protection',0))
    return result


def damage_part(data,hit,damage):
    item=hit.get('item');rule=profile(item)
    result={'roll':hit['roll'],'part':hit['label'],'damage':0,'missing':not bool(rule)}
    if rule:
        before=max(0,item.get('durability',rule['durability']))
        item['durability']=max(0,before-max(0,damage))
        if item['durability']<=0:item['broken']=True
        result.update(name=item['name'],damage=before-item['durability'],durability=item['durability'],broken=bool(item.get('broken')))
        sync_weapons(data)
    return result


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


def _return_cells(data, prosthesis):
    cells = prosthesis.pop('rechargeableCells', [])
    if cells:
        data.setdefault('inventory', {}).setdefault('pockets', []).extend(cells)


def _set_energy(data, prosthesis, remaining):
    previous = prosthesis.get('energy_seconds', 0)
    prosthesis['energy_seconds'] = remaining
    for cell in prosthesis.get('rechargeableCells', []):
        charge = power_cells.percentage(cell) * remaining / previous if previous else 0
        power_cells.set_percentage(cell, charge)
    if remaining == 0:
        _return_cells(data, prosthesis)


def _install_cells(data, prosthesis, paths, required):
    from app.services.magazine import inventory_slot
    from app.services.portable_lights import walk

    if not isinstance(paths, list) or len(paths) != required:
        raise ValidationError(f'Для протеза нужно выбрать аккумуляторы в количестве {required} шт.')
    available = {tuple(path): item for path, item in walk(data, True)
        if path[0] in {'inventory', 'equipment'} and 'installedModules' not in path and type(path[-1]) is int
        and power_cells.kind(item) == 'rechargeable' and power_cells.usable(item)}
    slots = []
    chosen = set()
    for path in paths:
        if not isinstance(path, list) or any(type(part) not in (str, int) for part in path):
            raise ValidationError('Выберите аккумуляторы из инвентаря')
        address = tuple(path)
        if address not in available or address in chosen:
            raise ValidationError('Выберите разные заряженные аккумуляторы из инвентаря')
        chosen.add(address)
        parent, index = inventory_slot(data, path)
        slots.append((parent, index, parent[index]))
    _return_cells(data, prosthesis)
    for parent, index, _ in sorted(slots, key=lambda slot: slot[1], reverse=True):
        parent.pop(index)
    cells = [cell for _, _, cell in slots]
    for cell in cells:
        if not cell.get('id'):
            cell['id'] = uuid4().hex
    prosthesis['rechargeableCells'] = cells
    prosthesis['energy_seconds'] = 86400 * sum(power_cells.percentage(cell) for cell in cells) / (required * 100)


def advance(data,seconds):
    seconds=float(seconds or 0)
    if not math.isfinite(seconds) or seconds<=0:return False
    changed=False
    for parts in installed(data).values():
        for item in parts.values():
            rule=profile(item)
            if not rule:continue
            if item.get('energy_seconds',0)>0:
                _set_energy(data, item, max(0, item['energy_seconds'] - seconds))
                changed=True
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
                _set_energy(data, item, 0)
                changed=True
    if changed:sync_weapons(data)
    return changed


def advance_lobby(lobby,seconds):
    from app.services.prosthesis_surgery import advance_lobby as advance_surgeries
    changed=[];remaining=max(0,seconds)
    # При крупном сдвиге снятый на первом часу протез не расходует заряд ещё семь часов.
    while remaining>0:
        deadlines=[row.payload.get('remaining_seconds',remaining) for row in DeferredCombatAction.query.join(
            LocationCharacter,LocationCharacter.id==DeferredCombatAction.location_character_id).join(
            LobbyCharacter,LobbyCharacter.id==LocationCharacter.character_id).filter(
            LobbyCharacter.lobby_id==lobby.id,DeferredCombatAction.status=='operating').all()
            if (row.payload or {}).get('action_key')=='prosthesis_surgery']
        step=min([remaining,*[max(1,value) for value in deadlines]])
        for character in LobbyCharacter.query.filter_by(lobby_id=lobby.id).all():
            if not installed(character.data):continue
            data=deepcopy(character.data)
            if advance(data,step):
                character.data=data;flag_modified(character,'data');changed.append(character)
        changed.extend(advance_surgeries(lobby,step));remaining-=step
    if seconds==0:changed.extend(advance_surgeries(lobby,0))
    return list({character.id:character for character in changed}.values())


@atomic_operation
def operate(character_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    if not isinstance(payload,dict) or set(payload)-{'operation','zone','slot','path','battery_paths','character_revision',
            'target_character_id','target_revision','interaction_request_id','operation_id'}:
        raise ValidationError('Некорректные параметры протеза')
    if 'target_character_id' in payload and type(payload['target_character_id']) is not int:
        raise ValidationError('Некорректный пациент')
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,user_id,edit=True)
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:raise ConflictError()
    from app.services.prosthesis_surgery import busy,start,cancel
    if payload.get('operation')=='cancel_surgery':
        cancel(character,user_id)
        return character
    if payload.get('operation') in {'surgery_install','surgery_remove'}:
        start(character,user_id,payload)
        return character
    if busy(character.data):raise ValidationError('Сначала дождитесь окончания операции')
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
        if operation == 'power' and rule['batteries'] and 'battery_paths' in payload:
            _install_cells(data, item, payload['battery_paths'], rule['batteries'])
        elif operation=='lubricate':
            source=CombatService._inventory_item_at_path(data,payload.get('path'))
            from app.services.weapon_grip import functional_arms
            if functional_arms(data)<1:raise ValidationError('Для смазки нужна действующая рука')
            if not rule['lubrication_days']:raise ValidationError('Этому протезу смазка не требуется')
            if item.get('lubrication_remaining',0)>0:raise ValidationError('Смазка уже выполняется')
            if str(source.get('name') or '').casefold()!='набор смазочных приспособлений':raise ValidationError('Нужен набор смазочных приспособлений')
            CombatService._take_inventory_item(data,payload['path'],1);item['lubrication_remaining']=300
        else:
            source=CombatService._inventory_item_at_path(data,payload.get('path'))
            if rule['batteries']:
                quantity=rule['batteries'];duration=86400
                if (power_cells.kind(source) or not power_cells.usable(source, flashlight=True)
                        or CombatService._coerce_int(source.get('quantity'), 1) < quantity):
                    raise ValidationError(f'Нужны одноразовые батарейки в количестве {quantity} шт. или комплект аккумуляторов')
            elif rule['artifact_class']:
                attrs=source.get('attributes') or {}
                if source.get('category')!='artifact' or CombatService._coerce_int(attrs.get('class',attrs.get('artifact_class')),0)!=rule['artifact_class']:
                    raise ValidationError('Нужен артефакт соответствующего класса')
                quantity=1;duration=604800
            else:raise ValidationError('Этот протез не требует питания')
            CombatService._take_inventory_item(data,payload['path'],quantity)
            _return_cells(data, item)
            item['energy_seconds']=duration
    else:raise ValidationError('Неизвестное действие протеза')
    sync_weapons(data)
    character.data=data;flag_modified(character,'data');return character
