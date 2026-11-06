"""Объём каталожного КПК приведён к половине ячейки."""
from alembic import op
import sqlalchemy as sa

revision = 'c84f2d6a1e09'
down_revision = 'b72e6a1c4f90'
branch_labels = None
depends_on = None


def upgrade():
    table = sa.table('item_templates', sa.column('id', sa.Integer), sa.column('name', sa.String),
                     sa.column('category', sa.String), sa.column('price', sa.Integer),
                     sa.column('weight', sa.Float), sa.column('volume', sa.Float),
                     sa.column('attributes', sa.JSON))
    bind = op.get_bind()
    for row in bind.execute(sa.select(table).where(table.c.name == 'КПК',
                                                   table.c.category == 'misc')).mappings():
        if (row['attributes'] or {}).get('import_source') != 'equipment_workbook':
            continue
        if row['price'] == 1500 and row['weight'] == .25 and row['volume'] == 1:
            bind.execute(table.update().where(table.c.id == row['id']).values(volume=.5))


def downgrade():
    # Существующие экземпляры и ручные изменения шаблона не откатываются.
    pass
