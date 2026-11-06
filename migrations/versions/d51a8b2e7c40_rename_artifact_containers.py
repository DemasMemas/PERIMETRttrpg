"""Сохранить четыре контейнера использования под утверждёнными названиями."""
from alembic import op
import sqlalchemy as sa

revision = 'd51a8b2e7c40'
down_revision = 'c84f2d6a1e09'
branch_labels = None
depends_on = None


def upgrade():
    table = sa.table('item_templates', sa.column('id', sa.Integer),
                     sa.column('name', sa.String), sa.column('category', sa.String),
                     sa.column('attributes', sa.JSON))
    names = {'Контейнер для артефактов 1 ур': 'Блок-А',
             'Контейнер для артефактов 2 ур': 'Барьер',
             'Контейнер для артефактов 3 ур': 'Щит',
             'Контейнер для артефактов 4 ур': 'Стена'}
    bind = op.get_bind()
    for row in bind.execute(sa.select(table).where(table.c.category == 'container',
                                                   table.c.name.in_(names))).mappings():
        attributes = row['attributes'] or {}
        if attributes.get('import_source') != 'equipment_workbook' or attributes.get('artifact_container_kind') != 'equipped':
            continue
        bind.execute(table.update().where(table.c.id == row['id']).values(name=names[row['name']]))


def downgrade():
    # Старые экземпляры и заданные ГМом названия не изменяются при откате.
    pass
