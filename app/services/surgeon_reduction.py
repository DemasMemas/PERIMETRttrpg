"""Хирург без набора: согласие пациента, восстановление и запрет до его сна."""
from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified

from app.models import LocationCharacter
from app.services.character_traits import background_is,trait_count
from app.services.exceptions import NotFoundError,ValidationError


def prepare(location,actor,target_id,area,consent_id,user_id):
    from app.services.combat import CombatService
    from app.services.character_interaction import CharacterInteractionService
    from app.services.medical_procedure import _has_catastrophic_limb_injury
    if not background_is(actor.character.data,'Хирург'):
        raise ValidationError('Вправление без набора доступно только Хирургу')
    target=LocationCharacter.query.filter_by(location_id=location.id,character_id=target_id).first()
    if not target or not target.character:raise NotFoundError('Пациент не найден')
    if area not in {'leftArm','rightArm','leftLeg','rightLeg'}:raise ValidationError('Выберите конечность')
    data=target.character.data or {};health=data.get('health') or {}
    if CombatService._character_condition(data)['state']=='dead':raise ValidationError('Нельзя вправить конечность мёртвому персонажу')
    zone=(health.get('zones') or {}).get(area)
    if not isinstance(zone,dict) or zone.get('current')!=0 or _has_catastrophic_limb_injury(health,area):
        raise ValidationError('Нужна конечность с 0 ОЗ без утраты или размозжения')
    if (health.get('combatMeta') or {}).get('surgeonReductions',{}).get(area):
        raise ValidationError('Повторное вправление доступно после 8-часового отдыха пациента')
    if trait_count(actor.character.data,'Боязнь крови') and (actor.id==target.id or any(
        isinstance(e,dict) and e.get('active',True) and str(e.get('type','')).startswith('bleeding_external')
        for e in health.get('effects') or [])):
        raise ValidationError('Боязнь крови не позволяет оказать эту медицинскую помощь')
    if target.id!=actor.id:
        if CombatService._location_character_condition(target)['state']=='active':
            CharacterInteractionService._pair(location.id,user_id,actor.id,target.character_id)
            CharacterInteractionService.validate_treatment(consent_id,actor,target)
        else:CombatService._validate_incapacitated_interaction(location.id,user_id,actor.id,target.character_id)
    elif consent_id is not None:raise ValidationError('Для лечения себя не требуется согласие')
    return {'target':target,'area':area,'consent_id':consent_id}


def apply(actor,plan,user_id):
    from app.services.character import CharacterService
    from app.services.character_interaction import CharacterInteractionService
    from app.services.medical_procedure import _restore_zone_and_pool
    from app.services.effects import sync_health_derived_statuses
    target=plan['target'];data=deepcopy(target.character.data)
    health=data.setdefault('health',{});area=plan['area']
    restored=_restore_zone_and_pool(health,area,1)
    pain_before=float(health.get('painLevel') or 0)
    from app.services.effects import apply_effect_to_health
    apply_effect_to_health(health,{'type':'pain','value':5,'source':'surgeon_reduction'},character_data=data)
    health.setdefault('combatMeta',{}).setdefault('surgeonReductions',{})[area]=True
    sync_health_derived_statuses(health)
    target.character.data=data;flag_modified(target.character,'data')
    updates=CharacterService.sync_location_health(target.character,health)
    if plan['consent_id'] is not None:
        CharacterInteractionService.complete_treatment(plan['consent_id'],user_id,commit=False)
    return {'name':'Вправить конечность без набора','action_points':2,'roll_required':False,
            'target_character_id':target.character_id,'area':area,'restoration':restored,'pain_delta':float(health.get('painLevel') or 0)-pain_before,
            'posture_updates':updates}
