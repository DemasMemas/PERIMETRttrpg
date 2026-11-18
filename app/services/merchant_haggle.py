"""Серверные раунды торга за конкретную корзину НПС-торговца."""
from datetime import datetime, timezone
from random import randint

from app.extensions import db
from app.models import CharacterInteractionRequest
from app.services.character_traits import trait_count
from app.services.exceptions import ConflictError, PermissionDenied, ValidationError
from app.services.npc_trade import enabled, require_contact
from app.services.trade import money, prepare_payload
from app.services.transaction import atomic_operation


SKILLS = {'barter', 'persuasion', 'deception', 'intimidation'}
MAX_ROUNDS = 3


def terms(purchase, sale, steps):
    difference = purchase - sale
    factor = 10 - steps if difference > 0 else 10 + steps
    amount = money(abs(difference) * factor // 10, label='Сумма после торга')
    return {'purchase_total': purchase, 'sale_total': sale,
            'actor_money': amount if difference > 0 else 0, 'target_money': amount if difference < 0 else 0}


def _basket(offer):
    return sorted((str(entry['item_id']), repr(entry['path']), entry['amount']) for entry in offer)


def _matching_draft(actor, target, user_id, quote):
    rows = CharacterInteractionRequest.query.filter_by(location_id=actor.location_id,
        actor_location_character_id=actor.id, target_location_character_id=target.id,
        actor_user_id=user_id, kind='merchant_haggle', status='negotiating').all()
    return next((row for row in rows if row.payload.get('pricing') == 'net'
        and row.payload['_expected_revisions'] == quote['_expected_revisions']
        and all(_basket(row.payload[key]) == _basket(quote[key]) for key in ('actor_offer', 'target_offer'))), None)


def _non_ammo_count(data, offer):
    from app.services.combat import CombatService
    from app.services.item_template_lookup import resolve_item_template

    count = 0
    for entry in offer:
        item = CombatService._inventory_item_at_path(data, entry['path'])
        template = resolve_item_template(item)
        if (template.category if template else item.get('category')) != 'ammo':
            count += entry['amount']
    return count


def _roll(data, skill, *, defending=False, bulk=False):
    from app.services.combat import CombatService
    bonuses = {'charisma': CombatService._base_skill_modifier(data, 'skills.social.charisma'),
               skill: CombatService._base_skill_modifier(data, f'skills.social.{skill}')}
    if defending:
        bonuses['will'] = CombatService._base_skill_modifier(data, 'skills.physical.will')
    advantage = skill == 'barter' and bulk and bool(trait_count(data, 'Оптовик'))
    rolls = [randint(1, 20) for _ in range(2 if advantage else 1)]
    roll = max(rolls)
    return {'roll': roll, 'rolls': rolls, 'advantage': advantage, 'bonuses': bonuses,
            'total': roll + sum(bonuses.values()), 'skill': skill}


@atomic_operation
def negotiate(location_id, user_id, payload):
    from app.services.character_interaction import CharacterInteractionService
    if not isinstance(payload, dict) or set(payload) - {
        'actor_location_character_id', 'target_character_id', 'offer', 'skill', 'expected_round',
    }:
        raise ValidationError('Некорректные параметры торга')
    skill, expected = payload.get('skill'), payload.get('expected_round')
    if not isinstance(skill, str) or skill not in SKILLS or type(expected) is not int or not 0 <= expected < MAX_ROUNDS:
        raise ValidationError('Выберите социальный навык и раунд торга от 1 до 3')
    location, actor, target = CharacterInteractionService._pair(
        location_id, user_id, payload.get('actor_location_character_id'), payload.get('target_character_id'))
    db.session.refresh(actor.character, with_for_update=True)
    if not enabled(target.character):
        raise ValidationError('Торг доступен у настроенного НПС-торговца')
    from app.services.combat import CombatService
    if CombatService._location_character_condition(target)['state'] != 'active':
        raise ValidationError('Торговец не может торговаться')
    require_contact(actor, target)
    offer = payload.get('offer')
    if not isinstance(offer, dict) or offer.get('merchant_mode') is not True:
        raise ValidationError('Выберите товары из прайса торговца')
    quote = prepare_payload(actor.character, target.character, offer, quote_only=True)
    difference = quote['purchase_total'] - quote['sale_total']
    if not difference:
        raise ValidationError('Сумма сделки равна нулю, торговаться не за что')
    draft = _matching_draft(actor, target, user_id, quote)
    rounds = (draft.result or {}).get('rounds', []) if draft else []
    if expected < len(rounds):
        return {'id': draft.id, **draft.result}
    if expected != len(rounds):
        raise ConflictError('Корзина или раунд торга изменились. Откройте торговлю заново.')
    if not draft:
        draft = CharacterInteractionRequest(location_id=location_id, actor_location_character_id=actor.id,
            target_location_character_id=target.id, actor_user_id=user_id,
            target_user_id=CharacterInteractionService._target_user_id(location, target),
            kind='merchant_haggle', status='negotiating', payload={**quote, 'pricing': 'net'})
        db.session.add(draft)
    actor_data, target_data = actor.character.data or {}, target.character.data or {}
    bulk = _non_ammo_count(actor_data, quote['actor_offer']) + _non_ammo_count(target_data, quote['target_offer']) >= 3
    actor_roll = _roll(actor_data, skill, defending=difference < 0, bulk=bulk)
    target_roll = _roll(target_data, 'barter', defending=difference > 0, bulk=bulk)
    change = (actor_roll['total'] > target_roll['total']) - (actor_roll['total'] < target_roll['total'])
    steps = (draft.result or {}).get('steps', 0) + change
    round_result = {'actor': actor_roll, 'target': target_roll, 'change': change}
    draft.result = {'rounds': [*rounds, round_result], 'steps': steps, 'max_rounds': MAX_ROUNDS,
                    **terms(quote['purchase_total'], quote['sale_total'], steps)}
    db.session.flush()
    return {'id': draft.id, **draft.result}


def resolve_trade(actor, target, user_id, quote, draft_id, round_number):
    db.session.refresh(actor.character, with_for_update=True)
    draft = _matching_draft(actor, target, user_id, quote)
    if not draft:
        if draft_id is not None or round_number is not None:
            raise ConflictError('Торг устарел. Откройте торговлю заново.')
        return
    if draft_id is None:
        raise ConflictError('По этой корзине уже был торг. Нажмите «Торговаться», чтобы открыть итоговую цену.')
    if type(draft_id) is not int or draft.id != draft_id:
        raise PermissionDenied('Эта цена относится к другой сделке')
    result = draft.result or {}
    if type(round_number) is not int or round_number != len(result.get('rounds', [])):
        raise ConflictError('Цена изменилась в следующем раунде торга')
    steps = result['steps']
    quote.update(terms(quote['purchase_total'], quote['sale_total'], steps))
    quote['_haggle_steps'] = steps
    draft.status = 'completed'
    draft.resolved_at = datetime.now(timezone.utc)
