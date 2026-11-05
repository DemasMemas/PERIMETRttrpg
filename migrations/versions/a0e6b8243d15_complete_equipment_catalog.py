"""Добавлен полный каталог снаряжения без перезаписи существующих предметов."""
from alembic import op
import sqlalchemy as sa

revision = 'a0e6b8243d15'
down_revision = 'f9d5a7132c04'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.equipment_catalog import load_catalog
    from app.services.equipment_importer import _normalize_equipment_name, _weapon_aliases, _equipment_alias
    table = sa.table('item_templates', sa.column('id', sa.Integer), sa.column('name', sa.String),
        sa.column('category', sa.String), sa.column('subcategory', sa.String), sa.column('item_class', sa.String),
        sa.column('description', sa.Text), sa.column('price', sa.Integer), sa.column('weight', sa.Float),
        sa.column('volume', sa.Float), sa.column('attributes', sa.JSON), sa.column('compatible_ids', sa.JSON))
    bind = op.get_bind()
    known = {(row['category'], _normalize_equipment_name(row['name'])): row
             for row in bind.execute(sa.select(table)).mappings()}
    for data in sorted(load_catalog(), key=lambda item: item['category'] == 'magazine'):
        key = data['category'], _normalize_equipment_name(data['name'])
        if key in known:
            previous = known[key]
            attributes = previous['attributes'] or {}
            source = data['attributes'].get('source_description') or ''
            from app.services.equipment_importer import _normalize_text
            official = attributes.get('import_source') == 'equipment_workbook' or attributes.get('catalog_name') == data['name']
            if official and _normalize_text(previous['description']) == _normalize_text(source):
                bind.execute(table.update().where(table.c.id == previous['id']).values(
                    description=data['description'], attributes={**attributes, 'source_description': source}))
            continue
        if data['category'] == 'magazine':
            aliases = {}
            for weapon in bind.execute(sa.select(table.c.id, table.c.name).where(table.c.category == 'weapon')):
                for alias in _weapon_aliases(weapon.name):
                    aliases.setdefault(alias, []).append(weapon.id)
            ids = sorted({weapon_id for name in data['attributes'].get('compatible_weapon_names', [])
                          for weapon_id in aliases.get(_equipment_alias(name), [])})
            data['compatible_ids'] = ids
            data['attributes']['compatible_weapons'] = ids
        bind.execute(table.insert().values(**data))
        known[key] = data


def downgrade():
    # Игровые экземпляры могут ссылаться на новые шаблоны. Они не удаляются.
    pass
