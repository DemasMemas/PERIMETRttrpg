const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, npc, safeSession}) => {
    assert.equal(process.env.VTT_SAFE_SCENARIO, 'equipment');
    assert.ok(safeSession.verified);
    const item = (template,id) => ({id,templateId:template.id,name:template.name,category:template.category,
        itemClass:template.item_class,attributes:structuredClone(template.attributes),
        weight:template.weight,volume:template.volume,quantity:1});
    const armor = item(offered.global.find(template=>template.name==='Экзоскелет'),'station-exoskeleton');
    const battery = item(offered.global.find(template=>template.attributes?.slot_type==='exoskeleton_battery'),'station-battery');
    battery.slotType = 'exoskeleton_battery';
    battery.attributes.remaining_days = 0;
    armor.installedModules = [battery];
    const fuel = item(offered.global.find(template=>template.id===9601),'station-artifact');
    const trash = item(offered.global.find(template=>template.id===9602),'station-trash');
    const owner = await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель электростанции',data:{
        equipment:{armor},inventory:{money:150000,pockets:[fuel,trash],backpack:[]},
    }},token);
    const sheet = '/lobbies/characters/'+owner.id;
    const read = () => request(sheet,null,token,'GET');
    const placed = await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:49},token);
    let specialist = await request('/lobbies/characters/'+npc.id,null,token,'GET');
    await request(`/lobbies/${lobby.id}/characters/${npc.id}/merchant`,{character_revision:specialist.data._revision,
        enabled:false,repair_enabled:true,modification_enabled:true,scientist_enabled:true,
        workbenches:['Станок Бронника'],prices:{}},token,'PATCH');
    specialist = await request('/lobbies/characters/'+npc.id,null,token,'GET');
    const modification = {actor_location_character_id:placed.location_character_id,specialist_character_id:npc.id,
        target_path:['equipment','armor'],item_id:armor.id,rule_id:'armor-d29'};
    const quote = await request(loc+'/professional-modification',modification,token);
    await request(loc+'/professional-modification',{...modification,execute:true,operation_id:'qa-install-d29',
        actor_revision:(await read()).data._revision,specialist_revision:specialist.data._revision},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    const combat = () => request(loc+'/combat',null,token,'GET');
    const before = (await combat()).current_character.action_points_current;
    await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.loadout-slot[data-equipment-open=armor]')",'Слот экзоскелета');
    await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=armor]').click();true");
    await page.evaluate("document.querySelector('[onclick=\"window.rechargeExoskeleton()\"]').click();true");
    await page.until("document.querySelector('#consumable-application-modal .consumable-application-list button')",'Выбор артефакта');
    assert.deepEqual(await page.evaluate("[...document.querySelectorAll('.consumable-application-list button')].map(e=>e.textContent)"),[fuel.name]);
    await page.evaluate("document.querySelector('.consumable-application-list button').click();true");
    await page.until("[...document.querySelectorAll('.equipment-care-status')].some(e=>e.textContent.includes('заряд 100%'))",'Батарея заряжена сразу');
    const {data} = await read();
    assert.equal(data.inventory.money,150000-quote.price);
    assert.deepEqual(data.inventory.pockets.map(entry=>entry.id),[trash.id]);
    assert.equal(data.equipment.armor.installedModules[0].id,battery.id);
    assert.equal(data.equipment.armor.installedModules[0].attributes.remaining_days,battery.attributes.charge_days);
    assert.equal((await combat()).current_character.action_points_current,before-5);
    await request(loc+'/combat/end',{},token);
    await page.evaluate('window.closeCharacterSheet();true');
    console.log('Электростанция: услуга учёного, выбор артефакта, та же батарея заряжена сразу за 5 ОД; Мусорный артефакт остался.');
};
