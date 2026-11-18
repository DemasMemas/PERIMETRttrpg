"""Сохранять предложение о смене погоды до решения ГМа."""
from alembic import op
import sqlalchemy as sa

revision = 'd7ca9310e6f2'
down_revision = 'a683e2d415c9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('world_travel_events', sa.Column('weather_shift', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('world_travel_events', 'weather_shift')
