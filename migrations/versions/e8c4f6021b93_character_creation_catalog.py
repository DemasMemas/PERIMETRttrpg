"""Добавлены исходные предыстории и особые черты без изменения существующих листов."""
from alembic import op
import sqlalchemy as sa
import json
from pathlib import Path

revision='e8c4f6021b93'
down_revision='d7b3e5f9012a'
branch_labels=None
depends_on=None


def upgrade():
    # Каталог содержит исходные тексты; миграция только дополняет отсутствующие записи.
    source=json.loads((Path(__file__).parents[2]/'app/data/character_creation.json').read_text(encoding='utf-8'))
    if source['version']!='character-3-20261103':raise RuntimeError('Для миграции нужен исходный каталог character-3-20261103')
    table=sa.table('item_templates',sa.column('name',sa.String),sa.column('category',sa.String),
        sa.column('description',sa.Text),sa.column('attributes',sa.JSON),sa.column('price',sa.Integer),
        sa.column('weight',sa.Float),sa.column('volume',sa.Float),sa.column('compatible_ids',sa.JSON))
    connection=op.get_bind()
    known={str((row[0] or {}).get('ruleId')) for row in connection.execute(sa.select(table.c.attributes).where(table.c.category.in_(['background','special_trait'])))}
    rows=[]
    for category,key in (('background','backgrounds'),('special_trait','traits')):
        for entry in source[key]:
            if entry['id'] in known:continue
            attrs={'ruleId':entry['id'],'rulesVersion':source['version'],'source':entry['source'],'available':entry.get('available',True)}
            if category=='background':attrs.update(pluses=entry['pluses'],minuses=entry['minuses'],entries=entry['entries'],skillBonuses=[{'skill':e['skill'],'bonus':e['amount']} for e in entry['entries'] if e['type']=='skill'])
            else:attrs.update(effect=entry['effect'],cost=entry['cost'],maxCount=entry['maxCount'])
            rows.append({'name':entry['name'],'category':category,'description':entry.get('effect',entry.get('pluses','')),
                'attributes':attrs,'price':0,'weight':0,'volume':0,'compatible_ids':[]})
    if rows:op.bulk_insert(table,rows)


def downgrade():
    # Листы могут ссылаться на шаблоны. Откат схемы не удаляет игровые записи.
    pass
