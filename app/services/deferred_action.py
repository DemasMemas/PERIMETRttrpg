from copy import deepcopy
from functools import wraps
from inspect import signature

from sqlalchemy.exc import IntegrityError
from sqlalchemy import event
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import DeferredCombatAction, LocationCharacter, LocationObject
from app.services.exceptions import ConflictError, PermissionDenied, ValidationError, ServiceError


def _bound_items(actor, payload):
    data = actor.character.data or {}
    result = {}
    if payload.get('action_key')=='trap':
        from app.services.traps import _item
        choice=payload.get('attribute_choice') or {}
        if choice.get('tool_path') is not None:result['trapTool']=deepcopy(_item(data,choice['tool_path']))
    if payload.get('action_key')=='attack' and payload.get('attribute_choice')=='paired_fire':
        from app.services.weapon_grip import pair,hands,primary_hand
        result['pairedWeapons']=deepcopy(pair(actor))
        result['pairedGrip']={'hands':hands(data),'hand':primary_hand(data),'primary':getattr(actor,'drawn_weapon_index',None)}
    if payload.get('action_key')=='draw_weapon' and payload.get('attribute_choice')=='second_hand':
        index=getattr(actor,'drawn_weapon_index',None)
        weapons=data.get('weapons') or []
        result['primaryWeapon']=deepcopy(weapons[index]) if type(index) is int and 0<=index<len(weapons) else None
    if payload.get('action_key')=='pour_water':
        held=((data.get('health') or {}).get('combatMeta') or {}).get('heldWaterItem') or {}
        result['heldWaterItem']=deepcopy(held)
        try:
            item=data
            for key in held['path']:item=item[key]
            result['water']=deepcopy(item)
        except (KeyError,IndexError,TypeError):result['water']=None
    if payload.get('loading') is not None:
        from app.services.ammo_loading import loading_bindings
        result['loading'] = loading_bindings(data, payload['loading'])
    weapon_index = payload.get('weapon_index')
    if weapon_index is not None:
        try:
            index = int(weapon_index)
            weapons = data.get('weapons') or []
            if 0 <= index < len(weapons):
                result['weapon'] = deepcopy(weapons[index])
        except (TypeError, ValueError):
            pass
    if payload.get('item_path'):
        item = data
        try:
            for key in payload['item_path']:
                item = item[key]
            result['item'] = deepcopy(item)
        except (KeyError, IndexError, TypeError):
            result['item'] = None
    return result


def snapshot_action_arguments(arguments):
    return deepcopy({key: value for key, value in arguments.items()
                     if key not in {'location_id', 'user_id', 'pending_action_id', 'resume_pending_action_id'}})


def durable_combat_action(operation):
    call_signature = signature(operation)

    @wraps(operation)
    def run(*args, **kwargs):
        call = call_signature.bind(*args, **kwargs)
        call.apply_defaults()
        values = call.arguments
        resume_id = values.get('resume_pending_action_id')
        pending_id = values.get('pending_action_id')
        authorized_resume = False
        try:
            if resume_id:
                row = db.session.get(DeferredCombatAction, str(resume_id))
                actor = db.session.get(LocationCharacter, values['location_character_id'])
                if not row or not actor or row.location_character_id != actor.id or actor.location_id != values['location_id']:
                    raise ValidationError('Сохранённое действие не найдено. Старое действие нужно начать заново.')
                from app.services.combat import CombatService
                is_gm = CombatService._ensure_access(actor.location, values['user_id'])
                if not CombatService._can_end_turn_for_character(actor, values['user_id'], is_gm=is_gm):
                    raise PermissionDenied('Вы не управляете этим персонажем')
                if (((actor.character.data or {}).get('health') or {}).get('combatMeta') or {}).get('grenadeReaction'):
                    raise ConflictError('Сначала завершите реакцию на гранату')
                if row.status != 'ready':
                    raise ConflictError('Действие ещё не оплачено, уже завершено или отменено')
                if row.payload.get('action_key') == 'consumable_use':
                    raise ValidationError('Лечение завершается через сохранённую процедуру применения расходника')
                saved_payload = deepcopy(row.payload)
                authorized_resume = True
                validate_deferred_conditions(actor, row)
                bindings = saved_payload.pop('_bindings', {})
                if bindings != _bound_items(actor, saved_payload):
                    raise ValidationError('Оружие или предмет длительного действия изменились')
                # Claim and apply in the same transaction. The version check rejects a racing resume.
                row.status = 'completed'
                db.session.flush()
                return operation(location_id=values['location_id'], user_id=values['user_id'],
                                 **saved_payload, resume_pending_action_id=row.id)
            if pending_id is not None:
                if not isinstance(pending_id, str) or not 1 <= len(pending_id) <= 120:
                    raise ValidationError('Некорректный идентификатор длительного действия')
                if db.session.get(DeferredCombatAction, pending_id):
                    raise ConflictError('Действие с этим идентификатором уже принято')
            if pending_id is None:
                return operation(*args, **kwargs)
            # Быстрое действие тоже получает квитанцию в той же транзакции,
            # что урон/расход ресурсов. Повтор запроса не может исполнить его заново.
            session=db.session()
            def receipt_before_commit(session):
                if db.session.get(DeferredCombatAction,pending_id) is None:
                    db.session.add(DeferredCombatAction(id=pending_id,
                        location_character_id=values['location_character_id'],
                        payload={'action_key':values['action_key']},label=str(values['action_key'])[:200],
                        status='completed',remaining_action_points=0))
            event.listen(session,'before_commit',receipt_before_commit)
            try:
                return operation(*args, **kwargs)
            finally:
                event.remove(session,'before_commit',receipt_before_commit)
        except IntegrityError as error:
            db.session.rollback()
            raise ConflictError() from error
        except ValidationError:
            db.session.rollback()
            if authorized_resume:
                cancel_failed_action(values['location_character_id'], resume_id, values['user_id'])
            raise
        except Exception:
            db.session.rollback()
            raise
    return run


