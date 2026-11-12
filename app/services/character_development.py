"""Награды по завершении сессии/цели и опыт в выбранный навык."""
import math
import random
from copy import deepcopy
from app.services.character_creation import _catalog
from app.services.character_traits import background_is,skill_learning_multiplier
from app.services.exceptions import ValidationError


def add_skill_experience(data,path,amount,*,apply_multiplier=True,maximum_level=None):
    group,key=path.split('.')
    skill=data.setdefault('skills',{}).setdefault(group,{}).setdefault(key,{'base':5,'bonus':0,'xp':0})
    gained=amount*(skill_learning_multiplier(data,path) if apply_multiplier else 1)+float(skill.get('learningRemainder',0))
    skill['learningRemainder']=gained-math.floor(gained)
    skill['xp']=int(skill.get('xp',0))+math.floor(gained)
    while skill['xp']>=max(1,int(skill.get('base',5))+1-10) and (maximum_level is None or skill['base']<maximum_level):
        skill['xp']-=max(1,int(skill.get('base',5))+1-10)
        skill['base']=int(skill.get('base',5))+1
    return skill


def award_session(data,payload,*,rng=None):
    rng=rng or random.SystemRandom()
    if not isinstance(payload,dict) or set(payload)-{'operationId','roleplay','heroism','contribution','gmBonus','trainingSkill','weaponType','usedBackgroundWeapon'}:
        raise ValidationError('Неизвестные параметры награды')
    operation=payload.get('operationId')
    if not isinstance(operation,str) or not 1<=len(operation)<=100:raise ValidationError('Нужен идентификатор награды')
    for key in ('roleplay','heroism','contribution','gmBonus'):
        if type(payload.get(key,False)) is not bool:raise ValidationError('Выберите основания награды')
    if type(payload.get('usedBackgroundWeapon', False)) is not bool:
        raise ValidationError('Укажите, использован ли выбранный вид оружия')
    previous=(data.get('characterRewards') or [])
    found=next((r for r in previous if r['operationId']==operation),None)
    if found:return deepcopy(found)
    paths={f'{g}.{k}' for g,items in _catalog()['skills'].items() for k in items}
    skill=payload.get('trainingSkill')
    if skill is not None and (not isinstance(skill,str) or skill not in paths):raise ValidationError('Неизвестный навык обучения')
    free=1+sum(int(payload.get(key,False)) for key in ('roleplay','heroism','contribution','gmBonus'))
    training=0;rolls=[]
    if skill:
        security_training = background_is(data, 'СБшник') and skill == 'physical.shooting'
        training=1+int(background_is(data,'Бедный') or background_is(data,'Призывник') and skill.startswith('physical.') or security_training or background_is(data,'Истинный пацифист') and skill.startswith('social.'))
        risk=background_is(data,'Атлет') or background_is(data,'Призывник') and skill.startswith('other.') or background_is(data,'Давний ветеран') and skill=='physical.shooting'
        if security_training:
            chosen = (((data.get('basic') or {}).get('background') or {}).get('choices') or {}).get('backgroundWeapon')
            valid_choice = isinstance(chosen, str) and chosen in _catalog()['weaponTypes']
            used = payload.get('usedBackgroundWeapon', False)
            if 'usedBackgroundWeapon' not in payload and 'weaponType' in payload:
                weapon = payload['weaponType']
                if not isinstance(weapon, str) or weapon not in _catalog()['weaponTypes']:
                    raise ValidationError('Неизвестный вид оружия')
                used = weapon == chosen
            risk = risk or not (valid_choice and used)
        if risk:
            roll=rng.randint(1,100);rolls.append(roll)
            if roll>50:training=0
        if training:
            add_skill_experience(data,skill,training)
            group,key=skill.split('.')
            learned=(data.get('crafting') or {}).get('library',[])
            if data['skills'][group][key]['base']<16 and any(book.get('book_kind')=='skill' and book.get('skill_path')==skill for book in learned):
                add_skill_experience(data,skill,1,apply_multiplier=False,maximum_level=16)
                training+=1
    data.setdefault('skills',{})['skillPoints']=int(data.get('skills',{}).get('skillPoints',0))+free
    result={'operationId':operation,'freePoints':free,'trainingSkill':skill,'trainingPoints':training,'rolls':rolls,
            'reasons':{k:payload.get(k,False) for k in ('roleplay','heroism','contribution','gmBonus')}}
    data['characterRewards']=[*previous,result]
    return deepcopy(result)
