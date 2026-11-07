// Рецепт v20, два настоящих броска и подрыв выстрелом в двух клиентах Припяти.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'improvised');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const actor=await request(api+'/characters',{name:'Сапёр с кустарными бомбами',data:{
        skills:{physical:{strength:{base:100},agility:{base:100},shooting:{base:100}},other:{engineering:{base:100},tactics:{base:100}}},
        weapons:[{name:'Проверочная винтовка',range:30,ergonomics:50,ammo:20,damage:10,durability:100,maxDurability:100}],
        inventory:{pockets:[],backpack:[{name:'Металлические детали',category:'crafting_material',quantity:10},
            {name:'Химические материалы',category:'crafting_material',quantity:4},{name:'Вода',category:'consumable',quantity:2}]},
        health:{current:700,max:700,effects:[]}}},token);
    const placed=await request(loc+'/spawn_character',{character_id:actor.id,tile_x:40,tile_y:48},token);
    await request(loc+'/characters/'+actor.id+'/facing',{facing_x:1,facing_y:0},token,'PATCH');
    await request(loc+'/objects',{name:'Смесительный Станок',type:'household_workbench',tile_x:40,tile_y:49,
        properties:{craft_workbench:'Смесительный Станок'}},token);
    const craft='/lobbies/characters/'+actor.id+'/crafting';
    for(let i=0;i<2;i++){
        let fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');
        const started=await request(craft,{operation:'start',operation_id:'make-bomb-'+i,character_revision:fresh.data._revision,recipe_id:'recipe-v20'},token);
        const clock=await request(api,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+30;
        await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
        fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');
        const done=await request(craft,{operation:'finish',operation_id:'finish-bomb-'+i,character_revision:fresh.data._revision,
            job_id:started.result.id,chosen_loss:Array(started.result.quote.failure_loss.chosen).fill(0)},token);assert.ok(done.result.success);
    }
    let fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    const bombs=Object.values(fresh.data.inventory).filter(Array.isArray).flat().filter(item=>item.name==='Кустарная бомба');assert.equal(bombs.length,2);
    for(const [key,value]of Object.entries(fresh.data.inventory))if(Array.isArray(value))fresh.data.inventory[key]=value.filter(item=>item.name!=='Кустарная бомба');
    fresh.data.inventory.pockets.push(...bombs);
    await request('/lobbies/characters/'+actor.id,{data:fresh.data},token,'PUT');
    await request('/auth/register',{username:'bomb-observer',email:'bomb-observer@example.test',password:'synthetic-bomb-secret'},null);
    const login=await request('/auth/login',{username:'bomb-observer',password:'synthetic-bomb-secret'},null),playerToken=login.access_token;
    await request(api+'/join',{},playerToken);
    const observer=await request(api+'/characters',{name:'Наблюдатель взрыва',data:{health:{current:700,max:700,effects:[]}}},playerToken);
    const observed=await request(loc+'/spawn_character',{character_id:observer.id,tile_x:58,tile_y:48},token);
    await request('/lobbies/characters/'+actor.id,{visible_to:[login.user_id]},token,'PUT');
    await request(api+'/weather',{sun:{enabled:true,intensity:1},rain:{enabled:false,intensity:0},fog:{enabled:false,intensity:0},emission:{enabled:false,intensity:0}},token,'PATCH');
    const href=base+api+'/page?location='+location.id;
    const enter=async(client,auth,user)=>{
        const epoch=await client.evaluate('performance.timeOrigin');
        await client.evaluate(`localStorage.setItem('access_token',${JSON.stringify(auth)});localStorage.setItem('user_id',${JSON.stringify(String(user))});true`);
        await client.call('Page.navigate',{url:href});await waitForDocument(client,{href,afterEpoch:epoch,label:'Кустарная бомба в Припяти'});
        await client.until(`window.currentLocationId===${location.id}&&window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
        await client.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');window.mapAudit=(await import('/static/js/mapSceneContext.js')).getMapSceneContext();
            L.setLocationEditMode(false);locationControls.enableDamping=false;locationControls.target.set(45.5,1,48.5);locationControls.object.position.set(48,14,63);locationControls.update();
            window.bombEvents=[];window.bombErrors=[];const originalFetch=window.fetch;
            window.fetch=async(...args)=>{const response=await originalFetch(...args);if(String(args[0]).includes('/combat/action')&&!response.ok)bombErrors.push(await response.clone().json());return response};
            for(const name of ['location_object_created','location_object_deleted','combat_explosion'])socket.on(name,e=>bombEvents.push({name,...e}));return true})()`);
    };
    let protocol,context,peer;
    try{
        await enter(page,token,lobby.gm_id);
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();protocol=await connect(version.webSocketDebuggerUrl);
        context=await protocol.call('Target.createBrowserContext');const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();peer=await connect(tabs.find(t=>t.id===target.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');await waitForDocument(peer,{href:base,label:'Второй клиент бомбы'});await enter(peer,playerToken,login.user_id);
        const actorId=placed.location_character_id,observerId=observed.location_character_id;
        await request(loc+'/combat/start',{location_character_ids:[actorId,observerId],initiator_location_character_id:actorId,initiator_first:true},token);
        await page.until(`L.getCombatState()?.current_character?.location_character_id===${actorId}`,'Ход сапёра');
        const click=async point=>{await page.call('Page.bringToFront');await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point,buttons:0});
            await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',buttons:1,clickCount:1});await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',buttons:0,clickCount:1});};
        const nextRound=async()=>{
            for(let i=0;i<2;i++){const state=await request(loc+'/combat',null,token,'GET');
                await request(loc+'/combat/end_turn',{location_character_id:state.current_location_character_id||state.current_character.location_character_id},token);}
            await page.until(`L.getCombatState()?.current_character?.location_character_id===${actorId}`,'Новый ход сапёра');
        };
        const thrown=[];
        for(let i=0;i<2;i++){
            await page.evaluate(`window.currentLocationCharacterId=${actor.id};window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
            await page.until(`[...document.querySelectorAll('[data-widget-identity]')].some(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(bombs[i].id)})`,'Созданная бомба в инвентаре');
            await page.evaluate(`(()=>{const row=[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(bombs[i].id)});row.querySelector('[data-widget-key=use]').click()})()`);
            await page.until("document.querySelector('#grenade-fuse-modal [data-fuse=normal]')",'Контактная бомба готова');
            assert.ok(await page.evaluate("!document.querySelector('#grenade-fuse-modal [data-fuse=delay]')"),'Задержка обычного запала не предлагается');
            await page.evaluate("document.querySelector('#grenade-fuse-modal [data-fuse=normal]').click();true");
            await page.until("!document.getElementById('grenade-fuse-modal')&&document.getElementById('combat-status-panel')?.textContent.includes('Выбор:')",'Выбор точки броска');
            const point=await page.evaluate(`(()=>{const p=new T.Vector3(${45+i*4+.5},1,48.5).project(locationControls.object),r=mapAudit.canvas.getBoundingClientRect();return{x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
            await click(point);
            await page.until(`(()=>{if(bombErrors.length)throw Error(JSON.stringify(bombErrors));return bombEvents.some(e=>e.name==='location_object_created'&&e.object?.properties?.contents?.some(item=>item.id===${JSON.stringify(bombs[i].id)}))})()`,'Целая невзорвавшаяся бомба появилась на земле');
            const obj=await page.evaluate(`bombEvents.find(e=>e.object?.properties?.contents?.some(item=>item.id===${JSON.stringify(bombs[i].id)})).object`);thrown.push(obj);
            await peer.until(`bombEvents.some(e=>e.object?.id===${obj.id})`,'Второй клиент получил предмет на земле');
            await nextRound();
        }
        await page.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))");
        fs.writeFileSync(path.join(safeSession.artifacts,'improvised-bombs-before.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await request(loc+'/combat/action',{location_character_id:actorId,action_key:'draw_weapon',weapon_index:0},token);
        await page.evaluate(`L.queueCombatActionFromSheet({actorCharacterId:${actor.id},actionKey:'attack',weaponIndex:0,fireMode:'unaimed',shotCount:1,volleyCount:1,actionPoints:2,targetType:'character',source:'sheet'});true`);
        const point=await page.evaluate(`(()=>{let object;mapAudit.scene.traverse(node=>{if(!object&&node.userData?.locationObject?.id===${thrown[0].id})object=node});if(!object)throw Error('Модель бомбы не найдена');const p=new T.Box3().setFromObject(object).getCenter(new T.Vector3()).project(locationControls.object),r=mapAudit.canvas.getBoundingClientRect();return{x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
        await click(point);
        for(const client of [page,peer])await client.until("bombEvents.filter(e=>e.name==='combat_explosion'&&e.explosive?.explosion?.profile==='improvised_bomb').length===2",'Оба подрыва доставлены обоим клиентам');
        for(const client of [page,peer])for(const obj of thrown)await client.until(`bombEvents.some(e=>e.name==='location_object_deleted'&&e.object_id===${obj.id})`,'Бомба удалена на обоих клиентах');
        fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');assert.equal(fresh.data.weapons[0].ammo,19);
        const detail=await request(loc,null,token,'GET');assert.ok(thrown.every(obj=>!detail.objects.some(entry=>entry.id===obj.id)));
        const shot=await page.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(safeSession.artifacts,'improvised-bombs-after.png'),Buffer.from(shot.data,'base64'));
        console.log('Кустарная бомба: два результата настоящего рецепта v20, два броска мышью с отказом запала, целые предметы на земле у обоих клиентов, выстрел по модели и цепочка двух взрывов ровно по одному, одна израсходованная пуля. Копия Припяти/GPU.');
    }finally{peer?.close();if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});protocol?.close();}
};