def cancel_failed_action(actor_id, action_id, user_id):
    """После отката неудачного исполнения окончательно убираем оплаченный долг."""
    from app.extensions import socketio
    from app.services.character_events import emit_character_update
    from app.services.combat import CombatService

    actor = db.session.get(LocationCharacter, actor_id)
    row = db.session.get(DeferredCombatAction, str(action_id))
    if not actor or not row or row.location_character_id != actor.id or row.status != 'ready':
        return
    location_id = actor.location_id
    cancel_deferred_action(actor, row.id)
    db.session.commit()
    emit_character_update({'character_id': actor.character_id,
        'updates': {'data': actor.character.data}, 'source': 'combat'})
    socketio.emit('combat_state_updated', CombatService.get_state(location_id, user_id),
                 room=f'location_{location_id}')


def ensure_no_deferred_action(actor):
    unfinished = DeferredCombatAction.query.filter_by(location_character_id=actor.id).filter(
        DeferredCombatAction.status.in_(('paying', 'ready')),
    ).first()
    if unfinished:
        raise ValidationError('Сначала завершите или отмените предыдущее длительное действие')


def remember_action(actor, pending, payload):
    ensure_no_deferred_action(actor)
    db.session.add(DeferredCombatAction(
        id=pending['id'], location_character_id=actor.id,
        payload={**payload, '_bindings': _bound_items(actor, payload)},
        label=pending['label'][:200], remaining_action_points=pending['remaining_action_points'],
    ))


def update_action_payment(actor, action_id, remaining):
    if actor.id is None:
        return
    row = db.session.get(DeferredCombatAction, str(action_id))
    if row and row.location_character_id == actor.id and row.status == 'paying':
        row.remaining_action_points = remaining
        row.status = 'paying' if remaining > 0 else 'ready'
        if row.status == 'ready':
            from app.services.deferred_consumable import prepare_medicine_roll
            prepare_medicine_roll(row)


