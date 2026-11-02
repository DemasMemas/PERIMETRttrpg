"""Durable consumable selections and payment receipts; callers own transactions."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import random

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import CharacterInteractionRequest, DeferredCombatAction, LocationCharacter, LocationCombatState
from app.services.exceptions import ConflictError, PermissionDenied, ServiceError, ValidationError
from app.services.magazine import inventory_slot


def _same_effect(entry, selected):
    if not isinstance(entry, dict):
        return False
    if selected.get('id'):
        return entry.get('id') == selected['id']
    return all(entry.get(key) == selected.get(key) for key in ('type', 'area', 'source'))


def _selection_source(actor, selection):
    if not isinstance(selection, dict) or set(selection) - {
        'source', 'application', 'target_character_id', 'treatment_request_id', 'server_authoritative',
        'server_operation',
    }:
        raise ValidationError('Некорректная процедура лечения')
    if len(json.dumps(selection, ensure_ascii=False)) > 50000:
        raise ValidationError('Слишком большой объём параметров лечения')
    source = selection.get('source')
    if not isinstance(source, dict) or not isinstance(source.get('snapshot'), dict):
        raise ValidationError('Не указан расходник процедуры')
    parent, index = inventory_slot(actor.character.data or {}, source.get('path'))
    if parent[index] != source['snapshot']:
        raise ConflictError('Расходник изменился. Отмените действие и выберите его заново.')
    if parent[index].get('category') != 'consumable':
        raise ValidationError('Предмет не является расходником')
    target_id = selection.get('target_character_id')
    if type(target_id) is not int:
        raise ValidationError('Не указан пациент')
    request_id = selection.get('treatment_request_id')
    if request_id is not None and (type(request_id) is not int or request_id <= 0):
        raise ValidationError('Некорректное согласие на лечение')
    return target_id, request_id


def _selection_application(actor, target, selection):
    application = selection.get('application')
    if not isinstance(application, dict) or not isinstance(application.get('kind'), str) or application['kind'] not in {
        'self', 'bleeding', 'wound', 'injury', 'blood_type_test',
    }:
        raise ValidationError('Не выбрана процедура лечения')
    if application['kind'] == 'blood_type_test' and application.get('target') == 'packet':
        packet = application.get('entry')
        if not isinstance(packet, dict) or not isinstance(packet.get('item'), dict):
            raise ValidationError('Не выбран пакет крови')
        packets, packet_index = inventory_slot(actor.character.data or {}, packet.get('path'))
        if packets[packet_index] != packet['item']:
            raise ConflictError('Выбранный пакет крови изменился. Отмените старую процедуру.')
    effect = application.get('effect')
    if application['kind'] in {'bleeding', 'wound', 'injury'}:
        if not isinstance(effect, dict) or not isinstance(effect.get('type'), str) or not isinstance(effect.get('area'), str):
            raise ValidationError('Не выбрана травма')
        health = (target.character.data or {}).get('health') or {}
        if effect.get('type') in {'damaged_zone', 'body_zone'}:
            zone = (health.get('zones') or {}).get(effect.get('area'))
            if not isinstance(zone, dict):
                raise ConflictError('Выбранная часть тела больше не существует')
            if application.get('treatmentMode') == 'restore_limb' and float(zone.get('current') or 0) > 0:
                raise ConflictError('Конечность уже восстановлена. Отмените старую процедуру.')
        else:
            current = next((entry for entry in health.get('effects', []) if _same_effect(entry, effect)), None)
            if not current or any(current.get(key) != effect.get(key) for key in ('type', 'area')) or current.get('closed') or current.get('suppressed'):
                raise ConflictError('Выбранная травма изменилась или уже вылечена. Отмените старую процедуру.')


def validate_selection(actor, user_id, selection):
    from app.services.combat import CombatService
    from app.services.character_interaction import CharacterInteractionService

    target_id, request_id = _selection_source(actor, selection)
    consent = None
    if target_id == actor.character_id:
        target = actor
        if selection.get('treatment_request_id'):
            raise ValidationError('Для лечения себя не требуется согласие другого персонажа')
    elif selection.get('treatment_request_id'):
        _, _, target = CharacterInteractionService._pair(actor.location_id, user_id, actor.id, target_id)
        consent = CharacterInteractionService.validate_treatment(selection['treatment_request_id'], actor, target)
    else:
        _, target, _ = CombatService._validate_incapacitated_interaction(actor.location_id, user_id, actor.id, target_id)
    _selection_application(actor, target, selection)
    return target, consent


def _validate_saved_selection(actor, selection):
    """Проверка сохранённых данных; не заменяет авторизацию публичного запроса."""
    from app.services.combat import CombatService
    from app.services.character_interaction import CharacterInteractionService

    target_id, request_id = _selection_source(actor, selection)
    if target_id == actor.character_id:
        target = actor
        if request_id:
            raise ConflictError('Для лечения себя не требуется согласие другого персонажа')
    else:
        target = LocationCharacter.query.filter_by(
            location_id=actor.location_id, character_id=target_id,
        ).first()
        if not target or not target.character:
            raise ConflictError('Пациент покинул локацию')
        if not CombatService._is_adjacent(actor, target):
            raise ConflictError('Пациент больше не находится рядом')
        if request_id:
            CharacterInteractionService.validate_treatment(request_id, actor, target)
        elif CombatService._location_character_condition(target)['state'] == 'active':
            raise ConflictError('Пациенту снова требуется согласие на лечение')
    _selection_application(actor, target, selection)
    return target


def required_payment(actor, user_id, selection):
    from app.services.medical_procedure import minimum_consumable_payment

    target, consent = validate_selection(actor, user_id, selection)
    details = minimum_consumable_payment(
        actor.character.data or {}, target.character.data or {}, selection, actor.character.lobby_id,
    )
    return target, consent, details


def remember_consumable(actor, user_id, pending, selection, *, ready=False):
    from app.services.deferred_action import remember_action

    _, consent, payment = required_payment(actor, user_id, selection)
    remember_action(actor, pending, {
        'action_key': 'consumable_use',
        'selection': deepcopy(selection),
        'paid_action_points': max(0, int(pending.get('total_action_points') or 0)),
        'required_action_points': payment['total_action_points'],
        'payment_details': payment,
    })
    if ready:
        row = db.session.get(DeferredCombatAction, pending['id'])
        row.status = 'ready'
        row.remaining_action_points = 0
        prepare_medicine_roll(row)
    if consent:
        consent.status = 'in_progress'
        consent.payload = {**(consent.payload or {}), 'pending_action_id': pending['id']}
        flag_modified(consent, 'payload')


def resolve_ready(action_id, user_id, *, location_id=None):
    from app.services.combat import CombatService

    row = db.session.get(DeferredCombatAction, action_id) if isinstance(action_id, str) else None
    actor = db.session.get(LocationCharacter, row.location_character_id) if row else None
    if not actor or row.payload.get('action_key') != 'consumable_use' or (location_id is not None and actor.location_id != location_id):
        raise ValidationError('Сохранённая процедура не найдена')
    is_gm = CombatService._ensure_access(actor.location, user_id)
    if not CombatService._can_end_turn_for_character(actor, user_id, is_gm=is_gm):
        raise PermissionDenied('Вы не управляете этим персонажем')
    state = LocationCombatState.query.filter_by(location_id=actor.location_id, status='active').first()
    meta = ((actor.character.data or {}).get('health') or {}).get('combatMeta') or {}
    if row.status != 'ready' or not state or state.current_location_character_id != actor.id or meta.get('completedPendingActionId') != row.id:
        raise ConflictError('Процедура ещё не оплачена, завершена, отменена или сейчас не ход врача')
    if meta.get('grenadeReaction'):
        raise ConflictError('Сначала завершите реакцию на гранату')
    actor_id, action_id = actor.id, row.id
    try:
        CombatService.ensure_character_can_act(actor)
        target, consent = validate_selection(actor, user_id, row.payload['selection'])
    except ServiceError:
        db.session.rollback()
        from app.services.deferred_action import cancel_failed_action
        cancel_failed_action(actor_id, action_id, user_id)
        raise
    return row, actor, target, consent


def preparation(action_id, user_id, location_id):
    row, actor, target, _ = resolve_ready(action_id, user_id, location_id=location_id)
    return {
        'id': row.id, 'actor_character_id': actor.character_id,
        'actor_location_character_id': actor.id,
        'selection': deepcopy(row.payload['selection']),
        'target_data': target.character.data_snapshot(),
        'medicine_roll': row.payload.get('medicine_roll'),
    }


def prepare_medicine_roll(row):
    selection = row.payload.get('selection') or {}
    if (
        row.payload.get('action_key') == 'consumable_use'
        and selection.get('server_operation') != 'general'
        and 'medicine_roll' not in row.payload
    ):
        from app.services.medical_procedure import minimum_consumable_payment

        actor = db.session.get(LocationCharacter, row.location_character_id)
        if not actor or not actor.character:
            raise ConflictError('Врач больше не находится в локации')
        # Авторизация уже проверена при старте/запросе хода. Здесь только
        # актуальность сохранённых данных, без выбора другого пользователя.
        try:
            target = _validate_saved_selection(actor, selection)
            minimum_consumable_payment(
                actor.character.data or {}, target.character.data or {},
                selection, actor.character.lobby_id,
            )
        except ServiceError as error:
            raise ConflictError(str(error)) from error

        # Draw only after full payment, once for every browser and network retry.
        row.payload = {**row.payload, 'medicine_roll': random.randint(1, 20)}
        flag_modified(row, 'payload')


def claim_completion(actor, target, user_id, envelope, request_id=None):
    if envelope is not None and not isinstance(envelope, dict):
        raise ValidationError('Некорректные параметры применения расходника')
    action_id = (envelope or {}).get('deferred_action_id')
    if not action_id:
        return None
    row, saved_actor, saved_target, consent = resolve_ready(action_id, user_id)
    selection = row.payload['selection']
    if (saved_actor.character_id != actor.id or saved_target.character_id != target.id
            or envelope.get('id') != row.id or envelope.get('source') != selection['source']
            or request_id != selection.get('treatment_request_id')
            or envelope.get('combat_location_id') != saved_actor.location_id):
        raise ConflictError('Параметры завершения не совпадают с сохранённой процедурой')
    row.status = 'completed'
    # Optimistic claim rejects two browsers completing the same action.
    db.session.flush()
    return row


def finish_completion(actor, row):
    if row is None:
        return
    data = deepcopy(actor.data or {})
    meta = data.setdefault('health', {}).setdefault('combatMeta', {})
    if meta.get('completedPendingActionId') == row.id:
        meta.pop('completedPendingActionId')
    actor.data = data
    flag_modified(actor, 'data')


def cancel_treatment(row):
    if row.payload.get('action_key') != 'consumable_use':
        return
    request_id = (row.payload.get('selection') or {}).get('treatment_request_id')
    consent = db.session.get(CharacterInteractionRequest, request_id) if request_id else None
    if consent and consent.status == 'in_progress' and (consent.payload or {}).get('pending_action_id') == row.id:
        consent.status = 'cancelled'
        consent.resolved_at = datetime.now(timezone.utc)
