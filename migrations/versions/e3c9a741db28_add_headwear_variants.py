"""Добавить четыре варианта головного убора в существующий каталог."""
from alembic import op
import sqlalchemy as sa

revision = 'e3c9a741db28'
down_revision = 'a8e3b9c6f241'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.equipment_catalog import load_catalog
    from app.services.equipment_importer import HEADWEAR_VARIANTS

    table = sa.table('item_templates', sa.column('name', sa.String), sa.column('category', sa.String),
        sa.column('subcategory', sa.String), sa.column('item_class', sa.String), sa.column('description', sa.Text),
        sa.column('price', sa.Integer), sa.column('weight', sa.Float), sa.column('volume', sa.Float),
        sa.column('attributes', sa.JSON), sa.column('compatible_ids', sa.JSON))
    connection = op.get_bind()
    existing = set(connection.scalars(sa.select(table.c.name).where(table.c.category == 'helmet')))
    rows = [item for item in load_catalog() if item['category'] == 'helmet'
            and item['name'] in HEADWEAR_VARIANTS and item['name'] not in existing]
    if rows:
        connection.execute(table.insert(), rows)


def downgrade():
    # Уже выданным экземплярам нужны их шаблоны и ID.
    pass
