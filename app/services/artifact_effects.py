"""Passive artifact modifiers derived from equipped artifact rule text."""

from __future__ import annotations

import re
import math
import random
from copy import deepcopy
from hashlib import sha256

CLOCK_PREFIX='artifact-radiation-clear:'
PERIODIC_PREFIX='artifact-ten-minute:'
REACTION_PREFIX='artifact-reaction:'
CLEAR_RADIATION=re.compile(r'Выводит всю радиацию каждую минуту',re.IGNORECASE)
THERMAL_REACTION=re.compile(r'При получении\s+Термического урона\s+'
    r'(Штраф перемещения|Защита от физического урона|Тяжесть кровотечений)\s*([+-]\d+)%?\s+'
    r'на\s+(\d+)\s+раунд\w*',re.IGNORECASE)
REACTION_STATS={'штраф перемещения':'movement_penalty','защита от физического урона':'physical_protection',
                'тяжесть кровотечений':'bleeding_severity'}
PHYSICAL_BACKLASH=re.compile(r'При получении Физического урона шанс\s+(\d+)%\s+получить\s+(\d+)\s+Термического урона',re.IGNORECASE)


def equipped_artifacts(character_data):
    equipment = (character_data.get('equipment') or {}) if isinstance(character_data, dict) else {}
    if not isinstance(equipment,dict):return []
    armor = equipment.get('armor')
    armor = armor if isinstance(armor,dict) else {}
    found = []
    from app.services.belt_attachments import available,loaded
    belt=equipment.get('belt')
    if available(belt,'artifact'):found.extend(item for _,item in loaded(belt,'artifact'))
    for slot in armor.get('containers') or []:
        item = slot.get('item') if isinstance(slot, dict) else None
        if not isinstance(item, dict):
            continue
        if item.get('category') == 'artifact':
            found.append(item)
        for module in item.get('installedModules') or []:
            if isinstance(module, dict) and (
                module.get('category') == 'artifact'
                or module.get('slotType') == 'artifact'
            ):
                found.append(module)
    return found


