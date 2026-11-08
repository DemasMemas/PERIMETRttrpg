"""Общая оплата управляющего действия через физический КПК."""
from app.models import LocationCombatState
from app.services.exceptions import ValidationError,PermissionDenied


def charge(character,user_id,pda_id,payload,result,label):
    if result.get('replayed'):return 0
    from app.services.item_controllers import _context
    from app.services.portable_lights import held_pda
    from app.services.combat import CombatService
    actor=_context(character,payload.get('location_id'))
    state=LocationCombatState.query.filter_by(location_id=actor.location_id,status='active').first() if actor else None
    if not state:return 0
    is_gm=CombatService._ensure_access(actor.location,user_id)
    if state.current_location_character_id!=actor.id or not CombatService._can_end_turn_for_character(actor,user_id,is_gm=is_gm):
        raise PermissionDenied(f'{label}: действие доступно только в свой ход')
    if (held_pda(character.data) or {}).get('id')!=pda_id:raise ValidationError('Возьмите этот КПК в руку')
    if actor.action_points_current<3:raise ValidationError(f'{label}: недостаточно ОД')
    actor.action_points_current-=3
    return 3
