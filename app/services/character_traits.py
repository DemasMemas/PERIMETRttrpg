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


def carried_weapons(data):
    """Оружие в руках и во всех носимых контейнерах, без служебных снимков здоровья."""
    def visit(value,weapon=False):
        if isinstance(value,list):
            for item in value:yield from visit(item,weapon)
        elif isinstance(value,dict):
            if weapon or value.get('category') in {'weapon','melee_weapon'}:yield value
            for key in ('contents','pouches','storedItem'):yield from visit(value.get(key))
    yield from visit((data or {}).get('weapons'),True)
    inventory=(data or {}).get('inventory') or {}
    yield from visit(inventory.get('pockets'))
    yield from visit(inventory.get('backpack'))
    for item in ((data or {}).get('equipment') or {}).values():yield from visit(item)


def favored_mutant_bonus(data,species):
    bg=((data.get('basic') or {}).get('background') or {}) if isinstance(data,dict) else {}
    chosen=(bg.get('choices') or {}).get('favoredMutant')
    return 3 if chosen and background_is(data,'Охотник') and _name(chosen)==_name(species) else 0


def target_attack_accuracy(data,target_data,*,ranged=True):
    from app.services.combat import CombatService
    mutant=CombatService._is_mutant_character(target_data)
    value=favored_mutant_bonus(data,CombatService._mutant_profile_name(target_data)) if mutant else 0
    if ranged and not mutant and background_is(data,'Трюкач'):value-=1
    return value


def facial_profile(data):
    health=(data or {}).get('health') or {}
    parts={'leftEye','rightEye','leftEar','rightEar','nose'}
    missing={key for key,organ in (health.get('organs') or {}).items()
             if key in parts and isinstance(organ,dict) and organ.get('current')==0}
    missing.update(effect['area'] for effect in health.get('effects') or []
                   if isinstance(effect,dict) and effect.get('active',True)
                   and effect.get('type')=='organ_loss' and effect.get('area') in parts)
    eyes=len(missing&{'leftEye','rightEye'});ears=len(missing&{'leftEar','rightEar'})
    beauty=trait_count(data,'Вечно красив')
    return {'eyes':eyes,'ears':ears,'charisma_level':0 if beauty else -(5*eyes+(8 if ears==2 else 3*ears)+(5 if 'nose' in missing else 0)),
            'charisma_bonus':beauty*len(missing),'shooting':-3 if eyes==1 else 0,
            'melee':-5 if eyes==2 else 0,'agility':-3 if eyes==2 else 0,'hearing':-5 if ears==1 else 0}


def butchering_bonus(data,species):
    return 3*trait_count(data,'Мастер охоты')-3*trait_count(data,'Торопыга')+(4 if background_is(data,'Хозяин леса') else 0)+favored_mutant_bonus(data,species)


def selected_weapon_accuracy(data,weapon,target_data):
    if not background_is(data,'Один для бандитов\nВторой для мутантов'):return 0
    choice=((data or {}).get('ruleChoices') or {}).get('targetWeapons') or {}
    identity=weapon.get('id') if isinstance(weapon,dict) else None
    if not identity:return 0
    from app.services.combat import CombatService
    mutant=CombatService._is_mutant_character(target_data)
    if identity==choice.get('human'):return -5 if mutant else 2
    if identity==choice.get('mutant'):return 2 if mutant else -5
    return 0


def validate_target_weapons(current,incoming):
    from app.services.exceptions import ValidationError
    choices=incoming.get('ruleChoices') or {};old=(current or {}).get('ruleChoices') or {}
    if not isinstance(choices,dict):raise ValidationError('Некорректный выбор свойств персонажа')
    choice=choices.get('targetWeapons')
    previous=old.get('targetWeapons') if isinstance(old,dict) else None
    if choice==previous or choice is None:return
    if not background_is(incoming,'Один для бандитов\nВторой для мутантов') or not isinstance(choice,dict) or set(choice)-{'human','mutant'}:
        raise ValidationError('Некорректный выбор оружия предыстории')
    ids=[value for value in choice.values() if value is not None]
    if any(not isinstance(value,str) or not value or len(value)>120 for value in ids) or len(set(ids))!=len(ids):
        raise ValidationError('Выберите два разных экземпляра оружия')
    owned={weapon.get('id') for weapon in carried_weapons(incoming) if isinstance(weapon.get('id'),str)}
    if any(value not in owned and value!=(previous or {}).get(key) for key,value in choice.items() if value is not None):
        raise ValidationError('Можно выбрать только собственное оружие')


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
