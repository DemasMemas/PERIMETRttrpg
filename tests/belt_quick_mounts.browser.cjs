const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, npc, safeSession}) => {
    assert.equal(process.env.VTT_SAFE_SCENARIO, 'equipment');
    assert.ok(safeSession.verified);
    const item = (template,id) => ({id,templateId:template.id,name:template.name,category:template.category,
        subcategory:template.subcategory,attributes:structuredClone(template.attributes),
        weight:template.weight,volume:template.volume,quantity:1});
    const belt = item(offered.global.find(template=>template.category==='belt'),'quick-belt');
    const grenade = item(offered.global.find(template=>template.category==='grenade' && !template.attributes?.caliber),'quick-grenade');
    const bandage = item(offered.global.find(template=>template.name==='Бинт' && template.category==='consumable'),'quick-bandage');
    const owner = await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель быстрого доступа',data:{
        equipment:{belt},inventory:{money:100000,pockets:[grenade,bandage],backpack:[]},
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
    await install({rule_id:'armor-g60'},'belt-qa-grenade');
    await install({action:'unlock_slot'},'belt-qa-quick-slot');
    await install({rule_id:'armor-g64'},'belt-qa-dressing');
    await page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
    for (const kind of ['grenade','dressing']) {
        await page.evaluate(`document.querySelector('[data-belt-quick="${kind}"] button').click();true`);
        await page.until("document.querySelector('.consumable-application-list button')",'Подходящий предмет');
        await page.evaluate("document.querySelector('.consumable-application-list button').click();true");
        await page.until(`document.querySelector('[data-belt-quick="${kind}"] button[onclick*="retrieveBeltItem"]')`,'Предмет закреплён');
    }
    const actor = placed.location_character_id;
    await request(loc+'/combat/start',{location_character_ids:[actor],initiator_location_character_id:actor,initiator_first:true},token);
    const combat = async () => (await request(loc+'/combat',null,token,'GET')).current_character;
    const before = await combat();
    await request(loc+'/combat/adjust',{location_character_id:actor,free_actions:1-before.free_actions_current},token);
    for (const [kind,index] of [['grenade',0],['dressing',1]]) {
        await page.evaluate(`document.querySelector('[data-belt-quick="${kind}"] button[onclick*="retrieveBeltItem"]').click();true`);
        await page.until(`document.querySelectorAll('#held-items-container .item-name').length===${index+1}`,'Предмет в руке');
    }
    const after = await combat();
    assert.equal(after.action_points_current,before.action_points_current-1);
    assert.equal(after.free_actions_current,0);
    const held = (await read()).data.inventory.held;
    assert.deepEqual(held.map(entry=>entry.id),[grenade.id,bandage.id]);
    await page.evaluate("[...document.querySelectorAll('#held-items-container button')].find(button=>button.textContent==='Метнуть').click();true");
    await page.until("document.querySelector('#grenade-fuse-modal [data-fuse=normal]')",'Метание доступно для гранаты в руке');
    await page.evaluate("document.querySelector('#grenade-fuse-modal [data-cancel]').click();true");
    await request(loc+'/combat/end',{},token);
    await page.evaluate('window.closeCharacterSheet();true');
    console.log('Быстрый доступ: граната и бинт через штатные крепления, предметы в руках, списаны 1 СД и затем 1 ОД. Меню броска доступно.');
};
