# app/lobbies/__init__.py
import json
import gzip
import io
import math
import random
import re
from copy import deepcopy
from types import SimpleNamespace
from datetime import datetime, timezone
from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified
from flask import Blueprint, request, jsonify, render_template, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.extensions import socketio, db
from app.services.lobby import LobbyService
from app.services.participant import ParticipantService
from app.services.map import MapService
from app.services.world_editor_history import record_world_editor_command, apply_world_editor_command
from app.services.character import CharacterService
from app.services.character_roles import can_join_world_group, follows_world_time, is_npc
from app.services.container_transfer import ContainerTransferService
from app.services.treatment import TreatmentService
from app.services.medical_procedure import MedicalProcedureService
from app.services.general_consumable import GeneralConsumableService
from app.services.custom_consumable_profile import validate_custom_consumable
from app.services.exceptions import ConflictError, PermissionDenied, ValidationError, NotFoundError
from app.services.character_events import emit_character_update, publish_character_save
from app.services.combat import CombatService
from app.services.world_travel_events import roll_travel_event, travel_scenario
from app.services.artifact_effects import apply_artifact_world_movement
from app.services.character_interaction import CharacterInteractionService
from app.services.health import apply_health_maximums, heal_character_fully, health_zones_to_location
from app.services.location_lighting import normalize_location_lighting
from app.services.location_water import freeze_water_offsets, preserve_water_offsets
from app.services.equipment_repair import repair_equipment, resolve_item_path
from app.services.gas_mask_filters import consume_equipped_filter_charges
from app.services.addictions import advance_addictions, record_exposure, withdrawal_check, remove_addiction
from app.services.anomaly_profiles import anomaly_catalog
from app.services.world_rules import (
    anomaly_field_catalog,
    anomaly_field_profile,
    artifact_catalog,
    guaranteed_artifact_class,
    mutant_catalog,
    mutant_character_data,
    mutant_profile,
    random_artifact,
    roll_artifact_class,
)
from app.services.effects import (
    advance_timed_effects,
    apply_effect_to_health,
    normalize_effect_list,
    sync_health_derived_statuses,
)
from app.schemas.lobby import LobbyCreateSchema, LobbyDetailSchema, LobbyMySchema, LobbySchema
from app.schemas.participant import BannedUserSchema
from app.schemas.character import CharacterSchema, CharacterCreateSchema
from app.schemas.map import GameStateSchema, MapChunkSchema, TileUpdateSchema
from app.models import (
    GameState,
    ChatMessage,
    Lobby,
    LobbyCharacter,
    LobbyParticipant,
    LocationCombatState,
    CharacterInteractionRequest,
    User,
    WorldGroup,
    WorldMapEvent,
    WorldTravelEvent,
)
from app.utils.decorators import requires_participant, requires_gm
from app.models.location import Location
from app.models.location_character import LocationCharacter
from app.models.location_object import LocationObject
from app.schemas.location import LocationCreateSchema, LocationSchema, LocationObjectSchema


def _emit_lobby_chat_message(lobby_id, user_id, message, username='Бой'):
    chat_message = ChatMessage(
        lobby_id=lobby_id,
        user_id=user_id,
        username=username,
        message=message,
    )
    db.session.add(chat_message)
    db.session.commit()
    payload = {
        'username': username,
        'message': message,
        'timestamp': chat_message.timestamp.isoformat(),
    }
    socketio.emit('new_message', payload, room=f"lobby_{lobby_id}")
    return payload

# Импорты для универсальных шаблонов
from app.models.templates import ItemTemplate
from app.schemas.templates import ItemTemplateSchema
from app.models.lobby_templates import LobbyItemTemplate
from app.schemas.lobby_templates import LobbyItemTemplateSchema

lobbies_bp = Blueprint('lobbies', __name__)


@lobbies_bp.route('/<int:lobby_id>/world-rules', methods=['GET'])
@jwt_required()
@requires_participant
def get_world_rule_catalogs(lobby_id, lobby, participant):
    return jsonify({
        'anomalies': anomaly_catalog(),
        'anomaly_fields': anomaly_field_catalog(),
        'artifacts': artifact_catalog(),
        'mutants': mutant_catalog(),
    }), 200


@lobbies_bp.route('/<int:lobby_id>/mutants', methods=['POST'])
@jwt_required()
@requires_gm
def create_mutant_character(lobby_id, lobby):
    payload = request.get_json(silent=True) or {}
    profile = mutant_profile(payload.get('mutant_type'))
    if not profile:
        return jsonify({'error': 'Unknown mutant type'}), 400
    variant_name = str(payload.get('variant') or '').strip() or None
    if variant_name and not any(
        str(item.get('name') or '').casefold() == variant_name.casefold()
        for item in (profile.get('variants') or [])
    ):
        return jsonify({'error': 'Unknown mutant variant'}), 400
    name = str(payload.get('name') or variant_name or profile['name']).strip()[:100]
    character = CharacterService.create_character(
        lobby_id=lobby_id, owner_id=lobby.gm_id, name=name,
        data=mutant_character_data(profile, variant_name),
    )
    response = CharacterSchema().dump(character)
    socketio.emit('character_created', response, room=f'lobby_{lobby_id}')
    return jsonify(response), 201

BODY_CARRY_ROPE_NAME = 'канат для переноски'


def _iter_carried_items(character_data):
    data = character_data if isinstance(character_data, dict) else {}
    inventory = data.get('inventory') if isinstance(data.get('inventory'), dict) else {}
    equipment = data.get('equipment') if isinstance(data.get('equipment'), dict) else {}
    roots = []
    for key in ('backpack', 'pockets'):
        if isinstance(inventory.get(key), list):
            roots.extend(inventory[key])
    for group_name in ('belt', 'vest'):
        container = equipment.get(group_name)
        pouches = container.get('pouches') if isinstance(container, dict) else []
        for pouch in pouches if isinstance(pouches, list) else []:
            if isinstance(pouch, dict) and isinstance(pouch.get('contents'), list):
                roots.extend(pouch['contents'])

    pending = list(roots)
    while pending:
        item = pending.pop()
        if not isinstance(item, dict):
            continue
        yield item
        contents = item.get('contents')
        if isinstance(contents, list):
            pending.extend(contents)


def _body_carry_profile(member_ids, characters_by_id):
    profiles = {}
    rope_count = 0
    for character_id in member_ids:
        character = characters_by_id.get(character_id)
        if character is None:
            continue
        data = character.data if isinstance(character.data, dict) else {}
        breakdown = CombatService._movement_penalty_breakdown(data)
        condition = CombatService._character_condition(data)
        profiles[character_id] = {
            'breakdown': breakdown,
            'condition': condition,
            'requires_carry': not condition['can_act'],
            'uses_carry_rope': False,
            'carry_penalty': 0,
            'carried_by': None,
        }
        for item in _iter_carried_items(data):
            normalized_name = ' '.join(
                str(item.get('name') or '').strip().lower().replace('ё', 'е').split()
            )
            attributes = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
            if normalized_name == BODY_CARRY_ROPE_NAME or attributes.get('body_carry_rope'):
                rope_count += max(0, CombatService._coerce_int(item.get('quantity'), 1))

    carriers = [
        character_id for character_id in member_ids
        if character_id in profiles and not profiles[character_id]['requires_carry']
    ]
    bodies = [
        character_id for character_id in member_ids
        if character_id in profiles and profiles[character_id]['requires_carry']
    ]
    if not bodies:
        return profiles, rope_count, []
    if not carriers:
        return profiles, rope_count, []

    bodies_by_weight = sorted(
        bodies,
        key=lambda character_id: profiles[character_id]['breakdown']['weight'],
        reverse=True,
    )
    for character_id in bodies_by_weight[:rope_count]:
        profiles[character_id]['uses_carry_rope'] = True
    for character_id in bodies:
        weight_penalty = profiles[character_id]['breakdown']['weight']
        profiles[character_id]['carry_penalty'] = (
            1 + weight_penalty / 2
            if profiles[character_id]['uses_carry_rope']
            else 2 + weight_penalty
        )

    carrier_loads = {
        character_id: float(profiles[character_id]['breakdown']['total'])
        for character_id in carriers
    }
    assignments = []
    for body_id in sorted(
        bodies,
        key=lambda character_id: profiles[character_id]['carry_penalty'],
        reverse=True,
    ):
        carrier_id = min(carriers, key=lambda character_id: carrier_loads[character_id])
        carry_penalty = profiles[body_id]['carry_penalty']
        carrier_loads[carrier_id] += carry_penalty
        profiles[body_id]['carried_by'] = carrier_id
        assignments.append({
            'body_id': body_id,
            'carrier_id': carrier_id,
            'penalty': carry_penalty,
            'uses_carry_rope': profiles[body_id]['uses_carry_rope'],
        })
    for carrier_id, total in carrier_loads.items():
        profiles[carrier_id]['effective_movement_penalty'] = total
    return profiles, rope_count, assignments


def _eligible_world_group_characters(group):
    member_ids = [
        int(value) for value in (group.member_character_ids or [])
        if str(value).isdigit()
    ]
    characters_by_id = {
        character.id: character
        for character in LobbyCharacter.query.filter(
            LobbyCharacter.lobby_id == group.lobby_id,
            LobbyCharacter.id.in_(member_ids),
        ).all()
    } if member_ids else {}
    member_ids = [
        character_id for character_id in member_ids
        if character_id in characters_by_id
        and can_join_world_group(characters_by_id[character_id])
    ]
    return member_ids, {character_id: characters_by_id[character_id] for character_id in member_ids}


def _serialize_world_group(group):
    pending_event = next(
        (event for event in group.travel_events if event.status == 'pending'),
        None,
    )
    member_ids, characters_by_id = _eligible_world_group_characters(group)
    carry_profiles, rope_count, carry_assignments = _body_carry_profile(
        member_ids,
        characters_by_id,
    )
    member_penalties = {
        character_id: profile['breakdown']['total']
        for character_id, profile in carry_profiles.items()
    }
    carried_member_count = sum(
        1 for profile in carry_profiles.values() if profile['requires_carry']
    )
    capable_member_count = len(carry_profiles) - carried_member_count
    if carried_member_count and not capable_member_count:
        maximum_penalty = 10
    else:
        effective_penalties = [
            profile.get('effective_movement_penalty', profile['breakdown']['total'])
            for profile in carry_profiles.values()
            if not profile['requires_carry']
        ]
        maximum_penalty = max(effective_penalties, default=0)
    movement_distance, speed_label = _world_group_speed(maximum_penalty)
    turn_submitted = bool(
        group.turn_submitted_day == (group.lobby.game_day or 1)
        and group.turn_submitted_minutes == (group.lobby.game_time_minutes or 0)
    )
    tile = MapService.get_tile_data(group.lobby_id, group.tile_x, group.tile_y)
    field = tile.get('anomaly_field') if isinstance(tile.get('anomaly_field'), dict) else None
    emission_generation = int(
        ((group.lobby.weather_settings or {}).get('emission_generation') or 0)
    )
    field_payload = None
    if field:
        state = field.get('loot_state') if isinstance(field.get('loot_state'), dict) else {}
        field_payload = {
            'name': field.get('name'),
            'rank': field.get('rank'),
            'field_type': field.get('field_type'),
            'hazard': field.get('hazard'),
            'searched': state.get('generation') == emission_generation,
            'remaining_artifacts': sum(
                1 for item in (state.get('artifacts') or [])
                if isinstance(item, dict) and not item.get('recovered')
            ) if state.get('generation') == emission_generation else None,
        }
    return {
        'id': group.id,
        'name': group.name,
        'tile_x': group.tile_x,
        'tile_y': group.tile_y,
        'has_pending_event': pending_event is not None,
        'members': [
            {
                'id': character_id,
                'name': characters_by_id[character_id].name,
                'movement_penalty': member_penalties[character_id],
                'condition': carry_profiles[character_id]['condition'],
                'requires_carry': carry_profiles[character_id]['requires_carry'],
                'carry_penalty': carry_profiles[character_id]['carry_penalty'],
                'uses_carry_rope': carry_profiles[character_id]['uses_carry_rope'],
                'carried_by': carry_profiles[character_id]['carried_by'],
            }
            for character_id in member_ids
            if character_id in characters_by_id
        ],
        'movement_penalty': maximum_penalty,
        'movement_distance': movement_distance,
        'movement_speed_label': speed_label,
        'carried_member_count': carried_member_count,
        'carry_rope_count': rope_count,
        'carry_assignments': carry_assignments,
        'turn_active': bool(group.turn_active),
        'turn_submitted': turn_submitted,
        'anomaly_field': field_payload,
    }


def _world_emission_generation(lobby):
    return max(0, int(((lobby.weather_settings or {}).get('emission_generation') or 0)))


def _world_character_generator_bonus(character_data):
    bonus = 0
    for item in _iter_carried_items(character_data):
        attributes = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
        bonus = max(
            bonus,
            CombatService._coerce_int(
                attributes.get('artifact_generator_bonus', attributes.get('generator_bonus')),
                0,
            ),
        )
    return bonus


def _artifact_inventory_item(template):
    return {
        'id': f'artifact-{template.id}-{random.randint(100000, 999999)}',
        'templateId': template.id,
        'name': template.name,
        'category': template.category,
        'subcategory': template.subcategory,
        'quantity': 1,
        'weight': template.weight or 0,
        'volume': template.volume or 0,
        'price': template.price or 0,
        'attributes': deepcopy(template.attributes or {}),
        'contents': [],
        'installedModules': [],
        'isStackable': True,
    }


def _field_loot_state(field, character_data, generation):
    existing = field.get('loot_state') if isinstance(field.get('loot_state'), dict) else {}
    if existing.get('generation') == generation:
        return existing
    rank = max(1, min(4, CombatService._coerce_int(field.get('rank'), 1)))
    generator_bonus = _world_character_generator_bonus(character_data)
    survival_bonus = CombatService._skill_modifier(
        character_data, 'skills.other.survival',
    )
    untouched_roll = random.randint(1, 100)
    state = {
        'generation': generation,
        'untouched_roll': untouched_roll,
        'untouched': untouched_roll <= 50,
        'artifacts': [],
        'attempts_by_character': {},
    }
    if not state['untouched']:
        return state
    guaranteed = random_artifact(
        guaranteed_artifact_class(rank), field.get('field_type'),
    )
    if guaranteed:
        state['artifacts'].append({
            'name': guaranteed['name'], 'artifact_class': guaranteed['artifact_class'],
            'guaranteed': True, 'recovered': False,
        })
    random_slots = max(0, random.randint(1, 4) - 2)
    detection_difficulty = max(1, 14 - generator_bonus - survival_bonus - rank)
    state['random_slots'] = random_slots
    state['detection_difficulty'] = detection_difficulty
    state['detection_rolls'] = []
    for _ in range(random_slots):
        roll = random.randint(1, 20)
        state['detection_rolls'].append(roll)
        if roll < detection_difficulty:
            continue
        artifact_class = roll_artifact_class(rank)
        artifact = random_artifact(artifact_class, field.get('field_type'))
        if artifact:
            state['artifacts'].append({
                'name': artifact['name'], 'artifact_class': artifact_class,
                'guaranteed': False, 'recovered': False,
            })
    return state


def _anomaly_field_protection(character_data, field_type):
    normalized = str(field_type or '').strip().casefold()
    if normalized.startswith('радиоактив'):
        return CombatService._equipped_radiation_protection(character_data)
    damage_type = {
        'гравитационное': 'physical',
        'псионическое': 'psi',
        'термический': 'thermal',
        'химическое': 'chemical',
        'электрическое': 'electric',
    }.get(normalized)
    return CombatService._target_elemental_protection(
        character_data, damage_type,
    ) if damage_type else 0


def _world_group_speed(maximum_penalty):
    if maximum_penalty >= 10:
        return 0, 'Группа не может идти'
    if maximum_penalty <= 3:
        return 3, 'Без изменений'
    if maximum_penalty <= 6:
        return 2, 'На треть медленнее'
    return 1, 'Вдвое медленнее'


def _serialize_world_event(event, *, reveal_description):
    return {
        'id': event.id,
        'group_id': event.group_id,
        'group_name': event.group.name,
        'description': event.description if reveal_description else None,
        'status': event.status,
        'from_tile_x': event.from_tile_x,
        'from_tile_y': event.from_tile_y,
        'to_tile_x': event.to_tile_x,
        'to_tile_y': event.to_tile_y,
        'created_at': event.created_at.isoformat(),
        'world_map_event_id': event.world_map_event_id,
    }


def _serialize_world_map_event(event):
    return {
        'id': event.id,
        'name': event.name,
        'description': event.description,
        'tile_x': event.tile_x,
        'tile_y': event.tile_y,
        'repeatable': event.repeatable,
    }


def _world_route_tiles(from_x, from_y, to_x, to_y):
    steps = max(abs(to_x - from_x), abs(to_y - from_y))
    if steps <= 0:
        return []
    return [
        (
            round(from_x + (to_x - from_x) * step / steps),
            round(from_y + (to_y - from_y) * step / steps),
        )
        for step in range(1, steps + 1)
    ]


def _advance_world_time(lobby, minutes):
    previous_day = lobby.game_day or 1
    absolute_minutes = (
        (lobby.game_day or 1) * 1440
        + (lobby.game_time_minutes or 0)
        + minutes
    )
    lobby.game_day, lobby.game_time_minutes = divmod(absolute_minutes, 1440)
    group_member_ids = {
        int(character_id)
        for group in WorldGroup.query.filter_by(lobby_id=lobby.id).all()
        for character_id in (group.member_character_ids or [])
        if str(character_id).isdigit()
    }
    character_query = LobbyCharacter.query.filter(
        LobbyCharacter.lobby_id == lobby.id,
        or_(
            LobbyCharacter.time_active.is_(True),
            LobbyCharacter.id.in_(group_member_ids) if group_member_ids else False,
        ),
    )
    updated_characters = []
    for character in character_query.all():
        if not follows_world_time(character):
            continue
        character_data = dict(character.data or {})
        health = character_data.get('health')
        if not isinstance(health, dict):
            continue
        health['effects'] = advance_timed_effects(
            health,
            health.get('effects') or [],
            minutes * 60,
        )
        advance_addictions(health, previous_day, lobby.game_day)
        character.data = character_data
        flag_modified(character, 'data')
        updated_characters.append(character)
    return updated_characters


def _world_group_submitted_for_current_turn(group, lobby):
    return bool(
        group.turn_submitted_day == (lobby.game_day or 1)
        and group.turn_submitted_minutes == (lobby.game_time_minutes or 0)
    )


def _submit_world_group_turn(group, lobby):
    group.turn_submitted_day = lobby.game_day or 1
    group.turn_submitted_minutes = lobby.game_time_minutes or 0


def _complete_world_turn_if_ready(lobby):
    active_groups = WorldGroup.query.filter_by(
        lobby_id=lobby.id,
        turn_active=True,
    ).all()
    if not active_groups or not all(
        _world_group_submitted_for_current_turn(group, lobby)
        for group in active_groups
    ):
        return False, []
    return True, _advance_world_time(lobby, 10)


def _serialize_world_turn(lobby):
    active_groups = WorldGroup.query.filter_by(
        lobby_id=lobby.id,
        turn_active=True,
    ).order_by(WorldGroup.id).all()
    submitted_ids = [
        group.id for group in active_groups
        if _world_group_submitted_for_current_turn(group, lobby)
    ]
    return {
        'active_group_ids': [group.id for group in active_groups],
        'submitted_group_ids': submitted_ids,
        'waiting_group_ids': [
            group.id for group in active_groups if group.id not in submitted_ids
        ],
    }


def _emit_world_time_updates(lobby, characters):
    payload = {
        'game_day': lobby.game_day,
        'game_time_minutes': lobby.game_time_minutes,
    }
    socketio.emit('lobby_time_updated', payload, room=f"lobby_{lobby.id}")
    for character in characters:
        emit_character_update(
            {
                'character_id': character.id,
                'updates': {'data': character.data_snapshot()},
                'updated_by': lobby.gm_id,
            },
        )
    return payload


@lobbies_bp.route('/<int:lobby_id>/world-groups', methods=['GET'])
@jwt_required()
@requires_participant
def list_world_groups(lobby_id, lobby, participant):
    groups = WorldGroup.query.filter_by(lobby_id=lobby_id).order_by(WorldGroup.id).all()
    events = WorldTravelEvent.query.filter_by(
        lobby_id=lobby_id,
        status='pending',
    ).order_by(WorldTravelEvent.created_at).all()
    is_gm = participant.user_id == lobby.gm_id
    available_characters = []
    if is_gm:
        available_characters = [
            {'id': character.id, 'name': character.name}
            for character in LobbyCharacter.query.filter_by(lobby_id=lobby_id)
            .order_by(LobbyCharacter.name, LobbyCharacter.id).all()
            if can_join_world_group(character)
        ]
    map_events = []
    if is_gm:
        map_events = [
            _serialize_world_map_event(event)
            for event in WorldMapEvent.query.filter_by(
                lobby_id=lobby_id,
                is_active=True,
            ).order_by(WorldMapEvent.id).all()
        ]
    return jsonify({
        'groups': [_serialize_world_group(group) for group in groups],
        'world_turn': _serialize_world_turn(lobby),
        'available_characters': available_characters,
        'map_events': map_events,
        'pending_events': [
            _serialize_world_event(event, reveal_description=is_gm)
            for event in events
        ],
    }), 200


@lobbies_bp.route('/<int:lobby_id>/world-map-events', methods=['POST'])
@jwt_required()
@requires_gm
def create_world_map_event(lobby_id, lobby):
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    description = str(data.get('description') or '').strip()
    if not name or len(name) > 100:
        return jsonify({'error': 'Event name must contain from 1 to 100 characters'}), 400
    if not description:
        return jsonify({'error': 'Event description is required'}), 400
    try:
        tile_x = int(data.get('tile_x'))
        tile_y = int(data.get('tile_y'))
    except (TypeError, ValueError):
        return jsonify({'error': 'tile_x and tile_y must be integers'}), 400
    if not (0 <= tile_x < lobby.chunks_width * 32 and 0 <= tile_y < lobby.chunks_height * 32):
        return jsonify({'error': 'World tile is outside the map'}), 400
    event = WorldMapEvent(
        lobby_id=lobby_id,
        name=name,
        description=description,
        tile_x=tile_x,
        tile_y=tile_y,
        repeatable=data.get('repeatable') is True,
        created_by=lobby.gm_id,
    )
    db.session.add(event)
    db.session.commit()
    payload = _serialize_world_map_event(event)
    socketio.emit('world_map_event_created', {'id': event.id}, room=f"user_{lobby.gm_id}")
    return jsonify(payload), 201


@lobbies_bp.route('/<int:lobby_id>/world-map-events/<int:map_event_id>', methods=['DELETE'])
@jwt_required()
@requires_gm
def delete_world_map_event(lobby_id, map_event_id, lobby):
    event = WorldMapEvent.query.filter_by(id=map_event_id, lobby_id=lobby_id).first()
    if not event:
        return jsonify({'error': 'World map event not found'}), 404
    db.session.delete(event)
    db.session.commit()
    socketio.emit('world_map_event_deleted', {'id': map_event_id}, room=f"user_{lobby.gm_id}")
    return '', 204


@lobbies_bp.route('/<int:lobby_id>/world-groups', methods=['POST'])
@jwt_required()
@requires_gm
def create_world_group(lobby_id, lobby):
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    if not name or len(name) > 100:
        return jsonify({'error': 'Group name must contain from 1 to 100 characters'}), 400
    try:
        tile_x = int(data.get('tile_x'))
        tile_y = int(data.get('tile_y'))
    except (TypeError, ValueError):
        return jsonify({'error': 'tile_x and tile_y must be integers'}), 400
    if not (0 <= tile_x < lobby.chunks_width * 32 and 0 <= tile_y < lobby.chunks_height * 32):
        return jsonify({'error': 'World tile is outside the map'}), 400

    group = WorldGroup(
        lobby_id=lobby_id,
        name=name,
        tile_x=tile_x,
        tile_y=tile_y,
        member_character_ids=[],
        created_by=lobby.gm_id,
    )
    db.session.add(group)
    db.session.commit()
    payload = _serialize_world_group(group)
    socketio.emit('world_group_created', payload, room=f"lobby_{lobby_id}")
    return jsonify(payload), 201