def _signed(text, pattern):
    match = re.search(pattern + r'\s*([+-]\d+)', text, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _percent(text, pattern):
    match = re.search(pattern + r'\s*([+-]\d+)%', text, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _radiation_value(text, *, positive):
    total = 0
    for value in re.findall(
        r'([+-]?\d+)\s*Радиаци\w* за перемещение', text, re.IGNORECASE,
    ):
        # In the positive column an unsigned value is removal; in the negative
        # column it is accumulation. Explicit signs always win.
        if value.startswith(('+', '-')):
            total += int(value)
        else:
            total += -int(value) if positive else int(value)
    return total


def _conditional_value(text, pattern, radiation):
    total = 0
    matches = list(re.finditer(
        pattern + r'\s*([+-]\d+),?\s*Если накоплено хотя бы\s*(\d+)\s*Радиаци',
        text,
        re.IGNORECASE,
    ))
    for match in matches:
        if radiation >= int(match.group(2)):
            total += int(match.group(1))
    cleaned = text
    for match in reversed(matches):
        cleaned = cleaned[:match.start()] + cleaned[match.end():]
    return total, cleaned


def artifact_passive_profile(character_data):
    result = {
        'skill_all': 0, 'skill_physical': 0, 'skill_other': 0,
        'skill_strength': 0, 'skill_will': 0, 'skill_awareness': 0,
        'skill_stealth': 0, 'accuracy': 0, 'movement_penalty': 0,
        'melee_damage_percent': 0, 'incoming_physical_percent': 0,
        'armor_damage_reduction': 0, 'throw_range_bonus': 0,
        'strenuous_action_cost': 0, 'breath_duration_bonus': 0,
        'bleeding_severity': 0, 'radiation_per_movement': 0,
        'health_per_movement': 0, 'pain_per_movement': 0,
        'healing_multiplier': 1.0,
        'protection': {key: 0 for key in ('physical', 'chemical', 'thermal', 'electric', 'radiation', 'psi')},
    }
    health = character_data.get('health') if isinstance(character_data, dict) else {}
    radiation_level = float((health or {}).get('radiation') or 0)
    for artifact in equipped_artifacts(character_data):
        attrs = artifact.get('attributes') if isinstance(artifact.get('attributes'), dict) else {}
        positive = str(attrs.get('positive_effect') or '')
        negative = str(attrs.get('negative_effect') or '')
        text = THERMAL_REACTION.sub('',f'{positive} {negative}')
        conditional_movement, text = _conditional_value(
            text, r'Штраф перемещения', radiation_level,
        )
        conditional_accuracy, text = _conditional_value(
            text, r'Точность', radiation_level,
        )
        conditional_all_protection, text = _conditional_value(
            text, r'Все защиты', radiation_level,
        )
        protection_names = {
            'physical': 'физического', 'chemical': 'химического',
            'thermal': 'термического', 'electric': 'электрического',
            'radiation': 'радиации', 'psi': 'псионического',
        }
        conditional_protection = {}
        for key, label in protection_names.items():
            conditional_protection[key], text = _conditional_value(
                text,
                rf'Защита от {label}(?: урона)?',
                radiation_level,
            )
        result['skill_physical'] += _signed(text, r'Физические навыки')
        result['skill_other'] += _signed(text, r'Прочие навыки')
        result['skill_strength'] += _signed(text, r'Бонус Силы')
        result['skill_will'] += _signed(text, r'Воля')
        result['skill_awareness'] += _signed(text, r'Внимательность')
        result['skill_stealth'] += _signed(text, r'Скрытность')
        result['accuracy'] += _signed(text, r'Точность') + conditional_accuracy
        result['movement_penalty'] += (
            _signed(text, r'Штраф перемещения') + conditional_movement
        )
        result['melee_damage_percent'] += _percent(text, r'Урон в ближнем бою')
        result['incoming_physical_percent'] += _percent(text, r'Входящий физический урон')
        armor_reduction = re.search(
            r'Получаемый урон по броне снижен на\s*(\d+)', text, re.IGNORECASE,
        )
        if armor_reduction:
            result['armor_damage_reduction'] += int(armor_reduction.group(1))
        result['throw_range_bonus'] += _signed(
            text, r'Дополнительная дальность метания',
        )
        strenuous = re.search(
            r'Бег и Спринт занимают на\s*(\d+)\s*ОД больше', text, re.IGNORECASE,
        )
        if strenuous:
            result['strenuous_action_cost'] += int(strenuous.group(1))
        breath = re.search(r'Одышка на\s*(\d+)\s*раунд\w* дольше', text, re.IGNORECASE)
        if breath:
            result['breath_duration_bonus'] += int(breath.group(1))
        result['bleeding_severity'] += _signed(text, r'Тяжесть кровотечений')
        result['radiation_per_movement'] += _radiation_value(
            positive, positive=True,
        ) + _radiation_value(negative, positive=False)
        health_values = re.findall(r'Общее здоровье\s*([+-]\d+)\s*за перемещение', text, re.IGNORECASE)
        result['health_per_movement'] += sum(map(int, health_values))
        pain_up = re.findall(r'Усиливает боль на\s*(\d+)\s*уров', text, re.IGNORECASE)
        pain_down = re.findall(r'Облегчает боль на\s*(\d+)\s*уров', text, re.IGNORECASE)
        result['pain_per_movement'] += sum(map(int, pain_up)) - sum(map(int, pain_down))
        healing = re.search(r'Восстановление здоровья из любых источников\s*([\d.,]+)х', text, re.IGNORECASE)
        if healing:
            result['healing_multiplier'] *= float(healing.group(1).replace(',', '.'))
        all_protection = _percent(text, r'Все защиты') + conditional_all_protection
        for key, label in protection_names.items():
            result['protection'][key] += all_protection + conditional_protection[key]
            result['protection'][key] += _percent(
                text, rf'Защита от {label}(?: урона)?',
            )
    owners={_clock_source(item,REACTION_PREFIX) for item in equipped_artifacts(character_data)}
    for effect in (health or {}).get('effects') or []:
        if not is_reaction(effect) or effect.get('active',True) is False:continue
        if str(effect.get('source')).rsplit(':',1)[0] not in owners:continue
        try:remaining=float(effect.get('remaining_seconds',effect.get('remaining',0)))
        except (TypeError,ValueError):continue
        if remaining<=0:continue
        stat=effect.get('artifact_stat');value=effect.get('value')
        if type(value) not in (int,float) or not math.isfinite(value):continue
        if stat=='physical_protection':result['protection']['physical']+=value
        elif stat in {'movement_penalty','bleeding_severity'}:result[stat]+=value
    return result


def is_reaction(effect):
    return isinstance(effect,dict) and str(effect.get('source') or '').startswith(REACTION_PREFIX)


def damage_reactions(data,damage,damage_type):
    """Запустить известные временные свойства только после фактического урона."""
    if damage<=0 or damage_type!='thermal':return []
    health=data.setdefault('health',{});effects=health.setdefault('effects',[]);created=[]
    for item in equipped_artifacts(data):
        attrs=item.get('attributes') or {};text=f"{attrs.get('positive_effect','')} {attrs.get('negative_effect','')}"
        for match in THERMAL_REACTION.finditer(text):
            stat=REACTION_STATS[match.group(1).casefold()];value=int(match.group(2));rounds=int(match.group(3))
            source=_clock_source(item,REACTION_PREFIX)+':'+stat
            effect={'id':source,'source':source,'type':'custom','name':f"{item.get('name')}: реакция на термический урон",
                'note':f'{match.group(1)} {value:+g} на {rounds} раунда','artifact_stat':stat,'value':value,
                'active':True,'tick':'time_elapsed','time_unit':'second','remaining':rounds*6,'remaining_seconds':rounds*6}
            effects[:]=[old for old in effects if old.get('source')!=source];effects.append(effect);created.append(effect)
    return created


def damage_backlash(data,damage,damage_type):
    """Один бросок на физический артефакт и исходное попадание; без рекурсии."""
    if damage<=0 or damage_type!='physical':return []
    result=[];seen=set()
    for item in equipped_artifacts(data):
        source=_clock_source(item,REACTION_PREFIX)
        if source in seen:continue
        seen.add(source)
        match=PHYSICAL_BACKLASH.search(str((item.get('attributes') or {}).get('negative_effect') or ''))
        if not match:continue
        roll=random.randint(1,100)
        if roll<=int(match.group(1)):
            result.append({'artifact':item.get('name'),'source':source,'roll':roll,
                'damage_type':'thermal','raw_damage':int(match.group(2))})
    return result


def healing_amount(character_data,amount):
    """Масштабировать только новый прирост ОЗ, не сохранённую силу препарата."""
    value=float(amount)
    if value<=0 or not character_data:return value
    return value*artifact_passive_profile(character_data)['healing_multiplier']


def radiation_clock_removed(character_data,effect,amount,*,health):
    """Цена только радиации, выведенной этим физическим артефактом."""
    if amount<=0:return 0
    item=next((item for item in _clearing_artifacts(character_data)
        if _clock_source(item)==effect.get('source')),None)
    if not item:return 0
    negative=str((item.get('attributes') or {}).get('negative_effect') or '')
    rate=sum(map(int,re.findall(
        r'За каждую выведенную единицу радиации снижает Общее здоровье на\s*(\d+)',negative,re.IGNORECASE)))
    cost=rate*amount
    health['current']=max(0,float(health.get('current',health.get('max',700)) or 0)-cost)
    return cost


def _clock_source(item,prefix=CLOCK_PREFIX):
    identity=item.get('id') or f"{item.get('templateId')}:{item.get('name')}"
    return prefix+sha256(str(identity).encode('utf-8')).hexdigest()[:24]


def is_radiation_clock(effect):
    return isinstance(effect,dict) and str(effect.get('source') or '').startswith(CLOCK_PREFIX)


def _clearing_artifacts(data):
    return [item for item in equipped_artifacts(data) if CLEAR_RADIATION.search(
        str((item.get('attributes') or {}).get('positive_effect') or ''))]


def is_periodic_clock(effect):
    return isinstance(effect,dict) and str(effect.get('source') or '').startswith(PERIODIC_PREFIX)


def is_artifact_clock(effect):
    return is_radiation_clock(effect) or is_periodic_clock(effect)


def _item_profile(data,item):
    return artifact_passive_profile({'health':data.get('health') or {},'equipment':{'armor':{
        'containers':[{'item':{**item,'category':'artifact'}}]}}})


def _clock_rules(data):
    for item in equipped_artifacts(data):
        if CLEAR_RADIATION.search(str((item.get('attributes') or {}).get('positive_effect') or '')):
            yield item,CLOCK_PREFIX,60
        profile=_item_profile(data,item)
        if any(profile[key] for key in ('health_per_movement','radiation_per_movement','pain_per_movement')):
            yield item,PERIODIC_PREFIX,600


def needs_clocks(data):
    return bool(next(_clock_rules(data),None) or any(is_artifact_clock(e)
        for e in ((data.get('health') or {}).get('effects') or [])))


def sync_clocks(data,effects=None,*,canonical=None):
    """Артефакты используют существующие игровые часы, независимо от движения."""
    effects=list(effects if effects is not None else (data.get('health') or {}).get('effects') or [])
    previous=((canonical.get('health') or {}).get('effects') or []) if canonical is not None else effects
    old={e['source']:e for e in previous if is_artifact_clock(e)}
    result=[deepcopy(e) for e in effects if not is_artifact_clock(e)]
    seen=set()
    for item,prefix,interval in _clock_rules(data):
        source=_clock_source(item,prefix)
        if source in seen:continue
        seen.add(source)
        state=old.get(source) or {}
        try:seconds=float(state.get('remaining_seconds',float(state.get('remaining',interval/60))*60))
        except (TypeError,ValueError):seconds=interval
        seconds=max(.001,min(interval,seconds)) if math.isfinite(seconds) else interval
        minute=prefix==CLOCK_PREFIX
        result.append({'id':source,'source':source,'type':'delayed_adjustment',
            'name':(item.get('name') or 'Артефакт')+(': вывод радиации каждую минуту' if minute else ': свойства каждые 10 минут'),
            'note':('Пока артефакт надет, раз в минуту выводит всю радиацию. Потеря общих ОЗ начисляется только за радиацию, выведенную этим артефактом.'
                if minute else 'Свойства «за перемещение» срабатывают раз в 10 минут игрового времени. Отдельные шаги не вызывают срабатывания.'),
            'active':True,'time_unit':'minute','tick':'time_elapsed','remaining':interval//60,'remaining_seconds':seconds,
            'onExpire':[{'field':'radiation','delta':0,'min':0,'max':0}] if minute else []})
    return result


def apply_periodic_clock(data,effect,*,health):
    item=next((item for item in equipped_artifacts(data)
        if _clock_source(item,PERIODIC_PREFIX)==effect.get('source')),None)
    if item:apply_artifact_world_movement(data,artifact=item,health=health)


def preserve(current,incoming):
    canonical=((current or {}).get('health') or {}).get('effects') or []
    incoming_effects=((incoming or {}).get('health') or {}).get('effects') or []
    if not needs_clocks(incoming) and not any(is_reaction(e) for e in [*canonical,*incoming_effects]):return
    health=incoming.setdefault('health',{})
    health['effects']=sync_clocks(incoming,canonical=current or {})
    owners={_clock_source(item,REACTION_PREFIX) for item in equipped_artifacts(incoming)}
    health['effects']=[e for e in health['effects'] if not is_reaction(e)]+[
        deepcopy(e) for e in canonical if is_reaction(e) and str(e['source']).rsplit(':',1)[0] in owners]


def apply_artifact_world_movement(character_data,*,artifact=None,health=None):
    """Одно срабатывание свойств «за перемещение»: каждые десять игровых минут."""
    profile = _item_profile(character_data,artifact) if artifact is not None else artifact_passive_profile(character_data)
    health = health if health is not None else character_data.setdefault('health', {})
    before = {
        key: health.get(key)
        for key in ('current', 'radiation', 'painLevel')
    }
    health_delta = profile['health_per_movement']
    if health_delta>0:health_delta=healing_amount(character_data,health_delta)
    if health_delta:
        maximum = max(0, float(health.get('max') or health.get('maximum') or 700))
        health['current'] = min(
            maximum,
            max(0, float(health.get('current') or 0) + health_delta),
        )
    radiation_delta = profile['radiation_per_movement']
    if radiation_delta:
        health['radiation'] = min(
            100,
            max(0, float(health.get('radiation') or 0) + radiation_delta),
        )
    pain_delta = profile['pain_per_movement']
    if pain_delta:
        from app.services.effects import apply_effect_to_health
        apply_effect_to_health(health,{'type':'pain','value':pain_delta,'source':'artifact_movement'},character_data=character_data)
    after = {key: health.get(key) for key in before}
    return {
        'changed': before != after,
        'before': before,
        'after': after,
        'health_delta': health_delta,
        'radiation_delta': radiation_delta,
        'pain_delta': pain_delta,
    }
