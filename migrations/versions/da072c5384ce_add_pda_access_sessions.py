"""Сеансы физического и дистанционного взлома КПК."""
from alembic import op
import sqlalchemy as sa

revision='da072c5384ce'
down_revision='c9f61b4273bd'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('pda_access_sessions',sa.Column('id',sa.String(100),primary_key=True),
        sa.Column('lobby_id',sa.Integer,sa.ForeignKey('lobbies.id',ondelete='CASCADE'),nullable=False),
        sa.Column('source_character_id',sa.Integer,sa.ForeignKey('lobby_characters.id',ondelete='SET NULL')),
        sa.Column('source_device_id',sa.String(100),nullable=False),sa.Column('target_device_id',sa.String(100),nullable=False),
        sa.Column('mode',sa.String(16),nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('remaining_seconds',sa.Float,nullable=False),sa.Column('created_game_time',sa.Integer,nullable=False),
        sa.Column('created_at',sa.DateTime,nullable=False),sa.Column('notified',sa.Boolean,nullable=False),
        sa.Column('data',sa.JSON,nullable=False),sa.Column('receipts',sa.JSON,nullable=False),
        sa.Column('revision',sa.Integer,nullable=False,server_default='1'))
    op.create_index('ix_pda_access_sessions_lobby_id','pda_access_sessions',['lobby_id'])
    op.create_index('ix_pda_access_sessions_source_device_id','pda_access_sessions',['source_device_id'])


def downgrade():
    pass
