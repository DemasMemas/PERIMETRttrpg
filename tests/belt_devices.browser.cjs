const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, npc, safeSession}) => {
    assert.equal(process.env.VTT_SAFE_SCENARIO, 'equipment');
    assert.ok(safeSession.verified);
    const item = (template,id) => ({id,templateId:template.id,name:template.name,category:template.category,
        subcategory:template.subcategory,attributes:structuredClone(template.attributes),
        weight:template.weight,volume:template.volume,quantity:1});
    const belt = item(offered.global.find(template=>template.category==='belt'),'devices-belt');
    const detector = {...item(offered.global.find(template=>template.category==='detector'),'devices-detector'),bonus:7};
    const dosimeter = item(offered.global.find(template=>template.attributes?.device_kind==='dosimeter'),'devices-dosimeter');
    const radio = item(offered.global.find(template=>template.name==='Рация'),'devices-radio');
    const owner = await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель приборов на поясе',data:{
        equipment:{belt},inventory:{money:100000,pockets:[detector,dosimeter,radio],backpack:[]},
    }},token);
    const sheet = '/lobbies/characters/'+owner.id;
    const read = () => request(sheet,null,token,'GET');
    const placed = await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:49},token);
    const install = async (action,id) => {
        const specialist = await request('/lobbies/characters/'+npc.id,null,token,'GET');
        await request(loc+'/professional-modification',{actor_location_character_id:placed.location_character_id,
            specialist_character_id:npc.id,target_path:['equipment','belt'],item_id:belt.id,
            ...action,execute:true,operation_id:id,actor_revision:(await read()).data._revision,
            specialist_revision:specialist.data._revision},token);
    };
    await install({rule_id:'armor-g68'},'belt-qa-detector');
    await install({action:'unlock_slot'},'belt-qa-device-slot');
    await install({rule_id:'armor-g69'},'belt-qa-radio');
    await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=detector]')",'Вкладка приборов');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=detector]').click();true");
    const choose = async (kind,name,names) => {
        await page.evaluate(`document.querySelector('[data-belt-device="${kind}"] button').click();true`);
        await page.until("document.querySelector('.consumable-application-list button')",'Подходящие приборы');
        const actual = await page.evaluate("[...document.querySelectorAll('.consumable-application-list button')].map(button=>button.textContent)");
        assert.deepEqual(actual.sort(),names.sort());
        await page.evaluate(`Array.from(document.querySelectorAll('.consumable-application-list button'))
            .find(button=>button.textContent===${JSON.stringify(name)}).click();true`);
        await page.until(`(()=>{const mount=document.querySelector('[data-belt-device="${kind}"]');
            return mount?.textContent.includes(${JSON.stringify(' · '+name)})
                && Array.from(mount.querySelectorAll('button')).some(button=>button.textContent.startsWith('Снять'))})()`,
        'Прибор закреплён');
    };
    await choose('detector',detector.name,[detector.name,dosimeter.name]);
    await page.until("document.querySelector('[data-belt-device=detector]')?.textContent.includes('бонус 7')",'Бонус закреплённого детектора');
    await choose('radio',radio.name,[radio.name]);
    const mounted = (await read()).data.equipment.belt.installedModules;
    assert.deepEqual(mounted.map(entry=>[entry.id,entry.slotType]),[[detector.id,'belt_detector'],[radio.id,'belt_radio']]);
    await page.evaluate("document.querySelector('[data-belt-device=detector] button').click();true");
    await page.until("document.querySelector('[data-belt-device=detector]')?.textContent.includes('пусто')",'Детектор снят в карманы');
    await choose('detector',dosimeter.name,[detector.name,dosimeter.name]);
    await page.evaluate("[...document.querySelectorAll('[data-belt-device=detector] button')].find(button=>button.textContent==='Показания').click();true");
    await page.until("document.querySelector('#dosimeter-modal')?.textContent.includes('батарейка')",'Показания дозиметра из крепления');
    await page.evaluate("[...document.querySelectorAll('#dosimeter-modal button')].find(button=>button.textContent==='Включить').click();true");
    await page.until("document.querySelector('#dosimeter-modal')?.textContent.includes('Включён')",'Дозиметр включён на поясе');
    const saved = (await read()).data;
    assert.ok(saved.equipment.belt.installedModules.find(entry=>entry.id===dosimeter.id).deviceState.on);
    await page.evaluate("[...document.querySelectorAll('#dosimeter-modal button')].find(button=>button.textContent==='Закрыть').click();window.closeCharacterSheet();true");
    console.log('Приборы на поясе: две платные модификации, допустимые приборы, два физических крепления, бонус детектора и включение дозиметра.');
};
