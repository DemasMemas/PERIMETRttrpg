import random
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import CharacterInteractionRequest, LocationCombatState
from app.models.location_character import LocationCharacter
from app.services.combat import CombatService
from app.services.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from app.services.transaction import atomic_operation


class CharacterInteractionService:
    OPEN_STATUSES = {'pending', 'accepted', 'forced', 'in_progress'}

    @staticmethod
    def _claim_pending(request_row, status):
        claimed = db.session.execute(db.update(CharacterInteractionRequest).where(
            CharacterInteractionRequest.id == request_row.id,
            CharacterInteractionRequest.status == 'pending',
        ).values(status=status).execution_options(synchronize_session=False)).rowcount
        if claimed != 1:
            raise ConflictError('Предложение уже получило ответ или было отменено')
        db.session.refresh(request_row)

    @staticmethod
    def _controlled_by(location_character, user_id, is_gm=False):
        if is_gm:
            return True
        character = location_character.character
        return bool(
            location_character.controlled_by == user_id
            or (character and character.owner_id == user_id)
        )

    @staticmethod
    def _pair(location_id, user_id, actor_location_character_id, target_character_id):
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        actor = LocationCharacter.query.filter_by(
            id=actor_location_character_id,
            location_id=location_id,
        ).first()
        target = LocationCharacter.query.filter_by(
            character_id=target_character_id,
            location_id=location_id,
        ).first()
        if not actor or not target or actor.id == target.id:
            raise NotFoundError('Interaction target not found')
        if not CharacterInteractionService._controlled_by(actor, user_id, is_gm):
            raise PermissionDenied('You do not control the acting character')
        CombatService.ensure_character_can_act(actor)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if state and state.status == 'active' and state.current_location_character_id != actor.id:
            raise PermissionDenied("It is not this character's turn")
        if state and state.status == 'active' and not CombatService._is_adjacent(actor, target):
            raise ValidationError('Цель должна находиться на соседней клетке')
        return location, actor, target

    @staticmethod
    def _target_user_id(location, target):
        return (
            target.controlled_by
            or (target.character.owner_id if target.character else None)
            or location.lobby.gm_id
        )

    @staticmethod
    def _serialize(request_row):
        actor = db.session.get(LocationCharacter, request_row.actor_location_character_id)
        target = db.session.get(LocationCharacter, request_row.target_location_character_id)
        serialized = {
            'id': request_row.id,
            'location_id': request_row.location_id,
            'kind': request_row.kind,
            'status': request_row.status,
            'actor_location_character_id': request_row.actor_location_character_id,
            'actor_character_id': actor.character_id if actor else None,
            'actor_name': actor.character.name if actor and actor.character else 'Персонаж',
            'target_location_character_id': request_row.target_location_character_id,
            'target_character_id': target.character_id if target else None,
            'target_name': target.character.name if target and target.character else 'Персонаж',
            'actor_user_id': request_row.actor_user_id,
            'target_user_id': request_row.target_user_id,
            'payload': request_row.payload or {},
            'result': request_row.result or {},
        }
        if request_row.kind == 'trade':
            serialized['actor_data'] = deepcopy(
                actor.character.data
                if actor and actor.character and isinstance(actor.character.data, dict)
                else {}
            )
            serialized['target_data'] = deepcopy(
                target.character.data
                if target and target.character and isinstance(target.character.data, dict)
                else {}
            )
        return serialized

    @staticmethod
    def interaction_snapshot(location_id, user_id, actor_location_character_id, target_character_id):
        _, actor, target = CharacterInteractionService._pair(
            location_id,
            user_id,
            actor_location_character_id,
            target_character_id,
        )
        condition = CombatService._location_character_condition(target)
        snapshot = CombatService._incapacitated_character_snapshot(actor, target, condition)
        from app.services.npc_trade import discounted_purchase_prices, enabled
        if enabled(target.character):
            snapshot['merchant_purchase_prices'] = discounted_purchase_prices(snapshot['target_data'], actor.character.data or {})
        return snapshot

    @staticmethod
    @atomic_operation
    def create_request(
        location_id,
        user_id,
        actor_location_character_id,
        target_character_id,
        kind,
        payload=None,
    ):
        if kind not in {'treatment', 'trade'}:
            raise ValidationError('Unknown interaction type')
        location, actor, target = CharacterInteractionService._pair(
            location_id,
            user_id,
            actor_location_character_id,
            target_character_id,
        )
        if CombatService._location_character_condition(target)['state'] != 'active':
            raise ValidationError('Consent is only required from a conscious character')
        target_user_id = CharacterInteractionService._target_user_id(location, target)
        clean_payload = deepcopy(payload) if isinstance(payload, dict) else {}
        if kind == 'trade':
            from app.services.trade import prepare_payload
            clean_payload = prepare_payload(actor.character, target.character, clean_payload)
        clean_payload.pop('gm_decision_for_offline_target', None)
        automated_trade = kind == 'trade' and clean_payload.get('_merchant') is True
        delegated_to_gm = False
        if target_user_id != user_id and not automated_trade:
            from app.sockets.auth import lobby_has_online_users, user_is_online_in_lobby

            if lobby_has_online_users(location.lobby_id) and not user_is_online_in_lobby(
                target_user_id, location.lobby_id,
            ):
                if kind != 'treatment':
                    raise ConflictError('Игрок цели не в сети: обмен пока невозможен')
                gm_id = location.lobby.gm_id
                if not user_is_online_in_lobby(gm_id, location.lobby_id):
                    raise ConflictError('Игрок пациента не в сети, а ГМ недоступен для решения')
                target_user_id = gm_id
                delegated_to_gm = True
                clean_payload['gm_decision_for_offline_target'] = True
        request_row = CharacterInteractionRequest(
            location_id=location_id,
            actor_location_character_id=actor.id,
            target_location_character_id=target.id,
            actor_user_id=user_id,
            target_user_id=target_user_id,
            kind=kind,
            status='accepted' if automated_trade or target_user_id == user_id and not delegated_to_gm else 'pending',
            payload=clean_payload,
        )
        db.session.add(request_row)
        db.session.flush()
        if request_row.status == 'accepted' and kind == 'trade':
            CharacterInteractionService._complete_trade(request_row)
        else:
            db.session.commit()
        return CharacterInteractionService._serialize(request_row)

    @staticmethod
    @atomic_operation
    def cancel_request(request_id, user_id):
        request_row = db.session.get(CharacterInteractionRequest, request_id)
        if not request_row:
            raise NotFoundError('Запрос взаимодействия не найден')
        if request_row.actor_user_id != user_id:
            raise PermissionDenied('Отменить запрос может только его автор')
        if request_row.status != 'pending':
            raise ConflictError('Запрос уже получил ответ или начал выполняться')
        CharacterInteractionService._claim_pending(request_row, 'cancelled')
        request_row.resolved_at = datetime.now(timezone.utc)
        db.session.commit()
        return CharacterInteractionService._serialize(request_row)

    @staticmethod
    def _strength_roll(location_character):
        data = (
            location_character.character.data
            if location_character.character and isinstance(location_character.character.data, dict)
            else {}
        )
        result=CombatService._opposed_roll(data,'skills.physical.strength')
        return {key:result[key] for key in ('roll','bonus','total')}

    @staticmethod
    @atomic_operation
    def respond(request_id, user_id, decision):
        request_row = db.session.get(CharacterInteractionRequest, request_id)
        if not request_row or request_row.status != 'pending':
            raise NotFoundError('Interaction request is no longer pending')
        if request_row.kind=='order':
            from app.services.brigadier_order import respond
            return respond(request_row,user_id,decision)
        if request_row.kind not in {'treatment', 'trade'}:
            raise ValidationError('Use the dedicated endpoint for this request')
        location = CombatService._get_location(request_row.location_id)
        is_gm = location.lobby.gm_id == user_id
        target = db.session.get(LocationCharacter, request_row.target_location_character_id)
        if not target or not CharacterInteractionService._controlled_by(target, user_id, is_gm):
            raise PermissionDenied('You do not control the target character')
        actor = db.session.get(LocationCharacter, request_row.actor_location_character_id)
        state = LocationCombatState.query.filter_by(location_id=request_row.location_id).first()
        if not actor or (
            state
            and state.status == 'active'
            and not CombatService._is_adjacent(actor, target)
        ):
            raise ValidationError('The characters are no longer adjacent')

        accepted = str(decision).lower() == 'accept'
        if request_row.kind == 'trade' and (not isinstance(decision,str) or decision not in {'accept', 'decline'}):
            raise ValidationError('Неизвестный ответ на обмен')
        result = {'decision': 'accept' if accepted else 'decline'}
        CharacterInteractionService._claim_pending(request_row, 'accepted' if accepted else 'rejected')
        if accepted:
            request_row.status = 'accepted'
        elif request_row.kind == 'treatment':
            actor_roll = CharacterInteractionService._strength_roll(actor)
            target_roll = CharacterInteractionService._strength_roll(target)
            forced = actor_roll['total'] > target_roll['total']
            request_row.status = 'forced' if forced else 'rejected'
            result.update({
                'forced': forced,
                'actor_strength': actor_roll,
                'target_strength': target_roll,
            })
        else:
            request_row.status = 'rejected'
        request_row.result = result
        request_row.resolved_at = datetime.now(timezone.utc)
        if request_row.status == 'accepted' and request_row.kind == 'trade':
            CharacterInteractionService._complete_trade(request_row)
        else:
            db.session.commit()
        return CharacterInteractionService._serialize(request_row)

    @staticmethod
    def _resolve_item_path(character_data, item_id, fallback_path):
        from app.services.portable_lights import walk
        entries = [(path, item) for path, item in walk(character_data, all_items=True)
                   if path and (path[0] == 'equipment' and len(path) >= 2
                       or len(path) >= 3 and path[:2] in (['inventory', 'backpack'], ['inventory', 'pockets']))
                   and not any(key in path for key in ('installedModules', 'installedMagazine', 'fixedAmmo', 'ammo', 'loadedGrenade'))]
        valid_fallback = isinstance(fallback_path,list) and all(type(part) in {str,int} for part in fallback_path)
        if valid_fallback:
            found = next((path for path, item in entries if path == fallback_path
                          and (item_id is None or item.get('id') == item_id)), None)
            if found is not None:return found
        if item_id is not None:
            found = next((path for path, item in entries if item.get('id') == item_id), None)
            if found:return found
        if valid_fallback:
            return next((path for path, item in entries if path == fallback_path), [])
        return []

    @staticmethod
    def _apply_offer(source_data, target_data, offer):
        moved = []
        normalized = []
        for entry in offer or []:
            if not isinstance(entry, dict):
                continue
            path = CharacterInteractionService._resolve_item_path(
                source_data,
                entry.get('item_id'),
                entry.get('path'),
            )
            normalized.append((path, max(1, int(entry.get('amount') or 1))))
        normalized.sort(
            key=lambda value: tuple(
                (1, part) if isinstance(part, int) else (0, str(part))
                for part in value[0]
            ),
            reverse=True,
        )
        for path, amount in normalized:
            item = CombatService._take_inventory_item(source_data, path, amount)
            target_inventory = target_data.setdefault('inventory', {})
            backpack = target_inventory.setdefault('backpack', [])
            if not isinstance(backpack, list):
                backpack = []
                target_inventory['backpack'] = backpack
            from app.services.portable_lights import walk
            from uuid import uuid4
            occupied = {entry['id'] for _, entry in walk(target_data, all_items=True) if entry.get('id') is not None}
            for _, entry in walk({'inventory':{'pockets':[item]}}, all_items=True):
                if entry.get('id') is not None:
                    if entry['id'] in occupied:entry['id'] = uuid4().hex
                    occupied.add(entry['id'])
            backpack.append(item)
            moved.append({'name': item.get('name', 'Предмет'), 'amount': item.get('quantity', 1)})
        return moved

    @staticmethod
    def _complete_trade(request_row):
        if request_row.status != 'accepted':
            raise ConflictError('Предложение уже выполнено или не подтверждено')
        actor = db.session.get(LocationCharacter, request_row.actor_location_character_id)
        target = db.session.get(LocationCharacter, request_row.target_location_character_id)
        state = LocationCombatState.query.filter_by(location_id=request_row.location_id).first()
        if not actor or not target or (
            state
            and state.status == 'active'
            and not CombatService._is_adjacent(actor, target)
        ):
            raise ValidationError('The characters are no longer adjacent')
        CombatService.ensure_character_can_act(actor)
        if state and state.status == 'active':
            if state.current_location_character_id != actor.id:
                raise PermissionDenied("It is not this character's turn")
            if actor.action_points_current < 2:
                raise ValidationError('Not enough action points for trade')

        actor_data = deepcopy(actor.character.data or {})
        target_data = deepcopy(target.character.data or {})
        payload = request_row.payload or {}
        from app.services.trade import exchange_money, prepare_offer
        expected = payload.get('_expected_revisions') or {}
        if expected and (expected.get('actor') != actor.character.revision or expected.get('target') != target.character.revision):
            raise ConflictError('Снаряжение или деньги изменились. Откройте обмен заново.')
        if payload.get('_merchant'):
            from app.services.npc_trade import enabled, totals, require_contact
            if not enabled(target.character):
                raise ConflictError('Торговец больше не принимает предложения')
            require_contact(actor, target)
            current = totals(actor_data, target_data, payload.get('actor_offer', []), payload.get('target_offer', []))
            if any(current[key] != payload.get(key) for key in current):
                raise ConflictError('Стоимость товаров изменилась. Откройте торговлю заново.')
        prepare_offer(actor_data, payload.get('actor_offer', []))
        prepare_offer(target_data, payload.get('target_offer', []))
        money_result = exchange_money(actor_data, target_data, payload)
        actor_moved = CharacterInteractionService._apply_offer(
            actor_data,
            target_data,
            payload.get('actor_offer'),
        )
        target_moved = CharacterInteractionService._apply_offer(
            target_data,
            actor_data,
            payload.get('target_offer'),
        )
        if state and state.status == 'active':
            actor.action_points_current -= 2
            CombatService._clear_aim(actor)
        from app.services.action_journal import append_trade
        append_trade(actor_data, request_row.id, actor.character_id, target.character.name,
            given=actor_moved, received=target_moved, money_given=money_result['actor_to_target_money'],
            money_received=money_result['target_to_actor_money'], action_points=2 if state and state.status=='active' else 0,
            location_id=actor.location_id, merchant=bool(payload.get('_merchant')))
        append_trade(target_data, request_row.id, target.character_id, actor.character.name,
            given=target_moved, received=actor_moved, money_given=money_result['target_to_actor_money'],
            money_received=money_result['actor_to_target_money'], location_id=actor.location_id,
            merchant=bool(payload.get('_merchant')))
        actor.character.data = actor_data
        target.character.data = target_data
        flag_modified(actor.character, 'data')
        flag_modified(target.character, 'data')
        request_row.status = 'completed'
        request_row.result = {
            **(request_row.result or {}),
            'action_points': 2 if state and state.status == 'active' else 0,
            'actor_to_target': actor_moved,
            'target_to_actor': target_moved,
            **money_result,
        }
        request_row.resolved_at = datetime.now(timezone.utc)
        db.session.commit()

    @staticmethod
    def mark_treatment_in_progress(request_id, user_id, pending_action_id=None):
        request_row = db.session.get(CharacterInteractionRequest, request_id)
        if not request_row or request_row.kind != 'treatment':
            raise NotFoundError('Treatment request not found')
        if request_row.actor_user_id != user_id or request_row.status not in {'accepted', 'forced'}:
            raise PermissionDenied('Treatment request cannot be started')
        request_row.status = 'in_progress'
        request_row.payload = {
            **(request_row.payload or {}),
            'pending_action_id': str(pending_action_id or ''),
        }
        flag_modified(request_row, 'payload')
        db.session.commit()
        return CharacterInteractionService._serialize(request_row)

    @staticmethod
    def complete_treatment(request_id, user_id, *, commit=True):
        request_row = db.session.get(CharacterInteractionRequest, request_id)
        if not request_row or request_row.kind != 'treatment':
            raise NotFoundError('Treatment request not found')
        if request_row.actor_user_id != user_id:
            raise PermissionDenied('Treatment request does not belong to this user')
        if request_row.status not in {'accepted', 'forced', 'in_progress'}:
            raise ValidationError('Treatment request is not active')
        request_row.status = 'completed'
        request_row.resolved_at = datetime.now(timezone.utc)
        if commit:
            db.session.commit()
        return CharacterInteractionService._serialize(request_row)

    @staticmethod
    def validate_treatment(request_id, actor, target):
        request_row = db.session.get(CharacterInteractionRequest, request_id)
        if not request_row or request_row.kind != 'treatment':
            raise NotFoundError('Treatment consent not found')
        if (
            request_row.actor_location_character_id != actor.id
            or request_row.target_location_character_id != target.id
            or request_row.status not in {'accepted', 'forced', 'in_progress'}
        ):
            raise PermissionDenied('Treatment was not authorized')
        return request_row

    @staticmethod
    def movement_locked(location_character_id):
        from app.services.prosthesis_surgery import busy
        character=db.session.get(LocationCharacter,location_character_id)
        if character and character.character and busy(character.character.data):return True
        requests = CharacterInteractionRequest.query.filter(
            CharacterInteractionRequest.target_location_character_id == location_character_id,
            CharacterInteractionRequest.kind == 'treatment',
            CharacterInteractionRequest.status == 'in_progress',
        ).all()
        for request_row in requests:
            actor = db.session.get(LocationCharacter, request_row.actor_location_character_id)
            actor_data = actor.character.data if actor and actor.character and isinstance(actor.character.data, dict) else {}
            meta = (actor_data.get('health') or {}).get('combatMeta') or {}
            pending_action = meta.get('pendingAction') or {}
            expected_id = str((request_row.payload or {}).get('pending_action_id') or '')
            combat = LocationCombatState.query.filter_by(location_id=request_row.location_id).first()
            # Full payment is not completion: keep the patient still until effects are committed.
            if combat and combat.status == 'active' and actor and actor.id in (combat.turn_order or []) and expected_id and expected_id in {
                str(pending_action.get('id') or ''), str(meta.get('completedPendingActionId') or ''),
            }:
                return True
            request_row.status = 'cancelled'
            request_row.resolved_at = datetime.now(timezone.utc)
        # The caller owns the transaction; movement validation must not commit unrelated changes.
        return False