@lobbies_bp.route('/<int:lobby_id>/world-groups/<int:group_id>/members', methods=['PATCH'])
@jwt_required()
@requires_gm
def update_world_group_members(lobby_id, group_id, lobby):
    group = WorldGroup.query.filter_by(id=group_id, lobby_id=lobby_id).first()
    if not group:
        return jsonify({'error': 'World group not found'}), 404
    data = request.get_json(silent=True) or {}
    try:
        member_ids = list(dict.fromkeys(int(value) for value in (data.get('character_ids') or [])))
    except (TypeError, ValueError):
        return jsonify({'error': 'character_ids must contain integers'}), 400
    existing_characters = {
        character.id: character for character in LobbyCharacter.query.filter(
            LobbyCharacter.lobby_id == lobby_id,
            LobbyCharacter.id.in_(member_ids),
        ).all()
    } if member_ids else {}
    if set(member_ids) != set(existing_characters):
        return jsonify({'error': 'Character does not belong to this lobby'}), 400
    if any(not can_join_world_group(character) for character in existing_characters.values()):
        return jsonify({'error': 'Mutants and non-joining NPCs cannot join a world group'}), 400
    from app.services.character_starting_purchase import grant_world_entry
    entered=[character for character in existing_characters.values() if grant_world_entry(character)]
    group.member_character_ids = member_ids
    db.session.commit()
    for character in entered:emit_character_update({'character_id':character.id,'updates':{'data':character.data_snapshot()},'source':'world_entry'})
    payload = _serialize_world_group(group)
    socketio.emit('world_group_updated', payload, room=f"lobby_{lobby_id}")
    return jsonify(payload), 200


@lobbies_bp.route('/<int:lobby_id>/world-groups/<int:group_id>/turn-active', methods=['PATCH'])
@jwt_required()
@requires_gm
def update_world_group_turn_activity(lobby_id, group_id, lobby):
    group = WorldGroup.query.filter_by(id=group_id, lobby_id=lobby_id).first()
    if not group:
        return jsonify({'error': 'World group not found'}), 404
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('active'), bool):
        return jsonify({'error': 'active must be a boolean'}), 400
    group.turn_active = data['active']
    group.turn_submitted_day = None
    group.turn_submitted_minutes = None
    time_advanced, updated_characters = _complete_world_turn_if_ready(lobby)
    db.session.commit()
    payload = _serialize_world_group(group)
    socketio.emit('world_group_updated', payload, room=f"lobby_{lobby_id}")
    time_payload = (
        _emit_world_time_updates(lobby, updated_characters)
        if time_advanced else None
    )
    return jsonify({
        'group': payload,
        'world_turn': _serialize_world_turn(lobby),
        'time_advanced': time_advanced,
        'time': time_payload,
    }), 200


@lobbies_bp.route('/<int:lobby_id>/world-groups/<int:group_id>/wait', methods=['POST'])
@jwt_required()
@requires_participant
def wait_world_group(lobby_id, group_id, lobby, participant):
    group = WorldGroup.query.filter_by(id=group_id, lobby_id=lobby_id).first()
    if not group:
        return jsonify({'error': 'World group not found'}), 404
    if not group.turn_active:
        return jsonify({'error': 'This group is not active in the current world turn'}), 409
    if _world_group_submitted_for_current_turn(group, lobby):
        return jsonify({'error': 'This group has already acted in the current world turn'}), 409
    pending = WorldTravelEvent.query.filter_by(group_id=group.id, status='pending').first()
    if pending:
        return jsonify({'error': 'The GM must resolve the pending travel event first'}), 409
    _submit_world_group_turn(group, lobby)
    time_advanced, updated_characters = _complete_world_turn_if_ready(lobby)
    db.session.commit()
    group_payload = _serialize_world_group(group)
    socketio.emit('world_group_updated', group_payload, room=f"lobby_{lobby_id}")
    time_payload = (
        _emit_world_time_updates(lobby, updated_characters)
        if time_advanced else None
    )
    return jsonify({
        'group': group_payload,
        'world_turn': _serialize_world_turn(lobby),
        'time_advanced': time_advanced,
        'time': time_payload,
    }), 200


@lobbies_bp.route('/<int:lobby_id>/world-groups/<int:group_id>/anomaly-field', methods=['POST'])
@jwt_required()
@requires_participant
def search_world_anomaly_field(lobby_id, group_id, lobby, participant):
    group = WorldGroup.query.filter_by(id=group_id, lobby_id=lobby_id).first()
    if not group:
        return jsonify({'error': 'World group not found'}), 404
    tile = MapService.get_tile_data(lobby_id, group.tile_x, group.tile_y)
    field = deepcopy(tile.get('anomaly_field')) if isinstance(tile.get('anomaly_field'), dict) else None
    if not field:
        return jsonify({'error': 'There is no anomaly field on this tile'}), 400
    payload = request.get_json(silent=True) or {}
    try:
        character_id = int(payload.get('character_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Choose a group member'}), 400
    member_ids, _ = _eligible_world_group_characters(group)
    if character_id not in member_ids:
        return jsonify({'error': 'The character is not a member of this group'}), 400
    character = LobbyCharacter.query.filter_by(id=character_id, lobby_id=lobby_id).first()
    if not character:
        return jsonify({'error': 'Character not found'}), 404
    user_id = int(participant.user_id)
    if not (
        lobby.gm_id == user_id
        or character.owner_id == user_id
        or user_id in (character.editable_to or [])
    ):
        return jsonify({'error': 'You cannot act for this character'}), 403
    character_data = deepcopy(character.data or {})
    generation = _world_emission_generation(lobby)
    state = _field_loot_state(field, character_data, generation)
    field['loot_state'] = state

    action = str(payload.get('action') or 'inspect').strip().lower()
    recovery = None
    if action == 'recover':
        try:
            artifact_index = int(payload.get('artifact_index'))
            extra_dice = int(payload.get('extra_dice') or 0)
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid artifact recovery parameters'}), 400
        artifacts = state.get('artifacts') if isinstance(state.get('artifacts'), list) else []
        if not 0 <= artifact_index < len(artifacts) or artifacts[artifact_index].get('recovered'):
            return jsonify({'error': 'Artifact is no longer available'}), 409
        survival_bonus = max(0, CombatService._skill_modifier(
            character_data, 'skills.other.survival',
        ))
        if extra_dice < 0 or extra_dice > survival_bonus:
            return jsonify({'error': f'You can add no more than {survival_bonus} dice'}), 400
        agility_bonus = CombatService._skill_modifier(
            character_data, 'skills.physical.agility',
        )
        generator_bonus = _world_character_generator_bonus(character_data)
        base_difficulty = {
            'trash': 5, '1': 10, '2': 15, '3': 20, 'x': 25,
        }[artifacts[artifact_index]['artifact_class']]
        difficulty = max(1, base_difficulty - generator_bonus - agility_bonus)
        rolls = sorted(
            (random.randint(1, 10) for _ in range(2 + extra_dice)), reverse=True,
        )
        total = sum(rolls[:2])
        success = total >= difficulty
        attempts = state.setdefault('attempts_by_character', {})
        attempts[str(character_id)] = int(attempts.get(str(character_id), 0)) + 1
        rank = max(1, min(4, CombatService._coerce_int(field.get('rank'), 1)))
        field_exposures = (
            extra_dice * 0.5
            + (1 if attempts[str(character_id)] % 2 == 0 else 0)
            + (0 if success else 1)
        )
        health = character_data.setdefault('health', {})
        field_damage = 0
        required_protection = 15 * rank
        actual_protection = _anomaly_field_protection(
            character_data, field.get('field_type'),
        )
        if field_exposures:
            field_type = str(field.get('field_type') or '').casefold()
            exposed = actual_protection < required_protection
            if exposed and field_type.startswith('радиоактив'):
                health['radiation'] = max(
                    0, CombatService._coerce_float(health.get('radiation'), 0)
                    + 5 * field_exposures,
                )
            elif exposed and field_type.startswith('псионичес'):
                health['psiState'] = max(
                    0, CombatService._coerce_float(health.get('psiState'), 0)
                    + 5 * field_exposures,
                )
            elif exposed:
                raw_damage = 50 * rank * field_exposures
                field_damage = round(raw_damage)
                health['current'] = max(
                    0, CombatService._coerce_float(health.get('current'), 700) - field_damage,
                )
        if success:
            template = ItemTemplate.query.filter_by(
                category='artifact', name=artifacts[artifact_index]['name'],
            ).first()
            if not template:
                return jsonify({'error': 'Artifact template is missing; run database migrations'}), 409
            inventory = character_data.setdefault('inventory', {})
            inventory.setdefault('pockets', []).append(_artifact_inventory_item(template))
            artifacts[artifact_index]['recovered'] = True
        recovery = {
            'artifact_index': artifact_index,
            'artifact_name': artifacts[artifact_index]['name'],
            'rolls': rolls, 'total': total, 'difficulty': difficulty,
            'success': success, 'extra_dice': extra_dice,
            'field_exposures': field_exposures, 'field_damage': field_damage,
            'required_protection': required_protection,
            'actual_protection': actual_protection,
        }
        character.data = character_data
        flag_modified(character, 'data')
        sync_health_derived_statuses(health)

    MapService.update_tile(
        lobby_id, lobby.gm_id,
        group.tile_x // 32, group.tile_y // 32,
        group.tile_x % 32, group.tile_y % 32,
        {'anomaly_field': field},
    )
    db.session.commit()
    if action == 'recover':
        emit_character_update(
            {'character_id': character.id, 'updates': {'data': character.data_snapshot()},
             'updated_by': participant.user_id},
        )
    return jsonify({
        'field': {
            'name': field.get('name'), 'rank': field.get('rank'),
            'field_type': field.get('field_type'), 'hazard': field.get('hazard'),
            'untouched': state.get('untouched'),
            'artifacts': state.get('artifacts') or [],
            'generation': generation,
        },
        'character': {
            'id': character.id,
            'name': character.name,
            'survival_bonus': CombatService._skill_modifier(
                character_data, 'skills.other.survival',
            ),
            'agility_bonus': CombatService._skill_modifier(
                character_data, 'skills.physical.agility',
            ),
            'generator_bonus': _world_character_generator_bonus(character_data),
        },
        'recovery': recovery,
    }), 200


@lobbies_bp.route('/<int:lobby_id>/world-groups/<int:group_id>', methods=['DELETE'])
@jwt_required()
@requires_gm
def delete_world_group(lobby_id, group_id, lobby):
    group = WorldGroup.query.filter_by(id=group_id, lobby_id=lobby_id).first()
    if not group:
        return jsonify({'error': 'World group not found'}), 404
    db.session.delete(group)
    db.session.commit()
    socketio.emit('world_group_deleted', {'id': group_id}, room=f"lobby_{lobby_id}")
    return '', 204


@lobbies_bp.route('/<int:lobby_id>/world-groups/<int:group_id>/move', methods=['POST'])
@jwt_required()
@requires_participant
def move_world_group(lobby_id, group_id, lobby, participant):
    group = WorldGroup.query.filter_by(id=group_id, lobby_id=lobby_id).first()
    if not group:
        return jsonify({'error': 'World group not found'}), 404
    if not group.turn_active:
        return jsonify({'error': 'This group is not active in the current world turn'}), 409
    if _world_group_submitted_for_current_turn(group, lobby):
        return jsonify({'error': 'This group has already acted in the current world turn'}), 409
    pending = WorldTravelEvent.query.filter_by(group_id=group.id, status='pending').first()
    if pending:
        return jsonify({'error': 'The GM must resolve the pending travel event first'}), 409

    data = request.get_json(silent=True) or {}
    try:
        tile_x = int(data.get('tile_x'))
        tile_y = int(data.get('tile_y'))
    except (TypeError, ValueError):
        return jsonify({'error': 'tile_x and tile_y must be integers'}), 400
    if not (0 <= tile_x < lobby.chunks_width * 32 and 0 <= tile_y < lobby.chunks_height * 32):
        return jsonify({'error': 'World tile is outside the map'}), 400
    movement_profile = _serialize_world_group(group)
    movement_distance = movement_profile['movement_distance']
    requested_distance = max(abs(tile_x - group.tile_x), abs(tile_y - group.tile_y))
    if movement_distance <= 0:
        return jsonify({'error': 'The group cannot move with its current movement penalty'}), 400
    if requested_distance < 1 or requested_distance > movement_distance:
        return jsonify({
            'error': f'A world movement can reach no more than {movement_distance} tiles',
        }), 400

    from_x, from_y = group.tile_x, group.tile_y
    route_tiles = _world_route_tiles(from_x, from_y, tile_x, tile_y)
    route_index = {position: index for index, position in enumerate(route_tiles)}
    placed_event = next(iter(sorted(
        (
            map_event for map_event in WorldMapEvent.query.filter_by(
                lobby_id=lobby_id,
                is_active=True,
            ).all()
            if (map_event.tile_x, map_event.tile_y) in route_index
        ),
        key=lambda map_event: route_index[(map_event.tile_x, map_event.tile_y)],
    )), None)
    actual_tile_x = placed_event.tile_x if placed_event else tile_x
    actual_tile_y = placed_event.tile_y if placed_event else tile_y
    group.tile_x, group.tile_y = actual_tile_x, actual_tile_y
    destination_tile = MapService.get_tile_data(
        lobby_id,
        actual_tile_x,
        actual_tile_y,
    )
    destination_radiation = max(
        0.0,
        CombatService._coerce_float(destination_tile.get('radiation'), 0),
    )
    filter_updates = []
    radiation_updates = []
    radiation_consequences = []
    member_ids, _ = _eligible_world_group_characters(group)
    group_characters = (
        LobbyCharacter.query.filter(
            LobbyCharacter.lobby_id == lobby_id,
            LobbyCharacter.id.in_(member_ids),
        ).all()
        if member_ids else []
    )
    travel_updated_characters = []
    for character in group_characters:
        character_data = deepcopy(character.data or {})
        consequence_result = CombatService._apply_world_radiation_consequences(
            character_data,
        )
        filter_result = consume_equipped_filter_charges(character_data, 1)
        radiation_result = CombatService._apply_incoming_radiation(
            character_data,
            destination_radiation,
            binary=True,
        )
        pain_recovery = CombatService._recover_world_travel_pain(character_data)
        artifact_result = apply_artifact_world_movement(character_data)
        if artifact_result['changed']:
            health = character_data.setdefault('health', {})
            CombatService._apply_radiation_threshold_states(health)
            sync_health_derived_statuses(health)
        radiation_consequences.append({
            "character_id": character.id,
            "character_name": character.name,
            **consequence_result,
        })
        radiation_updates.append({
            "character_id": character.id,
            "character_name": character.name,
            "pain_recovery": pain_recovery,
            "artifact_effects": artifact_result,
            **radiation_result,
        })
        if (
            not consequence_result['applied']
            and not filter_result["changed"]
            and not radiation_result['changed']
            and not pain_recovery['changed']
            and not artifact_result['changed']
        ):
            continue
        character.data = character_data
        flag_modified(character, 'data')
        travel_updated_characters.append(character)
        if filter_result["changed"]:
            filter_updates.append({"character_id": character.id, **filter_result})
    _submit_world_group_turn(group, lobby)
    event = None
    if placed_event:
        event = WorldTravelEvent(
            lobby_id=lobby_id,
            group_id=group.id,
            world_map_event_id=placed_event.id,
            description=f'{placed_event.name}: {placed_event.description}',
            from_tile_x=from_x,
            from_tile_y=from_y,
            to_tile_x=actual_tile_x,
            to_tile_y=actual_tile_y,
        )
        if not placed_event.repeatable:
            placed_event.is_active = False
        db.session.add(event)
    else:
        outcome = roll_travel_event(travel_scenario(lobby_id, actual_tile_x, actual_tile_y))
        if outcome:
            event = WorldTravelEvent(
                lobby_id=lobby_id,
                group_id=group.id,
                description=f'{outcome.name}: {outcome.prompt}',
                from_tile_x=from_x,
                from_tile_y=from_y,
                to_tile_x=actual_tile_x,
                to_tile_y=actual_tile_y,
            )
            db.session.add(event)
    time_advanced, updated_characters = _complete_world_turn_if_ready(lobby)
    db.session.commit()

    group_payload = _serialize_world_group(group)
    all_updated_characters = {
        character.id: character
        for character in [*updated_characters, *travel_updated_characters]
    }
    time_payload = (
        _emit_world_time_updates(lobby, all_updated_characters.values())
        if time_advanced else None
    )
    if not time_advanced:
        for character in travel_updated_characters:
            emit_character_update(
                {
                    'character_id': character.id,
                    'updates': {'data': character.data_snapshot()},
                    'updated_by': participant.user_id,
                },
            )
    socketio.emit('world_group_moved', group_payload, room=f"lobby_{lobby_id}")
    if event:
        socketio.emit(
            'world_travel_event_pending',
            _serialize_world_event(event, reveal_description=False),
            room=f"lobby_{lobby_id}",
        )
    if placed_event:
        socketio.emit(
            'world_map_event_triggered',
            {'id': placed_event.id, 'active': placed_event.is_active},
            room=f"user_{lobby.gm_id}",
        )
    return jsonify({
        'group': group_payload,
        'world_turn': _serialize_world_turn(lobby),
        'time_advanced': time_advanced,
        'time': time_payload,
        'filter_updates': filter_updates,
        'radiation_updates': radiation_updates,
        'radiation_consequences': radiation_consequences,
        'event_pending': event is not None,
        'placed_event_triggered': placed_event is not None,
    }), 200


@lobbies_bp.route('/<int:lobby_id>/world-events/<int:event_id>', methods=['PATCH'])
@jwt_required()
@requires_gm
def resolve_world_travel_event(lobby_id, event_id, lobby):
    event = WorldTravelEvent.query.filter_by(
        id=event_id,
        lobby_id=lobby_id,
        status='pending',
    ).first()
    if not event:
        return jsonify({'error': 'Pending world event not found'}), 404
    decision = str((request.get_json(silent=True) or {}).get('decision') or '').strip().lower()
    if decision not in {'approve', 'reject'}:
        return jsonify({'error': 'decision must be approve or reject'}), 400

    event.status = 'approved' if decision == 'approve' else 'rejected'
    event.resolved_at = datetime.now(timezone.utc)
    event.resolved_by = lobby.gm_id
    if decision == 'approve':
        _emit_lobby_chat_message(
            lobby_id,
            lobby.gm_id,
            f"{event.group.name}: {event.description}",
            username='Событие',
        )
    else:
        db.session.commit()
    payload = {
        'id': event.id,
        'group_id': event.group_id,
        'status': event.status,
    }
    socketio.emit('world_travel_event_resolved', payload, room=f"lobby_{lobby_id}")
    return jsonify(payload), 200

@lobbies_bp.route('/', methods=['POST'])
@jwt_required()
def create_lobby():
    user_id = int(get_jwt_identity())
    if request.content_type and 'multipart/form-data' in request.content_type:
        # Импорт из файла
        name = request.form.get('name')
        map_type = request.form.get('map_type')
        if not name or map_type != 'imported':
            return jsonify({'error': 'Invalid request'}), 400
        file = request.files.get('map_file')
        if not file:
            return jsonify({'error': 'No file uploaded'}), 400

        try:
            content = file.read().decode('utf-8')
            import_data = json.loads(content)
        except Exception:
            return jsonify({'error': 'Invalid JSON file'}), 400

        required_fields = ['lobby_name', 'map_type', 'chunks_width', 'chunks_height', 'chunks']
        if not all(field in import_data for field in required_fields):
            return jsonify({'error': 'Missing fields in import file'}), 400

        lobby = LobbyService.create_lobby(
            user_id=user_id,
            name=name,
            map_type='imported',
            import_data=import_data
        )
    else:
        schema = LobbyCreateSchema()
        data = schema.load(request.get_json())
        lobby = LobbyService.create_lobby(
            user_id=user_id,
            name=data['name'],
            map_type=data['map_type'],
            chunks_width=data['chunks_width'],
            chunks_height=data['chunks_height']
        )

    response_schema = LobbySchema()
    return jsonify(response_schema.dump(lobby)), 201

@lobbies_bp.route('/', methods=['GET'])
@jwt_required()
def list_lobbies():
    lobbies = LobbyService.list_active_lobbies()
    schema = LobbySchema(many=True)
    return jsonify(schema.dump(lobbies)), 200

@lobbies_bp.route('/<int:lobby_id>', methods=['GET'])
@jwt_required()
@requires_participant
def get_lobby(lobby_id, lobby, participant):
    schema = LobbyDetailSchema()
    return jsonify(schema.dump(lobby)), 200


