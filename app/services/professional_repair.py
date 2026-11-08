"""Денежный ремонт у НПС-механика по листу «Поломка снаряжения»."""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
from math import isfinite

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import LocationCombatState
from app.services.character_interaction import CharacterInteractionService
from app.services.character_roles import is_npc
from app.services.combat import CombatService
from app.services.equipment_modifications import rule
from app.services.equipment_repair import (
    ARMOR_CATEGORIES,WEAPON_CATEGORIES,_repair_armor,_repair_weapon,resolve_item_path,
)
from app.services.exceptions import ConflictError,ValidationError
from app.services.item_template_lookup import resolve_item_template
from app.services.trade import balance,money
from app.services.transaction import atomic_operation


def markup_value(value):
    if type(value) not in {int,float} or not isfinite(value) or not 0<value<=10:
        raise ValidationError('Множитель услуг механика должен быть числом больше 0 и не выше 10')
    return Decimal(str(value))


def _prepare(location_id,user_id,payload):
    if not isinstance(payload,dict) or set(payload)-{
        'actor_location_character_id','mechanic_character_id','item_path','item_id','mode',
        'actor_revision','mechanic_revision','execute','operation_id',
    }:raise ValidationError('Некорректные параметры ремонта')
    location,actor,mechanic=CharacterInteractionService._pair(location_id,user_id,
        payload.get('actor_location_character_id'),payload.get('mechanic_character_id'))
    if not is_npc(mechanic.character):raise ValidationError('Ремонт выполняет НПС-механик')
    if not CombatService._is_adjacent(actor,mechanic):raise ValidationError('Подойдите к механику')
    state=LocationCombatState.query.filter_by(location_id=location_id).first()
    if state and state.status=='active':raise ValidationError('Платный ремонт доступен только вне боя')
    settings=(mechanic.character.data or {}).get('merchant') or {}
    if settings.get('repair_enabled') is not True:raise ValidationError('Этот НПС не оказывает услуги ремонта')
    markup=markup_value(settings.get('repair_markup',1))
    path=payload.get('item_path')
    if not isinstance(path,list) or not path or path[0] not in {'weapons','equipment','inventory'}:
        raise ValidationError('Выберите оружие или броню для ремонта')
    try:item=resolve_item_path(actor.character.data or {},path)
    except ValueError as error:raise ValidationError(str(error)) from error
    from app.services.electronics import burned
    if burned(item):raise ValidationError('Прибор сгорел и не подлежит ремонту')
    if payload.get('item_id') is not None and item.get('id')!=payload['item_id']:
        raise ConflictError('Предмет перемещён. Откройте ремонт заново.')
    if type(item.get('quantity',1)) is not int or item.get('quantity',1)!=1:
        raise ValidationError('Перед ремонтом разделите стопку на отдельные экземпляры')
    template=resolve_item_template(item,location.lobby_id)
    if not template or not template.price or template.price<=0 or 'price' in ((template.attributes or {}).get('unspecified_fields') or []):
        raise ValidationError('Для предмета нет подтверждённой каталожной цены ремонта')
    category=str(item.get('category') or template.category or '').casefold()
    mode=payload.get('mode')
    if mode=='protocol_recharge':
        from app.services.equipment_modifications import installed
        from app.services.workbenches import require
        if category!='armor' or not any(mod.get('ruleId')=='armor-d31' for mod in installed(item)):
            raise ValidationError('На броне нет протокола реанимации')
        if item.get('reanimationCharge') is not False:
            raise ValidationError('Протокол реанимации уже заряжен')
        if settings.get('scientist_enabled') is not True:
            raise ValidationError('Перезарядку выполняет механик учёных')
        require(settings,'Станок Электрика')
        repaired=deepcopy(item);repaired['reanimationCharge']=True
        price=money(int((Decimal(str(template.price))*Decimal('0.05')).to_integral_value(rounding=ROUND_FLOOR)),
            label='Цена перезарядки')
        return actor,mechanic,{'item_path':path,'item_name':item.get('name') or template.name,'mode':mode,
            'price':price,'base_price':template.price,'markup':1,'discount':0,
            'result':{'kind':'protocol_recharge','charged':True},'repaired':repaired}
    if category in WEAPON_CATEGORIES and mode!='weapon' or category in ARMOR_CATEGORIES and mode not in {'armor_stage','armor_durability'}:
        raise ValidationError('Выберите вид ремонта для этого предмета')
    if category not in WEAPON_CATEGORIES|ARMOR_CATEGORIES:
        raise ValidationError('Ремонтируются только оружие, броня и шлемы')
    from app.services.workbenches import for_item, require
    require(settings,for_item(category))
    repaired=deepcopy(item)
    if mode=='weapon':
        current,maximum=CombatService._weapon_durability(repaired)
        if current>=maximum:raise ValidationError('Оружие уже имеет максимальную прочность')
        fraction=Decimal(str(maximum-current))/Decimal(str(maximum))
        result=_repair_weapon(repaired,{'repair_amount':maximum,'minimum_durability':0},template)
    else:
        result=_repair_armor(repaired,{'kind':'armor' if mode=='armor_stage' else 'armor_current_stage',
            'repair_stages':1},template)
        fraction=Decimal('0.10' if mode=='armor_stage' else '0.05')
    discount=Decimal(str(max(0,min(.95,float(rule(item,'repair_discount',0))))))
    price=money(int((Decimal(str(template.price))*fraction*markup*(1-discount)).to_integral_value(rounding=ROUND_CEILING)),
        label='Цена ремонта')
    return actor,mechanic,{'item_path':path,'item_name':item.get('name') or template.name,'mode':mode,
        'price':price,'base_price':template.price,'markup':float(markup),'discount':float(discount),
        'result':result,'repaired':repaired}


