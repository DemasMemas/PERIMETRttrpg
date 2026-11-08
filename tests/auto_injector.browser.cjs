// Автоинъектор: штатная услуга, физическая ампула и две оплаты в настоящей сцене.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,npc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'catalog');assert.ok(safeSession.verified);
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const armorTemplate=offered.global.find(t=>t.category==='armor'&&t.attributes?.modification_category==='Лёгкая');
    const drugTemplate=offered.global.find(t=>t.name==='Миколий');assert.ok(armorTemplate&&drugTemplate);
    const item=(t,id)=>({id,templateId:t.id,name:t.name,category:t.category,attributes:structuredClone(t.attributes||{}),
        quantity:1,weight:t.weight,volume:t.volume});
    const armor={...item(armorTemplate,'injector-browser-armor'),protection:armorTemplate.attributes.protection};
    const drug={...item(drugTemplate,'injector-browser-dose'),quantity:2};
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель автоинъектора',data:{equipment:{armor},
        health:{painLevel:3},inventory:{money:100000,pockets:[drug],backpack:[]}}},token);
    const placed=await request(loc+'/spawn_character',{character_id:owner.id,tile_x:46,tile_y:48},token);
    await request(loc+'/spawn_character',{character_id:npc.id,tile_x:46,tile_y:49},token);
    const master=await request('/lobbies/characters/'+npc.id,null,token,'GET');
    await request(loc+'/professional-modification',{actor_location_character_id:placed.location_character_id,
        specialist_character_id:npc.id,target_path:['equipment','armor'],item_id:armor.id,rule_id:'armor-a15',execute:true,
        operation_id:'injector-browser-install',actor_revision:owner.data._revision,specialist_revision:master.data._revision},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.loadout-slot[data-equipment-open=armor]')",'Броня с автоинъектором');
    await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=armor]').click();true");
    await page.until("document.querySelector('[data-auto-injector]')?.textContent.includes('5 ОД')",'Загрузка за пять ОД');
    const load=async()=>{
        await page.evaluate("window.loadAutoInjector();true");
        await page.until("document.querySelector('#consumable-application-modal .consumable-application-list button')",'Выбор ампулы');
        await page.evaluate("document.querySelector('#consumable-application-modal .consumable-application-list button').click();true");
        await page.until("document.querySelector('[data-auto-injector]')?.textContent.includes('Миколий')",'Ампула загружена');
    };
    const before=await request(loc+'/combat',null,token,'GET');await load();
    const loaded=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    assert.equal(loaded.data.inventory.pockets[0].quantity,1);
    assert.notEqual(loaded.data.equipment.armor.installedModules[0].id,drug.id);
    for(const [width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.until("(()=>{const root=document.getElementById('sheet-tab-equipment').getBoundingClientRect(),bar=document.querySelector('[data-auto-injector]').getBoundingClientRect();return bar.y>=root.y&&bar.bottom<=root.bottom&&bar.right<=root.right})()",'Загруженный автоинъектор виден '+width);
        if(width===1280)fs.writeFileSync(path.join(safeSession.artifacts,'auto-injector-loaded-1280x720.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    const use=async(payment)=>{
        await page.evaluate(`window.useAutoInjector(${JSON.stringify(payment)});true`);
        await page.until("document.querySelector('[data-auto-injector]')?.textContent.includes('Пусто')||document.querySelector('#consumable-application-modal .consumable-application-list button')",'Применение или выбор цели медикамента');
        // Для Миколия обычная система может предложить применение к себе или кровотечению.
        const choice=await page.evaluate("document.querySelector('#consumable-application-modal .consumable-application-list button')?.textContent||''");
        if(choice)await page.evaluate("document.querySelector('#consumable-application-modal .consumable-application-list button').click();true");
        await page.until("document.querySelector('[data-auto-injector]')?.textContent.includes('Пусто')",'Препарат применён');
    };
    await use('free');
    const afterFree=await request(loc+'/combat',null,token,'GET');
    assert.equal(afterFree.current_character.action_points_current,before.current_character.action_points_current-5);
    assert.equal(afterFree.current_character.free_actions_current,before.current_character.free_actions_current-1);
    const one=await request('/lobbies/characters/'+owner.id,null,token,'GET');assert.equal(one.data.health.painLevel,2);
    await request(loc+'/combat/end',{},token);await load();
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await page.until("document.querySelector('[data-auto-injector]')?.textContent.includes('1 ОД')",'Оплата ОД');
    const second=await request(loc+'/combat',null,token,'GET');await use('action');
    const afterAction=await request(loc+'/combat',null,token,'GET');
    assert.equal(afterAction.current_character.action_points_current,second.current_character.action_points_current-1);
    const two=await request('/lobbies/characters/'+owner.id,null,token,'GET');assert.equal(two.data.health.painLevel,1);
    assert.ok(!two.data.inventory.pockets.some(i=>i.id===drug.id));
    for(const [width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.until("(()=>{const root=document.getElementById('sheet-tab-equipment').getBoundingClientRect(),bar=document.querySelector('[data-auto-injector]').getBoundingClientRect();return bar.y>=root.y&&bar.bottom<=root.bottom&&bar.right<=root.right})()",'Автоинъектор виден '+width);
        if(width===1280)fs.writeFileSync(path.join(safeSession.artifacts,'auto-injector-1280x720.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    await request(loc+'/combat/end',{},token);
    console.log('Автоинъектор: штатная установка, загрузка 5 ОД, две ампулы из стопки, применение за СД и ОД, реальные эффекты и четыре настольных размера.');
};
