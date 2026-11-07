"""Заметки внутри учётной записи КПК игрового мира."""
from alembic import op
import sqlalchemy as sa


revision = 'a1e83c6d7b42'
down_revision = 'd9b18f7e420a'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pda_accounts', sa.Column('notes', sa.Text(), nullable=False, server_default=''))


def downgrade():
    op.drop_column('pda_accounts', 'notes')
