"""Исправлены неизменённые шаблоны шлемов со сдвигом столбцов новой книги."""
from alembic import op
import sqlalchemy as sa

revision='b17f4a9c2d60'
down_revision='a0e6b8243d15'
branch_labels=None
depends_on=None


def upgrade():
    from app.services.equipment_catalog import load_catalog,shifted_helmet_updates
    from app.services.equipment_importer import _normalize_equipment_name
    table=sa.table('item_templates',sa.column('id',sa.Integer),sa.column('name',sa.String),sa.column('category',sa.String),
        sa.column('item_class',sa.String),sa.column('price',sa.Integer),sa.column('volume',sa.Float),sa.column('attributes',sa.JSON))
    canonical={(r['category'],_normalize_equipment_name(r['name'])):r for r in load_catalog() if r['category'] in {'helmet','gas_mask'}}
    bind=op.get_bind()
    for row in bind.execute(sa.select(table).where(table.c.category.in_(['helmet','gas_mask']))).mappings():
        target=canonical.get((row['category'],_normalize_equipment_name(row['name'])))
        updates=shifted_helmet_updates(dict(row),target) if target else None
        if updates:bind.execute(table.update().where(table.c.id==row['id']).values(**updates))


def downgrade():
    # Не возвращаем ошибочные цены/штрафы и не затрагиваем сохранённые экземпляры.
    pass
