// Укрытия на копии Припяти: настоящая сцена и обычные действия инвентаря.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'covers');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const items=[];
    for(const [key,name] of [['m19','Деревянная баррикада'],['m18','Занавеска']]){
        const t=await request(api+'/templates',{name,category:'misc',weight:key==='m19'?10:1,volume:key==='m19'?20:10,
            attributes:{crafted_cover:'recipe-'+key,placement_asset:'crafted_cover_'+key,not_for_sale:true}},token);
        items.push({id:'cover-'+key,name,category:'misc',templateId:t.id+1000000,quantity:1,weight:t.weight,volume:t.volume,attributes:t.attributes});
    }
    const actor=await request(api+'/characters',{name:'Установка укрытий',data:{inventory:{pockets:items,backpack:[]},health:{current:700,max:700,effects:[]}}},token);
    const spawned=await request(loc+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48},token);
    const anchor=await request(loc+'/objects',{name:'Окно для занавески',type:'window',tile_x:42,tile_y:48,
        properties:{elevation:1,is_open:true,dimensions:{width:1.2,depth:.14,height:1}}},token);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Укрытия в Припяти'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.currentLocationCharacterId=${actor.id};window.__coverEvents=[];socket.on('location_object_created',e=>__coverEvents.push(e));})()`);
    const models=await page.evaluate(`(async()=>{const T=await import('three'),{createHouseholdModel}=await import('/static/js/locationHouseholdModels.js'),{LOCATION_ASSETS}=await import('/static/js/locationAssetCatalog.js');
        return LOCATION_ASSETS.filter(a=>a.id.startsWith('crafted_cover_')).map(a=>{const mesh=createHouseholdModel(T,a,{width:a.dimensions[0],depth:a.dimensions[1],height:a.dimensions[2]});const size=new T.Box3().setFromObject(mesh).getSize(new T.Vector3()).toArray();mesh.material.dispose();return {id:a.id,size,triangles:mesh.geometry.attributes.position.count/3};});})()`);
    assert.equal(models.length,6);assert.ok(models.every(m=>m.size.every(v=>Number.isFinite(v)&&v>0)&&m.triangles>0));
    const height=Number(fixture.tiles_data[48][41].height)||1;
    const project=async(x,y,z)=>page.evaluate(`(()=>{const g=L.sceneGraphicsForTest();g.controls.target.set(42,${height},48.5);g.camera.position.set(48,${height+10},57);g.controls.update();g.camera.updateMatrixWorld(true);const p=g.camera.position.clone().set(${x},${y},${z}).project(g.camera),r=g.renderer.domElement.getBoundingClientRect();return {x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
    const start=async id=>{
        await page.evaluate(`window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
        await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Разместить')",'Доступна установка предмета');
        await page.evaluate("window.__placementToasts=[];window.__placementObserver?.disconnect();window.__placementObserver=new MutationObserver(records=>{for(const r of records)for(const n of r.addedNodes)if(n.matches?.('.notification'))__placementToasts.push(n.textContent)});__placementObserver.observe(document.body,{childList:true,subtree:true});true");
        await page.evaluate(`(()=>{const row=[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(id)});row.querySelector('[data-widget-key=place-station]').click();})()`);
        await page.until("!document.getElementById('character-sheet-modal')?.getClientRects().length||__placementToasts.length",'Выбор места в сцене');
        assert.ok(await page.evaluate("!document.getElementById('character-sheet-modal')?.getClientRects().length"),await page.evaluate('__placementToasts.join("; ")'));
    };
    const click=async point=>{for(const type of ['mouseMoved','mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{
        type,...point,button:type==='mouseMoved'?'none':'left',buttons:type==='mousePressed'?1:0,clickCount:type==='mouseMoved'?0:1});};
    await start('cover-m19');await click(await project(41.5,height,49.7));
    await page.until("__coverEvents.some(e=>e.object?.properties?.crafted_cover==='recipe-m19')",'Баррикада размещена мышью');
    const wood=await page.evaluate("__coverEvents.find(e=>e.object?.properties?.crafted_cover==='recipe-m19').object");
    assert.equal(wood.properties.cover_hp,1000);assert.equal(wood.properties.cover_physical_protection,5);
    await page.evaluate(`L.showObjectContextMenu(650,350,${JSON.stringify(wood)});true`);
    await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Снять в инвентарь')",'Доступно снятие укрытия');
    await page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Снять в инвентарь').click();true");
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');const c=await Server.getCharacter(${actor.id});return c.data.inventory.backpack.some(i=>i.id==='cover-m19')})()`,'Исходный предмет возвращён в инвентарь');
    await start('cover-m18');await click(await project(42.5,height+1.5,48.5));
    await page.until("__coverEvents.some(e=>e.object?.properties?.crafted_cover==='recipe-m18')",'Занавеска установлена на окно');
    const curtain=await page.evaluate("__coverEvents.find(e=>e.object?.properties?.crafted_cover==='recipe-m18').object");
    assert.equal(curtain.properties.cover_anchor_id,anchor.id);assert.equal(curtain.properties.cover_hp,200);
    const screenshot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'crafted-covers-scene.png'),Buffer.from(screenshot.data,'base64'));
    await request(loc+'/combat/start',{location_character_ids:[spawned.location_character_id],initiator_location_character_id:spawned.location_character_id,initiator_first:true},token);
    let state=await request(loc+'/combat',null,token,'GET');
    const fighter=state.characters.find(c=>c.character_id===actor.id);
    await request(loc+'/combat/adjust',{location_character_id:spawned.location_character_id,action_points:2-fighter.action_points_current},token);
    await start('cover-m19');await click(await project(41.5,height,49.7));
    await page.until(`__coverEvents.some(e=>e.object?.properties?.crafted_cover==='recipe-m19'&&e.object.id!==${wood.id})`,'Боевое длительное действие завершило установку');
    state=await request(loc+'/combat',null,token,'GET');
    const after=state.characters.find(c=>c.character_id===actor.id);
    assert.equal(after.action_points_current,after.action_points_max-3,'Остаток 3 ОД оплачен в следующем ходу один раз');
    await request(loc+'/combat/end',{},token);
    console.log('Укрытия: размещение, снятие, занавеска на окно и установка за 5 ОД через следующий ход проверены в Припяти.');
};
