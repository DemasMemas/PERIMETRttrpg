import random
from typing import Any, Dict, Optional

from app.services.effects import normalize_effect_list


WITHDRAWAL_DAYS = 28
MAX_DAILY_CHECKS = 5
_UNSET_PROFILE = object()


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dose_multiplier(dose, values):
    if dose <= 1 or not values:
        return 1.0
    steps = {int(key): _number(value, 1) for key, value in values.items() if str(key).isdigit()}
    if not steps:
        return 1.0
    index = min(dose, max(steps))
    while index not in steps and index > 1:
        index -= 1
    return steps.get(index, 1.0)


def addiction_profile(item_name: str, price=0) -> Optional[Dict[str, Any]]:
    name = str(item_name or '').strip().lower().replace('ё', 'е')
    price = _number(price, 0)
    if not name:
        return None

    exact = {
        'анальгин (таблетка)': ('analgin', 'Анальгин', 2, {2: 2, 3: 5, 4: 8, 5: 10, 6: 15}),
        'ибупрофен (таблетка)': ('ibuprofen', 'Ибупрофен', 1, {2: 1, 3: 3, 4: 5, 5: 8, 6: 10}),
        'настойка боярышника': ('hawthorn', 'Настойка боярышника', 3, {2: 5, 3: 7, 4: 10, 5: 15, 6: 30}),
        'питательный стимулятор': ('nutritional_stimulant', 'Питательный стимулятор', 4, {2: 2, 3: 4, 4: 5, 5: 10, 6: 15}),
        'борщевик': ('borshevik', 'Борщевик', 25, {2: 2, 3: 2, 4: 3, 5: 3, 6: 4}),
    }
    if name in exact:
        key, label, chance, multipliers = exact[name]
        return {'key': key, 'label': label, 'chance': chance, 'multipliers': multipliers, 'kind': 'medicine'}

    if 'банка пива' in name:
        return {'key': 'alcohol', 'label': 'Алкоголь', 'chance': 1, 'multipliers': {}, 'kind': 'alcohol'}
    if 'водк' in name:
        return {'key': 'alcohol', 'label': 'Алкоголь', 'chance': 3, 'multipliers': {}, 'kind': 'alcohol'}
    if 'самогон' in name:
        return {'key': 'alcohol', 'label': 'Алкоголь', 'chance': 5, 'multipliers': {}, 'kind': 'alcohol'}
    if 'вино' in name:
        return {'key': 'alcohol', 'label': 'Алкоголь', 'chance': 1, 'multipliers': {}, 'kind': 'alcohol'}

    if any(token in name for token in ('самокрут', 'сигарет', 'сигарилл', 'сигар')):
        if 'самокрут' in name:
            chance = 5
        elif 'сигарилл' in name:
            chance = 2
        elif 'сигар' in name and 'сигарет' not in name:
            chance = 1
        elif price and price <= 450:
            chance = 4
        elif price and price <= 600:
            chance = 3
        elif price and price <= 800:
            chance = 2
        else:
            chance = 2
        return {'key': 'nicotine', 'label': 'Никотин', 'chance': chance, 'multipliers': {}, 'kind': 'nicotine'}

    if 'кофе' in name or 'энергетик' in name:
        return {'key': 'caffeine', 'label': 'Кофеин', 'chance': 1, 'multipliers': {}, 'kind': 'caffeine'}

    pain_regen = any(token in name for token in (
        'аспирин', 'обезболивающ', 'морфин', 'миколий', 'варвар', 'викинг', 'препарат 02',
        'регенеративный стимулятор', 'стимпак',
    ))
    if pain_regen:
        key,label=('stimpaks','Стимпаки') if 'стимпак' in name else (
            ('regenerative_stimulants','Регенеративные стимуляторы') if any(t in name for t in ('регенератив','миколий','препарат 02'))
            else ('painkiller_stimulants','Обезболивающие стимуляторы'))
        return {
            'key': key, 'label': label,
            'chance': 10, 'multipliers': {2: 3, 3: 5, 4: 7, 5: 8}, 'kind': 'medicine',
        }
    accelerating = any(token in name for token in (
        'адреналин', 'эпинефрин', 'грация', 'б.о.л.т.', 'болид', 'ускоряющий',
    ))
    if accelerating:
        return {
            'key': 'accelerating_stimulants', 'label': 'Ускоряющие стимуляторы',
            'chance': 25, 'multipliers': {2: 2}, 'kind': 'medicine',
        }
    combat = any(token in name for token in (
        'стимулятор орел', 'стимулятор пчела', 'стимулятор волкодав',
    ))
    if combat:
        return {
            'key': 'combat_stimulants', 'label': 'Боевые стимуляторы',
            'chance': 20, 'multipliers': {2: 1.5, 3: 2, 4: 3}, 'kind': 'medicine',
        }
    return None


