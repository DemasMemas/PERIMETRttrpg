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
