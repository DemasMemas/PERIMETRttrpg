"""Перенести балаклавы в косметический слот с сохранением экземпляров и ID."""
from alembic import op
import sqlalchemy as sa

revision = 'f25c80a61d39'
down_revision = 'e3c9a741db28'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.cosmetic_masks import mask_name, normalize_character_masks
    from app.services.item_template_lookup import LOCAL_TEMPLATE_ID_OFFSET

    connection = op.get_bind()
    template_names = {}
    for table_name, offset in (('item_templates', 0), ('lobby_item_templates', LOCAL_TEMPLATE_ID_OFFSET)):
        table = sa.table(table_name, sa.column('id', sa.Integer), sa.column('name', sa.String),
                         sa.column('category', sa.String))
        rows = connection.execute(sa.select(table.c.id, table.c.name, table.c.category)
                                  .where(table.c.category.in_(['helmet', 'mask'])))
        for identity, name, category in rows:
            if (canonical := mask_name(name)) is None:
                continue
            template_names[str(identity + offset)] = canonical
            if category == 'helmet':
                connection.execute(table.update().where(table.c.id == identity).values(category='mask'))

    characters = sa.table('lobby_characters', sa.column('id', sa.Integer), sa.column('data', sa.JSON),
                          sa.column('revision', sa.Integer))
    for identity, data in connection.execute(sa.select(characters.c.id, characters.c.data)):
        if isinstance(data, dict) and normalize_character_masks(data, template_names):
            connection.execute(characters.update().where(characters.c.id == identity)
                               .values(data=data, revision=characters.c.revision + 1))


def downgrade():
    # В сохранениях маска может быть надета одновременно со шлемом.
    pass
