"""Состояние ловушек и недостающие шаблоны рецептов."""
from alembic import op
import sqlalchemy as sa

revision='e5b27a0fb386'
down_revision='d4a16f9ea275'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('location_traps',sa.Column('id',sa.Integer,primary_key=True),
        sa.Column('location_id',sa.Integer,sa.ForeignKey('locations.id',ondelete='CASCADE'),nullable=False),
        sa.Column('x',sa.Integer,nullable=False),sa.Column('y',sa.Integer,nullable=False),
        sa.Column('level',sa.Integer,nullable=False),sa.Column('creator_id',sa.Integer,sa.ForeignKey('lobby_characters.id',ondelete='SET NULL')),
        sa.Column('recipe_id',sa.String(40),nullable=False),sa.Column('status',sa.String(20),nullable=False),
        sa.Column('detection_dc',sa.Integer),sa.Column('known_users',sa.JSON,nullable=False),
        sa.Column('data',sa.JSON,nullable=False),sa.Column('revision',sa.Integer,nullable=False,server_default='1'))
    op.create_index('ix_location_traps_location_id','location_traps',['location_id'])
    from app.services.trap_catalog import templates
    table=sa.table('item_templates',sa.column('name',sa.String),sa.column('category',sa.String),
        sa.column('subcategory',sa.String),sa.column('item_class',sa.String),sa.column('description',sa.Text),
        sa.column('price',sa.Integer),sa.column('weight',sa.Float),sa.column('volume',sa.Float),
        sa.column('attributes',sa.JSON),sa.column('compatible_ids',sa.JSON))
    bind=op.get_bind();known=set(bind.execute(sa.select(table.c.category,table.c.name)).all())
    for row in templates():
        if (row['category'],row['name']) not in known:
            bind.execute(table.insert().values(**row));known.add((row['category'],row['name']))


def downgrade():
    # Снятие миграции не должно уничтожать размещённые ловушки и их экземпляры.
    pass
