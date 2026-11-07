"""Дозиметр: утверждённый шаблон без изменения существующих ID."""
from alembic import op
import sqlalchemy as sa

revision='f6c38b1de497'
down_revision='e5b27a0fb386'
branch_labels=None
depends_on=None


def upgrade():
    from app.services.electronics import templates
    table=sa.table('item_templates',sa.column('name',sa.String),sa.column('category',sa.String),
        sa.column('subcategory',sa.String),sa.column('item_class',sa.String),sa.column('description',sa.Text),
        sa.column('price',sa.Integer),sa.column('weight',sa.Float),sa.column('volume',sa.Float),
        sa.column('attributes',sa.JSON),sa.column('compatible_ids',sa.JSON))
    bind=op.get_bind()
    for row in templates():
        if not bind.execute(sa.select(table.c.name).where(table.c.category==row['category'],table.c.name==row['name'])).first():
            bind.execute(table.insert().values(**row))


def downgrade():
    pass
