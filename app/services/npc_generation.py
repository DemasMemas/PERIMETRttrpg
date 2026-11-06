"""Настройка будущей генерации НПС без неподтверждённых численных профилей."""
from copy import deepcopy
import hashlib
import json
import random
import re
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import NpcGenerationConfig, NpcCreationReceipt
from app.services.exceptions import ConflictError, PermissionDenied, ValidationError
from app.services.npc_loot import apply_rolled_loot, roll_loot, validate_profile
from app.models.templates import ItemTemplate


def _gm(lobby,user_id):
    if lobby.gm_id!=user_id:raise PermissionDenied('Генератор НПС настраивает только ГМ')


def _fields(config):
    return {'names':deepcopy(config.names or []),'danger_levels':deepcopy(config.danger_levels or []),
        'wealth_levels':deepcopy(config.wealth_levels or []),'combinations':deepcopy(config.combinations or []),
        'revision':config.revision}


def get_config(lobby,user_id):
    _gm(lobby,user_id)
    config=db.session.get(NpcGenerationConfig,lobby.id)
    return _fields(config) if config else {'names':[],'danger_levels':[],'wealth_levels':[],
        'combinations':[],'revision':0}


def _levels(value,label):
    if not isinstance(value,list) or len(value)>20:raise ValidationError(f'{label}: допустимо до 20 уровней')
    result=[]
    for entry in value:
        if not isinstance(entry,dict) or set(entry)!={'id','name'}:
            raise ValidationError(f'{label}: некорректный уровень')
        identifier=entry['id'];name=entry['name']
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',identifier):
            raise ValidationError(f'{label}: некорректный идентификатор')
        if not isinstance(name,str) or not 1<=len(name.strip())<=80:
            raise ValidationError(f'{label}: укажите название')
        result.append({'id':identifier,'name':name.strip()})
    if len({entry['id'] for entry in result})!=len(result) or len({entry['name'].casefold() for entry in result})!=len(result):
        raise ValidationError(f'{label}: уровни должны различаться')
    return result


def _validate(payload):
    if not isinstance(payload,dict) or set(payload)!={'revision','names','danger_levels','wealth_levels','combinations'}:
        raise ValidationError('Некорректная настройка генератора НПС')
    names=payload['names']
    if not isinstance(names,list) or len(names)>500 or any(not isinstance(name,str) or not 1<=len(name.strip())<=100 for name in names):
        raise ValidationError('Укажите до 500 имён длиной до 100 символов')
    names=[name.strip() for name in names]
    if len({name.casefold() for name in names})!=len(names):raise ValidationError('Имена в списке повторяются')
    danger=_levels(payload['danger_levels'],'Опасность')
    wealth=_levels(payload['wealth_levels'],'Богатство')
    combinations=payload['combinations']
    if not isinstance(combinations,list) or len(combinations)>400:
        raise ValidationError('Допустимо до 400 сочетаний уровней')
    danger_ids={entry['id'] for entry in danger};wealth_ids={entry['id'] for entry in wealth}
    result=[]
    for entry in combinations:
        if not isinstance(entry,dict) or set(entry)!={'danger_id','wealth_id','profile'}:
            raise ValidationError('Некорректное сочетание уровней')
        if not isinstance(entry['danger_id'],str) or not isinstance(entry['wealth_id'],str):
            raise ValidationError('Некорректное сочетание уровней')
        if entry['danger_id'] not in danger_ids or entry['wealth_id'] not in wealth_ids:
            raise ValidationError('Сочетание ссылается на удалённый уровень')
        result.append({'danger_id':entry['danger_id'],'wealth_id':entry['wealth_id'],
            'profile':validate_profile(entry['profile'])})
    if len({(entry['danger_id'],entry['wealth_id']) for entry in result})!=len(result):
        raise ValidationError('Сочетание уровней повторяется')
    return {'names':names,'danger_levels':danger,'wealth_levels':wealth,'combinations':result}


