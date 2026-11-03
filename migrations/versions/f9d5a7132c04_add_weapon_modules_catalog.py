"""Добавлены все утверждённые навесные модули оружия."""
from alembic import op
import sqlalchemy as sa

revision='f9d5a7132c04'
down_revision='e8c4f6021b93'
branch_labels=None
depends_on=None


def upgrade():
    from app.services.weapon_modules import builtin_templates
    table=sa.table('item_templates',sa.column('id',sa.Integer),sa.column('name',sa.String),
        sa.column('category',sa.String),sa.column('subcategory',sa.String),sa.column('item_class',sa.String),
        sa.column('description',sa.Text),sa.column('price',sa.Integer),sa.column('weight',sa.Float),
        sa.column('volume',sa.Float),sa.column('attributes',sa.JSON),sa.column('compatible_ids',sa.JSON))
    bind=op.get_bind()
    existing=bind.execute(sa.select(table).where(table.c.category=='weapon_module')).mappings().all()
    known={str(row['name']).casefold().replace('ё','е'):row for row in existing}
    for template in builtin_templates():
        previous=known.get(template['name'].casefold().replace('ё','е'))
        if previous:
            # Существующие настройки ГМа сохраняются, дополняются только ключи правил.
            attributes={**template['attributes'],**(previous['attributes'] or {})}
            bind.execute(table.update().where(table.c.id==previous['id']).values(attributes=attributes,weight=.5,volume=1))
        else:bind.execute(table.insert().values(**{k:v for k,v in template.items() if k!='id'}))


def downgrade():
    # Сохранённые экземпляры продолжают ссылаться на эти шаблоны.
    pass
