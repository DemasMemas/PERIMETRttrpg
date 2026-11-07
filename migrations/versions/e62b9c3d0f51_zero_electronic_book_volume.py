"""Электронные книги не занимают объём в каталоге и сохранённых инвентарях."""
from copy import deepcopy

from alembic import op
import sqlalchemy as sa

revision = 'e62b9c3d0f51'
down_revision = 'd51a8b2e7c40'
branch_labels = None
depends_on = None


def _normalize_items(value):
    changed = False
    if isinstance(value, list):
        for item in value:
            changed = _normalize_items(item) or changed
    elif isinstance(value, dict):
        if (value.get('attributes') or {}).get('book_kind') in {'skill', 'equipment'} and value.get('volume') == 1:
            value['volume'] = 0
            changed = True
        for item in value.values():
            changed = _normalize_items(item) or changed
    return changed


def upgrade():
    templates = sa.table('item_templates', sa.column('id', sa.Integer),
                         sa.column('category', sa.String), sa.column('weight', sa.Float),
                         sa.column('volume', sa.Float), sa.column('attributes', sa.JSON))
    characters = sa.table('lobby_characters', sa.column('id', sa.Integer),
                          sa.column('data', sa.JSON), sa.column('revision', sa.Integer))
    bind = op.get_bind()
    for row in bind.execute(sa.select(templates).where(templates.c.category == 'misc',
                                                       templates.c.volume == 1)).mappings():
        attrs = row['attributes'] or {}
        if attrs.get('import_source') == 'equipment_workbook' and attrs.get('book_kind') in {'skill', 'equipment'} and row['weight'] == 0:
            bind.execute(templates.update().where(templates.c.id == row['id']).values(volume=0))
    for row in bind.execute(sa.select(characters)).mappings():
        data = deepcopy(row['data'] or {})
        changed = _normalize_items(data.get('inventory'))
        changed = _normalize_items(data.get('equipment')) or changed
        if changed:
            bind.execute(characters.update().where(characters.c.id == row['id']).values(
                data=data, revision=row['revision'] + 1))


def downgrade():
    # Уже сохранённые нулевые объёмы и изменения мастера не возвращаем к 1.
    pass
