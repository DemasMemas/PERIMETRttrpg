"""Утверждённый каталог навесных модулей и их боевые свойства."""
from copy import deepcopy


# Цена, категория, вес и объём согласованы автором 03.11.2026.
_DEFINITIONS = [
    ('suppressor','Глушитель','barrel',-15,5000,'2',{'noise':'=2'},{'silenced':True,'caliber_specific':True},'Шум = 2.'),
    ('muzzle_brake','ДТК','barrel',-5,3500,'2',{'accuracy':1},{'caliber_specific':True},'Точность +1.'),
    ('short_barrel','Укорочение ствола','barrel',25,0,None,{'accuracy':-2,'min_strength':-2},
     {'shortened_barrel':True,'weapon_size_reduction':2,'removable':False,'not_for_sale':True},
     'Точность −2, требуемая сила −2, размер оружия −2. После установки не снимается.'),
    ('collimator','Коллиматорный прицел','scope',-5,3500,'1',{'range':5},{},'Дальность +5.'),
    ('optical_scope','Оптический прицел','scope',-15,7500,'2',{'range':15},{'close_aimed_accuracy':-1},
     'Дальность +15. Прицельная стрельба ближе 10 м: точность −1.'),
    ('night_scope','Ночной прицел','scope',-10,5000,'2',{'range':5},{'night_scope':True},
     'Дальность +5. Прицельная стрельба ночью без ПНВ не получает штрафа темноты.'),
    ('laser','ЛЦУ','handguard',-5,7500,'2',{'accuracy':1},{'laser':True},
     'Точность +1; дополнительно +1 при неприцельной стрельбе или использовании ПНВ.'),
    ('flashlight','Фонарик','handguard',-5,3500,'1',{}, {'flashlight':True},'Работает как ручной фонарик.'),
    ('tactical_grip','Тактическая рукоять','handguard',5,6500,'2',{'accuracy':1},{'sitting_accuracy':1},
     'Точность +1; дополнительно +1 при стрельбе сидя.'),
    ('bipod','Сошки','handguard',-30,5000,'2',{}, {'bipod':True,'main_effect':{'ergonomics':75,'requires_rest':True}},
     'На упоре: эргономика +75, требование силы не применяется.'),
    ('bayonet','Штык-нож','handguard',-10,1500,'1',{}, {'bayonet':True,'melee_template_name':'Штык-нож','allowed_attacks':['Колющий'],'action_points':2},
     'Колющий удар за 2 ОД. Характеристики одноимённого оружия ближнего боя.'),
    ('n101_2','Подствольный гранатомет N101-2','handguard',-25,12500,'3',{},
     {'type':'grenade_launcher','caliber':'N-101-2','range':30,'reload_action_points':5,'assault_rifle_only':True},
     'Только для штурмовых винтовок. Перезарядка 5 ОД, дальность 30, калибр N-101-2, включая СШ/газ/дым.'),
    ('gp25','Подствольный гранатомет ГП-25','handguard',-30,15000,'3',{},
     {'type':'grenade_launcher','caliber':'ВОГ-25','range':25,'reload_action_points':5,'assault_rifle_only':True},
     'Только для штурмовых винтовок. Перезарядка 5 ОД, дальность 25, калибр ВОГ-25, включая СШ/газ/дым.'),
]


def builtin_templates():
    return [dict(id=-30001-index,name=name,category='weapon_module',subcategory=slot,item_class=tier,
                 price=price,weight=.5,volume=1,description=description,compatible_ids=[],
                 attributes={**deepcopy(extra),'moduleRuleId':key,'slot_type':slot,
                             'modifiers':{'ergonomics':ergonomics,**deepcopy(modifiers)}})
            for index,(key,name,slot,ergonomics,price,tier,modifiers,extra,description) in enumerate(_DEFINITIONS)]


def missing_templates(existing,subcategory=None):
    existing=[t for t in existing if t.get('category')=='weapon_module']
    names={str(t.get('name','')).casefold().replace('ё','е') for t in existing}
    keys={(t.get('attributes') or {}).get('moduleRuleId') for t in existing}
    return [t for t in builtin_templates() if (not subcategory or t['subcategory']==subcategory)
            and t['name'].casefold().replace('ё','е') not in names and t['attributes']['moduleRuleId'] not in keys]


def normalize_module_template(template):
    if template.get('category')!='weapon_module':return template
    original=template.get('attributes') or {}
    known=next((t for t in builtin_templates() if t['attributes']['moduleRuleId']==original.get('moduleRuleId')
                or t['name'].casefold().replace('ё','е')==str(template.get('name','')).casefold().replace('ё','е')),None)
    if not known:return template
    result=deepcopy(template)
    result.update(weight=.5,volume=1)
    result['attributes']={**known['attributes'],**original}
    result['attributes']['modifiers']={**known['attributes']['modifiers'],**(original.get('modifiers') or {})}
    return result


def module_attributes(module):
    return module.get('attributes') or {} if isinstance(module,dict) else {}


def installed(weapon,flag):
    return any(module_attributes(m).get(flag) for m in (weapon or {}).get('installedModules') or [])


def weapon_stat(weapon,stat,base):
    from app.services.combat import CombatService
    value=base
    for module in (weapon or {}).get('installedModules') or []:
        if not isinstance(module,dict):continue
        modifiers=module.get('modifiers') or module_attributes(module).get('modifiers') or {}
        value=CombatService._apply_numeric_modifier(value,modifiers.get(stat))
    return value


def uses_nvg(data):
    equipment=(data or {}).get('equipment') or {}
    modules=(equipment.get('helmet') or {}).get('installedModules') or []
    return any(isinstance(m,dict) and (m.get('slotType')=='nvg' or module_attributes(m).get('night_vision'))
               and m.get('active',True) and m.get('enabled',True) and not m.get('raised',False) for m in modules)


def conditional_accuracy(weapon,*,fire_mode,distance,posture,nvg=False):
    result=0
    for module in (weapon or {}).get('installedModules') or []:
        attrs=module_attributes(module)
        if fire_mode=='aimed' and distance is not None and distance<10:result+=attrs.get('close_aimed_accuracy',0)
        if attrs.get('laser') and (fire_mode=='unaimed' or nvg):result+=1
        if posture=='sitting':result+=attrs.get('sitting_accuracy',0)
    return result


def bayonet_profile(weapon):
    from app.models.templates import ItemTemplate
    from app.services.combat import CombatService
    from app.services.exceptions import ValidationError
    if not installed(weapon,'bayonet'):raise ValidationError('Штык-нож не установлен')
    template=next((t for t in ItemTemplate.query.filter_by(category='melee_weapon').all()
                   if str(t.name).casefold().replace('ё','е').replace('–','-')=='штык-нож'),None)
    if not template:raise ValidationError('В каталоге отсутствует оружие ближнего боя «Штык-нож»')
    profile=CombatService._weapon_damage_profile({'name':template.name,'templateId':template.id,
                                                'weight':template.weight,'attributes':deepcopy(template.attributes)},'Колющий')
    profile.update(action_points=2,melee_damage_type='piercing')
    return profile