def validate_deferred_conditions(actor, row):
    """Проверка условий без оплаты, бросков и исполнения сохранённого действия."""
    from app.services.combat import CombatService
    payload = row.payload or {}
    kind = payload.get('action_key')
    CombatService.ensure_character_can_act(actor, kind)
    if kind=='place_cover':
        from app.services.crafted_covers import plan
        plan(actor,actor.character.data,payload.get('attribute_choice'))
    if kind=='trap':
        from app.services.traps import prepare
        prepare(actor,actor.character.data,payload.get('attribute_choice'),actor.location.lobby.gm_id)
    if kind=='prosthesis':
        from app.services.prostheses import plan
        plan(actor.character.data,payload.get('equipment_operation'),payload.get('equipment_slot'),payload.get('item_path'))
    if kind=='draw_weapon' and payload.get('attribute_choice')=='second_hand':
        from app.services.weapon_grip import validate_secondary
        validate_secondary(actor,payload.get('weapon_index'))
    elif kind=='draw_weapon':
        from app.services.weapon_grip import draw_plan
        weapons=actor.character.data.get('weapons') or [];index=payload.get('weapon_index')
        if type(index) is not int or not 0<=index<len(weapons):raise ValidationError('Оружие не найдено')
        draw_plan(actor.character.data,weapons[index],payload.get('attribute_choice'),extra_occupied=bool(actor.grapple_target_id))
    if kind=='attack' and payload.get('attribute_choice')=='paired_fire':
        from app.services.weapon_grip import pair,ensure_usable
        weapons=pair(actor)
        if len(weapons)!=2:raise ValidationError('Для общей стрельбы нужны два оружия в руках')
        for weapon in weapons:ensure_usable(actor,weapon)
    if kind=='brigadier_order':
        from app.services.brigadier_order import prepare
        prepare(actor,payload.get('target_character_id'),payload.get('attribute_choice'))
    if kind=='surgeon_reduction':
        from app.services.surgeon_reduction import prepare
        choice=payload.get('attribute_choice') or {}
        prepare(actor.location,actor,payload.get('target_character_id'),payload.get('target_zone'),choice.get('consentId'),actor.location.lobby.gm_id)
    if kind=='extinguish_fire':
        from app.services.effects import normalize_effect_list
        if not any(e['type']=='burning' and e.get('active',True) for e in normalize_effect_list((actor.character.data.get('health') or {}).get('effects') or [])):
            raise ValidationError('Персонаж больше не горит')
    if kind=='prepare_water':
        from app.services.burning import water_item
        water_item(actor.character.data,payload.get('item_path'))
        if not CombatService._has_usable_free_hand(actor):raise ValidationError('Рука для воды больше не свободна')
    if kind=='pour_water':
        from app.services.burning import water_item
        held=((actor.character.data.get('health') or {}).get('combatMeta') or {}).get('heldWaterItem') or {}
        if not held.get('path'):raise ValidationError('Вода больше не находится в руке')
        water_item(actor.character.data,held['path'])
    if kind == 'jump':
        from app.services.character_jump import prepare_jump
        prepare_jump(actor.location,actor,payload.get('target_x'),payload.get('target_y'),payload.get('jump_origin'))
        return
    if kind == 'consumable_use':
        from app.services.deferred_consumable import _validate_saved_selection
        _validate_saved_selection(actor, payload.get('selection') or {})
        return
    if payload.get('_bindings', {}) != _bound_items(actor, payload):
        raise ValidationError('Оружие или предмет продолжительного действия изменились')
    target_ids = list(payload.get('target_character_ids') or [])
    if payload.get('target_character_id') is not None:
        target_ids.append(payload['target_character_id'])
    for target_id in dict.fromkeys(target_ids):
        target = LocationCharacter.query.filter_by(location_id=actor.location_id, character_id=target_id).first()
        if not target:
            raise ValidationError('Цель продолжительного действия покинула локацию')
        if kind=='pour_water' and target.id!=actor.id:
            from app.services.burning import water_target_available
            if not water_target_available(actor,target):
                raise ValidationError('Цель обливания больше недоступна')
        contact = kind in {'melee_disarm','melee_shove','grapple','console_ally'} or (kind == 'attack' and not payload.get('fire_mode'))
        if contact and not CombatService._is_adjacent(actor, target):
            raise ValidationError('Цель продолжительного действия больше не находится рядом')
        if kind == 'aim' or (kind == 'attack' and payload.get('fire_mode') not in {'area','suppression'}):
            if not CombatService._can_see_location_character(actor, target, max_distance=float('inf')):
                raise ValidationError('Цель продолжительного действия больше не видна')
            index = payload.get('weapon_index')
            from app.services.weapon_grip import held
            if index is not None and int(index) >= 0 and not held(actor,int(index)):
                weapons = (actor.character.data or {}).get('weapons') or []
                weapon = weapons[int(index)] if int(index)<len(weapons) else {}
                if not CombatService._template_attributes(weapon).get('natural_weapon'):
                    raise ValidationError('Оружие продолжительного действия больше не находится в руках')
    object_id = payload.get('target_object_id', payload.get('object_id'))
    if object_id is not None:
        obj = LocationObject.query.filter_by(id=object_id, location_id=actor.location_id).first()
        if not obj:
            raise ValidationError('Объект продолжительного действия больше недоступен')
        if kind == 'take_cover':
            distance = min((max(abs(actor.pos_x-x),abs(actor.pos_y-y))
                for x,y in CombatService._object_footprint_tiles(obj)),default=float('inf'))
            if not CombatService._is_cover_object(obj) or distance > 1:
                raise ValidationError('Укрытие продолжительного действия больше недоступно')
            if CombatService._object_height(obj) <= 1.05 and CombatService._posture_key(actor) == 'standing':
                raise ValidationError('Для низкого укрытия требуется сидячее положение')
        if kind == 'attack' and not CombatService._is_in_facing_arc(actor,obj.tile_x,obj.tile_y):
            raise ValidationError('Объект продолжительного действия больше не находится в поле зрения')


