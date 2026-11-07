// Физические заряды в копии Припяти и решение ГМа на глобальной карте.
const assert=require('node:assert/strict');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'controllers');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const template=await request(api+'/templates',{name:'Дистанционный взрыватель',category:'device',weight:.1,volume:.1,
        attributes:{device_kind:'remote_detonator'}},token);
    const timer=await request(api+'/templates',{name:'Таймер',category:'device',weight:.1,volume:.1,attributes:{device_kind:'timer'}},token);
    const actor=await request(api+'/characters',{name:'Связист с зарядом',data:{inventory:{pockets:[
        {id:'physical-pda',name:'КПК',category:'misc',quantity:1},
        {id:'remote-fuse',name:template.name,category:'device',quantity:1,templateId:template.id+1000000,attributes:template.attributes},
        {id:'remote-charge',name:'Граната РГД-5',category:'grenade',quantity:1,weight:.5,volume:1},
    ],backpack:[]},health:{current:700,max:700,effects:[]}}},token);
    await request(loc+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48},token);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Контроллеры в Припяти'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Припять готова');
    await page.evaluate(`window.currentLocationCharacterId=${actor.id};window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
    const use=async id=>{
        await page.until(`Boolean([...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id==='${id}'))`,'Предмет в инвентаре');
        await page.evaluate(`(()=>{const row=[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id==='${id}');row.querySelector('[data-widget-key=use]').click()})()`);
    };
    await use('remote-fuse');
    await page.until("document.getElementById('item-controller-modal')?.textContent.includes('Привязать к КПК')",'Бесплатная привязка');
    assert.ok(await page.evaluate("(()=>{const r=document.querySelector('#item-controller-modal .modal-content').getBoundingClientRect();return Math.abs(r.x+r.width/2-innerWidth/2)<2&&r.bottom<innerHeight})()"));
    await page.evaluate("[...document.querySelectorAll('#item-controller-modal button')].find(b=>b.textContent.includes('Привязать к КПК')).click();true");
    await page.until("!document.getElementById('item-controller-modal')",'Привязка сохранена');
    await use('remote-fuse');
    await page.until("document.getElementById('item-controller-modal')?.textContent.includes('Установить на заряд')",'Установка на гранату');
    await page.evaluate("[...document.querySelectorAll('#item-controller-modal button')].find(b=>b.textContent.includes('Установить на заряд')).click();true");
    await page.until("!document.getElementById('item-controller-modal')",'Взрыватель установлен');
    let fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    const charge=fresh.data.inventory.pockets.find(e=>e.controllerState?.kind==='remote_detonator');assert.ok(charge);
    const dropped=await request(loc+'/drop-item',{character_id:actor.id,character_revision:fresh.data._revision,item_path:['inventory','pockets',fresh.data.inventory.pockets.indexOf(charge)],expected_item:charge,amount:1},token);
    assert.ok(dropped.object.id);
    await request(loc+'/spawn_character',{character_id:actor.id,tile_x:48,tile_y:48},token);
    await page.evaluate(`window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
    await use('physical-pda');
    await page.until("document.querySelector('[data-pda-hold]')",'КПК можно взять в руку');
    await page.evaluate("document.querySelector('[data-pda-hold]').click();true");
    await page.until("document.querySelector('[data-pda-hold]')?.textContent==='Убрать КПК'",'КПК находится в руке');
    await page.evaluate("[...document.querySelectorAll('#pda-shell-modal nav button')].find(b=>b.textContent==='Заряды').click();true");
    await page.until("[...document.querySelectorAll('#pda-shell-modal button')].some(b=>b.textContent==='Подорвать · 1 ОД'&&!b.disabled)",'Физический заряд доступен');
    await page.evaluate("window.__controlledBlasts=[];socket.on('combat_explosion',e=>__controlledBlasts.push(e));[...document.querySelectorAll('#pda-shell-modal button')].find(b=>b.textContent==='Подорвать · 1 ОД').click();true");
    await page.until("__controlledBlasts.length>0",'Сетевое событие взрыва');
    fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    const remaining=await request('/lobbies/characters/'+actor.id+'/controllers?item_path='+encodeURIComponent(JSON.stringify(['inventory','pockets',0]))+'&item_id=physical-pda&location_id='+location.id,null,token,'GET');assert.equal(remaining.charges.length,0);
    await page.evaluate("document.querySelector('[data-pda-close]').click();window.closeCharacterSheet();true");
    console.log('Припять: привязка и установка через инвентарь, КПК в руке, подрыв перенесённой гранаты и реальное сетевое событие проверены.');

    const carrier=await request(api+'/characters',{name:'Носитель таймера',data:{inventory:{pockets:[
        {id:'world-timer',name:'Таймер',category:'device',quantity:1,templateId:timer.id+1000000,attributes:timer.attributes},
        {id:'world-charge',name:'Граната РГД-5',category:'grenade',quantity:1,weight:.5,volume:1}],backpack:[]},health:{current:700,max:700,effects:[]}}},token);
    const additional=await request(api+'/characters',{name:'Дополнительный пострадавший',data:{health:{current:700,max:700,effects:[]}}},token);
    await request('/lobbies/characters/'+carrier.id+'/controllers',{operation:'install',operation_id:'world-timer-install',character_revision:carrier.data._revision,
        item_path:['inventory','pockets',1],item_id:'world-charge',controller_path:['inventory','pockets',0],controller_id:'world-timer',seconds:60},token);
    const clock=await request(api,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+1;
    await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    const harmed=await request('/lobbies/characters/'+carrier.id,null,token,'GET');assert.ok(harmed.data.health.current<700);
    await page.until("document.getElementById('detonations-btn')?.getClientRects().length>0",'ГМ получил сохранённый запрос последствий');
    await page.evaluate("document.getElementById('detonations-btn').click();true");
    await page.until("document.querySelector('#world-detonations-modal select')",'Выбор дополнительного пострадавшего');
    await page.evaluate(`document.querySelector('#world-detonations-modal select').value='${additional.id}';[...document.querySelectorAll('#world-detonations-modal button')].find(b=>b.textContent==='Применить к выбранному персонажу').click();true`);
    await page.until("document.getElementById('world-detonations-modal')?.textContent.includes('Носитель таймера, Дополнительный пострадавший')",'ГМ добавил пострадавшего');
    const affected=await request('/lobbies/characters/'+additional.id,null,token,'GET');assert.ok(affected.data.health.current<700);
    await page.evaluate("[...document.querySelectorAll('#world-detonations-modal button')].find(b=>b.textContent==='Завершить последствия').click();true");
    await page.until("document.getElementById('detonations-btn')?.style.display==='none'",'Решение ГМа сохранено');
    console.log('Глобальная карта: таймер автоматически поразил носителя; ГМ добавил другого пострадавшего через интерфейс и завершил последствия.');

    await page.evaluate("[...document.querySelectorAll('#world-detonations-modal button')].find(b=>b.textContent==='Закрыть').click();true");
    const technician=await request(api+'/characters',{name:'Таймер питания КПК',data:{inventory:{pockets:[
        {id:'power-pda',name:'КПК',category:'misc',quantity:1,pdaState:{on:false,charge_percent:50}},
        {id:'power-timer',name:'Таймер',category:'device',quantity:1,templateId:timer.id+1000000,attributes:timer.attributes}],backpack:[]},health:{current:700,max:700,effects:[]}}},token);
    await request(loc+'/spawn_character',{character_id:technician.id,tile_x:41,tile_y:48},token);
    await page.evaluate(`window.currentLocationCharacterId=${technician.id};window.openCharacterSheet(${technician.id},'inventory').then(()=>true)`);
    await use('power-timer');
    await page.until("document.querySelector('#item-controller-modal [aria-label=\"Действие таймера\"]')",'Таймер поддерживает питание устройств');
    await page.evaluate("[...document.querySelectorAll('#item-controller-modal button')].find(b=>b.textContent.includes('Установить таймер')).click();true");
    await page.until("!document.getElementById('item-controller-modal')",'Таймер установлен на физический КПК');
    const poweredClock=await request(api,null,token,'GET'),later=poweredClock.game_day*1440+poweredClock.game_time_minutes+2;
    await request(api+'/time',{game_day:Math.floor(later/1440),game_time_minutes:later%1440},token,'PATCH');
    const powered=await request('/lobbies/characters/'+technician.id,null,token,'GET');
    assert.equal(powered.data.inventory.pockets[0].pdaState.on,true);
    assert.ok(Math.abs(powered.data.inventory.pockets[0].pdaState.charge_percent-(50-.5/60))<.00001);
    const manage=async()=>{
        await page.until("document.querySelector('[data-widget-key=controller]')",'Настройка установленного таймера');
        await page.evaluate("document.querySelector('[data-widget-key=controller]').click();true");
        await page.until("document.querySelector('#item-controller-modal [aria-label=\"Действие таймера\"]')",'Форма таймера питания');
    };
    await manage();
    const size=await page.evaluate("(()=>{const r=document.querySelector('#item-controller-modal .modal-content').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})()");assert.ok(size);
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    assert.equal(await page.evaluate('innerWidth'),1920);assert.equal(await page.evaluate('innerHeight'),1080);
    assert.ok(await page.evaluate("(()=>{const r=document.querySelector('#item-controller-modal .modal-content').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})()"));
    const shot=await page.call('Page.captureScreenshot',{format:'png'});
    require('node:fs').writeFileSync(require('node:path').join(safeSession.artifacts,'controllers.png'),Buffer.from(shot.data,'base64'));
    await page.evaluate("document.querySelector('#item-controller-modal [aria-label=\"Действие таймера\"]').value='1';[...document.querySelectorAll('#item-controller-modal button')].find(b=>b.textContent.includes('Настроить и запустить')).click();true");
    await page.until("!document.getElementById('item-controller-modal')",'Выключение настроено');
    await manage();
    await page.evaluate("[...document.querySelectorAll('#item-controller-modal button')].find(b=>b.textContent.includes('Нажать кнопку таймера сейчас')).click();true");
    await page.until("!document.getElementById('item-controller-modal')",'Кнопка таймера нажата');
    const off=await request('/lobbies/characters/'+technician.id,null,token,'GET');
    assert.equal(off.data.inventory.pockets[0].pdaState.on,false);assert.ok(off.data.inventory.pockets[0].controllerState);
    console.log('Таймер питания: установка через интерфейс, настоящее включение КПК по времени, расход только за активную минуту и выключение физической кнопкой; 1920×1080.');
};
