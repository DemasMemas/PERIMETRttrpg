import pytest

from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.combat import CombatService
from app.services.exceptions import ValidationError


def test_fragment_glasses_are_incompatible_in_both_equipment_orders(app):
    from app.services.equipment_passives import validate_glasses
    data = {'equipment': {'glasses': {'name':'Защитные очки', 'attributes':{
        'incompatible_with_gasmask':True, 'incompatible_with_visor':True}}},
        'inventory': {'pockets': []}}
    validate_glasses(data)
    mask = add_template('Проверочный противогаз', 'gas_mask')
    data['inventory']['pockets'].append(inventory_item(mask))
    with pytest.raises(ValidationError, match='очк'):
        CombatService.equipment_action_details(data, 'equip', 'gasMask', item_path=['inventory','pockets',0])
    data['equipment']['gasMask'] = inventory_item(mask)
    with pytest.raises(ValidationError, match='очк'):
        validate_glasses(data)
    data['equipment'].pop('gasMask')
    helmet = add_template('Шлем с забралом', 'helmet', attributes={'slots':[{'type':'visor'}]})
    data['inventory']['pockets'].append(inventory_item(helmet,installedModules=[{'slotType':'visor'}]))
    with pytest.raises(ValidationError, match='очк'):
        CombatService.equipment_action_details(data, 'equip', 'helmet', item_path=['inventory','pockets',1])


def test_fragment_glasses_protect_eyes_but_do_not_cover_ears_or_whole_head(app):
    data = {'equipment': {'glasses': {'eyePhysicalProtection':15}}}
    assert CombatService._target_armor(data, 'head', 'eyes')[0] == 15
    assert CombatService._target_armor(data, 'head', 'ears')[0] == 0
    assert CombatService._target_armor(data, 'head')[0] == 0


@pytest.mark.parametrize('strength,normal,wrapped', [(8,10,25), (10,10,25), (14,20,30), (20,50,75)])
def test_hand_wraps_change_real_unarmed_attack_profile(app, strength, normal, wrapped):
    from copy import deepcopy
    data = {'skills':{'physical':{'strength':{'base':strength}}}, 'health':{'effects':[]}}
    assert CombatService._virtual_melee_profile('unarmed', character_data=data)['damage'] == normal
    data['equipment'] = {'gloves': {'name':'Обвязки на руки'}}
    assert CombatService._virtual_melee_profile('unarmed', character_data=data)['damage'] == wrapped
    carried = deepcopy(data['equipment']['gloves'])
    data['equipment'] = {}
    data['inventory'] = {'pockets':[carried]}
    assert CombatService._virtual_melee_profile('unarmed', character_data=data)['damage'] == normal


def test_legacy_incompatible_outfit_can_save_health_but_new_incompatibility_is_rejected(app):
    from copy import deepcopy
    from app.services.equipment_passives import validate_glasses
    previous = {'equipment': {'helmet':'Старое имя вместо объекта',
        'glasses': {'name':'Очки', 'attributes':{'incompatible_with_gasmask':True}},
        'gasMask': {'templateId':12, 'name':'Противогаз'}}}
    updated = deepcopy(previous)
    updated['health'] = {'painLevel':2}
    validate_glasses(updated, previous)
    previous['equipment'].pop('glasses')
    with pytest.raises(ValidationError):
        validate_glasses(updated, previous)


def add_template(name, category, *, subcategory=None, weight=0, attributes=None):
    template = ItemTemplate(
        name=name,
        category=category,
        subcategory=subcategory,
        weight=weight,
        volume=1,
        attributes=attributes or {},
        compatible_ids=[],
    )
    db.session.add(template)
    db.session.flush()
    return template


def inventory_item(template, **updates):
    item = {
        "templateId": template.id,
        "name": template.name,
        "category": template.category,
        "subcategory": template.subcategory,
        "weight": template.weight,
        "attributes": dict(template.attributes or {}),
        "quantity": 1,
    }
    item.update(updates)
    return item


