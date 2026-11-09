"""Питание дозиметра и доступ персонажа к показаниям радиации."""
from copy import deepcopy
import math
import random

DOSIMETER_SECONDS=7*24*3600


def burned(item):
    return bool(isinstance(item,dict) and (item.get('electronicsState') or {}).get('burned'))


def items(data):
    """Физические приборы, включая модули шлема, снятого на пояс."""
    from app.services.portable_lights import walk
    result=walk(data or {},True)
    belt=((data or {}).get('equipment') or {}).get('belt') or {}
    for key in ('storedItem','extraStoredItem'):
        stored=belt.get(key)
        if not isinstance(stored,dict):continue
        for path,item in walk({'equipment':{'stored':stored.get('savedAttributes') or stored}},True):
            result.append((['equipment','belt',key,*(['savedAttributes'] if stored.get('savedAttributes') else []),*path[2:]],item))
    return result


def is_dosimeter(item):
    return isinstance(item,dict) and ((item.get('attributes') or {}).get('device_kind')=='dosimeter'
        or str(item.get('name') or '').strip().casefold()=='дозиметр')


def state(item):
    saved=item.get('deviceState') or {}
    remaining=saved.get('remaining_seconds',DOSIMETER_SECONDS)
    if type(remaining) not in {int,float} or not math.isfinite(remaining):remaining=0
    return {'on':saved.get('on') is True,'remaining_seconds':max(0,min(DOSIMETER_SECONDS,remaining))}


def active(data, *, jammed_by=None):
    from app.services.portable_lights import walk
    from app.services.artifact_effects import device_block_reason
    return any(is_dosimeter(item) and state(item)['on'] and state(item)['remaining_seconds']>0
        and not device_block_reason(data,item,jammed_by=jammed_by) for _,item in walk(data,True))


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
    def identity(path,item):return item.get('id') or (tuple(path),item.get('templateId'),item.get('name'))
    old={identity(path,item):deepcopy(item.get('electronicsState')) for path,item in items(current)}
    for path,item in items(incoming):
        saved=old.get(identity(path,item))
        if saved is None:item.pop('electronicsState',None)
        else:item['electronicsState']=deepcopy(saved)
        if is_dosimeter(item):item['deviceState']=deepcopy(previous.get(item.get('id'),state({})))
        if (item.get('attributes') or {}).get('isActive'):
            from app.services.artifact_effects import device_block_reason
            if device_block_reason(incoming,item):item['attributes']['isActive']=False


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
    if on:
        from app.services.artifact_effects import require_device_available
        require_device_available(data,item)
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


def electric_protection(data,item):
    """Изоляция надетой брони защищает перечисленную автором электронику."""
    from app.services.portable_lights import definition
    from app.services.equipment_modifications import rule,installed
    if not isinstance(item,dict):return False
    kind=definition(item);attrs=item.get('attributes') or {}
    if {'electric','electrical'} & set(attrs.get('anomaly_immunities') or []):return True
    if kind=='improved':return True
    if kind=='pda' and any(mod.get('ruleId')=='pda-b13' for mod in installed(item)):return True
    electronic=(kind in {'pda','dosimeter','hand','head','module'} or item.get('category')=='detector'
        or item.get('slotType')=='nvg' or attrs.get('night_vision') or attrs.get('type') in {'anomaly','artifact','radio'}
        or str(item.get('name') or '').strip().casefold() in {'рация','радиостанция','радио'})
    armor=((data or {}).get('equipment') or {}).get('armor') or {}
    return bool(electronic and isinstance(armor,dict) and not armor.get('protectionDisabled')
        and (rule(armor,'electronics_insulation') or any(mod.get('ruleId')=='armor-d14' for mod in installed(armor))))


def expose_pdas(data,damage,*,forced=False,ignore_protection=False):
    """Разовый урон электроаномалии до защиты тела; ОЗ устройства не вводятся."""
    from app.services.portable_lights import walk,definition,pda_state
    from app.services.equipment_modifications import installed
    if damage<=0 and not forced:return []
    result=[]
    for _,item in walk(data):
        if definition(item)!='pda':continue
        status=pda_state(item)
        if status.get('broken'):continue
        mods={mod['ruleId'] for mod in installed(item)}
        protected=not ignore_protection and electric_protection(data,item);recharged=False
        if forced:
            broken=not protected
        elif 'pda-b23' in mods:
            broken=not protected and random.randint(1,100)<=10
            if not broken:status['charge_percent']=100;recharged=True
        else:broken=not protected and damage>200
        if broken:
            status.update(on=False,broken=True)
            item['electronicsState']={'burned':True}
            meta=(data.get('health') or {}).get('combatMeta') or {}
            if (meta.get('pdaDynamo') or {}).get('id')==item.get('id'):meta.pop('pdaDynamo',None)
        if broken or recharged:
            item['pdaState']=status
            result.append({'slot':'pda','name':item.get('name'),'item_id':item.get('id'),'broken':broken,'recharged':recharged})
    return result


def expose_devices(data,damage,*,forced=False,ignore_protection=False):
    """Порог относится к исходному разовому урону электроаномалии, не защите тела."""
    from app.services.portable_lights import definition,bindings
    from app.services.artifact_effects import is_electronic
    result=expose_pdas(data,damage,forced=forced,ignore_protection=ignore_protection)
    if damage<=200 and not forced:return result
    for path,item in items(data):
        kind=definition(item);attrs=item.get('attributes') or {};name=str(item.get('name') or '').strip().casefold()
        electronic=(kind in {'hand','improved','head','module','dosimeter'} or item.get('slotType')=='nvg'
            or attrs.get('night_vision') or item.get('category')=='detector' or attrs.get('type') in {'anomaly','artifact','radio'}
            or attrs.get('device_kind')=='radio' or name in {'рация','радиостанция','радио'}
            or forced and kind!='pda' and is_electronic(item))
        if not electronic or burned(item) or not ignore_protection and electric_protection(data,item):continue
        item['electronicsState']={'burned':True}
        if (item.get('controllerState') or {}).get('kind') in {'timer','remote_detonator'}:
            item['controllerState']['armed']=False
        if kind=='dosimeter':item['deviceState']={**state(item),'on':False}
        if item.get('slotType')=='nvg' or attrs.get('night_vision'):item['active']=item['enabled']=False
        if 'isActive' in attrs:attrs['isActive']=False
        for binding in bindings(data):
            if binding.get('id')==item.get('id'):binding['on']=False
        result.append({'slot':'electronics','name':item.get('name') or 'Прибор','item_id':item.get('id'),
            'path':path,'broken':True,'burned':True})
    return result
