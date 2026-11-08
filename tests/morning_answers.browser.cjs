// Уточнения автора и найденные в таблице ошибки: настоящие API и два клиента.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
const copy=x=>JSON.parse(JSON.stringify(x));
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'morning-answers');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    await request('/auth/register',{username:'morning-observer',email:'morning-observer@example.test',password:'synthetic-morning-password'});
    const observer=await request('/auth/login',{username:'morning-observer',password:'synthetic-morning-password'});await request(api+'/join',{},observer.access_token);
    const allow=character=>request('/lobbies/characters/'+character.id+'/visibility',{visible_to:[observer.user_id],editable_to:[]},token,'PUT');
    const health=()=>({current:300,max:700,painLevel:0,stress:0,exhaustion:0,infection:1,temperature:38,effects:[]});
    const read=id=>request('/lobbies/characters/'+id,null,token,'GET');
    const rows=[];
    for(const [name,description] of [
        ['Стимулятор Скала-Н','Ампула. Действует 3 хода.'],
        ['Стимулятор Орёл','Ампула. Действует 10 ходов.'],
        ['Военный стимулятор ПЧеЛа','Ампула. Действует 10 ходов.'],
        ['Жаропонижающее средство Мороз','Ампула. Понижает температуру тела на 1 градус. Работает 4 хода.'],
    ]){
        const t=await request(api+'/templates',{name,category:'consumable',description,weight:.1,volume:.1,
            attributes:{catalog_name:name,raw_description:description,consumable:{direct:{application_form:'injectable'},effects:[]}}},token);
        const item={id:'morning-'+t.id,templateId:t.id+1000000,name,category:'consumable',quantity:1,attributes:t.attributes};
        const character=await request(api+'/characters',{name:'Проверка '+name,data:{health:health(),skills:{other:{medicine:{base:20}}},inventory:{pockets:[item],backpack:[]}}},token);
        await allow(character);rows.push({name,character,item});
    }
    const source=JSON.parse(fs.readFileSync(path.join(__dirname,'../.private-testing/reports/catalog-sweep-2026-11-04/source.json'),'utf8'));
    const ammo=source.equipment.find(i=>i.category==='ammo'&&/9[хx×]18/.test(i.name));assert.ok(ammo);
    const rifle={id:'morning-gun',name:'Ствол проверки',category:'weapon',subcategory:'Пистолет',ammo:20,durability:100,maxDurability:100,
        attributes:{damage:50,range:30,accuracy:100,ergonomics:100,fire_rate:20,noise:0,min_strength:0,fixedMagazine:true,magazine_size:20},
        fixedAmmo:[{...copy(ammo),id:'morning-ammo',quantity:20}]};
    const actor=await request(api+'/characters',{name:'Стрелок утренних проверок',data:{health:health(),combat:{action_points:100},
        skills:{physical:{strength:{base:20},shooting:{base:20},accuracy:{base:20},will:{base:20}}},weapons:[rifle]}},token);
    await request(loc+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48},token);
    const armorRows=[];
    for(const raw of source.equipment.filter(i=>i.name==='Костюм Химзащиты'||i.category==='helmet'&&i.attributes.requires_filter)){
        const item={...copy(raw),id:'morning-armor-'+armorRows.length,durability:raw.attributes.max_durability,maxDurability:raw.attributes.max_durability};
        const character=await request(api+'/characters',{name:'Мишень '+raw.name,data:{health:health(),equipment:{[raw.category]:item}}},token);
        await allow(character);armorRows.push({name:raw.name,character,item});
    }
    let protocol,context,peer;
    try{
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();protocol=await connect(version.webSocketDebuggerUrl);
        context=await protocol.call('Target.createBrowserContext');const t=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();peer=await connect(tabs.find(i=>i.id===t.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');await waitForDocument(peer,{href:base,label:'Наблюдатель'});
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(observer.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(observer.user_id))});true`);
        for(const client of [page,peer]){
            const href=base+api+'/page?location='+location.id,epoch=await client.evaluate('performance.timeOrigin');
            await client.call('Page.navigate',{url:href});await waitForDocument(client,{href,afterEpoch:epoch,label:'Припять: утренние ответы'});
            await client.until(`window.socket?.connected&&window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
            await client.evaluate(`window.__morningUpdates=[];socket.on('character_data_updated',e=>window.__morningUpdates.push(e));true`);
        }
        for(const row of rows){
            for(const client of [page,peer]){
                const ack=await client.evaluate(`new Promise(resolve=>socket.emit('join_character',{token:localStorage.getItem('access_token'),character_id:${row.character.id}},resolve))`);assert.ok(ack.ok);
            }
            const current=await read(row.character.id),item=current.data.inventory.pockets[0];
            await request('/lobbies/characters/'+row.character.id+'/medical-procedure',{operation_id:'morning-dose-'+row.character.id,target_character_id:row.character.id,
                source:{path:['inventory','pockets',0],snapshot:item},application:{kind:'self',actionPoints:0}},token);
            row.beforeClock=(await read(row.character.id)).data.health;
        }
        assert.equal(rows[0].beforeClock.current,500);assert.equal(rows[0].beforeClock.max,900);
        assert.equal(rows[1].beforeClock.exhaustion,1);assert.equal(rows[2].beforeClock.exhaustion,1);assert.equal(rows[3].beforeClock.temperature,38);
        await request(api+'/characters/time-active',{character_ids:rows.map(r=>r.character.id)},token,'PATCH');
        const clock=await request(api,null,token,'GET');
        await request(api+'/time',{game_day:clock.game_day,game_time_minutes:clock.game_time_minutes+2},token,'PATCH');
        for(const row of rows)row.afterClock=(await read(row.character.id)).data.health;
        assert.equal(rows[0].afterClock.current,200);assert.equal(rows[0].afterClock.max,700);
        assert.equal(rows[1].afterClock.exhaustion,1);assert.equal(rows[2].afterClock.exhaustion,1);assert.equal(rows[3].afterClock.temperature,34);
        for(const client of [page,peer])await client.until(`window.__morningUpdates.some(e=>e.character_id===${rows[0].character.id}&&e.updates?.data?.health?.current===200)&&window.__morningUpdates.some(e=>e.character_id===${rows[3].character.id}&&e.updates?.data?.health?.temperature===34)`,'Эффекты времени доставлены обоим клиентам');
        await request(api+'/characters/time-active',{character_ids:[]},token,'PATCH');
        const combat=loc+'/combat';let state=await request(combat,null,token,'GET');const actorId=state.characters.find(c=>c.character_id===actor.id).location_character_id;
        await request(combat+'/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        const action=body=>request(combat+'/action',{location_character_id:actorId,...body},token);
        await action({action_key:'draw_weapon',weapon_index:0});
        for(const row of armorRows){
            for(const client of [page,peer]){
                const ack=await client.evaluate(`new Promise(resolve=>socket.emit('join_character',{token:localStorage.getItem('access_token'),character_id:${row.character.id}},resolve))`);assert.ok(ack.ok);
            }
            await request(loc+'/spawn_character',{character_id:row.character.id,tile_x:41,tile_y:50},token);
            await action({action_key:'aim',weapon_index:0,target_character_id:row.character.id});
            const result=await action({action_key:'attack',weapon_index:0,fire_mode:'aimed',shot_count:1,action_points:2,
                target_character_id:row.character.id,target_zone:row.item.category==='helmet'?'head':'chest'});
            row.damage=result.attack.results[0].armor_damage;assert.equal(row.damage.reduce((s,i)=>s+i.damage,0),50,row.name);
            const data=(await read(row.character.id)).data;row.after=data.equipment[row.item.category];
            assert.equal(row.after.durability,row.item.durability);assert.ok(row.after.currentStageDurability<row.after.stageDurability);
            for(const client of [page,peer])await client.until(`window.__morningUpdates.some(e=>e.character_id===${row.character.id}&&e.updates?.data?.equipment?.${row.item.category}?.currentStageDurability===${row.after.currentStageDurability})`,'Износ пришёл обоим клиентам');
            await request(loc+'/characters/'+row.character.id,null,token,'DELETE');
        }
        await request(combat+'/end',{},token);
        const armed=await read(actor.id);
        armed.data.weapons[0].attributes.noise=40;
        armed.data.weapons[0].fixedAmmo=[{id:'morning-flash',name:'12x70 Светошумовой',category:'ammo',quantity:2,
            attributes:{caliber:'12x70',ammo_variant:'flashbang',damage:0,armor_piercing:0}}];
        armed.data.weapons[0].ammo=2;
        await request('/lobbies/characters/'+actor.id,{data:armed.data},token,'PUT');
        await request(loc+'/characters/'+actor.id+'/facing',{facing_x:0,facing_y:1},token,'PATCH');
        const flashPeople=[];
        for(const [name,x,y,fx,fy] of [['Прямая вспышка',41,50,0,-1],['Край конуса',42,49,-1,-1],['Сбоку от стрелка',42,48,-1,0]]){
            const character=await request(api+'/characters',{name,data:{health:health()}},token);await allow(character);
            await request(loc+'/spawn_character',{character_id:character.id,tile_x:x,tile_y:y},token);
            await request(loc+'/characters/'+character.id+'/facing',{facing_x:fx,facing_y:fy},token,'PATCH');flashPeople.push(character);
            for(const client of [page,peer]){
                const ack=await client.evaluate(`new Promise(resolve=>socket.emit('join_character',{token:localStorage.getItem('access_token'),character_id:${character.id}},resolve))`);assert.ok(ack.ok);
            }
        }
        await request(combat+'/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        state=await request(combat,null,token,'GET');if(state.current_character.drawn_weapon_index!==0)await action({action_key:'draw_weapon',weapon_index:0});
        const flashShot=await action({action_key:'attack',weapon_index:0,fire_mode:'unaimed',shot_count:1,action_points:1,target_character_id:flashPeople[0].id});
        assert.equal(flashShot.attack.noise,15);assert.equal(flashShot.attack.results[0].roll,null);
        for(const [index,character] of flashPeople.entries()){
            const h=(await read(character.id)).data.health;const blind=h.effects.find(e=>e.type==='blindness');
            assert.equal(Boolean(blind&&blind.value>0),index!==2,character.name);assert.equal(h.current,300);
        }
        const noise=flashShot.attack.noise_targets.find(r=>r.character_id===actor.id);assert.equal(noise.noise_received,7.5);
        for(const client of [page,peer])await client.until(`window.__morningUpdates.some(e=>e.character_id===${flashPeople[1].id}&&e.updates?.data?.health?.effects?.some(v=>v.type==='blindness'&&v.value>0))`,'Соседняя цель получила вспышку на обоих клиентах');
        await request(combat+'/end',{},token);
        await allow(actor);
        for(const client of [page,peer]){
            await client.until(`window.locationCombatState?.status!=='active'`,'Бой завершён');
            await client.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
            await client.until(`document.querySelector('[data-weapon-grip-label="0"]')?.textContent.includes('двумя руками')`,'Базовый двуручный хват виден');
        }
        const clickGrip=async()=>{
            await page.call('Page.bringToFront');
            const point=await page.evaluate(`(async()=>{const e=document.querySelector('[data-weapon-grip-change="0"]');e.scrollIntoView({block:'center',behavior:'instant'});await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const b=e.getBoundingClientRect();return{x:b.x+b.width/2,y:b.y+b.height/2}})()`);
            for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
        };
        await clickGrip();
        for(const client of [page,peer])await client.until(`document.querySelector('[data-weapon-grip-label="0"]')?.textContent.includes('правая рука')`,'Правая рука доставлена двум листам');
        await request(combat+'/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        state=await request(combat,null,token,'GET');if(state.current_character.drawn_weapon_index!==0)await action({action_key:'draw_weapon',weapon_index:0});
        for(const client of [page,peer])await client.until(`window.locationCombatState?.status==='active'&&window.locationCombatState.current_character?.drawn_weapon_index===0`,'Оружие взято в начале боя');
        const beforeGrip=await request(combat,null,token,'GET');await clickGrip();
        for(const client of [page,peer])await client.until(`window.locationCombatState.current_character?.weapon_grip_hands===2&&document.querySelector('[data-weapon-grip-label="0"]')?.textContent.includes('двумя руками')`,'Двуручный хват и боевое состояние синхронизированы');
        const afterGrip=await request(combat,null,token,'GET');
        assert.equal(afterGrip.current_character.action_points_current,beforeGrip.current_character.action_points_current-1);
        const persisted=await read(actor.id);
        const denied=await fetch(base+'/lobbies/characters/'+actor.id+'/weapon-grip',{method:'POST',headers:{Authorization:'Bearer '+observer.access_token,'Content-Type':'application/json'},body:JSON.stringify({hands:1,weapon_index:0,character_revision:persisted.data._revision})});
        assert.equal(denied.status,403);
        await clickGrip();
        for(const client of [page,peer])await client.until(`window.locationCombatState.current_character?.weapon_grip_hands===1&&document.querySelector('[data-weapon-grip-label="0"]')?.textContent.includes('правая рука')`,'Возврат к правой руке за ещё 1 ОД');
        const afterOne=await request(combat,null,token,'GET');assert.equal(afterOne.current_character.action_points_current,beforeGrip.current_character.action_points_current-2);
        fs.writeFileSync(path.join(safeSession.artifacts,'weapon-grip-one-hand.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        const epoch=await page.evaluate('performance.timeOrigin');await page.call('Page.reload');
        await waitForDocument(page,{href:base+api+'/page?location='+location.id,afterEpoch:epoch,label:'Перезагрузка хвата'});
        await page.until('typeof window.openCharacterSheet===\'function\'','Лист после перезагрузки');
        await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
        await page.until(`document.querySelector('[data-weapon-grip-label="0"]')?.textContent.includes('правая рука')`,'Правая рука сохранена после перезагрузки');
        // Тушение: обычная бутылка, реальное доставание, доступные цели и оформление.
        const watered=await read(actor.id);
        watered.data.inventory={pockets:[{id:'water-native',name:'Вода',category:'consumable',volume:2,
            weight:1,uses:3,maxUses:3,quantity:1,description:'-1 радиации -1/2 Уровня истощения -10 Опьянения 3 Использования.',
            attributes:{uses:3,consumable:{direct:{radiation_delta:-1,intoxication_delta:-10,exhaustion_delta:-.5,
                uses:3,satisfy_water:true,water_source:true},effects:[{type:'radiation',value:-1,tick:'manual'},
                {type:'intoxication',value:-10,tick:'manual'},{type:'exhaustion',value:-.5,tick:'manual'}]}}}],backpack:[]};
        watered.data.health.effects.push({id:'burn-native',type:'burning',name:'Горение',remaining:8,tick:'round_end',active:true});
        await request('/lobbies/characters/'+actor.id,{data:watered.data},token,'PUT');
        await page.until(`window.locationCombatState?.characters?.find(c=>c.character_id===${actor.id})?.effects?.some(e=>e.type==='burning'&&e.remaining===8)`,'Боевой снимок после подготовки воды и горения');
        await page.evaluate('window.closeCharacterSheet()');
        const clickElement=async selector=>{
            const point=await page.evaluate(`(async()=>{for(let i=0;i<120;i++){const e=document.querySelector(${JSON.stringify(selector)});if(e&&!e.disabled&&e.getClientRects().length){e.scrollIntoView({block:'center',behavior:'instant'});const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}}await new Promise(resolve=>setTimeout(resolve,100))}throw Error('Кнопка не появилась: '+${JSON.stringify(selector)})})()`);
            for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
        };
        const openWheel=async()=>{
            const point=await page.evaluate(`(async()=>{const T=await import('three'),c=window.locationControls,a=window.locationCombatState.current_character;
                const target=new T.Vector3(a.x+.5,(a.height||1)+.8,a.y+.5),delta=target.clone().sub(c.target);
                c.object.position.add(delta);c.target.copy(target);c.object.zoom=3;c.object.updateProjectionMatrix();c.update();c.object.updateMatrixWorld(true);
                const p=target.project(c.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
            await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
            for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:'right',buttons:type==='mousePressed'?2:0,clickCount:1});
            await page.until(`Array.from(document.querySelectorAll('.combat-radial-action')).some(e=>e.textContent.includes('Тушение огня'))`,'Колесо персонажа');
        };
        const selectWheel=async text=>{
            await page.evaluate(`Array.from(document.querySelectorAll('.combat-radial-action')).find(e=>e.textContent.includes(${JSON.stringify(text)})).dataset.nativeSelected='true'`);
            await clickElement('[data-native-selected="true"]');
        };
        await openWheel();await selectWheel('Тушение огня');
        await page.until(`document.querySelector('#combat-fire-water-menu .fire-water-items button')?.textContent.includes('1 л')`,'Литраж и цена бутылки');
        let waterMenu=await page.evaluate(`(()=>{const s=document.querySelector('.fire-water-target');return{text:document.querySelector('#combat-fire-water-menu').innerText,
            targets:Array.from(s.options,e=>Number(e.value)),background:getComputedStyle(s).backgroundColor,radius:getComputedStyle(s).borderRadius}})()`);
        assert.ok(waterMenu.text.includes('Карманы · 1 ОД'));assert.ok(!waterMenu.targets.includes(armorRows[0].character.id));
        assert.notEqual(waterMenu.background,'rgb(255, 255, 255)');assert.ok(parseFloat(waterMenu.radius)>=8);
        fs.writeFileSync(path.join(safeSession.artifacts,'extinguishing-menu.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        const waterBefore=(await request(combat,null,token,'GET')).current_character.action_points_current;
        await clickElement('.fire-water-items button');
        await page.until('document.querySelector(".fire-pour")&&!document.querySelector(".fire-pour").disabled','Бутылка взята, меню осталось открытым');
        assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,waterBefore-1);
        assert.equal((await read(actor.id)).data.health.combatMeta.heldWaterItem.litres,1);
        assert.ok(await page.evaluate('document.getElementById("combat-fire-water-menu")?.isConnected'));
        await clickElement('.fire-pour');await page.until('!document.getElementById("combat-fire-water-menu")','Обливание завершено');
        assert.equal((await read(actor.id)).data.health.effects.find(e=>e.type==='burning').remaining,6);
        assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,waterBefore-3);
        await openWheel();await selectWheel('Ближний бой');await page.until('document.querySelector(".melee-block-arm")','Ближний бой открыт');
        assert.ok(parseFloat(await page.evaluate('getComputedStyle(document.querySelector(".melee-block-arm")).borderRadius'))>=8);
        fs.writeFileSync(path.join(safeSession.artifacts,'melee-select.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await clickElement('.melee-menu-close');
        const torchData=await read(actor.id);
        torchData.data.inventory.pockets=[{id:'native-torch',name:'Фонарик',category:'misc',weight:.5,volume:1,
            attributes:{portable_light:'hand'}}];
        await request('/lobbies/characters/'+actor.id,{data:torchData.data},token,'PUT');
        const nightClock=await request(api,null,token,'GET');
        await request(api+'/time',{game_day:nightClock.game_day,game_time_minutes:23*60},token,'PATCH');
        for(const client of [page,peer]) {
            await client.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
            await client.until(`document.querySelector('.portable-light-row[data-light-id="native-torch"]')`,'Фонарь появился в листах');
        }
        const lightBefore=(await request(combat,null,token,'GET')).current_character.action_points_current;
        await clickElement('[data-light-id="native-torch"] [data-light-operation="prepare"]');
        await page.until(`document.querySelector('[data-light-id="native-torch"] [data-light-operation="toggle"]')`,'Фонарь в руке');
        assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,lightBefore-1);
        await clickElement('[data-light-id="native-torch"] [data-light-operation="toggle"]');
        for(const client of [page,peer])await client.until(`document.querySelector('[data-light-id="native-torch"] strong')?.textContent.includes('включён')`,'Состояние фонаря синхронизировано');
        assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,lightBefore-2);
        for(const client of [page,peer])await client.evaluate('window.closeCharacterSheet()');
        await page.until(`window.locationCombatState.characters.find(c=>c.character_id===${flashPeople[0].id})?.portable_illumination?.lit`,'Персонаж перед фонарём освещён');
        assert.equal(await page.evaluate(`window.locationCombatState.characters.find(c=>c.character_id===${flashPeople[2].id}).portable_illumination.lit`),false);
        await page.until(`(async()=>{const scene=await import('/static/js/locationScene.js');return scene.getLocationPortableLightDiagnostics().lights===1})()`,'Настоящий источник света в сцене');
        fs.writeFileSync(path.join(safeSession.artifacts,'portable-light-night.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await request(loc+'/characters/'+flashPeople[0].id,null,token,'DELETE');
        await page.evaluate(`(async()=>{const T=await import('three'),c=window.locationControls,
            target=new T.Vector3(41.5,1,61),delta=target.clone().sub(c.target);c.object.position.add(delta);c.target.copy(target);
            c.object.zoom=1.3;c.object.updateProjectionMatrix();c.update();c.object.updateMatrixWorld(true)})()`);
        const toggleTorch=async()=>request('/lobbies/characters/'+actor.id+'/portable-lights',{
            operation:'toggle',path:['inventory','pockets',0],source_id:'native-torch',character_revision:(await read(actor.id)).data._revision},token);
        await toggleTorch();
        await page.until(`(async()=>{const s=await import('/static/js/locationScene.js');return s.getLocationPortableLightDiagnostics().lights===0})()`,'Фонарь выключен');
        await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
        fs.writeFileSync(path.join(safeSession.artifacts,'portable-light-open-off.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await toggleTorch();
        await page.until(`(async()=>{const s=await import('/static/js/locationScene.js');return s.getLocationPortableLightDiagnostics().lights===1})()`,'Фонарь включён на открытой дороге');
        await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
        fs.writeFileSync(path.join(safeSession.artifacts,'portable-light-open-on.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await clickElement('#actions-toggle');
        await page.until(`document.querySelector('.action-journal')?.innerText.includes('Взять воду в руку')`,'Серверный журнал в интерфейсе');
        const journalText=await page.evaluate('document.querySelector(".action-journal").innerText');
        assert.ok(journalText.includes('Исполнено'));assert.ok(journalText.includes('1 ОД'));assert.ok(journalText.includes('2 ОД'));
        fs.writeFileSync(path.join(safeSession.artifacts,'action-journal.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await request(combat+'/end',{},token);
        assert.equal((await read(actor.id)).data.inventory.pockets[0].lightPower.uses_left,9);
        await request(api+'/time',{game_day:nightClock.game_day,game_time_minutes:23*60+10},token,'PATCH');
        assert.equal((await read(actor.id)).data.inventory.pockets[0].lightPower.uses_left,8);
        // Два оружия: настоящая кнопка второго хвата, серверные выстрелы и повторный вход.
        await request('/lobbies/characters/'+actor.id+'/portable-lights',{operation:'stow',source_id:'native-torch',
            path:['inventory','pockets',0],character_revision:(await read(actor.id)).data._revision},token);
        const pairData=await read(actor.id);
        pairData.data.weapons=[copy(rifle),{...copy(rifle),id:'native-second-gun',name:'Второй проверочный пистолет'}];
        await request('/lobbies/characters/'+actor.id,{data:pairData.data},token,'PUT');
        for(const client of [page,peer])await client.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
        await page.until(`document.querySelector('[data-weapon-pair-take="1"]')`,'Кнопка второй руки');
        await clickElement('[data-weapon-pair-take="1"]');
        for(const client of [page,peer])await client.until(`window.locationCombatState.characters.find(c=>c.character_id===${actor.id})?.secondary_weapon_index===1`,'Парный хват синхронизирован');
        await request(combat+'/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        state=await request(combat,null,token,'GET');assert.equal(state.current_character.secondary_weapon_index,1);
        const pairTarget=await request(api+'/characters',{name:'Мишень двух оружий',data:{health:{...health(),current:700,max:700}}},token);
        await request(loc+'/spawn_character',{character_id:pairTarget.id,tile_x:41,tile_y:50},token);
        const pairBefore=state.current_character.action_points_current;
        for(const weapon_index of [0,1]) {
            const result=await action({action_key:'attack',weapon_index,fire_mode:'unaimed',shot_count:1,action_points:1,
                target_character_id:pairTarget.id,pending_action_id:'native-pair-shot-'+weapon_index});
            assert.equal(result.attack.paired_weapon_accuracy_penalty,2);
        }
        assert.deepEqual((await read(actor.id)).data.weapons.map(w=>w.ammo),[19,19]);
        const pairAfter=(await request(combat,null,token,'GET')).current_character.action_points_current;
        assert.equal(pairAfter,pairBefore-2);
        for(const client of [page,peer])await client.until(`document.querySelector('[data-paired-fire="unaimed"]')`,'Общая стрельба доступна в двух листах');
        const simultaneous=await action({action_key:'attack',weapon_index:0,fire_mode:'unaimed',shot_count:1,action_points:1,
            target_character_id:pairTarget.id,attribute_choice:'paired_fire',pending_action_id:'native-paired-together'});
        assert.ok(simultaneous.attack.paired_fire);assert.equal(simultaneous.attack.paired_attacks.length,2);
        assert.ok(simultaneous.attack.results.every(hit=>hit.rolls.length===2));
        assert.deepEqual((await read(actor.id)).data.weapons.map(w=>w.ammo),[18,18]);
        const afterSimultaneous=(await request(combat,null,token,'GET')).current_character.action_points_current;
        assert.equal(afterSimultaneous,pairAfter-1);
        const deniedReload=await fetch(base+combat+'/action',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
            body:JSON.stringify({location_character_id:actorId,action_key:'reload_weapon',weapon_index:0})});
        assert.equal(deniedReload.status,400);assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,afterSimultaneous);
        const pairEpoch=await page.evaluate('performance.timeOrigin');await page.call('Page.reload');
        await waitForDocument(page,{href:base+api+'/page?location='+location.id,afterEpoch:pairEpoch,label:'Перезагрузка парного хвата'});
        await page.until(`window.locationCombatState?.current_character?.secondary_weapon_index===1`,'Две руки сохранены после перезагрузки');
        assert.deepEqual((await read(actor.id)).data.weapons.map(w=>w.ammo),[18,18]);
        await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
        await page.until(`document.querySelector('#character-sheet-modal')?.textContent.includes('В левой руке')`,'Левая рука со вторым оружием видна после перезагрузки');
        fs.writeFileSync(path.join(safeSession.artifacts,'paired-weapons.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await request(combat+'/end',{},token);
        await request('/lobbies/characters/'+actor.id+'/weapon-pair',{operation:'stow',character_revision:(await read(actor.id)).data._revision},token);
        const prosthesisCatalog=await request(api+'/templates?category=misc',null,token,'GET');
        const legProfile=[...prosthesisCatalog.global,...prosthesisCatalog.local].find(t=>t.name==='Навесная голень со стопой');
        assert.ok(legProfile);
        const prosthesisData=await read(actor.id);
        prosthesisData.data.inventory={pockets:[{id:'native-head',name:'Налобный фонарик',attributes:{portable_light:'head'}},
            {id:'native-leg',name:legProfile.name,quantity:1,attributes:legProfile.attributes},
            {id:'native-lube',name:'Набор смазочных приспособлений',quantity:1}],backpack:[{id:'native-battery',name:'Батарейка',quantity:3}]};
        prosthesisData.data.health.zones.leftLeg.current=0;
        prosthesisData.data.health.effects.push({id:'native-loss',type:'amputation',area:'leftLeg',loss_extent:'elbow_or_knee',active:true});
        await request('/lobbies/characters/'+actor.id,{data:prosthesisData.data},token,'PUT');
        for(const client of [page,peer])await client.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
        await request(combat+'/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        await page.until(`document.querySelector('[data-light-id="native-head"] [data-light-operation="prepare"]')&&!document.querySelector('[data-light-id="native-head"] [data-light-operation="prepare"]').disabled`,'Налобный фонарь доступен в бою');
        await page.evaluate("window.__morningUpdates=[];socket.on('character_data_updated',e=>window.__morningUpdates.push(e));true");
        const mountingBefore=(await request(combat,null,token,'GET')).current_character.action_points_current;
        await clickElement('[data-light-id="native-head"] [data-light-operation="prepare"]');
        await page.until(`document.querySelector('[data-light-id="native-head"] strong')?.textContent.includes('на голове')`,'Фонарь надет в бою');
        assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,mountingBefore-2);
        await page.evaluate(`Array.from(document.querySelectorAll('[data-light-id="native-head"] button')).find(e=>e.textContent.includes('Заменить')).dataset.nativeBattery='true'`);
        await clickElement('[data-native-battery="true"]');
        await page.until(`window.__morningUpdates.some(e=>e.character_id===${actor.id}&&e.updates?.data?.inventory?.backpack?.[0]?.quantity===2)`,'Батарейка из рюкзака использована');
        assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,mountingBefore-5);
        await clickElement('[data-light-id="native-head"] [data-light-operation="stow"]');
        await page.until(`document.querySelector('[data-light-id="native-head"] [data-light-operation="prepare"]')`,'Налобный фонарь снят');
        assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,mountingBefore-6);
        await page.until(`document.querySelector('.prostheses select')`,'Раздел протезов');
        await page.until(`Array.from(document.querySelectorAll('.prostheses button')).some(e=>e.textContent.startsWith('Установить')&&!e.disabled)`,'Предыдущее действие завершено, установка доступна');
        await page.evaluate(`(()=>{const s=document.querySelector('.prostheses select');s.value='leftLeg';s.dispatchEvent(new Event('change',{bubbles:true}));
            Array.from(document.querySelectorAll('.prostheses button')).find(e=>e.textContent.startsWith('Установить')).dataset.nativeInstall='true'})()`);
        const installingBefore=(await request(combat,null,token,'GET')).current_character.action_points_current;
        await clickElement('[data-native-install="true"]');
        for(const client of [page,peer])await client.until(`document.querySelector('.prostheses')?.textContent.includes('прочность 75/75')`,'Протез установлен у двух клиентов');
        assert.equal((await request(combat,null,token,'GET')).current_character.action_points_current,installingBefore-10);
        await request(combat+'/end',{},token);
        await page.until(`window.locationCombatState?.status!=='active'&&Array.from(document.querySelectorAll('.prostheses button')).some(e=>e.textContent.includes('Использовать')&&!e.disabled)`,'Обслуживание доступно после боя');
        await page.evaluate(`(()=>{const s=document.querySelector('.prostheses select');s.value=0;
            Array.from(document.querySelectorAll('.prostheses button')).find(e=>e.textContent.includes('Использовать')).dataset.nativeSupply='true'})()`);
        // В карманах сначала набор смазки; батарейка — второй источник в списке.
        await page.evaluate(`(()=>{const s=document.querySelector('.prostheses select');s.value='native-battery';s.dispatchEvent(new Event('change',{bubbles:true}));window.dispatchEvent(new CustomEvent('combat-state-updated'))})()`);
        await page.until(`document.querySelector('.prostheses select')?.value==='native-battery'&&Array.from(document.querySelectorAll('.prostheses button')).some(e=>e.textContent.includes('Использовать')&&!e.disabled)`,'Выбор батарейки пережил обновление листа');
        await page.evaluate(`Array.from(document.querySelectorAll('.prostheses button')).find(e=>e.textContent.includes('Использовать')).dataset.nativeSupply='true'`);
        await clickElement('[data-native-supply="true"]');
        for(const client of [page,peer])await client.until(`document.querySelector('.prostheses')?.textContent.includes('питание 1440 мин')`,'Питание протеза синхронизировано');
        const prosthesisReload=await page.evaluate('performance.timeOrigin');await page.call('Page.reload');
        await waitForDocument(page,{href:base+api+'/page?location='+location.id,afterEpoch:prosthesisReload,label:'Перезагрузка протеза'});
        await page.until(`typeof window.openCharacterSheet==='function'`,'Лист готов');await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
        await page.until(`document.querySelector('.prostheses')?.textContent.includes('питание 1440 мин')`,'Питание сохранено после перезагрузки');
        await page.evaluate("document.querySelector('.prostheses')?.scrollIntoView({block:'center'})");
        fs.writeFileSync(path.join(safeSession.artifacts,'prostheses-and-light-controls.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        // Операция через настоящее меню: сохранение, два клиента и игровое время.
        const fixedProfile=[...prosthesisCatalog.global,...prosthesisCatalog.local].find(t=>t.name==='Фиксированная голень со стопой');assert.ok(fixedProfile);
        const surgeryData=await read(actor.id);
        surgeryData.data.skills.other={...(surgeryData.data.skills.other||{}),medicine:{base:40},engineering:{base:40}};
        surgeryData.data.health.painLevel=0;surgeryData.data.health.stress=0;surgeryData.data.health.current=700;
        surgeryData.data.health.zones.rightLeg.current=0;
        surgeryData.data.health.effects.push({id:'surgical-native-loss',type:'amputation',area:'rightLeg',loss_extent:'elbow_or_knee',active:true});
        surgeryData.data.inventory.pockets.push({id:'native-fixed-leg',name:fixedProfile.name,category:'misc',quantity:1,attributes:fixedProfile.attributes});
        for(const [name,description,quantity,uses] of [['Хирургический набор "Хирург"','Восстанавливает часть тела. Она имеет 30 здоровья. 5 использований.',2,5],['Морфий','Снижает боль на 5 уровней.',10,1]]){
            const template=await request(api+'/templates',{name,category:'consumable',description,attributes:{uses,consumable:{direct:name.startsWith('Хирургический')?{surgical_kit:true,uses}:{pain_delta:-5,application_form:'injectable'},effects:[]}}},token);
            surgeryData.data.inventory.pockets.push({id:'native-surgery-'+template.id,templateId:template.id+1000000,name,category:'consumable',quantity,uses,maxUses:uses,attributes:template.attributes});
        }
        await request('/lobbies/characters/'+actor.id,{data:surgeryData.data},token,'PUT');
        await page.until(`Array.from(document.querySelectorAll('.prostheses button')).some(button=>button.textContent==='Операция · 1 ч')`,'Кнопка операции в листе');
        await page.evaluate(`Array.from(document.querySelectorAll('.prostheses button')).find(button=>button.textContent==='Операция · 1 ч').dataset.nativeSurgery='true'`);
        await clickElement('[data-native-surgery="true"]');
        await page.until(`document.querySelector('.prosthesis-surgery select[name="operation"]')?.options.length>0&&getComputedStyle(document.querySelector('#medical-consumable-menu')).display!=='none'`,'Операционное крепление доступно в меню');
        const surgeryMenu=await page.evaluate(`document.querySelector('.prosthesis-surgery').innerText`);
        assert.ok(surgeryMenu.includes('1 ч')&&surgeryMenu.includes('Морфий: 3 амп.'),surgeryMenu);
        fs.writeFileSync(path.join(safeSession.artifacts,'prosthesis-surgery-menu.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await clickElement('.prosthesis-surgery button[type="submit"]');
        await page.until(`!document.querySelector('#medical-consumable-menu')||getComputedStyle(document.querySelector('#medical-consumable-menu')).display==='none'`,'Начало операции подтверждено сервером');
        await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
        for(const client of [page,peer])await client.until(`document.querySelector('.prostheses')?.textContent.includes('Идёт операция')`,'Операция началась у обоих клиентов');
        const surgeryStarted=await read(actor.id);assert.ok(surgeryStarted.data.health.combatMeta.prosthesisSurgeryId);
        assert.equal(surgeryStarted.data.health.painLevel,0);
        assert.equal(surgeryStarted.data.inventory.pockets.find(item=>item.name==='Морфий').quantity,7);
        const surgeryEpoch=await page.evaluate('performance.timeOrigin');await page.call('Page.reload');
        await waitForDocument(page,{href:base+api+'/page?location='+location.id,afterEpoch:surgeryEpoch,label:'Сохранённая операция'});
        await page.until(`typeof window.openCharacterSheet==='function'`,'Лист доступен');await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
        await page.until(`document.querySelector('.prostheses')?.textContent.includes('Идёт операция')`,'Операция пережила перезагрузку');
        const surgeryClock=await request(api,null,token,'GET'),absolute=surgeryClock.game_day*1440+surgeryClock.game_time_minutes+60;
        await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
        for(const client of [page,peer])await client.until(`document.querySelector('.prostheses')?.textContent.includes('Последняя операция: успех')`,'Исход операции доставлен двум клиентам');
        const surgeryFinished=await read(actor.id);assert.equal(surgeryFinished.data.health.prostheses.rightLeg.base.id,'native-fixed-leg');
        assert.equal(surgeryFinished.data.health.prostheses.rightLeg.base.weight,2);assert.equal(surgeryFinished.data.health.prostheses.rightLeg.base.volume,4);
        assert.equal(surgeryFinished.data.health.painLevel,0);assert.ok(!surgeryFinished.data.health.combatMeta.prosthesisSurgeryId);
        await page.until(`Array.from(document.querySelectorAll('.prostheses button')).some(button=>button.textContent==='Снять операцией · 1 ч')`,'Снятие зажима доступно в листе');
        await page.evaluate(`Array.from(document.querySelectorAll('.prostheses button')).find(button=>button.textContent==='Снять операцией · 1 ч').dataset.nativeRemoval='true'`);
        await clickElement('[data-native-removal="true"]');
        await page.until(`document.querySelector('.prosthesis-surgery select[name="operation"]')?.selectedOptions[0]?.textContent.includes('снять')`,'В меню выбрано операционное снятие');
        await clickElement('.prosthesis-surgery button[type="submit"]');
        await page.until(`!document.querySelector('#medical-consumable-menu')||getComputedStyle(document.querySelector('#medical-consumable-menu')).display==='none'`,'Начало снятия подтверждено сервером');
        for(const client of [page,peer]){
            await client.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
            await client.until(`document.querySelector('.prostheses')?.textContent.includes('Идёт операция')`,'Снятие началось у обоих клиентов');
        }
        const removalStart=await read(actor.id);assert.equal(removalStart.data.health.prostheses.rightLeg.base.id,'native-fixed-leg');
        assert.equal(removalStart.data.inventory.pockets.find(item=>item.name==='Морфий').quantity,4);
        const removalClock=await request(api,null,token,'GET'),removalAbsolute=removalClock.game_day*1440+removalClock.game_time_minutes+60;
        await request(api+'/time',{game_day:Math.floor(removalAbsolute/1440),game_time_minutes:removalAbsolute%1440},token,'PATCH');
        for(const client of [page,peer])await client.until(`document.querySelector('.prostheses')?.textContent.includes('Последняя операция: успех')&&!Array.from(document.querySelectorAll('.prostheses button')).some(button=>button.textContent==='Снять операцией · 1 ч')`,'Снятие завершено у обоих клиентов');
        const removed=await read(actor.id);assert.ok(!removed.data.health.prostheses.rightLeg.base);
        assert.equal(removed.data.inventory.pockets.filter(item=>item.id==='native-fixed-leg').length,1);
        assert.equal(removed.data.health.combatMeta.prosthesisSurgeryResult.operation,'surgery_remove');
        const singleHand=await read(actor.id);singleHand.data.health.zones.rightArm.current=0;
        singleHand.data.health.effects.push({id:'native-right-arm-loss',type:'amputation',area:'rightArm',loss_extent:'entire_limb',active:true});
        await request('/lobbies/characters/'+actor.id,{data:singleHand.data},token,'PUT');
        await request(combat+'/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        await action({action_key:'stow_weapon'});
        for(const client of [page,peer])await client.until(`window.locationCombatState?.status==='active'&&window.locationCombatState.current_character?.drawn_weapon_index==null`,'Оружие убрано, действует только левая рука');
        await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment')`);
        await page.until(`document.querySelector('[data-weapon-draw-hand="left"][data-weapon-draw-index="0"]')&&!document.querySelector('[data-weapon-draw-hand="left"][data-weapon-draw-index="0"]').disabled`,'Выбор руки доступен до доставания');
        const beforeLeft=await request(combat,null,token,'GET');
        await clickElement('[data-weapon-draw-hand="left"][data-weapon-draw-index="0"]');
        for(const client of [page,peer])await client.until(`document.querySelector('[data-weapon-grip-label="0"]')?.textContent.includes('левая рука')&&window.locationCombatState.current_character?.drawn_weapon_index===0`,'Доставание в левую руку синхронизировано');
        const afterLeft=await request(combat,null,token,'GET');
        assert.equal(afterLeft.current_character.action_points_current,beforeLeft.current_character.action_points_current-beforeLeft.current_character.weapon_ergonomics.find(profile=>profile.weapon_index===0).draw_action_points);
        assert.equal((await read(actor.id)).data.health.combatMeta.weaponPrimaryHand,'left');
        await request(combat+'/end',{},token);
        // Действительные препараты и отображение результата у владельца и наблюдателя.
        const drugResults=[];
        for(const drugName of ['Плитка Гематогена','Стимулятор "Котик"','Стимулятор "Полукровка"',
                'Стимулятор Шумодав','Стимулятор Воля','Стимулятор Сова','Подавитель эмоций']){
            const raw=source.consumables.find(item=>item.name===drugName);assert.ok(raw,drugName);
            const template=await request(api+'/templates',{name:raw.name,category:'consumable',description:raw.description,attributes:raw.attributes},token);
            const item={id:'native-drug-'+template.id,templateId:template.id+1000000,name:raw.name,category:'consumable',quantity:1,attributes:raw.attributes};
            const h=health();h.infection=80;
            if(drugName==='Плитка Гематогена')h.effects=[{type:'bleeding_external_severe',area:'leftArm'},{type:'bleeding_internal_medium',area:'chest'}];
            const subject=await request(api+'/characters',{name:'Проверка препарата: '+drugName,data:{health:h,skills:{other:{medicine:{base:40}}},inventory:{pockets:[item],backpack:[]}}},token);
            await allow(subject);await request(loc+'/spawn_character',{character_id:subject.id,tile_x:40,tile_y:48},token);
            for(const client of [page,peer])await client.evaluate(`window.openCharacterSheet(${subject.id},'basic')`);
            const current=await read(subject.id),medical=drugName!=='Плитка Гематогена';
            await request('/lobbies/characters/'+subject.id+(medical?'/medical-procedure':'/general-consumable'),{
                operation_id:'native-drug-'+subject.id,target_character_id:subject.id,
                source:{path:['inventory','pockets',0],snapshot:current.data.inventory.pockets[0]},application:{kind:'self',actionPoints:0}},token);
            const after=(await read(subject.id)).data.health;
            if(drugName==='Плитка Гематогена'){
                assert.equal(after.bleeding.totalSeverity,6);assert.equal(after.bleeding.modifierTotal,0);
                for(const client of [page,peer])await client.until(`document.querySelector('#sheet-tab-basic')?.textContent.includes('Тяжесть +6')`,'Уменьшенная тяжесть кровотечений в двух листах');
            }else if(drugName.includes('Котик')){
                assert.ok(after.combatMeta.addictionBlockedUntilMinute>0);
                for(const client of [page,peer])await client.until(`document.querySelector('[data-sheet-panel="addictions"]')?.textContent.includes('Получение новых зависимостей заблокировано')`,'Защита Котика видна обоим клиентам');
            }else if(drugName.includes('Полукровка'))assert.equal(after.infection,30);
            else if(drugName.includes('Шумодав')){
                assert.ok(after.effects.some(effect=>effect.type==='deafness'&&effect.value===100&&effect.remaining===3));
                for(const client of [page,peer])await client.until(`document.querySelector('#sheet-tab-basic')?.textContent.includes('Глухота')`,'Глухота от Шумодава видна обоим клиентам');
            }else if(drugName.includes('Воля')){
                for(const client of [page,peer])await client.until(`document.querySelector('#sheet-tab-basic')?.textContent.includes('Заблокировано боли: 0; после действия вернётся: 0')`,'Счётчик заблокированной боли виден обоим клиентам');
            }else if(drugName.includes('Сова')){
                const owlModifiers=after.combatMeta.consumableModifiers.filter(modifier=>
                    ['darkness_awareness','darkness_accuracy','light_awareness','darkness_visual_immunity','darkness_shooting_immunity'].includes(modifier.stat));
                assert.deepEqual(Object.fromEntries(owlModifiers.map(modifier=>[modifier.stat,modifier.value])),
                    {darkness_awareness:3,darkness_accuracy:3,light_awareness:-3});
                assert.ok(owlModifiers.every(modifier=>modifier.remaining_seconds===4*3600));
                assert.equal(after.needs.sleptToday,true,'Сова немедленно закрывает потребность во сне');
                for(const client of [page,peer])await client.until(`(()=>{const text=document.querySelector('#sheet-tab-basic')?.textContent||'';return text.includes('Внимательность в темноте без ПНВ: +3')&&text.includes('Точность в темноте без ПНВ: +3')&&text.includes('Внимательность на свету: -3')})()`,'Бонусы Совы для темноты и штраф на свету показаны в обоих листах');
            }else{
                assert.ok(!after.combatMeta.consumableModifiers?.some(m=>m.stat==='movement_points'));
                assert.ok(after.effects.some(effect=>effect.type==='stress_resistance'&&effect.remaining===6&&effect.tick==='movement_end'));
                for(const client of [page,peer])await client.until(`document.querySelector('#sheet-tab-basic')?.textContent.includes('сопротивление стрессу')&&!document.querySelector('#sheet-tab-basic').textContent.includes('Штраф перемещения: -6')&&!document.querySelector('#sheet-tab-basic').textContent.includes('stat_bonus')`,'Подавитель эмоций работает6перемещений без бонуса движения');
            }
            drugResults.push({name:drugName,health:after});
        }
        fs.writeFileSync(path.join(safeSession.artifacts,'medicines-actual-ui.json'),JSON.stringify({status:'PASS',twoClients:true,drugs:drugResults},null,2));
        const painSubject=await request(api+'/characters',{name:'Восстановление боли вне боя',data:{health:{...health(),painLevel:6}}},token);
        await allow(painSubject);await request(loc+'/spawn_character',{character_id:painSubject.id,tile_x:40,tile_y:48},token);
        for(const client of [page,peer])await client.evaluate(`window.openCharacterSheet(${painSubject.id},'basic')`);
        const painClock=await request(api,null,token,'GET'),painAbsolute=painClock.game_day*1440+painClock.game_time_minutes+1;
        await request(api+'/time',{game_day:Math.floor(painAbsolute/1440),game_time_minutes:painAbsolute%1440},token,'PATCH');
        assert.equal((await read(painSubject.id)).data.health.painLevel,0);
        for(const client of [page,peer])await client.until(`document.querySelector('[name="health.painLevel"]')?.value==='0'`,'Снижение боли доставлено обоим клиентам');
        fs.writeFileSync(path.join(safeSession.artifacts,'answers92-93.json'),JSON.stringify({status:'PASS',twoClients:true,
            clampRemoval:{realButton:true,installedUntilCompletion:true,returnedSameInstance:true,morphineSpent:3,durationSeconds:3600},
            suppressor:{sixMovements:true,movementBonus:0},painRecovery:{before:6,afterOneMinute:0}},null,2));
        fs.writeFileSync(path.join(safeSession.artifacts,'morning-answers.json'),JSON.stringify({status:'PASS',twoClients:true,scene:'Копия Припяти',medicines:rows.map(r=>({name:r.name,before:r.beforeClock,after:r.afterClock})),armor:armorRows.map(r=>({name:r.name,damage:r.damage,after:r.after})),flash:{noise:flashShot.attack.noise,received:noise.noise_received,targets:flashShot.attack.results[0].flash_targets},grip:{paidTwice:2,reloadStable:true,twoClients:true,observerDenied:true,explicitLeftRightBoth:true,onlyLeftHandDrawsFromUnarmed:true,drawDoesNotChargeGripExtra:true},water:{litres:1,retrieveAP:1,pourAP:2,burningBefore:8,burningAfter:6,reachableTargets:true,styledSelects:true},lights:{gpuBeam:true,twoClients:true,battleBattery:9,afterTenMinutes:8},journal:{persistentServerEntries:true,realCosts:true},prostheses:{installedWithRealButton:true,installAP:10,powerAfterReload:86400,twoClients:true,batteryChoiceSurvivesRefresh:true},combatLightMount:{prepareAP:2,batteryFromBackpackAP:3,stowAP:1},pair:{twoClients:true,eachWeaponFired:true,simultaneousFire:true,simultaneousAP:1,disadvantageBoth:true,ammunition:[18,18],reloadRejectedWithoutPayment:true,reloadStable:true},surgery:{realButton:true,durationSeconds:3600,morphineSpent:3,ordinaryDrugEffects:false,revisionAndReloadStable:true,twoClients:true,installedWeight:2,installedVolume:4}},null,2));
    }catch(error){
        for(const [index,client] of [page,peer].entries())if(client){
            const diagnostic=await client.evaluate(`({heading:document.querySelector('#character-sheet-name')?.textContent,
                surgery:document.querySelector('.prosthesis-surgery')?.textContent,
                prostheses:document.querySelector('.prostheses')?.textContent,
                selections:Array.from(document.querySelectorAll('.prostheses select'),s=>({value:s.value,disabled:s.disabled})),
                updates:(window.__morningUpdates||[]).slice(-8).map(e=>({id:e.character_id,prostheses:Boolean(e.updates?.data?.health?.prostheses)}))})`).catch(()=>null);
            fs.writeFileSync(path.join(safeSession.artifacts,'prosthesis-failure-'+index+'.json'),JSON.stringify(diagnostic,null,2));
            await client.evaluate(`document.querySelector('.prostheses')?.scrollIntoView({block:'center'})`).catch(()=>null);
            const shot=await client.call('Page.captureScreenshot',{format:'png'}).catch(()=>null);
            if(shot)fs.writeFileSync(path.join(safeSession.artifacts,'prosthesis-failure-'+index+'.png'),Buffer.from(shot.data,'base64'));
        }
        throw error;
    }finally{await peer?.close();if(context)await protocol?.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});await protocol?.close();}
};
