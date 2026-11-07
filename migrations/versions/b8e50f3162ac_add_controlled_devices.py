"""Физические контроллеры зарядов и сохранённые результаты подрыва."""
from alembic import op
import sqlalchemy as sa

revision='b8e50f3162ac'
down_revision='a7d49e2f510b'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('detonation_events',sa.Column('id',sa.String(100),primary_key=True),
        sa.Column('lobby_id',sa.Integer,sa.ForeignKey('lobbies.id',ondelete='CASCADE'),nullable=False),
        sa.Column('location_id',sa.Integer,sa.ForeignKey('locations.id',ondelete='SET NULL')),
        sa.Column('profile',sa.JSON,nullable=False),sa.Column('results',sa.JSON,nullable=False),
        sa.Column('affected_character_ids',sa.JSON,nullable=False),
        sa.Column('carrier_id',sa.Integer,sa.ForeignKey('lobby_characters.id',ondelete='SET NULL')),
        sa.Column('source_character_id',sa.Integer,sa.ForeignKey('lobby_characters.id',ondelete='SET NULL')),
        sa.Column('game_time',sa.Integer,nullable=False),sa.Column('closed',sa.Boolean,nullable=False),
        sa.Column('revision',sa.Integer,nullable=False,server_default='1'))
    op.create_index('ix_detonation_events_lobby_id','detonation_events',['lobby_id'])
    from app.services.electronics import controller_templates
    table=sa.table('item_templates',sa.column('name',sa.String),sa.column('category',sa.String),
        sa.column('subcategory',sa.String),sa.column('item_class',sa.String),sa.column('description',sa.Text),
        sa.column('price',sa.Integer),sa.column('weight',sa.Float),sa.column('volume',sa.Float),
        sa.column('attributes',sa.JSON),sa.column('compatible_ids',sa.JSON))
    bind=op.get_bind()
    for row in controller_templates():
        if not bind.execute(sa.select(table.c.name).where(table.c.category==row['category'],table.c.name==row['name'])).first():
            bind.execute(table.insert().values(**row))


def downgrade():
    pass
