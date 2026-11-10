const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, safeSession}) => {
    assert.equal(process.env.VTT_SAFE_SCENARIO, 'equipment');
    assert.ok(safeSession.verified);
    const pouch = offered.global.find(template => template.category === 'pouch'
        && template.attributes?.slots?.some(slot => slot.type === 'armor_plate'));
    const vest = offered.global.find(template => template.category === 'vest' && template.attributes?.modular
        && template.attributes.discount_pouch_type === pouch?.subcategory);
    const front = offered.global.find(template => template.name === 'Бронеплита Барьер-1');
    const back = offered.global.find(template => template.name === 'Бронеплита Гора-4');
    assert.ok(pouch && vest && front && back, 'В каталоге есть разгрузка и два подходящих вида плит');
    const item = (template,id) => ({id,templateId:template.id,name:template.name,category:template.category,
        attributes:structuredClone(template.attributes),weight:template.weight,volume:template.volume,quantity:1});
    const plate = (template,id) => ({...item(template,id),durability:template.attributes.durability,
        maxDurability:template.attributes.max_durability,stage:1});
    const owner = await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель передней и задней бронеплиты',data:{
        equipment:{vest:{...item(vest,'plate-vest'),model:vest.id,pouches:[]}},
        inventory:{pockets:[item(pouch,'plate-pouch-front'),item(pouch,'plate-pouch-back'),
            plate(front,'plate-front'),plate(back,'plate-back')],backpack:[]},
    }},token);
    await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:48},token);
    const read = () => request('/lobbies/characters/'+owner.id,null,token,'GET');
    const openInventory = () => page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
    await openInventory();
    for (const [index,template] of [front,back].entries()) {
        await page.evaluate('window.addVestPouch();true');
        await page.until("document.querySelector('#pouch-select-modal select option')",'Подсумок для плиты в выборе');
        await page.evaluate("document.querySelector('#pouch-select-modal .pouch-confirm').click();true");
        const pouchPath = ['equipment','vest','pouches',index];
        const mount = `[data-path="${pouchPath.join(',')}"] button[onclick*="installModuleFromSlot"]`;
        await page.until(`document.querySelector(${JSON.stringify(mount)})?.getClientRects().length`,'Крепление плиты в разгрузке');
        await page.evaluate(`document.querySelector(${JSON.stringify(mount)}).click();true`);
        await page.until(`Array.from(document.querySelectorAll('#universal-module-select option')).some(option=>option.textContent===${JSON.stringify(template.name)})`,'Выбор нужной бронеплиты');
        await page.evaluate(`(async()=>{const select=document.querySelector('#universal-module-select');
            select.value=Array.from(select.options).find(option=>option.textContent===${JSON.stringify(template.name)}).value;
            await document.querySelector('#confirm-universal-module').onclick();return true})()`);
    }
    const saved = await read();
    assert.deepEqual(saved.data.equipment.vest.pouches.map(entry=>[entry.id,entry.installedModules[0].id]),
        [['plate-pouch-front','plate-front'],['plate-pouch-back','plate-back']]);
    assert.equal(saved.data.inventory.pockets.length,0);
    await page.evaluate('window.closeCharacterSheet();true');
    await openInventory();
    const expectProtection = (front,back) => page.until(`(()=>{const summary=document.querySelector('[data-vest-protection]');
        return summary?.getClientRects().length&&summary.textContent.includes('перед ${front}%, спина ${back}%')})()`,
    `Защита плит в листе ${front}% / ${back}%`);
    await expectProtection(15,40);
    const updateVest = async (edit) => {
        await page.evaluate('window.closeCharacterSheet();true');
        const {data} = await read();
        edit(data.equipment.vest);
        await request('/lobbies/characters/'+owner.id,{data},token,'PUT');
        await openInventory();
    };
    await updateVest(worn => {
        delete worn.pouches[0].type;
        const plate = worn.pouches[1].installedModules[0];
        plate.protection = {physical:55};
        plate.stage = 4;
    });
    await expectProtection(15,44);
    await updateVest(worn => { worn.protectionDisabled = true; });
    await expectProtection(0,0);
    const disabled = (await read()).data.equipment.vest;
    assert.deepEqual(disabled.pouches.map(entry=>entry.installedModules[0].id),['plate-front','plate-back']);
    await page.evaluate('window.closeCharacterSheet();true');
    console.log(JSON.stringify({armorPlateMounts:true,front:15,back:40,damaged:44,disabled:0,physicalInstancesSaved:true}));
};
