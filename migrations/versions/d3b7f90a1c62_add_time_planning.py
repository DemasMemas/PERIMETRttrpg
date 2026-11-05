"""Планы игрового времени по отдельным персонажам."""
from alembic import op
import sqlalchemy as sa

revision = 'd3b7f90a1c62'
down_revision = 'e91c7b2a4f60'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('time_plan_requests',
        sa.Column('id', sa.String(80), primary_key=True),
        sa.Column('lobby_id', sa.Integer(), sa.ForeignKey('lobbies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('gm_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('start_minute', sa.Integer(), nullable=False),
        sa.Column('window_minutes', sa.Integer(), nullable=False),
        sa.Column('owner_ids', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_time_plan_requests_lobby_id', 'time_plan_requests', ['lobby_id'])
    op.create_table('time_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('request_id', sa.String(80), sa.ForeignKey('time_plan_requests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('character_id', sa.Integer(), sa.ForeignKey('lobby_characters.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('activities', sa.JSON(), nullable=False),
        sa.Column('confirmed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('overflow_state', sa.String(12), nullable=False, server_default='none'),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('request_id', 'character_id', name='uq_time_plan_character'),
    )
    op.create_index('ix_time_plans_request_id', 'time_plans', ['request_id'])
    op.create_index('ix_time_plans_owner_id', 'time_plans', ['owner_id'])


def downgrade():
    op.drop_index('ix_time_plans_owner_id', table_name='time_plans')
    op.drop_index('ix_time_plans_request_id', table_name='time_plans')
    op.drop_table('time_plans')
    op.drop_index('ix_time_plan_requests_lobby_id', table_name='time_plan_requests')
    op.drop_table('time_plan_requests')
