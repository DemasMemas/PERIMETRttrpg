"""Добавлены три вида воды по утверждённым эффектам питья."""
from alembic import op
import sqlalchemy as sa

revision = 'c28f5b0e1a71'
down_revision = 'b17f4a9c2d60'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.equipment_catalog import WATER_VARIANTS, load_catalog
    from app.services.equipment_importer import _normalize_equipment_name

    table = sa.table('item_templates', sa.column('id', sa.Integer), sa.column('name', sa.String),
        sa.column('category', sa.String), sa.column('subcategory', sa.String), sa.column('item_class', sa.String),
        sa.column('description', sa.Text), sa.column('price', sa.Integer), sa.column('weight', sa.Float),
        sa.column('volume', sa.Float), sa.column('attributes', sa.JSON), sa.column('compatible_ids', sa.JSON))
    bind = op.get_bind()
    known = {_normalize_equipment_name(name) for name, in bind.execute(
        sa.select(table.c.name).where(table.c.category == 'consumable'))}
    names = {name for name, _, _ in WATER_VARIANTS}
    for item in load_catalog():
        if item['name'] in names and _normalize_equipment_name(item['name']) not in known:
            bind.execute(table.insert().values(**item))
            known.add(_normalize_equipment_name(item['name']))


def downgrade():
    # Созданные экземпляры могут ссылаться на новые шаблоны.
    pass
