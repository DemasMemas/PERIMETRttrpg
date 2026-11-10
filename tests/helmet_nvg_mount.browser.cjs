// №187: штатная установка и проверка зрения ночью на копии Припяти.
const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, locationId}) => {
    const api = `/lobbies/${lobby.id}`;
    const buildItem = (template, id) => ({
        id, templateId: template.id, name: template.name, category: template.category,
        attributes: structuredClone(template.attributes), quantity: 1, weight: template.weight, volume: template.volume,
    });
    const helmetTemplate = offered.global.find(item => item.category === 'helmet'
        && item.attributes?.modification_category === 'Шлема без забрала'
        && !item.attributes.soft_headwear && !item.attributes.slots?.some(slot => slot.type === 'nvg'));
    const helmet = buildItem(helmetTemplate, 'night-vision-test-helmet');
    const nvg = buildItem(offered.global.find(item => item.attributes?.slot_type === 'nvg'), 'night-vision-test-device');
    const owner = await request(api+'/characters', {name:'Проверка ПНВ на шлеме', data:{
        equipment:{helmet}, inventory:{money:100000, pockets:[nvg], backpack:[]},
    }}, token);
    const mechanic = await request(api+'/characters', {name:'Мастер по шлемам', data:{is_npc:true}}, token);
    await request(api+'/characters/'+mechanic.id+'/merchant', {
        character_revision:mechanic.data._revision, enabled:false, repair_enabled:true,
        modification_enabled:true, workbenches:['Станок Бронника'], prices:{},
    }, token, 'PATCH');
    const placed = await request(loc+'/spawn_character', {character_id:owner.id, tile_x:43, tile_y:48}, token);
    await request(loc+'/spawn_character', {character_id:mechanic.id, tile_x:43, tile_y:49}, token);

    const ownerUrl = '/lobbies/characters/'+owner.id;
    const currentOwner = () => request(ownerUrl, null, token, 'GET');
    const install = async (ruleId, operationId, replaceRuleId) => {
        const ownerState = await currentOwner();
        const mechanicState = await request('/lobbies/characters/'+mechanic.id, null, token, 'GET');
        return request(loc+'/professional-modification', {
            actor_location_character_id:placed.location_character_id, specialist_character_id:mechanic.id,
            target_path:['equipment','helmet'], item_id:helmet.id, rule_id:ruleId, replace_rule_id:replaceRuleId,
            actor_revision:ownerState.data._revision, specialist_revision:mechanicState.data._revision,
            execute:true, operation_id:operationId,
        }, token);
    };
    const openHelmet = async () => {
        await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
        await page.until("document.querySelector('.loadout-slot[data-equipment-open=helmet]')", 'Шлем в листе');
        await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=helmet]').click();true");
    };
    const slots = '[data-widget-slots="equipment.helmet"]';
    const mountButton = slots+' button[onclick^="window.installModuleFromSlot"][onclick*="nvg"]';
    const removeButton = slots+' button[onclick^="window.uninstallModuleFromSlot"][onclick*="nvg"]';
    await openHelmet();
    assert.equal(await page.evaluate(`document.querySelectorAll(${JSON.stringify(mountButton)}).length`), 0);
    await install('armor-g25', 'nvg-mount-install');
    await openHelmet();
    await page.until(`document.querySelectorAll(${JSON.stringify(mountButton)}).length===1`, 'Один новый слот ПНВ');

    const oldClock = await request(api, null, token, 'GET');
    const nightDay = oldClock.game_day+1;
    await request(api+'/time', {game_day:nightDay, game_time_minutes:120}, token, 'PATCH');
    const checkVision = async operationId => (await request(ownerUrl+'/skill-check', {
        location_id:locationId, skill_path:'skills.physical.awareness.visual', operation_id:operationId,
    }, token)).check;
    const withoutNvg = await checkVision('nvg-check-without');
    assert.equal(withoutNvg.darkness, true);
    assert.equal(withoutNvg.equipment_modifier, -5);
    await page.evaluate(`document.querySelector(${JSON.stringify(mountButton)}).click();true`);
    await page.until("document.querySelector('#confirm-universal-module')", 'Выбор прибора на шлем');
    await page.evaluate("(async()=>{document.querySelector('#universal-module-select').selectedIndex=0;await document.querySelector('#confirm-universal-module').onclick();return true})()");
    await page.until(`(async()=>{const actor=await(await import('/static/js/api.js')).Server.getCharacter(${owner.id});
        return actor.data.equipment.helmet.installedModules?.some(item=>item.id===${JSON.stringify(nvg.id)})})()`, 'ПНВ сохранён в шлеме');
    const withNvg = await checkVision('nvg-check-with');
    assert.equal(withNvg.darkness, true);
    const nvgPenalty = -Math.abs(Number(nvg.attributes.awareness_penalty));
    assert.ok(nvgPenalty > -5);
    assert.equal(withNvg.equipment_modifier, nvgPenalty);
    await assert.rejects(install('armor-g26', 'nvg-mount-blocked', 'armor-g25'), /Сначала снимите ПНВ с крепления/);
    await request(api+'/time', {game_day:nightDay, game_time_minutes:130}, token, 'PATCH');
    const mounted = await currentOwner();
    const mountedDevice = mounted.data.equipment.helmet.installedModules[0];
    assert.equal(mountedDevice.id, nvg.id);
    assert.equal(mountedDevice.lightPower.uses_left, 9);
    assert.equal(mounted.data.equipment.helmet.modifications[0].ruleId, 'armor-g25');

    await openHelmet();
    await page.until(`document.querySelector(${JSON.stringify(removeButton)})`, 'Кнопка снятия ПНВ');
    await page.evaluate(`document.querySelector(${JSON.stringify(removeButton)}).click();true`);
    await page.until(`(async()=>{const actor=await(await import('/static/js/api.js')).Server.getCharacter(${owner.id});
        return actor.data.inventory.backpack.some(item=>item.id===${JSON.stringify(nvg.id)})})()`, 'Тот же ПНВ возвращён в рюкзак');
    const removed = await currentOwner();
    assert.deepEqual(removed.data.inventory.backpack.find(item=>item.id===nvg.id).lightPower, mountedDevice.lightPower);
    await install('armor-g26', 'nvg-mount-replace', 'armor-g25');
    await openHelmet();
    assert.equal(await page.evaluate(`document.querySelectorAll(${JSON.stringify(mountButton)}).length`), 0);
    assert.equal((await checkVision('nvg-check-after')).equipment_modifier, -5);
    await request(api+'/time', {game_day:nightDay, game_time_minutes:720}, token, 'PATCH');
    console.log('Крепление ПНВ: установка у мастера, слот в листе, каталожный штраф вместо штрафа темноты, отказ заменить занятое крепление, возврат того же ПНВ и замена пустого крепления.');
};
