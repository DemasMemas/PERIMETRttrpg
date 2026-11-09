"""Однократное применение сохранённых событий жизни, в том числе помесячно."""
from copy import deepcopy
import math
import uuid

from app.services.character_creation import _catalog,apply_trait_choice
from app.services.character_development import add_skill_experience
from app.services.exceptions import ValidationError,ConflictError


def _history(data):
    creation=data.get('creation') or {};history=creation.get('zoneHistory')
    if not isinstance(history,dict):raise ValidationError('У персонажа нет истории в Зоне')
    return creation,history


def debt_creditors(lobby_id):
    from app.extensions import db
    from app.models import WorldFactionConfig
    from app.services.world_factions_preset import ready_world_factions

    config=db.session.get(WorldFactionConfig,lobby_id)
    groups=config.groups if config and config.groups else ready_world_factions()['groups']
    return {group['id']:group['name'] for group in groups}


def starting_balance(initial,history):
    balance=initial
    for event in history.get('events',[]):
        balance=max(0,balance+(event.get('incomeOptions') or [0])[0])
    for event in history.get('events',[]):
        debt=event.get('debt')
        if not debt:continue
        paid=min(balance,debt['amount'])
        balance-=paid
        debt.update(paidAtCreation=paid,remaining=debt['amount']-paid)
    return balance


def repay_debt(original,month,amount):
    data=deepcopy(original);_,history=_history(data)
    if type(month) is not int or not 1<=month<=len(history['events']):
        raise ValidationError('Некорректный месяц истории')
    event=history['events'][month-1]
    debt=event.get('debt') or {}
    if not event.get('confirmed') or not debt.get('creditor') or not debt.get('remaining',0)>0:
        raise ValidationError('У этого месяца нет открытого долга')
    if type(amount) not in (int,float) or not 0<amount<=debt['remaining'] or not math.isfinite(amount) or round(amount,2)!=amount:
        raise ValidationError('Укажите положительную сумму в пределах остатка долга с точностью до копеек')
    money=data['inventory']['money']
    if money<amount:raise ValidationError('У персонажа не хватает денег для погашения')
    data['inventory']['money']=round(money-amount,2)
    debt['remaining']=round(debt['remaining']-amount,2)
    return data


def _requires_choice(event):
    return event.get('teacher') or event.get('hardening') or (event.get('debt') or {}).get('chooseCreditor')


def _apply_month(data,event,selected,day,creditors):
    if not isinstance(selected,dict):raise ValidationError('Некорректный выбор события')
    if event.get('confirmed'):
        if selected!=event.get('choice',{}):raise ConflictError('Этот месяц уже подтверждён с другим решением')
        return
    catalog=_catalog();paths={f'{g}.{k}' for g,items in catalog['skills'].items() for k in items}
    traits=data.setdefault('features',{}).setdefault('specialTraits',[])
    counts={}
    for trait in traits:counts[trait.get('ruleId')]=counts.get(trait.get('ruleId'),0)+1
    if (event.get('debt') or {}).get('chooseCreditor'):
        creditor=selected.get('creditor')
        if set(selected)!={'creditor'} or not isinstance(creditor,str) or creditor not in creditors:
            raise ValidationError('Выберите группировку-кредитора')
        event['debt'].update(creditorId=creditor,creditor=creditors[creditor])
    elif event.get('teacher'):
        if set(selected)!={'skill'}:raise ValidationError('Для Учителя выберите навык')
        path=selected['skill'];group=event['teacher']['group']
        if not isinstance(path,str) or path not in paths or group and not path.startswith(group+'.'):raise ValidationError('Навык не соответствует выпавшей категории Учителя')
        add_skill_experience(data,path,event['teacher']['xp'],apply_multiplier=False,maximum_level=20)
    elif event.get('hardening'):
        limit=event['hardening']['limit']
        if selected=={'decline':True}:
            data['skills']['skillPoints']+=limit//2
        else:
            if set(selected)!={'trait'}:raise ValidationError('Выберите черту Закалки или откажитесь от неё')
            choice=selected['trait'];entry=next((t for t in catalog['traits'] if isinstance(choice,dict) and t['id']==choice.get('id')),None)
            if not entry or not 0<entry['cost']<=limit:raise ValidationError('Закалка допускает только черту с положительной стоимостью в пределах выпавшего потолка')
            if entry['name']=='Специалист':raise ValidationError('Черту «Специалист» нельзя получить через Закалку')
            levels={p:data['skills'][p.split('.')[0]][p.split('.')[1]]['base'] for p in paths}
            specialized={p for p in paths if data['skills'][p.split('.')[0]][p.split('.')[1]].get('specialized')}
            item,weapon=apply_trait_choice(choice,levels,specialized,counts)
            receipt={'id':uuid.uuid4().hex,'month':event['month'],'ruleId':item['ruleId']}
            item['historyReceipt']=receipt['id'];traits.append(item)
            _history(data)[1].setdefault('earnedTraits',[]).append(receipt)
            for path,value in levels.items():
                group,key=path.split('.');data['skills'][group][key].update(base=value,specialized=path in specialized)
            if weapon:
                current=data['skills']['specialized'][weapon[0]]
                if current['level']!='professional':current['level']=weapon[1]
            dependencies={'Курящий':'Сигареты','Алкоголик':'Водка','Кофеман':'Кофе','Стимпанк':'Регенеративный стимулятор','Любитель маковых булочек':'Обезболивающий стимулятор'}
            if item['name'] in dependencies:
                from app.services.addictions import initial_addiction
                initial_addiction(data.setdefault('health',{}),dependencies[item['name']],item['name'],day)
    elif selected:raise ValidationError('У этого месяца нет выбора навыка или черты')
    if event.get('addiction'):
        from app.services.addictions import initial_addiction
        initial_addiction(data.setdefault('health',{}),event['addiction'],f'История: месяц {event["month"]}',day)
    event.update(choice=deepcopy(selected),pending=[],confirmed=True)


