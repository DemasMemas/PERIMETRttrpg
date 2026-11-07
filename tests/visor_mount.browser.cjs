// Специальная модификация существующего пользовательского забрала, обычная сцена Припяти.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'catalog');assert.ok(safeSession.verified);
    await page.evaluate('window.closeCharacterSheet();true');
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const item=(template,id)=>({id,templateId:template.id,name:template.name,category:template.category,
        attributes:structuredClone(template.attributes||{}),quantity:1,weight:template.weight,volume:template.volume});
    const helmet=item(offered.global.find(t=>t.category==='helmet'&&!t.attributes?.soft_headwear),'visor-test-helmet');
    helmet.attributes.slots=[{type:'visor',label:'Забрало'}];helmet.installedModules=[{
        id:'visor-test',name:'Пользовательское забрало',category:'helmet_module',slotType:'visor',itemClass:'1',quantity:1,installedModules:[]}];
    const nvg=item(offered.global.find(t=>t.attributes?.slot_type==='nvg'),'visor-test-nvg');
    const light=item(offered.global.find(t=>t.name==='Фонарик'&&t.category==='misc'),'visor-test-light');
    const actor=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверка креплений забрала',data:{equipment:{helmet},
        inventory:{money:20000,pockets:[nvg,light],backpack:[]}}},token);
    const placed=await request(loc+'/spawn_character',{character_id:actor.id,tile_x:43,tile_y:48},token);
    const npc=await request(`/lobbies/${lobby.id}/characters`,{name:'Мастер креплений',data:{is_npc:true,
        merchant:{modification_enabled:true,workbenches:['Станок Бронника']},inventory:{money:0}}},token);
    await request(`/lobbies/${lobby.id}/characters/${npc.id}/merchant`,{character_revision:npc.data._revision,
        enabled:false,modification_enabled:true,workbenches:['Станок Бронника'],prices:{}},token,'PATCH');
    await request(loc+'/spawn_character',{character_id:npc.id,tile_x:43,tile_y:49},token);
    const payload={actor_location_character_id:placed.location_character_id,specialist_character_id:npc.id,
        target_path:['equipment','helmet','installedModules',0],item_id:'visor-test',rule_id:'armor-g18'};
    const quoted=await request(loc+'/professional-modification',payload,token);assert.equal(quoted.price,5000);
    const currentNpc=await request('/lobbies/characters/'+npc.id,null,token,'GET');
    const currentActor=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    await request(loc+'/professional-modification',{...payload,execute:true,operation_id:'visor-install',
        actor_revision:currentActor.data._revision,specialist_revision:currentNpc.data._revision},token);
    await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.loadout-slot[data-equipment-open=helmet]')",'Шлем в листе');
    await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=helmet]').click();true");
    for(const [slot,name]of [['nvg',nvg.name],['light',light.name]]){
        await page.evaluate(`window.installModuleFromSlot(${JSON.stringify(JSON.stringify(payload.target_path))},${JSON.stringify(slot)});true`);
        await page.until("document.querySelector('#universal-module-select-modal option')",'Выбор прибора на забрало');
        assert.ok(await page.evaluate(`document.querySelector('#universal-module-select-modal').textContent.includes(${JSON.stringify(name)})`));
        await page.evaluate("document.querySelector('#universal-module-select').selectedIndex=0;document.querySelector('#confirm-universal-module').click();true");
        await page.until(`document.querySelector('[data-widget-slots="equipment.helmet.installedModules.0"]')?.textContent.includes(${JSON.stringify(name)})`,'Прибор установлен');
    }
    await page.evaluate(`(async()=>{const {Server}=await import('/static/js/api.js');await Server.waitForCharacterSave(${actor.id});return true})()`);
    const saved=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    const modules=saved.data.equipment.helmet.installedModules[0].installedModules;
    assert.equal(modules.length,2);assert.deepEqual(modules.map(m=>m.id).sort(),[nvg.id,light.id].sort());
    await page.until("[...document.querySelectorAll('.portable-light-row')].some(row=>row.textContent.includes('Фонарик')&&row.textContent.includes('Надеть'))",'Подготовка света на голове');
    await page.evaluate("[...document.querySelectorAll('.portable-light-row')].find(row=>row.textContent.includes('Фонарик')).querySelector('[data-light-operation=prepare]').click();true");
    await page.until("[...document.querySelectorAll('.portable-light-row')].some(row=>row.textContent.includes('Фонарик')&&row.textContent.includes('на голове'))",'Свет закреплён на забрале');
    await page.evaluate("[...document.querySelectorAll('.portable-light-row')].find(row=>row.textContent.includes('Фонарик')).querySelector('[data-light-operation=toggle]').click();true");
    await page.until("[...document.querySelectorAll('.portable-light-row')].some(row=>row.textContent.includes('Фонарик')&&row.textContent.includes('включён'))",'Фонарь включён');
    const status=await request('/lobbies/characters/'+actor.id+'/portable-lights',null,token,'GET');
    assert.equal(status.lights.find(l=>l.id===light.id).binding.mount,'head');
    fs.writeFileSync(path.join(safeSession.artifacts,'visor-mount.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    console.log('Забрало: настоящая платная установка g18, два прибора через существующий выбор модулей, сохранённые физические ID, ПНВ и фонарь на голове, включение света. Припять/GPU.');
};
