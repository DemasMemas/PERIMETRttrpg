"""Питание дозиметра и доступ персонажа к показаниям радиации."""
from copy import deepcopy
import math
import random

DOSIMETER_SECONDS=7*24*3600


def is_dosimeter(item):
    return isinstance(item,dict) and ((item.get('attributes') or {}).get('device_kind')=='dosimeter'
        or str(item.get('name') or '').strip().casefold()=='дозиметр')


def state(item):
    saved=item.get('deviceState') or {}
    remaining=saved.get('remaining_seconds',DOSIMETER_SECONDS)
    if type(remaining) not in {int,float} or not math.isfinite(remaining):remaining=0
    return {'on':saved.get('on') is True,'remaining_seconds':max(0,min(DOSIMETER_SECONDS,remaining))}


def active(data):
    from app.services.portable_lights import walk
    return any(is_dosimeter(item) and state(item)['on'] and state(item)['remaining_seconds']>0 for _,item in walk(data,True))


def advance(data,seconds):
    from app.services.portable_lights import walk
    changed=False
    for _,item in walk(data,True):
        if not is_dosimeter(item):continue
        power=state(item)
        if power['on'] and seconds>0:
            power['remaining_seconds']=max(0,power['remaining_seconds']-seconds)
            if not power['remaining_seconds']:power['on']=False
            item['deviceState']=power;changed=True
    return changed


def preserve(current,incoming):
    from app.services.portable_lights import walk
    previous={item.get('id'):state(item) for _,item in walk(current or {},True) if is_dosimeter(item) and item.get('id')}
    for _,item in walk(incoming,True):
        if is_dosimeter(item):item['deviceState']=deepcopy(previous.get(item.get('id'),state({})))


def templates():
    from app.services.catalog_import import template
    return [template('Дозиметр','device',500,.25,.5,
        'Показывает радиацию на глобальной карте и подлокациях, пока включён. Одной батарейки хватает на неделю работы.',
        'Детекторы',{'device_kind':'dosimeter','battery_duration_seconds':DOSIMETER_SECONDS,'source_recipe_id':'recipe-f10'})]


def controller_templates():
    from app.services.catalog_import import template
    return [template(name,'device',1000,.1,.1,description,'Электроника',{'device_kind':kind,'source_recipe_id':recipe})
        for name,kind,recipe,description in [
            ('Дистанционный взрыватель','remote_detonator','recipe-f6','Подрыв с привязанного КПК на дистанции до 250 м. Расходуется вместе с зарядом.'),
            ('Таймер','timer','recipe-f12','Запуск взрывчатки или переключение устройства по времени. Максимальная отсрочка — 24 часа.')]]


def switchable(item):
    from app.services.portable_lights import definition
    return ((definition(item) in {'pda','dosimeter','hand','improved','head','module'})
        or (item.get('slotType')=='nvg' or (item.get('attributes') or {}).get('night_vision'))
        or item.get('category') in {'device','detector'}) and (item.get('attributes') or {}).get('device_kind') not in {'timer','remote_detonator'}


def switch(item,data,on):
    """Таймер использует настоящее питание прибора, включая штатные фонари и КПК."""
    from app.services.exceptions import ValidationError
    from app.services.portable_lights import definition,pda_state,power,bindings
    kind=definition(item)
    if kind=='pda':
        status=pda_state(item)
        if on and (status.get('broken') or status['charge_percent']<=0):raise ValidationError('КПК неисправен или разряжен')
        status['on']=on;item['pdaState']=status
        if not on:item.setdefault('pdaNetwork',{})['jammer_on']=False
    elif kind=='dosimeter':
        status=state(item)
        if on and status['remaining_seconds']<=0:raise ValidationError('Дозиметр разряжен')
        status['on']=on;item['deviceState']=status
    elif item.get('slotType')=='nvg' or (item.get('attributes') or {}).get('night_vision'):
        if on and power(item)['uses_left']<=0:raise ValidationError('ПНВ разряжен')
        item['active']=item['enabled']=on
    elif kind in {'hand','improved','head','module'}:
        status=power(item)
        if on and status['uses_left']<=0:raise ValidationError('Фонарь разряжен')
        status['scheduled_on']=on
        for binding in bindings(data):
            if binding.get('id')==item.get('id'):
                binding['on']=on
                if on:binding['period_used']=True
    elif switchable(item):
        attrs=item.setdefault('attributes',{})
        battery=next((module for module in item.get('installedModules') or [] if module.get('slotType')=='battery'),None)
        if on and not attrs.get('isActive'):
            if battery:
                charge=(battery.get('attributes') or {}).get('power',0)
                if charge<=0:raise ValidationError('Батарейка прибора разряжена')
                battery.setdefault('attributes',{})['power']=max(0,charge-1)
            elif 'power' in attrs and attrs['power']<=0:raise ValidationError('Прибор разряжен')
        attrs['isActive']=on
    else:raise ValidationError('Этот предмет не имеет включения и выключения')


def expose_pdas(data,damage):
    """Разовый урон электроаномалии до защиты тела; ОЗ устройства не вводятся."""
    from app.services.portable_lights import walk,definition,pda_state
    from app.services.equipment_modifications import installed
    if damage<=0:return []
    result=[]
    for _,item in walk(data):
        if definition(item)!='pda':continue
        status=pda_state(item)
        if status.get('broken'):continue
        mods={mod['ruleId'] for mod in installed(item)}
        protected='pda-b13' in mods;recharged=False
        if 'pda-b23' in mods:
            broken=not protected and random.randint(1,100)<=10
            if not broken:status['charge_percent']=100;recharged=True
        else:broken=not protected and damage>200
        if broken:
            status.update(on=False,broken=True)
            meta=(data.get('health') or {}).get('combatMeta') or {}
            if (meta.get('pdaDynamo') or {}).get('id')==item.get('id'):meta.pop('pdaDynamo',None)
        if broken or recharged:
            item['pdaState']=status
            result.append({'slot':'pda','name':item.get('name'),'item_id':item.get('id'),'broken':broken,'recharged':recharged})
    return result
