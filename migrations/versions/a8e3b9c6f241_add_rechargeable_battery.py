"""Добавить аккумуляторную батарею в существующий каталог."""
from alembic import op
import sqlalchemy as sa

revision = 'a8e3b9c6f241'
down_revision = 'c61f4be88320'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.power_cells import templates

    table = sa.table('item_templates', sa.column('id', sa.Integer), sa.column('name', sa.String),
        sa.column('category', sa.String), sa.column('subcategory', sa.String), sa.column('item_class', sa.String),
        sa.column('description', sa.Text), sa.column('price', sa.Integer), sa.column('weight', sa.Float),
        sa.column('volume', sa.Float), sa.column('attributes', sa.JSON), sa.column('compatible_ids', sa.JSON))
    item, = templates()
    connection = op.get_bind()
    existing = connection.execute(sa.select(table.c.id).where(
        table.c.category == item['category'], table.c.name == item['name'])).first()
    if existing is None:
        connection.execute(table.insert().values(**item))


def downgrade():
    # Сохраняем шаблон для уже выданных экземпляров.
    pass
