"""Кустарная бомба из рецепта v20, с сохранением прежних шаблонов и ID."""
from alembic import op
import sqlalchemy as sa

revision='f08c617ab493'
down_revision='da072c5384ce'
branch_labels=None
depends_on=None


def upgrade():
    from app.services.improvised_explosives import templates
    table=sa.table('item_templates',sa.column('name',sa.String),sa.column('category',sa.String),
        sa.column('subcategory',sa.String),sa.column('item_class',sa.String),sa.column('description',sa.Text),
        sa.column('price',sa.Integer),sa.column('weight',sa.Float),sa.column('volume',sa.Float),
        sa.column('attributes',sa.JSON),sa.column('compatible_ids',sa.JSON))
    bind=op.get_bind()
    for row in templates():
        if not bind.execute(sa.select(table.c.name).where(table.c.category==row['category'],table.c.name==row['name'])).first():
            bind.execute(table.insert().values(**row))


def downgrade():
    # Игровые экземпляры не удаляются при откате схемы.
    pass