def resolve_addiction_profile(consumable_profile, template_name, price=0):
    if isinstance(consumable_profile, dict) and 'addiction' in consumable_profile:
        stored=consumable_profile['addiction']
        if isinstance(stored,dict) and stored.get('key')=='pain_regen_stimulants':
            resolved=addiction_profile(template_name,price)
            if resolved:return {**stored,'key':resolved['key'],'label':resolved['label']}
        return stored
    # Existing templates have no stored addiction rule yet. Keep their old
    # behavior, but never infer it from a mutable inventory display name.
    return addiction_profile(template_name, price)


def initial_addiction(health,item_name,source,day=1):
    profile=addiction_profile(item_name)
    if not profile:raise ValueError('Не найден профиль начальной зависимости')
    state=_state(health)
    record=state['records'].setdefault(profile['key'],{
        'key':profile['key'],'label':profile['label'],'kind':profile['kind'],'active':True,
        'acquired_day':day,'satisfied_days':[day],'withdrawal_days':0,'withdrawal_remaining':WITHDRAWAL_DAYS,
        'withdrawal_stage':0,'checks':{'day':day,'attempts':0,'successes':0,'reduction_applied':0},'creationSources':[]})
    if source not in record['creationSources']:record['creationSources'].append(source)
    return record


def _state(health):
    state = health.get('addictions')
    if not isinstance(state, dict):
        state = {}
        health['addictions'] = state
    state.setdefault('records', {})
    state.setdefault('exposures', {})
    return state


def _withdrawal_effect_id(key):
    return f'addiction-withdrawal-{key}'


def remove_addiction(health, addiction_key):
    state = _state(health)
    key = str(addiction_key)
    record = state['records'].pop(key, None)
    if record is None:
        raise ValueError('Зависимость не найдена')
    state['exposures'].pop(key, None)
    health['effects'] = [
        effect for effect in normalize_effect_list(health.get('effects') or [])
        if effect.get('id') != _withdrawal_effect_id(key)
    ]
    return record


def _daily_progress(record, day):
    progress = record.get('daily_progress')
    if not isinstance(progress, dict) or int(progress.get('day', 0)) != day:
        progress = {
            'day': day,
            'intoxication': 0.0,
            'exhaustion_relief': 0.0,
            'uses': 0,
        }
        record['daily_progress'] = progress
    return progress


def _satisfy_record(health, record, day):
    satisfied_days = record.setdefault('satisfied_days', [])
    if day not in satisfied_days:
        satisfied_days.append(day)
    record['withdrawal_days'] = 0
    record['withdrawal_remaining'] = WITHDRAWAL_DAYS
    record['withdrawal_stage'] = 0
    _set_withdrawal_effect(health, record, False)


