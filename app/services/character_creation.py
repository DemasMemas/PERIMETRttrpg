"""Каталог и поэтапное создание персонажа по книге «Персонаж (3)»."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import random
import re

from app.services.exceptions import ValidationError


@lru_cache(maxsize=1)
def _catalog():
    return json.loads((Path(__file__).parents[1] / 'data/character_creation.json').read_text(encoding='utf-8'))


def catalog():
    result=deepcopy(_catalog())
    result['skillOrder']=[f'{group}.{key}' for group,items in result['skills'].items() for key in items]
    from app.services.world_rules import mutant_catalog
    result['mutants']=[{'name':mutant['name']} for mutant in mutant_catalog()]
    return result


def builtin_templates(category=None):
    result=[]
    for kind,key in (('background','backgrounds'),('special_trait','traits')):
        if category and category!=kind:continue
        for entry in _catalog()[key]:
            attrs={'ruleId':entry['id'],'rulesVersion':_catalog()['version'],
                   'source':entry['source'],'available':entry.get('available',True)}
            if kind=='background':
                attrs.update(pluses=entry['pluses'],minuses=entry['minuses'],entries=entry['entries'],
                    skillBonuses=[{'skill':e['skill'],'bonus':e['amount']} for e in entry['entries'] if e['type']=='skill'])
            else:attrs.update(effect=entry['effect'],cost=entry['cost'],maxCount=entry['maxCount'])
            result.append({'id':entry['templateId'],'name':entry['name'],'category':kind,
                           'description':entry.get('effect',entry.get('pluses','')),'attributes':attrs,
                           'price':0,'weight':0,'volume':0,'compatible_ids':[]})
    return result


def preserve_creation(current,incoming,*,is_gm=False):
    """Расчёт создания неизменяем; обычное сохранение никогда не применяет его заново."""
    if 'creation' in (current or {}):
        incoming['creation']=deepcopy(current['creation'])
        if not is_gm:
            def descriptions(value):
                if isinstance(value,list):return [descriptions(item) for item in value]
                if not isinstance(value,dict):return value
                return {key:(' '.join(item.split()) if key in {'pluses','minuses','effect'} and isinstance(item,str) else item) for key,item in value.items()}
            for parent,key in (('basic','background'),('features','specialTraits')):
                original=(current.get(parent) or {}).get(key)
                if descriptions((incoming.get(parent) or {}).get(key))!=descriptions(original):
                    raise ValidationError('Изменение предыстории и черт готового персонажа доступно ГМу')
                # Переносы исходной таблицы могут отличаться от показанного текста.
                # Обычное сохранение листа сохраняет авторский снимок правил.
                if key in (current.get(parent) or {}):incoming.setdefault(parent,{})[key]=deepcopy(original)
    else:incoming.pop('creation',None)
    if 'characterRewards' in (current or {}):incoming['characterRewards']=deepcopy(current['characterRewards'])
    else:incoming.pop('characterRewards',None)


def validate_trait_selection(current,incoming):
    old_features=(current or {}).get('features') or {}
    features=(incoming or {}).get('features') or {}
    if not isinstance(features,dict):
        if features==old_features:return
        raise ValidationError('Особые черты должны быть объектом')
    old=old_features.get('specialTraits',[]) if isinstance(old_features,dict) else []
    traits=features.get('specialTraits',[])
    if traits==old:return
    if not isinstance(traits,list):raise ValidationError('Особые черты должны быть списком')
    receipts=(((incoming or {}).get('creation') or {}).get('zoneHistory') or {}).get('earnedTraits',[])
    allowed={r['id']:r['ruleId'] for r in receipts if isinstance(r,dict) and 'id' in r and 'ruleId' in r}
    consumed=set();regular=0
    for selected in traits:
        origin=selected.get('historyReceipt') if isinstance(selected,dict) else None
        if origin in allowed and origin not in consumed and selected.get('ruleId')==allowed[origin]:consumed.add(origin)
        else:regular+=1
    if regular>3:raise ValidationError('Можно иметь не более трёх начальных особых черт')
    counts={}
    for selected in traits:
        if not isinstance(selected,dict):raise ValidationError('Некорректная особая черта')
        entry=next((t for t in _catalog()['traits'] if selected.get('ruleId')==t['id'] or selected.get('name')==t['name']),None)
        if not entry:continue
        counts[entry['id']]=counts.get(entry['id'],0)+1
        if entry['limitText'] and counts[entry['id']]>entry['maxCount']:raise ValidationError(f'Превышен лимит черты «{entry["name"]}»')


def seed_templates(session):
    from app.models.templates import ItemTemplate
    existing={str((t.attributes or {}).get('ruleId')) for t in session.query(ItemTemplate).filter(ItemTemplate.category.in_(['background','special_trait']))}
    added=0
    for template in builtin_templates():
        if template['attributes']['ruleId'] in existing:continue
        fields={k:v for k,v in template.items() if k!='id'}
        session.add(ItemTemplate(**fields));added+=1
    return added


def _integer(value,label,minimum=0,maximum=1000):
    if type(value) is not int or not minimum<=value<=maximum:
        raise ValidationError(f'{label}: требуется целое число от {minimum} до {maximum}')
    return value


def _list(value,label,maximum):
    if not isinstance(value,list) or len(value)>maximum:raise ValidationError(f'{label}: неверный список')
    return value


def apply_trait_choice(choice,levels,specialized,counts):
    c=_catalog();paths={f'{g}.{k}' for g,items in c['skills'].items() for k in items}
    if not isinstance(choice,dict) or set(choice)-{'id','skill','weaponType','caliber','idea'}:raise ValidationError('Неверные параметры особой черты')
    entry=next((t for t in c['traits'] if t['id']==choice.get('id')),None)
    if not entry:raise ValidationError('Неизвестная особая черта')
    if any(not isinstance(v,str) or len(v)>300 for k,v in choice.items() if k!='id'):raise ValidationError('Некорректные параметры черты')
    counts[entry['id']]=counts.get(entry['id'],0)+1
    if entry['limitText'] and counts[entry['id']]>entry['maxCount']:raise ValidationError(f'Превышен лимит черты «{entry["name"]}»')
    name=entry['name'];weapon_choice=None
    if name in {'Специалист','Заинтересованный'}:
        path=choice.get('skill')
        if path not in paths:raise ValidationError(f'Выберите навык для «{name}»')
        if name=='Специалист':
            if path in specialized:raise ValidationError('Вторую специализацию в тот же навык взять нельзя')
            specialized.add(path);levels[path]+=3
    if name in {'Курс знакомства с оружием','Слияние с оружием'}:
        if choice.get('weaponType') not in c['weaponTypes']:raise ValidationError('Выберите вид оружия для черты')
        weapon_choice=(choice['weaponType'],'professional' if name=='Слияние с оружием' else 'familiar')
    if name=='Патронщик' and not str(choice.get('caliber','')).strip():raise ValidationError('Выберите калибр для «Патронщик»')
    if name=='Шапочка из фольги' and not str(choice.get('idea','')).strip():raise ValidationError('Опишите идею персонажа')
    if name=='Маленький рост':
        for p,v in {'physical.agility':1,'other.stealth':1,'social.charisma':-2}.items():levels[p]+=v
    if name=='Близорукий':levels['physical.shooting']-=1
    if any(v<1 or v>20 for v in levels.values()):raise ValidationError('Начальный навык должен быть от 1 до 20')
    return {'ruleId':entry['id'],'templateId':entry['templateId'],'name':name,'effect':entry['effect'],
            'cost':entry['cost'],'choices':{k:v for k,v in choice.items() if k!='id'}},weapon_choice


def build_character(name,plan,*,roll_history=True,finalize=True):
    if not isinstance(plan,dict):raise ValidationError('Параметры создания должны быть объектом')
    allowed={'backgroundId','specializations','raises','lowers','traits','finalRaises','cashPoints','backgroundWeapon','favoredMutant','weaponPair','zoneHistory','purchases'}
    if set(plan)-allowed:raise ValidationError('Неизвестные параметры создания')
    for key in ('backgroundWeapon','favoredMutant'):
        if key in plan and (not isinstance(plan[key],str) or len(plan[key])>100):raise ValidationError('Некорректный выбор для предыстории')
    c=_catalog();paths={f'{g}.{k}':label for g,items in c['skills'].items() for k,label in items.items()}
    bg=next((b for b in c['backgrounds'] if b['id']==plan.get('backgroundId')),None)
    if not bg or not bg.get('available',True):raise ValidationError('Выберите доступную предысторию')
    if bg['name']=='Охотник':
        from app.services.world_rules import mutant_profile
        favored=str(plan.get('favoredMutant','')).strip()
        if favored and not mutant_profile(favored):raise ValidationError('Выберите избранного мутанта из бестиария')
        if finalize and not favored:raise ValidationError('Выберите избранного мутанта для Охотника')
    levels={p:5 for p in paths};ledger=[]
    points=30+sum(e['amount'] for e in bg['entries'] if e['type']=='points')
    specialization_limit=10+sum(e['amount'] for e in bg['entries'] if e['type']=='specializations')
    specs=_list(plan.get('specializations',[]),'Специализации',16)
    if any(not isinstance(p,str) for p in specs) or len(specs)!=len(set(str(p) for p in specs)) or any(p not in paths for p in specs):raise ValidationError('Каждый навык может иметь только одну специализацию')
    if len(specs)>specialization_limit:raise ValidationError('Недостаточно специализаций')
    def snapshot(stage):ledger.append({'stage':stage,'levels':dict(levels),'points':points})
    def apply_background(positive):
        for e in bg['entries']:
            if (e.get('amount',0)>0)!=positive:continue
            selected=[e['skill']] if e['type']=='skill' else [p for p in paths if p.startswith(e['group']+'.')] if e['type']=='group' else list(paths) if e['type']=='all_skills' else []
            for path in selected:levels[path]=max(1,levels[path]+e['amount'])
    apply_background(True);snapshot('background_positive')
    for path in specs:levels[path]+=3
    snapshot('specializations')
    def raise_skills(key,price_levels=None):
        nonlocal points
        values=plan.get(key,{})
        if not isinstance(values,dict) or set(values)-paths.keys():raise ValidationError('Неизвестный навык повышения')
        for path,amount in values.items():
            amount=_integer(amount,'Количество повышений',maximum=19)
            for _ in range(amount):
                target=levels[path]+1
                if target>20:raise ValidationError('Покупкой нельзя повысить навык выше 20')
                price_target=(price_levels[path]+1) if price_levels is not None else target
                cost=max(1,price_target-10)
                if finalize and points<cost:raise ValidationError(f'Недостаточно очков: {paths[path]} до {target}')
                points-=cost;levels[path]=target
                if price_levels is not None:price_levels[path]=price_target
    raise_skills('raises');snapshot('raises')
    # Последующие понижения меняют навык, но не его ступень оплаты.
    final_price_levels=dict(levels)
    lowers=plan.get('lowers',{})
    if not isinstance(lowers,dict) or set(lowers)-paths.keys():raise ValidationError('Неизвестный навык понижения')
    count=0
    for path,amount in lowers.items():
        amount=_integer(amount,'Количество понижений',maximum=5);count+=amount
        if count>5:raise ValidationError('Понижением можно получить не более 5 очков')
        levels[path]-=amount*3
        if levels[path]<1:raise ValidationError('При ручном понижении навык не может стать меньше 1')
    points+=count;snapshot('lowers')
    apply_background(False);snapshot('background_negative')
    traits=[];counts={};selected_specs=set(specs);weapon_choices=[]
    for choice in _list(plan.get('traits',[]),'Особые черты',3):
        before_trait=dict(levels)
        item,weapon_choice=apply_trait_choice(choice,levels,selected_specs,counts)
        for path in paths:final_price_levels[path]+=max(0,levels[path]-before_trait[path])
        if weapon_choice:weapon_choices.append(weapon_choice)
        traits.append(item)
    points-=sum(t['cost'] for t in traits)
    if finalize and points<0:raise ValidationError('Недостаточно очков для выбранных черт')
    snapshot('traits');raise_skills('finalRaises',final_price_levels);snapshot('final_raises')
    if finalize and len(specs)<specialization_limit and len(selected_specs)<len(paths):
        raise ValidationError(f'Необходимо распределить все {specialization_limit} специализаций или специализировать все навыки')
    if any(v>20 for v in levels.values()):raise ValidationError('Начальный уровень навыка не может превышать 20')
    cash=_integer(plan.get('cashPoints',0),'Очки в стартовый капитал',maximum=10000)
    if finalize and cash>points:raise ValidationError('Недостаточно очков для обмена на деньги')
    points-=cash
    initial_cash=5000 if bg['name']=='Бедный' else 10000
    entry_money={'Богат деньгами':50000,'Полевой Ученый':5000,'Кладовщик':20000,'Вор в законе':20000}.get(bg['name'],0)
    specialized={k:{'level':'unfamiliar','xp':0} for k in c['weaponTypes']}
    aliases={'Дробовиками':'shotguns','Пистолетами':'pistols','Пистолетами-пулеметами':'smgs','Штурмовыми винтовками':'assaultRifles','Снайперскими винтовками':'sniperRifles','Гранатометами':'grenadeLaunchers','Пулеметами':'machineGuns'}
    for e in bg['entries']:
        text=e.get('text','')
        if 'Знаком с' in text or 'Профессионал с' in text:
            weapon=plan.get('backgroundWeapon') if 'выбранным видом' in text else next((k for phrase,k in aliases.items() if phrase in text),None)
            if weapon not in specialized:
                if not finalize and not weapon:continue
                raise ValidationError('Выберите вид оружия для предыстории')
            weapon_choices.append((weapon,'professional' if text.startswith('Профессионал') else 'familiar'))
    for weapon,level in weapon_choices:
        if specialized[weapon]['level']!='professional':specialized[weapon]['level']=level
    data={'basic':{'name':name,'background':{'ruleId':bg['id'],'templateId':bg['templateId'],'name':bg['name'],
          'pluses':bg['pluses'],'minuses':bg['minuses'],'skillBonuses':[],'choices':{k:deepcopy(plan[k]) for k in ('backgroundWeapon','favoredMutant','weaponPair') if k in plan}}},
          'skills':{g:{k:{'base':levels[f'{g}.{k}'],'bonus':0,'xp':0,'specialized':f'{g}.{k}' in selected_specs} for k in items} for g,items in c['skills'].items()},
          'features':{'specialTraits':traits},'inventory':{'money':initial_cash+cash*200,'pockets':[],'backpack':[]},
          'creation':{'version':c['version'],'plan':deepcopy(plan),'ledger':ledger,'initialCapital':initial_cash+cash*200,
                      'entryMoney':entry_money,'worldEntryGranted':False,'completed':finalize,
                      'finalRaisePricing':{'levels':final_price_levels,'nextCosts':{p:max(1,v+1-10) for p,v in final_price_levels.items()}}}}
    from app.services.character_starting_purchase import apply_purchase
    apply_purchase(data,plan.get('purchases',[]),initial_cash+cash*200,allow_debt=not finalize)
    data['skills'].update(skillPoints=points,specializations=specialization_limit-len(specs),specialized=specialized)
    from app.services.character_traits import passive_profile
    data['inventory']['pocketMaxVolume']=max(0,10+passive_profile(data)['pocket_cells'])
    from app.services.addictions import initial_addiction
    addictions={'Курящий':'Сигареты','Алкоголик':'Водка','Кофеман':'Кофе','Стимпанк':'Регенеративный стимулятор','Любитель маковых булочек':'Обезболивающий стимулятор'}
    initial_addictions=[]
    for t in traits:
        if t['name'] in addictions:initial_addictions.append((addictions[t['name']],t['name']))
    if bg['name']=='Гангстер':initial_addictions.append(('Самопальный стимпак',bg['name']))
    for item_name,source in initial_addictions:initial_addiction(data.setdefault('health',{}),item_name,source)
    from app.services.health import apply_health_maximums
    apply_health_maximums(data)
    if bg['name']=='Крепкий орешек':data['health'].setdefault('combatMeta',{})['toughNut']={'uses':1,'minimumPain':0}
    history=plan.get('zoneHistory',False)
    if type(history) is not bool:raise ValidationError('История в Зоне: требуется да/нет')
    if history and roll_history:
        data['creation']['zoneHistory']=generate_zone_history(data)
        data['creation']['zoneHistory'].update(presentationRequired=True,presentationComplete=False,confirmedThrough=0)
    return data


def monthly_income(data):
    skills=data.get('skills') or {};values=[]
    for group,items in _catalog()['skills'].items():
        for key in items:
            skill=(skills.get(group) or {}).get(key) or {}
            values.append((int(skill.get('base',5)),group))
    best=max(v for v,g in values);bonus=(best-10)//2
    rate=.25 if any(g=='other' for v,g in values if v==best) else .10
    return {'options':[2500*(1+bonus*rate)],'highestSkill':best,'bonus':bonus}


def generate_zone_history(data,rng=None):
    rng=rng or random.SystemRandom();months=rng.randint(1,12);events=[]
    cells={c['cell']:c['value'] for c in _catalog()['rules']['Предыстории']['cells']}
    incomes=monthly_income(data)
    for month in range(1,months+1):
        event=rng.randint(1,12);rolls=[];details=[];delta=0;multiplier=1;pending=[];extra={}
        def pick(column,row):
            roll=rng.randint(1,4);rolls.append(roll);details.append(cells.get(f'{column}{row+roll-1}',''));return roll
        if event==1:pick('A',34);pick('B',34)
        elif event==2:
            kind=pick('A',41)
            if kind<4:
                detail=pick({1:'B',2:'C',3:'D'}[kind],41)
                multiplier-=({2:[1,.75,.25,.1],3:[1.5,.5,.4,.2]}.get(kind,[0,0,0,0]))[detail-1]
                if kind==1:extra['addiction']=['Регенеративный стимулятор','Обезболивающий стимулятор','Водка','Сигареты'][detail-1]
        elif event==3:
            pick('A',48);debt=rng.randint(1,12);rolls.append(debt);details.append(f'Долг {debt*10000} рублей')
        elif event==4:pick('A',55);pick('B',55)
        elif event==5:pick('A',62);multiplier-=[2,1,.5,.25][pick('B',62)-1]
        elif event==6:multiplier-=[1,.75,.5,.25][pick('A',69)-1]
        elif event==7:multiplier+=[.25,.5,.75,1][pick('A',76)-1]
        elif event==8:delta=[5000,10000,15000,25000][pick('A',83)-1]
        elif event==9:pick('A',90);pick('B',90)
        elif event==10:
            group=pick('A',97);amount=[1,2,4,6][pick('B',97)-1]
            extra['teacher']={'group':{1:'social',2:'physical',3:'other',4:None}[group],'xp':amount}
            pending.append(f'Выбрать навык для {amount} XP без бонусов обучения')
        elif event==11:
            limit=[3,5,7,10][pick('A',104)-1];extra['hardening']={'limit':limit};pending.append(f'Выбрать бесплатную черту до {limit} очков либо {limit//2} очков навыков')
        elif event==12:pick('A',112);pick('B',112)
        events.append({'month':month,'event':event,'title':cells[f'A{18+event}'],'rolls':rolls,'details':details,
            'incomeMultiplier':multiplier,'cashDelta':delta,'incomeOptions':[v*multiplier+delta for v in incomes['options']],'pending':pending,**extra})
    return {'months':months,'income':incomes,'events':events,'settled':False}