def save_config(lobby,user_id,payload):
    _gm(lobby,user_id)
    if not isinstance(payload,dict) or type(payload.get('revision')) is not int:
        raise ConflictError('Обновите настройки генератора')
    fields=_validate(payload)
    config=db.session.get(NpcGenerationConfig,lobby.id)
    revision=config.revision if config else 0
    if payload['revision']!=revision:
        if config and all(getattr(config,key)==value for key,value in fields.items()):
            return {**_fields(config),'replayed':True}
        raise ConflictError('Настройки изменились. Откройте их заново')
    if config and all(getattr(config,key)==value for key,value in fields.items()):
        return {**_fields(config),'replayed':True}
    if not config:
        config=NpcGenerationConfig(lobby_id=lobby.id,**fields)
        db.session.add(config)
    else:
        for key,value in fields.items():setattr(config,key,value)
    try:db.session.commit()
    except (StaleDataError,IntegrityError) as error:
        db.session.rollback()
        raise ConflictError('Настройки изменились. Откройте их заново') from error
    return {**_fields(config),'replayed':False}


def preview_name(lobby,user_id,exclude=None,*,rng=None):
    _gm(lobby,user_id)
    config=db.session.get(NpcGenerationConfig,lobby.id)
    names=config.names if config else []
    if not names:raise ValidationError('Сначала добавьте утверждённые имена НПС')
    if exclude is not None and not isinstance(exclude,str):raise ValidationError('Некорректное имя для переброса')
    available=[name for name in names if name!=exclude] or names
    return {'name':(rng or random.SystemRandom()).choice(available),'source':'gm_name_pool'}


def _equip_rolled_protection(data):
    """Надеть по одному предмету каждого слота с сохранением остатка стопки."""
    from app.services.combat import CombatService
    categories={'armor':'armor','helmet':'helmet','gasMask':'gas_mask'}
    equipped=[]
    for slot,category in categories.items():
        backpack=data.get('inventory',{}).get('backpack',[])
        index=next((i for i,item in enumerate(backpack) if item.get('category')==category),None)
        if index is None:continue
        details=CombatService.equipment_action_details(data,'equip',slot,
            item_path=['inventory','backpack',index],preconfigured=True)
        CombatService.apply_equipment_action(data,details)
        equipped.append({'slot':slot,'item_id':data['equipment'][slot]['id']})
    for slot in ('gloves','glasses','headphones','vest','belt','backpack'):
        backpack=data.get('inventory',{}).get('backpack',[])
        index=next((i for i,item in enumerate(backpack) if item.get('category')==slot),None)
        if index is None:continue
        stacked=backpack[index].get('quantity',1)>1
        worn=CombatService._take_inventory_item(data,['inventory','backpack',index],1)
        if stacked:worn['id']=uuid4().hex
        data.setdefault('equipment',{})[slot]=worn
        equipped.append({'slot':slot,'item_id':worn['id']})
    from app.services.equipment_passives import validate_glasses
    validate_glasses(data)
    return equipped


def _load_rolled_fixed_ammo(data):
    """Зарядить несъёмные магазины по действующей проверке калибра и ёмкости."""
    from app.services.ammo_loading import load_ammunition, validate_loading
    from app.services.magazine import _attributes
    loaded=[]
    for weapon_index,weapon in enumerate(data.get('weapons') or []):
        if not _attributes(weapon).get('fixedMagazine'):continue
        for index,item in enumerate(data.get('inventory',{}).get('backpack',[])):
            if item.get('category')!='ammo':continue
            request={'weapon_index':weapon_index,'source_path':['inventory','backpack',index],'full':True}
            try:validate_loading(data,request)
            except ValidationError:continue
            result=load_ammunition(data,request)
            loaded.append({'weapon_id':weapon['id'],'ammo_name':item['name'],'quantity':result['quantity']})
            break
    return loaded


