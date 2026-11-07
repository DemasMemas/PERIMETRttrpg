"""Контакты и доставленные сообщения сети КПК внутри мира."""
from alembic import op
import sqlalchemy as sa

revision='a7d49e2f510b'
down_revision='f6c38b1de497'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('pda_contacts',sa.Column('account_id',sa.Integer,sa.ForeignKey('pda_accounts.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('target_account_id',sa.Integer,sa.ForeignKey('pda_accounts.id',ondelete='CASCADE'),primary_key=True),sa.Column('friendly',sa.Boolean,nullable=False))
    op.create_table('pda_transmissions',sa.Column('id',sa.String(100),primary_key=True),
        sa.Column('lobby_id',sa.Integer,sa.ForeignKey('lobbies.id',ondelete='CASCADE'),nullable=False),
        sa.Column('sender_account_id',sa.Integer,sa.ForeignKey('pda_accounts.id',ondelete='CASCADE'),nullable=False),
        sa.Column('source_signal_id',sa.String(64),nullable=False),sa.Column('recipient_account_ids',sa.JSON,nullable=False),
        sa.Column('text',sa.Text,nullable=False),sa.Column('target_login',sa.String(32)),sa.Column('game_time',sa.Integer,nullable=False),
        sa.Column('signature',sa.String(64),nullable=False),sa.Column('created_at',sa.DateTime,nullable=False,server_default=sa.func.current_timestamp()))
    op.create_index('ix_pda_transmissions_lobby_id','pda_transmissions',['lobby_id'])
    op.create_table('pda_deliveries',sa.Column('account_id',sa.Integer,sa.ForeignKey('pda_accounts.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('transmission_id',sa.String(100),sa.ForeignKey('pda_transmissions.id',ondelete='CASCADE'),primary_key=True))


def downgrade():
    pass