@lobbies_bp.route('/<int:lobby_id>/time', methods=['PATCH'])
@jwt_required()
@requires_gm
def update_lobby_time(lobby_id, lobby):
    data = request.get_json(silent=True) or {}
    try:
        game_day = int(data.get('game_day'))
        game_time_minutes = int(data.get('game_time_minutes'))
    except (TypeError, ValueError):
        return jsonify({'error': 'game_day and game_time_minutes must be integers'}), 400
    if game_day < 1:
        return jsonify({'error': 'game_day must be at least 1'}), 400
    if not 0 <= game_time_minutes < 24 * 60:
        return jsonify({'error': 'game_time_minutes must be between 0 and 1439'}), 400

    previous_absolute_minutes = (lobby.game_day or 1) * 1440 + (lobby.game_time_minutes or 0)
    next_absolute_minutes = game_day * 1440 + game_time_minutes
    elapsed_seconds = max(0, next_absolute_minutes - previous_absolute_minutes) * 60
    lobby.game_day = game_day
    lobby.game_time_minutes = game_time_minutes
    updated_characters = []
    if elapsed_seconds > 0:
        for character in LobbyCharacter.query.filter_by(
            lobby_id=lobby_id,
            time_active=True,
        ).all():
            if not follows_world_time(character):
                continue
            character_data = dict(character.data or {})
            health = character_data.get('health')
            if not isinstance(health, dict):
                continue
            health['effects'] = advance_timed_effects(
                health,
                health.get('effects') or [],
                elapsed_seconds,
            )
            advance_addictions(health, previous_absolute_minutes // 1440, game_day)
            character.data = character_data
            flag_modified(character, 'data')
            updated_characters.append(character)
    db.session.commit()
    payload = {'game_day': game_day, 'game_time_minutes': game_time_minutes}
    socketio.emit('lobby_time_updated', payload, room=f"lobby_{lobby_id}")
    for character in updated_characters:
        emit_character_update(
            {
                'character_id': character.id,
                'updates': {'data': character.data_snapshot()},
                'updated_by': lobby.gm_id,
            },
        )
    return jsonify(payload), 200


def _normalize_character_needs(health):
    needs = health.get('needs') if isinstance(health.get('needs'), dict) else {}
    missed_days = needs.get('missedDays') if isinstance(needs.get('missedDays'), dict) else {}
    streaks = {}
    for key in ('food', 'water', 'sleep'):
        try:
            streaks[key] = max(0, int(missed_days.get(key) or 0))
        except (TypeError, ValueError, OverflowError):
            streaks[key] = 0
    return {
        'day': max(1, int(needs.get('day') or 1)),
        'mealsToday': max(0, min(3, int(needs.get('mealsToday') or 0))),
        'drinksToday': max(0, min(3, int(needs.get('drinksToday') or 0))),
        'sleptToday': needs.get('sleptToday') is True,
        'missedDays': streaks,
        'lastDay': needs.get('lastDay') if isinstance(needs.get('lastDay'), dict) else None,
    }


def _resolve_character_day(health, effects, *, slept):
    needs = _normalize_character_needs(health)
    missed = []
    missed_days = needs['missedDays']
    exhaustion_gain = 0
    for key, label, satisfied in (
        ('food', 'еда', needs['mealsToday'] >= 3),
        ('water', 'вода', needs['drinksToday'] >= 3),
        ('sleep', 'сон', slept or needs['sleptToday']),
    ):
        missed_days[key] = 0 if satisfied else missed_days[key] + 1
        if not satisfied:
            missed.append(label)
            exhaustion_gain += missed_days[key]
    exhaustion = max(0, float(health.get('exhaustion') or 0))
    if slept:
        exhaustion -= math.floor(exhaustion / 2)
    health['exhaustion'] = min(10, exhaustion + exhaustion_gain)
    infection_blocked = any(
        effect.get('type') == 'infection_growth_block' and effect.get('active', True)
        for effect in effects
    )
    if not infection_blocked:
        health['infection'] = min(
            100,
            float(health.get('infection') or 0)
            + float(health.get('infectionGrowthPerDay') or 0),
        )

    survivors = []
    for effect in effects:
        if effect.get('tick') not in {'day_start', 'rest'}:
            survivors.append(effect)
            continue
        for adjustment in effect.get('adjustments') or []:
            if not isinstance(adjustment, dict) or not adjustment.get('field'):
                continue
            field = str(adjustment['field'])
            value = float(health.get(field) or 0) + float(adjustment.get('delta') or 0)
            if adjustment.get('min') is not None:
                value = max(float(adjustment['min']), value)
            if adjustment.get('max') is not None:
                value = min(float(adjustment['max']), value)
            health[field] = value
        if effect.get('remaining') is not None:
            effect['remaining'] = max(0, int(effect.get('remaining') or 0) - 1)
            if effect['remaining'] <= 0:
                continue
        survivors.append(effect)

    health['needs'] = {
        'day': needs['day'] + 1,
        'mealsToday': 0,
        'drinksToday': 0,
        'sleptToday': False,
        'missedDays': missed_days,
        'lastDay': {
            'day': needs['day'],
            'meals': needs['mealsToday'],
            'drinks': needs['drinksToday'],
            'slept': slept or needs['sleptToday'],
            'missed': missed,
        },
    }
    return survivors


def _advance_exoskeleton_battery_day(character_data):
    armor = ((character_data.get('equipment') or {}).get('armor') or {})
    if str(armor.get('name') or '').strip().lower() != 'экзоскелет':
        return
    battery = next((
        module for module in armor.get('installedModules') or []
        if isinstance(module, dict) and module.get('slotType') == 'exoskeleton_battery'
    ), None)
    if not battery:
        armor['powered'] = False
        return
    attributes = battery.setdefault('attributes', {})
    attributes['remaining_days'] = max(0, float(attributes.get('remaining_days') or 0) - 1)
    armor['powered'] = attributes['remaining_days'] > 0


@lobbies_bp.route('/<int:lobby_id>/characters/time-active', methods=['PATCH'])
@jwt_required()
@requires_gm
def update_time_active_characters(lobby_id, lobby):
    data = request.get_json(silent=True) or {}
    try:
        active_ids = {int(value) for value in (data.get('character_ids') or [])}
    except (TypeError, ValueError):
        return jsonify({'error': 'character_ids must contain integers'}), 400
    characters = LobbyCharacter.query.filter_by(lobby_id=lobby_id).all()
    existing_ids = {
        character.id for character in characters if follows_world_time(character)
    }
    if not active_ids.issubset(existing_ids):
        return jsonify({'error': 'Only player characters can be active in world time'}), 400
    for character in characters:
        character.time_active = follows_world_time(character) and character.id in active_ids
    db.session.commit()
    socketio.emit(
        'character_time_activity_updated',
        {'character_ids': sorted(active_ids)},
        room=f"lobby_{lobby_id}",
    )
    return jsonify({'character_ids': sorted(active_ids)}), 200


@lobbies_bp.route('/<int:lobby_id>/rest', methods=['POST'])
@jwt_required()
@requires_gm
def start_lobby_rest(lobby_id, lobby):
    data = request.get_json(silent=True) or {}
    rest_type = str(data.get('type') or '').strip().lower()
    hours = {'rest': 1, 'sleep': 8}.get(rest_type)
    if hours is None:
        return jsonify({'error': 'type must be rest or sleep'}), 400
    try:
        selected_ids = {int(value) for value in (data.get('character_ids') or [])}
    except (TypeError, ValueError):
        return jsonify({'error': 'character_ids must contain integers'}), 400
    if not selected_ids:
        return jsonify({'error': 'Select at least one character'}), 400

    all_characters = LobbyCharacter.query.filter_by(lobby_id=lobby_id).all()
    characters = [
        character for character in all_characters
        if character.time_active and follows_world_time(character)
    ]
    existing_ids = {character.id for character in characters}
    if not selected_ids.issubset(existing_ids):
        return jsonify({'error': 'Rest participants must be active characters'}), 400

    elapsed_seconds = hours * 3600
    previous_day = lobby.game_day or 1
    absolute_minutes = (
        (lobby.game_day or 1) * 1440
        + (lobby.game_time_minutes or 0)
        + hours * 60
    )
    lobby.game_day, lobby.game_time_minutes = divmod(absolute_minutes, 1440)
    summaries = []
    for character in characters:
        character_data = dict(character.data or {})
        health = character_data.get('health')
        if not isinstance(health, dict):
            continue
        selected = character.id in selected_ids
        effects = advance_timed_effects(
            health,
            health.get('effects') or [],
            elapsed_seconds,
            include_turn_effects=selected,
        )
        healed = 0
        if selected:
            rest_bonus = next((
                effect for effect in effects if effect.get('type') == 'next_rest_healing'
            ), None)
            modifiers = ((health.get('combatMeta') or {}).get('consumableModifiers') or [])
            rest_modifier = next((
                modifier for modifier in modifiers
                if isinstance(modifier, dict) and modifier.get('stat') == 'rest_heal_multiplier'
            ), None)
            multiplier = max(
                1,
                float((rest_bonus or {}).get('value') or (rest_modifier or {}).get('value') or 1),
            )
            maximum = float(health.get('max') or 700)
            healed = maximum * (0.5 if rest_type == 'sleep' else 0.05) * multiplier
            apply_effect_to_health(health, {'type': 'heal', 'value': healed})
            effects = [effect for effect in effects if effect.get('type') != 'next_rest_healing']
            if isinstance(health.get('combatMeta'), dict):
                health['combatMeta']['consumableModifiers'] = [
                    modifier for modifier in modifiers
                    if not isinstance(modifier, dict) or modifier.get('stat') != 'rest_heal_multiplier'
                ]
            if rest_type == 'sleep':
                health['intoxication'] = max(0, float(health.get('intoxication') or 0) - 75)
                effects = _resolve_character_day(health, effects, slept=True)
                _advance_exoskeleton_battery_day(character_data)
        health['effects'] = normalize_effect_list(effects)
        advance_addictions(health, previous_day, lobby.game_day)
        sync_health_derived_statuses(health)
        character.data = character_data
        flag_modified(character, 'data')
        if selected:
            summaries.append({'character_id': character.id, 'name': character.name, 'healed': round(healed)})

    db.session.commit()
    time_payload = {
        'game_day': lobby.game_day,
        'game_time_minutes': lobby.game_time_minutes,
    }
    socketio.emit('lobby_time_updated', time_payload, room=f"lobby_{lobby_id}")
    for character in characters:
        emit_character_update(
            {
                'character_id': character.id,
                'updates': {'data': character.data_snapshot()},
                'updated_by': lobby.gm_id,
            },
        )
    return jsonify({
        **time_payload,
        'type': rest_type,
        'hours': hours,
        'characters': summaries,
    }), 200

@lobbies_bp.route('/<int:lobby_id>/join', methods=['POST'])
@jwt_required()
def join_lobby(lobby_id):
    user_id = int(get_jwt_identity())
    ParticipantService.join_lobby(user_id, lobby_id)
    return jsonify({'message': 'Joined lobby'}), 200

@lobbies_bp.route('/<int:lobby_id>/leave', methods=['POST'])
@jwt_required()
def leave_lobby(lobby_id):
    user_id = int(get_jwt_identity())
    ParticipantService.leave_lobby(user_id, lobby_id)
    return jsonify({'message': 'Left lobby'}), 200

@lobbies_bp.route('/<int:lobby_id>', methods=['DELETE'])
@jwt_required()
@requires_gm
def delete_lobby(lobby_id, lobby):
    LobbyService.delete_lobby(lobby_id, lobby.gm_id)  # gm_id берётся из lobby
    return jsonify({'message': 'Lobby deleted'}), 200

@lobbies_bp.route('/<int:lobby_id>/page')
def lobby_page(lobby_id):
    return render_template('lobby.html')

@lobbies_bp.route('/<int:lobby_id>/participants_characters', methods=['GET'])
@jwt_required()
@requires_participant
def get_participants_characters(lobby_id, lobby, participant):
    is_gm = (lobby.gm_id == participant.user_id)

    participants = LobbyParticipant.query.filter_by(
        lobby_id=lobby_id,
        is_banned=False,
    ).all()
    result = []
    for p in participants:
        user_data = {
            'user_id': p.user_id,
            'username': p.user.username,
            'color': p.user.color
        }
        participant_character_id = getattr(p, 'character_id', None)
        if participant_character_id:
            char = db.session.get(LobbyCharacter, participant_character_id)
            if char:
                user_data['character'] = {
                    'id': char.id,
                    'name': char.name,
                    'data': char.data_snapshot()
                }
            else:
                user_data['character'] = None
        else:
            user_data['character'] = None
        result.append(user_data)
    return jsonify(result), 200

@lobbies_bp.route('/<int:lobby_id>/map', methods=['GET'])
@jwt_required()
@requires_participant
def get_map(lobby_id, lobby, participant):
    game_state = GameState.query.filter_by(lobby_id=lobby_id).first()
    if not game_state:
        game_state = GameState(lobby_id=lobby_id)
        db.session.add(game_state)
        db.session.commit()

    schema = GameStateSchema()
    return jsonify(schema.dump(game_state.map_data)), 200

@lobbies_bp.route('/join_by_code', methods=['POST'])
@jwt_required()
def join_by_code():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    code = data.get('code')
    if not code:
        return jsonify({'error': 'Code is required'}), 400
    lobby = LobbyService.join_by_code(user_id, code)
    return jsonify({'message': 'Joined lobby', 'lobby_id': lobby.id}), 200

@lobbies_bp.route('/my', methods=['GET'])
@jwt_required()
def get_my_lobbies():
    user_id = int(get_jwt_identity())
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', default=0, type=int)

    # Валидация
    if limit is not None and (limit <= 0 or limit > 100):
        return jsonify({'error': 'limit must be between 1 and 100'}), 400
    if offset < 0:
        return jsonify({'error': 'offset must be non-negative'}), 400

    lobbies = LobbyService.get_my_lobbies(user_id, limit=limit, offset=offset)
    schema = LobbyMySchema(many=True)
    return jsonify(schema.dump(lobbies)), 200

@lobbies_bp.route('/<int:lobby_id>/ban/<int:user_id>', methods=['POST'])
@jwt_required()
@requires_gm
def ban_participant(lobby_id, lobby, user_id):
    ParticipantService.ban_user(lobby.gm_id, lobby_id, user_id)
    return jsonify({'message': 'User banned'}), 200

@lobbies_bp.route('/<int:lobby_id>/banned', methods=['GET'])
@jwt_required()
@requires_gm
def get_banned_participants(lobby_id, lobby):
    banned = ParticipantService.get_banned_list(lobby.gm_id, lobby_id)
    schema = BannedUserSchema(many=True)
    return jsonify(schema.dump(banned)), 200

@lobbies_bp.route('/<int:lobby_id>/unban/<int:user_id>', methods=['POST'])
@jwt_required()
@requires_gm
def unban_participant(lobby_id, lobby, user_id):
    ParticipantService.unban_user(lobby.gm_id, lobby_id, user_id)
    return jsonify({'message': 'User unbanned'}), 200

@lobbies_bp.route('/<int:lobby_id>/characters', methods=['GET'])
@jwt_required()
@requires_participant
def get_lobby_characters(lobby_id, lobby, participant):
    characters = CharacterService.get_lobby_characters(lobby_id, participant.user_id)
    schema = CharacterSchema(many=True)
    return jsonify(schema.dump(characters)), 200

@lobbies_bp.route('/<int:lobby_id>/characters', methods=['POST'])
@jwt_required()
@requires_participant
def create_lobby_character(lobby_id, lobby, participant):
    schema = CharacterCreateSchema()
    data = schema.load(request.get_json())
    character = CharacterService.create_character(
        lobby_id=lobby_id,
        owner_id=participant.user_id,
        name=data['name'],
        data=data.get('data', {})
    )

    socketio.emit('character_created', {
        'id': character.id,
        'name': character.name,
        'owner_id': character.owner_id,
        'owner_username': character.owner.username if character.owner else None,
        'data': character.data_snapshot()
    }, room=f"lobby_{lobby_id}")

    response_schema = CharacterSchema()
    return jsonify(response_schema.dump(character)), 201

@lobbies_bp.route('/characters/<int:character_id>', methods=['GET'])
@jwt_required()
def get_character(character_id):
    user_id = int(get_jwt_identity())
    character = CharacterService.get_character(character_id, user_id)
    schema = CharacterSchema()
    payload = schema.dump(character)
    lobby = db.session.get(Lobby, character.lobby_id)
    is_controller = LocationCharacter.query.filter_by(
        character_id=character.id,
        controlled_by=user_id,
    ).first() is not None
    payload['can_edit'] = bool(
        character.owner_id == user_id
        or (lobby and lobby.gm_id == user_id)
        or user_id in (character.editable_to or [])
        or is_controller
    )
    return jsonify(payload), 200

@lobbies_bp.route('/characters/<int:character_id>', methods=['PUT'])
@jwt_required()
def update_character(character_id):
    user_id = int(get_jwt_identity())
    data = request.get_json()
    result = CharacterService.update_character(character_id, user_id, data)
    publish_character_save(result, data, user_id)
    return jsonify({'message': 'Character updated', 'data': result.character.data_snapshot()}), 200


@lobbies_bp.route('/characters/<int:character_id>/equipment-action', methods=['POST'])
@jwt_required()
def change_character_equipment_outside_combat(character_id):
    user_id = int(get_jwt_identity())
    character = db.session.get(LobbyCharacter, character_id)
    if not character:
        return jsonify({'error': 'Character not found'}), 404
    if not _character_edit_allowed(character, user_id):
        return jsonify({'error': 'You do not control this character'}), 403
    active_location_character = (
        LocationCharacter.query
        .join(LocationCombatState, LocationCombatState.location_id == LocationCharacter.location_id)
        .filter(
            LocationCharacter.character_id == character_id,
            LocationCombatState.status == 'active',
        )
        .first()
    )
    if active_location_character:
        return jsonify({'error': 'Use the combat equipment action during combat'}), 409

    payload = request.get_json(silent=True) or {}
    character_data = deepcopy(character.data or {})
    try:
        details = CombatService.equipment_action_details(
            character_data,
            payload.get('operation'),
            payload.get('slot'),
            item_path=payload.get('item_path'),
            retrieval_action_points=0,
            in_combat=False,
        )
        result = CombatService.apply_equipment_action(character_data, details)
    except (ValidationError, NotFoundError) as error:
        return jsonify({'error': str(error)}), 400

    health = apply_health_maximums(character_data)
    character.data = character_data
    flag_modified(character, 'data')
    for loc_char in LocationCharacter.query.filter_by(character_id=character.id).all():
        loc_char.hp_zones = health_zones_to_location(health)
    db.session.commit()
    emit_character_update(
        {
            'character_id': character.id,
            'updates': {'data': character.data_snapshot()},
            'source': 'equipment_action',
        },
    )
    lobby = db.session.get(Lobby, character.lobby_id)
    if lobby:
        for location_id in {
            row.location_id for row in LocationCharacter.query.filter_by(character_id=character.id).all()
        }:
            socketio.emit(
                'combat_state_updated',
                CombatService.get_state(location_id, lobby.gm_id),
                room=f"location_{location_id}",
            )
    return jsonify({'equipment_change': result, 'data': character.data_snapshot()}), 200


@lobbies_bp.route('/characters/<int:character_id>/equipment-module', methods=['POST'])
@jwt_required()
def change_character_equipment_module(character_id):
    from app.services.equipment_module import change_outside_combat

    user_id = int(get_jwt_identity())
    character, result = change_outside_combat(
        character_id,
        user_id,
        request.get_json(silent=True) or {},
    )
    emit_character_update({
        'character_id': character.id,
        'updates': {'data': character.data_snapshot()},
        'source': 'equipment_module',
    })
    return jsonify({
        'module_change': result,
        'data': character.data_snapshot(),
    }), 200


def _character_edit_allowed(character, user_id):
    lobby = db.session.get(Lobby, character.lobby_id)
    return bool(
        character.owner_id == user_id
        or (lobby and lobby.gm_id == user_id)
        or user_id in (character.editable_to or [])
        or LocationCharacter.query.filter_by(
            character_id=character.id, controlled_by=user_id,
        ).first() is not None
    )


@lobbies_bp.route('/characters/<int:character_id>/repair-equipment', methods=['POST'])
@jwt_required()
def repair_character_equipment(character_id):
    user_id = int(get_jwt_identity())
    character = CharacterService.get_character(character_id, user_id)
    if not _character_edit_allowed(character, user_id):
        return jsonify({'error': 'Character cannot be edited'}), 403
    lobby = db.session.get(Lobby, character.lobby_id)
    location_ids = [
        entry.location_id
        for entry in LocationCharacter.query.filter_by(character_id=character.id).all()
    ]
    if location_ids and LocationCombatState.query.filter(
        LocationCombatState.location_id.in_(location_ids),
        LocationCombatState.status == 'active',
    ).first():
        return jsonify({'error': 'Ремонт снаряжения доступен только вне боя'}), 400

    payload = request.get_json(silent=True) or {}
    tool_path = payload.get('tool_path')
    target_path = payload.get('target_path')
    if not isinstance(tool_path, list) or not isinstance(target_path, list):
        return jsonify({'error': 'Нужно выбрать инструменты и предмет для ремонта'}), 400
    character_data = deepcopy(character.data or {})
    try:
        tool = resolve_item_path(character_data, tool_path)
        target = resolve_item_path(character_data, target_path)
        template_ids = {
            int(value) for value in (tool.get('templateId'), target.get('templateId'))
            if str(value or '').isdigit()
        }
        templates = {
            item.id: item for item in ItemTemplate.query.filter(ItemTemplate.id.in_(template_ids)).all()
        } if template_ids else {}
        result = repair_equipment(
            character_data, tool_path, target_path,
            tool_template=templates.get(int(tool.get('templateId'))) if str(tool.get('templateId') or '').isdigit() else None,
            target_template=templates.get(int(target.get('templateId'))) if str(target.get('templateId') or '').isdigit() else None,
        )
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    character.data = character_data
    flag_modified(character, 'data')
    db.session.commit()
    emit_character_update(
        {
            'character_id': character.id,
            'updates': {'data': character.data_snapshot()},
            'updated_by': user_id,
        },
    )
    _emit_lobby_chat_message(
        lobby.id,
        user_id,
        (
            f"{character.name}: ремонт «{result['target_name']}» набором "
            f"«{result['tool_name']}». Длительность действия: {result['duration_minutes']} мин. "
            "Глобальное время не изменено."
        ),
        username='Система',
    )
    return jsonify({
        'message': 'Снаряжение отремонтировано',
        'result': result,
        'character_data': character.data_snapshot(),
        'time_advanced': False,
    }), 200


@lobbies_bp.route('/characters/<int:character_id>/addictions/exposure', methods=['POST'])
@jwt_required()
def register_character_addiction_exposure(character_id):
    user_id = int(get_jwt_identity())
    character = CharacterService.get_character(character_id, user_id)
    if not _character_edit_allowed(character, user_id):
        return jsonify({'error': 'Character cannot be edited'}), 403
    lobby = db.session.get(Lobby, character.lobby_id)
    data = request.get_json(silent=True) or {}
    character_data = dict(character.data or {})
    health = character_data.setdefault('health', {})
    absolute_minute = (lobby.game_day or 1) * 1440 + (lobby.game_time_minutes or 0)
    result = record_exposure(
        health,
        data.get('item_name'),
        data.get('price'),
        absolute_minute,
        intoxication=data.get('intoxication', 0),
        exhaustion_relief=data.get('exhaustion_relief', 0),
        addiction_block_hours=data.get('addiction_block_hours', 0),
    )
    character.data = character_data
    flag_modified(character, 'data')
    db.session.commit()
    withdrawal_effects = [
        effect for effect in normalize_effect_list(health.get('effects') or [])
        if effect.get('type') == 'addiction_withdrawal'
    ]
    return jsonify({
        'result': result,
        'addictions': health.get('addictions', {}),
        'withdrawal_effects': withdrawal_effects,
    }), 200


@lobbies_bp.route('/characters/<int:character_id>/addictions/<addiction_key>/check', methods=['POST'])
@jwt_required()
def check_character_addiction_withdrawal(character_id, addiction_key):
    user_id = int(get_jwt_identity())
    character = CharacterService.get_character(character_id, user_id)
    if not _character_edit_allowed(character, user_id):
        return jsonify({'error': 'Character cannot be edited'}), 403
    lobby = db.session.get(Lobby, character.lobby_id)
    character_data = dict(character.data or {})
    health = character_data.setdefault('health', {})
    will_bonus = CombatService._skill_modifier(
        character_data, 'skills.physical.will', include_pain=False,
    )
    difficulty_reduction = 0
    for effect in normalize_effect_list(health.get('effects') or []):
        if effect.get('active', True):
            difficulty_reduction = max(
                difficulty_reduction,
                int(effect.get('withdrawal_check_difficulty_reduction') or 0),
            )
    try:
        result = withdrawal_check(
            health, addiction_key, lobby.game_day or 1, will_bonus,
            difficulty_reduction=difficulty_reduction,
        )
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    character.data = character_data
    flag_modified(character, 'data')
    db.session.commit()
    emit_character_update(
        {
            'character_id': character.id,
            'updates': {'data': character.data_snapshot()},
            'source': 'withdrawal_check',
        },
    )
    _emit_lobby_chat_message(
        character.lobby_id,
        user_id,
        f"{character.name}: проверка ломки ({result['record']['label']}) — "
        f"d20 {result['roll']} против СЛ {result['difficulty']}: "
        f"{'успех' if result['success'] else 'провал'}.",
        username='Зависимость',
    )
    return jsonify({'result': result, 'data': character.data_snapshot()}), 200


@lobbies_bp.route('/<int:lobby_id>/characters/<int:character_id>/addictions/<addiction_key>', methods=['DELETE'])
@jwt_required()
@requires_gm
def remove_character_addiction(lobby_id, character_id, addiction_key, lobby):
    character = LobbyCharacter.query.filter_by(id=character_id, lobby_id=lobby_id).first()
    if not character:
        raise NotFoundError('Персонаж не найден')
    character_data = deepcopy(character.data or {})
    try:
        remove_addiction(character_data.setdefault('health', {}), addiction_key)
    except ValueError as error:
        return jsonify({'error': str(error)}), 404
    character.data = character_data
    flag_modified(character, 'data')
    db.session.commit()
    emit_character_update({
        'character_id': character.id,
        'updates': {'data': character.data_snapshot()},
        'source': 'gm_remove_addiction',
    })
    return jsonify({'data': character.data_snapshot()}), 200

@lobbies_bp.route('/characters/<int:character_id>', methods=['DELETE'])
@jwt_required()
def delete_character(character_id):
    user_id = int(get_jwt_identity())
    character = CharacterService.get_character(character_id, user_id)
    lobby_id = character.lobby_id
    placements = LocationCharacter.query.filter_by(character_id=character_id).all()
    placements_by_location = {}
    for placement in placements:
        placements_by_location.setdefault(placement.location_id, set()).add(placement.id)

    combat_states = {}
    for location_id, removed_ids in placements_by_location.items():
        combat_state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not combat_state:
            continue
        old_order = list(dict.fromkeys(combat_state.turn_order or []))
        removed_current = combat_state.current_location_character_id in removed_ids
        current_index = (
            old_order.index(combat_state.current_location_character_id)
            if removed_current and combat_state.current_location_character_id in old_order
            else 0
        )
        new_order = [item_id for item_id in old_order if item_id not in removed_ids]
        combat_state.turn_order = new_order
        if combat_state.reaction_pending_location_character_id in removed_ids:
            combat_state.reaction_pending_location_character_id = None
        if combat_state.reaction_return_location_character_id in removed_ids:
            combat_state.reaction_return_location_character_id = None
        if not new_order:
            combat_state.current_location_character_id = None
            combat_state.turn_index = 0
            if combat_state.status == 'active':
                combat_state.status = 'idle'
        elif removed_current:
            next_index = min(current_index, len(new_order) - 1)
            combat_state.current_location_character_id = new_order[next_index]
            combat_state.turn_index = next_index
            if combat_state.status == 'active':
                next_character = db.session.get(
                    LocationCharacter,
                    combat_state.current_location_character_id,
                )
                if next_character:
                    CombatService._prepare_character_for_turn(next_character)
        elif combat_state.current_location_character_id in new_order:
            combat_state.turn_index = new_order.index(
                combat_state.current_location_character_id
            )
        combat_states[location_id] = combat_state

    # Clear combat-state foreign keys before deleting the location entries.
    if combat_states:
        db.session.commit()
    CharacterService.delete_character(character_id, user_id)
    socketio.emit('character_deleted', {'id': character_id}, room=f"lobby_{lobby_id}")
    for location_id in placements_by_location:
        socketio.emit(
            'location_character_removed',
            {
                'location_id': location_id,
                'character_id': character_id,
            },
            room=f"location_{location_id}",
        )
        combat_state = combat_states.get(location_id)
        if combat_state:
            location = db.session.get(Location, location_id)
            socketio.emit(
                'combat_state_updated',
                CombatService._serialize_state(location, combat_state),
                room=f"location_{location_id}",
            )
    return jsonify({'message': 'Character deleted'}), 200

@lobbies_bp.route('/characters/<int:character_id>/visibility', methods=['PUT'])
@jwt_required()
def set_character_visibility(character_id):
    user_id = int(get_jwt_identity())
    data = request.get_json()
    if 'visible_to' not in data or not isinstance(data['visible_to'], list):
        return jsonify({'error': 'visible_to must be a list'}), 400
    editable_to = data.get('editable_to', [])
    if not isinstance(editable_to, list):
        return jsonify({'error': 'editable_to must be a list'}), 400
    character = CharacterService.set_visibility(
        character_id, user_id, data['visible_to'], editable_to
    )
    socketio.emit('character_updated', {
        'id': character.id,
        'visible_to': character.visible_to,
        'editable_to': character.editable_to,
    }, room=f"lobby_{character.lobby_id}")
    return jsonify({'message': 'Visibility updated'}), 200

@lobbies_bp.route('/<int:lobby_id>/chunks/<int:chunk_x>/<int:chunk_y>/tile/<int:tile_x>/<int:tile_y>', methods=['PATCH'])
@jwt_required()
@requires_gm
@record_world_editor_command
def update_tile(lobby_id, lobby, chunk_x, chunk_y, tile_x, tile_y):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    schema = TileUpdateSchema()
    updates = schema.load(data)
    MapService.update_tile(lobby_id, lobby.gm_id, chunk_x, chunk_y, tile_x, tile_y, updates)
    allowed_fields = ['terrain', 'height', 'objects', 'name', 'radiation', 'anomaly_field', 'water_profile']
    safe_updates = {k: v for k, v in updates.items() if k in allowed_fields}
    socketio.emit('tile_updated', {
        'chunk_x': chunk_x,
        'chunk_y': chunk_y,
        'tile_x': tile_x,
        'tile_y': tile_y,
        'updates': safe_updates
    }, room=f"lobby_{lobby_id}")
    return jsonify({'message': 'Tile updated'}), 200

@lobbies_bp.route('/<int:lobby_id>/chunks', methods=['GET'])
@jwt_required()
@requires_participant
def get_chunks(lobby_id, lobby, participant):
    min_x = request.args.get('min_chunk_x', type=int)
    max_x = request.args.get('max_chunk_x', type=int)
    min_y = request.args.get('min_chunk_y', type=int)
    max_y = request.args.get('max_chunk_y', type=int)
    if None in (min_x, max_x, min_y, max_y):
        return jsonify({'error': 'Missing bounds'}), 400
    chunks = MapService.get_chunks(lobby_id, participant.user_id, (min_x, max_x, min_y, max_y))
    schema = MapChunkSchema(many=True)
    return jsonify(schema.dump(chunks)), 200

@lobbies_bp.route('/<int:lobby_id>/water-profile', methods=['POST'])
@jwt_required()
@requires_gm
@record_world_editor_command
def set_connected_water_profile(lobby_id, lobby):
    from app.services.water_profiles import connected_world_water_updates
    data = request.get_json(silent=True) or {}
    updates = connected_world_water_updates(lobby, data.get('x'), data.get('z'), data.get('profile'))
    if updates:
        MapService.batch_update_tiles(lobby_id, lobby.gm_id, updates)
        socketio.emit('tiles_updated', updates, room=f"lobby_{lobby_id}")
    return jsonify({'updates': updates}), 200


@lobbies_bp.route('/<int:lobby_id>/chunks/batch', methods=['POST'])
@jwt_required()
@requires_gm
@record_world_editor_command
def batch_update_tiles(lobby_id, lobby):
    data = request.get_json()
    if not data or not isinstance(data, list):
        return jsonify({'error': 'Expected a list of updates'}), 400

    MapService.batch_update_tiles(lobby_id, lobby.gm_id, data)
    socketio.emit('tiles_updated', [
        {key: value for key, value in item.items() if key not in {'expected_objects', 'expected_height', 'expected_terrain'}} for item in data
    ], room=f"lobby_{lobby_id}")
    return jsonify({'message': 'Tiles updated successfully'}), 200

@lobbies_bp.route('/<int:lobby_id>/world-roads/preview', methods=['POST'])
@jwt_required()
@requires_gm
def preview_world_road(lobby_id, lobby):
    from app.services.world_road_editor import preview_world_road as preview
    return jsonify(preview(lobby_id, lobby.gm_id, request.get_json(silent=True))), 200


@lobbies_bp.route('/<int:lobby_id>/world-roads/build', methods=['POST'])
@jwt_required()
@requires_gm
@record_world_editor_command
def build_world_road(lobby_id, lobby):
    from app.services.world_road_editor import build_world_road as build
    result = build(lobby_id, lobby.gm_id, request.get_json(silent=True))
    socketio.emit('tiles_updated', result['updates'], room=f"lobby_{lobby_id}")
    return jsonify(result), 200


@lobbies_bp.route('/<int:lobby_id>/world-roads/connect', methods=['POST'])
@jwt_required()
@requires_gm
@record_world_editor_command
def connect_world_roads(lobby_id, lobby):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('Expected road coordinates')
    updates = MapService.connect_roads(lobby_id, lobby.gm_id, data.get('first'), data.get('second'))
    socketio.emit('tiles_updated', updates, room=f"lobby_{lobby_id}")
    return jsonify({'updates': updates}), 200


@lobbies_bp.route('/<int:lobby_id>/world-objects/edit', methods=['POST'])
@jwt_required()
@requires_gm
@record_world_editor_command
def edit_world_object(lobby_id, lobby):
    result = MapService.edit_world_object(
        lobby_id, lobby.gm_id, request.get_json(silent=True),
    )
    socketio.emit('tiles_updated', result['updates'], room=f"lobby_{lobby_id}")
    return jsonify(result), 200

@lobbies_bp.route('/<int:lobby_id>/world-editor/changes', methods=['POST'])
@jwt_required()
@requires_gm
def apply_world_editor_changes(lobby_id, lobby):
    result = apply_world_editor_command(lobby, request.get_json(silent=True))
    socketio.emit('tiles_updated', result['updates'], room=f'lobby_{lobby_id}')
    return jsonify(result), 200


@lobbies_bp.route('/<int:lobby_id>/export', methods=['GET'])
@jwt_required()
@requires_gm
def export_lobby(lobby_id, lobby):
    export_data = MapService.export_map(lobby_id, lobby.gm_id)
    json_str = json.dumps(export_data, ensure_ascii=False, separators=(',', ':'))
    json_bytes = json_str.encode('utf-8')
    gzip_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=gzip_buffer, mode='wb') as f:
        f.write(json_bytes)
    gzip_buffer.seek(0)
    return send_file(
        gzip_buffer,
        as_attachment=True,
        download_name=f'lobby_{lobby_id}_map.json.gz',
        mimetype='application/gzip'
    )

