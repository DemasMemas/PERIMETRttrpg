"""Объявление снайперского выстрела и сохранённый выбор дополнительной травмы."""
from copy import deepcopy
import uuid

from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LocationCharacter,LobbyCharacter
from app.services.exceptions import ConflictError,ValidationError,PermissionDenied
from app.services.character_traits import trait_count
from app.services.effects import apply_effect_to_health,sync_health_derived_statuses


def declared(data,weapon,fire_mode,choice):
    mode=choice.get('sniperMode') if isinstance(choice,dict) else None
    if mode is None:return None
    from app.services.combat import CombatService
    trait={'mercy':'Снайперское милосердие','precision':'Микроскопическая точность'}.get(mode)
    if not trait or fire_mode!='aimed' or CombatService._weapon_class(weapon)!='sniper_rifle' or not trait_count(data,trait):
        raise ValidationError('Это объявление недоступно для выбранного выстрела')
    return mode


def apply_trauma(target,data,trauma,rules,damage,impact,armor_penetrated,allow_bleeding=True):
    from app.services.combat import CombatService
    health=data.setdefault('health',{});area=trauma['area'];bleedings=[]
    if rules['fracture']:apply_effect_to_health(health,{'type':'fracture','area':area,'source':'combat_attack'})
    if allow_bleeding and rules['bleeding']:
        kind,stage=rules['bleeding'];bleedings.append({'kind':kind,'stage':stage,'area':area,'source':'additional_trauma'})
        apply_effect_to_health(health,{'type':f'bleeding_{kind}_{stage}','area':area,'source':'combat_attack'})
    if rules['pain']:apply_effect_to_health(health,{'type':'pain','value':rules['pain'],'source':'combat_attack'},character_data=data)
    if rules['shock']:apply_effect_to_health(health,{'type':'shock','area':area,'source':'combat_attack'})
    organs=[rules['organ']] if rules['organ'] else []
    if impact and armor_penetrated:
        if impact.get('damage_skull_if_penetrated'):organs.append('skull')
        organs.extend(impact.get('secondary_organs_if_penetrated') or [])
    damages=[]
    for organ in dict.fromkeys(organs):
        result=CombatService._apply_organ_damage(health,organ,damage,area)
        if result:
            damages.append(result)
            if result.get('bleeding'):bleedings.append(result['bleeding'])
    if damages:
        trauma.update(organ_damage=damages[0],organ_damages=damages)
        if not trauma.get('organ'):trauma['organ']=damages[0].get('organ')
    if rules['fall_or_drop']:trauma['fall_or_drop']=CombatService._apply_fall_or_drop(target,data,area)
    return bleedings


def enqueue(actor,target,trauma,damage,zone,armor_penetrated,allow_bleeding):
    from app.services.combat import CombatService
    cards=[]
    organs={'heart':'сердце','rightLung':'правое лёгкое','leftLung':'левое лёгкое','kidney':'почка','stomach':'желудок',
        'liver':'печень','spine':'позвоночник','jaw':'челюсть','rightEye':'правый глаз','leftEye':'левый глаз',
        'rightEar':'правое ухо','leftEar':'левое ухо','nose':'нос','brain':'мозг','skull':'череп'}
    for offset in (-1,0,1):
        roll=(trauma['roll']+offset-1)%20+1;rules=deepcopy(CombatService._trauma_effects(zone,roll))
        impact=CombatService._head_impact_profile(roll,actor,target) if zone=='head' else None
        if impact:rules['organ']=impact.get('primary_organ')
        selected={**deepcopy(trauma),'roll':roll,'fracture':bool(rules['fracture']),'pain':rules['pain'],
            'shock':bool(rules['shock']),'organ':rules['organ'],'fall_or_drop':rules['fall_or_drop'],'head_impact':impact,
            'bleeding':{'kind':rules['bleeding'][0],'stage':rules['bleeding'][1]} if rules['bleeding'] else None}
        parts=[]
        if rules['fracture']:parts.append('Перелом')
        if rules['pain']:parts.append(f"Боль +{rules['pain']}")
        if rules['shock']:parts.append('Шок')
        if rules['bleeding']:
            kind,stage=rules['bleeding'];parts.append(('Внешнее' if kind=='external' else 'Внутреннее')+' кровотечение: '+{'light':'слабое','medium':'среднее','severe':'сильное','extreme':'экстремальное'}[stage])
        damaged=[rules['organ']] if rules['organ'] else []
        if impact and armor_penetrated:
            if impact.get('damage_skull_if_penetrated'):damaged.append('skull')
            damaged.extend(impact.get('secondary_organs_if_penetrated') or [])
        if damaged:parts.append('Повреждение: '+', '.join(organs.get(organ,organ) for organ in dict.fromkeys(damaged)))
        if rules['fall_or_drop']:parts.append('Падение или потеря оружия')
        cards.append({'roll':roll,'offset':offset,'rules':rules,'impact':impact,'trauma':selected,
            'description':'; '.join(parts) or 'Без дополнительного эффекта'})
    entry={'id':uuid.uuid4().hex,'target_id':target.id,'target_name':target.character.name,'zone':zone,'damage':damage,
        'armor_penetrated':armor_penetrated,'allow_bleeding':allow_bleeding,'original_roll':trauma['roll'],'cards':cards}
    meta=actor.character.data.setdefault('health',{}).setdefault('combatMeta',{})
    meta.setdefault('sniperTraumaChoices',[]).append(entry);flag_modified(actor.character,'data')
    trauma.update(pending=True,choice_id=entry['id'])


