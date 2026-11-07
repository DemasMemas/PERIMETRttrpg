"""Отдельная электронная книга для каждого навыка."""
from alembic import op
import sqlalchemy as sa

revision = 'f4c81a6e2d90'
down_revision = 'e62b9c3d0f51'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.equipment_catalog import load_catalog

    table = sa.table('item_templates', sa.column('name', sa.String),
        sa.column('category', sa.String), sa.column('subcategory', sa.String),
        sa.column('item_class', sa.String), sa.column('description', sa.Text),
        sa.column('price', sa.Integer), sa.column('weight', sa.Float),
        sa.column('volume', sa.Float), sa.column('attributes', sa.JSON),
        sa.column('compatible_ids', sa.JSON))
    bind = op.get_bind()
    known = set(bind.execute(sa.select(table.c.name).where(table.c.category == 'misc')).scalars())
    for item in load_catalog():
        if (item['attributes'].get('book_skill_path') and item['name'] not in known):
            bind.execute(table.insert().values(**item))
            known.add(item['name'])


def downgrade():
    # Сохранённые книги могут ссылаться на новые шаблоны.
    pass
