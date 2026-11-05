"""Проверки навыков с контекстом и памятью диалога; навыки не переписываются."""
from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LobbyCharacter,LocationCharacter
from app.services.character_traits import background_is,trait_count,passive_profile,favored_mutant_bonus
from app.services.exceptions import ValidationError,NotFoundError,ConflictError,PermissionDenied
from app.services.effects import apply_effect_to_health


def preserve(current,incoming):
    previous=((current or {}).get('health') or {}).get('combatMeta') or {}
    meta=incoming.setdefault('health',{}).setdefault('combatMeta',{})
    for key in ('socialCheckReceipts','socialMemory','diplomatRetry'):
        if key in previous:meta[key]=deepcopy(previous[key])
        else:meta.pop(key,None)


def context_modifiers(data,target_data,path,*,face_visible=False,weapon=None,has_target=True):
    from app.services.combat import CombatService
    tags=(target_data.get('basic') or {}).get('tags') or []
    charisma=0;roll=0;advantage=False;disadvantage=False;rules=[]
    if 'Бандит' in tags and passive_profile(data)['knows_bandit_slang']:
        charisma+=3;rules.append('Блатной жаргон: Харизма +3')
    if 'Военный' in tags and passive_profile(data)['knows_military_slang']:
        charisma+=3;rules.append('Военный говор: Харизма +3')
    if 'Бандит' in tags and face_visible and trait_count(data,'Цель номер один'):
        charisma-=20*trait_count(data,'Цель номер один');rules.append('Цель номер один: собеседник видит лицо')
    if has_target and background_is(data,'Добрый товарищ Военком'):
        if path=='skills.social.persuasion' and 'Военный' in tags:advantage=True;rules.append('Убеждение военного: Преимущество')
        if not any(tag in tags for tag in ('Военный','Проходил военную службу')) and path!='skills.social.intimidation':
            roll-=8;rules.append('Собеседник не служил: −8')
    if background_is(data,'Страшный человек'):
        if path=='skills.social.persuasion':disadvantage=True;rules.append('Страшный человек: Помеха Убеждению')
        if path=='skills.social.intimidation' and isinstance(weapon,dict) and weapon.get('category')=='melee_weapon':
            advantage=True;rules.append('Оружие ближнего боя: Преимущество Устрашению')
    if path=='skills.social.persuasion' and trait_count(data,'Подержи моё пиво'):
        if CombatService._intoxication_profile(data)['stage']!='none' and CombatService._intoxication_profile(target_data)['stage']!='none':
            advantage=True;rules.append('Оба собеседника пьяны: Преимущество')
    return {'charisma_level':charisma,'roll':roll,'advantage':advantage,'disadvantage':disadvantage,'rules':rules}


