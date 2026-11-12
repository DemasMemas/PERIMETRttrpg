// Размещение и заправка устройства в настоящей сцене, отдельная БД и GPU.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'stations');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token);
    const loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const template=await request(api+'/templates',{name:'Кустарный станок электрика',category:'tool',weight:0,volume:0,
        attributes:{portable_station:'recipe-m45',station_asset:'station_electrician',craft_only:true,not_for_sale:true}},token);
    const book=await request(api+'/templates',{name:'Пистолеты — учебник проверки',category:'misc',weight:0,volume:0,
        attributes:{book_kind:'equipment',book_subject:'Пистолеты',reading_seconds:14400}},token);
    const character=await request(api+'/characters',{name:'Размещение станка',data:{health:{current:700,max:700,effects:[]},
        skills:{other:{engineering:{base:20}}},
        weapons:[{id:'station-gun',name:'Пистолет',category:'weapon',subcategory:'Пистолеты',itemClass:'1',durability:100,maxDurability:100,modifications:[]}],
        inventory:{pockets:[{id:'station-instance',templateId:template.id+1000000,name:template.name,category:'tool',quantity:1,attributes:template.attributes},
            {id:'solder-instance',name:'Паяльные инструменты',category:'tool',quantity:1,uses:3,maxUses:10,attributes:{uses:10}},
            {id:'reading-pda',name:'КПК',category:'device',quantity:1,pdaState:{on:true,charge_percent:100}},
            {id:'reading-book',templateId:book.id+1000000,name:book.name,category:'misc',quantity:1,attributes:book.attributes}]}}},token);
    const initial=await request('/lobbies/characters/'+character.id,null,token,'GET');
    await request('/lobbies/characters/'+character.id+'/crafting',{operation:'read_book',operation_id:'station-read-book',
        character_revision:initial.data._revision,path:['inventory','pockets',3]},token);
    const clock=await request(api,null,token,'GET');
    await request(api+'/time',{game_day:clock.game_day,game_time_minutes:clock.game_time_minutes+241},token,'PATCH');
    await request(loc+'/spawn_character',{character_id:character.id,tile_x:41,tile_y:48},token);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Размещение станка в Припяти'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
    await page.evaluate("if(!window.locationEditMode)window.toggleEditMode();true");
    const key = (type, code) => page.call('Input.dispatchKeyEvent', { type, code, key: code.slice(-1).toLowerCase() });
    await page.evaluate(`window.openCharacterSheet(${character.id},'inventory').then(()=>true)`);
    await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Разместить')",'Предмет позволяет начать размещение');
    await page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Разместить').click();true");
    await page.until("!document.getElementById('character-sheet-modal')?.getClientRects().length",'Лист закрыт для выбора точки');
    await page.evaluate("document.activeElement?.blur();true");
    await key('keyDown', 'KeyQ');
    assert.equal(await page.evaluate("Boolean(document.querySelector('.map-editor-wheel'))"), false, 'Q не открывает выбор инструмента при размещении');
    await key('keyUp', 'KeyQ');
    await key('keyDown', 'KeyE');
    await key('keyUp', 'KeyE');
    assert.equal(await page.evaluate('window.locationEditMode'), true, 'E не переключает режим при размещении');
    await key('keyDown', 'KeyQ');
    await key('keyUp', 'KeyQ');
    await page.evaluate("window.__stationEvents=[];socket.on('location_object_created',e=>window.__stationEvents.push(e));true");
    const before=await request('/lobbies/characters/'+character.id,null,token,'GET');
    assert.ok(before.data.inventory.pockets.some(i=>i.id==='station-instance'),'Предпросмотр не удалил предмет');
    const height=Number(fixture.tiles_data[48][42].height)||1;
    const point=await page.evaluate(`(async()=>{const m=await import('/static/js/locationScene.js'),g=m.sceneGraphicsForTest();
        g.controls.target.set(42,${height},48.5);g.camera.position.set(48,${height+10},57);g.controls.update();g.camera.updateMatrixWorld(true);
        const p=g.camera.position.clone().set(42.7,${height},48.5).project(g.camera),r=g.renderer.domElement.getBoundingClientRect();
        return {x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
    for(const type of ['mouseMoved','mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{
        type,...point,button:type==='mouseMoved'?'none':'left',buttons:type==='mousePressed'?1:0,clickCount:type==='mouseMoved'?0:1});
    await page.until("window.__stationEvents.some(e=>e.object?.properties?.station_item?.id==='station-instance')",'Размещённый станок пришёл в сцену');
    const result=await page.evaluate("window.__stationEvents.find(e=>e.object?.properties?.station_item?.id==='station-instance')");
    assert.ok(Math.abs(result.object.properties.rotation - Math.PI / 12) < 1e-6, 'Q и E повернули станок, угол сохранился');
    await page.until(`(async()=>{const m=await import('/static/js/locationScene.js');return m.sceneGraphicsForTest().roots.some(r=>r.userData.locationObject?.id===${result.object.id})})()`,'Станок появился среди видимых объектов');
    const shot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'station-in-scene.png'),Buffer.from(shot.data,'base64'));
    await key('keyDown', 'KeyQ');
    await page.until("Boolean(document.querySelector('.map-editor-wheel'))", 'Q снова открывает выбор инструмента после установки');
    await key('keyUp', 'KeyQ');
    await key('keyDown', 'KeyE');
    await key('keyUp', 'KeyE');
    assert.equal(await page.evaluate('window.locationEditMode'), false, 'E снова переключает режим после установки');
    await page.evaluate(`(async()=>{const {showStationTools}=await import('/static/js/stationTools.js');await showStationTools({lobbyId:${lobby.id},locationId:${location.id},characterId:${character.id},object:${JSON.stringify(result.object)}});})()`);
    await page.until("[...document.querySelectorAll('#station-tools-modal button')].some(b=>b.textContent==='Установить')",'Открыта заправка станка');
    await page.evaluate("[...document.querySelectorAll('#station-tools-modal button')].find(b=>b.textContent==='Установить').click();true");
    await page.until("[...document.querySelectorAll('#station-tools-modal p')].some(p=>p.textContent.includes('Паяльные инструменты: 3 заряда')&&[...p.querySelectorAll('button')].some(b=>b.textContent==='Забрать'))",'Инструмент установлен с сохранением зарядов');
    const detail=await request(loc,null,token,'GET');
    assert.equal(detail.objects.find(o=>o.id===result.object.id).properties.station_tools[0].id,'solder-instance');
    await request(loc+'/objects',{name:'Кустарный станок оружейника',type:'household_workbench',tile_x:41,tile_y:49,
        properties:{portable_station:'recipe-m42',craft_workbench:'Станок Оружейника',visual_asset:'station_gunsmith',
            station_tools:[{id:'loaded-gunsmith-tools',name:'Набор инструментов Оружейника (Стандартные)',category:'tool',quantity:1,uses:3}]}},token);
    await page.evaluate(`(async()=>{document.getElementById('station-tools-modal')?.remove();const {showCrafting}=await import('/static/js/crafting.js');await showCrafting({characterId:${character.id},lobbyId:${lobby.id}});})()`);
    await page.evaluate("document.querySelector('[data-crafting-tab=modifications]').click();true");
    await page.until("[...document.querySelectorAll('select[aria-label=\"Инструменты для модификации\"] option')].some(o=>o.textContent.includes('Кустарный станок оружейника'))",'Размещённый станок доступен для модификаций');
    await page.evaluate("(()=>{const t=document.querySelector('select[aria-label=\"Инструменты для модификации\"]');t.value=[...t.options].find(o=>o.textContent.includes('Кустарный станок оружейника')).value;t.dispatchEvent(new Event('change'));document.querySelector('[data-modification-id=weapon-c23]').click();})()");
    await page.until("document.getElementById('crafting-modal')?.innerText.includes('1 заряд · 15 мин')",'Получена действующая оценка установки со станком');
    console.log('Станок: размещение мышью, синхронизация, заправка и выбор для модификаций проверены в Припяти.');
    await page.evaluate("document.getElementById('crafting-modal').closeCrafting();true");
    const generator=await request(loc+'/objects',{name:'Электрогенератор',type:'household_generator',tile_x:41,tile_y:47,properties:{}},token);
    const stored=await request('/lobbies/characters/'+character.id,null,token,'GET');
    stored.data.inventory.pockets.push(
        {id:'generator-bottle',name:'Пластиковая бутылка (Объем 1 литр) (3 использования воды)',category:'misc',quantity:1,
            contents:[]},
        {id:'generator-canister',name:'Канистра (15 литров)',category:'misc',quantity:1,attributes:{liquid_capacity_liters:15},
            contents:[{id:'generator-fuel',name:'Топливо(1 Литр)',category:'misc',quantity:1.5}]},
        {id:'generator-cell',name:'Аккумуляторная батарея',category:'device',quantity:1,
            attributes:{rechargeable_battery:true,charge_percent:0}},
        {id:'generator-exo',name:'Аккумулятор экзоскелета',category:'device',quantity:1,slotType:'exoskeleton_battery',
            attributes:{slot_type:'exoskeleton_battery',charge_days:10,remaining_days:0}});
    await request('/lobbies/characters/'+character.id,{data:stored.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${character.id},'inventory').then(()=>true)`);
    await page.until("[...document.querySelectorAll('[data-widget-identity][data-container-path]')].some(e=>e.dataset.widgetIdentity.includes('generator-bottle'))",
        'Пустая бутылка принимает перетаскивание в инвентаре');
    await page.evaluate(`(()=>{const find=id=>[...document.querySelectorAll('[data-widget-identity][data-container-path]')]
        .find(e=>JSON.parse(e.dataset.widgetIdentity).id===id);
        const from=find('generator-canister'),to=find('generator-bottle'),transfer=new DataTransfer();
        const path=from.dataset.path.split(',').map(part=>/^\\d+$/.test(part)?Number(part):part);
        transfer.setData('application/x-inventory-path',JSON.stringify([...path,'contents',0]));
        to.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));return true;})()`);
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');const c=await Server.getCharacter(${character.id});
        const items=c.data.inventory.pockets;return items.find(i=>i.id==='generator-bottle')?.contents?.[0]?.quantity===1
            &&items.find(i=>i.id==='generator-canister')?.contents?.[0]?.quantity===.5;})()`,'Перелился один литр, в канистре осталось пол-литра');
    await page.evaluate("window.closeCharacterSheet();true");
    await page.until(`(async()=>{const m=await import('/static/js/locationScene.js');return m.sceneGraphicsForTest().roots.some(r=>r.userData.locationObject?.id===${generator.id})})()`,'Генератор появился на сцене');
    const generatorPoint=await page.evaluate(`(async()=>{const m=await import('/static/js/locationScene.js'),g=m.sceneGraphicsForTest();
        const root=g.roots.find(r=>r.userData.locationObject?.id===${generator.id});
        const p=root.getWorldPosition(g.camera.position.clone());p.y+=.3;
        g.controls.target.copy(p);g.camera.position.set(p.x-3,p.y+4,p.z-3);g.controls.update();g.camera.updateMatrixWorld(true);
        p.project(g.camera);const r=g.renderer.domElement.getBoundingClientRect();
        return {x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
    for(const type of ['mouseMoved','mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{
        type,...generatorPoint,button:type==='mouseMoved'?'none':'right',buttons:type==='mousePressed'?2:0,clickCount:type==='mouseMoved'?0:1});
    const click=async(label, selector='#generator-modal button')=>page.evaluate(`
        [...document.querySelectorAll(${JSON.stringify(selector)})].find(b=>b.textContent.trim()===${JSON.stringify(label)}).click();true`);
    await page.until("[...document.querySelectorAll('button,.context-menu-item')].some(b=>b.textContent.trim()==='Генератор')",'ПКМ по модели генератора');
    await click('Генератор','button,.context-menu-item');
    const see=(value,label)=>page.until(`document.getElementById('generator-modal')?.innerText.includes(${JSON.stringify(value)})`,label);
    await see('Пластиковая бутылка','Панель нашла топливо в бутылке персонажа');
    await click('Заправить','#generator-modal p:has(input[max="1"]) button');
    await see('топлива на 288 мин','Генератор принял один литр из бутылки');
    await click('Поставить на зарядку');
    await see('Аккумуляторная батарея · 0%','Разряженная батарея установлена');
    await click('Поставить на зарядку');
    await see('Аккумулятор экзоскелета · 0%','Аккумулятор экзоскелета установлен');
    await click('Подключить','#generator-modal p:has(button) button');
    await see('питание не подаётся','Станок подключён к выключенному генератору');
    await click('Включить');
    await see('питание есть, −10 к СЛ','Станок получил питание');
    const now=await request(api,null,token,'GET');
    const absolute=(now.game_day||1)*1440+(now.game_time_minutes||0);
    for(const [delta, normalCharge, exoCharge] of [[60,100,25],[240,100,100],[288,100,100]]){
        const elapsed=absolute+delta;
        await request(api+'/time',{game_day:Math.floor(elapsed/1440),game_time_minutes:elapsed%1440},token,'PATCH');
        await see(`Аккумуляторная батарея · ${normalCharge}%`,'Заряд батареи вырос за игровое время');
        await see(`Аккумулятор экзоскелета · ${exoCharge}%`,'Заряд экзоскелета вырос за игровое время');
    }
    await see('Выключен · топлива на 0 мин','Топливо закончилось, генератор выключился');
    await click('Забрать');
    await see('Аккумуляторная батарея в инвентаре · 100%','Та же батарея вернулась в инвентарь');
    const saved=await request('/lobbies/characters/'+character.id,null,token,'GET');
    assert.equal(saved.data.inventory.pockets.find(item=>item.id==='generator-cell').attributes.charge_percent,100);
    assert.deepEqual(saved.data.inventory.pockets.find(item=>item.id==='generator-bottle').contents,[]);
    await click('Закрыть');
    console.log('Генератор: ПКМ по модели, заправка из бутылки, зарядка батареи, подключение станка и выключение при исчерпании топлива.');
    saved.data.health.effects.push({id:'generator-prosthesis-injury',type:'amputation',area:'leftLeg',
        active:true,loss_extent:'elbow_or_knee'});
    saved.data.inventory.pockets.push({id:'generator-prosthesis',name:'Навесная голень со стопой',category:'prosthesis',quantity:1});
    await request('/lobbies/characters/'+character.id,{data:saved.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.prostheses select option[value=leftLeg]')",'Лист показывает доступный протез');
    await page.evaluate("document.querySelector('.prostheses select').value='leftLeg';true");
    await click('Установить','.prostheses button');
    await page.until("[...document.querySelectorAll('.prostheses button')].some(b=>b.textContent==='Установить аккумуляторы'&&!b.disabled)",
        'Лист предлагает зарядить протез батареей из генератора');
    await click('Установить аккумуляторы','.prostheses button');
    await page.until("document.querySelector('.prostheses')?.innerText.includes('питание 1440 мин')",'Протез получил сутки работы');
    const worn=await request('/lobbies/characters/'+character.id,null,token,'GET');
    assert.equal(worn.data.health.prostheses.leftLeg.base.rechargeableCells[0].id,'generator-cell');
    const clockAfterInstall=await request(api,null,token,'GET');
    await request(api+'/time',{game_day:clockAfterInstall.game_day+1,game_time_minutes:clockAfterInstall.game_time_minutes},token,'PATCH');
    await page.until("document.querySelector('.prostheses')?.innerText.includes('питание 0 мин')",'Протез разрядился за сутки');
    const drained=await request('/lobbies/characters/'+character.id,null,token,'GET');
    assert.equal(drained.data.inventory.pockets.find(item=>item.id==='generator-cell')?.attributes.charge_percent,0);
    await page.evaluate("window.closeCharacterSheet();true");
    console.log('Протез: аккумулятор из генератора установлен из листа и вернулся в инвентарь разряженным после суток работы.');
};
