"""Выбор каталожного калибра для рецептов обычных патронов и БП/ЭП."""
from copy import deepcopy

from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.exceptions import ValidationError


BASE_RECIPES = {'recipe-v25': 1, 'recipe-v26': 2, 'recipe-v27': 3}
VARIANTS = {'base': ('Обычные', 0, 0), 'bp': ('БП', 30, 15), 'ep': ('ЭП', 25, 10)}


def available_ammo():
    result = []
    for template in ItemTemplate.query.filter_by(category='ammo').order_by(ItemTemplate.name).all():
        attrs = template.attributes or {}
        if str(template.item_class) not in {'1', '2', '3'} or str(attrs.get('purchase_category')) != str(template.item_class):
            continue
        if not attrs.get('caliber') or (attrs.get('craft_only') is True):
            continue
        result.append({'id': template.id, 'name': template.name, 'category': int(template.item_class),
                       'variants': [kind for kind in ('bp', 'ep') if kind in (attrs.get('ammo_variants') or [])],
                       'price': template.price})
    return result


def chosen_ammo(recipe, payload):
    category = BASE_RECIPES.get(recipe['id'])
    if category is None:
        return recipe, None, None
    template_id = payload.get('ammo_template_id')
    if type(template_id) is not int:
        raise ValidationError('Выберите конкретный калибр патронов')
    template = db.session.get(ItemTemplate, template_id)
    options = {entry['id']: entry for entry in available_ammo()}
    if template_id not in options or not template or options[template_id]['category'] != category:
        raise ValidationError('Калибр не соответствует категории рецепта')
    variant = payload.get('ammo_variant', 'base')
    if not isinstance(variant, str) or variant not in VARIANTS:
        raise ValidationError('Неизвестный вид патронов')
    if variant != 'base' and variant not in options[template_id]['variants']:
        raise ValidationError('В каталоге для этого калибра нет выбранного вида БП/ЭП')
    selected = deepcopy(recipe)
    selected['output_name'] = template.name
    selected['name'] = f"{template.name} · {VARIANTS[variant][0]}"
    selected['ammo_variant'] = variant
    selected['ammo_template_id'] = template.id
    # Уточнённый автором пример для второго класса имеет один порох и СЛ 65.
    if category == 2:
        selected['components'][2]['quantity'] = 1
        selected['difficulty_options'] = [65]
    if variant != 'base':
        label, rounds, difficulty = VARIANTS[variant]
        selected['components'] = [
            {'name': template.name, 'quantity': rounds, 'source_text': f'{template.name} × {rounds}',
             'ammo_template_id': template.id},
            {'name': 'Металлические детали', 'quantity': category + (1 if variant == 'bp' else 0),
             'source_text': f"Металлические детали × {category + (1 if variant == 'bp' else 0)}"},
        ]
        selected['output_count'] = 20
        selected['difficulty_options'] = [selected['difficulty_options'][0] + difficulty]
    return selected, template, variant


def is_plain_ammo(item, template):
    if item.get('category') != 'ammo' or item.get('templateId') != template.id or item.get('name') != template.name:
        return False
    attrs = item.get('attributes') or {}
    allowed = {None, '', 'base', (template.attributes or {}).get('ammo_variant')}
    return item.get('ammo_variant') in allowed and attrs.get('ammo_variant') in allowed
