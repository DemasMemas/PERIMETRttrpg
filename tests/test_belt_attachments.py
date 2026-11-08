"""Крепления инструментов: физические предметы, оплата и общие действия."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.belt_attachments import preserve, loaded
from app.services.equipment_modifications import make_record, install
from app.services.combat import CombatService
from app.services.exceptions import ValidationError
from test_consumable_use import pair


def setup_tools(pair, *, quantity=1, name='Ножницы'):
    template=ItemTemplate(name=name,category='tool',weight=2,volume=1,attributes={'uses':50})
    db.session.add(template);db.session.flush()
    item={'id':'belt-tool-test','templateId':template.id,'name':name,'category':'tool',
          'quantity':quantity,'weight':2,'volume':1,'uses':17,'maxUses':50}
    character=pair['actor_character'];data=deepcopy(character.data)
    data.setdefault('equipment',{})['belt']={'id':'belt-test','category':'belt','weight':1,
        'modifications':[make_record('armor-g61')]}
    data['inventory']={'pockets':[item],'backpack':[]}
    character.data=data;pair['actor_location'].action_points_current=30;db.session.commit()
    return item


def change(client,pair,auth_headers,operation='mount',*,source=None,revision=None,user=None):
    character=pair['actor_character'];data=character.data
    path=['inventory','pockets',0] if operation=='mount' else ['equipment','belt','installedModules',0]
    item=data['inventory']['pockets'][0] if operation=='mount' else data['equipment']['belt']['installedModules'][0]
    return client.post(f'/lobbies/characters/{character.id}/belt-attachment',headers=auth_headers(user or pair['actor_user']),json={
        'operation':operation,'belt_id':'belt-test','character_revision':character.revision if revision is None else revision,
        'source':source or {'path':path,'snapshot':deepcopy(item)}})


@pytest.mark.parametrize('quantity',[1,2])
def test_mount_and_unmount_preserve_real_tool_and_charges(client,pair,auth_headers,quantity):
    original=setup_tools(pair,quantity=quantity);actor=pair['actor_location']
    assert change(client,pair,auth_headers).status_code==200
    data=pair['actor_character'].data;tool=data['equipment']['belt']['installedModules'][0]
    assert tool['quantity']==1 and tool['uses']==17 and tool['maxUses']==50
    assert (tool['id']==original['id']) is (quantity==1)
    assert len(data['inventory']['pockets'])==quantity-1
    weight=CombatService._inventory_weight_details(data)['total_weight']
    assert weight==2*(quantity-1)
    assert actor.action_points_current==27
    tool_id=tool['id'];assert change(client,pair,auth_headers,'unmount').status_code==200
    returned=pair['actor_character'].data['inventory']['pockets'][-1]
    assert returned['id']==tool_id and returned['uses']==17 and 'slotType' not in returned
    assert actor.action_points_current==24


def test_failed_payment_and_stale_request_cannot_clone_or_spend_twice(client,pair,auth_headers):
    setup_tools(pair);actor=pair['actor_location'];actor.action_points_current=2;db.session.commit()
    assert change(client,pair,auth_headers).status_code==400
    assert len(actor.character.data['inventory']['pockets'])==1 and not loaded(actor.character.data['equipment']['belt'])
    actor.action_points_current=3;db.session.commit();revision=actor.character.revision
    source={'path':['inventory','pockets',0],'snapshot':deepcopy(actor.character.data['inventory']['pockets'][0])}
    assert change(client,pair,auth_headers,source=source).status_code==200
    # При повторе клиент посылает тот же снимок, а не ищет уже перемещённый предмет.
    response=client.post(f'/lobbies/characters/{actor.character.id}/belt-attachment',headers=auth_headers(pair['actor_user']),json={
        'operation':'mount','belt_id':'belt-test','character_revision':revision,'source':source})
    assert response.status_code==409 and actor.action_points_current==0
    assert len(loaded(actor.character.data['equipment']['belt']))==1


def test_four_places_and_canonical_tool_category(client,pair,auth_headers):
    setup_tools(pair,quantity=5)
    for _ in range(4):assert change(client,pair,auth_headers).status_code==200
    assert change(client,pair,auth_headers).status_code==400
    data=pair['actor_character'].data
    assert len(loaded(data['equipment']['belt']))==4 and data['inventory']['pockets'][0]['quantity']==1
    assert len({item['id'] for _,item in loaded(data['equipment']['belt'])})==4
    # Подмена только клиентской категории не делает воду инструментом.
    template=ItemTemplate(name='Вода',category='consumable');db.session.add(template);db.session.flush()
    assert change(client,pair,auth_headers,'unmount').status_code==200
    data=deepcopy(pair['actor_character'].data);data['inventory']['pockets'][0].update(templateId=template.id,name='Вода')
    pair['actor_character'].data=data;db.session.commit()
    assert change(client,pair,auth_headers).status_code==400


def test_ownership_turn_and_disabled_mount(client,pair,auth_headers):
    setup_tools(pair)
    assert change(client,pair,auth_headers,user=pair['target_user']).status_code==403
    assert change(client,pair,auth_headers).status_code==200
    data=deepcopy(pair['actor_character'].data);data['equipment']['belt']['protectionDisabled']=True
    pair['actor_character'].data=data;db.session.commit()
    assert CombatService._inventory_weight_details(data)['total_weight']==2
    assert change(client,pair,auth_headers,'unmount').status_code==200
    assert change(client,pair,auth_headers).status_code==400


def test_only_own_turn_but_no_action_cost_outside_combat(client,pair,auth_headers):
    from app.models import LocationCombatState
    setup_tools(pair);state=LocationCombatState.query.filter_by(location_id=pair['location'].id).one()
    state.current_location_character_id=pair['target_location'].id;db.session.commit()
    assert change(client,pair,auth_headers).status_code==403
    state.status='finished';db.session.commit();before=pair['actor_location'].action_points_current
    assert change(client,pair,auth_headers).status_code==200
    assert change(client,pair,auth_headers,'unmount').status_code==200
    assert pair['actor_location'].action_points_current==before


def test_sheet_preserves_tools_with_same_belt_and_rejects_copies(client,pair,auth_headers):
    setup_tools(pair);assert change(client,pair,auth_headers).status_code==200
    current=deepcopy(pair['actor_character'].data);tool=deepcopy(loaded(current['equipment']['belt'])[0][1])
    incoming=deepcopy(current);belt=incoming['equipment'].pop('belt');belt.pop('installedModules')
    incoming['inventory']['backpack'].append(belt);preserve(current,incoming)
    assert loaded(incoming['inventory']['backpack'][0])[0][1]==tool
    assert CombatService._inventory_weight_details(incoming)['total_weight']==3
    copied=deepcopy(incoming);copied['inventory']['pockets'].append(tool)
    with pytest.raises(ValidationError,match='отдельным действием'):preserve(current,copied)
    forged=deepcopy(current);forged['equipment']['belt']['installedModules'][0]['uses']=50
    preserve(current,forged);assert loaded(forged['equipment']['belt'])[0][1]['uses']==17
    with pytest.raises(ValidationError,match='Сначала снимите инструменты'):
        install(current['equipment']['belt'],make_record('armor-g62'),replacing='armor-g61')


def test_mounted_tools_remain_available_to_crafting_medicine_and_repair(client,pair,auth_headers):
    from app.services.crafting import entries
    from app.services.medical_procedure import _scissors_medicine_bonus
    from app.services.inventory_access import calculate_inventory_access
    from app.services.equipment_repair import repair_equipment
    from test_equipment_repair import weapon_tool
    setup_tools(pair);assert change(client,pair,auth_headers).status_code==200
    data=deepcopy(pair['actor_character'].data);path=['equipment','belt','installedModules',0]
    assert next(item for candidate,item in entries(data) if candidate==path)['uses']==17
    assert _scissors_medicine_bonus(data,pair['actor_character'].lobby_id,{'direct':{'tourniquet':True}})==2
    assert calculate_inventory_access(data,path)['base_action_points']==1
    data['equipment']['belt']['installedModules'][0].update(weapon_tool())
    data.setdefault('skills',{}).setdefault('other',{})['engineering']={'base':10,'bonus':0}
    data['weapons']=[{'category':'weapon','itemClass':'Оружие 2','durability':80,'maxDurability':100}]
    result=repair_equipment(data,path,['weapons',0])
    assert result['after']==85 and loaded(data['equipment']['belt'])[0][1]['uses']==14
