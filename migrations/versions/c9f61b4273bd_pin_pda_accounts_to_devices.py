"""Сохранить прежний вход на физических КПК, не меняя учётные записи мира."""
from copy import deepcopy
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision='c9f61b4273bd'
down_revision='b8e50f3162ac'
branch_labels=None
depends_on=None


def upgrade():
    from app.services.portable_lights import walk,definition
    bind=op.get_bind()
    people=sa.table('lobby_characters',sa.column('id',sa.Integer),sa.column('lobby_id',sa.Integer),sa.column('data',sa.JSON))
    logins=sa.table('pda_account_logins',sa.column('character_id',sa.Integer),sa.column('account_id',sa.Integer))
    accounts=sa.table('pda_accounts',sa.column('id',sa.Integer),sa.column('lobby_id',sa.Integer))
    worlds=dict(bind.execute(sa.select(accounts.c.id,accounts.c.lobby_id)).all())
    legacy=dict(bind.execute(sa.select(logins.c.character_id,logins.c.account_id)).all())
    for row in bind.execute(sa.select(people)).mappings().all():
        data=deepcopy(row['data'] or {});found=False;changed=False
        account_id=legacy.get(row['id'])
        if worlds.get(account_id)!=row['lobby_id']:account_id=None
        for _,item in walk(data):
            if definition(item)!='pda':continue
            found=True
            if not item.get('id'):item['id']=str(uuid4());changed=True
            if not isinstance(item.get('pdaNetwork'),dict):item['pdaNetwork']={};changed=True
            network=item['pdaNetwork']
            if 'account_id' not in network:network['account_id']=account_id;changed=True
        if changed:bind.execute(people.update().where(people.c.id==row['id']).values(data=data))
        if found:bind.execute(logins.delete().where(logins.c.character_id==row['id']))
    objects=sa.table('location_objects',sa.column('id',sa.Integer),sa.column('properties',sa.JSON))
    for row in bind.execute(sa.select(objects)).mappings().all():
        props=deepcopy(row['properties'] or {});changed=False
        for _,item in walk({'inventory':{'backpack':props.get('contents') or []}}):
            if definition(item)!='pda':continue
            if not item.get('id'):item['id']=str(uuid4());changed=True
            if not isinstance(item.get('pdaNetwork'),dict):item['pdaNetwork']={};changed=True
            if 'account_id' not in item['pdaNetwork']:item['pdaNetwork']['account_id']=None;changed=True
        if changed:bind.execute(objects.update().where(objects.c.id==row['id']).values(properties=props))


def downgrade():
    pass
