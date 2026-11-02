# app/services/character.py
import logging
import math
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from sqlalchemy.orm import joinedload
from app.extensions import db
from app.models import LobbyCharacter, Lobby, LobbyParticipant, LocationCharacter
from app.services.exceptions import NotFoundError, PermissionDenied, ValidationError, ConflictError
from app.services.health import apply_health_maximums, health_zones_to_location
from app.services.inventory import normalize_inventory_ammo_stacks
from app.services.effects import normalize_effect_list, sync_health_derived_statuses
from app.services.character_merge import clean_snapshot, merge_sheet_data
from app.services.character_roles import follows_world_time

logger = logging.getLogger(__name__)


def _preserve_server_zombification(current_data,incoming_data):
    snapshot=(current_data or {}).get('psiZombification')
    if isinstance(snapshot,dict):
        incoming_data['psiZombification']=deepcopy(snapshot)
    else:
        incoming_data.pop('psiZombification',None)


def _preserve_server_medical_retry(current_data, incoming_data):
    # Публичный лист не создаёт, не заменяет и не отменяет серверную процедуру.
    current_health = (current_data or {}).get('health')
    current_meta = current_health.get('combatMeta') if isinstance(current_health, dict) else None
    current_retry = current_meta.get('mustDoRetry') if isinstance(current_meta, dict) else None
    health = incoming_data.get('health')
    if not isinstance(health, dict):
        return
    meta = health.get('combatMeta')
    if not isinstance(meta, dict):
        meta = health['combatMeta'] = {}
    private = current_retry.get('medical_retry') if isinstance(current_retry, dict) else None
    if (isinstance(current_retry, dict) and current_retry.get('kind') == 'medical'
            and isinstance(private, dict) and private.get('server_authoritative') is True):
        meta['mustDoRetry'] = deepcopy(current_retry)
    else:
        incoming_retry = meta.get('mustDoRetry')
        if isinstance(incoming_retry, dict) and (
            incoming_retry.get('kind') == 'medical'
            or any(key in incoming_retry for key in ('medical_retry', 'item_snapshot', 'interaction_context'))
        ):
            # Старый/поддельный summary не возрождает уже завершённый повтор.
            meta.pop('mustDoRetry', None)


@dataclass
class CharacterUpdate:
    character: LobbyCharacter
    posture_updates: list


