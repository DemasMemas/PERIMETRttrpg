// Мини-осциллограф после штатной установки на копии Припяти, без приборов в руках.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,characterId,fixture,safeSession})=>{
    const api='/lobbies/'+lobby.id,url='/lobbies/characters/'+characterId;
    const get=()=>request(url,null,token,'GET');
    await page.evaluate("document.querySelector('[data-pda-close]')?.click();window.closeCharacterSheet();true");
    let fresh=await get();const pockets=fresh.data.inventory.pockets;
    pockets.push({id:'scope-material',name:'Электронные материалы',category:'crafting_material',quantity:1},
        {id:'scope-tool',name:'Паяльные инструменты',category:'tool',quantity:1,uses:2,maxUses:50},
        {id:'scope-dose',name:'Дозиметр',category:'device',quantity:1,attributes:{device_kind:'dosimeter'}},
        {id:'scope-detector',name:'Детектор ДА',category:'detector',quantity:1,bonus:3,
            attributes:{isActive:true,avoidance_chance:25},installedModules:[{slotType:'battery',attributes:{power:50}}]});
    await request(url,{data:fresh.data},token,'PUT');fresh=await get();
    const pdaIndex=fresh.data.inventory.pockets.findIndex(i=>i.id==='artifact-pda');assert.ok(pdaIndex>=0);
    const selected={path:['inventory','pockets',pdaIndex],source_id:'artifact-pda'};
    await request(url+'/portable-lights',{operation:'pda_stow',...selected,character_revision:fresh.data._revision},token);
    fresh=await get();const tool=fresh.data.inventory.pockets.findIndex(i=>i.id==='scope-tool');
    const job=await request(url+'/crafting',{operation:'install_pda_modification',operation_id:'scope-install',
        character_revision:fresh.data._revision,target_path:selected.path,item_id:selected.source_id,rule_id:'pda-b6',tool_path:['inventory','pockets',tool]},token);
    const clock=await request(api,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+60;
    await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    fresh=await get();const finished=await request(url+'/crafting',{operation:'finish',operation_id:'scope-finish',
        character_revision:fresh.data._revision,job_id:job.result.id,chosen_loss:[]},token);assert.ok(finished.result.success);
    const tiles=structuredClone(fixture.tiles_data);tiles[48][42].radiation=7;
    await request(loc,{tiles_data:tiles},token,'PUT');
    fresh=await get();const doseIndex=fresh.data.inventory.pockets.findIndex(i=>i.id==='scope-dose');
    await request(url+'/portable-lights',{operation:'dosimeter_toggle',path:['inventory','pockets',doseIndex],source_id:'scope-dose',character_revision:fresh.data._revision},token);
    fresh=await get();assert.equal(fresh.data.health?.combatMeta?.heldPda,undefined);
    await page.evaluate(`window.currentLocationCharacterId=${characterId};window.currentCharacterId=${characterId};window.openCharacterSheet(${characterId},'inventory').then(()=>true)`);
    await page.until("Boolean([...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id==='artifact-pda'))",'Инвентарь для мини-осциллографа');
    await page.evaluate("[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id==='artifact-pda').querySelector('[data-widget-key=use]').click();true");
    await page.until("[...document.querySelectorAll('.pda-shell-nav button')].some(b=>b.textContent==='Показания приборов')",'Мини-осциллограф установлен');
    await page.evaluate("[...document.querySelectorAll('.pda-shell-nav button')].find(b=>b.textContent==='Показания приборов').click();true");
    await page.until("document.querySelector('[data-radio-instruments]')?.innerText.includes('Радиация: 7')&&document.querySelector('[data-radio-instruments]')?.innerText.includes('Бонус при встрече с аномалией: 3')",'Два активных прибора без рук');
    const visible=await page.evaluate("(()=>{const r=document.querySelector('[data-radio-instruments]').getBoundingClientRect(),p=document.querySelector('.pda-shell-main').getBoundingClientRect();return r.width>0&&r.height>0&&r.top>=p.top&&r.bottom<=p.bottom})()");assert.ok(visible);
    fs.writeFileSync(path.join(safeSession.artifacts,'pda-instruments.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    fresh=await get();await request(url+'/portable-lights',{operation:'dosimeter_toggle',path:['inventory','pockets',doseIndex],source_id:'scope-dose',character_revision:fresh.data._revision},token);
    await page.until("document.querySelectorAll('[data-radio-instruments] article').length===2&&document.querySelector('[data-radio-instruments]')?.innerText.includes('Детектор ДА')",'Выключенный дозиметр исчезает, прежний активный остаётся');
    assert.equal((await get()).data.inventory.pockets.find(i=>i.id==='scope-dose').deviceState.on,false);
    fs.writeFileSync(path.join(safeSession.artifacts,'pda-instruments.json'),JSON.stringify({scene:'Припять',installedViaCrafting:'pda-b6',radiation:7,detectorBonus:3,avoidanceChance:25,noHands:true,powerOffUpdates:true},null,2));
    console.log('Мини-осциллограф: настоящая установка, дозиметр и детектор без рук, радиация7 и отключение прибора.');
};