@lobbies_bp.route('/<int:lobby_id>/weather', methods=['PATCH'])
@jwt_required()
@requires_gm
def update_weather(lobby_id, lobby):
    data = request.get_json()
    previous = lobby.weather_settings if isinstance(lobby.weather_settings, dict) else {}
    previous_emission = bool((previous.get('emission') or {}).get('enabled'))
    next_emission = bool((data.get('emission') or {}).get('enabled'))
    generation = max(0, int(previous.get('emission_generation') or 0))
    if next_emission and not previous_emission:
        generation += 1
    data['emission_generation'] = generation
    lobby.weather_settings = data
    db.session.commit()

    socketio.emit('weather_updated', data, room=f"lobby_{lobby_id}")
    return jsonify({'message': 'Weather updated'}), 200

@lobbies_bp.route('/joined', methods=['GET'])
@jwt_required()
def get_joined_lobbies():
    user_id = int(get_jwt_identity())
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', default=0, type=int)

    if limit is not None and (limit <= 0 or limit > 100):
        return jsonify({'error': 'limit must be between 1 and 100'}), 400
    if offset < 0:
        return jsonify({'error': 'offset must be non-negative'}), 400

    lobbies = LobbyService.get_joined_lobbies(user_id, limit=limit, offset=offset)
    from app.schemas.lobby import LobbyMySchema
    schema = LobbyMySchema(many=True)
    return jsonify(schema.dump(lobbies)), 200


@lobbies_bp.route('/<int:lobby_id>/templates', methods=['GET'])
@jwt_required()
@requires_participant
def get_lobby_templates(lobby_id, lobby, participant):
    """Возвращает объединённый список глобальных и локальных шаблонов с фильтрацией по категории."""
    category = request.args.get('category')
    subcategory = request.args.get('subcategory')

    query_global = ItemTemplate.query
    query_local = LobbyItemTemplate.query.filter_by(lobby_id=lobby_id)

    if category:
        query_global = query_global.filter_by(category=category)
        query_local = query_local.filter_by(category=category)
    if subcategory:
        query_global = query_global.filter_by(subcategory=subcategory)
        query_local = query_local.filter_by(subcategory=subcategory)

    global_templates = query_global.all()
    local_templates = query_local.all()

    global_schema = ItemTemplateSchema(many=True)
    local_schema = LobbyItemTemplateSchema(many=True)

    serialized_global=global_schema.dump(global_templates)
    from app.services.character_creation import builtin_templates
    known={str((t.get('attributes') or {}).get('ruleId')) for t in serialized_global}
    serialized_global.extend(t for t in builtin_templates(category) if t['attributes']['ruleId'] not in known and not subcategory)
    if category in (None,'weapon_module'):
        from app.services.weapon_modules import missing_templates
        serialized_global.extend(missing_templates(serialized_global,subcategory))
    return jsonify({
        'global': serialized_global,
        'local': local_schema.dump(local_templates)
    })


@lobbies_bp.route('/<int:lobby_id>/character-creation', methods=['GET','POST'])
@jwt_required()
@requires_participant
def character_creation_rules(lobby_id,lobby,participant):
    from app.services.character_creation import catalog,build_character
    if request.method=='GET':
        from app.services.character_starting_purchase import catalog as purchase_catalog
        rules=catalog();rules['startingPurchaseTemplates']=purchase_catalog()
        return jsonify(rules)
    payload=request.get_json() or {}
    data=build_character(str(payload.get('name') or ''),payload.get('plan'),roll_history=False,finalize=False)
    return jsonify({'data':data})


@lobbies_bp.route('/<int:lobby_id>/characters/<int:character_id>/session-reward',methods=['POST'])
@jwt_required()
@requires_gm
def award_character_session(lobby_id,character_id,lobby):
    from app.services.character_development import award_session
    character=LobbyCharacter.query.filter_by(id=character_id,lobby_id=lobby_id).first()
    if not character:raise NotFoundError('Персонаж не найден')
    data=deepcopy(character.data or {})
    result=award_session(data,request.get_json() or {})
    character.data=data
    flag_modified(character,'data')
    db.session.commit()
    emit_character_update({'character_id':character.id,'updates':{'data':character.data_snapshot()},'source':'session_reward'})
    return jsonify({'result':result,'data':character.data_snapshot()})


@lobbies_bp.route('/<int:lobby_id>/characters/<int:character_id>/creation-history',methods=['POST'])
@jwt_required()
@requires_participant
def settle_character_creation_history(lobby_id,character_id,lobby,participant):
    from app.services.character_history import settle_history
    character=LobbyCharacter.query.filter_by(id=character_id,lobby_id=lobby_id).first()
    if not character:raise NotFoundError('Персонаж не найден')
    CharacterService.check_access(character,int(get_jwt_identity()),edit=True)
    payload=request.get_json() or {}
    if not isinstance(payload,dict) or set(payload)!={'choices'}:raise ValidationError('Нужны решения событий')
    character.data=settle_history(character.data,payload['choices'],max(1,lobby.game_day or 1))
    flag_modified(character,'data');db.session.commit()
    emit_character_update({'character_id':character.id,'updates':{'data':character.data_snapshot()},'source':'creation_history'})
    return jsonify({'data':character.data_snapshot()})


@lobbies_bp.route('/<int:lobby_id>/templates', methods=['POST'])
@jwt_required()
@requires_gm
def create_lobby_template(lobby_id, lobby):
    """Создаёт новый кастомный шаблон в комнате."""
    data = request.get_json()
    schema = LobbyItemTemplateSchema()
    validated_data = schema.load(data)
    validate_custom_consumable(validated_data.get('category'), validated_data.get('attributes'))
    template = LobbyItemTemplate(
        lobby_id=lobby_id,
        created_by=int(get_jwt_identity()),
        **validated_data
    )
    db.session.add(template)
    db.session.commit()
    return jsonify(schema.dump(template)), 201


@lobbies_bp.route('/<int:lobby_id>/templates/<int:template_id>', methods=['PUT'])
@jwt_required()
@requires_gm
def update_lobby_template(lobby_id, lobby, template_id):
    template = LobbyItemTemplate.query.filter_by(id=template_id, lobby_id=lobby_id).first_or_404()
    data = request.get_json()
    schema = LobbyItemTemplateSchema(partial=True)
    validated_data = schema.load(data)
    validate_custom_consumable(
        validated_data.get('category', template.category),
        validated_data.get('attributes', template.attributes),
    )
    for key, value in validated_data.items():
        setattr(template, key, value)
    db.session.commit()
    return jsonify(schema.dump(template))


@lobbies_bp.route('/<int:lobby_id>/templates/<int:template_id>', methods=['DELETE'])
@jwt_required()
@requires_gm
def delete_lobby_template(lobby_id, lobby, template_id):
    template = LobbyItemTemplate.query.filter_by(id=template_id, lobby_id=lobby_id).first_or_404()
    db.session.delete(template)
    db.session.commit()
    return '', 204

# ========== L O C A T I O N S   E N D P O I N T S ==========

@lobbies_bp.route('/<int:lobby_id>/locations', methods=['POST'])
@jwt_required()
@requires_gm
def create_location(lobby_id, lobby):
    schema = LocationCreateSchema()
    data = schema.load(request.get_json())
    freeze_water_offsets(data['tiles_data'])
    location = Location(
        lobby_id=lobby_id,
        name=data['name'],
        description=data['description'],
        type=data['type'],
        grid_width=data['grid_width'],
        grid_height=data['grid_height'],
        tiles_data=data['tiles_data'],
        lighting=normalize_location_lighting(data['lighting'], data['grid_width'], data['grid_height']),
        world_tile_x=data['world_tile_x'],
        world_tile_z=data['world_tile_z'],
        world_radius=data['world_radius'],
        spawn_points=data['spawn_points']
    )
    db.session.add(location)
    db.session.commit()

    socketio.emit('location_created', {
        'lobby_id': lobby_id,
        'location': {
            'id': location.id,
            'name': location.name,
            'type': location.type,
            'world_tile_x': location.world_tile_x,
            'world_tile_z': location.world_tile_z,
            'world_radius': location.world_radius
        }
    }, room=f"lobby_{lobby_id}")

    return jsonify({'id': location.id, 'message': 'Location created'}), 201


@lobbies_bp.route('/<int:lobby_id>/locations', methods=['GET'])
@jwt_required()
@requires_participant
def get_locations(lobby_id, lobby, participant):
    locations = Location.query.filter_by(lobby_id=lobby_id).all()
    result = [{
        'id': loc.id,
        'name': loc.name,
        'type': loc.type,
        'world_tile_x': loc.world_tile_x,
        'world_tile_z': loc.world_tile_z,
        'world_radius': loc.world_radius
    } for loc in locations]
    return jsonify(result), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>', methods=['GET'])
@jwt_required()
@requires_participant
def get_location_detail(lobby_id, location_id, lobby, participant):
    location = Location.query.get_or_404(location_id)
    if location.lobby_id != lobby_id:
        return jsonify({'error': 'Access denied'}), 403

    if freeze_water_offsets(location.tiles_data):
        flag_modified(location, 'tiles_data')
        db.session.commit()

    objects = LocationObject.query.filter_by(location_id=location.id).all()
    obj_schema = LocationObjectSchema(many=True)

    return jsonify({
        'id': location.id,
        'name': location.name,
        'description': location.description,
        'type': location.type,
        'world_tile_x': location.world_tile_x,
        'world_tile_z': location.world_tile_z,
        'grid_width': location.grid_width,
        'grid_height': location.grid_height,
        'tiles_data': location.tiles_data,
        'lighting': location.lighting,
        'spawn_points': location.spawn_points,
        'objects': obj_schema.dump(objects)
    }), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/movement-preview', methods=['POST'])
@jwt_required()
@requires_participant
def preview_location_movement(lobby_id, location_id, lobby, participant):
    location = Location.query.get_or_404(location_id)
    if location.lobby_id != lobby_id:
        return jsonify({'error': 'Access denied'}), 403
    payload = request.get_json() or {}
    actor = LocationCharacter.query.filter_by(location_id=location_id, character_id=payload.get('character_id')).first_or_404()
    user_id = int(get_jwt_identity())
    if lobby.gm_id != user_id and actor.controlled_by not in (None, user_id):
        return jsonify({'error': 'Access denied'}), 403
    if payload.get('special_action') == 'jump':
        from app.services.character_jump import prepare_jump, animation_path
        plan = prepare_jump(location,actor,payload.get('x'),payload.get('y'))
        points = animation_path(plan['start'],plan['landing'])
        return jsonify({'route':dict(surfaces=points,path=[[p['x'],p['y']] for p in points],
            distance=plan['distance'],cost=0,climbs=[],climb_cost=0,floor_level=plan['landing']['level']),
            'jump':plan['profile']}),200
    if payload.get('special_action') in ('climb','drop_down'):
        if type(payload.get('object_id')) is not int:
            return jsonify({'error':'Некорректное препятствие'}),400
        obj = LocationObject.query.filter_by(location_id=location_id,id=payload.get('object_id')).first()
        if not obj or (obj.properties or {}).get('gm_hidden') and lobby.gm_id!=user_id:
            return jsonify({'error':'Препятствие недоступно'}),404
        plan = (CombatService._object_drop_plan(location,actor,obj) if payload.get('special_action')=='drop_down'
                else CombatService._object_climb_plan(location,actor,obj))
        if plan:
            plan['climb_agility_bonus']=CombatService._climb_payment_profile(actor.character.data,plan['climbs'])['agility_bonus']
        return jsonify({'route':plan}),200
    x, y, level = payload.get('x'), payload.get('y'), payload.get('floor_level', actor.floor_level)
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (x,y)) or type(level) is not int or not (0<=x<=location.grid_width-1 and 0<=y<=location.grid_height-1):
        return jsonify({'error': 'Invalid position'}), 400
    movement_mode = payload.get('movement_mode','walk')
    if movement_mode is None:
        movement_mode = 'walk'
    if not isinstance(movement_mode,str) or movement_mode not in {'walk','run','sprint','backward_sideways','correction'}:
        return jsonify({'error':'Неизвестный режим перемещения'}),400
    state = LocationCombatState.query.filter_by(location_id=location_id).first()
    path = CombatService._find_movement_path(location, actor.pos_x, actor.pos_y, x, y, actor.character_id,
        end_level=level,movement_mode=movement_mode if state and state.status=='active' else None)
    if path is None:
        path = CombatService._vertical_movement_plan(location,actor,x,y,level)
    if path:
        path['climb_agility_bonus']=CombatService._climb_payment_profile(actor.character.data,[])['agility_bonus']
        # Разбиение нужно для серверного исполнения, предпросмотру достаточно цены и флага льда.
        path.pop('movement_segments', None)
    result = {'route':path}
    if path is None and state and state.status=='active':
        forward,backward = CombatService._movement_direction_profile(actor,{'path':[(actor.pos_x,actor.pos_y),(x,y)]})
        if backward and movement_mode in {'run','sprint'}:
            result['reason']='Для бега в этом направлении сначала измените направление взгляда; спиной/боком доступна ходьба.'
        elif backward and movement_mode=='walk' and actor.movement_mode_this_turn=='walk':
            result['reason']='В этом ходу уже выбрана ходьба вперёд. Сначала измените направление взгляда.'
        elif forward and (movement_mode=='backward_sideways' or movement_mode=='walk' and actor.movement_mode_this_turn=='backward_sideways'):
            result['reason']='В выбранном режиме можно идти только спиной или боком.'
    return jsonify(result), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>', methods=['PUT'])
@jwt_required()
@requires_gm
def update_location(lobby_id, location_id, lobby):
    location = Location.query.get_or_404(location_id)
    if location.lobby_id != lobby_id:
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}
    delete_structures = data.get('delete_structures') is True
    if 'name' in data:
        if not isinstance(data['name'], str) or not 1 <= len(data['name'].strip()) <= 100:
            return jsonify({'error': 'Location name must contain 1 to 100 characters'}), 400
        data['name'] = data['name'].strip()
    for field, limit in (
        ('world_tile_x', lobby.chunks_width * 32),
        ('world_tile_z', lobby.chunks_height * 32),
    ):
        if field in data and (type(data[field]) is not int or not 0 <= data[field] < limit):
            return jsonify({'error': f'{field} must be a tile within the world map'}), 400
    allowed = ['name', 'description', 'type', 'grid_width', 'grid_height', 'tiles_data',
               'spawn_points', 'world_radius', 'world_tile_x', 'world_tile_z']
    if 'tiles_data' in data:
        preserve_water_offsets(data['tiles_data'], location.tiles_data)
    for field in allowed:
        if field in data:
            setattr(location, field, data[field])
    if 'tiles_data' in data:
        freeze_water_offsets(location.tiles_data)
        flag_modified(location, 'tiles_data')
    if 'lighting' in data:
        location.lighting = normalize_location_lighting(
            data['lighting'], location.grid_width, location.grid_height,
        )
    elif 'grid_width' in data or 'grid_height' in data:
        location.lighting = normalize_location_lighting(
            location.lighting or {}, location.grid_width, location.grid_height,
        )

    deleted_structure_ids = []
    if delete_structures:
        location_objects = LocationObject.query.filter_by(location_id=location.id).all()
        structures = [
            obj for obj in location_objects
            if str(obj.type or '').lower() != 'ground_item'
            and not bool((obj.properties or {}).get('is_ground_item'))
        ]
        deleted_structure_ids = [obj.id for obj in structures]
        if deleted_structure_ids:
            LocationCharacter.query.filter(
                LocationCharacter.location_id == location.id,
                LocationCharacter.cover_object_id.in_(deleted_structure_ids),
            ).update({
                LocationCharacter.cover_object_id: None,
                LocationCharacter.weapon_braced: False,
                LocationCharacter.braced_weapon_index: None,
            }, synchronize_session=False)
            for structure in structures:
                db.session.delete(structure)
    db.session.commit()

    if any(field in data for field in ('tiles_data', 'grid_width', 'grid_height')):
        all_updates = []
        for z, row in enumerate(location.tiles_data):
            for x, tile in enumerate(row):
                all_updates.append({
                    'x': x,
                    'z': z,
                    'terrain': tile.get('terrain'),
                    'height': tile.get('height'),
                    'water_level_offset': tile.get('water_level_offset'),
                    'water_profile': tile.get('water_profile', 'auto'),
                    'objects': tile.get('objects', [])
                })
        socketio.emit('location_tiles_updated', {
            'location_id': location.id,
            'updates': all_updates
        }, room=f"location_{location.id}")

    location_marker = {
        'id': location.id,
        'name': location.name,
        'world_tile_x': location.world_tile_x,
        'world_tile_z': location.world_tile_z,
    }

    socketio.emit('location_updated', {
        'lobby_id': lobby_id,
        'location_id': location.id,
        'location': location_marker,
        'updates': {
            **{k: data[k] for k in allowed if k in data},
            **({'lighting': location.lighting} if 'lighting' in data else {}),
        }
    }, room=f"lobby_{lobby_id}")

    for object_id in deleted_structure_ids:
        socketio.emit('location_object_deleted', {
            'location_id': location.id,
            'object_id': object_id,
        }, room=f"location_{location.id}")

    return jsonify({
        'message': 'Location updated',
        'location': location_marker,
        **({'lighting': location.lighting} if 'lighting' in data else {}),
        'deleted_structure_count': len(deleted_structure_ids),
    }), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>', methods=['DELETE'])
