"""Восстановить правило «любая радиоактивность» для СОР-5."""

from copy import deepcopy

from alembic import op
import sqlalchemy as sa


revision = 'c61f4be88320'
down_revision = 'b92cf548a130'
branch_labels = None
depends_on = None

NAME = 'Контейнер для артефактов СОР-5'
OLD_DESCRIPTION = 'Радиация артефакта до 0. Положить артефакт: 4 ОД; вынуть: 8 ОД.'
DESCRIPTION = 'Допускаются артефакты с любой радиоактивностью. Положить артефакт: 4 ОД; вынуть: 8 ОД.'


def _correct_item(item):
    attrs = item.get('attributes')
    if (item.get('name') != NAME or item.get('category') != 'container'
            or not isinstance(attrs, dict) or attrs.get('import_source') != 'equipment_workbook'
            or attrs.get('artifact_container_kind') != 'storage' or attrs.get('radiation_limit') != 0
            or OLD_DESCRIPTION not in (attrs.get('source_description'), item.get('description'))):
        return False
    attrs['radiation_limit'] = None
    if attrs.get('source_description') == OLD_DESCRIPTION:
        attrs['source_description'] = DESCRIPTION
    if item.get('description') == OLD_DESCRIPTION:
        item['description'] = DESCRIPTION
    return True


def _correct_inventory(value):
    if isinstance(value, dict):
        corrected = _correct_item(value)
        children = value.values()
    elif isinstance(value, list):
        corrected = False
        children = value
    else:
        return False
    for child in children:
        corrected = _correct_inventory(child) or corrected
    return corrected


def upgrade():
    templates = sa.table('item_templates', sa.column('id', sa.Integer),
                         sa.column('name', sa.String), sa.column('category', sa.String),
                         sa.column('description', sa.Text), sa.column('attributes', sa.JSON))
    characters = sa.table('lobby_characters', sa.column('id', sa.Integer),
                          sa.column('data', sa.JSON), sa.column('revision', sa.Integer))
    bind = op.get_bind()
    for row in bind.execute(sa.select(templates).where(templates.c.name == NAME)).mappings():
        item = dict(row)
        if _correct_item(item):
            bind.execute(templates.update().where(templates.c.id == row['id']).values(
                attributes=item['attributes'], description=item['description']))
    for row in bind.execute(sa.select(characters)).mappings():
        data = deepcopy(row['data'] or {})
        inventory_corrected = _correct_inventory(data.get('inventory'))
        equipment_corrected = _correct_inventory(data.get('equipment'))
        if inventory_corrected or equipment_corrected:
            bind.execute(characters.update().where(characters.c.id == row['id']).values(
                data=data, revision=row['revision'] + 1))


def downgrade():
    # Исправленное значение и сохранённые экземпляры оставляем при откате.
    pass
