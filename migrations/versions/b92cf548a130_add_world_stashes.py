"""Тайники в клетках глобальной карты."""
from alembic import op
import sqlalchemy as sa

revision = 'b92cf548a130'
down_revision = 'e14a39b7062c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('world_stashes',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('lobby_id', sa.Integer, sa.ForeignKey('lobbies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tile_x', sa.Integer, nullable=False),
        sa.Column('tile_y', sa.Integer, nullable=False),
        sa.Column('properties', sa.JSON, nullable=False),
        sa.Column('revision', sa.Integer, nullable=False, server_default='1'))
    op.create_index('ix_world_stashes_tile', 'world_stashes', ['lobby_id','tile_x','tile_y'])


def downgrade():
    op.drop_table('world_stashes')