def run(character_id,user_id,payload):
    from app.services.character import CharacterService
    from app.services.combat import CombatService
    if not isinstance(payload,dict) or set(payload)-{'target_character_id','skill_path','difficulty','face_visible','operation_id','retry','vegetation','related_favored','location_id'}:
        raise ValidationError('Некорректные параметры социальной проверки')
    actor=CharacterService.get_character(character_id,user_id);CharacterService.check_access(actor,user_id,edit=True)
    operation=payload.get('operation_id')
    if not isinstance(operation,str) or not 1<=len(operation)<=120:raise ValidationError('Нужен идентификатор проверки')
    data=deepcopy(actor.data);meta=data.setdefault('health',{}).setdefault('combatMeta',{})
    receipts=meta.setdefault('socialCheckReceipts',[])
    previous=next((entry for entry in receipts if entry['id']==operation),None)
    if previous:return {**deepcopy(previous['result']),'data':actor.data_snapshot(),'replayed':True}
    retry=payload.get('retry') is True
    pending=meta.get('diplomatRetry') if retry else None
    if retry:
        if not background_is(data,'Дипломат') or not pending:raise ConflictError('Повтор Убеждения уже использован или недоступен')
        payload={**pending,'operation_id':operation,'retry':True}
    target_id=payload.get('target_character_id');target=db.session.get(LobbyCharacter,target_id) if type(target_id) is int else None
    if target_id is not None and (not target or target.id==actor.id or target.lobby_id!=actor.lobby_id):raise NotFoundError('Собеседник не найден')
    if target:
        try:CharacterService.check_access(target,user_id)
        except PermissionDenied:
            actors=LocationCharacter.query.filter_by(character_id=actor.id).all()
            visible=False
            for source in actors:
                other=LocationCharacter.query.filter_by(location_id=source.location_id,character_id=target.id).first()
                if other and not other.gm_hidden and CombatService._can_see_location_character(source,other):visible=True;break
            if not visible:raise
    path=payload.get('skill_path');difficulty=payload.get('difficulty')
    quick = difficulty is None and isinstance(path,str) and path in {'skills.physical.shooting','skills.physical.awareness.visual','skills.physical.awareness.hearing'}
    if not isinstance(path,str) or path not in CombatService.NARRATIVE_SKILLS or not quick and (type(difficulty) is not int or not 1<=difficulty<=40):
        raise ValidationError('Выберите навык и СЛ от 1 до 40')
    if quick and (target is not None or retry or payload.get('vegetation') or payload.get('related_favored')):
        raise ValidationError('Для проверки с дополнительным контекстом укажите СЛ')
    if type(payload.get('face_visible',False)) is not bool:raise ValidationError('Укажите, видит ли собеседник лицо')
    vegetation=payload.get('vegetation',False)
    if type(vegetation) is not bool or vegetation and path!='skills.other.stealth':raise ValidationError('Растительное укрытие применимо только к Скрытности')
    related_favored=payload.get('related_favored',False)
    if type(related_favored) is not bool:raise ValidationError('Некорректный контекст избранного мутанта')
    favored=((data.get('basic') or {}).get('background') or {}).get('choices',{}).get('favoredMutant')
    if related_favored and not favored_mutant_bonus(data,favored):raise ValidationError('Не выбран избранный мутант Охотника')
    social=path.startswith('skills.social.')
    # Память Балабола истекает спустя полные сутки после последнего диалога.
    now=(actor.lobby.game_day or 1)*1440+(actor.lobby.game_time_minutes or 0)
    memory=meta.setdefault('socialMemory',{}).setdefault(str(target.id),{'skills':{}}) if target and social else {'skills':{}}
    if now-float(memory.get('last_dialog',now))>=1440:memory['skills']={}
    memory['last_dialog']=now
    location_id=payload.get('location_id')
    if location_id is not None:
        if type(location_id) is not int:raise ValidationError('Некорректная локация проверки')
        active=LocationCharacter.query.filter_by(character_id=actor.id,location_id=location_id).first()
        if not active or active.location.lobby_id!=actor.lobby_id:raise NotFoundError('Персонаж не находится в этой локации')
    else:
        active=LocationCharacter.query.filter_by(character_id=actor.id).order_by(LocationCharacter.last_action.desc().nullslast(),LocationCharacter.id.desc()).first()
    index=active.drawn_weapon_index if active else data.get('activeWeaponIndex')
    weapons=data.get('weapons') or [];weapon=weapons[index] if type(index) is int and 0<=index<len(weapons) else None
    target_data=target.data or {} if target else {}
    modifiers=context_modifiers(data,target_data,path,face_visible=payload.get('face_visible',False),weapon=weapon,has_target=bool(target)) if social else {'roll':0,'charisma_level':0,'advantage':False,'disadvantage':False,'rules':[]}
    roll_bonus=modifiers['roll']
    bonus=3 if related_favored else (favored_mutant_bonus(data,CombatService._mutant_profile_name(target_data)) if target and CombatService._is_mutant_character(target_data) else 0)
    if bonus:roll_bonus+=bonus;modifiers['rules'].append('Избранный мутант Охотника: +3')
    if vegetation and trait_count(data,'Человек-куст'):
        roll_bonus+=4*trait_count(data,'Человек-куст');modifiers['rules'].append('Человек-куст: растительное укрытие +4')
    refusal=((data.get('health') or {}).get('combatMeta') or {}).get('brigadierRefusals',{}).get(str(target.id)) if target else None
    if refusal and background_is(data,'Бригадир') and path=='skills.social.intimidation':
        roll_bonus+=((CombatService._skill_value(data,path)+15-10)//2)-CombatService._base_skill_modifier(data,path)
        modifiers['rules'].append('Отказ от указания: Устрашение +15 уровней на этот бросок')
    if target and social and background_is(data,'Балабол'):
        roll_bonus+=int(memory['skills'].get(path,0));modifiers['rules'].append(f"Балабол: {int(memory['skills'].get(path,0)):+d}")
    if target and path=='skills.social.deception' and trait_count(data,'Честные глаза') and not memory.get('firstLieUsed') and not CombatService._is_mutant_character(target_data):
        roll_bonus+=3;modifiers['rules'].append('Честные глаза: первая проверка +3')
    lie_check=None
    if path=='skills.social.deception' and trait_count(data,'Психолог на пол ставки'):
        lie_check=CombatService._narrative_skill_check(data,'skills.physical.will')
        lie_check.update(difficulty=12,success=not lie_check.get('automatic_failure') and lie_check['total']>=12)
    if lie_check and not lie_check['success']:
        check={'blocked':True,'success':False,'failure_reason':'Не удалось заставить себя солгать','will_check':lie_check,
            'skill_path':path,'skill_label':CombatService.NARRATIVE_SKILLS[path],'difficulty':difficulty,'rules':modifiers['rules']}
    else:
        from app.services.lighting_rules import world_is_dark
        from app.services.weather_rules import at_character,profile as weather_profile
        weather=at_character(active.location,active) if active else weather_profile(actor.lobby.weather_settings)
        darkness=CombatService._is_dark_at_character(active.location,active) if active and path in {'skills.physical.awareness.visual','skills.physical.shooting','skills.other.stealth'} else world_is_dark(actor.lobby.game_time_minutes)
        light_modifier=0
        if active and path in {'skills.physical.awareness.visual','skills.physical.shooting'}:
            from app.services.portable_lights import illumination
            from app.services.weapon_modules import uses_nvg
            light=illumination(active)
            if light['lit']:
                darkness=False
                if light['base_dark'] and not uses_nvg(data):
                    light_modifier=-(light['penalty'] or 0)
                    roll_bonus+=light_modifier
        if light_modifier:modifiers['rules'].append(f'Переносной свет: {light_modifier:+d}')
        check=CombatService._narrative_skill_check(data,path,advantage=modifiers['advantage'],forced_disadvantage=modifiers['disadvantage'],
            charisma_level_delta=modifiers['charisma_level'],roll_bonus=roll_bonus,darkness=darkness,weather=weather)
        check['illumination_modifier']=light_modifier
        if check.get('weather_modifier'):modifiers['rules'].append(f"Погода на улице: {check['weather_modifier']:+d}")
        if check.get('weather_disadvantage'):modifiers['rules'].append('Ливень: Помеха')
        check.update(difficulty=difficulty,success=None if quick else not check.get('automatic_failure') and check['total']>=difficulty,rules=modifiers['rules'])
        if lie_check:check['will_check']=lie_check
        if path=='skills.social.deception':memory['firstLieUsed']=True
        if target and social and background_is(data,'Балабол'):memory['skills'][path]=max(-4,min(4,int(memory['skills'].get(path,0))+(1 if check['success'] else -1)))
        if not quick:CombatService._apply_stress_check_consequences(data,path,check['success'])
    if retry:
        meta.pop('diplomatRetry',None)
        if not check['success']:
            actor.data=data;flag_modified(actor,'data')
            # Сохраняем настоящий источник стресса и обычные проверки его проявления.
            from types import SimpleNamespace
            stress_actor=active or SimpleNamespace(character=actor,location_id=None)
            check['stress']=CombatService.apply_stress_trigger(stress_actor,1,trigger='diplomat_retry_failed')
            data=actor.data;meta=data['health']['combatMeta']
            apply_effect_to_health(data['health'],{'type':'diplomat_social_penalty','name':'Дипломат: неудачный повтор',
                'modifiers':{skill:2 for skill in CombatService.NARRATIVE_SKILLS if skill.startswith('skills.social.')},
                'expires_on_stress_decrease':True,'tick':'manual','source':'Дипломат'})
    elif background_is(data,'Дипломат') and path=='skills.social.persuasion' and not check['success']:
        meta['diplomatRetry']={key:payload.get(key) for key in ('target_character_id','skill_path','difficulty')}
        meta['diplomatRetry']['face_visible']=payload.get('face_visible',False)
    elif not quick:meta.pop('diplomatRetry',None)
    result={'check':check,'target_name':target.name if target else 'Обычная проверка','diplomat_retry_available':bool(meta.get('diplomatRetry'))}
    meta.setdefault('socialCheckReceipts',[]).append({'id':operation,'result':deepcopy(result)})
    meta['socialCheckReceipts']=meta['socialCheckReceipts'][-30:]
    actor.data=data;flag_modified(actor,'data')
    if active:CombatService._sync_location_effects_from_character(active)
    db.session.commit()
    return {**result,'data':actor.data_snapshot()}
