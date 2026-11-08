"""Authoritative targeted treatment for bleeding and splints."""
from copy import deepcopy
from app.services.action_journal import journaled
from dataclasses import dataclass, field
import hashlib
import hmac
import math
import random
import uuid

from flask import current_app
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import CharacterInteractionRequest, DeferredCombatAction, LobbyCharacter, LocationCharacter, LocationCombatState
from app.models.templates import ItemTemplate
from app.services.addictions import resolve_addiction_profile
from app.services.character import CharacterService
from app.services.character_interaction import CharacterInteractionService
from app.services.consumable_use import apply_consumable_use, validate_consumable_use
from app.services.consumable_effects import with_darkness_modifiers
from app.services.deferred_consumable import claim_completion, finish_completion
from app.services.effects import apply_effect_to_health, normalize_effect_list, sync_health_derived_statuses
from app.services.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from app.services.inventory_access import calculate_inventory_access
from app.services.item_template_lookup import resolve_item_template
from app.services.magazine import inventory_slot
from app.services.transaction import atomic_operation


BLEEDING_STAGE_RANK = {'light': 1, 'medium': 2, 'severe': 3, 'extreme': 4}
BLEEDING_DIFFICULTY = {'light': 8, 'medium': 10, 'severe': 12, 'extreme': 14}
LIMB_AREAS = {'leftArm', 'rightArm', 'leftLeg', 'rightLeg'}


def _scissors_medicine_bonus(actor_data, lobby_id, profile):
    direct=profile.get('direct') or {}
    if not direct.get('tourniquet') and 'bandage_bleeding_stop' not in (profile.get('notes') or []):
        return 0
    from app.services.portable_lights import walk
    for path,item in walk(actor_data,True):
        from app.services.belt_attachments import accessible
        if path[:2] not in (['inventory','pockets'],['inventory','backpack']) and not accessible(actor_data,path,item):
            continue
        template=resolve_item_template(item,lobby_id)
        if (template and template.name=='Ножницы' and template.category=='tool'
                and item.get('name')==template.name and item.get('category')==template.category):
            return 2
    return 0


def _doctor_tools_medicine_bonus(actor_data, lobby_id, direct):
    if direct.get('application_form') == 'injectable' or direct.get('medical_difficulty') is not None:
        return 0
    from app.services.portable_lights import walk
    for path, item in walk(actor_data, True):
        from app.services.belt_attachments import accessible
        if path[:2] not in (['inventory', 'pockets'], ['inventory', 'backpack']) and not accessible(actor_data,path,item):
            continue
        template = resolve_item_template(item, lobby_id)
        if not (template and template.name == 'Набор инструментов Врача 50/50'
                and template.category == 'tool' and item.get('name') == template.name
                and item.get('category') == template.category):
            continue
        uses = item.get('uses', item.get('maxUses', (template.attributes or {}).get('uses', 0)))
        if isinstance(uses, (int, float)) and uses > 0:
            return 2
    return 0


def _surgeon_tools_available(actor_data, lobby_id):
    from app.services.portable_lights import walk
    for path, item in walk(actor_data, True):
        from app.services.belt_attachments import accessible
        if path[:2] not in (['inventory', 'pockets'], ['inventory', 'backpack']) and not accessible(actor_data,path,item):
            continue
        template = resolve_item_template(item, lobby_id)
        if not (template and template.name == 'Набор инструментов Хирурга 30/30'
                and template.category == 'tool' and item.get('name') == template.name
                and item.get('category') == template.category):
            continue
        uses = item.get('uses', item.get('maxUses', (template.attributes or {}).get('uses', 0)))
        if isinstance(uses, (int, float)) and uses > 0:
            return True
    return False
MINOR_RESTORABLE_PARTS = {'nose', 'jaw', 'leftEar', 'rightEar', 'ear'}
GENERIC_MEDICAL_BLOCKED_FIELDS = {
    'applications', 'requires_injury', 'target_body_part', 'target_required',
    'requires_infusion_tool', 'blood_compatibility_required', 'blood_collection',
    'blood_type_test', 'requires_shock', 'fracture_splint', 'surgical_kit',
    'catastrophic_limb_surgery', 'special_limb_treatment', 'restore_missing_part',
    'restore_full_body_part', 'restore_limb_health', 'close_area_bleeding',
    'filter_charges', 'requires_gas_mask', 'wound_treatment', 'tourniquet',
    'limb_only', 'bleeding_stop_light_cost', 'bleeding_stop_medium_cost',
}


@dataclass
class MedicalProcedureResult:
    actor: LobbyCharacter
    target: LobbyCharacter
    result: dict
    posture_updates: list
    related_characters: list = field(default_factory=list)


def _profile(item, lobby_id=None):
    template = resolve_item_template(item, lobby_id)
    if not template or template.category != 'consumable':
        raise ValidationError('Для этого предмета нет серверных медицинских правил')
    attributes = template.attributes if isinstance(template.attributes, dict) else {}
    consumable = attributes.get('consumable')
    from app.services.consumable_effects import enrich_imported_consumable
    enriched = enrich_imported_consumable(attributes, template.description or '')
    if enriched != consumable:
        class TemplateView:
            def __init__(self, original, profile):
                self.original = original
                self.attributes = {**attributes, 'consumable': profile}
            def __getattr__(self, key):
                return getattr(self.original, key)
        template = TemplateView(template, enriched)
        consumable = enriched
    if not isinstance(consumable, dict) or not isinstance(consumable.get('direct'), dict):
        raise ValidationError('Для этого расходника не описаны медицинские правила')
    return template, consumable['direct']


def _consumable_effect_source(item):
    name = str((item or {}).get('name') or '').strip()
    if name:
        return name
    template_id = (item or {}).get('templateId')
    if template_id not in (None, ''):
        return f'consumable-template:{template_id}'
    return (item or {}).get('id') or 'consumable'


def _same_effect(entry, selected):
    if not isinstance(entry, dict) or not isinstance(selected, dict):
        return False
    if selected.get('id'):
        return entry.get('id') == selected['id']
    return all(entry.get(key) == selected.get(key) for key in ('type', 'area', 'source'))


def _selected_effect(health, selected):
    effect = next((entry for entry in health.get('effects', []) if _same_effect(entry, selected)), None)
    if not effect:
        raise ConflictError('Выбранная травма изменилась или уже вылечена')
    if effect.get('closed') or effect.get('suppressed') or effect.get('active') is False:
        raise ConflictError('Выбранная травма уже не активна')
    return effect


def _restore_zone_and_pool(health, area, restored_health):
    zone = (health.get('zones') or {}).get(area)
    if not isinstance(zone, dict):
        raise ConflictError('Выбранная часть тела больше не существует')
    try:
        maximum = max(0, float(zone.get('max') or restored_health or 0))
        before = max(0, float(zone.get('current') or 0))
        after = min(maximum, max(0, float(restored_health or 0)))
    except (TypeError, ValueError) as error:
        raise ValidationError('Некорректное здоровье части тела') from error
    zone['current'] = after
    zone['destructionDamage'] = max(0, maximum - after)
    recovered = max(0, after - before)
    if recovered:
        try:
            pool = float(health.get('current') or 0)
            pool_max = float(health.get('max')) if health.get('max') not in (None, '') else None
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректное общее здоровье') from error
        health['current'] = pool + recovered if pool_max is None else min(pool_max, pool + recovered)
    return {'before': before, 'after': after, 'recovered': recovered}


def _has_catastrophic_limb_injury(health, area):
    return any(
        effect.get('type') in {'mangled_limb', 'amputation'}
        and effect.get('area') == area
        and effect.get('active', True) is not False
        for effect in health.get('effects', []) if isinstance(effect, dict)
    )


