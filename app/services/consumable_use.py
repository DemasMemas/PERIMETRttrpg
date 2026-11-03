"""Commit consumable side effects with the sheet result, never as separate requests.

Some targeted procedures are authoritative server operations, while legacy
medications still submit their calculated sheet result through this boundary.
"""
from copy import deepcopy
from math import isfinite

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import ConsumableUse, Lobby, LocationCharacter, LocationCombatState
from app.services.addictions import record_exposure
from app.services.exceptions import ConflictError, ValidationError
from app.services.magazine import inventory_slot


_UNSET_ADDICTION_PROFILE = object()


def _number(value):
    if type(value) not in (int, float) or not isfinite(value) or abs(value) > 1000000:
        raise ValidationError('Некорректное значение эффекта расходника')
    return value


def validate_consumable_use(actor, envelope):
    if envelope is None:
        return None
    if not isinstance(envelope, dict) or set(envelope) - {'id', 'side_effects', 'combat_location_id', 'source', 'deferred_action_id'}:
        raise ValidationError('Некорректные параметры применения расходника')
    operation_id = envelope.get('id')
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 120:
        raise ValidationError('Не указан идентификатор применения расходника')
    effects = envelope.get('side_effects') or {}
    if not isinstance(effects, dict) or set(effects) - {'exposure', 'action_points_delta'}:
        raise ValidationError('Некорректные побочные эффекты расходника')
    if db.session.get(ConsumableUse, operation_id):
        raise ConflictError('Это применение уже сохранено. Обновите лист.')
    source = envelope.get('source')
    if source is not None:
        if not isinstance(source, dict) or not isinstance(source.get('snapshot'), dict):
            raise ValidationError('Не указан выбранный расходник')
        parent, index = inventory_slot(actor.data or {}, source.get('path'))
        if parent[index] != source['snapshot']:
            raise ConflictError('Расходник изменился. Обновите лист и выберите его заново.')


def apply_consumable_use(actor, target, envelope, *, location_id=None, addiction_profile=_UNSET_ADDICTION_PROFILE):
    if envelope is None:
        return None
    operation_id = envelope['id']
    effects = envelope.get('side_effects') or {}
    db.session.add(ConsumableUse(id=operation_id, actor_character_id=actor.id, target_character_id=target.id))
    try:
        # The unique insert and every gameplay mutation roll back together.
        db.session.flush()
    except IntegrityError as error:
        raise ConflictError('Это применение уже сохранено. Обновите лист.') from error

    result = {}
    exposure = effects.get('exposure')
    if exposure is not None:
        if not isinstance(exposure, dict) or set(exposure) - {
            'item_name', 'price', 'intoxication', 'exhaustion_relief', 'addiction_block_hours',
        }:
            raise ValidationError('Некорректные параметры проверки зависимости')
        if not isinstance(exposure.get('item_name'), str) or not 1 <= len(exposure['item_name']) <= 300:
            raise ValidationError('Не указан использованный расходник')
        values = {key: max(0, _number(exposure.get(key, 0))) for key in (
            'price', 'intoxication', 'exhaustion_relief', 'addiction_block_hours',
        )}
        lobby = db.session.get(Lobby, target.lobby_id)
        data = deepcopy(target.data or {})
        from app.services.character_traits import background_is
        if background_is(data,'Врач по бумагам'):
            from app.services.addictions import addiction_profile as profile_for_name
            selected=profile_for_name(exposure['item_name'],values['price']) if addiction_profile is _UNSET_ADDICTION_PROFILE else addiction_profile
            if selected:addiction_profile={**selected,'chance':selected['chance']*2}
        result['addiction'] = record_exposure(
            data.setdefault('health', {}), exposure['item_name'], values.pop('price'),
            (lobby.game_day or 1) * 1440 + (lobby.game_time_minutes or 0), **values,
            **({} if addiction_profile is _UNSET_ADDICTION_PROFILE else {'profile': addiction_profile}),
        )
        target.data = data
        flag_modified(target, 'data')

    delta = max(-10, min(10, int(_number(effects.get('action_points_delta', 0)))))
    if delta:
        requested_location = envelope.get('combat_location_id')
        if requested_location is not None and (type(requested_location) is not int or requested_location <= 0):
            raise ValidationError('Некорректная локация применения')
        if location_id is not None and requested_location is not None and requested_location != location_id:
            raise ValidationError('Локация применения изменилась')
        resolved_location = location_id or requested_location
        state = LocationCombatState.query.filter_by(location_id=resolved_location, status='active').first()
        doctor = LocationCharacter.query.filter_by(location_id=resolved_location, character_id=actor.id).first()
        patient = LocationCharacter.query.filter_by(location_id=resolved_location, character_id=target.id).first()
        if not state or not doctor or not patient or state.current_location_character_id != doctor.id:
            raise ValidationError('Изменить ОД препаратом можно при применении в свой ход')
        # The drug affects the recipient, not the doctor administering it.
        patient.action_points_current = max(0, patient.action_points_current + delta)
        result['action_points_delta'] = delta
    return result
