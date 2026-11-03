"""Общие свойства предысторий и особых черт; без повторной правки базовых навыков."""
from app.services.character_creation import _catalog
from functools import lru_cache


def _name(value):return str(value or '').strip().casefold().replace('ё','е')


def _traits(data):
    features=(data or {}).get('features') if isinstance(data,dict) else None
    values=features.get('specialTraits') if isinstance(features,dict) else None
    return [t for t in values if isinstance(t,dict)] if isinstance(values,list) else []


@lru_cache(maxsize=256)
def _known_id(kind,name):
    return next((t['id'] for t in _catalog()[kind] if _name(t['name'])==_name(name)),None)


def trait_count(data,name):
    known=_known_id('traits',name)
    return sum(1 for t in _traits(data)
               if isinstance(t,dict) and t.get('active',True)
               and (_name(t.get('name'))==_name(name) or known and t.get('ruleId')==known))


def background_is(data,name):
    basic=(data or {}).get('basic') if isinstance(data,dict) else None
    bg=basic.get('background') if isinstance(basic,dict) else None
    bg=bg if isinstance(bg,dict) else {}
    known=_known_id('backgrounds',name)
    aliases={'сбушник':'сбшник'}
    return aliases.get(_name(bg.get('name')),_name(bg.get('name')))==aliases.get(_name(name),_name(name)) or bool(known and bg.get('ruleId')==known)


def skill_learning_multiplier(data,path):
    amount=sum(1 for t in _traits(data)
               if isinstance(t,dict) and t.get('active',True) and (_name(t.get('name'))==_name('Заинтересованный') or t.get('ruleId')==_known_id('traits','Заинтересованный'))
               and (t.get('choices') or {}).get('skill')==path)
    return 3 if amount>=3 else 2 if amount==2 else 1.5 if amount else 1


def passive_profile(data):
    n=lambda name:trait_count(data,name)
    return {
        'weight_multiplier':max(0,1-.25*(n('Тяжеловес')+int(background_is(data,'Кладовщик')))),
        'movement_penalty':n('Ползун')-n('Я - конченный. Бегите')+int(background_is(data,'Утиный стрелок')),
        'pocket_cells':30*n('Глубокие карманы')-5*n('Узкие карманы'),
        'pockets_quick_access':not bool(n('Глубокие карманы')),
        'backpack_volume_reduction':.2*n('Игрок в тетрис'),
        'launcher_weight_reduction':3 if n('Да не такой и тяжелый') else 0,
        'minimum_strength_reduction':3*n('Равномерное распределение'),
        'psi_will_multiplier':10 if n('Пси-устойчивый') else 5,
        'psy_state_multiplier':.5 if n('Шапочка из фольги') else 1,
        'stress_multiplier':2 if n('Паникёр') else 1,
        'pain_ignored':3 if n('Спартанец') or background_is(data,'Контрактник') else 0,
        'pain_penalty_reduction':n('Железная воля'),
        'pain_shock_threshold':8 if n('Спартанец') else 5,
        'injury_penalty_reduction':n('Стойкий'),
        'bleeding_reduction':2*n('Лучшая свертываемость'),
        'bleeding_multiplier':1,
        'incoming_trauma_modifier':10*n('Есть пробитие'),
        'outgoing_trauma_modifier':20*n('Есть пробитие'),
        'blindness_multiplier':1.5 if n('Сварщик') else 1,
        'grenade_radius_bonus':n('Пироманьяк'),
        'evade_cost':2 if n('Уклонист') else 4,
        'reload_discount':n('Быстрые руки'),
        'case_discount':2*n('Мастер хранения'),
        'block_discount':n('Глухая оборона'),
        'block_efficiency':2*n('Глухая оборона'),
        'free_swing':bool(n('Никто не должен знать о черте')),
        'first_rounds_ap':n('Укрощенный адреналин'),
        'breathless_avoid_chance':min(100,50*n('Марафонец')),
        'filter_usage_multiplier':2**n('Плохие легкие'),
        'read_lips':bool(n('Чтец по губам')),
        'no_darkness_penalty':background_is(data,'Ночной дозор'),
        'knows_bandit_slang':bool(n('Блатной жаргон') or background_is(data,'Прогульщик') or background_is(data,'Вор в законе')),
        'knows_military_slang':bool(n('Никак нет, товарищ сержант') or any(background_is(data,b) for b in ('Давний ветеран','Контрактник','Добрый товарищ Военком'))),
    }