def _surgery_plan(health, direct, application):
    surgery = str(direct.get('catastrophic_limb_surgery') or '')
    if surgery not in {'full_restoration', 'surgeon'}:
        raise ValidationError('Этот набор не поддерживает выбранную операцию')
    mode = str(application.get('treatmentMode') or '')
    selected = application.get('effect') or {}
    area = str(selected.get('area') or '')
    full_restoration = surgery == 'full_restoration'

    if mode == 'restore_disabled_limb':
        if area not in LIMB_AREAS:
            raise ValidationError('Можно восстановить только выбитую руку или ногу')
        zone = (health.get('zones') or {}).get(area)
        if not isinstance(zone, dict) or float(zone.get('current') or 0) > 0:
            raise ConflictError('Выбранная конечность больше не выбита')
        if _has_catastrophic_limb_injury(health, area):
            raise ValidationError('Искорёженная или утраченная конечность требует отдельной операции')
        restored = float(zone.get('max') or 0) if full_restoration else float(direct.get('restore_limb_health') or 30)
        return {'mode': mode, 'area': area, 'uses': 1, 'restored_health': restored, 'effect': None}

    effect = _selected_effect(health, selected)
    effect_type = effect.get('type')
    if mode == 'treat_unfixed_fracture' and effect_type == 'fracture_unfixed':
        return {'mode': mode, 'area': area, 'uses': 3, 'effect': effect}
    if mode == 'restore_mangled_limb' and effect_type == 'mangled_limb':
        if area not in LIMB_AREAS:
            raise ValidationError('Искорёженная травма не относится к конечности')
        zone = (health.get('zones') or {}).get(area)
        if not isinstance(zone, dict):
            raise ConflictError('Выбранная конечность больше не существует')
        restored = float(zone.get('max') or 0) if full_restoration else float(direct.get('restore_limb_health') or 30)
        return {'mode': mode, 'area': area, 'uses': 1 if full_restoration else 5,
                'restored_health': restored, 'effect': effect}
    if mode == 'restore_lost_part' and full_restoration and effect_type == 'organ_loss':
        if effect.get('treatment_window_expired') or float(effect.get('treatment_window_seconds') or 1) <= 0:
            raise ConflictError('Время восстановления этой части тела истекло')
        return {'mode': mode, 'area': area,
                'uses': 3 if area in MINOR_RESTORABLE_PARTS else 5, 'effect': effect}
    raise ValidationError('Выбранная травма не лечится этим набором')


def _disabled_limb_surgery_plan(health, direct, application):
    if not direct.get('surgical_kit') or direct.get('catastrophic_limb_surgery'):
        raise ValidationError('Этот набор не поддерживает выбранную операцию')
    if application.get('treatmentMode') != 'restore_disabled_limb':
        raise ValidationError('Этим набором можно восстановить только выбитую конечность')
    area = str((application.get('effect') or {}).get('area') or '')
    if area not in LIMB_AREAS:
        raise ValidationError('Можно восстановить только выбитую руку или ногу')
    zone = (health.get('zones') or {}).get(area)
    if not isinstance(zone, dict) or float(zone.get('current') or 0) > 0:
        raise ConflictError('Выбранная конечность больше не выбита')
    if _has_catastrophic_limb_injury(health, area):
        raise ValidationError('Этот набор не восстанавливает искорёженные или утраченные конечности')
    try:
        restored = float(direct.get('restore_limb_health') or 1)
    except (TypeError, ValueError) as error:
        raise ValidationError('Некорректное здоровье после операции') from error
    return {'mode': 'restore_disabled_limb', 'area': area, 'uses': 1,
            'restored_health': restored}


def _procedure_requirements(health, direct, application):
    selected_effect = application.get('effect') or {}
    canonical_application = None
    required_uses = 1
    requires_roll = True
    if application.get('kind') == 'blood_type_test' and direct.get('blood_type_test'):
        if application.get('target') not in {'character', 'packet'}:
            raise ValidationError('Не выбрана цель определения группы крови')
        base_difficulty = 0
        requires_roll = False
    elif application.get('kind') == 'self' and direct.get('blood_collection'):
        base_difficulty = 0
        requires_roll = False
    elif application.get('kind') == 'self' and direct.get('requires_shock'):
        if not any(
            effect.get('type') in {'shock', 'unconsciousness'}
            and effect.get('active', True) is not False
            for effect in health.get('effects', []) if isinstance(effect, dict)
        ):
            raise ConflictError('Нашатырь можно применить только к персонажу в болевом шоке')
        base_difficulty = 0
        requires_roll = False
    elif application.get('kind') == 'bleeding':
        effect = _selected_effect(health, selected_effect)
        canonical_application = _canonical_bleeding_application(direct, application)
        _, _, bleeding = _bleeding_outcome(effect, canonical_application,direct.get('bleeding_rank_bonus',0))
        base_difficulty = BLEEDING_DIFFICULTY[bleeding['stage']]
        required_uses = canonical_application.get('item_uses', 1)
    elif application.get('kind') == 'wound' and direct.get('wound_treatment'):
        effect = _selected_effect(health, selected_effect)
        if effect.get('type') != 'untreated_wound':
            raise ConflictError('Выбранная рана уже изменилась')
        base_difficulty = 4
        requires_roll = False
    elif application.get('kind') == 'injury' and direct.get('special_limb_treatment'):
        treatment = str(direct['special_limb_treatment'])
        area = str(selected_effect.get('area') or '')
        allowed_areas = set(LIMB_AREAS)
        if treatment == 'chimera':
            allowed_areas.add('head')
        if area not in allowed_areas or not isinstance((health.get('zones') or {}).get(area), dict):
            raise ConflictError('Выбранная часть тела больше не существует')
        base_difficulty = 14 if direct.get('restore_limb_health') else 4
    elif application.get('kind') == 'injury' and direct.get('catastrophic_limb_surgery'):
        plan = _surgery_plan(health, direct, application)
        required_uses = plan['uses']
        base_difficulty = 14
    elif (application.get('kind') == 'injury' and direct.get('surgical_kit')
          and not direct.get('catastrophic_limb_surgery')):
        plan = _disabled_limb_surgery_plan(health, direct, application)
        required_uses = plan['uses']
        base_difficulty = 14
    elif (application.get('kind') == 'injury' and direct.get('fracture_splint')
          and application.get('treatmentMode') in {'fix_fracture', 'restore_limb'}):
        if application.get('treatmentMode') == 'fix_fracture':
            effect = _selected_effect(health, selected_effect)
            if effect.get('type') != 'fracture':
                raise ConflictError('Выбранный перелом уже изменился')
        base_difficulty = 10
    elif application.get('kind') == 'self' and direct.get('requires_infusion_tool'):
        base_difficulty = 4
    elif application.get('kind') == 'self' and (
        direct.get('medical_difficulty') is not None
        or direct.get('application_form') == 'injectable'
    ):
        blocked = sorted(key for key in GENERIC_MEDICAL_BLOCKED_FIELDS if direct.get(key))
        if blocked:
            raise ValidationError('Этот препарат требует отдельной медицинской процедуры')
        base_difficulty = 4
    else:
        raise ValidationError('Эта медицинская процедура ещё не перенесена на сервер')
    if direct.get('medical_difficulty') is not None:
        try:
            base_difficulty = float(direct['medical_difficulty'])
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректная сложность медикамента') from error
    return {
        'base_difficulty': base_difficulty,
        'required_uses': required_uses,
        'canonical_application': canonical_application,
        'requires_roll': requires_roll,
    }


def _bleeding(effect):
    parts = str(effect.get('type') or '').split('_')
    if len(parts) != 3 or parts[0] != 'bleeding' or parts[1] not in {'external', 'internal'} or parts[2] not in BLEEDING_STAGE_RANK:
        return None
    return {'kind': parts[1], 'stage': parts[2], 'rank': BLEEDING_STAGE_RANK[parts[2]]}


def _canonical_bleeding_application(direct, selected):
    requested = selected.get('application')
    if not isinstance(requested, dict):
        raise ValidationError('Не выбран способ остановки кровотечения')
    for application in direct.get('applications') or []:
        if isinstance(application, dict) and application == requested:
            return application
    raise ValidationError('Этот расходник не поддерживает выбранный способ лечения')


def _bleeding_outcome(effect, application,rank_bonus=0):
    bleeding = _bleeding(effect)
    medicine_rank = BLEEDING_STAGE_RANK.get(str(application.get('max_stage') or '').lower(), 0)
    if not bleeding or not medicine_rank:
        raise ValidationError('Выбрано некорректное кровотечение')
    medicine_rank=min(4,medicine_rank+int(rank_bonus or 0))
    if not application.get('internal') and bleeding['kind'] == 'internal':
        raise ValidationError('Этот расходник не останавливает внутреннее кровотечение')
    if application.get('internal_only') and bleeding['kind'] != 'internal':
        raise ValidationError('Этот расходник предназначен только для внутреннего кровотечения')
    if bleeding['rank'] <= medicine_rank:
        return 'close', None, bleeding
    if application.get('allow_weakening', True) and bleeding['rank'] == medicine_rank + 1:
        return 'weaken', next(stage for stage,rank in BLEEDING_STAGE_RANK.items() if rank==medicine_rank), bleeding
    raise ValidationError('Тяжесть кровотечения слишком велика для этого расходника')


