const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture}=require('./durable_reload.browser.cjs');

module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'terrain');
    assert.ok(safeSession.verified);
    const {fixture}=loadFixture(),api='/lobbies/'+lobby.id;
    const location=await request(api+'/locations',{...fixture,objects:undefined,
        name:'Припять — линии видимости',world_tile_x:9,world_tile_z:7},token);
    const loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    await page.evaluate('(async()=>{window.L=await import("/static/js/locationScene.js");await enterLocation('+location.id+');return true})()');
    await page.until('document.getElementById("scene-loading-screen").classList.contains("is-hidden")','Копия Припяти готова');
    const routes=await page.evaluate(`(()=>{
        const candidates=[[33,20,39,20],[32,32,38,32],[20,2,26,2],[35,35,41,35]];
        for(let z=2;z<68;z+=8)for(let x=2;x<60;x+=8)candidates.push([x,z,x+6,z]);
        return candidates.filter(([x,z,tx,tz])=>L.hasLineOfSight(x,z,tx,tz)).slice(0,10);
    })()`);
    assert.ok(routes.length,'На настоящей Припяти найден открытый луч');
    const password='synthetic-terrain-observer';
    await request('/auth/register',{username:'terrain-observer',email:'terrain-observer@example.test',password});
    const observer=await request('/auth/login',{username:'terrain-observer',password});
    await request(api+'/join',{},observer.access_token);
    const version=await(await fetch('http://127.0.0.1:'+debugPort+'/json/version')).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    const {browserContextId}=await protocol.call('Target.createBrowserContext');
    let peer;
    try{
        const {targetId}=await protocol.call('Target.createTarget',{url:base,browserContextId});
        const tabs=await(await fetch('http://127.0.0.1:'+debugPort+'/json')).json();
        peer=await connect(tabs.find(t=>t.id===targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.until('document.readyState==="complete"','Окно наблюдателя');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(observer.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(observer.user_id))});true`);
        await peer.call('Page.navigate',{url:base+api+'/page?location='+loc.split('/').at(-1)});
        await peer.until(`window.isGM===false&&window.isLocationActive&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Наблюдатель в копии Припяти');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');return true})()`);
        let route;
        for(const candidate of routes){
            if(await peer.evaluate(`L.hasLineOfSight(...${JSON.stringify(candidate)})`)){route=candidate;break;}
        }
        assert.ok(route,'Оба клиента видят исходный открытый луч');
        const [x,z,tx,tz]=route;
        const rifle={id:'pripyat-los-rifle',name:'Проверочная винтовка',category:'weapon',weight:1,ergonomics:0,accuracy:0,range:20,
            durability:100,maxDurability:100,ammo:3,fireModes:{single_shot_options:[1]},
            installedMagazine:{id:'pripyat-los-mag',ammo:[{id:'pripyat-los-ammo',name:'5.56x45',quantity:3,category:'ammo',
                attributes:{caliber:'5.56x45',damage:10,armor_piercing:0}}]}};
        const shooter=await request(api+'/characters',{name:'№105 Стрелок Припяти',data:{health:{current:700,max:700,effects:[]},
            combat:{action_points:12},
            skills:{physical:{shooting:{base:20,bonus:0},strength:{base:10,bonus:0}}},weapons:[rifle]}},token);
        const target=await request(api+'/characters',{name:'№105 Цель Припяти',data:{health:{current:700,max:700,effects:[]}}},token);
        const placed=await request(loc+'/spawn_character',{character_id:shooter.id,tile_x:x,tile_y:z,assign_to_user_id:observer.user_id},token);
        const targetPlaced=await request(loc+'/spawn_character',{character_id:target.id,tile_x:tx,tile_y:tz},token);
        for(const client of [page,peer])await client.until(`(()=>{try{return L.characterPose(${shooter.id}).x===${x}&&L.characterPose(${target.id}).x===${tx}}catch{return false}})()`,'Персонажи размещены на обоих клиентах');
        for(const client of [page,peer])await client.evaluate(`window.terrainEvents=[];socket.on('location_tiles_updated',e=>terrainEvents.push(e));true`);
        const original=structuredClone(fixture.tiles_data),hill=structuredClone(original),mid=Math.floor((x+tx)/2);
        for(let row=z-1;row<=z+1;row++)hill[row][mid]={...hill[row][mid],height:7};
        await request(loc,{tiles_data:hill},token,'PUT');
        for(const client of [page,peer]){
            await client.until(`terrainEvents.length>0&&!L.hasLineOfSight(${x},${z},${tx},${tz})`,'Холм перекрыл видимость по сетевому обновлению');
            assert.equal(await client.evaluate(`L.hasLineOfSight(${x},${z},${tx},${tz},12,12)`),true);
        }
        await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id,targetPlaced.location_character_id],
            initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
        for(const action of [{action_key:'draw_weapon',weapon_index:0},{action_key:'change_facing',facing_x:1,facing_y:0,payment:'action'},
            {action_key:'aim',weapon_index:0,target_character_id:target.id}])
            await request(loc+'/combat/action',{location_character_id:placed.location_character_id,...action},token);
        const before=await request(loc+'/combat',null,token,'GET');
        assert.ok(before.current_character.action_points_current>=6,'Стрелок может полностью оплатить выстрел');
        const weaponBefore=(await request('/lobbies/characters/'+shooter.id,null,token,'GET')).data.weapons;
        const healthBefore=(await request('/lobbies/characters/'+target.id,null,token,'GET')).data.health;
        const attack=()=>page.evaluate(`(async()=>{
            const response=await fetch(${JSON.stringify(loc+'/combat/action')},{method:'POST',headers:{'Content-Type':'application/json',
                Authorization:'Bearer '+localStorage.getItem('access_token')},body:JSON.stringify({location_character_id:${placed.location_character_id},
                action_key:'attack',weapon_index:0,target_character_id:${target.id},fire_mode:'aimed',target_zone:'chest',shot_count:1,volley_count:1,
                action_points:6,pending_action_id:'pripyat-terrain-check'})});return {status:response.status,body:await response.json()};
        })()`);
        const blocked=await attack();assert.equal(blocked.status,400);assert.match(JSON.stringify(blocked.body),/закрыта укрытием/);
        await page.call('Page.bringToFront');
        const unaimedButton = `document.querySelector('#sheet-tab-equipment button[onclick^="useWeaponFromEquipment"][onclick*="unaimed"]:not(:disabled)')`;
        await page.evaluate(`L.openCharacterAttackSelection(${shooter.id}).then(()=>true)`);
        await page.until(unaimedButton,'ГМ видит кнопку неприцельного выстрела');
        await page.evaluate(`${unaimedButton}.click();true`);
        await page.until("[...document.querySelectorAll('.notification')].some(n=>n.textContent.includes('выберите цель на сцене'))",
            'Неприцельная стрельба ожидает цель на сцене');
        const targetPoint=await page.evaluate(`(()=>{
            const {camera,controls,renderer,characters}=L.sceneGraphicsForTest();
            const model=characters.get(${target.id}).model;
            const position=model.position.clone().add(new threeModule.Vector3(0,.85,0));
            controls.target.copy(position);
            camera.position.copy(position).add(new threeModule.Vector3(4,8,7));
            controls.update();camera.updateMatrixWorld();
            const point=position.project(camera),rect=renderer.domElement.getBoundingClientRect();
            return{x:rect.left+(point.x+1)*rect.width/2,y:rect.top+(1-point.y)*rect.height/2};
        })()`);
        for(const type of ['mouseMoved','mousePressed','mouseReleased']){
            await page.call('Input.dispatchMouseEvent',{type,...targetPoint,button:type==='mouseMoved'?'none':'left',
                buttons:type==='mousePressed'?1:0,clickCount:1});
        }
        await page.until("[...document.querySelectorAll('.notification')].some(n=>n.textContent.includes('не видна стрелку'))",
            'Неприцельный выстрел ГМа отклонён до броска');
        assert.equal(await page.evaluate("[...document.querySelectorAll('.notification')].some(n=>n.textContent.includes('Итоговый урон'))"),false);
        await page.evaluate('L.clearPendingCombatAction();true');
        const after=await request(loc+'/combat',null,token,'GET');
        assert.equal(after.current_character.action_points_current,before.current_character.action_points_current);
        assert.deepEqual((await request('/lobbies/characters/'+shooter.id,null,token,'GET')).data.weapons,weaponBefore);
        assert.deepEqual((await request('/lobbies/characters/'+target.id,null,token,'GET')).data.health,healthBefore);
        await page.evaluate(`locationControls.target.set(${mid},3,${z});locationControls.object.position.set(${mid+13},18,${z+20});locationControls.update();true`);
        fs.writeFileSync(path.join(safeSession.artifacts,'pripyat-terrain-hill.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await request(loc,{tiles_data:original},token,'PUT');
        for(const client of [page,peer])await client.until(`terrainEvents.length>=2&&L.hasLineOfSight(${x},${z},${tx},${tz})`,'После удаления холма луч открыт на обоих клиентах');
        const allowed=await attack();assert.equal(allowed.status,200);
        assert.ok(!allowed.body.pending_action,'Выстрел завершён в текущем ходе');
        assert.equal(allowed.body.attack.shot_count,1);
        assert.equal(allowed.body.attack.action_points,6);
        assert.equal(allowed.body.attack.ammo_remaining,2);
        const afterShot=await request(loc+'/combat',null,token,'GET');
        const finalAp=before.current_character.action_points_current-6;
        assert.equal(afterShot.current_character.action_points_current,finalAp);
        for(const client of [page,peer])await client.until(`L.getCombatState()?.characters?.find(c=>c.character_id===${shooter.id})?.action_points_current===${finalAp}`,'Списание ОД после выстрела видно на обоих клиентах');
        await request(loc+'/combat/end',{},token);
        await page.call('Page.bringToFront');
        await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
        const performance=await page.evaluate(`(async()=>{
            locationControls.target.set(36,1,36);
            locationControls.object.position.set(80,42,105);
            locationControls.update();
            return L.measureTerrainVisibilityForTest(${shooter.id});
        })()`);
        assert.deepEqual(performance.viewport,[1920,1080]);
        assert.ok(performance.runs.filter(run=>run.refresh).every(run=>run.rays.median>0),'Замерен активный обзор');
        fs.writeFileSync(path.join(safeSession.artifacts,'pripyat-terrain-performance.json'),JSON.stringify(performance,null,2));
        const wall = await request(loc+'/objects', {
            name: 'Стена для проверки проёмов',
            type: 'wall', tile_x: mid, tile_y: z,
            properties: {
                building_id: 'terrain-los-openings', level: 0, rotation: Math.PI/2,
                dimensions: {width: 3, depth: .2, height: 2.4},
            },
        }, token);
        const checkVisibility = async (expected, label) => {
            for (const client of [page, peer]) {
                await client.until(`L.hasLineOfSight(${x},${z},${tx},${tz})===${expected}`, label);
            }
        };
        await checkVisibility(false, 'Сплошная стена перекрывает луч на обоих клиентах');
        const window = await request(loc+'/objects', {
            name: 'Окно для проверки видимости',
            type: 'window', tile_x: mid, tile_y: z,
            properties: {
                building_id: 'terrain-los-openings', level: 0, rotation: Math.PI/2,
                elevation: 1, is_open: true,
                dimensions: {width: 1.2, depth: .14, height: 1},
            },
        }, token);
        await checkVisibility(true, 'Открытое окно пропускает луч на обоих клиентах');
        await request(api+'/locations/objects/'+window.id, {properties: {is_open: false}}, token, 'PATCH');
        await checkVisibility(false, 'Закрытое окно снова перекрывает луч на обоих клиентах');
        await request(api+'/locations/objects/'+window.id, {properties: {is_open: true}}, token, 'PATCH');
        await checkVisibility(true, 'Повторное открытие окна обновляет видимость у обоих клиентов');
        await request(api+'/locations/objects/'+window.id, null, token, 'DELETE');
        await checkVisibility(false, 'Удаление окна восстанавливает сплошную стену');
        const door = await request(loc+'/objects', {
            name: 'Дверь для проверки видимости',
            type: 'door', tile_x: mid, tile_y: z,
            properties: {
                building_id: 'terrain-los-openings', level: 0, rotation: Math.PI/2,
                is_open: true, dimensions: {width: .9, depth: .18, height: 2},
            },
        }, token);
        await checkVisibility(true, 'Открытая дверь пропускает луч на обоих клиентах');
        await request(api+'/locations/objects/'+door.id, {properties: {is_open: false}}, token, 'PATCH');
        await checkVisibility(false, 'Закрытая дверь перекрывает луч на обоих клиентах');
        await request(api+'/locations/objects/'+wall.id, null, token, 'DELETE');
        const report={scene:'Копия Припяти',route,objects:fixture.objects.length,twoClients:true,liveTerrainUpdates:true,aboveHill:true,
            blockedStatus:blocked.status,gmUnaimedBlockedFromSheet:true,noApAmmoHpSpent:true,clearStatus:allowed.status,
            liveOpenings: {window: true, door: true, openCloseReopen: true},
            completedShot:true,spentActionPoints:6,ammoRemaining:2,
            scope:'Завершённый прицельный выстрел после удаления холма и видимость проёмов на двух клиентах'};
        fs.writeFileSync(path.join(safeSession.artifacts,'pripyat-terrain-los.json'),JSON.stringify(report,null,2));
        console.log(JSON.stringify(report));
        await page.evaluate('exitLocation();true');
    }finally{
        if(peer)await peer.close();await protocol.call('Target.disposeBrowserContext',{browserContextId});await protocol.close();
    }
};
