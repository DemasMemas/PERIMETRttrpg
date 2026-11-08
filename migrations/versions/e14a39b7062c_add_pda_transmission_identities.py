"""Исторические имена отправителей сообщений КПК."""
from alembic import op
import sqlalchemy as sa

revision='e14a39b7062c'
down_revision='f08c617ab493'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('pda_transmission_identities',
        sa.Column('transmission_id',sa.String(100),sa.ForeignKey('pda_transmissions.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('sender_name',sa.String(100),nullable=False),sa.Column('recipient_name',sa.String(100)))
    op.create_table('pda_contact_labels',sa.Column('account_id',sa.Integer,primary_key=True),
        sa.Column('target_account_id',sa.Integer,primary_key=True),sa.Column('observed_name',sa.String(100),nullable=False),
        sa.Column('signal_id',sa.String(64)),sa.ForeignKeyConstraint(['account_id','target_account_id'],
            ['pda_contacts.account_id','pda_contacts.target_account_id'],ondelete='CASCADE'))


def downgrade():
    op.drop_table('pda_contact_labels')
    op.drop_table('pda_transmission_identities')
