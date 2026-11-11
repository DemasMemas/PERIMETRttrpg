// Ловушки в настоящей сцене Припяти; только собственная БД и браузер.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'traps');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const template=await request(api+'/templates',{name:'Сеть',category:'misc',weight:1,volume:4,
        attributes:{trap_recipe:'recipe-m5',trap_kind:'net',source_recipe_id:'recipe-m5'}},token);
    const actor=await request(api+'/characters',{name:'Проверка ловушек',data:{skills:{physical:{strength:{base:40}}},
        inventory:{pockets:[{id:'test-trap-net',name:'Сеть',category:'misc',quantity:1,templateId:template.id+1000000,
            weight:1,volume:4,attributes:template.attributes}],backpack:[]},health:{current:700,max:700,effects:[]}}},token);
    const spawned=await request(loc+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48},token);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Ловушки в Припяти'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.currentLocationCharacterId=${actor.id};window.__trapEvents=[];socket.on('location_object_updated',e=>__trapEvents.push(e));})()`);
    const shapes=await page.evaluate(`(async()=>{const T=await import('three'),{createHouseholdModel}=await import('/static/js/locationHouseholdModels.js');
        return ['beartrap','net','mine','tripwire','alarm','caltrops'].map(kind=>{const mesh=createHouseholdModel(T,{visual:{modelId:'trap_'+kind}},{width:.65,depth:.65,height:.08});const size=new T.Box3().setFromObject(mesh).getSize(new T.Vector3()).toArray();return {kind,size,triangles:mesh.geometry.attributes.position.count/3};});})()`);
    assert.ok(shapes.every(m=>m.size.every(v=>Number.isFinite(v)&&v>0)&&m.triangles>0));
    const height=Number(fixture.tiles_data[48][41].height)||1;
    const project=async(x,z)=>page.evaluate(`(()=>{const g=L.sceneGraphicsForTest();g.controls.target.set(42,${height},48.5);g.camera.position.set(48,${height+10},57);g.controls.update();g.camera.updateMatrixWorld(true);const p=g.camera.position.clone().set(${x},${height},${z}).project(g.camera),r=g.renderer.domElement.getBoundingClientRect();return {x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
    await page.evaluate(`window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
    await page.until("[...document.querySelectorAll('[data-widget-key=place-station]')].length>0",'Сеть доступна для установки');
    await page.evaluate("document.querySelector('[data-widget-key=place-station]').click();true");
    await page.until("!document.getElementById('character-sheet-modal')?.getClientRects().length",'Выбор клетки');
    const point=await project(42.5,48.5);
    for(const type of ['mouseMoved','mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{
        type,...point,button:type==='mouseMoved'?'none':'left',buttons:type==='mousePressed'?1:0,clickCount:type==='mouseMoved'?0:1});
    await page.until("__trapEvents.some(e=>e.object?.type==='placed_trap')",'Сеть размещена мышью');
    let trap=await page.evaluate("__trapEvents.find(e=>e.object?.type==='placed_trap').object");
    assert.equal(trap.tile_x,42);assert.equal(trap.tile_y,48);
    await page.until(`L.sceneGraphicsForTest().roots.some(r=>r.userData.locationObjectId===${trap.id})`,'Ловушка появилась по сетевому событию');
    await checkTrapTraits({page,request,token,api,loc,lobby,location,template,trap,safeSession});
    trap=(await request(loc,null,token,'GET')).objects.find(o=>o.id===trap.id);
    await page.evaluate(`L.showObjectContextMenu(650,350,${JSON.stringify(trap)});true`);
    await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent.includes('Замаскировать'))",'Действие маскировки');
    await page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Замаскировать')).click();true");
    await page.until("document.getElementById('trap-action-modal')?.innerText.includes('5 ОД')",'Форма маскировки');
    const centered=await page.evaluate("(()=>{const r=document.querySelector('#trap-action-modal .modal-content').getBoundingClientRect();return Math.abs(r.x+r.width/2-innerWidth/2)<2&&Math.abs(r.y+r.height/2-innerHeight/2)<2})()");assert.ok(centered);
    await page.evaluate("[...document.querySelectorAll('#trap-action-modal button')].find(b=>b.textContent==='Замаскировать').click();true");
    await page.until("!document.getElementById('trap-action-modal')",'Маскировка завершилась');
    const beforeReload=await page.evaluate('performance.timeOrigin');await page.call('Page.reload');
    await waitForDocument(page,{href,afterEpoch:beforeReload,label:'Сохранённая ловушка'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Перезагрузка сцены');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.currentLocationCharacterId=${actor.id};})()`);
    assert.ok(await page.evaluate(`L.sceneGraphicsForTest().roots.some(r=>r.userData.locationObjectId===${trap.id})`));
    await page.evaluate(`socket.emit('move_in_location',{token:${JSON.stringify(token)},location_id:${location.id},character_id:${actor.id},x:44,y:48});true`);
    await page.until(`(async()=>{const c=await (await import('/static/js/api.js')).Server.getCharacter(${actor.id});return !!c.data.health.combatMeta?.trappedNet})()`,'Наступивший персонаж удерживается сетью');
    let detail=await request(loc,null,token,'GET');trap=detail.objects.find(o=>o.type==='placed_trap');assert.equal(trap.properties.trap_status,'spent');
    await page.evaluate(`L.showObjectContextMenu(650,350,${JSON.stringify(trap)});true`);
    await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent.includes('Освободиться из сети'))",'Освобождение из сети');
    await page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Освободиться из сети')).click();true");
    await page.until("document.getElementById('trap-action-modal')",'Форма освобождения');
    // Вне боя не начисляем ресурсы; критическая единица допускает повторную попытку.
    for(let attempt=0;attempt<5;attempt++){
        await page.evaluate("[...document.querySelectorAll('#trap-action-modal button')].find(b=>b.textContent==='Освободиться из сети').click();true");
        await page.until("!document.getElementById('trap-action-modal')",'Проверка Силы завершилась');
        const c=await request('/lobbies/characters/'+actor.id,null,token,'GET');if(!c.data.health.combatMeta?.trappedNet)break;
        await page.evaluate(`(async()=>{const {showTrapAction}=await import('/static/js/traps.js');await showTrapAction({characterId:${actor.id},lobbyId:${lobby.id},locationId:${location.id},trap:${JSON.stringify(trap)},operation:'escape_net'});})()`);
    }
    assert.ok(!(await request('/lobbies/characters/'+actor.id,null,token,'GET')).data.health.combatMeta?.trappedNet);
    await project(42.5,48.5);const screenshot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'traps-scene.png'),Buffer.from(screenshot.data,'base64'));
    await checkTripwireChoices({page,request,token,api,safeSession});
    console.log('Ловушки в Припяти: модели, установка мышью, сетевое появление, маскировка, reload, срабатывание и освобождение из сети проверены.');
};

