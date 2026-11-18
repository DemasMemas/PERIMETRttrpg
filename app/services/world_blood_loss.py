"""Проверка кровопотери при завершении десятиминутного мирового хода."""
import random

from app.services.combat import CombatService
from app.services.effects import (
    BLEEDING_STAGE_ORDER,
    apply_effect_to_health,
    get_bleeding_state,
    normalize_effect_list,
    sync_health_derived_statuses,
)


def blood_loss_plan(data):
    health = data.get('health') or {}
    bleeding = get_bleeding_state(health)
    if not bleeding['effects'] or CombatService._character_condition(data)['state'] == 'dead':
        return None
    if any(effect['type'] == 'blood_loss_freeze' and effect.get('active', True)
           for effect in normalize_effect_list(health.get('effects') or [])):
        return None

    profile = CombatService._bleeding_check_profile({**data, 'health': {**health, 'bleeding': bleeding}})
    if profile['severity'] <= 0:
        return None

    stages = BLEEDING_STAGE_ORDER
    current = stages[bleeding['stagePenalty']]
    strongest = max((effect['stage'] for effect in bleeding['effects']),
                    key=('light', 'medium', 'severe', 'extreme').index)
    failure_steps = 3 if strongest in {'severe', 'extreme'} else 2
    last = len(stages) - 1
    return {
        'severity': strongest,
        'difficulty': profile['difficulty'],
        'before': current,
        'on_success': stages[min(last, bleeding['stagePenalty'] + 1)],
        'on_failure': stages[min(last, bleeding['stagePenalty'] + failure_steps)],
    }


def resolve_world_blood_loss(data):
    plan = blood_loss_plan(data)
    if not plan:
        return None

    roll = random.randint(1, 20)
    success = roll >= plan['difficulty']
    stage = plan['on_success'] if success else plan['on_failure']
    health = data['health']
    health['blood'] = health['bloodStage'] = stage
    if stage == 'fatal':
        apply_effect_to_health(health, {'type': 'death', 'source': 'blood_loss', 'tick': 'manual'})
    sync_health_derived_statuses(health)
    return {**plan, 'roll': roll, 'success': success, 'after': stage}