@jwt_required()
@requires_gm
def delete_location(lobby_id, location_id, lobby):
    location = Location.query.get_or_404(location_id)
    if location.lobby_id != lobby_id:
        return jsonify({'error': 'Access denied'}), 403

    CharacterInteractionRequest.query.filter_by(location_id=location.id).delete(synchronize_session=False)
    LocationCombatState.query.filter_by(location_id=location.id).delete(synchronize_session=False)
    LocationCharacter.query.filter_by(location_id=location.id).delete(synchronize_session=False)
    LocationObject.query.filter_by(location_id=location.id).delete(synchronize_session=False)
    db.session.delete(location)
    db.session.commit()

    socketio.emit('location_deleted', {
        'lobby_id': lobby_id,
        'location_id': location_id
    }, room=f"lobby_{lobby_id}")
    return jsonify({'message': 'Location deleted'}), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/objects', methods=['POST'])
@jwt_required()
@requires_gm
def add_location_object(lobby_id, location_id, lobby):
    location = Location.query.get_or_404(location_id)
    if location.lobby_id != lobby_id:
        return jsonify({'error': 'Access denied'}), 403

    schema = LocationObjectSchema()
    data = schema.load(request.get_json())
    obj = LocationObject(
        location_id=location_id,
        name=data['name'],
        type=data['type'],
        tile_x=data['tile_x'],
        tile_y=data['tile_y'],
        properties=data.get('properties', {})
    )
    db.session.add(obj)
    db.session.commit()
    payload = schema.dump(obj)
    socketio.emit('location_object_created', {
        'location_id': location_id,
        'object': payload
    }, room=f"location_{location_id}")
    return jsonify(payload), 201


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/object-edits', methods=['POST'])
@jwt_required()
@requires_gm
def edit_location_objects(lobby_id, location_id, lobby):
    from app.services.location_editor_history import apply_object_edit
    payload = apply_object_edit(location_id, lobby_id, request.get_json())
    socketio.emit('location_editor_objects_changed', payload, room=f'location_{location_id}')
    return jsonify(payload)


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/objects/batch', methods=['POST', 'DELETE'])
@jwt_required()
@requires_gm
def change_location_objects_batch(lobby_id, location_id, lobby):
    """One committed edit and one room event, including for the initiating client."""
    from app.services.location_navigation import level_of, contains, surface, pose
    from time import perf_counter
    started = perf_counter()
    timings = {}
    location = Location.query.get_or_404(location_id)
    if location.lobby_id != lobby_id:
        return jsonify({'error': 'Access denied'}), 403
    data = request.get_json() or {}
    if not isinstance(data, dict):
        raise ValidationError('Batch must be an object')
    schema = LocationObjectSchema()
    created, deleted = [], []
    if request.method == 'POST':
        replacement = data.get('replace_layout')
        furniture_replacement = data.get('replace_furniture')
        if replacement is not None and furniture_replacement is not None:
            raise ValidationError('Replace layout and furniture separately')
        if replacement is not None:
            if (not isinstance(replacement, dict) or not isinstance(replacement.get('building_id'), str)
                    or not replacement['building_id'].strip() or type(replacement.get('level')) is not int):
                raise ValidationError('Layout replacement requires building_id and integer level')
            ids = replacement.get('object_ids')
            if (not isinstance(ids, list) or len(ids) > 5000
                    or any(type(i) is not int for i in ids) or len(set(ids)) != len(ids)):
                raise ValidationError('Layout replacement requires unique object IDs')
            # A stale client must never replace a newer layout or arbitrary manual geometry.
            peers = LocationObject.query.filter_by(location_id=location_id).all()
            deleted = [o for o in peers if (o.properties or {}).get('building_id') == replacement['building_id']
                       and level_of(o) == replacement['level']
                       and isinstance((o.properties or {}).get('layout_generation'), dict)
                       and o.properties['layout_generation'].get('version') == 1
                       and o.type in {'wall', 'door'}]
            if {o.id for o in deleted} != set(ids):
                return jsonify({'error': 'Планировка изменилась. Обновите локацию и повторите предпросмотр.'}), 409
        anomaly_generation = data.get('generate_anomalies')
        if anomaly_generation is not None:
            if (replacement is not None or furniture_replacement is not None
                    or not isinstance(anomaly_generation, dict)
                    or not isinstance(anomaly_generation.get('building_id'), str)):
                raise ValidationError('Generate anomalies separately for a building')
            versions = anomaly_generation.get('source_versions')
            if (not isinstance(versions, list) or any(not isinstance(v, list) or len(v) != 2
                    or any(type(n) is not int for n in v) for v in versions)):
                raise ValidationError('Anomaly generation requires source revisions')
            current = LocationObject.query.filter_by(location_id=location_id).all()
            if sorted(versions) != sorted([[o.id, o.revision] for o in current]):
                return jsonify({'error': 'Обстановка изменилась. Повторите генерацию аномалий.'}), 409
        specs = data.get('objects')
        if not isinstance(specs, list) or not (0 if replacement is not None or furniture_replacement is not None else 1) <= len(specs) <= 5000:
            raise ValidationError('Expected 1 to 5000 objects')
        specs = schema.load(specs, many=True)
        for spec in specs:
            if not all(k in spec for k in ('name', 'type', 'tile_x', 'tile_y')) or not (
                    0 <= spec['tile_x'] < location.grid_width and 0 <= spec['tile_y'] < location.grid_height):
                raise ValidationError('Invalid object position or missing fields')
            if replacement is not None:
                props = spec.get('properties') or {}
                generation = props.get('layout_generation')
                if (spec['type'] not in {'wall', 'door'} or props.get('building_id') != replacement['building_id']
                        or type(props.get('level')) is not int or props['level'] != replacement['level']
                        or not isinstance(generation, dict) or generation.get('version') != 1):
                    raise ValidationError('Replacement must contain only layout walls and doors on the selected floor')
        if anomaly_generation is not None:
            from app.services.anomaly_profiles import anomaly_profile
            if any(o['type'] != 'anomaly' or o.get('properties', {}).get('building_id') != anomaly_generation['building_id']
                   or not anomaly_profile(o.get('properties', {}).get('anomaly_key')) for o in specs):
                raise ValidationError('Expected existing anomaly types in the selected building')
        if furniture_replacement is not None:
            from app.services.location_furniture import validate_furniture_replacement
            deleted = validate_furniture_replacement(location, furniture_replacement, specs)
        timings['validation_ms'] = (perf_counter() - started) * 1000
        phase = perf_counter()
        created = [LocationObject(location_id=location_id, **{k: v for k, v in spec.items()
                   if k != 'location_id'}) for spec in specs]
        db.session.add_all(created)
        removed = {o.id for o in deleted}
        if removed:
            for actor in LocationCharacter.query.filter_by(location_id=location_id).all():
                if actor.cover_object_id in removed:
                    actor.cover_object_id = None
                    actor.weapon_braced = False
                    actor.braced_weapon_index = None
            for obj in deleted:
                db.session.delete(obj)
        timings['objects_ms'] = (perf_counter() - phase) * 1000
    else:
        objects = LocationObject.query.filter_by(location_id=location_id).all()
        building_id, level, ids = data.get('building_id'), data.get('level'), data.get('object_ids')
        if level is not None and type(level) is not int:
            raise ValidationError('level must be an integer')
        if ids is not None:
            if not isinstance(ids, list) or not ids or len(ids) > 5000 or any(type(i) is not int for i in ids) or level is None:
                raise ValidationError('Object deletion requires IDs and an active level')
            selected = set(ids)
            deleted = [o for o in objects if o.id in selected and level_of(o) == level]
            if {o.id for o in deleted} != selected:
                raise ValidationError('Objects must belong to this location and active level')
        elif isinstance(building_id, str) and building_id.strip():
            peers = [o for o in objects if (o.properties or {}).get('building_id') == building_id]
            if level is not None:
                levels = {level_of(o) for o in peers}
                if level == 0:
                    return jsonify({'error': 'Этаж 0 — обязательный базовый уровень. Удалите здание целиком.'}), 409
                if level not in levels:
                    return jsonify({'error': 'В выбранном здании нет этого этажа.'}), 409
                if level > 0 and level != max(levels):
                    return jsonify({'error': 'Можно удалить только самый верхний наземный этаж здания.'}), 409
                if level < 0 and level != min(levels):
                    return jsonify({'error': 'Можно удалить только самый нижний подвальный этаж здания.'}), 409
            deleted = [o for o in peers if level is None or level_of(o) == level]
            if level is not None:
                # A stair is owned by its lower level but depends on the upper landing.
                deleted += [o for o in peers if o.type in {'stairs', 'ladder'} and level_of(o) + 1 == level]
                shafts = [o for o in deleted if o.type in {'stairs', 'ladder'} and level_of(o) == level]
                deleted += [o for o in peers if o.type in {'hatch', 'floor_opening'} and level_of(o) == level+1
                            and any(contains(o, *pose(stair)[:2]) for stair in shafts)]
        else:
            raise ValidationError('Specify building_id or object_ids with level')
        removed = {o.id for o in deleted}
        remaining = [o for o in objects if o.id not in removed]
        actors = LocationCharacter.query.filter_by(location_id=location_id).all()
        for actor in actors:
            if any(o.type == 'floor' and level_of(o) == (actor.floor_level or 0)
                   and contains(o, actor.pos_x + .5, actor.pos_y + .5) for o in deleted):
                return jsonify({'error': 'Сначала переместите персонажей с удаляемого этажа'}), 409
            if surface(location, objects, actor.pos_x, actor.pos_y, actor.floor_level or 0) and not surface(
                    location, remaining, actor.pos_x, actor.pos_y, actor.floor_level or 0):
                return jsonify({'error': 'Удаление лишит персонажа опоры; сначала переместите его'}), 409
        for actor in actors:
            if actor.cover_object_id in removed:
                actor.cover_object_id = None
                actor.weapon_braced = False
                actor.braced_weapon_index = None
        for obj in deleted:
            db.session.delete(obj)
    phase = perf_counter()
    db.session.commit()
    timings['persist_ms'] = (perf_counter() - phase) * 1000
    phase = perf_counter()
    payload = {'location_id': location_id, 'objects': schema.dump(created, many=True),
               'deleted_ids': [o.id for o in deleted]}
    timings['serialize_ms'] = (perf_counter() - phase) * 1000
    payload['timings'] = timings
    phase = perf_counter()
    socketio.emit('location_objects_changed', payload, room=f'location_{location_id}')
    # Enqueue time is server work, not a claim about delivery latency.
    timings['emit_ms'] = (perf_counter() - phase) * 1000
    return jsonify(payload), 201 if request.method == 'POST' else 200


@lobbies_bp.route('/<int:lobby_id>/locations/objects/<int:object_id>', methods=['DELETE'])
@jwt_required()
@requires_gm
def delete_location_object(lobby_id, object_id, lobby):
    obj = LocationObject.query.get_or_404(object_id)
    location = Location.query.get(obj.location_id)
    if location.lobby_id != lobby_id:
        return jsonify({'error': 'Access denied'}), 403
    location_id = location.id
    db.session.delete(obj)
    db.session.commit()
    socketio.emit('location_object_deleted', {
        'location_id': location_id,
        'object_id': object_id
    }, room=f"location_{location_id}")
    return '', 204


@lobbies_bp.route('/<int:lobby_id>/locations/objects/<int:object_id>', methods=['PATCH'])
@jwt_required()
@requires_participant
def update_location_object(lobby_id, object_id, lobby, participant):
    obj = LocationObject.query.get_or_404(object_id)
    location = Location.query.get(obj.location_id)
    if location.lobby_id != lobby_id:
        return jsonify({'error': 'Access denied'}), 403

    data = request.get_json() or {}
    if not isinstance(data, dict):
        raise ValidationError('Object update must be an object')
    properties = data.get('properties') if isinstance(data.get('properties'), dict) else {}
    if obj.type == 'hatch':
        is_gm = participant.user_id == lobby.gm_id
        if not is_gm and (set(data) - {'properties','actor_character_id'} or set(properties) != {'is_open'}):
            raise PermissionDenied('Players can only open or close hatches')
        if 'is_open' in properties:
            if type(properties['is_open']) is not bool:
                raise ValidationError('is_open must be a boolean')
            actor_id = data.get('actor_character_id')
            if actor_id is not None or not is_gm:
                if type(actor_id) is not int:
                    raise ValidationError('actor_character_id is required')
                actor = LocationCharacter.query.filter_by(location_id=location.id,character_id=actor_id).first()
                if not actor or not (is_gm or actor.character.owner_id == participant.user_id or actor.controlled_by == participant.user_id):
                    raise PermissionDenied('Character is not controlled by this player')
                CombatService.ensure_character_can_act(actor)
                state = LocationCombatState.query.filter_by(location_id=location.id).first()
                if state and state.status == 'active' and not is_gm and state.current_location_character_id != actor.id:
                    raise PermissionDenied("It is not this character's turn")
                from app.services.location_navigation import level_of, contains
                level = level_of(obj)
                actor_level = actor.floor_level or 0
                footprint = CombatService._object_footprint_tiles(obj)
                if actor_level not in {level,level-1} or not footprint or min(max(abs(actor.pos_x-x),abs(actor.pos_y-y)) for x,y in footprint) > 1:
                    raise ValidationError('Too far from hatch')
                if actor_level == level-1 and not any(o.type == 'ladder' and level_of(o) == actor_level
                        and (o.properties or {}).get('building_id') == (obj.properties or {}).get('building_id')
                        and contains(obj,o.tile_x+.5+(o.properties or {}).get('offset_x',0),o.tile_y+.5+(o.properties or {}).get('offset_z',0))
                        for o in LocationObject.query.filter_by(location_id=location.id).all()):
                    raise ValidationError('A ladder is required to reach the hatch from below')
                if (obj.properties or {}).get('locked') and properties['is_open']:
                    raise ValidationError('Hatch is locked')
                if not is_gm and (obj.properties or {}).get('gm_hidden'):
                    raise PermissionDenied('Hidden hatch cannot be used')
    if obj.type in {'ceiling_bulb', 'wall_bulb'} and participant.user_id != lobby.gm_id:
        raise PermissionDenied('Only GM can edit electric fixtures')
    if 'is_lit' in properties:
        if obj.type not in {'campfire', 'torch', 'wall_torch', 'fire_barrel'}:
            raise ValidationError('Only fire objects can be lit')
        if type(properties['is_lit']) is not bool:
            raise ValidationError('is_lit must be a boolean')
        if participant.user_id != lobby.gm_id:
            if (obj.properties or {}).get('gm_hidden'):
                raise PermissionDenied('Hidden fire object cannot be used')
            if set(properties) != {'is_lit'} or 'tile_x' in data or 'tile_y' in data:
                raise PermissionDenied('Players can only toggle fire')
            actor_id = data.get('actor_character_id')
            if type(actor_id) is not int:
                raise ValidationError('actor_character_id is required')
            actor = LocationCharacter.query.filter_by(
                location_id=location.id, character_id=actor_id,
            ).first()
            if not actor or not actor.character or not (
                actor.character.owner_id == participant.user_id
                or actor.controlled_by == participant.user_id
            ):
                raise PermissionDenied('Character is not controlled by this player')
            footprint = CombatService._object_footprint_tiles(obj) or {(obj.tile_x, obj.tile_y)}
            if min(max(abs(actor.pos_x - x), abs(actor.pos_y - y)) for x, y in footprint) > 1:
                raise ValidationError('Too far from fire object')
    if 'is_on' in properties:
        if obj.type not in {'ceiling_bulb', 'wall_bulb', 'household_street_lamp', 'household_floor_lamp'}:
            raise ValidationError('Only electric fixtures can be switched')
        if type(properties['is_on']) is not bool:
            raise ValidationError('is_on must be a boolean')
        if participant.user_id != lobby.gm_id:
            raise PermissionDenied('Only GM can switch electric fixtures')
    if 'gm_hidden' in (data.get('properties') or {}):
        if participant.user_id != lobby.gm_id:
            raise PermissionDenied('Only GM can hide location objects')
        if type(data['properties']['gm_hidden']) is not bool:
            raise ValidationError('gm_hidden must be a boolean')
    if 'contents' in (data.get('properties') or {}):
        if participant.user_id != lobby.gm_id:
            raise PermissionDenied('Use the item transfer action')
        if type(data.get('revision')) is not int or data['revision'] != obj.revision:
            raise ConflictError()
    if 'tile_x' in data:
        obj.tile_x = data['tile_x']
    if 'tile_y' in data:
        obj.tile_y = data['tile_y']
    if 'properties' in data and isinstance(data['properties'], dict):
        obj.properties = {**(obj.properties or {}), **data['properties']}
    db.session.commit()

    payload = LocationObjectSchema().dump(obj)
    socketio.emit('location_object_updated', {
        'location_id': location.id,
        'object': payload
    }, room=f"location_{location.id}")
    return jsonify(payload), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/teams', methods=['GET', 'PUT'])
