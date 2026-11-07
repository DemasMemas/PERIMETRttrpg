// Электроника в Припяти: отдельная БД, настоящий интерфейс и игровой ход времени.
const assert=require('node:assert/strict');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'electronics');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const actor=await request(api+'/characters',{name:'Проверка электроники',data:{
        features:{specialTraits:[{name:'Программист'}]},skills:{other:{engineering:{base:100}}},inventory:{pockets:[{
        id:'dynamo-device',name:'КПК',category:'misc',quantity:1,pdaState:{on:false,charge_percent:50}},
        {name:'Электронные материалы',category:'crafting_material',quantity:1},
        {name:'Паяльные инструменты 50/50',category:'tool',quantity:1,uses:2,maxUses:50}],backpack:[]},health:{current:700,max:700,effects:[]}}},token);
    const craft='/lobbies/characters/'+actor.id+'/crafting';
    const started=await request(craft,{operation:'install_pda_modification',operation_id:'dynamo-install',character_revision:actor.data._revision,
        target_path:['inventory','pockets',0],item_id:'dynamo-device',rule_id:'pda-b7',tool_path:['inventory','pockets',2]},token);
    const installClock=await request(api,null,token,'GET'),ready=installClock.game_day*1440+installClock.game_time_minutes+60;
    await request(api+'/time',{game_day:Math.floor(ready/1440),game_time_minutes:ready%1440},token,'PATCH');
    const beforeFinish=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    const installed=await request(craft,{operation:'finish',operation_id:'dynamo-finish',character_revision:beforeFinish.data._revision,
        job_id:started.result.id,chosen_loss:[]},token);assert.ok(installed.result.success);
    const spawned=await request(loc+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48},token);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Электроника в Припяти'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
    await page.evaluate(`window.currentLocationCharacterId=${actor.id};window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
    await page.until("document.querySelector('[data-widget-key=use]')",'Использование КПК');
    await page.evaluate("document.querySelector('[data-widget-key=use]').click();true");
    await page.until("document.querySelector('[data-pda-dynamo]')?.textContent.includes('Крутить')",'Кнопка динамо доступна при выключенном экране');
    const layout=await page.evaluate("(()=>{const b=document.querySelector('[data-pda-dynamo]').getBoundingClientRect(),p=document.querySelector('.pda-shell').getBoundingClientRect();return b.x>=p.x&&b.right<=p.right&&b.bottom<=p.bottom&&b.height>0})()");assert.ok(layout);
    await page.evaluate("document.querySelector('[data-pda-dynamo]').click();true");
    await page.until("document.querySelector('[data-pda-dynamo]')?.textContent.includes('Прекратить')",'Зарядка запущена');
    const clock=await request(api,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+1;
    await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    await page.until("document.querySelector('[data-pda-charge]')?.textContent.includes('51.0%')",'Заряд обновлён по сетевому событию игрового времени');
    await request(loc+'/combat/start',{location_character_ids:[spawned.location_character_id],initiator_location_character_id:spawned.location_character_id,initiator_first:true},token);
    const fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');assert.ok(!fresh.data.health.combatMeta?.pdaDynamo);
    await request(loc+'/combat/end',{},token);
    console.log('Динамо-машина: кнопка при выключенном экране, начало зарядки, +1% за минуту и остановка при начале боя проверены в Припяти.');
    await request('/auth/register',{username:'dosimeter-player',email:'dosimeter@example.test',password:'synthetic-device-test'},null);
    const playerLogin=await request('/auth/login',{username:'dosimeter-player',password:'synthetic-device-test'},null),playerToken=playerLogin.access_token;
    await request(api+'/join',{},playerToken);
    const dose=await request(api+'/templates',{name:'Дозиметр',category:'device',price:500,weight:.25,volume:.5,
        attributes:{device_kind:'dosimeter',battery_duration_seconds:604800}},token);
    const player=await request(api+'/characters',{name:'Игрок с дозиметром',data:{inventory:{pockets:[{
        id:'dose-one',name:'Дозиметр',category:'device',templateId:dose.id+1000000,quantity:1,attributes:dose.attributes},
        {id:'dose-battery',name:'Батарейки',category:'device',quantity:1}],backpack:[]},health:{current:700,max:700,effects:[]}}},playerToken);
    await request(loc+'/spawn_character',{character_id:player.id,tile_x:43,tile_y:48},token);
    assert.equal((await request(api+'/radiation-access?character_id='+player.id,null,playerToken,'GET')).can_read,false);
    const doseEpoch=await page.evaluate('performance.timeOrigin');
    await page.evaluate(`localStorage.setItem('access_token',${JSON.stringify(playerToken)});localStorage.setItem('user_id',${JSON.stringify(String(playerLogin.user_id))});localStorage.setItem('currentCharacterId','${player.id}');localStorage.setItem('currentLocationCharacterId','${player.id}');true`);
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:doseEpoch,label:'Игрок с дозиметром'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена игрока');
    assert.equal(await page.evaluate('window.isGM'),false);
    await page.evaluate(`window.openCharacterSheet(${player.id},'inventory').then(()=>true)`);
    await page.until("document.querySelector('[data-widget-key=use]')",'Дозиметр доступен игроку');
    await page.evaluate("document.querySelector('[data-widget-key=use]').click();true");
    await page.until("document.getElementById('dosimeter-modal')?.innerText.includes('Выключен')",'Окно дозиметра');
    await page.evaluate("[...document.querySelectorAll('#dosimeter-modal button')].find(b=>b.textContent==='Включить').click();true");
    await page.until("document.getElementById('dosimeter-modal')?.innerText.includes('Включён')",'Дозиметр включён');
    assert.equal((await request(api+'/radiation-access?character_id='+player.id,null,playerToken,'GET')).can_read,true);
    await page.evaluate("(async()=>{window.R=await import('/static/js/radiationReadings.js');window.__firstDoseReading=R.radiationTooltipLine(7)})()");
    await page.until("R.radiationTooltipLine(7).includes('Радиация: 7')",'Показания радиации разрешены для игрока');
    await page.evaluate("[...document.querySelectorAll('#dosimeter-modal button')].find(b=>b.textContent==='Выключить').click();true");
    await page.until("document.getElementById('dosimeter-modal')?.innerText.includes('Выключен')",'Дозиметр выключен');
    assert.equal(await page.evaluate("R.radiationTooltipLine(7)"),'');
    await page.evaluate("[...document.querySelectorAll('#dosimeter-modal button')].find(b=>b.textContent==='Заменить батарейку').click();true");
    await page.until("!document.querySelector('#dosimeter-modal select')",'Израсходована одна батарейка');
    console.log('Дозиметр: игрок включает и выключает прибор, получает и теряет показания радиации, замена расходует одну батарейку.');
};
