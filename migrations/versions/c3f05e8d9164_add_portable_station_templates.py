"""Добавить семь созданных устройств по ответам автора без изменения существующих ID."""
from alembic import op
import sqlalchemy as sa

revision='c3f05e8d9164'
down_revision='b2e94d7c8053'
branch_labels=None
depends_on=None


def upgrade():
    from app.services.portable_stations import templates
    table=sa.table('item_templates',sa.column('id',sa.Integer),sa.column('name',sa.String),
        sa.column('category',sa.String),sa.column('subcategory',sa.String),sa.column('item_class',sa.String),
        sa.column('description',sa.Text),sa.column('price',sa.Integer),sa.column('weight',sa.Float),
        sa.column('volume',sa.Float),sa.column('attributes',sa.JSON),sa.column('compatible_ids',sa.JSON))
    bind=op.get_bind();known=set(bind.execute(sa.select(table.c.category,table.c.name)).all())
    for item in templates():
        key=item['category'],item['name']
        if key not in known:bind.execute(table.insert().values(**item));known.add(key)


def downgrade():
    # Не удаляем шаблоны, на которые ссылаются созданные экземпляры.
    pass
