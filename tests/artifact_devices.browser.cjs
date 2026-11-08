// Надетый Громоотвод подавляет уже работающие приборы; отказ не расходует ресурс.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,characterId,rules,safeSession})=>{
    const url='/lobbies/characters/'+characterId,get=()=>request(url,null,token,'GET');
    let current=await get();current.data.equipment={};current.data.inventory ||= {};current.data.inventory.pockets=[
        {id:'artifact-lamp',name:'Фонарик',category:'misc',quantity:1},
        {id:'artifact-pda',name:'КПК',category:'misc',quantity:1,pdaState:{on:false,charge_percent:100}},
        {id:'artifact-dose',name:'Дозиметр',category:'device',quantity:1}];
    await request(url,{data:current.data},token,'PUT');
    const operate=async(operation,index,extra={})=>{const fresh=await get();return request(url+'/portable-lights',{
        operation,path:['inventory','pockets',index],source_id:fresh.data.inventory.pockets[index].id,
        character_revision:fresh.data._revision,...extra},token);};
    await operate('prepare',0,{mount:'hand'});await operate('toggle',0);await operate('pda_toggle',1);await operate('dosimeter_toggle',2);
    await page.evaluate("(async()=>{window.artifactDeviceScene=(await import('/static/js/locationScene.js')).sceneGraphicsForTest();return true})()");
    await page.until("artifactDeviceScene.scene.children.some(n=>n.isSpotLight&&n.visible&&n.intensity>0)",'Настоящий источник света появился в сцене');
    const before=await request(loc+'/combat',null,token,'GET');assert.equal(before.characters.find(c=>c.character_id===characterId).portable_lights.length,1);
    current=await get();const rule=rules.find(a=>a.name==='Громоотвод');assert.ok(rule);
    current.data.equipment={armor:{containers:[{item:{id:'electric-block-artifact',category:'artifact',name:rule.name,attributes:rule}}]}};
    await request(url,{data:current.data},token,'PUT');
    await page.until("!artifactDeviceScene.scene.children.some(n=>n.isSpotLight&&n.visible&&n.intensity>0)",'Надевание артефакта убрало источник света по сетевому событию');
    const state=await request(loc+'/combat',null,token,'GET');assert.equal(state.characters.find(c=>c.character_id===characterId).portable_lights.length,0);
    const selection='?item_path='+encodeURIComponent(JSON.stringify(['inventory','pockets',1]))+'&item_id=artifact-pda&location_id='+state.location_id;
    const network=await request(url+'/pda-network'+selection,null,token,'GET');assert.ok(network.source.blocked.includes('Громоотвод'));
    await page.evaluate(`window.openCharacterSheet(${characterId},'equipment').then(()=>true)`);
    await page.until("document.querySelector('[data-light-id=artifact-lamp] strong')?.textContent.includes('подавлен артефактом')",'Подавление видно у фонаря');
    await operate('toggle',0);
    await page.until("document.querySelector('[data-light-id=artifact-lamp] [data-light-operation=toggle]')?.disabled",'Повторное включение недоступно');
    const stopped=await get();
    const refused=await page.evaluate(`(async()=>{const {Server}=await import('/static/js/api.js');try{await Server.operatePortableLight(${characterId},${JSON.stringify({operation:'toggle',path:['inventory','pockets',0],source_id:'artifact-lamp',character_revision:stopped.data._revision})});return{accepted:true}}catch(e){return{accepted:false,message:e.message}}})()`);
    assert.equal(refused.accepted,false);assert.ok(refused.message.includes('Громоотвод'));
    const afterRefusal=await get();assert.deepEqual(afterRefusal.data.inventory,stopped.data.inventory);
    current=afterRefusal;current.data.equipment={};await request(url,{data:current.data},token,'PUT');
    await operate('toggle',0);
    const restored=await request(loc+'/combat',null,token,'GET');assert.equal(restored.characters.find(c=>c.character_id===characterId).portable_lights.length,1);
    await page.until("artifactDeviceScene.scene.children.some(n=>n.isSpotLight&&n.visible&&n.intensity>0)",'Свет вернулся в настоящую сцену после снятия');
    const resumed=await request(url+'/pda-network'+selection,null,token,'GET');assert.equal(resumed.source.blocked,null);
    fs.writeFileSync(path.join(safeSession.artifacts,'artifact-devices.json'),JSON.stringify({artifact:rule.name,existingLightSuppressed:true,
        networkSuppressed:true,sceneLightRemovedAndRestored:true,uiExplains:true,switchOffAllowed:true,rejectedWithoutInventoryCost:true,unequipRestores:true},null,2));
    console.log('Громоотвод: готовый свет и связь подавлены, причина видна, выключение разрешено, отказ не расходует заряд, снятие возвращает работу.');
};
