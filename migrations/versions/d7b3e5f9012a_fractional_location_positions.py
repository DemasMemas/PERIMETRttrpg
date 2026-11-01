"""Дробные координаты и очки перемещения без сдвига старых позиций."""
from alembic import op
import sqlalchemy as sa

revision = 'd7b3e5f9012a'
down_revision = 'c6a2d4e8f019'
branch_labels = None
depends_on = None

FIELDS = ('pos_x','pos_y','movement_points_max','movement_points_current',
          'movement_distance_this_turn','correction_distance_this_turn')


def upgrade():
    with op.batch_alter_table('location_characters') as batch:
        for name in FIELDS:
            batch.alter_column(name,existing_type=sa.Integer(),type_=sa.Float(),existing_nullable=False)


def downgrade():
    table = sa.table('location_characters',*(sa.column(name,sa.Float()) for name in FIELDS))
    fractional = sa.or_(*(table.c[name] != sa.cast(table.c[name],sa.Integer()) for name in FIELDS))
    if op.get_bind().execute(sa.select(sa.literal(1)).select_from(table).where(fractional).limit(1)).first():
        raise RuntimeError('Откат остановлен: целочисленная схема потеряет дробные позиции или ОП.')
    with op.batch_alter_table('location_characters') as batch:
        for name in FIELDS:
            batch.alter_column(name,existing_type=sa.Float(),type_=sa.Integer(),existing_nullable=False)
