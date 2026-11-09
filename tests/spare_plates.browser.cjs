const assert = require('node:assert/strict');
const {waitForDocument} = require('./durable_reload.browser.cjs');

module.exports = async ({page, request, token, lobby, offered, loc, npc, safeSession}) => {
    assert.equal(process.env.VTT_SAFE_SCENARIO, 'equipment');
    assert.ok(safeSession.verified);
    const heavy = offered.global.find(item => item.category === 'armor'
        && /^тяж[её]л/i.test(item.attributes?.modification_category || '') && item.price > 0);
    assert.ok(heavy, 'В каталоге есть тяжёлая броня');
    const armor = {
        id: 'qa-spare-plate-armor', templateId: heavy.id, name: heavy.name, category: 'armor',
        attributes: heavy.attributes, weight: heavy.weight, volume: heavy.volume, quantity: 1,
        durability: heavy.attributes.max_durability || 100, stage: 1,
    };
    const owner = await request(`/lobbies/${lobby.id}/characters`, {name:'Испытатель запасных пластин',
        data:{equipment:{armor},inventory:{money:300000,pockets:[],backpack:[]}}}, token);
    const sheet = '/lobbies/characters/' + owner.id;
    const read = () => request(sheet,null,token,'GET');
    const combat = () => request(loc+'/combat',null,token,'GET');
    const placed = await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:49},token);
    const actor = placed.location_character_id;
    const specialist = await request('/lobbies/characters/'+npc.id,null,token,'GET');
    const target = {actor_location_character_id:actor,specialist_character_id:npc.id,
        target_path:['equipment','armor'],item_id:armor.id,rule_id:'armor-a49'};
    const quote = await request(loc+'/professional-modification',target,token);
    await request(loc+'/professional-modification',{...target,execute:true,operation_id:'qa-install-plates',
        actor_revision:(await read()).data._revision,specialist_revision:specialist.data._revision},token);
    const prepared = (await read()).data;
    assert.equal(prepared.inventory.money,300000-quote.price);
    assert.equal(prepared.equipment.armor.modifications.at(-1).ruleId,'armor-a49');
    prepared.equipment.armor.stage=4;
    prepared.equipment.armor.condition='4. Сильно повреждена';
    prepared.equipment.armor.currentStageDurability=1;
    await request(sheet,{data:prepared},token,'PUT');
    await request(loc+'/combat/start',{location_character_ids:[actor],
        initiator_location_character_id:actor,initiator_first:true},token);
    const turn = (await combat()).current_character;
    assert.equal(turn.location_character_id,actor);
    await request(loc+'/combat/adjust',{location_character_id:actor,action_points:12-turn.action_points_current},token);
    const openArmor = async () => {
        await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
        await page.until("document.querySelector('.loadout-slot[data-equipment-open=armor]')",'Слот тяжёлой брони');
        await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=armor]').click();true");
    };
    const button = '[onclick="window.useSparePlates()"]';
    await openArmor();
    await page.until(`document.querySelector(${JSON.stringify(button)})?.getClientRects().length`,'Кнопка запасных пластин видна');
    assert.match(await page.evaluate(`document.querySelector(${JSON.stringify(button)}).textContent`),/10 ОД/);
    const before = (await read()).data;
    assert.equal(before.equipment.armor.stage,4);
    await page.evaluate(`document.querySelector(${JSON.stringify(button)}).click();true`);
    await page.until(`!document.querySelector(${JSON.stringify(button)})`,'После расхода пластин кнопка исчезла');
    const after = (await read()).data;
    const restored = after.equipment.armor;
    assert.equal(restored.stage,2);
    assert.equal(restored.currentStageDurability,restored.stageDurability);
    assert.equal(restored.durability,armor.durability);
    assert.equal(restored.modifications.length,0);
    assert.equal((await combat()).current_character.action_points_current,2);
    const receipt = after.health.combatMeta.actionJournal.find(entry=>entry.action==='spare_plates');
    assert.equal(receipt.action_points,10);
    const repeat = await request(sheet+'/spare-plates',{character_revision:before._revision,item_id:armor.id,
        operation_id:receipt.id.slice('spare-plates:'.length)},token);
    assert.equal(repeat.operation.duplicate,true);
    assert.equal((await combat()).current_character.action_points_current,2);
    await request(loc+'/combat/end',{},token);
    await page.evaluate('window.unequipArmor().then(()=>true)');
    const carried = (await read()).data.inventory.backpack;
    const index = carried.findIndex(item=>item.id===armor.id);
    assert.ok(index>=0);
    assert.equal(carried[index].stage,2);
    assert.equal(carried[index].modifications.length,0);
    await page.evaluate(`window.equipArmorFromInventory(['inventory','backpack',${index}]).then(()=>true)`);
    assert.equal((await read()).data.equipment.armor.id,armor.id);
    const documentState = await page.evaluate('({href:location.href,epoch:performance.timeOrigin})');
    await page.call('Page.reload');
    await waitForDocument(page,{href:documentState.href,afterEpoch:documentState.epoch,label:'Повторное открытие отремонтированной брони'});
    await page.until("typeof window.openCharacterSheet === 'function'",'Лист персонажа готов после перезагрузки');
    await openArmor();
    const reloaded = (await read()).data.equipment.armor;
    assert.equal(reloaded.id,armor.id);
    assert.equal(reloaded.stage,2);
    assert.equal(reloaded.currentStageDurability,restored.stageDurability);
    assert.equal(reloaded.modifications.length,0);
    await page.evaluate('window.closeCharacterSheet();true');
    console.log(JSON.stringify({sparePlates:true,professionalInstall:true,paidActionPoints:10,
        stageBefore:4,stageAfter:2,duplicate:true,physicalArmorRemounted:true,reloaded:true}));
};
