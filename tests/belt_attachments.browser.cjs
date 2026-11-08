// Четыре физических инструмента, штатная установка и сохранение при снятии пояса.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,npc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'equipment');assert.ok(safeSession.verified);
    const evidence=require('./helpers/playtest_registry.cjs').evidenceWriter(safeSession,'belt-tools');
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const make=(t,id)=>({id,templateId:t.id,name:t.name,category:t.category,attributes:structuredClone(t.attributes||{}),quantity:1,weight:t.weight,volume:t.volume});
    const belt=make(offered.global.find(t=>t.category==='belt'),'tool-browser-belt');
    const tool={...make(offered.global.find(t=>t.category==='tool'&&t.name==='Ножницы'),'tool-browser-stack'),quantity:5,uses:17,maxUses:50};
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель креплений инструментов',data:{equipment:{belt},
        inventory:{money:100000,pockets:[tool],backpack:[]}}},token);
    const placed=await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:49},token);
    const master=await request('/lobbies/characters/'+npc.id,null,token,'GET');
    await request(loc+'/professional-modification',{actor_location_character_id:placed.location_character_id,specialist_character_id:npc.id,
        target_path:['equipment','belt'],item_id:belt.id,rule_id:'armor-g61',execute:true,operation_id:'belt-tool-install',
        actor_revision:owner.data._revision,specialist_revision:master.data._revision},token);
    const open=()=>page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
    const saved=()=>request('/lobbies/characters/'+owner.id,null,token,'GET');
    await open();await page.until("document.querySelector('[data-belt-tools]')",'Крепления в инвентаре');
    const weight=()=>page.evaluate("Number(document.getElementById('total-weight-display').textContent)");
    const beforeWeight=await weight();
    const mount=async count=>{
        await page.evaluate('window.mountBeltTool();true');
        await page.until("document.querySelector('#consumable-application-modal .consumable-application-list button')",'Выбор инструмента');
        await page.evaluate("document.querySelector('#consumable-application-modal .consumable-application-list button').click();true");
        await page.until(`document.querySelector('[data-belt-tools]')?.textContent.includes('Инструменты (${count} / 4)')`,'Закреплён инструмент '+count);
    };
    await mount(1);await mount(2);await mount(3);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await open();const before=await request(loc+'/combat',null,token,'GET');await mount(4);
    const after=await request(loc+'/combat',null,token,'GET');
    assert.equal(after.current_character.action_points_current,before.current_character.action_points_current-3);
    let snapshot=await saved(),tools=snapshot.data.equipment.belt.installedModules.filter(i=>i.slotType==='belt_tool');
    assert.equal(tools.length,4);assert.equal(new Set(tools.map(i=>i.id)).size,4);
    assert.ok(tools.every(i=>i.uses===17&&i.quantity===1));assert.equal(snapshot.data.inventory.pockets[0].quantity,1);
    assert.equal(await weight(),Math.round((beforeWeight-4*tool.weight)*10)/10);
    evidence.step('Четыре инструмента из стопки, последнее крепление в бою',
        {tool:tool.name,quantity:5,uses:17,weight:beforeWeight,actionPoints:before.current_character.action_points_current},
        {mounted:tools.map(i=>({id:i.id,name:i.name,quantity:i.quantity,uses:i.uses})),remaining:snapshot.data.inventory.pockets[0].quantity,
            weight:await weight(),actionPoints:after.current_character.action_points_current});
    assert.equal(await page.evaluate("document.querySelector('[data-belt-tools] button[onclick=\"mountBeltTool()\"]').disabled"),true);
    const crafting=await request('/lobbies/characters/'+owner.id+'/crafting',null,token,'GET');
    assert.equal(crafting.inventory.filter(i=>i.path?.[2]==='installedModules'&&i.item?.slotType==='belt_tool').length,4);
    await request(loc+'/combat/end',{},token);await open();
    for(const[width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.evaluate("document.querySelector('[data-belt-tools]').scrollIntoView({block:'center'});true");
        await page.until("(()=>{const r=document.querySelector('[data-belt-tools]').getBoundingClientRect();return r.x>=0&&r.right<=innerWidth&&r.y>=0&&r.bottom<=innerHeight})()",'Четыре инструмента доступны '+width);
        if(width===1280)fs.writeFileSync(path.join(safeSession.artifacts,'belt-tools-1280x720.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    await page.evaluate('window.unequipBelt().then(()=>true)');
    await page.evaluate(`(async()=>{const {Server}=await import('/static/js/api.js');await Server.waitForCharacterSave(${owner.id});return true})()`);
    snapshot=await saved();const stored=snapshot.data.inventory.backpack.find(i=>i.id===belt.id);
    assert.equal(stored.installedModules.filter(i=>i.slotType==='belt_tool').length,4);
    evidence.step('Снятие целого пояса', {equippedBelt:belt.id,toolIds:tools.map(i=>i.id)},
        {storedBelt:stored.id,toolIds:stored.installedModules.filter(i=>i.slotType==='belt_tool').map(i=>i.id)});
    await page.evaluate('window.equipBeltFromInventory(["inventory","backpack",0]).then(()=>true)');
    await page.until("document.querySelector('[data-belt-tools]')?.textContent.includes('Инструменты (4 / 4)')",'Инструменты вернулись вместе с поясом');
    await page.evaluate('window.unmountBeltTool(0);true');
    await page.until("document.querySelector('[data-belt-tools]')?.textContent.includes('Инструменты (3 / 4)')",'Инструмент снят');
    snapshot=await saved();assert.equal(snapshot.data.inventory.pockets.filter(i=>i.name===tool.name).reduce((sum,i)=>sum+i.quantity,0),2);
    assert.ok(snapshot.data.inventory.pockets.some(i=>tools.some(t=>t.id===i.id)&&i.uses===17));
    evidence.step('Повторное надевание пояса и снятие одного инструмента',
        {mounted:4,pocketQuantity:1},
        {mounted:snapshot.data.equipment.belt.installedModules.filter(i=>i.slotType==='belt_tool').length,
            pockets:snapshot.data.inventory.pockets.map(i=>({id:i.id,name:i.name,quantity:i.quantity,uses:i.uses}))});
    evidence.publish({images:['belt-tools-1280x720.png'],controls:{random:'Бросков в этом сценарии нет',time:'Игровое время не продвигается'},
        limitations:['В браузере проверены ножницы; остальные инструменты — профильными серверными тестами.',
            'Медицинское применение и ремонт здесь не выполняются. Отдельный клиент игрока в этом сценарии не участвует.']});
    console.log('Крепления инструментов: платная установка g61, четыре места, заряды и ID, нулевая масса на поясе, 3 ОД в бою, доступ из создания, снятие/надевание всего пояса, четыре настольных размера.');
};