def _refresh(original,data):
    from app.services.character_traits import sync_pocket_capacity
    sync_pocket_capacity(original,data)
    from app.services.health import apply_health_maximums
    health=apply_health_maximums(data);health['current']=health['max']
    for zone in health.get('zones',{}).values():
        if isinstance(zone,dict) and 'max' in zone:zone['current']=zone['max']


def _finish(data,history):
    creation=data['creation']
    if not creation.get('creationMoneyGranted'):
        initial=data['inventory']['money']+creation.get('entryMoney',0)
        history['cashTotal']=starting_balance(initial,history)-initial
    history.update(settled=True,confirmedThrough=len(history['events']),presentationComplete=True)


def settle_history(original,choices,day=1,*,creditors=None):
    data=deepcopy(original);creation,history=_history(data)
    if history.get('settled'):return data
    if creation.get('worldEntryGranted'):raise ValidationError('Историю нужно завершить до входа в мир')
    if not isinstance(choices,dict):raise ValidationError('Некорректные решения событий')
    required={str(e['month']) for e in history['events'] if _requires_choice(e)}
    if set(choices)!=required:raise ValidationError('Нужно принять решение по каждому событию с выбором')
    for event in history['events']:_apply_month(data,event,choices.get(str(event['month']),{}),day,creditors or {})
    _finish(data,history);_refresh(original,data)
    return data


def confirm_history_month(original,month,choice,day=1,*,creditors=None):
    data=deepcopy(original);creation,history=_history(data)
    if type(month) is not int or not 1<=month<=len(history['events']):raise ValidationError('Некорректный месяц истории')
    if not isinstance(choice,dict):raise ValidationError('Некорректное решение месяца')
    confirmed=history.get('confirmedThrough',0);event=history['events'][month-1]
    if month<=confirmed:
        if choice!=event.get('choice',{}):raise ConflictError('Этот месяц уже подтверждён с другим решением')
        return data
    if month!=confirmed+1:raise ValidationError('Подтверждайте месяцы истории по порядку')
    was_settled=history.get('settled',False)
    if was_settled:
        if choice!=event.get('choice',{}):raise ConflictError('Решения завершённой истории нельзя изменить')
    else:
        if creation.get('worldEntryGranted'):raise ValidationError('Историю нужно завершить до входа в мир')
        _apply_month(data,event,choice,day,creditors or {});_refresh(original,data)
    history['confirmedThrough']=month
    if month==len(history['events']):
        if was_settled:history['presentationComplete']=True
        else:_finish(data,history)
    return data
