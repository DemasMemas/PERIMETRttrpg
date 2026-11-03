"""Passive artifact modifiers derived from equipped artifact rule text."""

from __future__ import annotations

import re


def equipped_artifacts(character_data):
    equipment = character_data.get('equipment') if isinstance(character_data, dict) else {}
    armor = equipment.get('armor') if isinstance(equipment, dict) else None
    if not isinstance(armor, dict):
        return []
    found = []
    for slot in armor.get('containers') or []:
        item = slot.get('item') if isinstance(slot, dict) else None
        if not isinstance(item, dict):
            continue
        if item.get('category') == 'artifact':
            found.append(item)
        for module in item.get('installedModules') or []:
            if isinstance(module, dict) and (
                module.get('category') == 'artifact'
                or module.get('slotType') == 'artifact'
            ):
                found.append(module)
    return found


def _signed(text, pattern):
    match = re.search(pattern + r'\s*([+-]\d+)', text, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _percent(text, pattern):
    match = re.search(pattern + r'\s*([+-]\d+)%', text, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _radiation_value(text, *, positive):
    total = 0
    for value in re.findall(
        r'([+-]?\d+)\s*Радиаци\w* за перемещение', text, re.IGNORECASE,
    ):
        # In the positive column an unsigned value is removal; in the negative
        # column it is accumulation. Explicit signs always win.
        if value.startswith(('+', '-')):
            total += int(value)
        else:
            total += -int(value) if positive else int(value)
    return total


def _conditional_value(text, pattern, radiation):
    total = 0
    matches = list(re.finditer(
        pattern + r'\s*([+-]\d+),?\s*Если накоплено хотя бы\s*(\d+)\s*Радиаци',
        text,
        re.IGNORECASE,
    ))
    for match in matches:
        if radiation >= int(match.group(2)):
            total += int(match.group(1))
    cleaned = text
    for match in reversed(matches):
        cleaned = cleaned[:match.start()] + cleaned[match.end():]
    return total, cleaned


def artifact_passive_profile(character_data):
    result = {
        'skill_all': 0, 'skill_physical': 0, 'skill_other': 0,
        'skill_strength': 0, 'skill_will': 0, 'skill_awareness': 0,
        'skill_stealth': 0, 'accuracy': 0, 'movement_penalty': 0,
        'melee_damage_percent': 0, 'incoming_physical_percent': 0,
        'armor_damage_reduction': 0, 'throw_range_bonus': 0,
        'strenuous_action_cost': 0, 'breath_duration_bonus': 0,
        'bleeding_severity': 0, 'radiation_per_movement': 0,
        'health_per_movement': 0, 'pain_per_movement': 0,
        'healing_multiplier': 1.0,
        'protection': {key: 0 for key in ('physical', 'chemical', 'thermal', 'electric', 'radiation', 'psi')},
    }
    health = character_data.get('health') if isinstance(character_data, dict) else {}
    radiation_level = float((health or {}).get('radiation') or 0)
    for artifact in equipped_artifacts(character_data):
        attrs = artifact.get('attributes') if isinstance(artifact.get('attributes'), dict) else {}
        positive = str(attrs.get('positive_effect') or '')
        negative = str(attrs.get('negative_effect') or '')
        text = f'{positive} {negative}'
        conditional_movement, text = _conditional_value(
            text, r'Штраф перемещения', radiation_level,
        )
        conditional_accuracy, text = _conditional_value(
            text, r'Точность', radiation_level,
        )
        conditional_all_protection, text = _conditional_value(
            text, r'Все защиты', radiation_level,
        )
        protection_names = {
            'physical': 'физического', 'chemical': 'химического',
            'thermal': 'термического', 'electric': 'электрического',
            'radiation': 'радиации', 'psi': 'псионического',
        }
        conditional_protection = {}
        for key, label in protection_names.items():
            conditional_protection[key], text = _conditional_value(
                text,
                rf'Защита от {label}(?: урона)?',
                radiation_level,
            )
        result['skill_physical'] += _signed(text, r'Физические навыки')
        result['skill_other'] += _signed(text, r'Прочие навыки')
        result['skill_strength'] += _signed(text, r'Бонус Силы')
        result['skill_will'] += _signed(text, r'Воля')
        result['skill_awareness'] += _signed(text, r'Внимательность')
        result['skill_stealth'] += _signed(text, r'Скрытность')
        result['accuracy'] += _signed(text, r'Точность') + conditional_accuracy
        result['movement_penalty'] += (
            _signed(text, r'Штраф перемещения') + conditional_movement
        )
        result['melee_damage_percent'] += _percent(text, r'Урон в ближнем бою')
        result['incoming_physical_percent'] += _percent(text, r'Входящий физический урон')
        armor_reduction = re.search(
            r'Получаемый урон по броне снижен на\s*(\d+)', text, re.IGNORECASE,
        )
        if armor_reduction:
            result['armor_damage_reduction'] += int(armor_reduction.group(1))
        result['throw_range_bonus'] += _signed(
            text, r'Дополнительная дальность метания',
        )
        strenuous = re.search(
            r'Бег и Спринт занимают на\s*(\d+)\s*ОД больше', text, re.IGNORECASE,
        )
        if strenuous:
            result['strenuous_action_cost'] += int(strenuous.group(1))
        breath = re.search(r'Одышка на\s*(\d+)\s*раунд\w* дольше', text, re.IGNORECASE)
        if breath:
            result['breath_duration_bonus'] += int(breath.group(1))
        result['bleeding_severity'] += _signed(text, r'Тяжесть кровотечений')
        result['radiation_per_movement'] += _radiation_value(
            positive, positive=True,
        ) + _radiation_value(negative, positive=False)
        health_values = re.findall(r'Общее здоровье\s*([+-]\d+)\s*за перемещение', text, re.IGNORECASE)
        result['health_per_movement'] += sum(map(int, health_values))
        pain_up = re.findall(r'Усиливает боль на\s*(\d+)\s*уров', text, re.IGNORECASE)
        pain_down = re.findall(r'Облегчает боль на\s*(\d+)\s*уров', text, re.IGNORECASE)
        result['pain_per_movement'] += sum(map(int, pain_up)) - sum(map(int, pain_down))
        healing = re.search(r'Восстановление здоровья из любых источников\s*([\d.,]+)х', text, re.IGNORECASE)
        if healing:
            result['healing_multiplier'] *= float(healing.group(1).replace(',', '.'))
        all_protection = _percent(text, r'Все защиты') + conditional_all_protection
        for key, label in protection_names.items():
            result['protection'][key] += all_protection + conditional_protection[key]
            result['protection'][key] += _percent(
                text, rf'Защита от {label}(?: урона)?',
            )
    return result


def apply_artifact_world_movement(character_data):
    """Apply effects explicitly defined as occurring once per world movement."""
    profile = artifact_passive_profile(character_data)
    health = character_data.setdefault('health', {})
    before = {
        key: health.get(key)
        for key in ('current', 'radiation', 'painLevel')
    }
    health_delta = profile['health_per_movement']
    if health_delta:
        maximum = max(0, float(health.get('max') or health.get('maximum') or 700))
        health['current'] = min(
            maximum,
            max(0, float(health.get('current') or 0) + health_delta),
        )
    radiation_delta = profile['radiation_per_movement']
    if radiation_delta:
        health['radiation'] = min(
            100,
            max(0, float(health.get('radiation') or 0) + radiation_delta),
        )
    pain_delta = profile['pain_per_movement']
    if pain_delta:
        from app.services.effects import apply_effect_to_health
        apply_effect_to_health(health,{'type':'pain','value':pain_delta,'source':'artifact_movement'},character_data=character_data)
    after = {key: health.get(key) for key in before}
    return {
        'changed': before != after,
        'before': before,
        'after': after,
        'health_delta': health_delta,
        'radiation_delta': radiation_delta,
        'pain_delta': pain_delta,
    }
