"""Убрать лишнюю точку после вопросительного знака в названиях книг."""
from alembic import op
import sqlalchemy as sa


revision = 'c8e67d40a291'
down_revision = 'f4c81a6e2d90'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.equipment_catalog import load_catalog

    table = sa.table('item_templates', sa.column('name', sa.String),
                     sa.column('category', sa.String))
    bind = op.get_bind()
    for item in load_catalog():
        if not item['attributes'].get('book_skill_path'):
            continue
        new_name = item['name']
        old_name = new_name.replace('? Раздел:', '?. Раздел:')
        bind.execute(table.update().where(table.c.category == 'misc')
                     .where(table.c.name == old_name).values(name=new_name))
    characters = sa.table('lobby_characters', sa.column('id', sa.Integer),
                          sa.column('data', sa.JSON))

    def clean_names(value):
        if isinstance(value, list):
            return any(clean_names(item) for item in value)
        if not isinstance(value, dict):
            return False
        changed = False
        name = value.get('name')
        attrs = value.get('attributes') if isinstance(value.get('attributes'), dict) else {}
        if isinstance(name, str) and '?. Раздел:' in name and (
                value.get('book_kind') == 'skill' or attrs.get('book_skill_path')):
            value['name'] = name.replace('?. Раздел:', '? Раздел:')
            changed = True
        for child in value.values():
            changed = clean_names(child) or changed
        return changed

    for character_id, data in bind.execute(sa.select(characters.c.id, characters.c.data)):
        if isinstance(data, dict) and clean_names(data):
            bind.execute(characters.update().where(characters.c.id == character_id)
                         .values(data=data))


def downgrade():
    pass
