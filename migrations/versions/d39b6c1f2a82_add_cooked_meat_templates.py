"""Добавлены шаблоны жареного и варёного мяса для рецептов."""
from alembic import op
import sqlalchemy as sa

revision = 'd39b6c1f2a82'
down_revision = 'c28f5b0e1a71'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.equipment_catalog import load_catalog
    from app.services.cooked_meat import NAMES

    table = sa.table('item_templates', sa.column('id', sa.Integer), sa.column('name', sa.String),
        sa.column('category', sa.String), sa.column('subcategory', sa.String), sa.column('item_class', sa.String),
        sa.column('description', sa.Text), sa.column('price', sa.Integer), sa.column('weight', sa.Float),
        sa.column('volume', sa.Float), sa.column('attributes', sa.JSON), sa.column('compatible_ids', sa.JSON))
    bind = op.get_bind()
    known = set(bind.execute(sa.select(table.c.name).where(table.c.category == 'consumable')).scalars())
    for item in load_catalog():
        if item['name'] in NAMES.values() and item['name'] not in known:
            bind.execute(table.insert().values(**item))
            known.add(item['name'])


def downgrade():
    # Существующие экземпляры могут ссылаться на шаблоны.
    pass