async function checkTrapTraits({page,request,token,api,loc,lobby,location,template,trap,safeSession}){
    const engineer=await request(api+'/characters',{name:'Инженер и маскировка',data:{
        basic:{background:{ruleId:'background-z',name:'Полевой инженер'}},
        features:{specialTraits:[{ruleId:'trait-f14',name:'Мастер маскировки'},{ruleId:'trait-f14',name:'Мастер маскировки'}]},
        skills:{other:{stealth:{base:10}}},health:{current:700,max:700,effects:[]},
        inventory:{pockets:[{id:'engineer-net',name:'Сеть',category:'misc',quantity:1,templateId:template.id+1000000,
            attributes:template.attributes},{id:'engineer-shovel',name:'Лопата',category:'tool',quantity:1}],backpack:[]},
    }},token);
    await request(loc+'/spawn_character',{character_id:engineer.id,tile_x:41,tile_y:49},token);
    await page.evaluate(`window.openCharacterSheet(${engineer.id},'inventory').then(()=>true)`);
    await page.until("document.querySelector('[data-widget-key=place-station]')?.disabled",'Инженер видит недоступную установку ловушки');
    assert.ok(await page.evaluate("document.querySelector('[data-widget-key=place-station]').title.includes('Полевой инженер')"));
    await page.evaluate(`(async()=>{window.closeCharacterSheet();const {showTrapAction}=await import('/static/js/traps.js');await showTrapAction({characterId:${engineer.id},lobbyId:${lobby.id},locationId:${location.id},trap:${JSON.stringify(trap)},operation:'mask'});})()`);
    await page.until("document.querySelector('#trap-action-modal')?.innerText.includes('Мастер маскировки: +8')",'Показан личный бонус маскировки');
    assert.ok(await page.evaluate("document.querySelector('#trap-action-modal').innerText.includes('×1,5')"));
    await page.evaluate("(()=>{const s=document.querySelector('#trap-action-modal select');s.value=[...s.options].find(o=>o.textContent==='Лопата').value;return true})()");
    const screenshot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'trap-traits.png'),Buffer.from(screenshot.data,'base64'));
    await page.evaluate("[...document.querySelectorAll('#trap-action-modal button')].find(b=>b.textContent==='Замаскировать').click();true");
    await page.until("!document.getElementById('trap-action-modal')",'Бросок маскировки инженера сохранён');
    const saved=await request('/lobbies/characters/'+engineer.id,null,token,'GET');
    const result=saved.data.crafting.trap_receipts.at(-1).result;
    assert.equal(result.check.tool_bonus,5);assert.equal(result.check.masking_bonus,8);
    assert.equal(result.check.total,result.check.roll+13);
    assert.equal(saved.data.inventory.pockets.length,2);
    fs.writeFileSync(path.join(safeSession.artifacts,'trap-traits.json'),JSON.stringify(result,null,2));
}