def cancel_unavailable_actions(actor):
    """Вызывается до оплаты нового хода; отмена окончательна и не возвращает ОД."""
    if actor.id is None:
        return
    rows = DeferredCombatAction.query.filter_by(location_character_id=actor.id).filter(
        DeferredCombatAction.status.in_(('paying', 'ready')),
    ).all()
    for row in rows:
        try:
            validate_deferred_conditions(actor, row)
        except ServiceError:
            cancel_deferred_action(actor, row.id)
    # У старого клиентского долга нет сохранённых условий, которые можно
    # доказуемо проверить. Не оплачиваем его автоматически новым ходом.
    data = actor.character.data or {}
    meta = (data.get('health') or {}).get('combatMeta') or {}
    known_ids = {row.id for row in rows}
    orphan = (meta.get('pendingAction') or {}).get('id')
    completed = meta.get('completedPendingActionId')
    if (orphan and orphan not in known_ids) or (completed and completed not in known_ids):
        data = deepcopy(data)
        meta = data.setdefault('health',{}).setdefault('combatMeta',{})
        if orphan and orphan not in known_ids:
            meta.pop('pendingAction',None)
        if completed and completed not in known_ids:
            meta.pop('completedPendingActionId',None)
        actor.character.data = data
        flag_modified(actor.character,'data')


def serialize_deferred_action(actor, health):
    if actor.id is None:
        return None
    meta = health.get('combatMeta') or {}
    action_id = meta.get('completedPendingActionId') or (meta.get('pendingAction') or {}).get('id')
    if not action_id:
        return None
    row = db.session.get(DeferredCombatAction, str(action_id))
    if not row or row.location_character_id != actor.id or row.status not in {'paying', 'ready'}:
        return None
    return {'id': row.id, 'label': row.label, 'status': row.status,
            'remaining_action_points': row.remaining_action_points,
            'action_key': row.payload['action_key'],
            # Older reload records contain only a payment, not an inventory selection.
            'server_executable': row.payload['action_key'] != 'consumable_use' and (
                row.payload['action_key'] != 'reload_weapon' or bool(row.payload.get('item_path'))),
            'client_executable': row.payload['action_key'] == 'consumable_use'}


def preserve_action_payment(current_data, incoming_data):
    current = ((current_data or {}).get('health') or {}).get('combatMeta') or {}
    health = incoming_data.setdefault('health', {})
    if not isinstance(health, dict):
        return
    meta = health.setdefault('combatMeta', {})
    if not isinstance(meta, dict):
        meta = health['combatMeta'] = {}
    for key in ('pendingAction', 'completedPendingActionId', 'unstableJump', 'jumpSupport', 'slipperyMovement', 'grenadeReaction','heldWaterItem','trappedNet','surgeonReductions','fencerUsed','observedDeathNotified','sniperTraumaChoices','socialMemory','socialCheckReceipts','diplomatRetry','standaloneEvasion','firefighterNearby','harmAllowedTargets','combatExplosionOccurred','grenadirCheck','grenadirMustExplode','breakthroughProgress','breakthroughProgressOP','breakthroughMovementImpossible','breakthroughWillPassed','breakthroughWillFailed','breakthroughCheck','sniperOldCover','sniperPositionCheck','hunterActivityDay','hunterProcessedDay','hunterStressDebt','brigadierOrder','brigadierRefusals','locationScout','locationScoutPoint','observerTargets'):
        if key in current:
            meta[key] = deepcopy(current[key])
        else:
            meta.pop(key, None)


def cancel_deferred_action(actor, action_id):
    row = db.session.get(DeferredCombatAction, str(action_id))
    if not row or row.location_character_id != actor.id:
        raise ValidationError('Нет активного длительного действия')
    # Повторное удаление устаревшей строки панели не меняет итог действия.
    if row.status in {'paying', 'ready'}:
        row.status = 'cancelled'
        from app.services.deferred_consumable import cancel_treatment
        cancel_treatment(row)
    data = deepcopy(actor.character.data or {})
    meta = data.setdefault('health', {}).setdefault('combatMeta', {})
    if (meta.get('pendingAction') or {}).get('id') == row.id:
        meta.pop('pendingAction', None)
    if meta.get('completedPendingActionId') == row.id:
        meta.pop('completedPendingActionId', None)
    actor.character.data = data
    flag_modified(actor.character, 'data')


def cancel_actor_actions(actor):
    for row in DeferredCombatAction.query.filter_by(location_character_id=actor.id).filter(
        DeferredCombatAction.status.in_(('paying', 'ready')),
    ).all():
        cancel_deferred_action(actor, row.id)