@jwt_required()
def manage_location_teams(lobby_id, location_id):
    user_id = int(get_jwt_identity())
    location = Location.query.get(location_id)
    lobby = Lobby.query.get(lobby_id)
    if not location or not lobby or location.lobby_id != lobby_id:
        return jsonify({'error': 'Location not found'}), 404
    participant = LobbyParticipant.query.filter_by(lobby_id=lobby_id, user_id=user_id).first()
    if not participant:
        return jsonify({'error': 'Access denied'}), 403
    characters = LocationCharacter.query.filter_by(location_id=location_id).all()
    if request.method == 'PUT':
        if lobby.gm_id != user_id:
            return jsonify({'error': 'Only GM can manage teams'}), 403
        payload = request.get_json() or {}
        assignments = payload.get('assignments') or []
        by_id = {item.id: item for item in characters}
        for assignment in assignments:
            target = by_id.get(assignment.get('location_character_id'))
            if not target:
                continue
            team_name = str(assignment.get('team_name') or '').strip()[:80]
            team_color = str(assignment.get('team_color') or '').strip()
            # A team marker is presentation data too, but only accept a CSS hex color.
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', team_color):
                team_color = ''
            target.team_name = team_name or None
            target.team_color = team_color or None
        db.session.commit()
        socketio.emit('location_teams_updated', {'location_id': location_id}, room=f"location_{location_id}")
    return jsonify({'characters': [{'location_character_id': item.id, 'character_id': item.character_id, 'name': item.character.name if item.character else '', 'team_name': item.team_name, 'team_color': item.team_color} for item in characters]})


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/spawn_character', methods=['POST'])
@jwt_required()
def spawn_character_in_location(lobby_id, location_id):
    user_id = int(get_jwt_identity())
    data = request.get_json()
    character_id = data.get('character_id')
    tile_x = data.get('tile_x')
    tile_y = data.get('tile_y')
    assign_to_user_id = data.get('assign_to_user_id')  # может быть None

    if not character_id or tile_x is None or tile_y is None:
        return jsonify({'error': 'Missing parameters'}), 400

    location = Location.query.get(location_id)
    if not location or location.lobby_id != lobby_id:
        return jsonify({'error': 'Location not found'}), 404

    lobby = Lobby.query.get(lobby_id)
    if not lobby or not lobby.is_active:
        return jsonify({'error': 'Lobby not found'}), 404

    participant = LobbyParticipant.query.filter_by(lobby_id=lobby_id, user_id=user_id).first()
    if not participant or participant.is_banned:
        return jsonify({'error': 'Access denied'}), 403

    is_gm = (lobby.gm_id == user_id)

    force_relocate = data.get('force_relocate', False)
    if type(force_relocate) is not bool:
        return jsonify({'error': 'Invalid relocation mode'}), 400
    if force_relocate and not is_gm:
        return jsonify({'error': 'Only GM can force relocation'}), 403

    character = LobbyCharacter.query.get(character_id)
    if not character:
        return jsonify({'error': 'Character not found'}), 404
    if character.lobby_id != lobby_id:
        return jsonify({'error': 'Character not in this lobby'}), 403

    # Определяем, кому назначить управление
    if assign_to_user_id is not None:
        if not is_gm:
            return jsonify({'error': 'Only GM can assign character to another user'}), 403
        target_user_id = assign_to_user_id
        target_participant = LobbyParticipant.query.filter_by(lobby_id=lobby_id, user_id=target_user_id, is_banned=False).first()
        if not target_participant:
            return jsonify({'error': 'Target user not in lobby or banned'}), 400
    else:
        # По умолчанию владелец – тот, кто спавнит, если он владелец персонажа
        if character.owner_id != user_id and not is_gm:
            return jsonify({'error': 'You cannot spawn this character'}), 403
        target_user_id = user_id

    # Проверяем, не находится ли уже персонаж в этой локации (если да – перемещаем)
    existing = LocationCharacter.query.filter_by(location_id=location_id, character_id=character_id).order_by(
        LocationCharacter.last_action.desc().nullslast(),
        LocationCharacter.id.desc(),
    ).first()
    if existing and not is_gm:
        return jsonify({'error': 'Only GM can relocate an existing character'}), 403
    if force_relocate and not existing:
        return jsonify({'error': 'Character is not placed in this location'}), 404
    target_level = data.get('floor_level', (existing.floor_level or 0) if existing else 0)
    if any(isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) for value in (tile_x,tile_y)) or type(target_level) is not int or not (
        0 <= tile_x <= location.grid_width-1 and 0 <= tile_y <= location.grid_height-1
    ):
        return jsonify({'error': 'Invalid position'}), 400
    if not is_gm and target_level != 0:
        return jsonify({'error': 'Only GM can choose a placement floor'}), 403
    from app.services.location_navigation import surface
    objects = LocationObject.query.filter_by(location_id=location_id).all()
    if not force_relocate and surface(location, objects, tile_x, tile_y, target_level) is None:
        return jsonify({'error': 'No supporting surface on selected floor'}), 400

    from app.services.character_starting_purchase import grant_world_entry
    entered=grant_world_entry(character)

    if existing:
        # Перемещаем на новые координаты
        previous_position = (existing.pos_x, existing.pos_y)
        if (existing.pos_x,existing.pos_y,existing.floor_level or 0)!=(tile_x,tile_y,target_level):
            sheet=deepcopy(existing.character.data or {})
            meta=(sheet.get('health') or {}).get('combatMeta') or {}
            if any(key in meta for key in ('unstableJump','jumpSupport')):
                meta.pop('unstableJump',None)
                meta.pop('jumpSupport',None)
                existing.character.data=sheet
                flag_modified(existing.character,'data')
        existing.pos_x = tile_x
        existing.pos_y = tile_y
        existing.floor_level = target_level
        combat_state = LocationCombatState.query.filter_by(location_id=location_id).first()
        anomaly_changed = False
        if not combat_state or combat_state.status != 'active':
            anomaly_changed = CombatService._sync_anomaly_after_relocation(
                location, existing, previous_position,
            )
        if not force_relocate:
            existing.status = 'idle'
        if assign_to_user_id is not None:
            existing.controlled_by = target_user_id  # обновляем управление
        db.session.commit()
        loc_char = existing
        action = 'moved'
    else:
        # Создаём нового LocationCharacter
        character_data = deepcopy(character.data or {})
        hp_zones = apply_health_maximums(character_data).get('zones', {})
        character.data = character_data
        zones_dict = {
            'head': {'current': hp_zones.get('head', {}).get('current', 50), 'max': hp_zones.get('head', {}).get('max', 50)},
            'chest': {'current': hp_zones.get('chest', {}).get('current', 150), 'max': hp_zones.get('chest', {}).get('max', 150)},
            'abdomen': {'current': hp_zones.get('abdomen', {}).get('current', 120), 'max': hp_zones.get('abdomen', {}).get('max', 120)},
            'left_arm': {'current': hp_zones.get('leftArm', {}).get('current', 90), 'max': hp_zones.get('leftArm', {}).get('max', 90)},
            'right_arm': {'current': hp_zones.get('rightArm', {}).get('current', 90), 'max': hp_zones.get('rightArm', {}).get('max', 90)},
            'left_leg': {'current': hp_zones.get('leftLeg', {}).get('current', 100), 'max': hp_zones.get('leftLeg', {}).get('max', 100)},
            'right_leg': {'current': hp_zones.get('rightLeg', {}).get('current', 100), 'max': hp_zones.get('rightLeg', {}).get('max', 100)}
        }
        loc_char = LocationCharacter(
            location_id=location_id,
            character_id=character_id,
            pos_x=tile_x,
            pos_y=tile_y,
            floor_level=target_level,
            status='idle',
            hp_zones=zones_dict,
            effects=[],
            controlled_by=target_user_id
        )
        db.session.add(loc_char)
        db.session.flush()
        profile = CombatService._combat_profile(loc_char)
        loc_char.initiative_bonus = profile['initiative_bonus']
        loc_char.action_points_max = profile['action_points']
        loc_char.action_points_current = profile['action_points']
        loc_char.free_actions_max = profile['free_actions']
        loc_char.free_actions_current = profile['free_actions']
        loc_char.movement_points_max = 0
        loc_char.movement_points_current = 0
        db.session.commit()
        action = 'spawned'

    target_user_id = loc_char.controlled_by
    if entered:emit_character_update({'character_id':character.id,'updates':{'data':character.data_snapshot()},'source':'world_entry'})
    # Уведомляем всех в локации через сокет
    socketio.emit('character_spawned', {
        'action': action,
        'character': {
            'id': character.id,
            'name': character.name,
            'owner_id': character.owner_id,           # создатель
            'controlled_by': target_user_id,          # <-- кто управляет
            'owner_username': User.query.get(target_user_id).username if target_user_id else None,
            'hp_zones': loc_char.hp_zones,
            'effects': loc_char.effects,
            'pos_x': loc_char.pos_x,
            'pos_y': loc_char.pos_y,
            'facing_x': loc_char.facing_x,
            'facing_y': loc_char.facing_y,
            'team_name': loc_char.team_name,
            'team_color': loc_char.team_color,
            **CombatService.physical_position(loc_char),
            'posture': loc_char.posture,
            'prone_on_back': bool(loc_char.prone_on_back),
            'gm_hidden': bool(loc_char.gm_hidden),
            'is_mutant': CombatService._is_mutant_character(character.data or {}),
            'mutant_type': CombatService._mutant_profile_name(character.data),
            'is_npc': is_npc(character),
        }
    }, room=f"location_{location_id}")
    revealed = getattr(loc_char, '_revealed_anomaly', None)
    if revealed:
        if revealed['object_id']:
            obj = db.session.get(LocationObject, revealed['object_id'])
            if obj:
                socketio.emit('location_object_updated', {
                    'location_id': location_id, 'object': LocationObjectSchema().dump(obj),
                }, room=f'location_{location_id}')
        else:
            x, y = revealed['tile_x'], revealed['tile_y']
            socketio.emit('location_tiles_updated', {
                'location_id': location_id,
                'updates': [{'x': x, 'z': y, 'objects': location.tiles_data[y][x].get('objects', [])}],
            }, room=f'location_{location_id}')
    if existing:
        socketio.emit(
            'combat_state_updated', CombatService.get_state(location_id, user_id),
            room=f"location_{location_id}",
        )

    return jsonify({'message': f'Character {action} successfully', 'location_character_id': loc_char.id}), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/gm-hidden',
    methods=['PATCH'],
)
@jwt_required()
@requires_gm
def set_location_character_hidden(lobby_id, location_id, character_id, lobby):
    location = Location.query.filter_by(id=location_id, lobby_id=lobby_id).first_or_404()
    placed = LocationCharacter.query.filter_by(
        location_id=location.id, character_id=character_id,
    ).first_or_404()
    character = placed.character
    if not character or not (CombatService._is_mutant_character(character.data) or is_npc(character)):
        return jsonify({'error': 'Only mutants and NPCs can be hidden'}), 400
    hidden = (request.get_json(silent=True) or {}).get('gm_hidden')
    if type(hidden) is not bool:
        return jsonify({'error': 'gm_hidden must be a boolean'}), 400
    placed.gm_hidden = hidden
    db.session.commit()
    payload = {'location_id': location_id, 'character_id': character_id, 'gm_hidden': hidden}
    socketio.emit('location_character_visibility_updated', payload, room=f'location_{location_id}')
    return jsonify(payload), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>',
    methods=['DELETE'],
)
@jwt_required()
@requires_gm
def remove_character_from_location(
    lobby_id,
    location_id,
    character_id,
    lobby,
):
    location = Location.query.filter_by(id=location_id, lobby_id=lobby_id).first()
    if not location:
        return jsonify({'error': 'Location not found'}), 404

    location_characters = LocationCharacter.query.filter_by(
        location_id=location_id,
        character_id=character_id,
    ).all()
    if not location_characters:
        return jsonify({'error': 'Character is not in this location'}), 404

    removed_ids = {item.id for item in location_characters}
    combat_state = LocationCombatState.query.filter_by(location_id=location_id).first()
    if combat_state:
        old_order = list(dict.fromkeys(combat_state.turn_order or []))
        removed_current = combat_state.current_location_character_id in removed_ids
        current_index = (
            old_order.index(combat_state.current_location_character_id)
            if removed_current and combat_state.current_location_character_id in old_order
            else 0
        )
        new_order = [item_id for item_id in old_order if item_id not in removed_ids]
        combat_state.turn_order = new_order

        if not new_order:
            combat_state.current_location_character_id = None
            combat_state.turn_index = 0
            if combat_state.status == 'active':
                combat_state.status = 'idle'
        elif removed_current:
            next_index = min(current_index, len(new_order) - 1)
            combat_state.current_location_character_id = new_order[next_index]
            combat_state.turn_index = next_index
            if combat_state.status == 'active':
                next_character = db.session.get(
                    LocationCharacter,
                    combat_state.current_location_character_id,
                )
                if next_character:
                    CombatService._prepare_character_for_turn(next_character)
        elif combat_state.current_location_character_id in new_order:
            combat_state.turn_index = new_order.index(
                combat_state.current_location_character_id
            )

    for location_character in location_characters:
        db.session.delete(location_character)
    db.session.commit()

    socketio.emit(
        'location_character_removed',
        {
            'location_id': location_id,
            'character_id': character_id,
        },
        room=f"location_{location_id}",
    )
    if combat_state:
        socketio.emit(
            'combat_state_updated',
            CombatService._serialize_state(location, combat_state),
            room=f"location_{location_id}",
        )

    return jsonify({'message': 'Character removed from location'}), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/posture',
    methods=['PATCH'],
)
@jwt_required()
@requires_participant
def change_character_posture_outside_combat(
    lobby_id,
    location_id,
    character_id,
    lobby,
    participant,
):
    location = Location.query.filter_by(id=location_id, lobby_id=lobby_id).first()
    if not location:
        return jsonify({'error': 'Location not found'}), 404

    combat_state = LocationCombatState.query.filter_by(location_id=location_id).first()
    if combat_state and combat_state.status == 'active':
        return jsonify({'error': 'Use the combat action to change posture'}), 409

    location_character = LocationCharacter.query.filter_by(
        location_id=location_id,
        character_id=character_id,
    ).first()
    if not location_character:
        return jsonify({'error': 'Character is not in this location'}), 404

    user_id = participant.user_id
    can_control = (
        lobby.gm_id == user_id
        or location_character.controlled_by == user_id
        or (
            location_character.character
            and location_character.character.owner_id == user_id
        )
    )
    if not can_control:
        return jsonify({'error': 'You do not control this character'}), 403
    try:
        CombatService.ensure_character_can_act(location_character)
    except Exception as error:
        return jsonify({'error': str(error)}), 409

    target_posture = str((request.get_json() or {}).get('posture') or '').lower()
    if target_posture not in {'standing', 'sitting', 'prone'}:
        return jsonify({'error': 'Unknown posture'}), 400
    if target_posture == location_character.posture:
        return jsonify({'error': 'Character is already in this posture'}), 400

    location_character.posture = target_posture
    location_character.weapon_braced = False
    location_character.braced_weapon_index = None
    if target_posture == 'standing':
        location_character.cover_object_id = None
    db.session.commit()

    payload = {
        'location_id': location_id,
        'character_id': character_id,
        'posture': target_posture,
    }
    socketio.emit(
        'location_character_posture_updated',
        payload,
        room=f"location_{location_id}",
    )
    return jsonify(payload), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/visual-pose',
    methods=['PATCH'],
)
@jwt_required()
@requires_participant
def change_character_visual_pose(lobby_id, location_id, character_id, lobby, participant):
    location = Location.query.filter_by(id=location_id, lobby_id=lobby_id).first()
    if not location:
        return jsonify({'error': 'Location not found'}), 404
    location_character = LocationCharacter.query.filter_by(
        location_id=location_id, character_id=character_id,
    ).first()
    if not location_character:
        return jsonify({'error': 'Character is not in this location'}), 404
    user_id = participant.user_id
    can_control = (
        lobby.gm_id == user_id
        or location_character.controlled_by == user_id
        or (
            location_character.character
            and location_character.character.owner_id == user_id
        )
    )
    if not can_control:
        return jsonify({'error': 'You do not control this character'}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected a JSON object'}), 400
    if type(data.get('prone_on_back')) is not bool:
        return jsonify({'error': 'prone_on_back must be a boolean'}), 400
    if location_character.posture != 'prone':
        return jsonify({'error': 'This visual variant requires the prone posture'}), 409
    character_data = location_character.character.data if location_character.character else {}
    if CombatService._is_mutant_character(character_data or {}):
        return jsonify({'error': 'Visual variant is unavailable for mutants'}), 400

    location_character.prone_on_back = data['prone_on_back']
    db.session.commit()
    payload = {
        'location_id': location_id,
        'character_id': character_id,
        'prone_on_back': bool(location_character.prone_on_back),
    }
    socketio.emit('location_character_visual_pose_updated', payload,
                  room=f'location_{location_id}')
    return jsonify(payload), 200


@lobbies_bp.route('/<int:lobby_id>/locations/objects/<int:object_id>/transfer', methods=['POST'])
@jwt_required()
@requires_participant
def transfer_location_item(lobby_id, object_id, lobby, participant):
    character, obj, deleted = ContainerTransferService.transfer(
        lobby_id, object_id, participant.user_id, request.get_json() or {},
    )
    emit_character_update({'character_id': character.id, 'updates': {'data': character.data}, 'source': 'container_transfer'})
    payload = None if deleted else LocationObjectSchema().dump(obj)
    socketio.emit('location_object_deleted' if deleted else 'location_object_updated',
                  {'location_id': obj.location_id, 'object_id': object_id, 'object': payload},
                  room=f'location_{obj.location_id}')
    return jsonify({'data': character.data_snapshot(), 'object': payload, 'deleted': deleted}), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/drop-item', methods=['POST'])
@jwt_required()
@requires_participant
def drop_location_item(lobby_id, location_id, lobby, participant):
    character, obj, created = ContainerTransferService.drop(
        lobby_id, location_id, participant.user_id, request.get_json() or {},
    )
    emit_character_update({'character_id': character.id, 'updates': {'data': character.data}, 'source': 'drop_item'})
    payload = LocationObjectSchema().dump(obj)
    socketio.emit('location_object_created' if created else 'location_object_updated',
                  {'location_id': location_id, 'object': payload}, room=f'location_{location_id}')
    return jsonify({'data': character.data_snapshot(), 'object': payload, 'created': created}), 200


@lobbies_bp.route('/characters/<int:character_id>/treatment', methods=['POST'])
@jwt_required()
def finish_sheet_treatment(character_id):
    user_id = int(get_jwt_identity())
    payload = request.get_json() or {}
    actor, target, use_result = TreatmentService.finish_sheets(user_id, character_id, payload)
    publish_character_save(actor, payload['actor_updates'], user_id)
    publish_character_save(target, payload['target_updates'], user_id)
    return jsonify({'actor_data': actor.character.data_snapshot(), 'target_data': target.character.data_snapshot(), 'consumable_result': use_result}), 200


@lobbies_bp.route('/characters/<int:character_id>/consumable', methods=['POST'])
@jwt_required()
def finish_self_consumable(character_id):
    user_id = int(get_jwt_identity())
    payload = request.get_json() or {}
    result, use_result = TreatmentService.finish_self(user_id, character_id, payload)
    publish_character_save(result, payload['actor_updates'], user_id)
    return jsonify({'data': result.character.data_snapshot(), 'consumable_result': use_result}), 200


def _public_medical_character_snapshot(snapshot):
    # Удаляем внутренние аргументы только из публичной копии, не из ORM JSON.
    public = deepcopy(snapshot)
    health = public.get('health')
    meta = health.get('combatMeta') if isinstance(health, dict) else None
    retry = meta.get('mustDoRetry') if isinstance(meta, dict) else None
    if isinstance(retry, dict):
        retry.pop('medical_retry', None)
        retry.pop('attack_details', None)
    return public


@lobbies_bp.route('/characters/<int:character_id>/medical-procedure', methods=['POST'])
@jwt_required()
def apply_medical_procedure(character_id):
    user_id = int(get_jwt_identity())
    payload = request.get_json() or {}
    operation = MedicalProcedureService.apply(user_id, character_id, payload)
    emit_character_update({
        'character_id': operation.actor.id,
        'updates': {'data': operation.actor.data},
        'source': 'medical_procedure',
    }, snapshot_projector=_public_medical_character_snapshot)
    if operation.target.id != operation.actor.id:
        emit_character_update({
            'character_id': operation.target.id,
            'updates': {'data': operation.target.data},
            'source': 'medical_procedure',
        }, snapshot_projector=_public_medical_character_snapshot)
    for related in operation.related_characters:
        emit_character_update({
            'character_id': related.id,
            'updates': {'data': related.data},
            'source': 'medical_procedure',
        }, snapshot_projector=_public_medical_character_snapshot)
    for posture_update in operation.posture_updates:
        socketio.emit('location_character_posture_updated', posture_update,
                      room=f"location_{posture_update['location_id']}")
    location_id = payload.get('combat_location_id')
    if type(location_id) is int:
        state = CombatService.get_state(location_id, user_id)
        socketio.emit('combat_state_updated', state, room=f'location_{location_id}')
    return jsonify({
        'actor_data': _public_medical_character_snapshot(operation.actor.data_snapshot()),
        'target_data': _public_medical_character_snapshot(operation.target.data_snapshot()),
        'medical_result': operation.result,
    }), 200


@lobbies_bp.route('/characters/<int:character_id>/general-consumable', methods=['POST'])
@jwt_required()
def apply_general_consumable(character_id):
    payload = request.get_json() or {}
    operation = GeneralConsumableService.apply(
        int(get_jwt_identity()), character_id, payload,
    )
    emit_character_update({
        'character_id': operation.character.id,
        'updates': {'data': operation.character.data},
        'source': 'general_consumable',
    })
    for posture_update in operation.posture_updates:
        socketio.emit('location_character_posture_updated', posture_update,
                      room=f"location_{posture_update['location_id']}")
    location_id = payload.get('combat_location_id')
    if type(location_id) is int:
        state = CombatService.get_state(location_id, int(get_jwt_identity()))
        socketio.emit('combat_state_updated', state, room=f'location_{location_id}')
    return jsonify({
        'data': operation.character.data_snapshot(),
        'consumable_result': operation.result,
    }), 200


@lobbies_bp.route('/characters/<int:character_id>/magazine', methods=['POST'])
@jwt_required()
def install_character_magazine(character_id):
    from app.services.magazine import install_outside_combat
    character = install_outside_combat(character_id, int(get_jwt_identity()), request.get_json() or {})
    emit_character_update({'character_id': character.id, 'updates': {'data': character.data}, 'source': 'reload'})
    return jsonify({'data': character.data_snapshot()}), 200


@lobbies_bp.route('/characters/<int:character_id>/ammunition', methods=['POST'])
@jwt_required()
def load_character_ammunition(character_id):
    from app.services.ammo_loading import load_outside_combat
    character, result = load_outside_combat(character_id, int(get_jwt_identity()), request.get_json() or {})
    emit_character_update({'character_id': character.id, 'updates': {'data': character.data}, 'source': 'reload'})
    return jsonify({'data': character.data_snapshot(), 'loading': result}), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/facing',
    methods=['PATCH'],
)
@jwt_required()
@requires_participant
def change_character_facing_outside_combat(
    lobby_id,
    location_id,
    character_id,
    lobby,
    participant,
):
    location = Location.query.filter_by(id=location_id, lobby_id=lobby_id).first()
    if not location:
        return jsonify({'error': 'Location not found'}), 404
    combat_state = LocationCombatState.query.filter_by(location_id=location_id).first()
    if combat_state and combat_state.status == 'active':
        return jsonify({'error': 'Use the combat action to turn'}), 409
    location_character = LocationCharacter.query.filter_by(
        location_id=location_id,
        character_id=character_id,
    ).first()
    if not location_character:
        return jsonify({'error': 'Character is not in this location'}), 404
    user_id = participant.user_id
    can_control = (
        lobby.gm_id == user_id
        or location_character.controlled_by == user_id
        or (location_character.character and location_character.character.owner_id == user_id)
    )
    if not can_control:
        return jsonify({'error': 'You do not control this character'}), 403
    payload = request.get_json() or {}
    facing_x = CombatService._coerce_int(payload.get('facing_x'), 0)
    from app.services.character_jump import require_balance
    require_balance(location_character)
    facing_y = CombatService._coerce_int(payload.get('facing_y'), 0)
    if (facing_x, facing_y) == (0, 0):
        return jsonify({'error': 'Choose a facing direction'}), 400
    location_character.facing_x = facing_x
    location_character.facing_y = facing_y
    db.session.commit()
    result = {
        'location_id': location_id,
        'character_id': character_id,
        'facing_x': facing_x,
        'facing_y': facing_y,
    }
    socketio.emit('location_character_facing_updated', result, room=f"location_{location_id}")
    return jsonify(result), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/interaction',
    methods=['GET'],
)
@jwt_required()
@requires_participant
def inspect_incapacitated_location_character(
    lobby_id,
    location_id,
    character_id,
    lobby,
    participant,
):
    actor_location_character_id = request.args.get('actor_location_character_id', type=int)
    if not actor_location_character_id:
        return jsonify({'error': 'actor_location_character_id is required'}), 400
    result = CharacterInteractionService.interaction_snapshot(
        location_id,
        participant.user_id,
        actor_location_character_id,
        character_id,
    )
    return jsonify(result), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/butchering',
    methods=['POST'],
)
@jwt_required()
@requires_participant
def butcher_location_mutant(
    lobby_id,
    location_id,
    character_id,
    lobby,
    participant,
):
    data = request.get_json(silent=True) or {}
    actor_location_character_id = data.get('actor_location_character_id')
    if not actor_location_character_id:
        return jsonify({'error': 'actor_location_character_id is required'}), 400
    result = CombatService.butcher_mutant(
        location_id,
        participant.user_id,
        actor_location_character_id,
        character_id,
        allocation=data.get('allocation') if data.get('confirm') else None,
    )
    if result.get('completed'):
        emit_character_update(
            {
                'character_id': result['actor_character_id'],
                'updates': {'data': result['actor_data']},
                'source': 'mutant_butchering',
            },
        )
        loot_text = ', '.join(
            f"{item['name']} x{item['quantity']}" for item in result.get('loot') or []
        ) or 'ничего'
        _emit_lobby_chat_message(
            lobby_id,
            participant.user_id,
            f"Разделка {result['target_name']}: получено {loot_text}.",
            username='Разделка',
        )
    return jsonify(result), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/loot',
    methods=['POST'],
)
@jwt_required()
@requires_participant
def loot_incapacitated_location_character(
    lobby_id,
    location_id,
    character_id,
    lobby,
    participant,
):
    data = request.get_json() or {}
    actor_location_character_id = data.get('actor_location_character_id')
    if not actor_location_character_id:
        return jsonify({'error': 'actor_location_character_id is required'}), 400
    result = CombatService.loot_incapacitated_character(
        location_id,
        participant.user_id,
        actor_location_character_id,
        character_id,
        data.get('item_path'),
        data.get('amount', 1),
    )
    emit_character_update(
        {
            'character_id': character_id,
            'updates': {'data': result['target_data']},
            'updated_by': participant.user_id,
        },
    )
    actor = db.session.get(LocationCharacter, actor_location_character_id)
    if actor and actor.character:
        emit_character_update(
            {
                'character_id': actor.character.id,
                'updates': {'data': actor.character.data},
                'updated_by': participant.user_id,
            },
        )
    return jsonify(result), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/treatment',
    methods=['PATCH'],
)
@jwt_required()
@requires_participant
def treat_incapacitated_location_character(
    lobby_id,
    location_id,
    character_id,
    lobby,
    participant,
):
    if not Location.query.filter_by(id=location_id, lobby_id=lobby_id).first():
        return jsonify({'error': 'Location not found'}), 404
    data = request.get_json() or {}
    actor_location_character_id = data.get('actor_location_character_id')
    if not actor_location_character_id:
        return jsonify({'error': 'actor_location_character_id is required'}), 400
    actor_result, target, interaction_result, use_result = TreatmentService.finish(
        location_id, participant.user_id, character_id, data,
    )
    if interaction_result:
        socketio.emit(
            'character_interaction_resolved',
            interaction_result,
            room=f"user_{interaction_result['target_user_id']}",
        )
    publish_character_save(actor_result, data['actor_updates'], participant.user_id)
    emit_character_update(
        {
            'character_id': character_id,
            'updates': {'data': target.character.data},
            'updated_by': participant.user_id,
        },
    )
    socketio.emit('combat_state_updated', CombatService.get_state(location_id, lobby.gm_id), room=f'location_{location_id}')
    return jsonify({'actor_data': actor_result.character.data_snapshot(), 'target_data': target.character.data_snapshot(), 'consumable_result': use_result}), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/character-interactions',
    methods=['POST'],
)
@jwt_required()
@requires_participant
def create_character_interaction(lobby_id, location_id, lobby, participant):
    data = request.get_json() or {}
    result = CharacterInteractionService.create_request(
        location_id,
        participant.user_id,
        data.get('actor_location_character_id'),
        data.get('target_character_id'),
        data.get('kind'),
        data.get('payload'),
    )
    if result['status'] == 'pending':
        socketio.emit(
            'character_interaction_requested',
            result,
            room=f"user_{result['target_user_id']}",
        )
    else:
        socketio.emit(
            'character_interaction_resolved',
            result,
            room=f"user_{result['actor_user_id']}",
        )
    if result['kind'] == 'trade' and result['status'] == 'completed':
        for location_character_id in (
            result['actor_location_character_id'],
            result['target_location_character_id'],
        ):
            location_character = db.session.get(LocationCharacter, location_character_id)
            if location_character and location_character.character:
                emit_character_update(
                    {
                        'character_id': location_character.character_id,
                        'updates': {'data': location_character.character.data},
                        'updated_by': participant.user_id,
                    },
                )
        state = CombatService.get_state(location_id, participant.user_id)
        socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    return jsonify(result), 201


