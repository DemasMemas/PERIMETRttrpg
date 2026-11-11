"""Приготовление мяса мутантов и эффекты порций из утверждённых правил."""
from copy import deepcopy
from math import isfinite

from app.services.exceptions import ValidationError


RECIPES = {'recipe-aa15': 'fried', 'recipe-aa16': 'boiled'}
NAMES = {'fried': 'Мясо(жареное) (3 использования)',
         'boiled': 'Мясо(вареное) (3 использования)'}


def templates():
    result = []
    for kind, name in NAMES.items():
        result.append({'name': name, 'category': 'consumable', 'subcategory': 'Продукты',
            'item_class': 'Еда', 'description': 'Три порции мяса мутанта. Эффект зависит от вида и приготовления.',
            'price': 0, 'weight': 1, 'volume': 1,
            'attributes': {'section': 'Продукты', 'cooked_meat': kind, 'uses': 3,
                'unspecified_fields': ['price'],
                'consumable': {'direct': {'uses': 3, 'nutrition': 1}, 'effects': [],
                    'modifiers': [], 'status_removals': [], 'status_additions': [], 'requirements': []}},
            'compatible_ids': []})
    return result


def ingredient_matches(recipe_id, item):
    if recipe_id not in {*RECIPES, 'recipe-aa8'} or not isinstance(item, dict):
        return False
    attrs = item.get('attributes') or {}
    species = str(attrs.get('source_mutant') or '').strip().casefold().replace('ё', 'е')
    raw = bool(species) and attrs.get('mutant_part') == 'meat'
    boiled = bool(species) and attrs.get('cooked_meat') == 'boiled'
    if recipe_id == 'recipe-aa8':
        return boiled and _radiation(attrs.get('meat_remaining_radiation')) is not None
    if recipe_id == 'recipe-aa15':
        return raw and species != 'туша' and _radiation(attrs.get('radiation')) is not None
    return (raw and _radiation(attrs.get('radiation')) is not None
            or boiled and _radiation(attrs.get('meat_original_radiation')) is not None
            and _radiation(attrs.get('meat_remaining_radiation')) is not None)


def _radiation(value):
    if type(value) not in {int, float} or value < 0 or not isfinite(value):
        return None
    return float(value)


def cooked_output(source, recipe_id, output, *, cook_bonus=0):
    """Сохраняет исходный вид и снижает радиацию от исходного максимума."""
    if recipe_id not in RECIPES or not ingredient_matches(recipe_id, source):
        raise ValidationError('Для приготовления нужно мясо подходящего мутанта')
    attrs = source['attributes']
    original = _radiation(attrs.get('meat_original_radiation', attrs.get('radiation')))
    current = _radiation(attrs.get('meat_remaining_radiation', attrs.get('radiation')))
    if original is None or current is None or current > original:
        raise ValidationError('Некорректная радиация мяса')
    kind = RECIPES[recipe_id]
    reduction = original * (0.75 if kind == 'fried' else 0.5 if str(attrs.get('source_mutant') or '').strip().casefold() == 'туша' else 0.25)
    remaining = max(0, current - reduction)
    new_attrs = output.setdefault('attributes', {})
    new_attrs.update(cooked_meat=kind, source_mutant=attrs.get('source_mutant'),
        meat_original_radiation=original, meat_remaining_radiation=remaining,
        radiation=remaining, cooking_count=int(attrs.get('cooking_count') or 0) + 1)
    bonus = max(cook_bonus, _cook_bonus(attrs))
    if bonus:
        new_attrs['cook_exhaustion_bonus'] = bonus
    output['price'] = source.get('price', 0)
    if attrs.get('cooked_meat') == 'boiled':
        output['uses'] = source.get('uses', 3)
        new_attrs['uses_remaining'] = output['uses']
    return output


def _cook_bonus(attrs):
    bonus = attrs.get('cook_exhaustion_bonus')
    return bonus if type(bonus) is int and bonus > 0 else 0


def portion_profile(template, item, profile, direct):
    if (template.attributes or {}).get('cooked_meat') not in NAMES:
        return profile, direct
    attrs = item.get('attributes') or {}
    remaining = _radiation(attrs.get('meat_remaining_radiation'))
    original = _radiation(attrs.get('meat_original_radiation'))
    if remaining is None or original is None or remaining > original:
        raise ValidationError('У мяса нет подтверждённого профиля приготовления')
    profile, direct = deepcopy(profile), deepcopy(direct)
    direct.update(nutrition=1, radiation_delta=remaining / 3,
                  exhaustion_delta=(0.5 if remaining else -1) - _cook_bonus(attrs))
    profile['direct'] = direct
    return profile, direct
