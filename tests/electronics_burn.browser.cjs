// Настоящее попадание в электрическую аномалию с исходным уроном250.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,locationId,safeSession})=>{
    await page.evaluate("document.querySelector('[data-pda-close]')?.click();window.closeCharacterSheet();true");
    const listed=await request('/lobbies/'+lobby.id+'/characters',null,token,'GET');
    const receiver=listed.find(c=>c.name==='Получатель спам-атаки');assert.ok(receiver);
    const url='/lobbies/characters/'+receiver.id,get=()=>request(url,null,token,'GET');
    let fresh=await get();const physical=[{id:'burn-hand',name:'Фонарик'},{id:'burn-head',name:'Налобный фонарик'},
        {id:'burn-dose',name:'Дозиметр'},{id:'burn-detector',name:'ДА-2',category:'detector',attributes:{isActive:true}},
        {id:'burn-nvg',name:'ПНВ',slotType:'nvg',active:true,enabled:true},{id:'burn-radio',name:'Рация',attributes:{isActive:true,type:'radio'}},
        {id:'immune-lamp',name:'Улучшенный фонарик'},{id:'burn-battery',name:'Батарейки',quantity:2}];
    fresh.data.inventory.pockets.push(...physical);fresh.data.equipment={};fresh.data.weapons=[];
    await request(url,{data:fresh.data},token,'PUT');
    const hardware=async(operation,id,extra={})=>{const data=await get(),index=data.data.inventory.pockets.findIndex(i=>i.id===id);
        return request(url+'/portable-lights',{operation,path:['inventory','pockets',index],source_id:id,
            character_revision:data.data._revision,...extra},token);};
    await hardware('prepare','burn-hand',{mount:'hand'});await hardware('toggle','burn-hand');
    await request(loc+'/objects/batch',{objects:[{name:'Аккумулятор',type:'anomaly',tile_x:47,tile_y:48,
        properties:{anomaly_key:'akkumulyator',gm_hidden:false}}]},token);
    const state=await request(loc+'/combat',null,token,'GET'),placed=state.characters.find(c=>c.character_id===receiver.id);
    await request(loc+'/characters/'+receiver.id+'/facing',{facing_x:1,facing_y:0},token,'PATCH');
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/action',{location_character_id:placed.location_character_id,action_key:'convert_free_action_to_movement'},token);
    await page.evaluate(`window.currentLocationCharacterId=${receiver.id};window.currentCharacterId=${receiver.id};window.__burnMoves=[];window.socket.on('character_moved',e=>__burnMoves.push(e));void window.socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${locationId},character_id:${receiver.id},x:47,y:48,floor_level:0,movement_mode:'walk'});true`);
    await page.until(`__burnMoves.some(e=>e.character_id===${receiver.id}&&e.x===47&&e.y===48)`,'Вход в реальную электрическую аномалию');
    assert.equal((await get()).data.health.combatMeta.activeAnomaly.key,'akkumulyator');
    const hit=await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    fresh=await get();const items=fresh.data.inventory.pockets;
    assert.ok(physical.slice(0,6).every(item=>items.find(i=>i.id===item.id).electronicsState?.burned));
    assert.ok(items.find(i=>i.id==='spam-receiver-pda').pdaState.broken);
    assert.equal(items.find(i=>i.id==='immune-lamp').electronicsState,undefined);
    assert.ok(!fresh.data.health.combatMeta.portableLights.find(b=>b.id==='burn-hand').on);
    await request(loc+'/combat/end',{},token);
    await page.evaluate(`window.openCharacterSheet(${receiver.id},'equipment').then(()=>true)`);
    await page.until("[...document.querySelectorAll('.portable-light-row')].some(r=>r.dataset.lightId==='burn-hand'&&r.textContent.includes('сгорел'))",'Причина неработающего фонаря видна в экипировке');
    const controls=await page.evaluate("(()=>{const r=[...document.querySelectorAll('.portable-light-row')].find(r=>r.dataset.lightId==='burn-hand');return {battery:r.textContent.includes('Заменить батарейку'),on:r.querySelector('[data-light-operation=toggle]').disabled,stow:r.querySelector('[data-light-operation=stow]').disabled}})()");
    assert.deepEqual(controls,{battery:false,on:true,stow:false});
    await page.evaluate("[...document.querySelectorAll('.portable-light-row')].find(r=>r.dataset.lightId==='burn-hand').scrollIntoView({block:'center'});true");
    fs.writeFileSync(path.join(safeSession.artifacts,'electronics-burn.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    fs.writeFileSync(path.join(safeSession.artifacts,'electronics-burn.json'),JSON.stringify({scene:'Припять',anomaly:'Аккумулятор',rawDamage:250,
        burnedDevices:6,pdaBroken:true,improvedSurvived:true,activeLightOff:true,visibleReason:true},null,2));
    console.log('Электроника: реальный вход/конец хода в Аккумуляторе250, шесть приборов и КПК сгорели; улучшенный фонарь цел, причина видна.');
};
