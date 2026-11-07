"""Учётные записи КПК внутри игрового мира."""
from alembic import op
import sqlalchemy as sa


revision = 'd9b18f7e420a'
down_revision = 'c8e67d40a291'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pda_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lobby_id', sa.Integer(), sa.ForeignKey('lobbies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('login', sa.String(32), nullable=False),
        sa.Column('login_key', sa.String(32), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('lobby_id', 'login_key', name='uq_pda_account_world_login'))
    op.create_table('pda_account_logins',
        sa.Column('character_id', sa.Integer(), sa.ForeignKey('lobby_characters.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('pda_accounts.id', ondelete='CASCADE'), nullable=False))


def downgrade():
    op.drop_table('pda_account_logins')
    op.drop_table('pda_accounts')
