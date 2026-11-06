"""Структура источников имён и уровней генерации НПС."""
from alembic import op
import sqlalchemy as sa

revision='f9a10d7c4e52'
down_revision='d3b7f90a1c62'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('npc_generation_configs',
        sa.Column('lobby_id',sa.Integer(),sa.ForeignKey('lobbies.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('names',sa.JSON(),nullable=False),
        sa.Column('danger_levels',sa.JSON(),nullable=False),
        sa.Column('wealth_levels',sa.JSON(),nullable=False),
        sa.Column('combinations',sa.JSON(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))
    op.create_table('npc_creation_receipts',
        sa.Column('operation_id',sa.String(80),primary_key=True),
        sa.Column('lobby_id',sa.Integer(),sa.ForeignKey('lobbies.id',ondelete='CASCADE'),nullable=False),
        sa.Column('character_id',sa.Integer(),sa.ForeignKey('lobby_characters.id',ondelete='CASCADE'),nullable=False),
        sa.Column('payload_hash',sa.String(64),nullable=False))
    op.create_index('ix_npc_creation_receipts_lobby_id','npc_creation_receipts',['lobby_id'])


def downgrade():
    op.drop_index('ix_npc_creation_receipts_lobby_id',table_name='npc_creation_receipts')
    op.drop_table('npc_creation_receipts')
    op.drop_table('npc_generation_configs')
