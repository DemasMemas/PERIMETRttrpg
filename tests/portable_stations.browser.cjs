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
    await page.evaluate(`window.openCharacterSheet(${character.id},'inventory').then(()=>true)`);
    await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Разместить')",'Предмет позволяет начать размещение');
    await page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Разместить').click();true");
    await page.until("!document.getElementById('character-sheet-modal')?.getClientRects().length",'Лист закрыт для выбора точки');
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
    await page.until(`(async()=>{const m=await import('/static/js/locationScene.js');return m.sceneGraphicsForTest().roots.some(r=>r.userData.locationObject?.id===${result.object.id})})()`,'Станок появился среди видимых объектов');
    const shot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'station-in-scene.png'),Buffer.from(shot.data,'base64'));
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
    await page.evaluate("(()=>{const t=document.querySelector('select[aria-label=\"Инструменты для модификации\"]');t.value=[...t.options].find(o=>o.textContent.includes('Кустарный станок оружейника')).value;t.dispatchEvent(new Event('change'));const r=document.querySelector('select[aria-label=Модификация]');r.value='weapon-c23';r.dispatchEvent(new Event('change'));})()");
    await page.until("document.getElementById('crafting-modal')?.innerText.includes('1 заряд · 15 мин')",'Получена действующая оценка установки со станком');
    console.log('Станок: размещение мышью, синхронизация, заправка и выбор для модификаций проверены в Припяти.');
};
