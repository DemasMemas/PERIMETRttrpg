"""Каталожные ловушки и сохранение конкретных гранат в результате создания."""
from copy import deepcopy
from uuid import uuid4
import math
from app.services.exceptions import ConflictError

PROFILES={
    'recipe-m4':dict(name='Кустарный капкан',price=1000,weight=2,volume=4,difficulty=12,kind='beartrap',damage=75,pain=3,bleeding='medium'),
    'recipe-m5':dict(name='Сеть',price=750,weight=1,volume=4,difficulty=8,kind='net'),
    'recipe-m7':dict(name='Мина',price=3500,weight=.5,volume=.5,difficulty=15,kind='mine',fragments=[500,250],blast=[600,300],penetration=[50,20]),
    'recipe-m8':dict(name='Растяжка',price=300,weight=0,volume=2,difficulty=10,kind='tripwire',grenades=1),
    'recipe-m9':dict(name='Растяжка двойная',price=500,weight=0,volume=2,difficulty=18,kind='tripwire',grenades=2),
    'recipe-m10':dict(name='Мина усиленная',price=5000,weight=.5,volume=.5,difficulty=18,kind='mine',fragments=[650,500,350],blast=[1000,750,500],penetration=[65,35,15]),
    'recipe-m11':dict(name='Звуковая ловушка',price=400,weight=.5,volume=.5,difficulty=12,kind='alarm',reusable=True),
    'recipe-m12':dict(name='Игольные ежи',price=450,weight=.5,volume=4,difficulty=None,kind='caltrops',damage=25,pain=3,bleeding='medium'),
    'recipe-m13':dict(name='Капкан',price=2850,weight=2,volume=4,difficulty=12,kind='beartrap',damage=120,pain=8,bleeding='severe'),
}


def tripwire_grenade_allowed(item, lobby_id=None):
    """Проверить и сам предмет, и его исходный шаблон, если он сохранён."""
    from app.services.combat import CombatService
    from app.services.item_template_lookup import resolve_item_template
    if not isinstance(item,dict) or item.get('category') not in {'grenade','ammo'}:
        return False
    profile=CombatService._explosive_profile(item)
    if not profile:return False
    keys={profile['key']}
    labels=[item.get('name'),item.get('subcategory')]
    template=resolve_item_template(item,lobby_id)
    if template:
        keys.add(CombatService._explosive_key(template.name) or '')
        labels.extend([template.name,template.subcategory])
    return not (keys & {'vog25','n1012','og12','molotov','improvised_bomb'}
        or any(key.startswith('underbarrel_') for key in keys)
        or any('подствольн' in str(label or '').casefold() for label in labels))


def radial_damage(profile,distance):
    table=profile.get('radial_damage')
    if not table:return None
    index=max(0,min(len(table['blast'])-1,math.ceil(distance)-profile.get('radial_first_distance',1)))
    return {key:table[key][index] for key in ('fragments','blast','penetration')}


def templates():
    from app.services.catalog_import import template
    result=[]
    for key,p in PROFILES.items():
        text='Ловушка для установки на клетку 1×1 м. Установка: 5 ОД плюс извлечение предмета.'
        if p['kind'] in {'beartrap','caltrops'}:text+=f" При наступании: {p['damage']} урона в ногу, боль {p['pain']}, внешнее кровотечение {'сильное' if p['bleeding']=='severe' else 'среднее'}. Одноразовая."
        if p['kind']=='beartrap':text+=' Завершает перемещение и ход наступившего.'
        if p['kind']=='net':text+=' Удерживает до проверки Силы СЛ 12 (2 ОД) либо разрезания режущим/рубящим оружием (3 ОД). Одноразовая.'
        if p['kind']=='alarm':text+=' При срабатывании: шум 20, слышит вся подлокация. Повторное использование.'
        if p['kind']=='mine':text+=' Взрывается в конце хода наступившего. Осколки: '+', '.join(map(str,p['fragments']))+'; волна: '+', '.join(map(str,p['blast']))+'; бронебойность: '+', '.join(str(v)+'%' for v in p['penetration'])+' (по метрам).'
        if p['kind']=='tripwire':text+=f" Требует {p['grenades']} гранат. Цена и масса готовой ловушки учитывают выбранные гранаты. Их взрыв происходит на 3 секунды раньше обычного."
        result.append(template(p['name'],'misc',p['price'],p['weight'],p['volume'],text,'Ловушки',
            {'trap_recipe':key,'trap_kind':p['kind'],'source_recipe_id':key,'craft_only':True}))
    return result


def bind_crafted_output(produced,recipe_id,escrow,lobby_id):
    profile=PROFILES.get(recipe_id)
    if not profile or profile['kind']!='tripwire':return
    from app.services.item_template_lookup import resolve_item_template
    grenades=[]
    for entry in escrow:
        if entry['component']!=0:continue
        original=entry['item'];count=entry['amount']
        if not tripwire_grenade_allowed(original,lobby_id):raise ConflictError('Эта граната не подходит для создания растяжки')
        for _ in range(int(count)):
            grenade=deepcopy(original);grenade.update(id=str(uuid4()),quantity=1);grenades.append(grenade)
    if len(grenades)!=profile['grenades']:raise ConflictError('Изменился состав гранат растяжки')
    produced['trapGrenades']=grenades
    weight=0;price=profile['price']
    for grenade in grenades:
        template=resolve_item_template(grenade,lobby_id)
        weight+=float(grenade.get('weight',template.weight if template else 0) or 0)
        price+=int(grenade.get('price',template.price if template else 0) or 0)
    produced['weight']=weight;produced['price']=price


def preserve(current,incoming):
    from app.services.portable_lights import walk
    previous={item.get('id'):item for _,item in walk(current or {},True) if item.get('id')}
    for _,item in walk(incoming,True):
        old=previous.get(item.get('id')) or {}
        if 'trapGrenades' in old:item['trapGrenades']=deepcopy(old['trapGrenades'])
        else:item.pop('trapGrenades',None)
