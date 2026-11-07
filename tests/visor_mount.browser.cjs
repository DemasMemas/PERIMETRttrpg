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
        id:'visor-test',name:'Пользовательское забрало',category:'helmet_module',slotType:'visor',itemClass:'1',quantity:1,
        durability:100,maxDurability:100,installedModules:[]}];
    const nvg=item(offered.global.find(t=>t.attributes?.slot_type==='nvg'),'visor-test-nvg');
    const light=item(offered.global.find(t=>t.name==='Фонарик'&&t.category==='misc'),'visor-test-light');
    const actor=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверка креплений забрала',data:{equipment:{helmet},
        inventory:{money:20000,pockets:[nvg,light],backpack:[]}}},token);
    const placed=await request(loc+'/spawn_character',{character_id:actor.id,tile_x:43,tile_y:48},token);
    const npc=await request(`/lobbies/${lobby.id}/characters`,{name:'Мастер креплений',data:{is_npc:true,
        merchant:{modification_enabled:true,workbenches:['Станок Бронника']},inventory:{money:0}}},token);
    await request(`/lobbies/${lobby.id}/characters/${npc.id}/merchant`,{character_revision:npc.data._revision,
        enabled:false,repair_enabled:true,modification_enabled:true,workbenches:['Станок Бронника'],prices:{}},token,'PATCH');
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
    const service=async(details,id)=>{
        const owner=await request('/lobbies/characters/'+actor.id,null,token,'GET');
        const master=await request('/lobbies/characters/'+npc.id,null,token,'GET');
        return request(loc+'/professional-modification',{...details,execute:true,operation_id:id,
            actor_revision:owner.data._revision,specialist_revision:master.data._revision},token);
    };
    const unlock={...payload,action:'unlock_slot'};delete unlock.rule_id;
    await service(unlock,'visor-extra-slot');await service({...payload,rule_id:'armor-g21'},'visor-stop-layer');
    await request(loc+'/characters/'+actor.id+'/facing',{facing_x:-1,facing_y:0},token,'PATCH');
    const shooter=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверочный стрелок',data:{
        skills:{physical:{shooting:{base:100},strength:{base:20}}},weapons:[{name:'Проверочная винтовка',
            range:30,ergonomics:50,damage:100,ammo:20,durability:100,maxDurability:100}]}},token);
    const gunner=await request(loc+'/spawn_character',{character_id:shooter.id,tile_x:42,tile_y:48},token);
    await request(loc+'/characters/'+shooter.id+'/facing',{facing_x:1,facing_y:0},token,'PATCH');
    await request(loc+'/combat/start',{location_character_ids:[gunner.location_character_id,placed.location_character_id],
        initiator_location_character_id:gunner.location_character_id,initiator_first:true},token);
    const command={location_character_id:gunner.location_character_id,weapon_index:0,target_character_id:actor.id};
    await request(loc+'/combat/action',{...command,action_key:'draw_weapon'},token);
    await request(loc+'/combat/end_turn',{location_character_id:gunner.location_character_id},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    await request(loc+'/combat/action',{...command,action_key:'aim'},token);
    const combat=await request(loc+'/combat',null,token,'GET');
    const shotCost=combat.current_character.weapon_ergonomics.find(profile=>profile.weapon_index===0).aimed_shot_action_points;
    const shot=await request(loc+'/combat/action',{...command,action_key:'attack',fire_mode:'aimed',target_zone:'head',
        shot_count:1,volley_count:1,action_points:shotCost},token);
    assert.ok(shot.attack.results[0].stop_layer);assert.equal(shot.attack.results[0].damage,0);
    await page.until("document.querySelector('[data-widget-slots="+JSON.stringify('equipment.helmet')+"]')?.textContent.includes('разрушено Стоп-слоем')",'Забрало разрушено по сетевому обновлению');
    const after=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    assert.ok(after.data.equipment.helmet.installedModules[0].stopLayerBroken);
    assert.equal(after.data.equipment.helmet.installedModules[0].installedModules.length,2);
    fs.writeFileSync(path.join(safeSession.artifacts,'visor-stop-layer.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    for(const [width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.until("(()=>{const root=document.getElementById('sheet-tab-equipment').getBoundingClientRect();const items=[...document.querySelectorAll('.equipment-loadout .loadout-slot,.equipment-loadout .portable-light-row')];return items.length===20&&items.every(item=>{const b=item.getBoundingClientRect();return b.y>=root.y&&b.bottom<=Math.min(innerHeight,root.bottom)+1&&b.right<=innerWidth})})()",'Все слоты и два прибора видимы '+width+'×'+height);
        if(width===1280)fs.writeFileSync(path.join(safeSession.artifacts,'visor-stop-layer-1280x720.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    console.log('Стоп-слой: слот куплен у механика, g21 установлен, настоящее попадание в лицо поглощено, разрушение пришло в лист по сети, вложенные приборы сохранены.');
};