def sync_pocket_capacity(current,incoming):
    # Вместимость задают правила персонажа, а не присланное из листа число.
    incoming.setdefault('inventory',{})['pocketMaxVolume']=max(0,10+passive_profile(incoming)['pocket_cells'])


def favored_mutant_bonus(data,species):
    bg=((data.get('basic') or {}).get('background') or {}) if isinstance(data,dict) else {}
    chosen=(bg.get('choices') or {}).get('favoredMutant')
    return 3 if background_is(data,'Охотник') and _name(chosen)==_name(species) else 0


def butchering_bonus(data,species):
    return 3*trait_count(data,'Мастер охоты')-3*trait_count(data,'Торопыга')+(4 if background_is(data,'Хозяин леса') else 0)+favored_mutant_bonus(data,species)


def weapon_accuracy_bonus(data,weapon,*,melee=False):
    from app.services.combat import CombatService
    attrs=CombatService._template_attributes(weapon)
    suppressed=bool(weapon.get('silenced') or attrs.get('silenced') or any(
        'глушител' in _name(m.get('name')) for m in [*(weapon.get('modifications') or []),*(weapon.get('installedModules') or [])] if isinstance(m,dict)))
    value=trait_count(data,'Бесшумный убивца') if melee or suppressed else 0
    value-=int(bool(trait_count(data,'Сварщик')))
    quality=trait_count(data,'Первоклассный ценитель')
    durability=CombatService._coerce_float(weapon.get('durability',attrs.get('durability')),100)
    maximum=CombatService._coerce_float(weapon.get('maxDurability',weapon.get('max_durability',attrs.get('max_durability'))),100)
    percentage=100*durability/maximum if maximum else 0
    if quality:value+=2*min(2,quality) if percentage>90 else -4 if percentage<80 else 0
    return value


def action_cost(data,action,cost,*,weapon_type=None,aimed=False):
    p=passive_profile(data);discount=0;minimum=1
    if action=='reload':discount=p['reload_discount']
    elif action=='evade':return p['evade_cost']
    elif action=='swing' and p['free_swing']:return 0
    elif action=='block':discount=p['block_discount'];minimum=0
    elif action=='case':discount=p['case_discount']
    if aimed and weapon_type=='pistol':
        count=trait_count(data,'Джохн Уык');discount+=count
        if count==1:minimum=2
    if aimed and weapon_type in {'pistol','smg','submachine_gun'} and background_is(data,'Полицейский'):discount+=1
    if aimed and weapon_type=='grenade_launcher' and background_is(data,'Гренадир'):discount+=1
    return max(minimum,cost-discount) if discount else cost


def consumable_profile(profile,direct,item,recipient):
    """Меняется копия применяемого профиля, исходный шаблон остаётся прежним."""
    from copy import deepcopy
    profile=deepcopy(profile);direct=deepcopy(direct)
    if background_is(recipient,'Атлет'):
        if isinstance(direct.get('hp'),(int,float)) and direct['hp']>0:direct['hp']*=1.5
        for effect in profile.get('effects',[]):
            if effect.get('type') in {'heal','regeneration'} and isinstance(effect.get('value'),(int,float)) and effect['value']>0:effect['value']*=1.5
    if 'пив' in _name(item.get('name')) and trait_count(recipient,'Пивец пива'):
        multiplier=1+min(2,trait_count(recipient,'Пивец пива'))
        if direct.get('radiation_reduction_ceiling') is None and direct.get('is_alcohol') and direct.get('radiation_delta')==-1:
            direct['radiation_reduction_ceiling']=50
        if isinstance(direct.get('radiation_delta'),(int,float)) and direct['radiation_delta']<0:direct['radiation_delta']*=multiplier
        for effect in profile.get('effects',[]):
            if effect.get('type')=='radiation' and isinstance(effect.get('value'),(int,float)) and effect['value']<0:effect['value']*=multiplier
    if background_is(recipient,'Дядя Толик') and isinstance(direct.get('intoxication_delta'),(int,float)) and direct['intoxication_delta']>0:
        direct['intoxication_delta']*=.5
    return profile,direct
