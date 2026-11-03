"""Стартовый закуп по ценам каталога; бонус предыстории выдаётся отдельно."""
from copy import deepcopy
import math
import uuid

from app.services.exceptions import ValidationError


def _text(value):
    return str(value or '').strip().casefold().replace('ё','е')


def eligible(template):
    category=template.category
    attrs=template.attributes or {}
    if category.endswith('_module') or category in {'modification','artifact','background','special_trait'}:
        return False
    if attrs.get('installedModules') or attrs.get('modifications'):
        return False
    if category=='ammo':
        variant=_text(attrs.get('ammo_variant') or attrs.get('ammo_kind') or attrs.get('special_version'))
        return variant in {'','base','standard','обычный','обычная','базовый','базовая'}
    if category in {'headphones','glasses','gloves','jewelry','backpack','vest','belt','pouch','magazine','food','other','misc'}:
        return True
    section=_text(attrs.get('section') or template.subcategory)
    if category=='consumable' and section in {'еда','продукты','продукты питания','пища','прочее'}:
        return True
    if category in {'weapon','melee_weapon','grenade','armor','helmet','gas_mask','armor_plate','consumable'}:
        return _text(template.item_class or attrs.get('import_tier') or attrs.get('item_class')) in {'1','1 класс','класс 1','i'}
    return False


def available_templates():
    from app.models.templates import ItemTemplate
    return [t for t in ItemTemplate.query.order_by(ItemTemplate.category,ItemTemplate.name).all() if eligible(t)]


def catalog():
    return [{'id':t.id,'name':t.name,'category':t.category,'subcategory':t.subcategory,
             'item_class':t.item_class,'price':max(0,t.price or 0),'description':t.description or '',
             'attributes':{key:value for key,value in (t.attributes or {}).items() if key in {
                 'section','caliber','ammo_group','ammo_kind','ammo_variant','capacity','damage','penetration','range','isLoader'}}}
            for t in available_templates()]


def item_from_template(template,quantity=1):
    attrs=deepcopy(template.attributes or {})
    item={**attrs,'id':uuid.uuid4().hex,'templateId':template.id,'name':template.name,
          'category':template.category,'subcategory':template.subcategory,'itemClass':template.item_class,
          'description':template.description,'price':template.price or 0,'weight':template.weight or 0,
          'volume':template.volume or 0,'quantity':quantity,'attributes':attrs,
          'installedModules':[],'modifications':[],'contents':[], 'compatible_ids':deepcopy(template.compatible_ids or [])}
    if attrs.get('uses') is not None:item.update(uses=attrs['uses'],maxUses=attrs['uses'])
    if template.category in {'weapon','melee_weapon','armor','helmet','gas_mask'}:
        maximum=attrs.get('max_durability',100)
        item.update(durability=maximum,maxDurability=maximum)
    if template.category in {'weapon','melee_weapon'}:
        item.update(ammo=0,fixedAmmo=[],installedMagazine=None)
        if template.category=='weapon':item['model']=template.name
    from app.services.weapon_catalog import normalize_template
    return normalize_template(item)


def apply_purchase(data,selection,budget,*,allow_debt=False):
    if not isinstance(selection,list) or len(selection)>200:raise ValidationError('Некорректный список стартового закупа')
    spent=0;receipt=[]
    if selection:
        templates={t.id:t for t in available_templates()}
        for line in selection:
            if not isinstance(line,dict) or set(line)!={'templateId','quantity'}:
                raise ValidationError('В закупе разрешены только шаблон и количество, без модификаций')
            template_id=line['templateId'];quantity=line['quantity']
            if type(template_id) is not int or template_id not in templates:raise ValidationError('Предмет недоступен при стартовом закупе')
            if type(quantity) is not int or not 1<=quantity<=10000:raise ValidationError('Некорректное количество покупок')
            template=templates[template_id]
            price=template.price or 0
            if not math.isfinite(price) or price<0:raise ValidationError('Некорректная цена предмета')
            spent+=price*quantity
            if spent>budget and not allow_debt:raise ValidationError('Превышен бюджет стартового закупа')
            receipt.append({'templateId':template_id,'quantity':quantity,'unitPrice':price,'name':template.name})
            # Оружие — отдельные экземпляры: износ и боеприпасы не должны быть общими.
            if template.category in {'weapon','melee_weapon'}:
                if quantity>50:raise ValidationError('Слишком много отдельных экземпляров оружия')
                data.setdefault('weapons',[]).extend(item_from_template(template) for _ in range(quantity))
            else:data['inventory']['pockets'].append(item_from_template(template,quantity))
    data['inventory']['money']=budget-spent
    data['creation']['startingPurchase']={'budget':budget,'spent':spent,'remaining':budget-spent,'items':receipt,'completed':not allow_debt}


def grant_creation_money(data):
    """Деньги выдаются один раз после создания, отдельно от допуска в мир."""
    creation=data.get('creation')
    if not isinstance(creation,dict) or creation.get('creationMoneyGranted') or creation.get('worldEntryGranted') or 'entryMoney' not in creation:return False
    inventory=data.setdefault('inventory',{})
    initial=inventory.get('money',0)+creation.get('entryMoney',0)
    balance=initial
    history=creation.get('zoneHistory') or {}
    for event in history.get('events') or []:
        balance=max(0,balance+(event.get('incomeOptions') or [0])[0])
    if history:history['cashTotal']=balance-initial
    inventory['money']=max(0,balance)
    creation.update(creationMoneyGranted=True,creationMoneyBase=initial,creationMoneyAmount=balance-(initial-creation.get('entryMoney',0)))
    return True


def grant_world_entry(character):
    from sqlalchemy.orm.attributes import flag_modified
    data=deepcopy(character.data or {});creation=data.get('creation')
    if not isinstance(creation,dict) or creation.get('worldEntryGranted') or 'entryMoney' not in creation:return False
    history=creation.get('zoneHistory') or {}
    if history.get('presentationRequired') and not history.get('presentationComplete'):
        raise ValidationError('Сначала завершите помесячную историю жизни в Зоне')
    if history and not history.get('settled'):raise ValidationError('Сначала завершите выбор событий жизни в Зоне в листе персонажа')
    amount=creation.get('creationMoneyAmount',0) if creation.get('creationMoneyGranted') else creation.get('entryMoney',0)+history.get('cashTotal',0)
    inventory=data.setdefault('inventory',{})
    if not creation.get('creationMoneyGranted'):inventory['money']=max(0,inventory.get('money',0)+amount)
    creation.update(worldEntryGranted=True,worldEntryAmount=amount)
    character.data=data;flag_modified(character,'data')
    return True