def test_armor_equipment_cost_includes_inventory_retrieval_and_preserves_item(app):
    armor = add_template(
        "Medium armor",
        "armor",
        subcategory="Средняя",
        weight=8,
        attributes={
            "max_durability": 40,
            "movement_penalty": 3,
            "protection": {"physical": 0.4},
            "protection_zones": ["chest", "abdomen"],
        },
    )
    data = {
        "inventory": {"backpack": [inventory_item(armor, durability=27)], "pockets": []},
        "equipment": {},
    }

    details = CombatService.equipment_action_details(
        data,
        "equip",
        "armor",
        item_path=["inventory", "backpack", 0],
        retrieval_action_points=0,
        in_combat=True,
    )
    result = CombatService.apply_equipment_action(data, details)

    assert details["action_points"] == 22
    assert result["action_points"] == 22
    assert data["inventory"]["backpack"] == []
    assert data["equipment"]["armor"]["durability"] == 27
    assert data["equipment"]["armor"]["movementPenalty"] == 3

    remove = CombatService.equipment_action_details(
        data,
        "unequip",
        "armor",
        in_combat=True,
    )
    CombatService.apply_equipment_action(data, remove)
    assert remove["action_points"] == 10
    assert "armor" not in data["equipment"]
    assert data["inventory"]["backpack"][0]["durability"] == 27


@pytest.mark.parametrize(
    ("weight", "equip_cost", "remove_cost"),
    [(2, 2, 1), (2.1, 4, 3)],
)
def test_helmet_cost_uses_two_kilogram_boundary(app, weight, equip_cost, remove_cost):
    helmet = add_template("Helmet", "helmet", weight=weight)
    data = {
        "inventory": {"backpack": [inventory_item(helmet)], "pockets": []},
        "equipment": {},
    }
    equip = CombatService.equipment_action_details(
        data,
        "equip",
        "helmet",
        item_path=["inventory", "backpack", 0],
        in_combat=True,
    )
    assert equip["action_points"] == equip_cost + 2
    assert equip["inventory_access"]["source"] == "backpack"
    CombatService.apply_equipment_action(data, equip)
    remove = CombatService.equipment_action_details(
        data,
        "unequip",
        "helmet",
        in_combat=True,
    )
    assert remove["action_points"] == remove_cost


def test_integrated_helmet_follows_armor(app):
    armor = add_template(
        "Sealed suit",
        "armor",
        subcategory="Научная",
        attributes={
            "integrated_helmet": True,
            "integrated_helmet_name": "Suit helmet",
            "integrated_helmet_profile": {
                "physical": 0.35,
                "accuracyPenalty": 3,
                "charismaPenalty": 2,
            },
        },
    )
    data = {
        "inventory": {"backpack": [inventory_item(armor)], "pockets": []},
        "equipment": {},
    }
    equip = CombatService.equipment_action_details(
        data,
        "equip",
        "armor",
        item_path=["inventory", "backpack", 0],
        in_combat=True,
    )
    CombatService.apply_equipment_action(data, equip)
    assert data["equipment"]["helmet"]["integratedWithArmor"] is True
    assert data["equipment"]["helmet"]["name"] == "Suit helmet"
    assert data["equipment"]["helmet"]["protection"]["physical"] == 0.35

    remove = CombatService.equipment_action_details(data, "unequip", "armor", in_combat=True)
    CombatService.apply_equipment_action(data, remove)
    assert "helmet" not in data["equipment"]


@pytest.mark.parametrize(
    "occupied_slot",
    [
        {"helmet": {"templateId": 999, "name": "Helmet"}},
        {"gasMask": {"templateId": 998, "name": "Gas mask"}},
    ],
)
def test_integrated_helmet_armor_does_not_overwrite_head_equipment(app, occupied_slot):
    armor = add_template(
        "Sealed armor",
        "armor",
        attributes={"integrated_helmet": True},
    )
    data = {
        "inventory": {"backpack": [inventory_item(armor)]},
        "equipment": occupied_slot,
    }

    with pytest.raises(ValidationError, match="Remove the"):
        CombatService.equipment_action_details(
            data,
            "equip",
            "armor",
            item_path=["inventory", "backpack", 0],
        )


def test_visor_helmet_and_gas_mask_are_mutually_exclusive(app):
    helmet = add_template(
        "Visor helmet",
        "helmet",
        attributes={"integrated_visor": True},
    )
    gas_mask = add_template("Gas mask", "gas_mask")
    data = {
        "inventory": {
            "backpack": [inventory_item(helmet), inventory_item(gas_mask)],
        },
        "equipment": {"gasMask": {"templateId": gas_mask.id, "name": gas_mask.name}},
    }

    with pytest.raises(ValidationError, match="cannot be worn"):
        CombatService.equipment_action_details(
            data,
            "equip",
            "helmet",
            item_path=["inventory", "backpack", 0],
        )


