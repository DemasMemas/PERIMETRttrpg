"""Свойства врача: копии профилей и личный запас использований медикаментов."""
from copy import deepcopy
import math

from app.services.character_traits import background_is,trait_count
from app.services.exceptions import ValidationError


ECONOMY_MEDICINES={
    'Бинт','Стерильный Бинт','Жгут','Жгут турникет','Жгут Альфа','Антисептический тампон',
    'Пластырь с гемостатиком','Пластырь "Стазис"','Губка коллагеновая','Бутылек соляного раствора',
    'Спирт','Пенициллин (Таблетка)','Сангвинил (Таблетка)','Настойка мяты','Аугментин (Таблетка)',
    'Плитка Гематогена','Пакет физраствора','Пакет крови','Анальгин (Таблетка)','Аспирин (Таблетка)',
    'Настойка боярышника','Ибупрофен (Таблетка)','Настойка подорожника','Активированный уголь (Таблетка)',
    'Антирад. Препарат "Йод-Плюс" (Таблетка)','Очищенная настойка коры','Шина','Шина Шарнирова',
    'Кустарный набор "Айболит"','Хирургический набор "Крест"','Хирургический набор "Хирург"',
    'Набор полного восстановления конечности',
}


def _name(value):return str(value or '').strip().casefold().replace('ё','е')


def economy_applies(data,template):
    return bool(trait_count(data,'На жизни сэкономим') and _name(template.name) in {_name(n) for n in ECONOMY_MEDICINES})


def prepare_profile(profile,direct,item,doctor,template=None):
    profile=deepcopy(profile);direct=deepcopy(direct)
    if template is not None:item={'name':template.name,'subcategory':template.subcategory,'attributes':template.attributes or {}}
    section=_name((item.get('attributes') or {}).get('section') or item.get('subcategory'))
    medicine=not direct.get('nutrition') and not direct.get('satisfy_water') and section not in {'еда','алкоголь','табачные изделия'} and not any(token in _name(item.get('name')) for token in ('банка пива','водка','самогон','сигарет'))
    if background_is(doctor,'Врач по бумагам') and medicine:
        positive={'hp','strength_delta','agility_delta','accuracy_delta','will_delta','psy_defense_delta'}
        negative={'pain_delta','exhaustion_delta','radiation_delta','infection_delta','infection_progress_delta',
                  'psy_delta','bleeding_modifier_delta','weight_delta','movement_points_delta','intoxication_delta'}
        for key,value in list(direct.items()):
            if isinstance(value,(int,float)) and not isinstance(value,bool) and (key in positive and value>0 or key in negative and value<0):
                direct[key]=math.copysign(math.floor(abs(value)*1.5),value)
        for effect in profile.get('effects') or []:
            value=effect.get('value')
            if not isinstance(value,(int,float)):continue
            kind=effect.get('type')
            if kind in {'heal','regeneration'} and value>0 or kind in {'pain','exhaustion','radiation','infection','psy_state','intoxication'} and value<0:
                effect['value']=math.copysign(math.floor(abs(value)*1.5),value)
        for modifier in profile.get('modifiers') or []:
            value=modifier.get('value');stat=modifier.get('stat')
            if isinstance(value,(int,float)) and (stat in {'strength','agility','accuracy','will','psy_defense'} and value>0 or stat in {'weight','movement_points'} and value<0):
                modifier['value']=math.copysign(math.floor(abs(value)*1.5),value)
    if background_is(doctor,'Гений медицинской мысли'):
        for effect in profile.get('effects') or []:
            if effect.get('type')=='blood_recovery':effect['value']=max(1,float(effect.get('value') or 1))+1
    direct['bleeding_rank_bonus']=bleeding_rank_bonus(doctor)
    injectable=(direct.get('application_form')=='injectable' or direct.get('medical_difficulty') is not None) and not any(direct.get(k) for k in ('requires_infusion_tool','requires_shock','blood_collection','applications'))
    if injectable:
        if background_is(doctor,'Гений медицинской мысли') or trait_count(doctor,'Боязнь ампул'):direct['medical_difficulty']=8
        else:direct['medical_difficulty']=4
        if background_is(doctor,'Гений медицинской мысли'):direct['med_bonus']=0
        if trait_count(doctor,'Главное знать куда ставить'):
            direct['positive_duration_bonus']=1
            for effect in profile.get('effects') or []:
                value=effect.get('value',0)
                positive=effect.get('type') in {'regeneration','blood_recovery','pain_block','stress_resistance'} or effect.get('type')=='heal' and isinstance(value,(int,float)) and value>0
                if positive and effect.get('tick','turn_end') in {'turn_end','round_end'} and isinstance(effect.get('remaining'),(int,float)) and effect['remaining']>1:
                    effect['remaining']+=1
                    if isinstance(effect.get('remaining_seconds'),(int,float)):effect['remaining_seconds']+=6
            for field in ('pain_block_turns','stress_block_turns','action_points_duration'):
                if isinstance(direct.get(field),(int,float)) and direct[field]>1:direct[field]+=1
    return profile,direct


