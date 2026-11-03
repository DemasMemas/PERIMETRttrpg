"""Однократное применение сохранённых событий жизни до входа в мир."""
from copy import deepcopy
import uuid

from app.services.character_creation import _catalog,apply_trait_choice
from app.services.character_development import add_skill_experience
from app.services.exceptions import ValidationError


def settle_history(original,choices,day=1):
    data=deepcopy(original);creation=data.get('creation') or {};history=creation.get('zoneHistory')
    if not isinstance(history,dict):raise ValidationError('У персонажа нет истории в Зоне')
    if history.get('settled'):return data
    if creation.get('worldEntryGranted'):raise ValidationError('Историю нужно завершить до входа в мир')
    if not isinstance(choices,dict):raise ValidationError('Некорректные решения событий')
    required={str(e['month']) for e in history['events'] if e.get('teacher') or e.get('hardening')}
    if set(choices)!=required:raise ValidationError('Нужно принять решение по каждому Учителю и Закалке')
    catalog=_catalog();paths={f'{g}.{k}' for g,items in catalog['skills'].items() for k in items}
    traits=data.setdefault('features',{}).setdefault('specialTraits',[])
    counts={}
    for trait in traits:counts[trait.get('ruleId')]=counts.get(trait.get('ruleId'),0)+1
    receipts=[];initial_money=data['inventory']['money']+creation.get('entryMoney',0);balance=initial_money
    for event in history['events']:
        balance=max(0,balance+event['incomeOptions'][0])
        if event.get('addiction'):
            from app.services.addictions import initial_addiction
            initial_addiction(data.setdefault('health',{}),event['addiction'],f'История: месяц {event["month"]}',day)
        selected=choices.get(str(event['month']),{})
        if not isinstance(selected,dict):raise ValidationError('Некорректный выбор события')
        if event.get('teacher'):
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
                if not entry or entry['cost']>limit:raise ValidationError('Черта дороже выпавшего потолка Закалки')
                if entry['name']=='Специалист':raise ValidationError('Черту «Специалист» нельзя получить через Закалку')
                levels={p:data['skills'][p.split('.')[0]][p.split('.')[1]]['base'] for p in paths}
                specialized={p for p in paths if data['skills'][p.split('.')[0]][p.split('.')[1]].get('specialized')}
                item,weapon=apply_trait_choice(choice,levels,specialized,counts)
                receipt={'id':uuid.uuid4().hex,'month':event['month'],'ruleId':item['ruleId']}
                item['historyReceipt']=receipt['id'];receipts.append(receipt);traits.append(item)
                for path,value in levels.items():
                    group,key=path.split('.');data['skills'][group][key].update(base=value,specialized=path in specialized)
                if weapon:
                    current=data['skills']['specialized'][weapon[0]]
                    if current['level']!='professional':current['level']=weapon[1]
                dependencies={'Курящий':'Сигареты','Алкоголик':'Водка','Кофеман':'Кофе','Стимпанк':'Регенеративный стимулятор','Любитель маковых булочек':'Обезболивающий стимулятор'}
                if item['name'] in dependencies:
                    from app.services.addictions import initial_addiction
                    initial_addiction(data.setdefault('health',{}),dependencies[item['name']],item['name'],day)
        if selected:event['choice']=deepcopy(selected)
        event['pending']=[]
    history.update(settled=True,cashTotal=balance-initial_money,earnedTraits=receipts)
    from app.services.character_traits import sync_pocket_capacity
    sync_pocket_capacity(original,data)
    from app.services.health import apply_health_maximums
    health=apply_health_maximums(data);health['current']=health['max']
    for zone in health.get('zones',{}).values():
        if isinstance(zone,dict) and 'max' in zone:zone['current']=zone['max']
    return data