def _medicine_skill(character_data):
    medicine = (((character_data or {}).get('skills') or {}).get('other') or {}).get('medicine') or {}
    try:
        base = float(medicine.get('base', 10))
    except (TypeError, ValueError):
        base = 10
    try:
        bonus = float(medicine.get('bonus', 0))
    except (TypeError, ValueError):
        bonus = 0
    return int((base - 10) // 2 + bonus)


def _medicine_level(character_data):
    medicine = (((character_data or {}).get('skills') or {}).get('other') or {}).get('medicine') or {}
    try:
        base = float(medicine.get('base', medicine.get('value', 5)))
    except (TypeError, ValueError):
        base = 5
    try:
        bonus = float(medicine.get('bonus', 0))
    except (TypeError, ValueError):
        bonus = 0
    return max(0, base + bonus)


def _numeric(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _roll_blood_type():
    roll = random.randint(1, 20)
    return (1 if roll <= 10 else 2 if roll <= 15 else 3 if roll <= 19 else 4), roll


def _validate_infusion(actor_data, target_health, direct, item):
    from app.services.general_consumable import _inventory_entries

    tools = []
    for _, _, entry in _inventory_entries(actor_data):
        name = str(entry.get('name') or '').strip().casefold()
        if any(fragment in name for fragment in (
            'капельница', 'хирургический набор', 'кустарный набор «айболит',
            'кустарный набор "айболит', 'кустарный набор айболит',
            'набор полного восстановления конечности',
        )):
            tools.append(name)
    if not tools:
        raise ValidationError('Для применения нужна капельница или хирургический набор')
    if direct.get('blood_compatibility_required'):
        recipient_meta=target_health.get('combatMeta') or {}
        recipient_type = int(_numeric(recipient_meta.get('bloodType')))
        attributes = item.get('attributes') or {}
        packet_type = int(_numeric(attributes.get('bloodType') or attributes.get('blood_type')))
        if (not recipient_meta.get('bloodTypeKnown') or not attributes.get('bloodTypeKnown')
                or recipient_type not in {1, 2, 3, 4} or packet_type not in {1, 2, 3, 4}):
            raise ValidationError('Сначала нужно определить группу крови получателя и пакета')
        compatible = {1: {1}, 2: {1, 2}, 3: {1, 3}, 4: {1, 2, 3, 4}}
        if packet_type not in compatible[recipient_type]:
            raise ValidationError('Группа крови не подходит')
    return 1 if any('капельница' in name for name in tools) else 0


def _is_blood_packet(item, lobby_id=None):
    template = resolve_item_template(item, lobby_id)
    profile = ((template.attributes or {}).get('consumable') or {}) if template else (item.get('attributes') or {}).get('consumable') or {}
    direct = profile.get('direct') if isinstance(profile, dict) else None
    if isinstance(direct, dict) and direct:
        return bool(direct.get('blood_packet') or (
            direct.get('blood_compatibility_required') and direct.get('requires_infusion_tool')
        ))
    return str(item.get('name') or '').strip().casefold() == 'пакет крови'


def _blood_test_context(actor_data, application, lobby_id=None):
    if application.get('target') != 'packet':
        return None
    selected = application.get('entry')
    if not isinstance(selected, dict) or not isinstance(selected.get('item'), dict):
        raise ValidationError('Не выбран пакет крови')
    parent, index = inventory_slot(actor_data, selected.get('path'))
    packet = parent[index]
    if packet != selected['item'] or not _is_blood_packet(packet, lobby_id):
        raise ConflictError('Выбранный пакет крови изменился')
    return packet


def _blood_packet_from_template(blood_type, known=False):
    templates = ItemTemplate.query.filter_by(category='consumable').all()
    template = next((
        entry for entry in templates
        if _is_blood_packet({'name': entry.name, 'attributes': entry.attributes})
    ), None)
    if not template:
        raise ConflictError('Шаблон пакета крови не найден')
    attributes = deepcopy(template.attributes or {})
    attributes['bloodType'] = blood_type if blood_type in {1, 2, 3, 4} else None
    attributes['bloodTypeKnown'] = bool(known)
    return {
        'id': f'item_{uuid.uuid4().hex}', 'templateId': template.id,
        'name': template.name, 'category': template.category,
        'subcategory': template.subcategory, 'quantity': 1,
        'uses': attributes.get('uses'), 'maxUses': attributes.get('uses'),
        'weight': template.weight or 0, 'volume': template.volume or 0,
        'price': template.price or 0, 'attributes': attributes,
        'installedModules': [], 'contents': [], 'isContainer': False,
        'isEquippable': False, 'isStackable': False,
    }


def _blood_packet_proof(packet_id, donor_id, blood_type):
    message = f'{packet_id}:{donor_id}:{blood_type}'.encode('utf-8')
    if not current_app.secret_key:
        raise ConflictError('Сервер не настроен для безопасного забора крови')
    secret = str(current_app.secret_key).encode('utf-8')
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _verified_blood_donor(packet):
    attributes = packet.get('attributes') or {}
    donor_id = attributes.get('donorCharacterId')
    blood_type = int(_numeric(attributes.get('bloodType') or attributes.get('blood_type')))
    proof = attributes.get('bloodDonorProof')
    if type(donor_id) is not int or blood_type not in {1, 2, 3, 4} or not isinstance(proof, str):
        return None
    expected = _blood_packet_proof(packet.get('id'), donor_id, blood_type)
    return donor_id if hmac.compare_digest(proof, expected) else None


def _synchronize_blood_knowledge(actor, target, actor_data, target_data, application, context, outcome):
    if not outcome or outcome.get('kind') != 'blood_type_test':
        return []
    packet = (context or {}).get('blood_packet')
    donor_id = target.id if application.get('target') == 'character' else (
        _verified_blood_donor(packet) if isinstance(packet, dict) else None
    )
    if type(donor_id) is not int:
        return []
    donor = db.session.get(LobbyCharacter, donor_id)
    if not donor or donor.lobby_id != actor.lobby_id:
        return []
    blood_type = outcome['blood_type']
    related = []
    from app.services.general_consumable import _inventory_entries

    for character in LobbyCharacter.query.filter_by(lobby_id=actor.lobby_id).all():
        current_data = (actor_data if character.id == actor.id else
                        target_data if character.id == target.id else character.data or {})
        has_linked_packet = any(
            _is_blood_packet(entry, actor.lobby_id)
            and _verified_blood_donor(entry) == donor.id
            for _, _, entry in _inventory_entries(current_data)
        )
        if character.id != donor.id and not has_linked_packet:
            continue
        data = (current_data if character.id in {actor.id, target.id} else deepcopy(current_data))
        changed = False
        if character.id == donor.id:
            meta = data.setdefault('health', {}).setdefault('combatMeta', {})
            stored_type = int(_numeric(meta.get('bloodType')))
            if stored_type in {1, 2, 3, 4} and stored_type != blood_type:
                raise ConflictError('Группа донора не совпадает с группой пакета')
            meta['bloodType'] = blood_type
            meta['bloodTypeKnown'] = True
            meta['bloodTypeTested'] = True
            changed = True
        for _, _, entry in _inventory_entries(data):
            if not _is_blood_packet(entry, actor.lobby_id):
                continue
            attributes = entry.get('attributes') or {}
            if _verified_blood_donor(entry) != donor.id:
                continue
            stored_type = int(_numeric(attributes.get('bloodType') or attributes.get('blood_type')))
            if stored_type in {1, 2, 3, 4} and stored_type != blood_type:
                raise ConflictError('Группа связанного пакета не совпадает с группой донора')
            attributes['bloodType'] = blood_type
            attributes['bloodTypeKnown'] = True
            entry['attributes'] = attributes
            changed = True
        if changed and character.id not in {actor.id, target.id}:
            character.data = data
            flag_modified(character, 'data')
            related.append(character)
    return related


def _minimum_treatment_cost(character_data, direct, canonical_application=None,application=None):
    configured = direct.get('treatment_action_points')
    if configured is not None:
        try:
            cost = float(configured)
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректная стоимость лечения') from error
        if direct.get('treatment_time_uses_medicine', True):
            medicine_level = _medicine_level(character_data)
            from app.services.character_traits import background_is
            if medicine_level >= 15 and not background_is(character_data,'Фармацевт'):
                cost -= 1
            elif medicine_level <= 5:
                cost += 1
    else:
        raw_cost = direct.get('action_points_cost')
        if raw_cost is None and canonical_application:
            raw_cost = canonical_application.get('action_points')
        if raw_cost is None:
            raw_cost = 1 if (
                direct.get('medical_difficulty') is not None
                or direct.get('application_form') == 'injectable'
            ) else 0
        try:
            cost = float(1 if raw_cost is None else raw_cost)
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректная стоимость лечения') from error
    cost=max(0,int(cost))
    if (application or {}).get('fineWork'):
        from app.services.character_traits import trait_count
        if not trait_count(character_data,'Тонкая работа') or cost<3:
            raise ValidationError('Тонкая работа доступна при лечении за 3 ОД или больше')
        cost*=2
    return cost


def minimum_consumable_payment(actor_data, target_data, selection, lobby_id=None):
    source = selection.get('source') if isinstance(selection, dict) else None
    application = selection.get('application') if isinstance(selection, dict) else None
    if not isinstance(source, dict) or not isinstance(source.get('snapshot'), dict):
        raise ValidationError('Не указан расходник процедуры')
    if not isinstance(application, dict):
        raise ValidationError('Не выбрана процедура лечения')
    parent, index = inventory_slot(actor_data or {}, source.get('path'))
    item = parent[index]
    if item != source['snapshot']:
        raise ConflictError('Расходник изменился. Выберите его заново.')
    template = None
    direct = None
    try:
        template, direct = _profile(item, lobby_id)
    except ValidationError:
        item_profile = ((item.get('attributes') or {}).get('consumable') or {})
        direct = item_profile.get('direct') if isinstance(item_profile, dict) else None
        if selection.get('server_authoritative') is True or not isinstance(direct, dict):
            direct = None
    canonical_application = None
    if (
        direct is not None
        and selection.get('server_authoritative') is True
        and selection.get('server_operation') != 'general'
    ):
        from app.services.character_medicine import prepare_profile
        _,direct=prepare_profile({},direct,item,actor_data,template)
        requirements = _procedure_requirements(
            ((target_data or {}).get('health') or {}), direct, application,
        )
        canonical_application = requirements['canonical_application']
        if not direct.get('not_consumed'):
            _validated_uses(item, requirements['required_uses'], template,actor_data=actor_data)
    if direct is None:
        # Compatibility for old/custom client-calculated actions. Retrieval is
        # still authoritative; imported consumables always use their profile.
        try:
            requested_cost = float(application.get('actionPoints', 0))
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректная стоимость применения предмета') from error
        if requested_cost < 0 or requested_cost > 100:
            raise ValidationError('Некорректная стоимость применения предмета')
        use_cost = int(requested_cost)
    else:
        use_cost = _minimum_treatment_cost(actor_data, direct, canonical_application,application)
    access = calculate_inventory_access(actor_data, source.get('path'), item)
    discounted_use_cost = max(0, use_cost - int(access['use_action_discount']))
    return {
        'treatment_action_points': use_cost,
        'discounted_treatment_action_points': discounted_use_cost,
        'retrieval_action_points': int(access['retrieval_action_points']),
        'total_action_points': int(access['retrieval_action_points']) + discounted_use_cost,
        'access': access,
    }


def _validated_uses(item, amount, template,actor_data=None):
    # Общая проверка количества: без изменения предмета и без броска.
    if actor_data is not None:
        from app.services.character_medicine import prepare_uses
        item=deepcopy(item);prepare_uses(item,template,actor_data)
    try:
        quantity = max(0, int(item.get('quantity') or 0))
        maximum = max(1, int(item.get('maxUses') or (template.attributes or {}).get('uses') or 1))
        uses = max(0, int(item.get('uses') or (item.get('attributes') or {}).get('uses_remaining') or maximum))
        remaining = max(1, int(amount or 1))
    except (TypeError, ValueError) as error:
        raise ValidationError('Некорректное количество зарядов расходника') from error
    available = max(0, (quantity - 1) * maximum + uses) if quantity else 0
    if available < remaining:
        raise ValidationError(f'Недостаточно зарядов: требуется {remaining}')
    return quantity, maximum, uses, remaining


def _consume_uses(parent, index, amount, template,actor_data=None):
    item = parent[index]
    from app.services.character_medicine import prepare_uses
    prepare_uses(item,template,actor_data or {})
    quantity, maximum, uses, remaining = _validated_uses(item, amount, template)
    while remaining > 0:
        spent = min(uses, remaining)
        uses -= spent
        remaining -= spent
        if uses <= 0:
            quantity -= 1
            uses = maximum if quantity > 0 else 0
    if quantity <= 0:
        parent.pop(index)
    else:
        item['quantity'] = quantity
        item['uses'] = uses
        item['maxUses'] = maximum
        item.setdefault('attributes', {})['uses_remaining'] = uses


def _validate_treatment_consent(request_id, actor, target, user_id):
    if type(request_id) is not int or request_id <= 0:
        raise ValidationError('Некорректное согласие на лечение')
    request_row = db.session.get(CharacterInteractionRequest, request_id)
    if not request_row or request_row.kind != 'treatment':
        raise NotFoundError('Согласие на лечение не найдено')
    _, actor_model, target_model = CharacterInteractionService._pair(
        request_row.location_id, user_id, request_row.actor_location_character_id, target.id,
    )
    if actor_model.character_id != actor.id:
        raise PermissionDenied('Согласие выдано другому врачу')
    return CharacterInteractionService.validate_treatment(request_id, actor_model, target_model)


def _combat_context(actor, target, user_id, payload):
    location_id = payload.get('combat_location_id')
    if location_id is None:
        request_id = payload.get('interaction_request_id')
        if actor.id != target.id:
            if request_id:
                consent = _validate_treatment_consent(request_id, actor, target, user_id)
                if LocationCombatState.query.filter_by(location_id=consent.location_id, status='active').first():
                    raise ValidationError('В бою медицинскую процедуру нужно сначала оплатить')
            else:
                CharacterService.check_access(target, user_id, edit=True)
        elif request_id:
            raise ValidationError('Для лечения себя не требуется согласие')
        return None, None, None
    if type(location_id) is not int or location_id <= 0:
        raise ValidationError('Некорректная локация лечения')
    actor_model = LocationCharacter.query.filter_by(
        id=payload.get('actor_location_character_id'), location_id=location_id, character_id=actor.id,
    ).first()
    target_model = LocationCharacter.query.filter_by(location_id=location_id, character_id=target.id).first()
    state = LocationCombatState.query.filter_by(location_id=location_id, status='active').first()
    if not actor_model or not target_model or not state:
        raise ConflictError('Бой или участники лечения изменились')
    from app.services.combat import CombatService
    is_gm = CombatService._ensure_access(actor_model.location, user_id)
    if not CombatService._can_end_turn_for_character(actor_model, user_id, is_gm=is_gm):
        raise PermissionDenied('Вы не управляете врачом')
    if state.current_location_character_id != actor_model.id:
        raise PermissionDenied('Сейчас не ход врача')
    CombatService.ensure_character_can_act(actor_model)
    request_id = payload.get('interaction_request_id')
    if actor.id != target.id:
        if request_id:
            _, checked_actor, checked_target = CharacterInteractionService._pair(
                location_id, user_id, actor_model.id, target.id,
            )
            CharacterInteractionService.validate_treatment(request_id, checked_actor, checked_target)
        else:
            CombatService._validate_incapacitated_interaction(
                location_id, user_id, actor_model.id, target.id,
            )
    elif request_id:
        raise ValidationError('Для лечения себя не требуется согласие')
    return actor_model, target_model, state


def _apply_temporary_limb_effects(health, direct, item):
    if not direct.get('affects_all_limbs'):
        return []
    minutes = max(0, int(direct.get('temporary_limb_health_minutes') or 0))
    turns = max(0, int(direct.get('temporary_limb_health_turns') or 0))
    moves = max(0, int(direct.get('temporary_limb_health_moves') or 0))
    if not minutes and not turns and not moves:
        return []
    applied = []
    source = _consumable_effect_source(item)
    for area in LIMB_AREAS:
        zones=health.get('zones') or {}
        zone=next((value for key,value in zones.items() if str(key).replace('_','').casefold()==area.casefold()),None)
        if not isinstance(zone, dict):
            continue
        previous = max(0, float(zone.get('current') or 0))
        existing = next((
            effect for effect in health.get('effects', [])
            if isinstance(effect, dict)
            and effect.get('type') == 'temporary_limb_restoration'
            and effect.get('area') == area
            and effect.get('source') == source
            and effect.get('active', True)
        ), None)
        minimum_health = max(0, float(direct.get('minimum_limb_health') or 0))
        suppress_fracture = bool(direct.get('suppress_limb_trauma'))
        if previous > 0 and not suppress_fracture and not minimum_health:
            continue
        if previous <= 0:
            zone['current'] = 1
        apply_effect_to_health(health, {
            'type': 'temporary_limb_restoration',
            'name': 'Временное восстановление конечности',
            'area': area, 'source': source,
            'previous_health': (
                existing.get('previous_health') if existing is not None else previous
            ),
            'restore_on_expire': (
                existing.get('restore_on_expire', True) if existing is not None else previous <= 0
            ),
            'health_cap': (
                existing.get('health_cap') if existing is not None else (1 if previous <= 0 else None)
            ),
            'minimum_limb_health': minimum_health,
            'suppress_fracture': suppress_fracture,
            'remaining': moves or minutes or turns,
            'tick': 'movement_end' if moves else 'time_elapsed' if minutes else 'turn_end',
            'time_unit': 'movement' if moves else 'minute' if minutes else None,
            'remaining_seconds': moves * 600 if moves else minutes * 60 if minutes else None,
            'medication_clock': True,
        })
        applied.append(area)
    return applied


def _apply_success(
        health, direct, application, item, *, profile=None, in_combat=False,
        infusion_bonus=0, actor_data=None, procedure_context=None,recipient_data=None):
    if application.get('kind') == 'blood_type_test':
        if application.get('target') == 'packet':
            packet = (procedure_context or {}).get('blood_packet')
            if not isinstance(packet, dict):
                raise ConflictError('Выбранный пакет крови больше недоступен')
            attributes = packet.setdefault('attributes', {})
            blood_type = int(_numeric(attributes.get('bloodType') or attributes.get('blood_type')))
            if blood_type not in {1, 2, 3, 4}:
                blood_type, _ = _roll_blood_type()
                attributes['bloodType'] = blood_type
            attributes['bloodTypeKnown'] = True
            return {'kind': 'blood_type_test', 'target': 'packet', 'blood_type': blood_type}
        meta = health.setdefault('combatMeta', {})
        blood_type = int(_numeric(meta.get('bloodType')))
        roll = meta.get('bloodTypeRoll')
        if blood_type not in {1, 2, 3, 4}:
            blood_type, roll = _roll_blood_type()
        meta.update({
            'bloodTypeTested': True, 'bloodTypeKnown': True,
            'bloodType': blood_type, 'bloodTypeRoll': roll,
        })
        return {
            'kind': 'blood_type_test', 'target': 'character',
            'blood_type': blood_type, 'blood_type_roll': roll,
        }

    if application.get('kind') == 'self' and direct.get('blood_collection'):
        if not isinstance(actor_data, dict):
            raise ValidationError('Инвентарь врача недоступен')
        meta = health.setdefault('combatMeta', {})
        blood_type = int(_numeric(meta.get('bloodType')))
        if blood_type not in {1, 2, 3, 4}:
            blood_type, roll = _roll_blood_type()
            meta['bloodType'] = blood_type
            meta['bloodTypeRoll'] = roll
        packet = _blood_packet_from_template(blood_type, meta.get('bloodTypeKnown'))
        donor_id = (procedure_context or {}).get('blood_donor_id')
        packet['attributes']['donorCharacterId'] = donor_id
        packet['attributes']['bloodDonorProof'] = _blood_packet_proof(packet['id'], donor_id, blood_type)
        inventory = actor_data.setdefault('inventory', {})
        inventory.setdefault('pockets', []).append(packet)
        stages = ['normal', 'light', 'medium', 'severe', 'critical']
        current = str(health.get('blood') or health.get('bloodStage') or 'normal').lower()
        index = stages.index(current) if current in stages else 0
        stage = stages[min(len(stages) - 1, index + max(0, int(direct.get('blood_stage_delta') or 2)))]
        health['blood'] = stage
        health['bloodStage'] = stage
        from app.services.general_consumable import _apply_profile
        _apply_profile(health, profile or {}, direct, item, in_combat=in_combat,character_data=recipient_data)
        return {
            'kind': 'blood_collection', 'blood_type': blood_type or None,
            'blood_stage': stage, 'packet_id': packet['id'],
        }

    if application.get('kind') == 'self' and direct.get('requires_shock'):
        from app.services.general_consumable import _apply_profile
        if not _apply_profile(health, profile or {}, direct, item, in_combat=in_combat,character_data=recipient_data):
            raise ValidationError('Нашатырь не имеет применимых эффектов')
        return {'kind': 'shock_support'}

    if application.get('kind') == 'self':
        from app.services.general_consumable import _apply_profile

        if not isinstance(profile, dict):
            raise ValidationError('Правила препарата повреждены')
        profile = deepcopy(profile)
        if infusion_bonus:
            for effect in profile.get('effects') or []:
                if effect.get('type') == 'blood_recovery' and effect.get('remaining') is not None:
                    effect['remaining'] = _numeric(effect.get('remaining')) + infusion_bonus
        bleeding_before = sum(1 for entry in health.get('effects', []) if _bleeding(entry))
        changed = _apply_profile(health, profile, direct, item, in_combat=in_combat,character_data=recipient_data)
        temporary_limbs = _apply_temporary_limb_effects(health, direct, item)
        closed_bleeding = 0
        if direct.get('stop_all_bleeding'):
            health['effects'] = [entry for entry in health.get('effects', []) if not _bleeding(entry)]
            closed_bleeding = bleeding_before - sum(
                1 for entry in health.get('effects', []) if _bleeding(entry)
            )
            changed = True
        if not changed and not temporary_limbs:
            raise ValidationError('Препарат не имеет применимых эффектов')
        return {
            'kind': 'self', 'temporary_limbs': sorted(temporary_limbs),
            'closed_bleeding': closed_bleeding, 'infusion_bonus': infusion_bonus,
        }

    selected = application.get('effect')
    if application.get('kind') == 'bleeding':
        effect = _selected_effect(health, selected)
        treatment, result_stage, bleeding = _bleeding_outcome(
            effect, _canonical_bleeding_application(direct, application),direct.get('bleeding_rank_bonus',0),
        )
        canonical = _canonical_bleeding_application(direct, application)
        if treatment == 'weaken':
            effect['type'] = f"bleeding_{bleeding['kind']}_{result_stage}"
            effect['name'] = f"Кровотечение {result_stage}"
            effect['closed'] = False
            effect['suppressed'] = False
        else:
            health['effects'] = [entry for entry in health.get('effects', []) if entry is not effect]
            if not canonical.get('treated'):
                apply_effect_to_health(health, {
                    'type': 'untreated_wound', 'name': 'Необработанная рана',
                    'area': effect.get('area'), 'source': effect.get('id') or effect.get('source') or item.get('name'),
                    'tick': 'manual',
                })
        if direct.get('tourniquet'):
            for entry in health.get('effects', []):
                if _bleeding(entry) and entry.get('area') == effect.get('area'):
                    entry['suppressed'] = True
            apply_effect_to_health(health, {
                'type': 'tourniquet', 'name': f"Жгут: {effect.get('area')}",
                'area': effect.get('area'), 'source': _consumable_effect_source(item), 'tick': 'manual',
            })
        return {'kind': 'bleeding', 'mode': treatment, 'result_stage': result_stage,
                'area': effect.get('area')}

    if application.get('kind') == 'wound':
        if not direct.get('wound_treatment'):
            raise ValidationError('Этот расходник не обрабатывает раны')
        effect = _selected_effect(health, selected)
        if effect.get('type') != 'untreated_wound':
            raise ConflictError('Выбранная рана уже изменилась')
        health['effects'] = [entry for entry in health.get('effects', []) if entry is not effect]
        try:
            pain_delta = float(direct.get('pain_delta') or 0)
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректное изменение боли') from error
        if pain_delta:
            apply_effect_to_health(health, {'type':'pain','value':pain_delta,'source':'medical_procedure'}, character_data=recipient_data)
        return {'kind': 'wound', 'area': effect.get('area'), 'pain_delta': pain_delta}

    if (application.get('kind') == 'injury' and direct.get('surgical_kit')
            and not direct.get('catastrophic_limb_surgery')):
        plan = _disabled_limb_surgery_plan(health, direct, application)
        restoration = _restore_zone_and_pool(health, plan['area'], plan['restored_health'])
        try:
            pain_delta = float(direct.get('pain_delta') or 0)
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректное изменение боли') from error
        if pain_delta:
            apply_effect_to_health(health, {'type':'pain','value':pain_delta,'source':'medical_procedure'}, character_data=recipient_data)
        return {'kind': 'surgery', 'mode': plan['mode'], 'area': plan['area'],
                'uses': plan['uses'], 'restoration': restoration, 'pain_delta': pain_delta}

    if application.get('kind') == 'injury' and direct.get('catastrophic_limb_surgery'):
        plan = _surgery_plan(health, direct, application)
        effect = plan.get('effect')
        if effect is not None:
            health['effects'] = [entry for entry in health.get('effects', []) if entry is not effect]
        try:
            pain_delta = float(direct.get('pain_delta') or 0)
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректное изменение боли') from error
        if pain_delta:
            apply_effect_to_health(health, {'type':'pain','value':pain_delta,'source':'medical_procedure'}, character_data=recipient_data)

        if plan['mode'] == 'treat_unfixed_fracture':
            apply_effect_to_health(health, {
                'type': 'delayed_limb_treatment',
                'name': f"Лечение незафиксированного перелома: {plan['area']}",
                'area': plan['area'], 'source': _consumable_effect_source(item),
                'cure_fracture': True, 'remaining': 72, 'remaining_seconds': 259200,
                'tick': 'time_elapsed', 'time_unit': 'hour',
            })
            return {'kind': 'surgery', **{key: plan[key] for key in ('mode', 'area', 'uses')},
                    'delayed_hours': 72, 'pain_delta': pain_delta}

        restoration = None
        if plan.get('restored_health') is not None:
            restoration = _restore_zone_and_pool(health, plan['area'], plan['restored_health'])
        elif plan['mode']=='restore_lost_part':
            organ=(health.get('organs') or {}).get(plan['area'])
            if isinstance(organ,dict):
                before=_numeric(organ.get('current'));organ['current']=_numeric(organ.get('max'))
                restoration={'area':plan['area'],'organ':True,'restored':organ['current']-before,'current':organ['current']}
            elif plan['area'] in LIMB_AREAS:
                zone=(health.get('zones') or {}).get(plan['area']) or {}
                restoration=_restore_zone_and_pool(health,plan['area'],_numeric(zone.get('max')))
        return {'kind': 'surgery', **{key: plan[key] for key in ('mode', 'area', 'uses')},
                'restoration': restoration, 'pain_delta': pain_delta}

    if application.get('kind') == 'injury' and direct.get('special_limb_treatment'):
        treatment = str(direct['special_limb_treatment'])
        area = str((selected or {}).get('area') or '')
        allowed_areas = set(LIMB_AREAS)
        if treatment == 'chimera':
            allowed_areas.add('head')
        if area not in allowed_areas:
            raise ValidationError('Для препарата выбрана недопустимая часть тела')
        zone = (health.get('zones') or {}).get(area)
        if not isinstance(zone, dict):
            raise ConflictError('Выбранная часть тела больше не существует')

        health['effects'] = [
            effect for effect in health.get('effects', [])
            if not (_bleeding(effect) and effect.get('area') == area)
        ]
        fractures = [
            effect for effect in health.get('effects', [])
            if effect.get('type') in {'fracture', 'fracture_fixed'} and effect.get('area') == area
        ]
        try:
            pain_delta = float(direct.get('pain_delta') or 0)
        except (TypeError, ValueError) as error:
            raise ValidationError('Некорректное изменение боли') from error
        if pain_delta:
            apply_effect_to_health(health, {'type':'pain','value':pain_delta,'source':'medical_procedure'}, character_data=recipient_data)

        if treatment == 'chimera' and area == 'head':
            apply_effect_to_health(health, {
                'type': 'death', 'name': 'Смерть после применения «Химеры»',
                'area': area, 'source': _consumable_effect_source(item), 'tick': 'manual',
            })
            return {'kind': 'special_limb_treatment', 'mode': treatment, 'area': area,
                    'death': True, 'closed_bleeding': True}

        if treatment == 'chimera' and not fractures:
            try:
                damage = abs(float(direct.get('invalid_limb_damage') or -200))
            except (TypeError, ValueError) as error:
                raise ValidationError('Некорректный урон препарата') from error
            before = max(0, float(zone.get('current') or 0))
            zone['current'] = max(0, before - damage)
            zone['destructionDamage'] = max(
                float(zone.get('destructionDamage') or 0),
                max(0, float(zone.get('max') or 0) - zone['current']),
            )
            overflow = max(0, damage - before)
            if overflow:
                health['current'] = max(0, float(health.get('current') or 0) - overflow)
            return {'kind': 'special_limb_treatment', 'mode': treatment, 'area': area,
                    'damage': damage, 'overflow_damage': overflow, 'closed_bleeding': True}

        minutes = max(1, int(direct.get('delayed_limb_treatment_minutes') or direct.get('delay') or 1))
        apply_effect_to_health(health, {
            'type': 'delayed_limb_treatment',
            'name': f"{item.get('name') or 'Препарат'}: лечение {area}",
            'area': area, 'source': _consumable_effect_source(item),
            'cure_fracture': bool(direct.get('cure_fracture')),
            'restore_limb_health': (
                float(direct.get('restore_limb_health') or 50)
                if treatment == 'second_life' else None
            ),
            'remaining': minutes, 'remaining_seconds': minutes * 60,
            'tick': 'time_elapsed', 'time_unit': 'minute',
        })
        return {'kind': 'special_limb_treatment', 'mode': treatment, 'area': area,
                'delayed_minutes': minutes, 'closed_bleeding': True,
                'pain_delta': pain_delta}

    if application.get('kind') != 'injury' or not direct.get('fracture_splint'):
        raise ValidationError('Эта процедура ещё не перенесена на сервер')
    area = str((selected or {}).get('area') or '')
    if area not in LIMB_AREAS:
        raise ValidationError('Шину можно применить только к руке или ноге')
    mode = application.get('treatmentMode')
    if mode == 'fix_fracture':
        fracture = _selected_effect(health, selected)
        if fracture.get('type') != 'fracture':
            raise ConflictError('Выбранный перелом уже изменился')
        health['effects'] = [entry for entry in health.get('effects', []) if entry is not fracture]
        health['painLevel'] = max(0, min(10, float(health.get('painLevel') or 0) - 1))
        apply_effect_to_health(health, {
            'type': 'fracture_fixed', 'name': 'Зафиксированный перелом', 'area': area,
            'source': _consumable_effect_source(item), 'value': 1,
            'remaining': 24, 'tick': 'time_elapsed', 'time_unit': 'hour',
            'remaining_seconds': 86400,
        })
        return {'kind': 'splint', 'mode': mode, 'area': area}
    if mode != 'restore_limb':
        raise ValidationError('Шина может либо зафиксировать перелом, либо временно восстановить конечность')
    zone = (health.get('zones') or {}).get(area)
    if not isinstance(zone, dict) or float(zone.get('current') or 0) > 0:
        raise ConflictError('Для временного восстановления конечность должна иметь 0 ОЗ')
    zone['current'] = 1
    minutes = max(0, int(direct.get('temporary_limb_health_minutes') or 0))
    turns = max(0, int(direct.get('temporary_limb_health_turns') or 0))
    apply_effect_to_health(health, {
        'type': 'temporary_limb_restoration', 'name': 'Временное восстановление конечности',
        'area': area, 'source': _consumable_effect_source(item),
        'previous_health': 0, 'restore_on_expire': True, 'health_cap': 1,
        'remaining': minutes or turns, 'tick': 'time_elapsed' if minutes else 'turn_end',
        'time_unit': 'minute' if minutes else None,
        'remaining_seconds': minutes * 60 if minutes else None,
    })
    return {'kind': 'splint', 'mode': mode, 'area': area, 'remaining_minutes': minutes,
            'remaining_turns': turns}


class MedicalProcedureService:
    @staticmethod
    @journaled(kind='medical_procedure')
    @atomic_operation
    def apply(user_id, actor_id, payload):
        if not isinstance(payload, dict):
            raise ValidationError('Некорректные параметры процедуры')
        actor = db.session.get(LobbyCharacter, actor_id)
        target_id = payload.get('target_character_id', actor_id)
        target = db.session.get(LobbyCharacter, target_id) if type(target_id) is int else None
        if not actor or not target or actor.lobby_id != target.lobby_id:
            raise NotFoundError('Врач или пациент не найден')
        CharacterService.check_access(actor, user_id, edit=True)
        injector_payment=payload.get('auto_injector_payment')
        if injector_payment is not None:
            from app.services.auto_injector import combat_actor,validate_source
            if not isinstance(injector_payment,str) or injector_payment not in {'action','free'} or actor.id!=target.id or payload.get('deferred_action_id'):
                raise ValidationError('Автоинъектор применяется только к себе за 1 ОД или 1 СД')
            injector_actor=combat_actor(actor,user_id)
            source=payload.get('source') or {}
            validate_source(actor.data or {},source.get('path'),source.get('snapshot') or {},actor.lobby_id)
            payload={**payload,'combat_location_id':injector_actor.location_id if injector_actor else None,
                'actor_location_character_id':injector_actor.id if injector_actor else None}
        actor_model, _, _ = _combat_context(actor, target, user_id, payload)
        if actor_model and not payload.get('deferred_action_id') and injector_payment is None:
            raise ValidationError('В бою медицинскую процедуру нужно сначала оплатить')

        source = payload.get('source')
        operation_id = payload.get('operation_id')
        if not isinstance(source, dict) or not isinstance(source.get('snapshot'), dict):
            raise ValidationError('Не указан выбранный расходник')
        envelope = {
            'id': operation_id,
            'deferred_action_id': payload.get('deferred_action_id'),
            'source': source,
            'combat_location_id': payload.get('combat_location_id'),
            'side_effects': {},
        }
        completion = claim_completion(
            actor, target, user_id, envelope, payload.get('interaction_request_id'),
        )
        validate_consumable_use(actor, envelope)

        actor_data = deepcopy(actor.data or {})
        target_data = actor_data if actor.id == target.id else deepcopy(target.data or {})
        parent, index = inventory_slot(actor_data, source.get('path'))
        item = parent[index]
        if item != source['snapshot']:
            raise ConflictError('Расходник изменился. Выберите его заново.')
        template, direct = _profile(item, actor.lobby_id)
        profile = with_darkness_modifiers(
            (template.attributes or {}).get('consumable') or {}, template.description or '',
        )
        from app.services.character_medicine import prepare_profile
        profile,direct=prepare_profile(profile,direct,item,actor_data,template)
        application = payload.get('application')
        if not isinstance(application, dict):
            raise ValidationError('Не выбрана медицинская процедура')
        if completion and completion.payload['selection'].get('application') != application:
            raise ConflictError('Выбранная процедура не совпадает с оплаченной')

        health = target_data.setdefault('health', {})
        from app.services.character_traits import trait_count,background_is
        if trait_count(actor_data,'Боязнь крови') and (actor.id==target.id or any(
            (_bleeding(e) or {}).get('kind')=='external' for e in health.get('effects',[]) if isinstance(e,dict))):
            raise ValidationError('Боязнь крови не позволяет оказать эту медицинскую помощь')
        if trait_count(actor_data,'Аккуратные пальчики') and direct.get('surgical_kit') and _numeric(direct.get('pain_delta'))>0:
            direct=deepcopy(direct);direct['pain_delta']=_numeric(direct['pain_delta'])/2
        surgeon_tools = bool(direct.get('surgical_kit') and _surgeon_tools_available(actor_data,actor.lobby_id))
        if surgeon_tools and _numeric(direct.get('pain_delta'))>0:
            direct=deepcopy(direct);direct['pain_delta']=math.floor(_numeric(direct['pain_delta'])/2)
        requirements = _procedure_requirements(health, direct, application)
        canonical_application = requirements['canonical_application']
        required_uses = requirements['required_uses']
        base_difficulty = requirements['base_difficulty']
        requires_roll = requirements['requires_roll']
        if injector_payment is not None:
            requires_roll=False
            if application.get('fineWork'):raise ValidationError('Автоинъектор не требует Тонкой работы')
        if application.get('fineWork'):
            from app.services.character_traits import trait_count
            _minimum_treatment_cost(actor_data,direct,canonical_application,application)
            if not requires_roll:raise ValidationError('Для этой процедуры не требуется медицинская проверка')
            base_difficulty-=6 if trait_count(actor_data,'Тонкая работа')>=2 else 4
        helper=((actor_data.get('health') or {}).get('combatMeta') or {}).get('helpAdvantage')
        if helper and helper.get('skill_path') not in {'','skills.other.medicine'}:helper=None
        if requires_roll and helper:base_difficulty-=_numeric(helper.get('medical_dc_reduction'))
        if requires_roll and background_is(actor_data,'Врач по бумагам') and not helper:base_difficulty+=4
        if requires_roll and background_is(actor_data,'Акушер') and actor.id==target.id:base_difficulty+=3
        if not direct.get('not_consumed'):
            _validated_uses(item, required_uses, template,actor_data=actor_data)
        generic_self = application.get('kind') == 'self'
        procedure_context = {
            'blood_packet': _blood_test_context(actor_data, application, actor.lobby_id),
            'blood_donor_id': target.id,
        }
        infusion_bonus = (
            _validate_infusion(actor_data, health, direct, item)
            if generic_self and direct.get('requires_infusion_tool') else 0
        )
        usage_key = str(direct.get('exclusive_group') or item.get('templateId') or item.get('name'))
        if generic_self and direct.get('use_limit'):
            usage = health.setdefault('combatMeta', {}).setdefault('consumableUsage', {})
            if _numeric(usage.get(usage_key)) >= _numeric(direct.get('use_limit'), 1):
                raise ConflictError('Лимит использования этого препарата исчерпан')

        minimum_cost = _minimum_treatment_cost(actor_data, direct, canonical_application,application)
        payment_details = minimum_consumable_payment(actor_data, target_data, {
            'source': source,
            'application': application,
            'server_authoritative': True,
        }, actor.lobby_id)
        if injector_payment is not None:
            cost=1 if actor_model and injector_payment=='action' else 0
            free=1 if actor_model and injector_payment=='free' else 0
            if actor_model:
                if actor_model.action_points_current<cost or actor_model.free_actions_current<free:
                    raise ValidationError('Для автоинъектора нужен 1 ОД или 1 СД')
                actor_model.action_points_current-=cost;actor_model.free_actions_current-=free
                from app.services.combat import CombatService
                CombatService._clear_aim(actor_model)
            payment_details={'total_action_points':cost,'free_actions':free,'retrieval_action_points':0,'auto_injector':True}
        if completion:
            paid_action_points = completion.payload.get('paid_action_points')
            if (
                type(paid_action_points) is not int
                or paid_action_points < payment_details['total_action_points']
            ):
                raise ConflictError(
                    'Сохранённая процедура оплачена неверно. Отмените её и начните заново.'
                )

        try:
            medication_bonus = float(direct.get('med_bonus') or 0) + float(
                (canonical_application or {}).get('medicine_bonus') or 0
            )
        except (TypeError, ValueError):
            medication_bonus = 0
        skill_bonus = (_medicine_skill(actor_data)
                       + _scissors_medicine_bonus(actor_data,actor.lobby_id,profile)
                       + _doctor_tools_medicine_bonus(actor_data,actor.lobby_id,direct)
                       + (5 if surgeon_tools else 0))
        if helper:skill_bonus+=_numeric(helper.get('roll_bonus'))
        patient_counts=((actor_data.get('health') or {}).get('combatMeta') or {}).get('patientTreatments') or {}
        if trait_count(actor_data,'Личный врач') and _numeric(patient_counts.get(str(target.id)))>10:skill_bonus+=3
        from app.services.brigadier_order import consume
        order=consume(actor_data) if requires_roll else None
        if order:skill_bonus+=3
        difficulty = max(1, base_difficulty - skill_bonus - medication_bonus) if requires_roll else None
        roll = (
            completion.payload.get('medicine_roll') if completion else random.randint(1, 20)
        ) if requires_roll else None
        if order and order.get('advantage') and not completion:
            roll=max(roll,random.randint(1,20))
        if requires_roll and (type(roll) is not int or not 1 <= roll <= 20):
            raise ConflictError('Не удалось восстановить медицинский бросок')
        success = not requires_roll or roll >= difficulty
        if requires_roll and helper:actor_data['health']['combatMeta'].pop('helpAdvantage',None)
        if not direct.get('not_consumed'):
            _consume_uses(parent, index, required_uses, template,actor_data=actor_data)

        outcome = None
        intoxication_before=_numeric(health.get('intoxication'))
        if success:
            if requires_roll:
                patient_counts[str(target.id)]=int(_numeric(patient_counts.get(str(target.id))))+1
                actor_data.setdefault('health',{}).setdefault('combatMeta',{})['patientTreatments']=patient_counts
            outcome = _apply_success(
                health, direct, application, item,
                profile=profile, in_combat=actor_model is not None,
                infusion_bonus=infusion_bonus,
                actor_data=actor_data, procedure_context=procedure_context,recipient_data=target_data,
            )
            related_characters = _synchronize_blood_knowledge(
                actor, target, actor_data, target_data, application, procedure_context, outcome,
            )
            if generic_self and direct.get('use_limit'):
                usage = health.setdefault('combatMeta', {}).setdefault('consumableUsage', {})
                usage[usage_key] = _numeric(usage.get(usage_key)) + 1
            if generic_self:
                envelope['side_effects']['exposure'] = {
                    'item_name': item.get('name') or template.name,
                    'price': max(0, _numeric(template.price or item.get('price'))),
                    'intoxication': max(0, _numeric(health.get('intoxication'))-intoxication_before),
                    'exhaustion_relief': max(0, -_numeric(direct.get('exhaustion_delta'))),
                    'addiction_block_hours': max(0, _numeric(direct.get('addiction_block_hours'))),
                }
                if actor_model is not None and _numeric(direct.get('action_points_delta')):
                    envelope['side_effects']['action_points_delta'] = _numeric(
                        direct.get('action_points_delta')
                    )
        else:
            related_characters = []
            meta = actor_data.setdefault('health', {}).setdefault('combatMeta', {})
            meta['mustDoRetry'] = {
                'kind': 'medical', 'name': f"Применить {item.get('name') or template.name}",
                'skill_path': 'skills.other.medicine', 'skill_label': 'Медицина',
                'difficulty': max(1, base_difficulty - medication_bonus),
                'medical_retry': {
                    'actor_character_id': actor.id, 'target_character_id': target.id,
                    'item_id': item.get('id'), 'item_path': source.get('path'),
                    'item_snapshot': deepcopy(item), 'application': deepcopy(application),
                    'server_authoritative': True,
                    'interaction_context': ({
                        'actorLocationCharacterId': actor_model.id,
                        'lobbyId': actor.lobby_id,
                        'locationId': actor_model.location_id,
                    } if actor_model else None),
                },
            }

        health['effects'] = normalize_effect_list(health.get('effects') or [])
        sync_health_derived_statuses(health)
        actor.data = actor_data
        flag_modified(actor, 'data')
        if target.id != actor.id:
            target.data = target_data
            flag_modified(target, 'data')
        posture_updates = CharacterService.sync_location_health(target, health)
        use_result = apply_consumable_use(
            actor, target, envelope, location_id=payload.get('combat_location_id'),
            addiction_profile=resolve_addiction_profile(
                profile, (template.attributes or {}).get('catalog_name') or template.name, template.price,
            ),
        )
        finish_completion(actor, completion)

        interaction = None
        request_id = payload.get('interaction_request_id')
        if request_id:
            interaction = CharacterInteractionService.complete_treatment(request_id, user_id, commit=False)

        result = {
            'roll': roll, 'difficulty': difficulty, 'base_difficulty': base_difficulty,
            'medicine_bonus': skill_bonus, 'medication_bonus': medication_bonus,
            'success': success, 'outcome': outcome,
            'minimum_action_points': payment_details['total_action_points'],
            'payment_details': payment_details,
            'consumable': use_result,
            'interaction': interaction,
        }
        return MedicalProcedureResult(actor, target, result, posture_updates, related_characters)

    @staticmethod
    def apply_retry_success(actor_model, retry):
        if not isinstance(retry, dict) or retry.get('server_authoritative') is not True:
            raise ValidationError('Серверный медицинский повтор не найден')
        actor = actor_model.character
        if not actor or retry.get('actor_character_id') != actor.id:
            raise ConflictError('Врач медицинского повтора изменился')
        target_id = retry.get('target_character_id')
        target = db.session.get(LobbyCharacter, target_id) if type(target_id) is int else None
        if not target or target.lobby_id != actor.lobby_id:
            raise ConflictError('Пациент медицинского повтора больше недоступен')
        target_model = LocationCharacter.query.filter_by(
            location_id=actor_model.location_id, character_id=target.id,
        ).first()
        if not target_model:
            raise ConflictError('Пациент покинул место лечения')
        if target.id != actor.id and max(
            abs(actor_model.pos_x - target_model.pos_x),
            abs(actor_model.pos_y - target_model.pos_y),
        ) > 1:
            raise ConflictError('Пациент больше не находится рядом с врачом')

        item = retry.get('item_snapshot')
        application = retry.get('application')
        if not isinstance(item, dict) or not isinstance(application, dict):
            raise ValidationError('Параметры медицинского повтора повреждены')
        template, direct = _profile(item, actor.lobby_id)
        profile = with_darkness_modifiers(
            (template.attributes or {}).get('consumable') or {}, template.description or '',
        )
        target_data = deepcopy(target.data or {})
        from app.services.character_medicine import prepare_profile
        profile,direct=prepare_profile(profile,direct,item,actor.data or {},template)
        health = target_data.setdefault('health', {})
        requirements = _procedure_requirements(health, direct, application)
        if not requirements['requires_roll']:
            raise ConflictError('Эта процедура не поддерживает повтор «Должен это сделать»')
        infusion_bonus = (
            _validate_infusion(actor.data or {}, health, direct, item)
            if application.get('kind') == 'self' and direct.get('requires_infusion_tool') else 0
        )
        usage_key = str(direct.get('exclusive_group') or item.get('templateId') or item.get('name'))
        if application.get('kind') == 'self' and direct.get('use_limit'):
            usage = health.setdefault('combatMeta', {}).setdefault('consumableUsage', {})
            if _numeric(usage.get(usage_key)) >= _numeric(direct.get('use_limit'), 1):
                raise ConflictError('Лимит использования этого препарата исчерпан')
        outcome = _apply_success(
            health, direct, application, item, profile=profile, in_combat=True,
            infusion_bonus=infusion_bonus, actor_data=actor.data,recipient_data=target_data,
        )
        if application.get('kind') == 'self' and direct.get('use_limit'):
            usage = health.setdefault('combatMeta', {}).setdefault('consumableUsage', {})
            usage[usage_key] = _numeric(usage.get(usage_key)) + 1
        health['effects'] = normalize_effect_list(health.get('effects') or [])
        sync_health_derived_statuses(health)
        target.data = target_data
        flag_modified(target, 'data')
        posture_updates = CharacterService.sync_location_health(target, health)
        return {
            'target_character_id': target.id,
            'outcome': outcome,
            '_posture_updates': posture_updates,
        }
