"""Добавить шаблоны создаваемых укрытий без изменения прежних экземпляров."""
from alembic import op
import sqlalchemy as sa

revision='d4a16f9ea275'
down_revision='c3f05e8d9164'
branch_labels=None
depends_on=None


def upgrade():
    from app.services.crafted_covers import templates
    table=sa.table('item_templates',sa.column('id',sa.Integer),sa.column('name',sa.String),
        sa.column('category',sa.String),sa.column('subcategory',sa.String),sa.column('item_class',sa.String),
        sa.column('description',sa.Text),sa.column('price',sa.Integer),sa.column('weight',sa.Float),
        sa.column('volume',sa.Float),sa.column('attributes',sa.JSON),sa.column('compatible_ids',sa.JSON))
    bind=op.get_bind();known=set(bind.execute(sa.select(table.c.category,table.c.name)).all())
    for row in templates():
        if (row['category'],row['name']) not in known:
            bind.execute(table.insert().values(**row));known.add((row['category'],row['name']))


def downgrade():
    # Существующие экземпляры могут ссылаться на эти ID.
    pass