def _install_rolled_magazines(data):
    """Подобрать съёмный магазин, зарядить его и установить без расхода боевого действия."""
    from app.services.ammo_loading import load_ammunition, validate_loading
    from app.services.magazine import install_magazine, validate_magazine_selection, _attributes
    installed=[];loaded=[]
    def split_magazine(sheet,index):
        backpack=sheet['inventory']['backpack'];item=backpack[index]
        if item.get('quantity',1)>1:
            item['quantity']-=1
            single=deepcopy(item);single['quantity']=1;single['id']=uuid4().hex
            backpack.insert(index,single)
        return backpack[index]
    for weapon_index,weapon in enumerate(data.get('weapons') or []):
        if weapon.get('category')!='weapon' or _attributes(weapon).get('fixedMagazine'):continue
        backpack=data.get('inventory',{}).get('backpack',[])
        for index,item in enumerate(backpack):
            if item.get('category')!='magazine':continue
            trial=deepcopy(data)
            split_magazine(trial,index)
            try:validate_magazine_selection(trial,weapon_index,['inventory','backpack',index])
            except ValidationError:continue
            magazine=split_magazine(data,index);magazine_id=magazine['id']
            for ammo_index,ammo in enumerate(data['inventory']['backpack']):
                if ammo.get('category')!='ammo':continue
                request={'target_path':['inventory','backpack',index],
                    'source_path':['inventory','backpack',ammo_index],'full':True}
                try:validate_loading(data,request)
                except ValidationError:continue
                result=load_ammunition(data,request)
                loaded.append({'weapon_id':weapon['id'],'ammo_name':ammo['name'],'quantity':result['quantity']})
                break
            magazine_index=next(i for i,value in enumerate(data['inventory']['backpack']) if value.get('id')==magazine_id)
            install_magazine(data,weapon_index,['inventory','backpack',magazine_index])
            installed.append({'weapon_id':weapon['id'],'magazine_id':magazine_id})
            break
    return installed,loaded


def create_npc(lobby,user_id,payload):
    _gm(lobby,user_id)
    if not isinstance(payload,dict) or set(payload)!={'operation_id','name','danger_id','wealth_id'}:
        raise ValidationError('Некорректное создание НПС')
    operation=payload['operation_id'];name=payload['name'];danger_id=payload['danger_id'];wealth_id=payload['wealth_id']
    if not isinstance(operation,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',operation):
        raise ValidationError('Некорректный идентификатор создания')
    if not isinstance(name,str) or not 1<=len(name.strip())<=100:
        raise ValidationError('Укажите имя НПС')
    name=name.strip()
    if (danger_id is None)!=(wealth_id is None) or any(value is not None and not isinstance(value,str) for value in (danger_id,wealth_id)):
        raise ValidationError('Выберите оба уровня или оставьте их пустыми')
    signature=hashlib.sha256(json.dumps([lobby.id,name,danger_id,wealth_id],ensure_ascii=False,
        separators=(',',':')).encode('utf-8')).hexdigest()
    previous=db.session.get(NpcCreationReceipt,operation)
    if previous:
        if previous.lobby_id!=lobby.id or previous.payload_hash!=signature:
            raise ConflictError('Идентификатор создания уже использован')
        return previous.character,True
    config=db.session.get(NpcGenerationConfig,lobby.id)
    profile=None
    if danger_id is not None:
        pair=next((entry for entry in (config.combinations if config else [])
            if entry['danger_id']==danger_id and entry['wealth_id']==wealth_id),None)
        if pair is None:raise ValidationError('Для выбранных уровней не настроен пул снаряжения')
        profile=deepcopy(pair['profile'])
    from app.services.character import CharacterService
    from sqlalchemy.orm.attributes import flag_modified
    character=CharacterService.create_character(lobby.id,user_id,name,{'basic':{'is_npc':True}},commit=False)
    data=deepcopy(character.data or {})
    data['npcGenerator']={'name_source':'gm_name_pool' if config and name in config.names else 'manual',
        'danger_id':danger_id,'wealth_id':wealth_id,'numeric_profile_id':None,
        'group_id':None,'faction_id':None}
    if profile:
        items, loot_receipt=roll_loot(profile,ItemTemplate.query.order_by(ItemTemplate.id).all())
        apply_rolled_loot(data,items,loot_receipt)
        loot_receipt['equipped']=_equip_rolled_protection(data)
        loot_receipt['loaded_ammo']=_load_rolled_fixed_ammo(data)
        loot_receipt['installed_magazines'],magazine_ammo=_install_rolled_magazines(data)
        loot_receipt['loaded_ammo'].extend(magazine_ammo)
        data['npcLoot']={'profile':profile,'initial_receipt':loot_receipt}
    character.data=data;flag_modified(character,'data')
    db.session.add(NpcCreationReceipt(operation_id=operation,lobby_id=lobby.id,character_id=character.id,
        payload_hash=signature))
    try:db.session.commit()
    except (StaleDataError,IntegrityError) as error:
        db.session.rollback()
        previous=db.session.get(NpcCreationReceipt,operation)
        if previous and previous.lobby_id==lobby.id and previous.payload_hash==signature:
            return previous.character,True
        raise ConflictError('Создание НПС изменилось. Повторите запрос') from error
    return character,False