async function checkTripwireChoices({page,request,token,api,safeSession}){
    const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../app/data/equipment_catalog.json'),'utf8')).templates;
    const forbidden=['Подствольная граната Вог-25','Подствольная граната N-101-2','Граната ОГ-12',
        'Подствольный газовый','Подствольный дымовой','Подствольный светошумовой','Коктейль Молотова','Кустарная бомба'];
    const allowed=['Граната РГД-5','Граната Ф-1'];
    const pockets=[...forbidden,...allowed].map((name,index)=>{
        const template=name==='Кустарная бомба'?{category:'grenade',subcategory:'Ручные',weight:.5,volume:1,
            attributes:{fragile_explosive:true}}:catalog.find(t=>t.name===name);
        assert.ok(template,'Шаблон для проверки растяжек: '+name);
        return {id:'tripwire-choice-'+index,name,category:template.category,subcategory:template.subcategory,
            attributes:template.attributes,quantity:2,weight:template.weight,volume:template.volume};
    });
    pockets.push({name:'Электронные материалы',category:'crafting_material',quantity:2});
    const character=await request(api+'/characters',{name:'Проверка совместимости растяжек',data:{
        skills:{other:{engineering:{base:20},survival:{base:20}}},inventory:{pockets,backpack:[]}}},token);
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.evaluate("document.querySelector('#character-crafting-open').click();true");
    await page.until("document.querySelector('#crafting-modal select[aria-label=Рецепт]')",'Рецепты для проверки гранат');
    const results=[];
    for(const [recipe,count] of [['recipe-m8',1],['recipe-m9',2]]){
        await page.evaluate(`(()=>{const s=document.querySelector('#crafting-modal select[aria-label=Рецепт]');s.value=${JSON.stringify(recipe)};s.dispatchEvent(new Event('change'));return true})()`);
        await page.until(`document.querySelectorAll('#crafting-modal select[aria-label^="Граната "]').length===${count}`,'Выбор гранат '+recipe);
        const options=await page.evaluate("[...document.querySelectorAll('#crafting-modal select[aria-label^=\"Граната \"]')].map(s=>[...s.options].map(o=>o.textContent))");
        for(const names of options){
            assert.deepEqual(names,allowed.map(name=>name+' × 2'),'В каждом списке доступны только разрешённые гранаты');
        }
        results.push({recipe,options});
    }
    fs.writeFileSync(path.join(safeSession.artifacts,'tripwire-choices.json'),JSON.stringify(results,null,2));
    await page.evaluate("document.querySelector('#crafting-modal .block-header button').click();window.closeCharacterSheet?.();true");
}