class CharacterService:
    @staticmethod
    def restore_player_sheet(character):
        current=deepcopy(character.data or {})
        basic=current.get('basic') or {}
        profile=(current.get('mutant') or {}).get('profile',basic.get('mutant_type'))
        if str(profile or '').casefold()!='зомбированный' or str(basic.get('former_character_id'))!=str(character.id):
            raise ValidationError('Это не персонаж, превращённый в Зомбированного после Пси-урона')
        snapshot=current.get('psiZombification') or {}
        previous=snapshot.get('previousData')
        data=deepcopy(previous if isinstance(previous,dict) else current)
        # Вещи могли измениться уже после превращения: возвращаем текущие, не копии до смерти.
        for key in ('inventory','equipment','money','currency','cash'):
            if key in current:data[key]=deepcopy(current[key])
        data['weapons']=[deepcopy(item) for item in current.get('weapons',[])
                         if not str(item.get('id','')).startswith('mutant-attack-')]
        live_health=deepcopy(current.get('health') or {})
        data['health']=live_health
        for owner in (data,data.setdefault('basic',{})):
            for key in ('is_mutant','isMutant','is_npc','mutant_type','mutant_variant','former_character_id','former_name','characterType'):
                owner.pop(key,None)
            owner['character_type']='player'
            if 'мутант' in str(owner.get('species','')).casefold():owner['species']='Человек'
        data.pop('mutant',None);data.pop('psiZombification',None)
        if not isinstance(previous,dict):data.pop('movement',None)
        meta=live_health.get('combatMeta') or {}
        for key in list(meta):
            if key.startswith('mutant') or key in ('zombificationController','zombificationPreviousController'):meta.pop(key,None)
        old_values={'current':live_health.get('current',0),
            'zones':{key:zone.get('current',0) for key,zone in (live_health.get('zones') or {}).items()}}
        health=apply_health_maximums(data)
        # Смена типа листа не лечит персонажа и не воскрешает погибшего.
        health['current']=min(health['max'],max(0,float(old_values['current'])))
        for key,value in old_values['zones'].items():
            if key in health['zones']:health['zones'][key]['current']=min(health['zones'][key]['max'],max(0,float(value)))
        character.data=data
        previous_controller=snapshot.get('previousController',character.owner_id)
        participant=LobbyParticipant.query.filter_by(lobby_id=character.lobby_id,user_id=previous_controller,is_banned=False).first()
        if not participant:
            participant=LobbyParticipant.query.filter_by(lobby_id=character.lobby_id,user_id=character.owner_id,is_banned=False).first()
        for model in LocationCharacter.query.filter_by(character_id=character.id).all():
            if participant:model.controlled_by=participant.user_id
            model.drawn_weapon_index=None
        updates=CharacterService.sync_location_health(character,health)
        return CharacterUpdate(character,updates)

    @staticmethod
    def require_membership(lobby_id, user_id):
        if user_id is None:
            raise PermissionDenied("Access denied")
        participant = db.session.get(LobbyParticipant, (lobby_id, user_id))
        if not participant or participant.is_banned:
            raise PermissionDenied("Access denied")

    @staticmethod
    def check_access(character, user_id, *, edit=False):
        CharacterService.require_membership(character.lobby_id, user_id)
        lobby = db.session.get(Lobby, character.lobby_id)
        if character.owner_id == user_id or lobby.gm_id == user_id:
            return
        if user_id in (character.editable_to or []):
            return
        if not edit and user_id in (character.visible_to or []):
            return
        if LocationCharacter.query.filter_by(character_id=character.id, controlled_by=user_id).first():
            return
        raise PermissionDenied("Access denied")

    @staticmethod
    def _access_lists(visible_to, editable_to):
        if not isinstance(visible_to, list) or not isinstance(editable_to, list):
            raise ValidationError("Access lists must be lists of user IDs")
        if any(type(value) is not int or value <= 0 for value in [*visible_to, *editable_to]):
            raise ValidationError("Access lists must contain positive integer user IDs")
        editable = list(dict.fromkeys(editable_to))
        return list(dict.fromkeys([*visible_to, *editable])), editable

    @staticmethod
    def sync_location_health(character, health):
        """Keep sheet effects, body zones and the placed model in sync."""
        health['effects'] = normalize_effect_list(health.get('effects') or [])
        sync_health_derived_statuses(health)
        effect_types = {
            effect.get('type') for effect in health['effects']
            if isinstance(effect, dict) and effect.get('active', True)
        }
        zones = health.get('zones') or {}
        vital_zone_zero = any(
            float((zones.get(zone) or {}).get('max') or 0) > 0
            and float((zones.get(zone) or {}).get('current') or 0) <= 0
            for zone in ('head', 'chest')
        )
        total_zero = health.get('current') is not None and float(health['current']) <= 0
        incapacitated = bool(
            effect_types.intersection({'shock', 'unconsciousness', 'critical_condition', 'death'})
            or total_zero or vital_zone_zero
        )
        posture_updates = []
        for model in LocationCharacter.query.filter_by(character_id=character.id).all():
            model.effects = deepcopy(health['effects'])
            model.hp_zones = health_zones_to_location(health)
            if incapacitated:
                if model.posture != 'prone':
                    posture_updates.append({
                        'location_id': model.location_id,
                        'character_id': character.id,
                        'posture': 'prone',
                    })
                model.posture = 'prone'
                model.cover_object_id = None
                model.weapon_braced = False
                model.braced_weapon_index = None
        return posture_updates

    @staticmethod
    def apply_manual_field_resets(current_data, updated_data, manual_fields):
        """Treat explicitly edited health and skill fields as a new baseline."""
        if not isinstance(updated_data, dict) or not isinstance(manual_fields, list):
            return updated_data

        current_data = current_data if isinstance(current_data, dict) else {}
        health = updated_data.get('health')
        current_health = current_data.get('health') if isinstance(current_data.get('health'), dict) else {}
        reset_round_damage = False
        manual_paths = set(manual_fields)

        for raw_path in manual_fields:
            path = str(raw_path or '')
            parts = path.split('.')
            if len(parts) == 4 and parts[:2] == ['health', 'zones'] and parts[3] in {'current', 'max'}:
                area = parts[2]
                zone = (
                    (health.get('zones') or {}).get(area)
                    if isinstance(health, dict) and isinstance(health.get('zones'), dict)
                    else None
                )
                old_zone = (
                    (current_health.get('zones') or {}).get(area)
                    if isinstance(current_health.get('zones'), dict)
                    else None
                )
                if (
                    isinstance(zone, dict)
                    and isinstance(old_zone, dict)
                    and zone.get(parts[3]) != old_zone.get(parts[3])
                ):
                    zone['destructionDamage'] = 0
                    reset_round_damage = True
            elif path == 'health.current' and isinstance(health, dict):
                if health.get('current') != current_health.get('current'):
                    reset_round_damage = True
            elif (len(parts) == 4 and parts[0] == 'skills'
                  and parts[1] in {'physical', 'social', 'other'} and parts[3] == 'base'):
                old, new = current_data, updated_data
                for part in parts[:3]:
                    old = old.get(part) if isinstance(old, dict) else None
                    new = new.get(part) if isinstance(new, dict) else None
                # Явно переданный xp может уже включать нажатую после ввода кнопку.
                if (isinstance(new, dict)
                        and new.get('base') != (old.get('base') if isinstance(old, dict) else None)
                        and '.'.join(parts[:3] + ['xp']) not in manual_paths):
                    new['xp'] = 0

        if reset_round_damage and isinstance(health, dict):
            combat_meta = health.get('combatMeta')
            if isinstance(combat_meta, dict):
                for key in (
                    'damageTakenThisRound',
                    'damagePainAppliedThisRound',
                    'damagePainRound',
                    'pendingDamageStressTrigger',
                ):
                    combat_meta.pop(key, None)
        return updated_data

    @staticmethod
    def validate_skill_fields(current_data, updated_data):
        """Проверяет изменённые числа навыков, сохраняя совместимость старых листов."""
        skills = updated_data.get('skills')
        if not isinstance(skills, dict):
            return
        previous = (current_data or {}).get('skills') or {}
        previous = previous if isinstance(previous, dict) else {}
        for category in ('physical', 'social', 'other'):
            group = skills.get(category) or {}
            old_group = previous.get(category) or {}
            if not isinstance(group, dict):
                if group == old_group:
                    continue
                raise ValidationError('Навыки должны быть объектом')
            old_group = old_group if isinstance(old_group, dict) else {}
            for key, skill in group.items():
                old = old_group.get(key) or {}
                if not isinstance(skill, dict) or not isinstance(old, dict):
                    if skill == old:
                        continue
                    raise ValidationError('Некорректные данные навыка')
                for field in ('base', 'bonus', 'xp'):
                    if field not in skill or (skill[field] == old.get(field) and type(skill[field]) is type(old.get(field))):
                        continue
                    value = skill[field]
                    if (type(value) not in (int, float) or not -2**53 + 1 <= value <= 2**53 - 1 or not math.isfinite(value)
                            or (field != 'bonus' and (int(value) != value or abs(value) > 2**53 - 1))
                            or (field == 'xp' and value < 0)):
                        raise ValidationError('Значение навыка и опыт должны быть корректными числами')
        for field in ('skillPoints', 'specializations'):
            if field not in skills or (skills[field] == previous.get(field) and type(skills[field]) is type(previous.get(field))):
                continue
            value = skills[field]
            if type(value) not in (int, float) or not 0 <= value <= 2**53 - 1 or not math.isfinite(value) or int(value) != value:
                raise ValidationError('Количество свободных очков должно быть целым неотрицательным числом')

    @staticmethod
    def _item_totals(character_data):
        """Count item quantities regardless of their current container or slot."""
        totals = Counter()

        def visit(value):
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if not isinstance(value, dict):
                return

            template_id = value.get('templateId')
            looks_like_item = (
                template_id is not None
                or (
                    value.get('id')
                    and any(key in value for key in ('category', 'weight', 'volume', 'quantity'))
                )
            )
            if looks_like_item:
                if template_id is not None:
                    key = f"template:{template_id}"
                else:
                    key = (
                        f"custom:{value.get('category', '')}:"
                        f"{str(value.get('name', '')).strip().casefold()}"
                    )
                try:
                    quantity = float(value.get('quantity', 1) or 0)
                except (TypeError, ValueError):
                    quantity = 1
                totals[key] += max(0, quantity)

            for nested in value.values():
                visit(nested)

        visit(character_data if isinstance(character_data, dict) else {})
        return totals

    @staticmethod
    def mark_added_items_as_player_created(current_data, updated_data):
        """Mark quantities introduced by a player without flagging moved items."""
        remaining = CharacterService._item_totals(current_data)

        def visit(value):
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if not isinstance(value, dict):
                return

            template_id = value.get('templateId')
            looks_like_item = (
                template_id is not None
                or (
                    value.get('id')
                    and any(key in value for key in ('category', 'weight', 'volume', 'quantity'))
                )
            )
            if looks_like_item:
                key = (
                    f"template:{template_id}"
                    if template_id is not None
                    else (
                        f"custom:{value.get('category', '')}:"
                        f"{str(value.get('name', '')).strip().casefold()}"
                    )
                )
                try:
                    quantity = max(0, float(value.get('quantity', 1) or 0))
                except (TypeError, ValueError):
                    quantity = 1
                existing_quantity = max(0, remaining.get(key, 0))
                if quantity > existing_quantity:
                    value['createdByPlayer'] = True
                remaining[key] = max(0, existing_quantity - quantity)

            for nested in value.values():
                visit(nested)

        visit(updated_data if isinstance(updated_data, dict) else {})
        return updated_data

    @staticmethod
    def create_character(lobby_id, owner_id, name, data=None):
        CharacterService.require_membership(lobby_id, owner_id)

        character_data = clean_snapshot(data or {})
        jump_meta = (character_data.get('health') or {}).get('combatMeta')
        if isinstance(jump_meta,dict):
            for key in ('unstableJump','jumpSupport','slipperyMovement','grenadeReaction','heldWaterItem'):
                jump_meta.pop(key,None)
        normalize_inventory_ammo_stacks(character_data)
        apply_health_maximums(character_data)
        character = LobbyCharacter(
            lobby_id=lobby_id,
            owner_id=owner_id,
            name=name,
            data=character_data,
            visible_to=[],
            editable_to=[],
            time_active=follows_world_time(character_data),
        )
        db.session.add(character)
        db.session.commit()
        db.session.refresh(character, attribute_names=['owner'])
        logger.info(f"Character '{name}' (id={character.id}) created by user {owner_id} in lobby {lobby_id}")
        return character

    @staticmethod
    def get_character(character_id, user_id):
        """Получение персонажа по ID (с проверкой доступа)."""
        character = LobbyCharacter.query.get(character_id)
        if not character:
            raise NotFoundError("Character not found")

        CharacterService.check_access(character, user_id)
        return character

    @staticmethod
    def update_character(character_id, user_id, updates, *, commit=True):
        """Validate and save a sheet identically for HTTP and Socket.IO."""
        character = LobbyCharacter.query.get(character_id)
        if not character:
            raise NotFoundError("Character not found")

        CharacterService.check_access(character, user_id, edit=True)
        lobby = Lobby.query.get(character.lobby_id)
        is_gm = lobby.gm_id == user_id
        allowed = {'name', 'data', 'visible_to', 'editable_to', '_manual_fields', '_save_id', '_base_data'}
        if not isinstance(updates, dict) or set(updates) - allowed:
            raise ValidationError("Unsupported character fields")
        if 'data' in updates and not isinstance(updates['data'], dict):
            raise ValidationError("Character data must be an object")
        if '_base_data' in updates and not isinstance(updates['_base_data'], dict):
            raise ValidationError("Base data must be an object")
        if '_save_id' in updates and (not isinstance(updates['_save_id'], str) or len(updates['_save_id']) > 100):
            raise ValidationError("Invalid save ID")
        if 'name' in updates and (
            not isinstance(updates['name'], str)
            or not updates['name'].strip() or len(updates['name']) > 100
        ):
            raise ValidationError("Character name must contain 1 to 100 characters")
        if '_manual_fields' in updates and (
            not isinstance(updates['_manual_fields'], list)
            or any(not isinstance(path, str) for path in updates['_manual_fields'])
        ):
            raise ValidationError("Manual fields must be a list of paths")
        access = None
        posture_updates = []
        if 'visible_to' in updates or 'editable_to' in updates:
            if not is_gm:
                raise PermissionDenied("Only GM can change visibility")
            access = CharacterService._access_lists(
                updates.get('visible_to', character.visible_to or []),
                updates.get('editable_to', character.editable_to or []),
            )
        if 'data' in updates:
            character_data = clean_snapshot(updates['data'])
            from app.services.deferred_action import preserve_action_payment
            preserve_action_payment(character.data, character_data)
            revision = updates['data'].get('_revision')
            if type(revision) is not int or revision <= 0 or revision > character.revision:
                raise ConflictError()
            if revision != character.revision:
                if '_base_data' not in updates:
                    raise ConflictError()
                base_data = clean_snapshot(updates['_base_data'])
                preserve_action_payment(character.data, base_data)
                character_data = merge_sheet_data(base_data, character_data, character.data)
            _preserve_server_medical_retry(character.data, character_data)
            _preserve_server_zombification(character.data,character_data)
            CharacterService.validate_skill_fields(character.data, character_data)
            CharacterService.apply_manual_field_resets(
                character.data,
                character_data,
                updates.get('_manual_fields'),
            )
            normalize_inventory_ammo_stacks(character_data)
            if not is_gm:
                CharacterService.mark_added_items_as_player_created(
                    character.data,
                    character_data,
                )
            # Нормализация предметов не должна менять внутренний снимок процедуры.
            _preserve_server_medical_retry(character.data, character_data)
            _preserve_server_zombification(character.data,character_data)
            health = apply_health_maximums(character_data)
            posture_updates = CharacterService.sync_location_health(character, health)
            character.data = character_data
        if 'name' in updates:
            character.name = updates['name']
        if access is not None:
            character.visible_to, character.editable_to = access
        if commit:
            db.session.commit()
        logger.debug("Character %s updated by user %s", character_id, user_id)
        return CharacterUpdate(character, posture_updates)

    @staticmethod
    def delete_character(character_id, user_id):
        """Удаление персонажа (владелец или GM)."""
        character = LobbyCharacter.query.get(character_id)
        if not character:
            raise NotFoundError("Character not found")
        CharacterService.require_membership(character.lobby_id, user_id)
        lobby = Lobby.query.get(character.lobby_id)
        if character.owner_id != user_id and lobby.gm_id != user_id:
            raise PermissionDenied("Permission denied")

        # Location entries reference the character without database-level cascade.
        LocationCharacter.query.filter_by(character_id=character.id).delete(
            synchronize_session=False
        )
        db.session.delete(character)
        db.session.commit()
        logger.info(f"Character {character_id} deleted by user {user_id}")

    @staticmethod
    def get_lobby_characters(lobby_id, user_id):
        """Возвращает список персонажей в комнаты, видимых пользователю."""
        CharacterService.require_membership(lobby_id, user_id)

        lobby = Lobby.query.get(lobby_id)
        is_gm = (lobby.gm_id == user_id)

        # Явно загружаем связанного владельца
        characters = LobbyCharacter.query.filter_by(lobby_id=lobby_id).options(
            joinedload(LobbyCharacter.owner)
        ).all()

        controlled_ids = {
            item.character_id
            for item in LocationCharacter.query.join(LobbyCharacter).filter(
                LocationCharacter.controlled_by == user_id,
                LobbyCharacter.lobby_id == lobby_id,
            ).all()
        }
        result = []
        for c in characters:
            if (
                c.owner_id == user_id or is_gm
                or user_id in (c.visible_to or [])
                or user_id in (c.editable_to or []) or c.id in controlled_ids
            ):
                result.append(c)
        return result

    @staticmethod
    def set_visibility(character_id, gm_id, visible_to, editable_to=None):
        """Устанавливает видимость и право редактирования персонажа (только GM)."""
        character = LobbyCharacter.query.get(character_id)
        if not character:
            raise NotFoundError("Character not found")
        CharacterService.require_membership(character.lobby_id, gm_id)
        lobby = Lobby.query.get(character.lobby_id)
        if lobby.gm_id != gm_id:
            raise PermissionDenied("Only GM can change visibility")

        if editable_to is None:
            editable_to = character.editable_to or []
        character.visible_to, character.editable_to = CharacterService._access_lists(visible_to, editable_to)
        db.session.commit()
        logger.info(
            "Access of character %s set to visible=%s editable=%s by GM %s",
            character_id, character.visible_to, character.editable_to, gm_id,
        )
        return character