@lobbies_bp.route(
    '/<int:lobby_id>/character-interactions/<int:request_id>/response',
    methods=['POST'],
)
@jwt_required()
@requires_participant
def respond_character_interaction(lobby_id, request_id, lobby, participant):
    data = request.get_json() or {}
    result = CharacterInteractionService.respond(
        request_id,
        participant.user_id,
        data.get('decision'),
    )
    socketio.emit(
        'character_interaction_resolved',
        result,
        room=f"user_{result['actor_user_id']}",
    )
    socketio.emit(
        'character_interaction_resolved',
        result,
        room=f"user_{result['target_user_id']}",
    )
    if result['kind'] == 'trade' and result['status'] == 'completed':
        actor = db.session.get(LocationCharacter, result['actor_location_character_id'])
        target = db.session.get(LocationCharacter, result['target_location_character_id'])
        for location_character in (actor, target):
            if location_character and location_character.character:
                emit_character_update(
                    {
                        'character_id': location_character.character_id,
                        'updates': {'data': location_character.character.data},
                        'updated_by': participant.user_id,
                    },
                )
        state = CombatService.get_state(result['location_id'], participant.user_id)
        socketio.emit('combat_state_updated', state, room=f"location_{result['location_id']}")
    return jsonify(result), 200


@lobbies_bp.route(
    '/<int:lobby_id>/character-interactions/<int:request_id>',
    methods=['DELETE'],
)
@jwt_required()
@requires_participant
def cancel_character_interaction(lobby_id, request_id, lobby, participant):
    request_row = db.session.get(CharacterInteractionRequest, request_id)
    if not request_row or not Location.query.filter_by(id=request_row.location_id, lobby_id=lobby_id).first():
        return jsonify({'error': 'Запрос взаимодействия не найден'}), 404
    result = CharacterInteractionService.cancel_request(request_id, participant.user_id)
    for recipient_id in {result['actor_user_id'], result['target_user_id']}:
        socketio.emit('character_interaction_resolved', result, room=f'user_{recipient_id}')
    return jsonify(result), 200


@lobbies_bp.route(
    '/<int:lobby_id>/character-interactions/<int:request_id>/progress',
    methods=['PATCH'],
)
@jwt_required()
@requires_participant
def start_character_treatment(lobby_id, request_id, lobby, participant):
    result = CharacterInteractionService.mark_treatment_in_progress(
        request_id,
        participant.user_id,
        (request.get_json(silent=True) or {}).get('pending_action_id'),
    )
    socketio.emit(
        'character_interaction_resolved',
        result,
        room=f"user_{result['target_user_id']}",
    )
    return jsonify(result), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat', methods=['GET'])
@jwt_required()
@requires_participant
def get_location_combat_state(lobby_id, location_id, lobby, participant):
    state = CombatService.get_state(location_id, participant.user_id)
    return jsonify(state), 200


def _combat_start_request_payload(start_request):
    actor = db.session.get(LocationCharacter, start_request.actor_location_character_id)
    return {
        'id': start_request.id,
        'location_id': start_request.location_id,
        'actor_location_character_id': start_request.actor_location_character_id,
        'actor_character_id': actor.character_id if actor else None,
        'actor_name': actor.character.name if actor and actor.character else 'Персонаж',
        'actor_user_id': start_request.actor_user_id,
        'status': start_request.status,
    }


def _resolve_combat_start_requests(location_id, *, approved_id=None):
    pending = CharacterInteractionRequest.query.filter_by(
        location_id=location_id, kind='combat_start', status='pending',
    ).all()
    for start_request in pending:
        start_request.status = 'approved' if start_request.id == approved_id else 'cancelled'
        start_request.resolved_at = datetime.now(timezone.utc)
    db.session.commit()
    for start_request in pending:
        payload = _combat_start_request_payload(start_request)
        for user_id in {start_request.actor_user_id, start_request.target_user_id}:
            socketio.emit('combat_start_request_resolved', payload, room=f'user_{user_id}')


def _publish_location_character_snapshots(location_id):
    # Начало хода тоже может изменить здоровье: обязательная атака при Пси ≥ 50.
    for loc_char in LocationCharacter.query.filter_by(location_id=location_id).all():
        character = loc_char.character
        if character and isinstance(character.data,dict):
            emit_character_update({'character_id':character.id,
                'updates':{'data':character.data_snapshot()},'updated_by':0})


def _publish_detonations(lobby_id, location_id, user_id, detonations, *, chat=True):
    """Одинаковые последствия мгновенного и отложенного взрыва для всех клиентов."""
    for detonation in detonations:
        if (detonation.get('explosion') or {}).get('awaiting_gm'):
            continue
        socketio.emit(
            'combat_explosion',
            {'location_id': location_id, 'explosive': detonation},
            room=f"location_{location_id}",
        )
        for damage in (detonation.get('explosion') or {}).get('objects') or []:
            object_id = damage.get('object_id')
            if not object_id:
                continue
            obj = db.session.get(LocationObject, object_id)
            payload = {'location_id': location_id}
            payload.update({'object': LocationObjectSchema().dump(obj)} if obj else {'object_id': object_id})
            socketio.emit('location_object_updated' if obj else 'location_object_deleted',
                          payload, room=f"location_{location_id}")
        summary = CombatService.format_explosion_summary({'explosive': detonation}) if chat else None
        if summary:
            _emit_lobby_chat_message(
                lobby_id, user_id, summary, username='\u0412\u0437\u0440\u044b\u0432',
            )


def _publish_combat_start(lobby_id, location_id, gm_id, state):
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    _publish_location_character_snapshots(location_id)
    _publish_detonations(lobby_id, location_id, gm_id, state.get('detonations') or [])
    participants = [
        character
        for character in (state.get('characters') or [])
        if character.get('initiative_roll') is not None
    ]
    participants.sort(
        key=lambda character: character.get('initiative_total') or 0,
        reverse=True,
    )
    initiative_lines = ['Инициатива:']
    for character in participants:
        bonus = character.get('initiative_bonus') or 0
        initiative_lines.append(
            f"{character.get('name') or 'Персонаж'}: "
            f"d20 {character.get('initiative_roll')} "
            f"{bonus:+d} = {character.get('initiative_total')}"
        )
    _emit_lobby_chat_message(
        lobby_id,
        gm_id,
        '\n'.join(initiative_lines),
    )


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/start-requests', methods=['POST'])
@jwt_required()
@requires_participant
def request_location_combat_start(lobby_id, location_id, lobby, participant):
    location = Location.query.filter_by(id=location_id, lobby_id=lobby_id).first()
    if not location:
        raise NotFoundError('Подлокация не найдена')
    data = request.get_json(silent=True) or {}
    actor_id = CombatService._coerce_int(data.get('actor_location_character_id'), 0)
    actor = LocationCharacter.query.filter_by(id=actor_id, location_id=location_id).first()
    if not actor or not CombatService._can_end_turn_for_character(actor, participant.user_id):
        raise PermissionDenied('Вы не управляете этим персонажем')
    if CombatService._location_character_condition(actor)['state'] != 'active':
        raise ValidationError('Только персонаж в сознании может начать бой действием')
    state = LocationCombatState.query.filter_by(location_id=location_id).first()
    if state and state.status == 'active':
        raise ConflictError('Бой уже начался')
    existing = CharacterInteractionRequest.query.filter_by(
        location_id=location_id, actor_location_character_id=actor.id,
        kind='combat_start', status='pending',
    ).first()
    if existing:
        return jsonify(_combat_start_request_payload(existing)), 200
    start_request = CharacterInteractionRequest(
        location_id=location_id,
        actor_location_character_id=actor.id,
        target_location_character_id=actor.id,
        actor_user_id=participant.user_id,
        target_user_id=lobby.gm_id,
        kind='combat_start',
        status='pending',
        payload={},
    )
    db.session.add(start_request)
    db.session.commit()
    payload = _combat_start_request_payload(start_request)
    socketio.emit('combat_start_requested', payload, room=f'user_{lobby.gm_id}')
    return jsonify(payload), 201


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/start-requests', methods=['GET'])
@jwt_required()
@requires_gm
def list_location_combat_start_requests(lobby_id, location_id, lobby):
    if not Location.query.filter_by(id=location_id, lobby_id=lobby_id).first():
        raise NotFoundError('Подлокация не найдена')
    pending = CharacterInteractionRequest.query.filter_by(
        location_id=location_id, kind='combat_start', status='pending',
    ).order_by(CharacterInteractionRequest.created_at).all()
    return jsonify([_combat_start_request_payload(item) for item in pending]), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/start-requests/<int:request_id>/response', methods=['POST'])
@jwt_required()
@requires_gm
def respond_location_combat_start_request(lobby_id, location_id, request_id, lobby):
    if not Location.query.filter_by(id=location_id, lobby_id=lobby_id).first():
        raise NotFoundError('Подлокация не найдена')
    start_request = CharacterInteractionRequest.query.filter_by(
        id=request_id, location_id=location_id, kind='combat_start',
    ).first()
    if not start_request:
        raise NotFoundError('Запрос на начало боя не найден')
    if start_request.status != 'pending':
        raise ConflictError('Запрос уже обработан')
    data = request.get_json(silent=True) or {}
    decision = data.get('decision')
    if decision not in {'approve', 'reject'}:
        raise ValidationError('Выберите подтверждение или отказ')
    if decision == 'reject':
        start_request.status = 'rejected'
        start_request.resolved_at = datetime.now(timezone.utc)
        db.session.commit()
        payload = _combat_start_request_payload(start_request)
        for user_id in {start_request.actor_user_id, lobby.gm_id}:
            socketio.emit('combat_start_request_resolved', payload, room=f'user_{user_id}')
        return jsonify(payload), 200
    actor = LocationCharacter.query.filter_by(
        id=start_request.actor_location_character_id, location_id=location_id,
    ).first()
    if not actor or CombatService._location_character_condition(actor)['state'] != 'active':
        raise ValidationError('Инициатор больше не может начать бой действием')
    selected_ids = data.get('location_character_ids')
    if selected_ids is not None and (
        not isinstance(selected_ids, list)
        or start_request.actor_location_character_id not in selected_ids
    ):
        raise ValidationError('Атакующий должен участвовать в бою')
    initiator_first = data.get('initiator_first', True)
    if not isinstance(initiator_first, bool):
        raise ValidationError('Укажите, должен ли атакующий ходить первым')
    state = CombatService.start_combat(
        location_id,
        lobby.gm_id,
        location_character_ids=selected_ids,
        initiator_location_character_id=start_request.actor_location_character_id,
        initiator_first=initiator_first,
    )
    _resolve_combat_start_requests(location_id, approved_id=start_request.id)
    _publish_combat_start(lobby_id, location_id, lobby.gm_id, state)
    return jsonify(state), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/start', methods=['POST'])
@jwt_required()
@requires_gm
def start_location_combat(lobby_id, location_id, lobby):
    data = request.get_json(silent=True) or {}
    state = CombatService.start_combat(
        location_id,
        lobby.gm_id,
        location_character_ids=data.get('location_character_ids'),
        initiator_location_character_id=data.get('initiator_location_character_id'),
        initiator_first=data.get('initiator_first', True),
    )
    _resolve_combat_start_requests(location_id)
    _publish_combat_start(lobby_id, location_id, lobby.gm_id, state)
    return jsonify(state), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/end_turn', methods=['POST'])
@jwt_required()
@requires_participant
def end_location_combat_turn(lobby_id, location_id, lobby, participant):
    data = request.get_json() or {}
    state = CombatService.end_turn(
        location_id,
        participant.user_id,
        location_character_id=data.get('location_character_id'),
    )
    anomaly = state.get('anomaly')
    if isinstance(anomaly, dict):
        _emit_lobby_chat_message(
            lobby_id,
            participant.user_id,
            f"{anomaly.get('name') or '\u0410\u043d\u043e\u043c\u0430\u043b\u0438\u044f'}: \u043f\u043e\u043b\u043d\u043e\u0435 \u0432\u043e\u0437\u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0432 \u043a\u043e\u043d\u0446\u0435 \u0445\u043e\u0434\u0430, "
            f"\u0443\u0440\u043e\u043d {anomaly.get('damage', 0)}.",
            username='\u0410\u043d\u043e\u043c\u0430\u043b\u0438\u044f',
        )
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    _publish_location_character_snapshots(location_id)
    _publish_detonations(lobby_id, location_id, participant.user_id, state.get('detonations') or [])
    return jsonify(state), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/explosions/<string:event_id>/targets/<int:actor_id>', methods=['POST'])
@jwt_required()
@requires_gm
def decide_explosion_target(lobby_id, location_id, event_id, actor_id, lobby):
    from app.services.explosion_choice import decide
    location=CombatService._get_location(location_id)
    if location.lobby_id!=lobby_id:
        raise NotFoundError('Location not found')
    state=decide(location_id,lobby.gm_id,event_id,actor_id,(request.get_json() or {}).get('choice'))
    socketio.emit('combat_state_updated',state,room=f'location_{location_id}')
    _publish_location_character_snapshots(location_id)
    _publish_detonations(lobby_id,location_id,lobby.gm_id,state.get('detonations') or [])
    return jsonify(state),200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/combat/participants/<int:location_character_id>',
    methods=['POST'],
)
@jwt_required()
@requires_gm
def add_location_combat_participant(
    lobby_id, location_id, location_character_id, lobby,
):
    state = CombatService.add_combat_participant(
        location_id, lobby.gm_id, location_character_id,
    )
    socketio.emit('combat_state_updated', state, room=f'location_{location_id}')
    return jsonify(state), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/combat/participants/<int:location_character_id>',
    methods=['DELETE'],
)
@jwt_required()
@requires_gm
def remove_location_combat_participant(
    lobby_id,
    location_id,
    location_character_id,
    lobby,
):
    state = CombatService.remove_combat_participant(
        location_id,
        lobby.gm_id,
        location_character_id,
    )
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    _publish_location_character_snapshots(location_id)
    return jsonify(state), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/gm-events', methods=['POST'])