def choices(data):
    return [{'id':entry['id'],'target_name':entry['target_name'],'original_roll':entry['original_roll'],
        'cards':[{key:card[key] for key in ('roll','offset','description')} for card in entry['cards']]}
        for entry in ((data or {}).get('health') or {}).get('combatMeta',{}).get('sniperTraumaChoices',[])]


def require_resolved(state):
    if state and state.status=='active':
        pending=LobbyCharacter.data['health']['combatMeta']['sniperTraumaChoices'].as_string()
        if db.session.query(LocationCharacter.id).join(LobbyCharacter,LocationCharacter.character_id==LobbyCharacter.id).filter(
                LocationCharacter.location_id==state.location_id,pending.isnot(None),pending!='[]').first():
            raise ConflictError('Сначала выберите дополнительную травму снайперского выстрела')


def decide(actor,user_id,choice):
    from app.services.combat import CombatService
    if not CombatService._can_end_turn_for_character(actor,user_id,is_gm=actor.location.lobby.gm_id==user_id):
        raise PermissionDenied('Вы не управляете стрелком')
    if not isinstance(choice,dict) or set(choice)!={'id','index'} or type(choice.get('index')) is not int or choice['index'] not in (0,1,2):
        raise ValidationError('Выберите одну из трёх карточек')
    data=deepcopy(actor.character.data);pending=data.setdefault('health',{}).setdefault('combatMeta',{}).get('sniperTraumaChoices',[])
    entry=next((entry for entry in pending if entry['id']==choice['id']),None)
    if not entry:raise ConflictError('Дополнительная травма уже выбрана или недоступна')
    target=db.session.get(LocationCharacter,entry['target_id'])
    if not target or target.location_id!=actor.location_id:
        pending.remove(entry);actor.character.data=data;flag_modified(actor.character,'data');db.session.commit()
        return {'target_character_id':None,'trauma':None,'bleedings':[],'tough_nut':None,'posture_updates':[],
            'chosen_description':'Цель покинула подлокацию. Дополнительная травма отменена; ОД и патрон не возвращаются.'}
    target_data=deepcopy(target.character.data);health=target_data.setdefault('health',{});before=deepcopy(health)
    previous=CombatService._character_condition(target_data)['state'];card=entry['cards'][choice['index']];trauma=deepcopy(card['trauma'])
    bleedings=apply_trauma(target,target_data,trauma,card['rules'],entry['damage'],card['impact'],entry['armor_penetrated'],entry['allow_bleeding'])
    sync_health_derived_statuses(health)
    survival=None
    if previous!='dead' and CombatService._character_condition(target_data)['state']=='dead':
        from app.services.character_survival import survive_wound
        survival=survive_wound(target_data,before,entry['zone'])
    health['lastTrauma']=None if survival else trauma;health['lastTraumas']=[] if survival else [trauma]
    pending.remove(entry);actor.character.data=data;flag_modified(actor.character,'data')
    target.character.data=target_data;flag_modified(target.character,'data')
    CombatService._sync_location_effects_from_character(target)
    from app.services.character_rules import after_kill
    after_kill(actor,target,previous!='dead')
    from app.services.character import CharacterService
    posture_updates=CharacterService.sync_location_health(target.character,target.character.data['health'])
    db.session.commit()
    return {'target_character_id':target.character_id,'trauma':None if survival else trauma,'bleedings':[] if survival else bleedings,
        'tough_nut':survival,'posture_updates':posture_updates,'chosen_description':card['description']}
