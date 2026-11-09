const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
const copy=x=>JSON.parse(JSON.stringify(x));

module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'health-audit');assert.ok(safeSession.verified);
    const api='/lobbies/'+lobby.id,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:8,world_tile_z:8},token);
    const loc=api+'/locations/'+location.id,combat=loc+'/combat';
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const screenshot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const click=async(client,selector)=>{
        await client.call('Page.bringToFront');
        const point=await client.evaluate(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});if(!n)throw Error('Нет элемента');n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        for(const type of ['mousePressed','mouseReleased'])await client.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    };
    const medProfiles=[
        {name:'Регенератор аудита',direct:{hp_per_turn:10,duration:3,duration_phase:'turn_end'}},
        {name:'Анальгетик аудита',direct:{pain_delta:-1,stress_delta:-2,strength_delta:2,duration:3,duration_phase:'turn_end'}},
    ];
    const controls=await(await fetch(base+'/__health_audit/'+safeSession.runId)).json();
    medProfiles.push(...controls.catalog_profiles);
    const meds=[];
    for(const profile of medProfiles){
        const t=await request(api+'/templates',{name:profile.name,category:'consumable',weight:.1,volume:.1,description:profile.description,attributes:{uses:3,consumable:profile.profile || {direct:profile.direct,effects:[]}}},token);
        meds.push({id:'audit-med-'+t.id,templateId:t.id+1000000,name:t.name,category:'consumable',quantity:2,uses:3,maxUses:3,weight:.1,volume:.1,attributes:copy(t.attributes)});
    }
    const data={skills:{physical:{strength:{base:20},accuracy:{base:20},shooting:{base:20},will:{base:20}}},health:{current:500,max:700,painLevel:3,stress:3,radiation:10,effects:[]},inventory:{pockets:meds,backpack:[]},equipment:{
        gloves:{id:'audit-gloves',templateId:999997,name:'Перчатки аудита',category:'misc',durability:17,maxDurability:80,weight:.5,volume:1,attributes:{protection:{physical:.2,chemical:.3,thermal:.4,electric:.5,radiation:.1},notes:'Защитные перчатки'}}}};
    const patient=await request(api+'/characters',{name:'Аудит здоровья',data},token),read=()=>request('/lobbies/characters/'+patient.id,null,token,'GET');
    const shooter=await request(api+'/characters',{name:'Стрелок аудита',data:{skills:data.skills,health:{current:700,max:700,effects:[]},inventory:{pockets:[],backpack:[]},equipment:{},weapons:[{
        id:'audit-rifle',name:'Винтовка аудита',category:'weapon',accuracy:100,ergonomics:100,damage:80,range:20,durability:100,maxDurability:100,ammo:20,fixedMagazine:true,
        attributes:{damage:80,accuracy:100,range:20,min_strength:0,noise:0,magazine_size:20}}]}},token);
    for(const [c,y]of [[shooter,48],[patient,50]])await request(loc+'/spawn_character',{character_id:c.id,tile_x:41,tile_y:y},token);
    await request('/auth/register',{username:'health-observer',email:'health-observer@example.test',password:'synthetic-health-password'});
    const player=await request('/auth/login',{username:'health-observer',password:'synthetic-health-password'});await request(api+'/join',{},player.access_token);
    await request('/lobbies/characters/'+patient.id+'/visibility',{visible_to:[player.user_id],editable_to:[]},token,'PUT');
    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();let protocol,context,peer;
    const href=base+api+'/page?location='+location.id;
    try{
        protocol=await connect(version.webSocketDebuggerUrl);context=await protocol.call('Target.createBrowserContext');
        const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const info=(await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(t=>t.id===target.targetId);peer=await connect(info.webSocketDebuggerUrl);await peer.call('Page.enable');await peer.call('Runtime.enable');
        await waitForDocument(peer,{href:base,label:'Начальная страница наблюдателя'});
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))});true`);
        for(const client of [page,peer]){
            const epoch=await client.evaluate('performance.timeOrigin');await client.call('Page.navigate',{url:href});await waitForDocument(client,{href,afterEpoch:epoch,label:'Припять для аудита здоровья'});
            await client.until(`window.socket?.connected&&window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Подлокация загружена');
            await client.evaluate(`window.openCharacterSheet(${patient.id})`);await client.until('document.querySelector("#effects-container")','Лист открыт');
        }
        // Настоящая кнопка и поля редактора, затем штатное автосохранение.
        await click(page,'[onclick="addEffect()"]');
        await page.until(`document.querySelector('[name="health.effects.0.name"]')`,'Добавлен ручной эффект');
        await page.evaluate(`(()=>{const values={name:'Наблюдение после ранения',value:'-0.25',remaining:'3',tick:'turn_end',note:'Проверка ручного состояния без автоматического числового бонуса'};for(const [key,value]of Object.entries(values)){const n=document.querySelector('[name="health.effects.0.'+key+'"]');n.value=value;}document.querySelector('[name="health.effects.0.name"]').dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
        await page.until('document.querySelector(".health-effect-caption")?.textContent.includes("-0.25")','Дробное значение отображается');
        await peer.until('document.querySelector("#effects-container")?.textContent.includes("Наблюдение после ранения")','Ручной эффект пришёл наблюдателю');
        const manual=(await read()).data.health.effects[0];assert.equal(manual.value,-.25);assert.equal(manual.tick,'turn_end');assert.equal(manual.remaining,3);
        await click(page,'[onclick="addEffect()"]');
        await page.until(`document.querySelector('[name="health.effects.1.type"]')`,'Второй ручной эффект');
        await page.evaluate(`(()=>{const type=document.querySelector('[name="health.effects.1.type"]');type.value='pain';document.querySelector('[name="health.effects.1.value"]').value='2';type.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
        await page.until(`document.querySelector('[onclick="applyManualNumericEffect(1,this)"]')`,'Явное применение числового изменения');
        await click(page,'[onclick="applyManualNumericEffect(1,this)"]');
        for(const client of [page,peer])await client.until(`document.querySelector('[name="health.painLevel"]')?.value==='5'`,'Разовое изменение боли пришло обоим клиентам');
        assert.ok(!(await read()).data.health.effects.some(effect=>effect.type==='pain'));
        for(let index=0;index<meds.length;index++){
            const current=(await read()).data.inventory.pockets.find(item=>item.id===meds[index].id);
            const pocketIndex=(await read()).data.inventory.pockets.findIndex(item=>item.id===current.id);
            const direct = medProfiles[index].profile?.direct || {};
            const medical = direct.medical_difficulty != null || direct.application_form === 'injectable';
            const endpoint = medical ? '/medical-procedure' : '/general-consumable';
            const outcome=await request('/lobbies/characters/'+patient.id+endpoint,{operation_id:'audit-med-'+index,target_character_id:patient.id,source:{path:['inventory','pockets',pocketIndex],snapshot:current},application:{kind:'self',actionPoints:0}},token);
            if (medical) assert.equal(outcome.medical_result.success,true);
        }
        await page.until('document.querySelector("#effects-container")?.textContent.includes("Регенерация")&&document.querySelector("#effects-container")?.textContent.includes("Блок нового стресса")','Последний препарат создал состояние');
        const text=await page.evaluate('document.querySelector("#health-right-column").innerText');
        assert.ok(!/\+\-|consumable-template|turn_end|hour_start/.test(text));
        const captions=await page.evaluate('Array.from(document.querySelectorAll(".health-effect-caption"),n=>n.innerText).join("\\n")');
        assert.ok(!captions.includes('Поддержка без сна')&&captions.includes('Блок нового стресса'));
        await page.evaluate('document.querySelector("#effects-container").scrollIntoView({block:"center"})');await screenshot('health-after-medicines');
        await request(api+'/characters/time-active',{character_ids:[patient.id]},token,'PATCH');
        await request(api+'/time',{game_day:lobby.game_day||1,game_time_minutes:(lobby.game_time_minutes||0)+10},token,'PATCH');
        for(const client of [page,peer])await client.until('Array.from(document.querySelectorAll(".health-effect-caption"),n=>n.innerText).every(t=>!t.includes("Регенерация"))','Перевод времени завершил лечение у обоих клиентов');
        const timed=(await read()).data.health;
        assert.equal(timed.radiation,0);assert.ok(timed.current>=530);
        assert.ok(!timed.effects.some(e=>e.type==='pain_block'||e.blocks_new_stress));
        assert.ok(timed.needs.sleptToday);
        // Настоящие боевые выстрелы: повреждение руки и состояние раны в общем листе.
        for(const client of [page,peer])await client.evaluate('window.closeCharacterSheet()');
        let state=await request(combat,null,token,'GET');const actorId=state.characters.find(c=>c.character_id===shooter.id).location_character_id,targetId=state.characters.find(c=>c.character_id===patient.id).location_character_id;
        await request(combat+'/start',{location_character_ids:[actorId,targetId],initiator_location_character_id:actorId,initiator_first:true},token);
        const action=payload=>request(combat+'/action',{location_character_id:actorId,...payload},token);
        await action({action_key:'draw_weapon',weapon_index:0});await action({action_key:'aim',weapon_index:0,target_character_id:patient.id});
        const tree=await request(loc+'/objects',{type:'tree',name:'Ствол проверки',tile_x:41,tile_y:49,
            properties:{dimensions:{width:8,depth:8,height:5}}},token);
        const beforeTree=(await read()).data.health.current;
        const treeShot=await action({action_key:'attack',weapon_index:0,fire_mode:'unaimed',shot_count:1,action_points:2,target_character_id:patient.id});
        assert.ok(treeShot.attack.results.some(hit=>hit.blocked_by_cover&&hit.cover_hit.protection===20));
        assert.equal((await read()).data.health.current,beforeTree);
        await request(api+'/locations/objects/'+tree.id,null,token,'DELETE');
        let shots=0;
        for(let attempt=0;attempt<8;attempt++){
            state=await request(combat,null,token,'GET');
            if(state.current_character.action_points_current<3){await request(combat+'/end_turn',{location_character_id:actorId},token);await request(combat+'/end_turn',{location_character_id:targetId},token);}
            const shot=await action({action_key:'attack',weapon_index:0,fire_mode:'aimed',shot_count:1,action_points:2,target_character_id:patient.id,target_zone:'left_arm'});shots++;
            if((await read()).data.health.effects.some(e=>['mangled_limb','amputation','fracture'].includes(e.type)))break;
        }
        const wounded=(await read()).data;assert.ok(wounded.health.zones.leftArm.current<100,'Оружие действительно повредило руку');
        await request(combat+'/end',{},token);
        for(const client of [page,peer]){await client.evaluate(`window.openCharacterSheet(${patient.id})`);await client.until('document.querySelector("#effects-container")','Лист после выстрелов');}
        await page.evaluate('document.querySelector("#effects-container").scrollIntoView({block:"center"})');await screenshot('health-after-weapon');
        const hover=async selector=>{
            await page.call('Page.bringToFront');
            await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
            await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
            const point=await page.evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
            try{await page.until('document.querySelector("#delayed-item-tooltip")?.style.display==="block"','Подсказка появилась')}catch(error){
                const evidence=await page.evaluate(`(()=>{const t=document.querySelector('#delayed-item-tooltip'),a=document.querySelector(${JSON.stringify(selector)}),n=document.elementFromPoint(${point.x},${point.y});return{point:${JSON.stringify(point)},anchor:a?.outerHTML.slice(0,300),at:n?.outerHTML.slice(0,300),tip:{display:t?.style.display,text:t?.innerText,visibility:t&&getComputedStyle(t).visibility}}})()`);
                fs.writeFileSync(path.join(safeSession.artifacts,'hover-evidence.json'),JSON.stringify(evidence,null,2));throw error;
            }
            return page.evaluate(`(()=>{const t=document.querySelector('#delayed-item-tooltip'),a=document.querySelector(${JSON.stringify(selector)}).closest('[data-item-template-id],[data-item-tooltip],.loadout-slot');const r=t.getBoundingClientRect(),s=a.getBoundingClientRect();return{text:t.innerText,within:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,separate:r.right<=s.left||r.left>=s.right||r.bottom<=s.top||r.top>=s.bottom}})()`);
        };
        await click(page,'[data-tab="equipment"]');
        await click(page,'.equipment-panel-tab[data-panel="inventory"]');
        const medTooltip=await hover('[data-item-template-id="'+meds[1].templateId+'"] strong');assert.ok(medTooltip.within&&medTooltip.separate);assert.ok(medTooltip.text.includes('Боль: -1'));assert.ok(!medTooltip.text.includes('pain_delta'));await screenshot('inventory-medicine-tooltip');
        const longState=await read();
        longState.data.inventory.pockets.push({id:'audit-no-template',name:'Предмет с подробным описанием',category:'misc',weight:.1,volume:.1,quantity:1,description:'Дополнительные сведения. '.repeat(90)+'Конец длинного описания'});
        await request('/lobbies/characters/'+patient.id,{data:longState.data},token,'PUT');
        await page.evaluate(`window.openCharacterSheet(${patient.id},'inventory')`);
        const ownSelector='[data-path="inventory,pockets,'+meds.length+'"] input[data-widget-key="name"]';
        await page.until(`document.querySelector(${JSON.stringify(ownSelector)})`,'Предмет без каталожного шаблона виден');
        const longTooltip=await hover(ownSelector);assert.ok(longTooltip.within&&longTooltip.text.includes('Конец длинного описания'));
        const wheel=await page.evaluate(`(()=>{const t=document.querySelector('#delayed-item-tooltip'),r=t.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2,scrollable:t.scrollHeight>t.clientHeight}})()`);assert.ok(wheel.scrollable);
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:wheel.x,y:wheel.y});await page.call('Input.dispatchMouseEvent',{type:'mouseWheel',x:wheel.x,y:wheel.y,deltaX:0,deltaY:3000});
        await page.until('document.querySelector("#delayed-item-tooltip").scrollTop>0&&document.querySelector("#delayed-item-tooltip").style.display==="block"','Длинная подсказка прокручивается и не исчезает');await screenshot('inventory-long-tooltip');
        await click(page,'[data-tab="equipment"]');const equippedTooltip=await hover('.loadout-slot[data-equipment-open="cosmetic"].is-equipped');assert.ok(equippedTooltip.within);assert.ok(equippedTooltip.text.includes('17 / 80'));assert.ok(equippedTooltip.text.includes('Термическая'));await screenshot('equipment-current-tooltip');
        // Переноска настоящего персонажа и сетевое перемещение второго токена.
        const bodyData=(await read()).data;
        bodyData.health.effects.push({type:'unconsciousness',name:'Без сознания',tick:'manual',source:'audit'});
        await request('/lobbies/characters/'+patient.id,{data:bodyData},token,'PUT');
        for(const client of [page,peer])await client.evaluate(`window.__bodyMoves=[];socket.on('character_moved',event=>window.__bodyMoves.push(event));true`);
        const move=async(x,y)=>{
            await page.evaluate(`void socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${shooter.id},x:${x},y:${y}})`);
            await page.until(`window.__bodyMoves.some(e=>e.character_id===${shooter.id}&&e.x===${x}&&e.y===${y})`,'Носильщик перемещён');
        };
        await move(41,49);
        await request(loc+'/characters/'+patient.id+'/body-transport',{actor_location_character_id:actorId,mode:'drag'},token);
        await move(42,49);
        for(const client of [page,peer])await client.until(`window.__bodyMoves.some(e=>e.character_id===${patient.id}&&e.x===41&&e.y===49)`,'Тело прошло за носильщиком у обоих клиентов');
        await request(loc+'/characters/'+patient.id+'/body-transport',{actor_location_character_id:actorId,mode:'stop'},token);
        const carrier=await request('/lobbies/characters/'+shooter.id,null,token,'GET');
        carrier.data.features={...(carrier.data.features||{}),specialTraits:[{name:'Санитар'}]};
        carrier.data.inventory.pockets.push({id:'audit-rope',name:'Канат для переноски',category:'misc',quantity:1,attributes:{body_carry_rope:true}});
        await request('/lobbies/characters/'+shooter.id,{data:carrier.data},token,'PUT');
        await request(loc+'/characters/'+patient.id+'/body-transport',{actor_location_character_id:actorId,mode:'shoulder'},token);
        await move(42,50);
        for(const client of [page,peer])await client.until(`window.__bodyMoves.some(e=>e.character_id===${patient.id}&&e.x===42&&e.y===50)`,'Переноска на плече синхронизирована');
        await request(loc+'/characters/'+patient.id+'/body-transport',{actor_location_character_id:actorId,mode:'stop'},token);
        await request(loc+'/characters/'+patient.id+'/body-transport',{actor_location_character_id:actorId,mode:'rope'},token);
        await move(43,50);
        const carried=await read();
        assert.equal(carried.owner_id??carried.ownerId,patient.owner_id??patient.ownerId);
        await request(loc+'/characters/'+patient.id+'/body-transport',{actor_location_character_id:actorId,mode:'stop'},token);
        // Три сна не заменяют сохранённую алкогольную отключку обычным -75.
        await page.evaluate('window.closeCharacterSheet()');await peer.evaluate('window.closeCharacterSheet()');
        await page.evaluate(`void socket.emit('send_message',{token:localStorage.getItem('access_token'),lobby_id:${lobby.id},message:'Сообщение проверки журнала'})`);
        await page.until('document.querySelector("#chat")?.textContent.includes("Сообщение проверки журнала")','Сообщение доставлено');
        await click(page,'#actions-toggle');
        assert.equal(await page.evaluate('document.querySelector("#panel-chat").dataset.chatView'),'actions');
        assert.ok(await page.evaluate('Array.from(document.querySelectorAll("#chat .chat-human")).every(n=>getComputedStyle(n).display==="none")'));
        assert.ok(await page.evaluate('document.querySelectorAll("#chat .chat-event").length>0'));
        const historyCount=await page.evaluate('document.querySelectorAll("#chat .message").length');
        await page.evaluate('window.__historyReloaded=false;socket.once("chat_history",()=>{window.__historyReloaded=true});socket.disconnect();socket.connect();true');
        await page.until('window.__historyReloaded','Сервер повторно доставил историю после переподключения');
        await page.until(`window.socket?.connected&&window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена восстановлена после переподключения');
        assert.equal(await page.evaluate('document.querySelectorAll("#chat .message").length'),historyCount);
        await screenshot('recent-actions');
        await click(page,'#chat-toggle');
        assert.equal(await page.evaluate('document.querySelector("#panel-chat").dataset.chatView'),'all');
        await click(page,'#chat-close');
        const drunk=await request(api+'/characters',{name:'Отключка аудита',data:{...data,inventory:{pockets:[],backpack:[]},health:{current:700,max:700,intoxication:170,effects:[]}}},token);
        let snapshot=await request('/lobbies/characters/'+drunk.id,null,token,'GET');assert.equal(snapshot.data.health.effects.find(e=>e.source==='deadly_intoxication').remaining_seconds,86400);
        await request('/lobbies/characters/'+drunk.id+'/visibility',{visible_to:[player.user_id],editable_to:[]},token,'PUT');
        for(const client of [page,peer]){await client.evaluate(`window.openCharacterSheet(${drunk.id})`);await client.until('document.querySelector("#effects-container")?.textContent.includes("Отключка от опьянения")','Отключка видна обоим клиентам');}
        await page.evaluate('window.openCharacterSleepSetup()');
        await page.until('document.querySelector("#character-sleep-setup-modal [data-sleep-mode]")','Выбор места сна открыт');
        assert.equal(await page.evaluate('Array.from(document.querySelector("#character-sleep-setup-modal [data-sleep-mode]").options).find(option=>option.value==="tent").disabled'),true);
        assert.equal(await page.evaluate('Array.from(document.querySelector("#character-sleep-setup-modal [data-sleep-mode]").options).find(option=>option.value==="sleeping_bag").disabled'),true);
        await page.evaluate('document.querySelector("#character-sleep-setup-modal [data-sleep-mode]").value="bed";document.querySelector("#character-sleep-setup-modal [data-sleep-save]").click()');
        await page.until('!document.querySelector("#character-sleep-setup-modal")','Место сна сохранено');
        snapshot=await request('/lobbies/characters/'+drunk.id,null,token,'GET');assert.equal(snapshot.data.health.combatMeta.sleepMode,'bed');
        for(let n=0;n<3;n++)await request(api+'/rest',{type:'sleep',character_ids:[drunk.id]},token);
        for(const client of [page,peer])await client.until(`document.querySelector('[data-widget-block="health-summary"]')?.parentElement.textContent.includes('Опьянение: 100')&&document.querySelector('#effects-container')?.textContent.includes('24 ч')`,'Повторные сутки синхронизированы');

        snapshot=await request('/lobbies/characters/'+drunk.id,null,token,'GET');assert.equal(snapshot.data.health.intoxication,100);assert.equal(snapshot.data.health.effects.find(e=>e.source==='deadly_intoxication').remaining_seconds,86400);
        const after=copy(snapshot.data);after.health.intoxication+=20;
        await request('/lobbies/characters/'+drunk.id,{data:after},token,'PUT');
        const evidence=await(await fetch(base+'/__health_audit/'+safeSession.runId)).json();assert.equal(evidence.draws.length,3);
        for(const [name,maximum] of [['Стимулятор Скала',840],['Стимулятор Скала-Н',1040]]){
            await page.evaluate('window.closeCharacterSheet()');
            const skala=await request(api+'/templates',{name,category:'consumable',weight:0,volume:.2,
                attributes:{consumable:controls.skala_profiles[name]}},token);
            const mountain=await request(api+'/characters',{name:'Гора · '+name,data:{
                basic:{background:{name:'Гора'}},health:{current:840,max:840,effects:[]},
                skills:{other:{medicine:{base:20}}},
                inventory:{pockets:[{id:'audit-skala-'+skala.id,templateId:skala.id+1000000,name:skala.name,category:'consumable',
                    quantity:1,attributes:copy(skala.attributes)}],backpack:[]},
            }},token);
            const mountainUrl='/lobbies/characters/'+mountain.id;
            const mountainBefore=(await request(mountainUrl,null,token,'GET')).data;
            assert.equal(mountainBefore.health.current,840);
            const outcome=await request(mountainUrl+'/medical-procedure',{operation_id:'audit-skala-mountain-'+skala.id,
                target_character_id:mountain.id,source:{path:['inventory','pockets',0],snapshot:mountainBefore.inventory.pockets[0]},
                application:{kind:'self',actionPoints:0}},token);
            assert.equal(outcome.medical_result.success,true);
            const boosted=(await request(mountainUrl,null,token,'GET')).data.health;
            assert.deepEqual([boosted.current,boosted.max,boosted.organs.heart.current,boosted.organs.heart.max],[maximum,maximum,40,40]);
            await page.evaluate(`window.openCharacterSheet(${mountain.id}).then(()=>true)`);
            await page.until(`document.querySelector('[name="health.current"]')?.value==='${maximum}'
                &&document.querySelector('[name="health.max"]')?.value==='${maximum}'
                &&document.querySelector('[name="health.organs.heart.current"]')?.value==='40'`,name+' в листе Горы');
            await request(api+'/characters/time-active',{character_ids:[mountain.id]},token,'PATCH');
            const clock=await request(api,null,token,'GET'), minute=clock.game_time_minutes+1;
            await request(api+'/time',{game_day:clock.game_day+Math.floor(minute/1440),game_time_minutes:minute%1440},token,'PATCH');
            await page.until(`document.querySelector('[name="health.current"]')?.value==='740'
                &&document.querySelector('[name="health.max"]')?.value==='840'
                &&document.querySelector('[name="health.organs.heart.current"]')?.value==='20'`,name+' вернул обычные ОЗ органов');
            console.log(JSON.stringify({skalaMountain:true,name,pool:[840,maximum,740],organHealth:[20,40,20]}));
        }
        fs.writeFileSync(path.join(safeSession.artifacts,'health-audit.json'),JSON.stringify({status:'PASS',shots,manualEffect:true,instantManualEffectOnce:true,worldClockMedication:true,treeTrunkProtection:20,bodyTransport:['drag','shoulder','rope'],recentActions:{reconnectWithoutDuplicates:true,humanMessagesHidden:true},medicines:meds.map(m=>m.name),woundedZones:wounded.health.zones.leftArm,tooltips:{medTooltip,equippedTooltip,longTooltip},intoxication:{draws:evidence.draws,after24h:snapshot.data.health.intoxication},twoClients:true},null,2));
    }finally{await peer?.close();if(context)await protocol?.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});await protocol?.close();}
};