def _record_daily_prevention(
    health, state, profile, day, intoxication, exhaustion_relief,
):
    for record in state['records'].values():
        if not record.get('active', True):
            continue
        progress = _daily_progress(record, day)
        kind = record.get('kind')
        same_dependency = record.get('key') == profile.get('key')
        if kind == 'alcohol' and (
            profile.get('kind') == 'alcohol' or profile.get('key') == 'hawthorn'
        ):
            progress['intoxication'] += max(0, _number(intoxication, 0))
        elif kind == 'nicotine' and profile.get('kind') == 'nicotine':
            progress['exhaustion_relief'] += max(0, _number(exhaustion_relief, 0))
        elif kind in {'caffeine', 'medicine'} and same_dependency:
            progress['uses'] += 1

        satisfied = (
            kind == 'alcohol' and progress['intoxication'] >= 50
            or kind == 'nicotine' and progress['exhaustion_relief'] >= 1
            or kind in {'caffeine', 'medicine'} and progress['uses'] >= 1
        )
        if satisfied:
            _satisfy_record(health, record, day)


def _set_withdrawal_effect(health, record, active):
    effects = normalize_effect_list(health.get('effects') or [])
    effect_id = _withdrawal_effect_id(record['key'])
    effects = [effect for effect in effects if str(effect.get('id')) != effect_id]
    if active:
        stage = max(1, min(4, int(record.get('withdrawal_stage') or 1)))
        penalties = {
            1: {'accuracy': 1, 'will': 1, 'agility': 1},
            2: {'accuracy': 2, 'will': 2, 'agility': 1},
            3: {'accuracy': 2, 'will': 3, 'agility': 2},
            4: {'accuracy': 3, 'will': 5, 'agility': 3},
        }[stage]
        effects.append({
            'id': effect_id,
            'type': 'addiction_withdrawal',
            'name': f"Ломка: {record['label']} (неделя {stage})",
            'source': record['key'],
            'active': True,
            'tick': 'day_start',
            'remaining': record.get('withdrawal_remaining', WITHDRAWAL_DAYS),
            'note': (
                f"Точность -{penalties['accuracy']}, Воля -{penalties['will']}, "
                f"Ловкость -{penalties['agility']}."
            ),
            'modifiers': {
                'skills.physical.accuracy': penalties['accuracy'],
                'skills.physical.shooting': penalties['accuracy'],
                'skills.physical.will': penalties['will'],
                'skills.physical.agility': penalties['agility'],
            },
            'addiction_key': record['key'],
            'withdrawal_stage': stage,
        })
    health['effects'] = effects


