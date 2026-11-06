"""Справочник группировок конкретной комнаты."""
from alembic import op
import sqlalchemy as sa

revision='a41f7c9d2e36'
down_revision='f9a10d7c4e52'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('world_faction_configs',
        sa.Column('lobby_id',sa.Integer(),sa.ForeignKey('lobbies.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('source',sa.String(20),nullable=False,server_default='custom'),
        sa.Column('groups',sa.JSON(),nullable=False),
        sa.Column('relations',sa.JSON(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))


def downgrade():
    op.drop_table('world_faction_configs')
