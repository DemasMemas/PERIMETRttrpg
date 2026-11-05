"""Уточнено питание каталожного КПК по утверждённому правилу."""
from alembic import op
import sqlalchemy as sa

revision = 'e91c7b2a4f60'
down_revision = 'd39b6c1f2a82'
branch_labels = None
depends_on = None


def upgrade():
    table = sa.table('item_templates', sa.column('id', sa.Integer),
        sa.column('name', sa.String), sa.column('description', sa.Text),
        sa.column('attributes', sa.JSON))
    old = 'КПК для связи и хранения электронных книг. Батарейка работает одну игровую неделю.'
    new = 'КПК для связи и хранения электронных книг. Включённый КПК расходует 0,5% заряда батарейки за игровой час.'
    bind = op.get_bind()
    for row in bind.execute(sa.select(table).where(table.c.name == 'КПК')).mappings():
        attrs = row['attributes'] or {}
        if row['description'] != old or attrs.get('import_source') != 'equipment_workbook':
            continue
        updated = {**attrs, 'battery_duration_seconds': 720000,
                   'battery_drain_percent_per_hour': .5, 'source_description': new}
        bind.execute(table.update().where(table.c.id == row['id']).values(
            description=new, attributes=updated))


def downgrade():
    # Игровые экземпляры и ручные правки шаблона не откатываются.
    pass