def bleeding_rank_bonus(doctor):
    return int(background_is(doctor,'Гений медицинской мысли') or background_is(doctor,'Врач по бумагам'))


def prepare_uses(item,template,doctor):
    """Преобразование выполняется лишь перед реальным расходом, не при просмотре."""
    marker=item.get('medicineEconomy')
    applies=economy_applies(doctor,template)
    if marker and not applies:
        quantity=max(1,int(item.get('quantity') or 1));uses=max(0,int(item.get('uses') or 0))//2
        maximum=int(marker['baseMax'])
        if uses==0:
            quantity-=1;uses=maximum
        if quantity<=0:raise ValidationError('Осталась только дополнительная доза черты; без неё предмет использовать нельзя')
        item.update(quantity=quantity,uses=uses,maxUses=maximum);item.pop('medicineEconomy',None)
        item.setdefault('attributes',{})['uses_remaining']=uses
    elif applies and not marker:
        attrs=template.attributes or {};maximum=max(1,int(item.get('maxUses') or attrs.get('uses') or 1))
        current=item.get('uses') if item.get('uses') is not None else (item.get('attributes') or {}).get('uses_remaining')
        uses=max(0,int(current if current is not None else maximum))
        item['medicineEconomy']={'baseMax':maximum}
        item.update(maxUses=maximum*2,uses=uses*2)
        item.setdefault('attributes',{})['uses_remaining']=uses*2


def take_item(source_items,index,amount):
    """Передаются физические дозы; личная дополнительная половина не переносится."""
    source=source_items[index];marker=source.get('medicineEconomy')
    if not isinstance(marker,dict):return None
    maximum=max(1,int(marker['baseMax']));quantity=max(1,int(source.get('quantity') or 1))
    uses=max(0,int(source.get('uses') or 0));amount=min(quantity,max(1,int(amount)))
    if maximum==1 and uses<2 or uses//2==0:
        # Частично использованная единица остаётся у владельца; можно передать свежие из той же стопки.
        if amount>=quantity:raise ValidationError('Использованный одноразовый медикамент нельзя передать или забрать с тела')
        transferred=deepcopy(source);transferred.update(quantity=amount,uses=maximum,maxUses=maximum)
        source['quantity']=quantity-amount
    else:
        transferred=deepcopy(source);transferred.update(quantity=amount,uses=min(maximum,uses//2),maxUses=maximum)
        if amount==quantity:source_items.pop(index)
        else:source.update(quantity=quantity-amount,uses=maximum*2)
    transferred.pop('medicineEconomy',None)
    transferred.setdefault('attributes',{})['uses_remaining']=transferred['uses']
    if index<len(source_items) and source_items[index] is source:source.setdefault('attributes',{})['uses_remaining']=source['uses']
    return transferred
