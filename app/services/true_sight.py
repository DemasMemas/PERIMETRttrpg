"""Личное распознавание видимых оригиналов и пси-копий без раскрытия скрытых целей."""
from app.models import LocationCharacter
from app.services.character_traits import trait_count,facial_profile
from app.services.exceptions import NotFoundError,PermissionDenied,ValidationError


def inspect(location_id,user_id,character_id):
    from app.services.combat import CombatService
    location=CombatService._get_location(location_id);is_gm=CombatService._ensure_access(location,user_id)
    observer=LocationCharacter.query.filter_by(location_id=location_id,character_id=character_id).first()
    if not observer or not observer.character:raise NotFoundError('Наблюдатель не найден в подлокации')
    if not CombatService._can_end_turn_for_character(observer,user_id,is_gm=is_gm):raise PermissionDenied('Вы не управляете наблюдателем')
    data=observer.character.data or {}
    if not trait_count(data,'Истинное зрение'):raise ValidationError('У персонажа нет Истинного зрения')
    blind=facial_profile(data)['eyes']==2 or any(effect.get('type')=='blindness' and effect.get('active',True)
        and CombatService._coerce_float(effect.get('value'),0)>=100 for effect in (data.get('health') or {}).get('effects') or [])
    if blind or CombatService._character_condition(data)['state']!='active':return {'active':False,'marks':[]}
    people=CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=location_id).all())
    sources={(person.character.data.get('basic') or {}).get('psi_clone_source_id') for person in people if person.character}
    marks=[]
    for target in people:
        if not target.character or target.gm_hidden and not is_gm:continue
        target_data=target.character.data or {};basic=target_data.get('basic') or {}
        copy=basic.get('is_psi_clone')
        original=not copy and (target.character_id in sources or any('Клонирование' in value for value in CombatService._mutant_traits(target_data)))
        if not copy and not original:continue
        if not CombatService._can_see_location_character(observer,target):continue
        marks.append({'character_id':target.character_id,'kind':'copy' if copy else 'original'})
    return {'active':True,'marks':marks}