@pytest.mark.parametrize('first', ['helmet', 'gasMask'])
def test_helmet_with_empty_visor_mount_can_be_worn_with_gas_mask_in_either_order(app,first):
    helmet=add_template('Шлем без забрала','helmet',attributes={
        'slots':[{'type':'visor'}], 'protection_zones':['crown','back','face']})
    mask=add_template('Противогаз','gas_mask')
    data={'inventory':{'backpack':[inventory_item(helmet),inventory_item(mask)],'pockets':[]},'equipment':{}}
    for slot in (first,'gasMask' if first=='helmet' else 'helmet'):
        category='helmet' if slot=='helmet' else 'gas_mask'
        index=next(index for index,item in enumerate(data['inventory']['backpack']) if item['category']==category)
        details=CombatService.equipment_action_details(data,'equip',slot,item_path=['inventory','backpack',index])
        CombatService.apply_equipment_action(data,details)
    assert data['equipment']['helmet']['templateId']==helmet.id
    assert data['equipment']['gasMask']['templateId']==mask.id
    for slot in (first,'gasMask' if first=='helmet' else 'helmet'):
        details=CombatService.equipment_action_details(data,'unequip',slot)
        CombatService.apply_equipment_action(data,details)
    assert not data['equipment']
    assert {item['templateId'] for item in data['inventory']['backpack']}=={helmet.id,mask.id}


@pytest.mark.parametrize('helmet_attrs,installed_modules',[
    ({'integrated_visor':True},[]),
    ({'slots':[{'type':'visor'}]},[{'slotType':'visor','name':'Забрало'}]),
    ({'requires_filter':True},[]),
])
@pytest.mark.parametrize('first',['helmet','gasMask'])
def test_real_visor_or_gas_mask_helmet_blocks_both_equipment_orders(app,helmet_attrs,installed_modules,first):
    helmet=add_template('Проверочный шлем','helmet',attributes=helmet_attrs)
    mask=add_template('Противогаз','gas_mask')
    helmet_item=inventory_item(helmet,installedModules=installed_modules)
    mask_item=inventory_item(mask)
    data={'inventory':{'backpack':[helmet_item,mask_item],'pockets':[]},
        'equipment':{first:helmet_item if first=='helmet' else mask_item}}
    other='gasMask' if first=='helmet' else 'helmet'
    index=1 if first=='helmet' else 0
    with pytest.raises(ValidationError,match='cannot be worn'):
        CombatService.equipment_action_details(data,'equip',other,item_path=['inventory','backpack',index])


def test_integrated_armor_helmet_blocks_gas_mask_even_without_generated_helmet(app):
    armor=add_template('Броня со встроенным шлемом','armor',attributes={'integrated_helmet':True})
    mask=add_template('Противогаз','gas_mask')
    data={'inventory':{'backpack':[inventory_item(mask)],'pockets':[]},
        'equipment':{'armor':inventory_item(armor)}}
    with pytest.raises(ValidationError,match='integrated armor helmet'):
        CombatService.equipment_action_details(data,'equip','gasMask',item_path=['inventory','backpack',0])


def test_exoskeleton_requires_tools_and_engineering_and_is_blocked_in_combat(app):
    exoskeleton = add_template(
        "Экзоскелет",
        "armor",
        subcategory="Экзоскелет",
        attributes={"is_exoskeleton": True, "requires_exoskeleton_battery": True},
    )
    simple_tool = {
        "name": "Упрощенные инструменты бронника",
        "category": "tool",
        "quantity": 1,
        "attributes": {"repair_profile": {"kind": "armor", "max_item_class": 2}},
    }
    advanced_tool = {
        "name": "Расширенные инструменты бронника",
        "category": "tool",
        "quantity": 1,
        "attributes": {
            "repair_profile": {"kind": "armor", "engineering_min": 17},
        },
    }
    data = {
        "skills": {"other": {"engineering": {"base": 18, "bonus": 0}}},
        "inventory": {
            "backpack": [inventory_item(exoskeleton), simple_tool, advanced_tool],
            "pockets": [],
        },
        "equipment": {},
    }
    with pytest.raises(ValidationError, match="during combat"):
        CombatService.equipment_action_details(
            data,
            "equip",
            "armor",
            item_path=["inventory", "backpack", 0],
            in_combat=True,
        )

    equip = CombatService.equipment_action_details(
        data,
        "equip",
        "armor",
        item_path=["inventory", "backpack", 0],
        in_combat=False,
    )
    assert equip["duration_minutes"] == 10
    CombatService.apply_equipment_action(data, equip)
    remove = CombatService.equipment_action_details(
        data,
        "unequip",
        "armor",
        in_combat=False,
    )
    assert remove["duration_minutes"] == 60
