const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, npc, safeSession}) => {
    assert.equal(process.env.VTT_SAFE_SCENARIO, 'equipment');
    assert.ok(safeSession.verified);
    const item = (template,id,quantity=1) => ({id,templateId:template.id,name:template.name,category:template.category,
        subcategory:template.subcategory,attributes:structuredClone(template.attributes),
        weight:template.weight,volume:template.volume,quantity});
    const named = (name,id,quantity) => item(offered.global.find(template=>template.name===name),id,quantity);
    const belt = item(offered.global.find(template=>template.category==='belt'),'sheriff-belt');
    const pistol = named('Пистолет Февраль','sheriff-pistol');
    const large = item(offered.global.find(template=>template.category==='weapon' && Number(template.attributes?.size)>=4),'sheriff-large');
    const ties = named('Хомут пластиковый','sheriff-ties',16);
    const rope = named('Веревка 10м','sheriff-rope');
    const first = named('9x19','sheriff-ammo-first',20);
    const other = named('9x18','sheriff-ammo-other',40);
    const magazine = named('Магазин Пистолетный 9x19','sheriff-magazine');
    const owner = await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель ремня Шерифа',data:{
        equipment:{belt},weapons:[pistol,large],inventory:{money:100000,pockets:[ties,rope,first,other,magazine],backpack:[]},
    }},token);
    const read = () => request('/lobbies/characters/'+owner.id,null,token,'GET');
    const placed = await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:49},token);
    const install = async (action,id) => {
        const specialist = await request('/lobbies/characters/'+npc.id,null,token,'GET');
        await request(loc+'/professional-modification',{actor_location_character_id:placed.location_character_id,
            specialist_character_id:npc.id,target_path:['equipment','belt'],item_id:belt.id,
            ...action,execute:true,operation_id:id,actor_revision:(await read()).data._revision,
            specialist_revision:specialist.data._revision},token);
    };
    for (const [index,rule] of ['armor-g72','armor-g73','armor-g74'].entries()) {
        if (index) await install({action:'unlock_slot'},`sheriff-slot-${index}`);
        await install({rule_id:rule},`sheriff-install-${rule}`);
    }
    await page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
    const mount = async (kind, name, quantity, state) => {
        await page.evaluate(`document.querySelector('[data-belt-quick="${kind}"] button').click();true`);
        await page.until("document.querySelector('.consumable-application-list button')",'Выбор содержимого крепления');
        await page.evaluate(`(()=>{const button=[...document.querySelectorAll('.consumable-application-list button')]
            .find(e=>e.textContent===${JSON.stringify(name)});const input=button.parentElement.querySelector('input');
            if(input)input.value=${JSON.stringify(String(quantity))};button.click();return true})()`);
        await page.until(`document.querySelector('[data-belt-quick="${kind}"] h5')?.textContent.includes(${JSON.stringify(state)})`,'Количество на поясе');
    };
    await mount('restraint',ties.name,15,'15/15');
    await mount('cartridge',first.name,20,'20/50');
    await mount('cartridge',other.name,30,'50/50');
    await page.evaluate("document.querySelector('[data-belt-holster] button').click();true");
    await page.until("document.querySelector('.consumable-application-list button')",'Выбор оружия для кобуры');
    assert.deepEqual(await page.evaluate("[...document.querySelectorAll('.consumable-application-list button')].map(e=>e.textContent)"),[pistol.name]);
    await page.evaluate("document.querySelector('.consumable-application-list button').click();true");
    await page.until(`document.querySelector('[data-belt-holster] h5')?.textContent.includes(${JSON.stringify(pistol.name)})`,'Пистолет в кобуре');

    const path = (await read()).data.inventory.pockets.findIndex(entry=>entry.id===magazine.id);
    await page.evaluate(`window.reloadMagazineFromInventory('inventory,pockets,${path}').then(()=>true)`);
    await page.until("document.querySelector('#ammo-select option')?.textContent.includes('9x19')",'Патроны из патронташа в штатной зарядке');
    await page.evaluate("document.querySelector('#reload-inventory-full-btn').click();true");
    await page.until("document.querySelector('#ammo-select-modal')?.style.display==='none'",'Магазин заряжен');
    const loaded = (await read()).data;
    assert.ok(loaded.inventory.pockets.find(entry=>entry.id===magazine.id).currentAmmo>0);
    assert.ok(loaded.equipment.belt.installedModules.find(entry=>entry.slotType==='belt_cartridge'&&entry.name===first.name).quantity<20);

    const actor = placed.location_character_id;
    await request(loc+'/combat/start',{location_character_ids:[actor],initiator_location_character_id:actor,initiator_first:true},token);
    const combat = async () => (await request(loc+'/combat',null,token,'GET')).current_character;
    const before = await combat();
    await page.evaluate("document.querySelector('[data-belt-quick=restraint] button[onclick*=retrieveBeltItem]').click();true");
    await page.until("document.querySelector('#held-items-container .item-name')?.textContent.includes('Хомут')",'Хомуты в руке');
    const afterTies = await combat();
    assert.equal(afterTies.action_points_current,before.action_points_current-1);
    assert.equal(afterTies.free_actions_current,before.free_actions_current);
    await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=weapon-0]').click();true");
    const draw = '[data-weapon-draw-hand=right][data-weapon-draw-index="0"]';
    const cost = afterTies.weapon_ergonomics.find(entry=>entry.weapon_index===0).draw_action_points;
    assert.ok(await page.evaluate(`document.querySelector(${JSON.stringify(draw)}).textContent.includes('${cost} ОД')`));
    await page.evaluate(`document.querySelector(${JSON.stringify(draw)}).click();true`);
    await page.until("document.querySelector('[data-weapon-grip-label=\"0\"]')?.textContent.includes('правая рука')",'Штатное доставание оружия из кобуры');
    assert.equal((await combat()).action_points_current,afterTies.action_points_current-cost);
    await request(loc+'/combat/end',{},token);
    await page.evaluate('window.closeCharacterSheet();true');
    console.log('Модификации Шерифа: 15 хомутов, 50 патронов двух калибров, штатная зарядка из патронташа, 1 ОД за ремень и штатное доставание из кобуры.');
};
