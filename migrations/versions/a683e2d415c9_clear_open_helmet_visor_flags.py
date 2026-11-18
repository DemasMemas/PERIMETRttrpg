"""Снять устаревшую отметку встроенного забрала у открытых шлемов."""
from alembic import op
import sqlalchemy as sa

revision = 'a683e2d415c9'
down_revision = 'f25c80a61d39'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.helmet_compatibility import has_open_helmet_shell
    from app.services.item_template_lookup import LOCAL_TEMPLATE_ID_OFFSET
    from app.services.portable_lights import walk

    connection = op.get_bind()
    open_templates = {}
    for name, offset in (('item_templates', 0), ('lobby_item_templates', LOCAL_TEMPLATE_ID_OFFSET)):
        table = sa.table(name, sa.column('id', sa.Integer), sa.column('name', sa.String),
                         sa.column('category', sa.String), sa.column('attributes', sa.JSON))
        rows = connection.execute(sa.select(table).where(table.c.category == 'helmet')).mappings().all()
        for row in rows:
            open_shell = has_open_helmet_shell(dict(row), row['name'])
            open_templates[str(row['id'] + offset)] = open_shell
            if not open_shell:
                continue
            attrs = row['attributes'] or {}
            if isinstance(attrs, dict) and attrs.get('integrated_visor'):
                connection.execute(table.update().where(table.c.id == row['id'])
                                   .values(attributes={**attrs, 'integrated_visor': False}))

    characters = sa.table('lobby_characters', sa.column('id', sa.Integer), sa.column('data', sa.JSON),
                          sa.column('revision', sa.Integer))
    for identity, data in connection.execute(sa.select(characters.c.id, characters.c.data)):
        if not isinstance(data, dict):
            continue
        changed = False
        containers = {key: data[key] for key in ('inventory', 'equipment') if isinstance(data.get(key), dict)}
        for path, item in walk(containers, all_items=True):
            attrs = item.get('attributes')
            if not isinstance(attrs, dict) or not attrs.get('integrated_visor') or item.get('integratedWithArmor'):
                continue
            template_id = str(item.get('templateId'))
            is_helmet = item.get('category') == 'helmet' or path == ['equipment', 'helmet']
            open_shell = open_templates.get(template_id)
            if open_shell is True or (open_shell is None and is_helmet and has_open_helmet_shell(item)):
                attrs['integrated_visor'] = False
                changed = True
        if changed:
            connection.execute(characters.update().where(characters.c.id == identity)
                               .values(data=data, revision=characters.c.revision + 1))


def downgrade():
    # Исправленный флаг не возвращаем в сохранения.
    pass