@jwt_required()
@requires_gm
def apply_location_gm_event(lobby_id, location_id, lobby):
    """Apply a small, auditable GM event to selected location characters."""
    data = request.get_json(silent=True) or {}
    event_type = str(data.get('type') or '').strip().lower()
    if event_type not in {'stress', 'fall'}:
        return jsonify({'error': 'Unsupported GM event'}), 400
    raw_ids = data.get('location_character_ids') or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({'error': 'Choose at least one character'}), 400
    try:
        character_ids = list({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid character list'}), 400
    characters = LocationCharacter.query.filter(
        LocationCharacter.location_id == location_id,
        LocationCharacter.id.in_(character_ids),
    ).all()
    if len(characters) != len(character_ids):
        return jsonify({'error': 'One or more characters are not on this location'}), 404

    amount = 0
    height_meters = 0.0
    if event_type == 'stress':
        try:
            amount = int(data.get('amount', 1))
        except (TypeError, ValueError):
            return jsonify({'error': 'Stress amount must be a number'}), 400
        if amount == 0 or abs(amount) > 10:
            return jsonify({'error': 'Stress amount must be from -10 to 10, excluding zero'}), 400
    else:
        try:
            height_meters = float(data.get('height_meters'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Fall height must be a number'}), 400
        if height_meters < 0 or height_meters > 1000:
            return jsonify({'error': 'Fall height must be from 0 to 1000 meters'}), 400

    note = ' '.join(str(data.get('note') or '').split())[:240]
    changed = []
    for location_character in characters:
        character = location_character.character
        if not character:
            continue
        character_data = character.data if isinstance(character.data, dict) else {}
        health = apply_health_maximums(character_data)
        fall_result = None
        stress_result = None
        if event_type == 'stress':
            stress_result = CombatService.apply_stress_trigger(
                location_character,
                amount,
                trigger=str(data.get('stress_trigger') or 'gm_event'),
                force_manifest=bool(data.get('force_manifest')),
            )
            character_data = character.data if isinstance(character.data, dict) else {}
            health = character_data.setdefault('health', {})
        else:
            combat_state = LocationCombatState.query.filter_by(location_id=location_id).first()
            fall_result = CombatService.resolve_fall(
                location_character,
                height_meters,
                round_number=(combat_state.round_number if combat_state and combat_state.status == 'active' else 0),
            )
            character_data = character.data if isinstance(character.data, dict) else {}
            health = character_data.setdefault('health', {})
        sync_health_derived_statuses(health)
        character_data['health'] = health
        character.data = character_data
        flag_modified(character, 'data')
        changed.append({
            'id': location_character.id,
            'character_id': character.id,
            'name': character.name,
            'stress': health.get('stress', 0),
            'posture': location_character.posture,
            'fall': fall_result,
            'stress_result': stress_result,
        })

    db.session.commit()
    for item in changed:
        character = db.session.get(LobbyCharacter, item['character_id'])
        emit_character_update(
            {
                'character_id': item['character_id'],
                'updates': {'data': character.data_snapshot()},
                'updated_by': lobby.gm_id,
            },
        )
    state = CombatService.get_state(location_id, lobby.gm_id)
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    names = ', '.join(item['name'] or 'Персонаж' for item in changed)
    event_label = f"Стресс {amount:+d}" if event_type == 'stress' else f"Падение с {height_meters:g} м"
    fall_lines = []
    stress_lines = []
    if event_type == 'fall':
        for item in changed:
            result = item.get('fall') or {}
            outcome = 'успех' if result.get('success') else 'провал'
            fall_lines.append(
                f"{item['name'] or 'Персонаж'}: d20 {result.get('roll')} "
                f"{result.get('agility_bonus', 0):+d} = {result.get('total')} против {result.get('difficulty')} ({outcome})"
            )
    if event_type == 'stress':
        for item in changed:
            result = item.get('stress_result') or {}
            if result.get('blocked'):
                stress_lines.append(f"{item['name'] or 'Персонаж'}: стресс заблокирован эффектом")
            elif result.get('manifested'):
                stress_lines.append(
                    f"{item['name'] or 'Персонаж'}: Воля d20 {result.get('roll')} {result.get('will_bonus', 0):+d} = {result.get('total')} против {result.get('difficulty')}; проявление: {result.get('effect')} (к{result.get('sides')}, {result.get('effect_roll')})"
                )
            else:
                stress_lines.append(
                    f"{item['name'] or 'Персонаж'}: Воля d20 {result.get('roll')} {result.get('will_bonus', 0):+d} = {result.get('total')} против {result.get('difficulty')}; проявления нет"
                )
    _emit_lobby_chat_message(
        lobby_id,
        lobby.gm_id,
        f"Событие ГМа: {event_label}. Цели: {names}." + (f" {note}" if note else '')
        + ("\n" + "\n".join(fall_lines + stress_lines) if fall_lines or stress_lines else ''),
        username='ГМ',
    )
    return jsonify({'characters': changed, 'combat_state': state}), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/stress-effects/resolve', methods=['POST'])
@jwt_required()
@requires_gm
def resolve_location_stress_effect(lobby_id, location_id, lobby):
    data = request.get_json(silent=True) or {}
    loc_char = LocationCharacter.query.filter_by(
        id=data.get('location_character_id'), location_id=location_id,
    ).first()
    if not loc_char or not loc_char.character:
        return jsonify({'error': 'Character not found'}), 404
    CombatService.resolve_stress_effect(
        loc_char,
        data.get('effect_id'),
        str(data.get('action') or ''),
        replacement=data.get('replacement'),
        effect_name=data.get('effect_name'),
        stress_table=data.get('stress_table'),
        stress_roll=data.get('stress_roll'),
    )
    db.session.commit()
    state = CombatService.get_state(location_id, lobby.gm_id)
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    emit_character_update(
        {
            'character_id': loc_char.character.id,
            'updates': {'data': loc_char.character.data_snapshot()},
            'source': 'stress_resolution',
        },
    )
    return jsonify(state), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/characters/<int:character_id>/stress',
    methods=['POST'],
)
@jwt_required()
@requires_gm
def adjust_location_character_stress(lobby_id, location_id, character_id, lobby):
    data = request.get_json(silent=True) or {}
    amount = data.get('amount')
    if amount not in {-1, 1}:
        return jsonify({'error': 'Stress adjustment must be -1 or 1'}), 400
    loc_char = LocationCharacter.query.filter_by(
        location_id=location_id,
        character_id=character_id,
    ).first()
    if not loc_char or not loc_char.character:
        return jsonify({'error': 'Character is not on this location'}), 404
    result = CombatService.apply_stress_trigger(
        loc_char,
        amount,
        trigger='gm_quick_adjustment',
    )
    db.session.commit()
    state = CombatService.get_state(location_id, lobby.gm_id)
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    emit_character_update(
        {
            'character_id': loc_char.character.id,
            'updates': {'data': loc_char.character.data_snapshot()},
            'source': 'stress_adjustment',
        },
    )
    return jsonify({
        'stress': result,
        'data': loc_char.character.data_snapshot(),
        'combat_state': state,
    }), 200


@lobbies_bp.route('/<int:lobby_id>/characters/<int:character_id>/stress', methods=['POST'])
@jwt_required()
@requires_gm
def adjust_character_stress(lobby_id, character_id, lobby):
    data = request.get_json(silent=True) or {}
    amount = data.get('amount')
    if type(amount) is not int or amount not in {-1, 1}:
        raise ValidationError('Изменение стресса должно быть -1 или 1')
    character = LobbyCharacter.query.filter_by(id=character_id, lobby_id=lobby_id).first()
    if not character:
        raise NotFoundError('Персонаж не найден')
    result = CombatService.apply_stress_trigger(
        SimpleNamespace(character=character), amount, trigger='gm_quick_adjustment',
    )
    db.session.commit()
    emit_character_update({
        'character_id': character.id,
        'updates': {'data': character.data_snapshot()},
        'source': 'stress_adjustment',
    })
    for location_id in {
        row.location_id for row in LocationCharacter.query.filter_by(character_id=character.id).all()
    }:
        socketio.emit(
            'combat_state_updated', CombatService.get_state(location_id, lobby.gm_id),
            room=f'location_{location_id}',
        )
    return jsonify({'stress': result, 'data': character.data_snapshot()}), 200


@lobbies_bp.route('/<int:lobby_id>/characters/<int:character_id>/restore-player', methods=['POST'])
@jwt_required()
@requires_gm
def restore_player_character_by_gm(lobby_id,character_id,lobby):
    character=LobbyCharacter.query.filter_by(id=character_id,lobby_id=lobby_id).first()
    if not character:raise NotFoundError('Персонаж не найден')
    result=CharacterService.restore_player_sheet(character)
    flag_modified(character,'data')
    db.session.commit()
    emit_character_update({'character_id':character.id,'updates':{'data':character.data_snapshot()},'source':'gm_restore_player_sheet'})
    socketio.emit('character_updated',{'id':character.id},room=f'lobby_{lobby_id}')
    models=LocationCharacter.query.filter_by(character_id=character.id).all()
    for location_id in {model.location_id for model in models}:
        model=CombatService._unique_location_characters([m for m in models if m.location_id==location_id])[0]
        state=CombatService.get_state(model.location_id,lobby.gm_id)
        row=next(c for c in state['characters'] if c['location_character_id']==model.id)
        socketio.emit('character_spawned',{'action':'moved','character':{
            **row,'id':character.id,'pos_x':model.pos_x,'pos_y':model.pos_y,
            'hp_zones':model.hp_zones,'effects':model.effects,
        }},room=f'location_{model.location_id}')
        socketio.emit('combat_state_updated',state,room=f'location_{model.location_id}')
    return jsonify({'data':character.data_snapshot()}),200


@lobbies_bp.route('/<int:lobby_id>/characters/<int:character_id>/heal', methods=['POST'])
@jwt_required()
@requires_gm
def heal_character_by_gm(lobby_id, character_id, lobby):
    character = LobbyCharacter.query.filter_by(id=character_id, lobby_id=lobby_id).first()
    if not character:
        raise NotFoundError('Персонаж не найден')
    character_data = deepcopy(character.data or {})
    health = heal_character_fully(character_data)
    character.data = character_data
    flag_modified(character, 'data')
    posture_updates = CharacterService.sync_location_health(character, health)
    db.session.commit()
    emit_character_update({
        'character_id': character.id,
        'updates': {'data': character.data_snapshot()},
        'source': 'gm_full_heal',
    })
    for update in posture_updates:
        socketio.emit('location_character_posture_updated', update, room=f"location_{update['location_id']}")
    for location_id in {
        row.location_id for row in LocationCharacter.query.filter_by(character_id=character.id).all()
    }:
        socketio.emit(
            'combat_state_updated', CombatService.get_state(location_id, lobby.gm_id),
            room=f'location_{location_id}',
        )
    return jsonify({'data': character.data_snapshot()}), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/reaction/reserve', methods=['POST'])
@jwt_required()
@requires_participant
def reserve_location_combat_reaction(lobby_id, location_id, lobby, participant):
    data = request.get_json() or {}
    state = CombatService.reserve_reaction(
        location_id,
        participant.user_id,
        data.get('location_character_id'),
        action_points=data.get('action_points', 0),
        free_actions=data.get('free_actions', 0),
        movement_points=data.get('movement_points', 0),
        trigger=data.get('trigger', ''),
        kind=data.get('kind', 'reaction'),
        help_target_character_id=data.get('help_target_character_id'),
        help_action_label=data.get('help_action_label', ''),
        help_skill_path=data.get('help_skill_path', ''),
    )
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    return jsonify(state), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/reaction/request', methods=['POST'])
@jwt_required()
@requires_participant
def request_location_combat_reaction(lobby_id, location_id, lobby, participant):
    data = request.get_json() or {}
    state = CombatService.request_reaction(
        location_id,
        participant.user_id,
        data.get('location_character_id'),
    )
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    return jsonify(state), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/reaction/resolve', methods=['POST'])
@jwt_required()
@requires_gm
def resolve_location_combat_reaction(lobby_id, location_id, lobby):
    data = request.get_json() or {}
    state = CombatService.resolve_reaction_request(
        location_id,
        lobby.gm_id,
        data.get('approve') is True,
    )
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    return jsonify(state), 200


@lobbies_bp.route(
    '/<int:lobby_id>/locations/<int:location_id>/combat/opportunity-attack',
    methods=['POST'],
)
@jwt_required()
@requires_participant
def resolve_location_opportunity_attack(lobby_id, location_id, lobby, participant):
    data = request.get_json() or {}
    result = CombatService.resolve_opportunity_attack(
        location_id,
        participant.user_id,
        data.get('location_character_id'),
        data.get('opportunity_id'),
        data.get('accept') is True,
        weapon_index=data.get('weapon_index'),
        attack_type=data.get('attack_type'),
    )
    socketio.emit('combat_character_updated', result['character'], room=f"location_{location_id}")
    if result.get('target'):
        socketio.emit('combat_character_updated', result['target'], room=f"location_{location_id}")
    socketio.emit('combat_state_updated', result['state'], room=f"location_{location_id}")
    for serialized in (result.get('character'), result.get('target')):
        character_id = serialized.get('character_id') if isinstance(serialized, dict) else None
        character = db.session.get(LobbyCharacter, character_id) if character_id else None
        if character:
            emit_character_update(
                {
                    'character_id': character.id,
                    'updates': {'data': character.data_snapshot()},
                    'source': 'opportunity_attack',
                },
            )
    if result.get('attack'):
        summary = CombatService.format_attack_summary(result)
        if summary:
            _emit_lobby_chat_message(lobby_id, participant.user_id, summary)
    return jsonify(result), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/end', methods=['POST'])
@jwt_required()
@requires_gm
def end_location_combat(lobby_id, location_id, lobby):
    state = CombatService.end_combat(location_id, lobby.gm_id)
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    characters = LocationCharacter.query.filter_by(location_id=location_id).all()
    for loc_char in characters:
        character = loc_char.character
        if not character or not isinstance(character.data, dict):
            continue
        emit_character_update(
            {
                'character_id': character.id,
                'updates': {'data': character.data_snapshot()},
                'updated_by': 0,
            },
        )
    return jsonify(state), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/spend', methods=['POST'])
@jwt_required()
@requires_participant
def spend_location_combat_resources(lobby_id, location_id, lobby, participant):
    if not Location.query.filter_by(id=location_id, lobby_id=lobby_id).first():
        return jsonify({'error': 'Location not found'}), 404
    data = request.get_json() or {}
    location_character_id = data.get('location_character_id')
    if not location_character_id:
        return jsonify({'error': 'location_character_id is required'}), 400

    updated_character = CombatService.spend_resources(
        location_id,
        participant.user_id,
        location_character_id=location_character_id,
        action_points=data.get('action_points', 0),
        free_actions=data.get('free_actions', 0),
        movement_points=data.get('movement_points', 0),
        allow_deferred=bool(data.get('allow_deferred')),
        pending_action_id=data.get('pending_action_id'),
        pending_action_label=data.get('pending_action_label'),
        consumable_selection=data.get('consumable_selection'),
    )
    state = updated_character.pop('state', None) or CombatService.get_state(location_id, participant.user_id)
    socketio.emit('combat_character_updated', updated_character, room=f"location_{location_id}")
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    return jsonify(updated_character), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/deferred-consumable/<string:action_id>', methods=['GET'])
@jwt_required()
@requires_participant
def prepare_deferred_consumable(lobby_id, location_id, action_id, lobby, participant):
    from app.services.deferred_consumable import preparation
    if not Location.query.filter_by(id=location_id, lobby_id=lobby_id).first():
        return jsonify({'error': 'Location not found'}), 404
    return jsonify(preparation(action_id, participant.user_id, location_id)), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/adjust', methods=['POST'])
@jwt_required()
@requires_participant
def adjust_location_combat_resources(lobby_id, location_id, lobby, participant):
    data = request.get_json() or {}
    location_character_id = data.get('location_character_id')
    if not location_character_id:
        return jsonify({'error': 'location_character_id is required'}), 400
    updated_character = CombatService.adjust_resources(
        location_id,
        participant.user_id,
        location_character_id=location_character_id,
        action_points=data.get('action_points', 0),
        movement_points=data.get('movement_points', 0),
    )
    state = CombatService.get_state(location_id, participant.user_id)
    socketio.emit('combat_character_updated', updated_character, room=f"location_{location_id}")
    socketio.emit('combat_state_updated', state, room=f"location_{location_id}")
    return jsonify(updated_character), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/deferred-action/cancel', methods=['POST'])
@jwt_required()
@requires_participant
def cancel_location_deferred_action(lobby_id, location_id, lobby, participant):
    from app.services.deferred_action import cancel_deferred_action
    data = request.get_json() or {}
    actor = LocationCharacter.query.filter_by(id=data.get('location_character_id'), location_id=location_id).first()
    if not actor or actor.location.lobby_id != lobby_id:
        return jsonify({'error': 'Character not found'}), 404
    if not CombatService._can_end_turn_for_character(actor, participant.user_id, is_gm=lobby.gm_id == participant.user_id):
        raise PermissionDenied('Вы не управляете этим персонажем')
    cancel_deferred_action(actor, data.get('action_id'))
    db.session.commit()
    emit_character_update({'character_id': actor.character_id, 'updates': {'data': actor.character.data}, 'source': 'combat'})
    state = CombatService.get_state(location_id, participant.user_id)
    socketio.emit('combat_state_updated', state, room=f'location_{location_id}')
    return jsonify(state), 200


@lobbies_bp.route('/<int:lobby_id>/locations/<int:location_id>/combat/action', methods=['POST'])
@jwt_required()
@requires_participant
def perform_location_combat_action(lobby_id, location_id, lobby, participant):
    if not Location.query.filter_by(id=location_id, lobby_id=lobby_id).first():
        return jsonify({'error': 'Location not found'}), 404
    data = request.get_json() or {}
    location_character_id = data.get('location_character_id')
    action_key = data.get('action_key')
    if not location_character_id or not action_key:
        return jsonify({'error': 'location_character_id and action_key are required'}), 400

    result = CombatService.perform_action(
        location_id,
        participant.user_id,
        location_character_id=location_character_id,
        action_key=action_key,
        weapon_index=data.get('weapon_index'),
        fire_mode=data.get('fire_mode'),
        shot_count=data.get('shot_count'),
        volley_count=data.get('volley_count'),
        action_points=data.get('action_points'),
        target_character_id=data.get('target_character_id'),
        target_character_ids=data.get('target_character_ids'),
        target_object_id=data.get('target_object_id'),
        area_center_x=data.get('area_center_x'),
        area_center_y=data.get('area_center_y'),
        target_x=data.get('target_x'),
        target_y=data.get('target_y'),
        target_floor_level=data.get('target_floor_level'),
        posture=data.get('posture'),
        facing_x=data.get('facing_x'),
        facing_y=data.get('facing_y'),
        attack_type=data.get('attack_type'),
        target_zone=data.get('target_zone'),
        payment=data.get('payment'),
        magazine_template_id=data.get('magazine_template_id'),
        magazine_selection=data.get('magazine_selection'),
        loading=data.get('loading'),
        inventory_retrieval_action_points=data.get('inventory_retrieval_action_points'),
        inventory_use_action_discount=data.get('inventory_use_action_discount'),
        attribute_choice=data.get('attribute_choice'),
        pending_action_id=data.get('pending_action_id'),
        resume_pending_action_id=data.get('resume_pending_action_id'),
        narrative_action_name=data.get('narrative_action_name'),
        narrative_skill_path=data.get('narrative_skill_path'),
        narrative_roll_required=data.get('narrative_roll_required') is True,
        narrative_difficulty=data.get('narrative_difficulty'),
        item_path=data.get('item_path'),
        explosive_source=data.get('explosive_source'),
        explosive_fire_mode=data.get('explosive_fire_mode'),
        explosive_fuse_mode=data.get('explosive_fuse_mode'),
        equipment_operation=data.get('equipment_operation'),
        equipment_slot=data.get('equipment_slot'),
        module_operation=data.get('module_operation'),
        module_slot_type=data.get('module_slot_type'),
        module_target_path=data.get('module_target_path'),
        module_selection=data.get('module_selection'),
    )
    socketio.emit('combat_character_updated', result['character'], room=f"location_{location_id}")
    socketio.emit('combat_state_updated', result['state'], room=f"location_{location_id}")
    if result.get('jump'):
        actor = result['character']
        socketio.emit('character_moved',{'character_id':actor['character_id'],
            'x':actor['x'],'y':actor['y'],'floor_level':actor['floor_level'],'height':actor['height'],
            'facing_x':actor['facing_x'],'facing_y':actor['facing_y'],
            'path':result['jump']['path'],'jump':True},room=f'location_{location_id}')
    ground_object_events = {}
    pending_nodes = [result]
    while pending_nodes:
        node = pending_nodes.pop()
        if isinstance(node, dict):
            revealed = node.get('revealed_anomaly')
            if isinstance(revealed,dict):
                if revealed.get('object_id'):
                    ground_object_events[int(revealed['object_id'])]='updated'
                elif revealed.get('tile_x') is not None and revealed.get('tile_y') is not None:
                    location = db.session.get(Location,location_id)
                    x,y=revealed['tile_x'],revealed['tile_y']
                    socketio.emit('location_tiles_updated',{'location_id':location_id,'updates':[
                        {'x':x,'z':y,'objects':location.tiles_data[y][x].get('objects',[])}]},room=f'location_{location_id}')
            object_id = node.get('ground_object_id')
            object_event = node.get('ground_object_event')
            if object_id and object_event:
                ground_object_events[int(object_id)] = object_event
            pending_nodes.extend(node.values())
        elif isinstance(node, list):
            pending_nodes.extend(node)
    for object_id, object_event in ground_object_events.items():
        ground_object = db.session.get(LocationObject, object_id)
        if not ground_object:
            continue
        socketio.emit(
            'location_object_created' if object_event == 'created' else 'location_object_updated',
            {
                'location_id': location_id,
                'object': LocationObjectSchema().dump(ground_object),
            },
            room=f"location_{location_id}",
        )
    if result.get('explosive') and result['explosive'].get('detonated', True):
        _publish_detonations(lobby_id, location_id, participant.user_id, [result['explosive']], chat=False)
    reaction_detonations = (result.get('explosive') or {}).get('reaction_detonations') or []
    _publish_detonations(lobby_id, location_id, participant.user_id, reaction_detonations)
    actor = db.session.get(LobbyCharacter, result['character'].get('character_id'))
    if actor:
        emit_character_update(
            {
                'character_id': actor.id,
                'updates': {'data': actor.data},
                'source': 'combat',
            },
        )
    medical_retry_result = (result.get('must_do_it') or {}).get('medical_result')
    if isinstance(medical_retry_result, dict):
        posture_updates = medical_retry_result.pop('_posture_updates', [])
        target = db.session.get(LobbyCharacter, medical_retry_result.get('target_character_id'))
        if target and (not actor or target.id != actor.id):
            emit_character_update({
                'character_id': target.id,
                'updates': {'data': target.data},
                'source': 'medical_procedure',
            })
        for posture_update in posture_updates:
            socketio.emit(
                'location_character_posture_updated', posture_update,
                room=f"location_{posture_update['location_id']}",
            )
    mutant_action = result.get('mutant_action')
    if isinstance(mutant_action, dict) and isinstance(mutant_action.get('clone'), dict):
        clone = mutant_action['clone']
        socketio.emit(
            'character_spawned',
            {
                'action': 'spawned',
                'character': {
                    'id': clone.get('character_id'),
                    'name': clone.get('name'),
                    'owner_id': clone.get('owner_id'),
                    'owner_username': clone.get('owner_username'),
                    'controlled_by': clone.get('controlled_by'),
                    'team_name': clone.get('team_name'),
                    'team_color': clone.get('team_color'),
                    'hp_zones': clone.get('hp_zones'),
                    'effects': clone.get('effects'),
                    'pos_x': clone.get('x'),
                    'pos_y': clone.get('y'),
                    'facing_x': clone.get('facing_x'),
                    'facing_y': clone.get('facing_y'),
                    'is_mutant': clone.get('is_mutant'),
                    'mutant_type': clone.get('mutant_type'),
                },
            },
            room=f"location_{location_id}",
        )
    evaporated_clones = []
    for source in (result.get('attack'), result.get('explosive'), mutant_action):
        if isinstance(source, dict):
            evaporated_clones.extend(source.get('evaporated_clones') or [])
    for clone in {item.get('character_id'): item for item in evaporated_clones if item.get('character_id')}.values():
        socketio.emit(
            'location_character_removed',
            {
                'location_id': location_id,
                'character_id': clone.get('character_id'),
            },
            room=f"location_{location_id}",
        )
    if isinstance(mutant_action, dict) and mutant_action.get('kind') == 'battle_cry':
        for target_result in mutant_action.get('targets') or []:
            target_character = db.session.get(
                LobbyCharacter, target_result.get('character_id'),
            )
            if not target_character:
                continue
            emit_character_update(
                {
                    'character_id': target_character.id,
                    'updates': {'data': target_character.data},
                    'source': 'mutant_battle_cry',
                },
            )
        checks = '; '.join(
            f"{item.get('name')}: d20 {item.get('roll')} "
            f"{item.get('modifier', 0):+d} = {item.get('total')} против СЛ 15 - "
            f"{'успех' if item.get('success') else 'провал, стресс +1'}"
            for item in mutant_action.get('targets') or []
        ) or 'подходящих целей нет'
        _emit_lobby_chat_message(
            lobby_id,
            participant.user_id,
            f"Боевой клич. {checks}.",
            username='Мутант',
        )
    if isinstance(mutant_action, dict) and mutant_action.get('kind') in {
        'psy_attack', 'psy_roar', 'psy_sleep', 'object_attack', 'steal_device',
    }:
        affected_ids = {
            int(value)
            for value in [mutant_action.get('target_character_id')]
            if value is not None
        }
        affected_ids.update(
            int(item['character_id'])
            for item in mutant_action.get('targets') or []
            if isinstance(item, dict) and item.get('character_id') is not None
        )
        for affected_id in affected_ids:
            target_character = db.session.get(LobbyCharacter, affected_id)
            if not target_character:
                continue
            emit_character_update(
                {
                    'character_id': target_character.id,
                    'updates': {'data': target_character.data},
                    'source': mutant_action.get('kind'),
                },
            )

        if mutant_action.get('kind') == 'psy_attack':
            save = mutant_action.get('save') or {}
            message = (
                f"{mutant_action.get('attack_name') or 'Пси-атака'}: "
                f"Воля d20 {save.get('roll')} "
                f"{save.get('modifier', 0):+d} = {save.get('total')} "
                f"против СЛ {save.get('difficulty')} - "
                f"{'успех' if save.get('success') else 'провал'}."
            )
        elif mutant_action.get('kind') == 'psy_sleep':
            save = mutant_action.get('save') or {}
            outcome = 'успех' if save.get('success') else mutant_action.get('duration', 'сон')
            message = (
                f"Пси-сон: Воля d20 {save.get('roll')} "
                f"{save.get('modifier', 0):+d} = {save.get('total')} "
                f"против СЛ {save.get('difficulty')} - {outcome}."
            )
        elif mutant_action.get('kind') == 'psy_roar':
            target_lines = []
            for item in mutant_action.get('targets') or []:
                save = item.get('save') or {}
                outcome = 'успех' if save.get('success') else 'провал'
                target_lines.append(
                    f"{item.get('name')}: {save.get('total')} против СЛ 16 - {outcome}"
                )
            message = 'Пси-рёв. ' + ('; '.join(target_lines) or 'Целей нет.')
        elif mutant_action.get('kind') == 'object_attack':
            save = mutant_action.get('save') or {}
            outcome = (
                'цель увернулась'
                if save.get('success')
                else f"урон {round(mutant_action.get('damage') or 0)}"
            )
            message = (
                f"{mutant_action.get('attack_name')}: "
                f"{mutant_action.get('object_name')} "
                f"({mutant_action.get('object_weight')} кг), "
                f"Ловкость {save.get('total')} против СЛ {save.get('difficulty')} - "
                f"{outcome}."
            )
        else:
            message = (
                f"Электроворона украла "
                f"{(mutant_action.get('stolen_item') or {}).get('name') or mutant_action.get('item_name')} "
                f"у {mutant_action.get('target_name')}."
            )
        _emit_lobby_chat_message(
            lobby_id,
            participant.user_id,
            message,
            username='Мутант',
        )

    attack_summary = CombatService.format_attack_summary(result)
    if attack_summary:
        _emit_lobby_chat_message(
            lobby_id,
            participant.user_id,
            attack_summary,
        )
    explosion_summary = (
        CombatService.format_explosion_summary(result)
        if not result.get('explosive') or result['explosive'].get('detonated', True)
        else None
    )
    if explosion_summary:
        _emit_lobby_chat_message(
            lobby_id,
            participant.user_id,
            explosion_summary,
            username='\u0412\u0437\u0440\u044b\u0432',
        )
    narrative_summary = CombatService.format_narrative_action_summary(result)
    if narrative_summary:
        _emit_lobby_chat_message(
            lobby_id,
            participant.user_id,
            narrative_summary,
            username='Действие',
        )
    must_do = result.get('must_do_it')
    if isinstance(must_do, dict) and isinstance(must_do.get('check'), dict):
        check = must_do['check']
        total = check.get('total')
        if total is None and check.get('roll') is not None:
            total = check.get('roll')
        _emit_lobby_chat_message(
            lobby_id, participant.user_id,
            f"Должен это сделать: {must_do.get('name')}. d20 {check.get('roll')}, "
            f"итог {total} против СЛ {check.get('difficulty')}: "
            f"{'успех' if check.get('success') else 'провал'}.",
            username='Стресс',
        )
    consolation = result.get('consolation')
    if isinstance(consolation, dict) and isinstance(consolation.get('check'), dict):
        check = consolation['check']
        _emit_lobby_chat_message(
            lobby_id, participant.user_id,
            f"Утешение: {consolation.get('target_name')}. Воля {check.get('total')} "
            f"против СЛ {check.get('difficulty')}: "
            f"{'стресс снижен' if check.get('success') else 'проявление стресса'}.",
            username='Стресс',
        )
    anomaly_action = result.get('anomaly')
    if isinstance(anomaly_action, dict):
        check = anomaly_action.get('check') or {}
        exposure = anomaly_action.get('exposure') or {}
        outcome_label = {
            'success': '\u0443\u0441\u043f\u0435\u0445',
            'partial_exit': '\u0432\u044b\u0445\u043e\u0434 \u0441 25% \u0443\u0440\u043e\u043d\u0430',
            'failure': '\u043f\u0440\u043e\u0432\u0430\u043b, 50% \u0443\u0440\u043e\u043d\u0430',
            'severe_failure': '\u043f\u0440\u043e\u0432\u0430\u043b, 100% \u0443\u0440\u043e\u043d\u0430',
            'critical_failure': '\u043a\u0440\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043f\u0440\u043e\u0432\u0430\u043b',
        }.get(anomaly_action.get('outcome'), anomaly_action.get('outcome'))
        _emit_lobby_chat_message(
            lobby_id,
            participant.user_id,
            f"{anomaly_action.get('name') or '\u0410\u043d\u043e\u043c\u0430\u043b\u0438\u044f'}: "
            f"d20 {check.get('roll')} + {check.get('modifier', 0)} = {check.get('total')} "
            f"\u043f\u0440\u043e\u0442\u0438\u0432 \u0421\u041b {check.get('difficulty')}; "
            f"{outcome_label}, \u0443\u0440\u043e\u043d {exposure.get('damage', 0)}.",
            username='\u0410\u043d\u043e\u043c\u0430\u043b\u0438\u044f',
        )
    affected_character_ids = {
        character_id
        for character_id in (
            [data.get('target_character_id')]
            + list(data.get('target_character_ids') or [])
        )
        if character_id
    }
    explosion = (result.get('explosive') or {}).get('explosion') or {}
    affected_character_ids.update(entry['character_id'] for entry in explosion.get('noise_targets',[]))
    affected_character_ids.update(entry['character_id'] for entry in (result.get('attack') or {}).get('noise_targets',[]))
    affected_character_ids.update(entry['character_id'] for entry in (result.get('explosive') or {}).get('noise_targets',[]))
    affected_character_ids.update(
        entry.get('character_id')
        for entry in (explosion.get('targets') or [])
        if entry.get('character_id')
    )
    affected_character_ids.update(
        entry.get('character_id')
        for detonation in reaction_detonations
        for entry in (detonation.get('explosion') or {}).get('targets') or []
        if entry.get('character_id')
    )
    affected_character_ids.update(
        entry.get('target_character_id')
        for entry in ((result.get('attack') or {}).get('results') or [])
        if isinstance(entry, dict) and entry.get('target_character_id')
    )
    for character_id in affected_character_ids:
        target = db.session.get(LobbyCharacter, character_id)
        if target:
            emit_character_update(
                {
                    'character_id': target.id,
                    'updates': {'data': target.data},
                    'source': 'combat',
                },
            )
            location_target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=character_id,
            ).first()
            if location_target:
                socketio.emit(
                    'combat_character_updated',
                    CombatService._serialize_character(
                        location_target,
                        current_turn_id=(result.get('state') or {}).get('current_location_character_id'),
                    ),
                    room=f"location_{location_id}",
                )
    return jsonify(result), 200