def record_exposure(
    health, item_name, price, absolute_minute, intoxication=0,
    exhaustion_relief=0, addiction_block_hours=0, profile=_UNSET_PROFILE,
):
    meta = health.setdefault('combatMeta', {})
    block_hours = max(
        _number(addiction_block_hours, 0),
        _number(meta.get('addictionBlockHours'), 0),
    )
    if block_hours > 0:
        meta['addictionBlockedUntilMinute'] = max(
            int(meta.get('addictionBlockedUntilMinute') or 0),
            int(absolute_minute + round(block_hours * 60)),
        )
        meta['addictionBlockHours'] = 0

    if profile is _UNSET_PROFILE:
        profile = addiction_profile(item_name, price)
    if not profile:
        return {
            'applicable': False,
            'blocked_until': int(meta.get('addictionBlockedUntilMinute') or 0),
        }
    state = _state(health)
    day = max(1, int(absolute_minute // 1440))
    exposure = state['exposures'].setdefault(profile['key'], {})
    previous_minute = int(exposure.get('last_minute', -1000000))
    dose = int(exposure.get('dose', 0)) + 1 if absolute_minute - previous_minute <= 10 else 1
    multiplier = _dose_multiplier(dose, profile['multipliers'])
    chance = min(100.0, profile['chance'] * multiplier)
    roll = random.random() * 100
    exposure.update({'last_minute': absolute_minute, 'dose': dose, 'chance': chance, 'item': item_name})

    blocked_until = int(meta.get('addictionBlockedUntilMinute') or 0)
    blocked = absolute_minute < blocked_until

    record = state['records'].get(profile['key'])
    acquired = False
    if (not record or not record.get('active', True)) and not blocked and roll < chance:
        record = {
            'key': profile['key'], 'label': profile['label'], 'kind': profile['kind'],
            'active': True, 'acquired_day': day, 'satisfied_days': [],
            'withdrawal_days': 0, 'withdrawal_remaining': WITHDRAWAL_DAYS,
            'checks': {'day': day, 'attempts': 0, 'successes': 0, 'reduction_applied': 0},
        }
        state['records'][profile['key']] = record
        acquired = True
    _record_daily_prevention(
        health, state, profile, day, intoxication, exhaustion_relief,
    )
    return {
        'applicable': True, 'profile': profile, 'dose': dose, 'multiplier': multiplier,
        'chance': chance, 'roll': round(roll, 2), 'blocked': blocked,
        'acquired': acquired, 'record': record,
    }


def advance_addictions(health, previous_day, next_day):
    state = _state(health)
    changed = []
    for day in range(max(1, int(previous_day)), max(1, int(next_day))):
        for record in state['records'].values():
            if not record.get('active', True):
                continue
            if day in record.get('satisfied_days', []):
                record['withdrawal_days'] = 0
                record['withdrawal_remaining'] = WITHDRAWAL_DAYS
                _set_withdrawal_effect(health, record, False)
                continue
            record['withdrawal_days'] = int(record.get('withdrawal_days', 0)) + 1
            record['withdrawal_remaining'] = max(0, int(record.get('withdrawal_remaining', WITHDRAWAL_DAYS)) - 1)
            record['withdrawal_stage'] = min(4, (record['withdrawal_days'] - 1) // 7 + 1)
            record['checks'] = {'day': day + 1, 'attempts': 0, 'successes': 0, 'reduction_applied': 0}
            if record['withdrawal_remaining'] <= 0:
                record['active'] = False
                record['recovered_day'] = day + 1
                _set_withdrawal_effect(health, record, False)
            else:
                _set_withdrawal_effect(health, record, True)
            changed.append(record['key'])
    return changed


def withdrawal_check(health, addiction_key, day, will_bonus, difficulty_reduction=0):
    state = _state(health)
    record = state['records'].get(str(addiction_key))
    if not record or not record.get('active', True) or int(record.get('withdrawal_days', 0)) <= 0:
        raise ValueError('У персонажа нет активной ломки этого типа')
    checks = record.get('checks')
    if not isinstance(checks, dict) or int(checks.get('day', 0)) != int(day):
        checks = {'day': int(day), 'attempts': 0, 'successes': 0, 'reduction_applied': 0}
        record['checks'] = checks
    if int(checks.get('attempts', 0)) >= MAX_DAILY_CHECKS:
        raise ValueError('За сутки уже выполнено пять проверок ломки')
    difficulty = max(1, 15 - int(will_bonus) - max(0, int(difficulty_reduction)))
    roll = random.randint(1, 20)
    success = roll == 20 or (roll != 1 and roll >= difficulty)
    checks['attempts'] = int(checks.get('attempts', 0)) + 1
    if success:
        checks['successes'] = int(checks.get('successes', 0)) + 1
    target_reduction = {3: 1, 4: 3, 5: 7}.get(int(checks.get('successes', 0)), 0)
    extra_reduction = max(0, target_reduction - int(checks.get('reduction_applied', 0)))
    if extra_reduction:
        record['withdrawal_remaining'] = max(0, int(record.get('withdrawal_remaining', WITHDRAWAL_DAYS)) - extra_reduction)
        checks['reduction_applied'] = target_reduction
    if int(record.get('withdrawal_remaining', 0)) <= 0:
        record['active'] = False
        record['recovered_day'] = int(day)
        _set_withdrawal_effect(health, record, False)
    else:
        _set_withdrawal_effect(health, record, True)
    return {
        'roll': roll, 'difficulty': difficulty, 'will_bonus': int(will_bonus),
        'difficulty_reduction': max(0, int(difficulty_reduction)), 'success': success,
        'attempts': checks['attempts'], 'successes': checks['successes'],
        'days_reduced': extra_reduction,
        'remaining': record.get('withdrawal_remaining', 0), 'record': record,
    }
