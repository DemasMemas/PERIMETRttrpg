"""Регрессии по проверяемым авторским правилам для ручных карточек."""
from copy import deepcopy

from app.extensions import db
from app.models.templates import ItemTemplate
from test_quick_skill_checks import checks
from test_crafting import workshop, call


def test_medical_recipe_rejects_partial_mixture_of_alternatives(app):
    """Части разных вариантов не должны складываться в допустимый комплект."""
    from app.services.crafting import _allocate, recipe_by_id

    recipe = recipe_by_id('recipe-aa23')
    data = {'inventory': {'pockets': [
        {'name': 'Анальгин', 'category': 'consumable', 'quantity': 2},
        {'name': 'Бинт', 'category': 'consumable', 'quantity': 2},
    ]}}

    allocations, missing = _allocate(data, recipe)

    assert allocations == []
    assert missing, 'Две неполные альтернативы по 2 не заменяют одну целую по 3'


def test_start_with_partial_alternatives_rejects_without_reserving_materials_or_advancing_time(
    client, workshop, auth_headers,
):
    character = workshop['actor_character']
    data = deepcopy(character.data)
    data['skills']['other']['medicine'] = {'base': 15, 'bonus': 0}
    data['inventory']['pockets'] = [
        {'id': 'partial-pills', 'name': 'Анальгин', 'category': 'consumable', 'quantity': 2},
        {'id': 'partial-bandages', 'name': 'Бинт', 'category': 'consumable', 'quantity': 2},
    ]
    character.data = data
    db.session.add(ItemTemplate(name='Медицинские материалы', category='crafting_material', price=100))
    db.session.commit()
    before = deepcopy(character.data)
    revision = character.revision
    clock = (workshop['lobby'].game_day, workshop['lobby'].game_time_minutes)
    response = call(client, workshop, auth_headers, 'start', recipe_id='recipe-aa23')
    assert response.status_code == 400, response.json
    assert 'нет полного варианта' in str(response.json)
    db.session.refresh(character)
    assert character.data == before
    assert character.revision == revision
    assert (workshop['lobby'].game_day, workshop['lobby'].game_time_minutes) == clock
