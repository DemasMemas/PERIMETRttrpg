const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, npc, safeSession}) => {
    assert.equal(process.env.VTT_SAFE_SCENARIO,'equipment');
    assert.ok(safeSession.verified);
    const medical = offered.global.find(item => item.category==='pouch' && item.subcategory==='Медицинский' && item.attributes?.capacity===4);
    const magazine = offered.global.find(item => item.category==='pouch' && item.attributes?.allowed_categories?.includes('magazine'));
    const beltTemplate = offered.global.find(item => item.category==='belt');
    assert.ok(medical && magazine && beltTemplate);
    const make = (template,id) => ({id,templateId:template.id,name:template.name,category:template.category,
        subcategory:template.subcategory,weight:template.weight,volume:template.volume,
        capacity:template.attributes?.capacity,attributes:template.attributes,contents:[],quantity:1});
    const belt = {...make(beltTemplate,'qa-medical-belt'),maxPouches:2,pouches:[]};
    const stack = (id,volume) => ({id,name:id,category:'consumable',volume,weight:.1,quantity:1});
    const owner = await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель медицинских подсумков',
        data:{equipment:{belt},inventory:{money:300000,backpack:[],pockets:[
            make(medical,'qa-medical-pouch'),make(magazine,'qa-magazine-pouch'),
            stack('qa-medical-volume-six',6),stack('qa-medical-volume-three',3),stack('qa-medical-volume-one',1),
        ]}}},token);
    const url='/lobbies/characters/'+owner.id;
    const read=()=>request(url,null,token,'GET');
    const placed=await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:49},token);
    const specialist=await request('/lobbies/characters/'+npc.id,null,token,'GET');
    await request(loc+'/professional-modification',{actor_location_character_id:placed.location_character_id,
        specialist_character_id:npc.id,target_path:['equipment','belt'],item_id:belt.id,
        execute:true,rule_id:'armor-g66',operation_id:'qa-install-medical-pouches',
        actor_revision:(await read()).data._revision,specialist_revision:specialist.data._revision},token);
    const open=()=>page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
    const waitSave=()=>page.evaluate(`import('/static/js/api.js').then(({Server})=>Server.waitForCharacterSave(${owner.id})).then(()=>true)`);
    await open();
    for (const [name,count] of [[medical.name,1],[magazine.name,2]]) {
        await page.evaluate('window.addBeltPouch();true');
        await page.until("document.querySelector('#pouch-select-modal select option')",'Выбор физического подсумка');
        await page.evaluate(`(()=>{const select=document.querySelector('#pouch-select-modal select');
            const option=[...select.options].find(o=>o.textContent.includes(${JSON.stringify(name)}));
            if(!option)throw Error('Нет выбранного подсумка');select.value=option.value;
            document.querySelector('#pouch-select-modal .pouch-confirm').click();return true})()`);
        await page.until(`document.querySelectorAll('#belt-pouches-container .belt-pouch-name').length===${count}`,'Подсумок установлен на пояс');
        await waitSave();
    }
    const at = (index, selector) => `[data-path="equipment,belt,pouches,${index}"] ${selector}`;
    const readout = index => page.evaluate(`document.querySelector(${JSON.stringify(at(index,'[data-volume-info]'))}).textContent`);
    assert.match(await readout(0), /0 \/ 8$/);
    assert.ok((await readout(1)).endsWith('0 / '+magazine.attributes.capacity));
    const drag = id => page.evaluate(`(()=>{const source=[...document.querySelectorAll('#pockets-container [draggable=true][data-widget-identity]')]
        .find(item=>JSON.parse(item.dataset.widgetIdentity).id===${JSON.stringify(id)});
        const transfer=new DataTransfer();source.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:transfer}));
        document.querySelector('[data-path="equipment,belt,pouches,0"]').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
        return true})()`);
    await drag('qa-medical-volume-six');
    await page.until(`document.querySelector(${JSON.stringify(at(0,'[data-volume-info]'))}).textContent.endsWith('6 / 8')`,'В увеличенный объём помещается предмет');
    await waitSave();
    await drag('qa-medical-volume-three');
    assert.match(await readout(0), /6 \/ 8$/);
    const saved=(await read()).data;
    assert.deepEqual(saved.equipment.belt.pouches[0].contents.map(item=>item.id),['qa-medical-volume-six']);
    assert.ok(saved.inventory.pockets.some(item=>item.id==='qa-medical-volume-three'));
    saved.equipment.belt.protectionDisabled=true;
    await request(url,{data:saved},token,'PUT');
    await open();
    await page.until(`document.querySelector(${JSON.stringify(at(0,'[data-volume-info]'))}).textContent.endsWith('6 / 4')`,'Вместимость отключённого пояса вернулась к базовой');
    await drag('qa-medical-volume-one');
    assert.match(await readout(0), /6 \/ 4$/);
    const extra=offered.global.find(item=>item.name==='Бинт'&&item.volume>0);
    assert.ok(extra);
    await page.evaluate("window.openInventoryTemplatePicker(['equipment','belt','pouches',0,'contents']).then(()=>true)");
    await page.until(`document.querySelector('#inventory-template-picker-modal button[data-item-template-id="${extra.id}"]')`,'Бинт есть в каталоге');
    await page.evaluate(`window.selectInventoryTemplate(${extra.id}).then(()=>true)`);
    assert.match(await readout(0), /6 \/ 4$/);
    await page.evaluate('window.closeInventoryTemplatePicker();window.unequipBelt().then(()=>true)');
    await waitSave();
    const unequipped=(await read()).data.inventory.backpack.find(item=>item.id===belt.id);
    assert.deepEqual(unequipped.pouches[0].contents.map(item=>item.id),['qa-medical-volume-six']);
    assert.equal(unequipped.pouches[1].id,'qa-magazine-pouch');
    await page.evaluate('window.closeCharacterSheet();true');
    console.log(JSON.stringify({medicalPouch:true,capacityDoubled:true,magazineUnchanged:true,
        overflowDenied:true,disabledBeltDeniedNewItems:true,contentsPreserved:true}));
};
