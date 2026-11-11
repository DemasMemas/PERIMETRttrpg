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
    await checkStashAndBuckshot({page,request,token,api,loc,lobby,location,safeSession});
    console.log('Ловушки в Припяти: модели, установка мышью, сетевое появление, маскировка, reload, срабатывание и освобождение из сети проверены.');
};

async function checkStashAndBuckshot({page,request,token,api,loc,lobby,location,safeSession}){
    const actor=await request(api+'/characters',{name:'Проверка тайника и картечи',data:{
        basic:{background:{ruleId:'background-av'}},
        features:{specialTraits:[{name:'Мастер маскировки'}]},
        inventory:{pockets:[
            {id:'stash-shovel',name:'Лопата',category:'tool',quantity:1},
            {id:'stash-buckshot',name:'12x70 Картечь',category:'ammo',quantity:12,
                attributes:{caliber:'12x70 Картечь',damage:250}},
        ],backpack:[]},
    }},token);
    const sheet='/lobbies/characters/'+actor.id;
    await request(loc+'/spawn_character',{character_id:actor.id,tile_x:45,tile_y:48},token);
    const crate=await request(loc+'/objects',{name:'Пень',type:'household_stump',tile_x:46,tile_y:48,
        properties:{contents:[{id:'visible-nail',name:'Гвозди',quantity:1}],floor_level:0}},token);
    await page.evaluate(`window.closeCharacterSheet();window.currentLocationCharacterId=${actor.id};
        L.showObjectContextMenu(650,350,${JSON.stringify(crate)});true`);
    const action="[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Устроить тайник'))";
    await page.until(action,'Действие на пне');
    await page.evaluate(`${action}.click();true`);
    await page.until("document.querySelector('#trap-action-modal select')",'Форма маскировки тайника');
    await page.evaluate(`(()=>{
        const modal=document.getElementById('trap-action-modal'), select=modal.querySelector('select');
        select.selectedIndex=1;
        [...modal.querySelectorAll('button')].find(b=>b.textContent==='Устроить тайник').click();
        return true;
    })()`);
    await page.until("!document.getElementById('trap-action-modal')",'Тайник в пне готов');
    const hidden=(await request(loc,null,token,'GET')).objects.find(obj=>obj.id===crate.id);
    const masked=(await request(sheet,null,token,'GET')).data.crafting.trap_receipts.at(-1).result;
    assert.equal(hidden.properties.stash_compartment.dc,masked.check.total);
    assert.equal(hidden.properties.contents[0].id,'visible-nail');
    assert.deepEqual(hidden.properties.stash_compartment.contents,[]);
    assert.equal(masked.check.tool_bonus,3);
    assert.equal(masked.check.masking_bonus,4);

    await page.evaluate(`import('/static/js/crafting.js').then(m=>m.showCrafting({characterId:${actor.id},lobbyId:${lobby.id}})).then(()=>true)`);
    await page.until("document.querySelector('#crafting-modal [data-crafting-tab=other]')",'Работы Утиного стрелка');
    await page.evaluate("document.querySelector('#crafting-modal [data-crafting-tab=other]').click();true");
    const button=label=>`[...document.querySelectorAll('#crafting-modal button')].find(b=>b.textContent===${JSON.stringify(label)})`;
    await page.evaluate(`document.querySelector('[aria-label="Количество картечи для доработки"]').value=10;
        ${button('Начать доработку')}.click();true`);
    const collect=button('Забрать картечь');
    await page.until(`${collect}?.disabled`,'Партия зарезервирована и ожидает час');
    const clock=await request(api,null,token,'GET');
    const next=clock.game_day*1440+clock.game_time_minutes+60;
    await request(api+'/time',{game_day:Math.floor(next/1440),game_time_minutes:next%1440},token,'PATCH');
    await page.evaluate(`${button('Обновить')}.click();true`);
    await page.until(`${collect} && !${collect}.disabled`,'Картечь можно забрать');
    await page.evaluate(`${collect}.click();true`);
    await page.until(`!${collect} && document.querySelector('#crafting-modal [data-crafting-tab=other]')`,'Работа завершена');
    const items=(await request(sheet,null,token,'GET')).data.inventory.pockets;
    assert.equal(items.find(item=>item.id==='stash-buckshot').quantity,2);
    assert.equal(items.find(item=>item.attributes?.tuned_buckshot).quantity,10);
    await page.evaluate("document.getElementById('crafting-modal').closeCrafting();true");
    const visualTypes=['household_hollow_tree','household_safe_small','household_safe_large'];
    const previews=[];
    for(const [index,type] of visualTypes.entries()){
        previews.push(await request(loc+'/objects',{name:['Дупло','Маленький сейф','Большой сейф'][index],type,
            tile_x:47+index,tile_y:48,properties:{contents:[],...(index?{locked:true,lock_difficulty:index===1?8:13}:{})}},token));
    }
    await page.until(`${JSON.stringify(previews.map(obj=>obj.id))}.every(id=>L.sceneGraphicsForTest().roots.some(root=>root.userData.locationObjectId===id))`,
        'Дупло и два сейфа загружены в Припяти');
    for(const [i,x,z] of [[0,51,54],[1,42,52]]){
        await page.evaluate(`(()=>{const {controls,camera}=L.sceneGraphicsForTest();
            controls.target.set(48,1,48.5);camera.position.set(${x},4,${z});controls.update();return true;})()`);
        const screenshot=await page.call('Page.captureScreenshot',{format:'png'});
        fs.writeFileSync(path.join(safeSession.artifacts,`stash-assets-${i}.png`),Buffer.from(screenshot.data,'base64'));
    }
    for(const safe of previews.slice(1)){
        await page.evaluate(`L.showObjectContextMenu(650,350,${JSON.stringify(safe)});true`);
        assert.equal(await page.evaluate(`Boolean(${action})`),false);
    }
    await request(api+'/chunks?min_chunk_x=0&max_chunk_x=0&min_chunk_y=0&max_chunk_y=0',null,token,'GET');
    await request(api+'/chunks/0/0/tile/2/2',{terrain:'grass'},token,'PATCH');
    const group=await request(api+'/world-groups',{name:'Группа проверки тайников',tile_x:2,tile_y:2},token);
    await request(api+'/world-groups/'+group.id+'/members',{character_ids:[actor.id]},token,'PATCH');
    await page.evaluate(`import('/static/js/worldStashDialog.js').then(m=>m.showWorldStashes(${lobby.id},{
        id:${group.id},tile_x:2,tile_y:2,members:[{id:${actor.id},name:${JSON.stringify(actor.name)}}]})).then(()=>true)`);
    const worldButton=label=>`[...document.querySelectorAll('#world-stash-dialog button')].find(button=>button.textContent===${JSON.stringify(label)})`;
    await page.until(worldButton('Прикопать тайник'),'Прикапывание через форму группы');
    await page.evaluate(`${worldButton('Прикопать тайник')}.click();true`);
    await page.until(worldButton('Положить в тайник'),'Мировой тайник доступен для вещей');
    await page.evaluate(`${worldButton('Положить в тайник')}.click();true`);
    await page.until(worldButton('Забрать из тайника'),'Предмет сохранён в тайнике на мировой клетке');
    await page.evaluate(`${worldButton('Забрать из тайника')}.click();true`);
    await page.until(`!${worldButton('Забрать из тайника')} && ${worldButton('Положить в тайник')}`,'Предмет забран из мирового тайника');
    await page.evaluate(`${worldButton('Закрыть')}.click();true`);
    console.log('В Припяти: тайник в пне отделён от открытых вещей, дупло и два сейфа в сцене. Мировой тайник принял и вернул предмет. Картечь доработана через интерфейс.');
}

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
