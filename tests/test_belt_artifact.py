"""Артефакт на поясе работает через общий профиль, включая отрицательные свойства."""
from copy import deepcopy
import pytest
from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.belt_attachments import loaded,preserve
from app.services.equipment_modifications import make_record,install
from app.services.artifact_effects import artifact_passive_profile,apply_artifact_world_movement
from app.services.combat import CombatService
from app.services.exceptions import ValidationError
from test_consumable_use import pair


def setup_artifact(pair,quantity=1):
    template=ItemTemplate(name='Контрольный артефакт',category='artifact',weight=2,volume=2,
        attributes={'positive_effect':'Защита от физического урона +15%',
                    'negative_effect':'Штраф перемещения +2 Точность -1 3 Радиации за перемещение'})
    db.session.add(template);db.session.flush()
    item={'id':'belt-artifact-test','templateId':template.id,'name':template.name,'category':'artifact',
        'weight':2,'volume':2,'quantity':quantity,'attributes':deepcopy(template.attributes)}
    character=pair['actor_character'];data=deepcopy(character.data)
    data['equipment']={'belt':{'id':'artifact-belt','category':'belt','weight':1,'modifications':[make_record('armor-g70')]}}
    data['inventory']={'pockets':[item],'backpack':[]};data['weapons']=[];data['health']['radiation']=0
    character.data=data;pair['actor_location'].action_points_current=20;db.session.commit()
    return item


def change(client,pair,auth_headers,operation='mount',*,kind='artifact',source=None):
    character=pair['actor_character'];data=character.data
    item=data['inventory']['pockets'][0] if operation=='mount' else data['equipment']['belt']['installedModules'][0]
    path=['inventory','pockets',0] if operation=='mount' else ['equipment','belt','installedModules',0]
    return client.post(f'/lobbies/characters/{character.id}/belt-attachment',headers=auth_headers(pair['actor_user']),json={
        'operation':operation,'kind':kind,'belt_id':'artifact-belt','character_revision':character.revision,
        'source':source or {'path':path,'snapshot':deepcopy(item)}})


@pytest.mark.parametrize('quantity',[1,2])
def test_mount_preserves_physical_artifact_and_activates_both_sides_without_armor(client,pair,auth_headers,quantity):
    item=setup_artifact(pair,quantity);assert change(client,pair,auth_headers).status_code==200
    data=pair['actor_character'].data;artifact=loaded(data['equipment']['belt'],'artifact')[0][1]
    assert artifact['quantity']==1 and (artifact['id']==item['id']) is (quantity==1)
    assert len(data['inventory']['pockets'])==quantity-1
    profile=artifact_passive_profile(data)
    assert profile['protection']['physical']==15 and profile['movement_penalty']==2 and profile['accuracy']==-1
    assert CombatService._target_armor(data,'chest')[0]==15
    assert CombatService._inventory_weight_details(data)['total_weight']==2*quantity
    apply_artifact_world_movement(data);assert data['health']['radiation']==3
    assert pair['actor_location'].action_points_current==17
    assert data['health']['combatMeta']['actionJournal'][-1]['label']=='Крепление артефакта на поясе'


def test_unmount_restores_same_artifact_and_removes_effects(client,pair,auth_headers):
    item=setup_artifact(pair);assert change(client,pair,auth_headers).status_code==200
    assert change(client,pair,auth_headers,'unmount').status_code==200
    data=pair['actor_character'].data;returned=data['inventory']['pockets'][0]
    assert returned['id']==item['id'] and 'slotType' not in returned
    assert artifact_passive_profile(data)['protection']['physical']==0
    assert artifact_passive_profile(data)['radiation_per_movement']==0
    assert pair['actor_location'].action_points_current==14


def test_single_slot_category_and_occupied_replacement(client,pair,auth_headers):
    setup_artifact(pair,2)
    assert change(client,pair,auth_headers,kind='tool').status_code==400
    assert change(client,pair,auth_headers,kind=[]).status_code==400
    assert change(client,pair,auth_headers).status_code==200
    assert change(client,pair,auth_headers).status_code==400
    with pytest.raises(ValidationError,match='Сначала снимите артефакт'):
        install(deepcopy(pair['actor_character'].data['equipment']['belt']),make_record('armor-g62'),'armor-g70')


def test_effects_require_worn_active_mount_and_mass_has_no_free_tool_exemption(client,pair,auth_headers):
    setup_artifact(pair);assert change(client,pair,auth_headers).status_code==200
    data=deepcopy(pair['actor_character'].data);belt=data['equipment']['belt']
    belt['modifications'].append(make_record('armor-g58'))
    assert CombatService._inventory_weight_details(data)['total_weight']==1
    belt['protectionDisabled']=True
    assert artifact_passive_profile(data)['protection']['physical']==0
    assert CombatService._inventory_weight_details(data)['total_weight']==2
    belt.pop('protectionDisabled');data['equipment'].pop('belt');data['inventory']['backpack'].append(belt)
    assert artifact_passive_profile(data)['protection']['physical']==0
    assert CombatService._inventory_weight_details(data)['total_weight']==3


def test_sheet_preserves_artifact_with_belt_and_rejects_copy_or_slot_renaming(client,pair,auth_headers):
    setup_artifact(pair);assert change(client,pair,auth_headers).status_code==200
    current=deepcopy(pair['actor_character'].data);incoming=deepcopy(current)
    belt=incoming['equipment'].pop('belt');incoming['inventory']['backpack'].append(belt)
    artifact=deepcopy(loaded(belt,'artifact')[0][1]);belt['installedModules'][0]['slotType']='fake-slot'
    preserve(current,incoming)
    assert belt['installedModules']==[artifact]
    copied=deepcopy(incoming);copied['inventory']['pockets'].append(artifact)
    with pytest.raises(ValidationError,match='отдельным действием'):preserve(current,copied)


def test_existing_armor_artifact_and_belt_artifact_both_contribute(client,pair,auth_headers):
    setup_artifact(pair);assert change(client,pair,auth_headers).status_code==200
    data=deepcopy(pair['actor_character'].data)
    data['equipment']['armor']={'containers':[{'item':{'category':'artifact','attributes':{'positive_effect':'Защита от физического урона +5%'}}}]}
    assert artifact_passive_profile(data)['protection']['physical']==20


def test_server_profile_is_derived_transport_data_and_cannot_be_saved_or_forged(client,pair,auth_headers):
    setup_artifact(pair);assert change(client,pair,auth_headers).status_code==200
    character=pair['actor_character'];snapshot=character.data_snapshot()
    assert snapshot['_artifact_profile']['movement_penalty']==2
    assert snapshot['_artifact_profile']['protection']['physical']==15
    snapshot['_artifact_profile']['movement_penalty']=-100
    result=client.put(f'/lobbies/characters/{character.id}',headers=auth_headers(pair['actor_user']),json={'data':snapshot})
    assert result.status_code==200,result.json
    assert '_artifact_profile' not in character.data
    assert character.data_snapshot()['_artifact_profile']['movement_penalty']==2