def quote(location_id,user_id,payload):
    _,_,plan=_prepare(location_id,user_id,payload)
    return {key:value for key,value in plan.items() if key!='repaired'}


@atomic_operation
def perform(location_id,user_id,payload):
    if type(payload.get('actor_revision')) is not int or type(payload.get('mechanic_revision')) is not int:
        raise ValidationError('Обновите листы персонажа и механика')
    operation_id=payload.get('operation_id')
    if not isinstance(operation_id,str) or not 1<=len(operation_id)<=100:
        raise ValidationError('Укажите идентификатор операции')
    _,actor,_=CharacterInteractionService._pair(location_id,user_id,
        payload.get('actor_location_character_id'),payload.get('mechanic_character_id'))
    history=(((actor.character.data or {}).get('health') or {}).get('combatMeta') or {}).get('actionJournal') or []
    if any(row.get('id')=='professional-repair:'+operation_id for row in history):
        return {'duplicate':True}
    actor,mechanic,plan=_prepare(location_id,user_id,payload)
    if actor.character.revision!=payload['actor_revision'] or mechanic.character.revision!=payload['mechanic_revision']:
        raise ConflictError('Снаряжение или деньги изменились. Откройте ремонт заново.')
    actor_data=deepcopy(actor.character.data or {});mechanic_data=deepcopy(mechanic.character.data or {})
    if balance(actor_data)<plan['price']:raise ValidationError('Не хватает денег на ремонт')
    target=resolve_item_path(actor_data,plan['item_path'])
    target.clear();target.update(plan['repaired'])
    actor_data.setdefault('inventory',{})['money']=balance(actor_data)-plan['price']
    mechanic_data.setdefault('inventory',{})['money']=money(balance(mechanic_data)+plan['price'],label='Деньги механика')
    journal=actor_data.setdefault('health',{}).setdefault('combatMeta',{}).setdefault('actionJournal',[])
    journal.append({'id':'professional-repair:'+operation_id,'timestamp':datetime.now(timezone.utc).isoformat(),
        'action':'professional_repair','kind':'professional_repair','label':'Ремонт у механика',
        'status':'completed','location_id':location_id,'action_points':0,'movement_points':0,'free_actions':0,
        'outcome':f"{plan['item_name']}: {plan['price']} ₽"})
    actor_data['health']['combatMeta']['actionJournal']=journal[-100:]
    actor.character.data=actor_data;mechanic.character.data=mechanic_data
    flag_modified(actor.character,'data');flag_modified(mechanic.character,'data')
    db.session.flush()
    return {'duplicate':False,'price':plan['price'],'item_name':plan['item_name'],'mode':plan['mode'],
        'result':plan['result'],'actor_character_id':actor.character_id,'mechanic_character_id':mechanic.character_id}
