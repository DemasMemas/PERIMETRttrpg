"""Разбор по таблицам книги: один инструмент, исходное состояние и реальная добыча."""
from copy import deepcopy
from math import floor
import random
from app.services.exceptions import ValidationError
from app.services.crafting_rules import catalog

TOOLS={'нож':5,'напильник':5,'ножницы':10,'плоскогубцы':10,'молоток':10,'отвертка':15,'мультитул':25}


def tool_bonus(item):
    from app.services.crafting import normalized
    name=normalized(item.get('name'))
    return 5 if 'нож' in name.split() or name.startswith('штык нож') else TOOLS.get(name,0)


def kind_of(item):
    from app.services.combat import CombatService
    category=item.get('category');attrs=CombatService._template_attributes(item)
    if attrs.get('flamethrower_tank') or attrs.get('flamethrower_air'):return None
    if category in {'armor','helmet','gas_mask'}:return 'armor'
    if category in {'weapon','melee_weapon'}:return 'weapon'
    if category=='ammo':return 'ammo'
    if category=='grenade':return 'grenade'
    if attrs.get('portable_light') or attrs.get('night_vision') or str(item.get('name','')).casefold() in {'кпк','рация','датчик'} or category=='device' and item.get('subcategory') not in {'battery'}:return 'electronics'
    return None


def plan(data,payload):
    from app.services.crafting import entries,normalized,_template
    from app.services.combat import CombatService
    from app.services.trade import _contains_individual_items
    from app.services.equipment_modifications import family,installed
    item=next((item for path,item in entries(data) if path==payload.get('path')),None)
    kind=kind_of(item) if item else None
    if not kind:raise ValidationError('Этот предмет нельзя разобрать по имеющейся таблице')
    if _contains_individual_items(item):raise ValidationError('Сначала извлеките содержимое, патроны и навесные модули')
    quantity=payload.get('quantity',1)
    if type(quantity) is not int or quantity<1 or quantity>int(item.get('quantity',1)):raise ValidationError('Неверное количество для разбора')
    if kind!='ammo' and quantity!=1:raise ValidationError('Оружие и снаряжение разбирают по одному экземпляру')
    if kind=='ammo' and quantity<20:raise ValidationError('Для разбора нужно не меньше 20 одинаковых патронов')
    tool=next((item for path,item in entries(data) if path==payload.get('tool_path')),None)
    if not tool or not tool_bonus(tool):raise ValidationError('Выберите один инструмент для разбора')
    if tool is item:raise ValidationError('Разбираемый предмет нельзя использовать как инструмент')
    attrs=CombatService._template_attributes(item)
    raw_class=str(item.get('itemClass',item.get('item_class',attrs.get('item_class',''))))
    template=CombatService._equipment_template(item)
    if not raw_class and template:raw_class=str(template.item_class or '')
    factor={'Н':25,'H':25,'3':0,'2':-25,'1':-50}.get(raw_class.upper(),-75)
    condition=0
    if kind=='weapon':
        current,maximum=CombatService._weapon_durability(deepcopy(item));percent=100*current/maximum
        condition=25 if percent>=86 else 0 if percent>=71 else -25 if percent>=51 else -50 if percent>=31 else -75
    elif kind=='armor':condition={1:25,2:0,3:-25,4:-50,5:-75}.get(int(item.get('stage',1)),25)
    amount_bonus=-20 if quantity<30 else 10 if quantity<40 else 40 if quantity<50 else 75
    book_subject=family(item) or {'ammo':'Боеприпасы','grenade':'Гранаты','electronics':'Электроника'}.get(kind)
    library=(data.get('crafting') or {}).get('library',[])
    book=25 if any(b.get('book_kind')=='equipment' and normalized(b.get('book_subject')).replace(' броня','')==normalized(book_subject) for b in library) else 0
    bonus=CombatService._base_skill_modifier(data,'skills.other.engineering')*10+tool_bonus(tool)+book
    bonus+=amount_bonus if kind=='ammo' else factor+condition if kind in {'weapon','armor'} else factor if kind=='grenade' else 0
    missing=[m['name'] for band in catalog()['salvage'][kind] for m in band['materials'] if not _template(m['name'])]
    if missing:raise ValidationError('В каталоге нет компонентов результата: '+', '.join(sorted(set(missing))))
    return {'kind':kind,'name':item['name'],'quantity':quantity,'item':item,'path':payload['path'],'tool':tool,
        'bonus':bonus,'book_bonus':book,'duration_seconds':1200 if kind in {'weapon','electronics'} or item.get('category') in {'helmet','gas_mask'} else 1800,
        'modifications':len(installed(item)) if kind in {'weapon','armor'} else 0}


def resolve(data,job,*,rng=None,band_override=None):
    from app.services.crafting import normalized
    from app.services.character_traits import trait_count
    rng=rng or random.SystemRandom()
    lost=bool(trait_count(data,'Утилизатор') and rng.randint(1,100)<=10)
    roll=rng.randint(1,100);total=roll+job['bonus'];kind=job['salvage_kind']
    band=next(b for b in catalog()['salvage'][kind] if b['maximum'] is None or total<=b['maximum'])
    if band_override is not None:band=catalog()['salvage'][kind][band_override]
    if not band['materials'] and total>99 and band_override is None:
        bands=catalog()['salvage'][kind]
        index=bands.index(band)
        band=next((previous for previous in reversed(bands[:index]) if previous['materials']),band)
    materials=deepcopy(band['materials']);name=normalized(job['escrow'][0]['item']['name'])
    if kind=='ammo':
        item=job['escrow'][0]['item'];attrs=item.get('attributes') or {};grade=str(item.get('itemClass',item.get('item_class',attrs.get('item_class','1'))))
        powder={'1':.5,'2':1,'3':2}.get(grade,1);metal=1
        variant=normalized(item.get('ammo_variant') or attrs.get('ammo_variant') or name)
        if 'светошум' in variant:powder*=2;metal=0
        elif 'газ' in variant:powder*=.5
        elif variant in {'bp','ep'} or ' бп' in variant or ' эп' in variant:metal=2
        elif variant in {'ubp','rip'} or 'убп' in variant:metal=3
        elif any(s in variant for s in ('разрыв','explosive')):powder*=2;metal=2
        elif any(s in variant for s in ('зажиг','incendiary')):powder*=2
        for material in materials:material['quantity']*=powder if normalized(material['name'])=='порох' else metal
        if job['quantity']>=40:materials.append({'name':'Металлические детали','quantity':1})
        if job['quantity']>=50:materials.append({'name':'Порох','quantity':1})
    multiplier=(1+.25*job.get('modifications',0))*(1.5 if trait_count(data,'Утилизатор') else 1)
    totals={}
    for m in materials:totals[m['name']]=totals.get(m['name'],0)+m['quantity']
    return {'roll':roll,'total':total,'bonus':job['bonus'],'salvage_lost':lost,'source_band':band['source_text'],
        'materials':[] if lost else [{'name':name,'quantity':floor(amount*multiplier)} for name,amount in totals.items() if floor(amount*multiplier)>0]}
