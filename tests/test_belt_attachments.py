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


def change(client,pair,auth_headers,operation='mount',*,source=None,revision=None,user=None,kind='tool'):
    character=pair['actor_character'];data=character.data
    path=['inventory','pockets',0] if operation=='mount' else ['equipment','belt','installedModules',0]
    item=data['inventory']['pockets'][0] if operation=='mount' else data['equipment']['belt']['installedModules'][0]
    return client.post(f'/lobbies/characters/{character.id}/belt-attachment',headers=auth_headers(user or pair['actor_user']),json={
        'operation':operation,'kind':kind,'belt_id':'belt-test','character_revision':character.revision if revision is None else revision,
        'source':source or {'path':path,'snapshot':deepcopy(item)}})


def test_radio_mount_keeps_one_physical_radio_and_rejects_other_items(client,pair,auth_headers):
    from app.services.equipment_modifications import definition, supported

    assert supported(definition('armor-g69'))
    setup_tools(pair)
    character = pair['actor_character']
    template = ItemTemplate(name='Рация', category='misc', weight=1, volume=2)
    db.session.add(template)
    db.session.flush()
    radio = {'id':'radio-1','templateId':template.id,'name':template.name,'category':template.category,
             'weight':1,'volume':2,'quantity':1}
    data = deepcopy(character.data)
    data['equipment']['belt']['modifications'] = [make_record('armor-g69')]
    character.data = data
    db.session.commit()
    assert change(client,pair,auth_headers,kind='radio').status_code == 400
    data = deepcopy(character.data)
    data['inventory']['pockets'] = [radio,{**radio,'id':'radio-2'}]
    character.data = data
    db.session.commit()
    assert change(client,pair,auth_headers,kind='radio').status_code == 200
    equipped = character.data['equipment']['belt']
    assert loaded(equipped,'radio')[0][1]['id'] == 'radio-1'
    assert change(client,pair,auth_headers,kind='radio').status_code == 400
    with pytest.raises(ValidationError,match='Сначала снимите рацию'):
        install(equipped,make_record('armor-g62'),replacing='armor-g69')
    assert change(client,pair,auth_headers,'unmount',kind='radio').status_code == 200
    assert [entry['id'] for entry in character.data['inventory']['pockets']] == ['radio-2','radio-1']
    assert character.data['inventory']['pockets'][-1] == radio


def test_mounted_detector_and_regular_slot_use_the_higher_active_bonus(client,pair,auth_headers):
    from app.services.equipment_modifications import definition, supported
    from app.services.world_travel_events import anomaly_check_details

    assert supported(definition('armor-g68'))
    setup_tools(pair)
    character = pair['actor_character']
    template = ItemTemplate(name='Детектор для крепления', category='detector', subcategory='Аномалии',
                            attributes={'type':'anomaly','bonus':7})
    db.session.add(template)
    db.session.flush()
    detector = {'id':'detector-belt','templateId':template.id,'name':template.name,'category':'detector',
                'subcategory':'Аномалии','attributes':template.attributes,'quantity':1}
    data = deepcopy(character.data)
    data['equipment']['belt']['modifications'] = [make_record('armor-g68')]
    data['equipment']['detector'] = {'id':'detector-normal','category':'detector','subcategory':'Аномалии','bonus':3}
    data['inventory']['pockets'] = [detector]
    character.data = data
    db.session.commit()
    difficulty = lambda: anomaly_check_details('anomaly',[character])
    assert 'СЛ 17 ' in difficulty()
    assert change(client,pair,auth_headers,kind='detector').status_code == 200
    assert 'СЛ 13 ' in difficulty()
    data = deepcopy(character.data)
    data['equipment']['detector']['bonus'] = 9
    character.data = data
    assert 'СЛ 11 ' in difficulty()
    data['equipment']['detector']['bonus'] = 3
    data['equipment']['belt']['protectionDisabled'] = True
    assert 'СЛ 17 ' in difficulty()
    data['equipment']['belt'].pop('protectionDisabled')
    data['inventory']['backpack'].append(data['equipment'].pop('belt'))
    assert 'СЛ 17 ' in difficulty()


@pytest.mark.parametrize('kind,rule_id,name,category,attributes',[
    ('grenade','armor-g60','РГД-5','grenade',{}),
    ('dressing','armor-g64','Бинт','consumable',{}),
    ('syringe','armor-g65','Миколий','consumable',{'consumable':{'direct':{'application_form':'injectable'}}}),
])
def test_quick_belt_retrieval_uses_free_action_then_one_ap(client,pair,auth_headers,kind,rule_id,name,category,attributes):
    setup_tools(pair)
    character, actor = pair['actor_character'], pair['actor_location']
    template = ItemTemplate(name=name,category=category,attributes=attributes)
    db.session.add(template)
    db.session.flush()
    data = deepcopy(character.data)
    data['equipment']['belt']['modifications'] = [make_record(rule_id)]
    data['inventory']['pockets'] = [{'id':'quick-belt-item','name':name,'category':category,'templateId':template.id,
                                    'quantity':1,'attributes':attributes}]
    character.data = data
    actor.free_actions_current = 1
    db.session.commit()
    assert change(client,pair,auth_headers,kind=kind).status_code == 200
    before = actor.action_points_current
    result = change(client,pair,auth_headers,'retrieve',kind=kind)
    assert result.status_code == 200, result.json
    assert actor.action_points_current == before and actor.free_actions_current == 0
    retrieved = character.data['inventory']['held'][0]
    assert retrieved['id'] == 'quick-belt-item' and 'slotType' not in retrieved
    data = deepcopy(character.data)
    data['inventory']['pockets'] = [data['inventory']['held'].pop()]
    character.data = data
    db.session.commit()
    assert change(client,pair,auth_headers,kind=kind).status_code == 200
    before = actor.action_points_current
    assert change(client,pair,auth_headers,'retrieve',kind=kind).status_code == 200
    assert actor.action_points_current == before-1 and actor.free_actions_current == 0


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
    renamed=deepcopy(current);renamed['equipment']['belt']['installedModules'][0]['slotType']='fake-slot'
    preserve(current,renamed);assert renamed['equipment']['belt']['installedModules']==[tool]
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
