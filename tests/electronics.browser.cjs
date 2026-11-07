// Электроника в Припяти: отдельная БД, настоящий интерфейс и игровой ход времени.
const assert=require('node:assert/strict');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
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
    await page.evaluate("[...document.querySelectorAll('#dosimeter-modal button')].find(b=>b.textContent==='Закрыть').click();true");
    const playerFresh=await request('/lobbies/characters/'+player.id,null,token,'GET');
    playerFresh.data.inventory.pockets.push({id:'radio-player',name:'КПК',category:'misc',quantity:1});
    await request('/lobbies/characters/'+player.id,{data:playerFresh.data},token,'PUT');
    const actorFresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    await request('/lobbies/characters/'+actor.id+'/portable-lights',{operation:'pda_toggle',path:['inventory','pockets',0],source_id:'dynamo-device',character_revision:actorFresh.data._revision},token);
    for(const [id,auth,login]of [[actor.id,token,'radio-actor'],[player.id,playerToken,'radio-player']])
        await request('/lobbies/characters/'+id+'/pda-account/register',{login,password:'synthetic-radio-password'},auth);
    let protocol,context,peer;
    try{
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();protocol=await connect(version.webSocketDebuggerUrl);
        context=await protocol.call('Target.createBrowserContext');const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();peer=await connect(tabs.find(t=>t.id===target.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');await waitForDocument(peer,{href:base,label:'Второй КПК'});
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(token)});localStorage.setItem('user_id','${lobby.gm_id}');true`);
        await peer.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
        const peerEpoch=await peer.evaluate('performance.timeOrigin');await peer.call('Page.navigate',{url:href});
        await waitForDocument(peer,{href,afterEpoch:peerEpoch,label:'ГМ со вторым КПК'});
        await peer.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Вторая сцена');
        const open=async(client,id,device)=>{
            await client.evaluate(`window.currentLocationCharacterId=${id};window.openCharacterSheet(${id},'inventory').then(()=>true)`);
            await client.until(`Boolean([...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(device)}))`,'Физический КПК в инвентаре');
            await client.evaluate(`(()=>{const row=[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(device)});row.querySelector('[data-widget-key=use]').click()})()`);
            await client.until("document.getElementById('pda-shell-modal')",'Открыт КПК');
            await client.evaluate("[...document.querySelectorAll('#pda-shell-modal nav button')].find(b=>b.textContent==='Общий чат').click();true");
            await client.until("document.querySelector('[data-radio-status]')?.textContent.includes('Дальность передачи: 300')",'Сеть КПК готова');
        };
        await open(page,player.id,'radio-player');await open(peer,actor.id,'dynamo-device');
        await page.evaluate("(()=>{const field=document.querySelector('[aria-label=\"Сообщение КПК\"]');field.value='Сигнал игрока';field.dispatchEvent(new Event('input'));const form=document.querySelector('[data-radio-send]');form.requestSubmit();form.requestSubmit()})()");
        await peer.until("document.querySelector('[data-radio-messages]')?.textContent.includes('Сигнал игрока')",'Второй КПК получил сообщение по сети');
        assert.equal(await peer.evaluate("[...document.querySelectorAll('.pda-radio-message')].filter(e=>e.textContent.includes('Сигнал игрока')).length"),1);
        await peer.evaluate("(()=>{const field=document.querySelector('[aria-label=\"Сообщение КПК\"]');field.value='Ответ ГМа';field.dispatchEvent(new Event('input'));document.querySelector('[data-radio-send]').requestSubmit()})()");
        await page.until("document.querySelector('[data-radio-messages]')?.textContent.includes('Ответ ГМа')",'Игрок получил ответ');
        await page.evaluate("[...document.querySelectorAll('#pda-shell-modal nav button')].find(b=>b.textContent==='Контакты').click();true");
        await page.until("document.querySelector('[data-radio-signals]')?.textContent.includes('radio-actor')",'КПК обнаружен как сигнал');
        await page.evaluate("(()=>{const input=document.querySelector('[aria-label=\"Логин контакта КПК\"]');input.value='radio-actor';document.querySelector('[data-radio-add]').requestSubmit()})()");
        await page.until("document.querySelector('[data-radio-saved] input')?.checked",'Друг сохранён в аккаунте');
        const bounds=await peer.evaluate("(()=>{const panel=document.querySelector('.pda-shell').getBoundingClientRect(),button=document.querySelector('[data-radio-send] button').getBoundingClientRect();return button.width>0&&button.x>=panel.x&&button.right<=panel.right&&button.bottom<=panel.bottom})()");assert.ok(bounds,'На 1920×1080 кнопка отправки находится внутри КПК');
        console.log('Сеть КПК: обычный игрок и ГМ обмениваются сообщениями в двух клиентах, двойной submit не дублирует сообщение, друг сохраняется; интерфейс проверен на 1920×1080.');
        let source=await request('/lobbies/characters/'+actor.id,null,token,'GET');
        source.data.inventory.pockets.push({id:'b8-materials',name:'Электронные материалы',category:'crafting_material',quantity:2});
        await request('/lobbies/characters/'+actor.id,{data:source.data},token,'PUT');
        source=await request('/lobbies/characters/'+actor.id,null,token,'GET');
        const solder=source.data.inventory.pockets.findIndex(item=>item.name.includes('Паяльные инструменты'));
        const job=await request(craft,{operation:'install_pda_modification',operation_id:'b8-install',character_revision:source.data._revision,
            target_path:['inventory','pockets',0],item_id:'dynamo-device',rule_id:'pda-b8',tool_path:['inventory','pockets',solder]},token);
        const nextClock=await request(api,null,token,'GET'),future=nextClock.game_day*1440+nextClock.game_time_minutes+60;
        await request(api+'/time',{game_day:Math.floor(future/1440),game_time_minutes:future%1440},token,'PATCH');
        source=await request('/lobbies/characters/'+actor.id,null,token,'GET');
        const finish=await request(craft,{operation:'finish',operation_id:'b8-finish',character_revision:source.data._revision,job_id:job.result.id,chosen_loss:[]},token);
        assert.ok(finish.result.success);
        const sourceSelection={item_path:['inventory','pockets',0],item_id:'dynamo-device',location_id:location.id};
        await request('/lobbies/characters/'+actor.id+'/pda-network',{...sourceSelection,operation:'contact',login:'radio-player',friendly:true},token);
        await peer.call('Page.bringToFront');
        await peer.evaluate("[...document.querySelectorAll('#pda-shell-modal nav button')].find(b=>b.textContent==='Контакты').click();true");
        await peer.until("!document.querySelector('[data-radio-proximity]')?.hidden",'Детектор приближения установлен по правилам');
        await peer.evaluate("window.__b8Beeps=0;window.__b8Alerts=[];window.addEventListener('pda-proximity-alert',e=>__b8Alerts.push(e.detail));const original=AudioContext.prototype.createOscillator;AudioContext.prototype.createOscillator=function(...args){__b8Beeps++;window.__b8LastBeepAt=performance.now();return original.apply(this,args)};true");
        const soundPoint=await peer.evaluate("(()=>{const r=document.querySelector('[data-radio-test-sound]').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()");
        for(const type of ['mouseMoved','mousePressed','mouseReleased'])await peer.call('Input.dispatchMouseEvent',{type,...soundPoint,button:type==='mouseMoved'?'none':'left',buttons:type==='mousePressed'?1:0,clickCount:type==='mouseMoved'?0:1});
        await peer.until('__b8Beeps>0','Реальный AudioContext создаёт звуковой сигнал');
        await peer.until('performance.now()-__b8LastBeepAt>=500','Проверочный звук завершён до появления нового сигнала');
        const baseline=await peer.evaluate('__b8Beeps');
        await peer.evaluate("document.querySelector('[data-pda-close]').click();window.closeCharacterSheet();true");
        const stranger=await request(api+'/characters',{name:'Незнакомый сигнал',data:{inventory:{pockets:[{id:'unknown-pda',name:'КПК',category:'misc',quantity:1}],backpack:[]}}},token);
        await request(loc+'/spawn_character',{character_id:stranger.id,tile_x:44,tile_y:48},token);
        const scanUrl='/lobbies/characters/'+actor.id+'/pda-proximity?lobby_id='+lobby.id+'&location_id='+location.id;
        const detected=await request(scanUrl,null,token,'GET');assert.equal(detected.devices[0].signals.length,1,'Друг не вызывает тревогу');
        try{
            await peer.until('__b8Alerts.some(e=>e.sound)','Закрытый КПК сообщил о незнакомом сигнале');
            await peer.until('__b8Beeps>'+baseline,'Детектор создал звуковой сигнал');
        }catch(error){
            console.log('Диагностика детектора:',JSON.stringify(await peer.evaluate("({alerts:__b8Alerts,beeps:__b8Beeps,hidden:document.hidden,location:window.currentLocationId,character:window.currentCharacterId,localCharacter:window.currentLocationCharacterId,caches:Object.keys(sessionStorage).filter(k=>k.startsWith('pda-proximity:')).map(k=>[k,sessionStorage.getItem(k)])})()")));
            throw error;
        }
        await peer.evaluate(`window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);await usePdaAgain();
        async function usePdaAgain(){
            await peer.until("Boolean([...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id==='dynamo-device'))",'КПК для выключения звука');
            await peer.evaluate("[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id==='dynamo-device').querySelector('[data-widget-key=use]').click();true");
            await peer.until("document.getElementById('pda-shell-modal')",'КПК открыт');
            await peer.evaluate("[...document.querySelectorAll('#pda-shell-modal nav button')].find(b=>b.textContent==='Контакты').click();true");
            await peer.until("!document.querySelector('[data-radio-proximity]')?.hidden",'Управление звуковым сигналом');
        }
        await peer.evaluate("document.querySelector('[data-radio-proximity-sound]').click();true");
        await peer.until("(async()=>!(await (await import('/static/js/api.js')).Server.getPdaNetwork("+actor.id+","+JSON.stringify(sourceSelection)+")).source.proximity_sound)()",'Звук выключен на физическом КПК');
        await peer.evaluate("document.querySelector('[data-pda-close]').click();window.closeCharacterSheet();true");
        const toggleStranger=async()=>{
            const item=await request('/lobbies/characters/'+stranger.id,null,token,'GET');
            return request('/lobbies/characters/'+stranger.id+'/portable-lights',{operation:'pda_toggle',path:['inventory','pockets',0],source_id:'unknown-pda',character_revision:item.data._revision},token);
        };
        await toggleStranger();
        const monitorUser=await peer.evaluate("localStorage.getItem('user_id')"),cache=`pda-proximity:${monitorUser}:${lobby.id}:${actor.id}`;
        await peer.until(`JSON.parse(sessionStorage.getItem(${JSON.stringify(cache)})||'[]').length===0`,'Уход сигнала замечен');
        const quietBaseline=await peer.evaluate('__b8Beeps'),count=await peer.evaluate('__b8Alerts.length');
        await toggleStranger();await peer.until(`__b8Alerts.length>${count}&&__b8Alerts.at(-1).sound===false`,'Повторное появление показано без звука');
        assert.equal(await peer.evaluate('__b8Beeps'),quietBaseline);
        console.log('Детектор приближения: установлен штатным рецептом; друг не вызывает тревогу, незнакомый КПК вызывает настоящий звуковой сигнал при закрытом интерфейсе; отключение звука сохраняется на устройстве.');
    }finally{
        if(peer)peer.close();
        if(protocol){if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId}).catch(()=>{});protocol.close();}
    }
};
