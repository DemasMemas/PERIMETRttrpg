// Настоящий бросок и личные реакции двух клиентов на неизменённой копии Припяти.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession,location,actor,player})=>{
    const url=`/lobbies/${lobby.id}/locations/${location.id}`,state=()=>request(url+'/combat',null,token,'GET');
    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    let peer,context,completed=false;
    const click=async(client,p)=>{
        await client.call('Page.bringToFront');
        await client.call('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',buttons:1,clickCount:1});
        await client.call('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',buttons:0,clickCount:1});
    };
    const button=async(client,selector)=>{
        await client.until(`document.querySelector(${JSON.stringify(selector)})?.getClientRects().length`,'кнопка доступна');
        const p=await client.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await click(client,p);
    };
    const targetPoint=async(client,x,y)=>client.evaluate(`(()=>{locationControls.object.updateMatrixWorld(true);const p=new T.Vector3(${x+.5},1,${y+.5}).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
    const enter=async client=>{
        await client.until('typeof enterLocation==="function"&&window.socket?.connected','комната готова');
        await client.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${location.id})})()`);
        await client.until('window.locationControls&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','Припять загружена');
        await client.evaluate(`L.setLocationEditMode(false);locationControls.enableDamping=false;locationControls.target.set(41.5,1,49.5);locationControls.object.position.set(47,12,59);locationControls.update();window.grenadeEvents=[];window.grenadeSheets=[];void socket.on('combat_explosion',e=>grenadeEvents.push(e));void socket.on('character_data_updated',e=>grenadeSheets.push(e))`);
    };
    try{
        const fresh=await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
        await request(`/lobbies/characters/${actor.id}`,{data:{...fresh.data,weapons:[],equipment:{},
            skills:{physical:{strength:{base:10},agility:{base:10},will:{base:10}},other:{tactics:{base:80}}},
            health:{current:700,max:700,painLevel:0,effects:[]},
            inventory:{pockets:[{id:'grenade-test',name:'Граната РГД-5',category:'grenade',quantity:2,attributes:{}}]}}},token,'PUT');
        const protection={durability:10000,maxDurability:10000,protection:{physical:80},attributes:{}};
        const victim=await request(`/lobbies/${lobby.id}/characters`,{name:'Цель гранаты',data:{basic:{is_npc:true},
            skills:{physical:{strength:{base:10},will:{base:10}}},equipment:{armor:{...protection,name:'Проверочная броня'},helmet:{...protection,name:'Проверочный шлем'}},
            health:{current:700,max:700,painLevel:0,effects:[]}}},token);
        await request(`/lobbies/characters/${victim.id}`,{visible_to:[player.user_id]},token,'PUT');
        await request(url+'/spawn_character',{character_id:victim.id,tile_x:42,tile_y:48},token);
        const outsider=await request(`/lobbies/${lobby.id}/characters`,{name:'Вне инициативы',data:{basic:{is_npc:true},
            skills:{physical:{strength:{base:10}}},equipment:{armor:{...protection,name:'Проверочная броня'},helmet:{...protection,name:'Проверочный шлем'}},
            health:{current:700,max:700,effects:[]}}},token);
        await request(url+'/spawn_character',{character_id:outsider.id,tile_x:43,tile_y:51},token);
        const initial=await state(),actorId=initial.characters.find(c=>c.character_id===actor.id).location_character_id,
            victimId=initial.characters.find(c=>c.character_id===victim.id).location_character_id,
            outsiderId=(await state()).characters.find(c=>c.character_id===outsider.id).location_character_id;
        context=(await protocol.call('Target.createBrowserContext')).browserContextId;
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer=await connect(tabs.find(t=>t.id===tab.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
        await peer.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
        await enter(peer);await enter(page);
        if(await page.evaluate('!document.querySelector("#panel-chat").hidden'))await button(page,'#chat-toggle');
        await page.evaluate(`(()=>{const list=document.querySelector('#panel-chat .chat-messages');for(let i=0;i<100;i++){const row=document.createElement('div');row.textContent='Проверка прокрутки '+i;list.append(row)}})()`);
        await button(page,'#chat-toggle');
        await page.until('(()=>{const e=document.querySelector("#panel-chat .chat-messages");return e.scrollHeight>e.clientHeight&&e.scrollHeight-e.clientHeight-e.scrollTop<2})()','чат открыт в конце');
        await page.evaluate('document.querySelector("#panel-chat .chat-messages").scrollTop=0');
        await button(page,'#chat-toggle');await button(page,'#chat-toggle');
        await page.until('(()=>{const e=document.querySelector("#panel-chat .chat-messages");return e.scrollHeight-e.clientHeight-e.scrollTop<2})()','повторное открытие чата в конце');
        await button(page,'#chat-toggle');
        for(const client of [page,peer])for(const id of [actor.id,victim.id]){
            const ack=await client.evaluate(`new Promise(resolve=>socket.emit('join_character',{character_id:${id},token:localStorage.getItem('access_token')},resolve))`);
            assert.equal(ack.ok,true,'разрешённая подписка на лист');
        }
        await request(url+'/combat/start',{location_character_ids:[actorId,victimId],initiator_location_character_id:actorId,initiator_first:true},token);
        await peer.until(`L.getCombatState()?.current_character?.location_character_id===${actorId}`,'ход метающего');
        await peer.evaluate(`window.openCharacterSheet(${actor.id},'inventory')`);
        await button(peer,'#sheet-tab-inventory [data-path="inventory,pockets,0"] [data-widget-key="use"]');
        await button(peer,'#grenade-fuse-modal [data-fuse="delay"]');
        await peer.until('!document.querySelector("#grenade-fuse-modal")&&document.querySelector("#combat-status-panel")?.textContent.includes("Выбор:")','выбор точки взрыва включён');
        await peer.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
        const target=await targetPoint(peer,41,51);
        await peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...target,buttons:0});await click(peer,target);
        for(const client of [page,peer])await client.until('document.querySelector(".explosion-gm-choices")','ожидание решения ГМа');
        assert.equal((await request(`/lobbies/characters/${outsider.id}`,null,token,'GET')).data.health.current,700);
        assert.equal(await peer.evaluate('document.querySelector("[data-explosion-choice]")===null'),true,'игрок не принимает решение ГМа');
        await button(page,`[data-explosion-choice="exclude"][data-actor-id="${outsiderId}"]`);
        for(const client of [page,peer])await client.until('L.getCombatState()?.current_character?.grenade_reaction','окно после броска');
        let current=(await state()).current_character;
        assert.equal(current.action_points_current,0,'пять ОД оплачивают доставание, бросок и задержку');
        assert.equal(current.grenade_reaction.held_action_points,0);
        const pending=(await state()).pending_explosives;
        assert.equal(pending.length,1);assert.equal(pending[0].trigger,'short_fuse');
        assert.deepEqual([pending[0].x,pending[0].y],[41,51]);
        assert.equal((await request(`/lobbies/characters/${victim.id}`,null,token,'GET')).data.health.current,700,'цель ещё не получила урон');
        await peer.evaluate(`L.showCombatActionMenu(innerWidth/2,innerHeight/2,${actor.id})`);
        const gain=await peer.evaluate(`(()=>{const b=document.querySelector('#combat-action-menu [aria-label="Получить ОП"]');if(!b||b.getAttribute('aria-disabled')==='true')throw Error('Получение ОП недоступно: '+b?.dataset.unavailableReason);const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await click(peer,gain);
        await peer.until('L.getCombatState().current_character.movement_points_current===5','половина получаемых ОП');
        await peer.evaluate(`L.showMovementTypeMenu(${actor.id})`);
        const walk=await peer.evaluate(`(()=>{const b=[...document.querySelectorAll('#combat-movement-type-menu button')].find(b=>b.querySelector('strong')?.textContent.trim()==='Ходьба');const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await click(peer,walk);const exit=await targetPoint(peer,41,46);
        await peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...exit,buttons:0});
        await peer.until('L.movementHintText().includes("Спиной / боком:")','выход из зоны взрыва');await click(peer,exit);
        for(const client of [page,peer])await client.until(`(()=>{const c=L.getCombatState()?.current_character;return c?.y===46&&!L.characterSpatialPose(${actor.id})?.animating})()`,'выход синхронизирован');
        await button(peer,'.combat-end-turn-btn');
        for(const client of [page,peer])await client.until(`L.getCombatState()?.current_character?.location_character_id===${actorId}&&!L.getCombatState()?.current_character?.grenade_reaction`,'продолжение того же хода');
        current=(await state()).current_character;
        assert.equal(current.movement_points_current,6);assert.equal(current.action_points_current,0);
        assert.equal((await request(`/lobbies/characters/${actor.id}`,null,token,'GET')).data.health.current,700,'вышедший из радиуса не повреждён');
        await button(peer,'.combat-end-turn-btn');
        for(const client of [page,peer])await client.until(`L.getCombatState()?.current_character?.location_character_id===${victimId}&&L.getCombatState().current_character.grenade_reaction`,'личная реакция следующей цели');
        current=(await state()).current_character;assert.equal(current.action_points_current,2);assert.equal(current.grenade_reaction.held_action_points,3);
        assert.equal((await request(`/lobbies/characters/${victim.id}`,null,token,'GET')).data.health.current,700);
        await request(url+'/combat/action',{location_character_id:victimId,action_key:'narrative_action',narrative_action_name:'Реакция',action_points:1,narrative_roll_required:false},token);
        await button(page,'.combat-end-turn-btn');
        for(const client of [page,peer])await client.until(`!L.getCombatState()?.current_character?.grenade_reaction&&grenadeEvents.some(e=>e.explosive?.explosion?.targets?.some(t=>t.character_id===${victim.id}))`,'взрыв доставлен обоим клиентам');
        const saved=await request(`/lobbies/characters/${victim.id}`,null,token,'GET');
        assert.ok(saved.data.health.current<700&&saved.data.health.current>0);
        assert.equal((await state()).current_character.action_points_current,4,'оставшиеся ОД возвращаются после личной реакции');
        assert.equal((await state()).pending_explosives.length,0);
        assert.equal((await request(`/lobbies/characters/${outsider.id}`,null,token,'GET')).data.health.current,700,'исключённая цель не повреждена');
        for(const client of [page,peer])await client.until(`grenadeSheets.some(e=>e.character_id===${victim.id}&&e.updates?.data?.health?.current===${saved.data.health.current})`,'лист цели обновлён у обоих');
        await peer.call('Page.reload');
        for(let attempt=0;;attempt++)try{await enter(peer);break;}catch(e){if(attempt>=3||!/Inspected target navigated or closed|Execution context was destroyed|Cannot find context/.test(e.message))throw e;}
        assert.equal((await state()).pending_explosives.length,0);
        assert.equal((await request(`/lobbies/characters/${victim.id}`,null,token,'GET')).data.health.current,saved.data.health.current);
        // Отдельная проверка штатного лечения контузии; гарантированный успех навыком, без подмены d20.
        const treatment=await request(`/lobbies/characters/${victim.id}`,null,token,'GET');
        await request(`/lobbies/characters/${victim.id}`,{data:{...treatment.data,
            skills:{...treatment.data.skills,physical:{...treatment.data.skills.physical,will:{base:80}}},
            health:{...treatment.data.health,effects:[{type:'concussion',name:'Контузия',remaining:5,tick:'round_end',source:'test'}]}}},token,'PUT');
        await request(url+'/combat/adjust',{location_character_id:victimId,action_points:0},token);
        await page.until('L.getCombatState()?.current_character?.effects?.some(e=>e.type==="concussion")','контузия видна в состоянии');
        await page.evaluate(`L.showCombatActionMenu(innerWidth/2,innerHeight/2,${victim.id})`);
        assert.equal(await page.evaluate('document.querySelector(\'#combat-action-menu [aria-label="Преодолеть контузию"]\').dataset.cost'),'2 ОД');
        await button(page,'#combat-action-menu [aria-label="Преодолеть контузию"]');
        for(const client of [page,peer])await client.until('L.getCombatState()?.current_character?.action_points_current===2&&!L.getCombatState()?.current_character?.effects?.some(e=>e.type==="concussion")','контузия снята за два ОД у обоих');
        // Тушение водой через штатное меню: достать ёмкость, затем облить себя.
        const burning=await request(`/lobbies/characters/${victim.id}`,null,token,'GET');
        await request(`/lobbies/characters/${victim.id}`,{data:{...burning.data,
            inventory:{pockets:[{id:'water-browser',name:'Вода',volume:2,maxUses:3,uses:3,quantity:1,attributes:{uses:3}}]},
            health:{...burning.data.health,effects:[{type:'burning',name:'Горение',remaining:8,tick:'round_end',source:'browser-fire'}]}}},token,'PUT');
        await request(url+'/combat/adjust',{location_character_id:victimId,action_points:5},token);
        await page.until('L.getCombatState()?.current_character?.effects?.some(e=>e.type==="burning")','горение синхронизировано');
        await page.evaluate(`L.showCombatActionMenu(innerWidth/2,innerHeight/2,${victim.id})`);
        await button(page,'#combat-action-menu [aria-label="Огонь и вода"]');
        await button(page,'#combat-fire-water-menu .fire-water-items button');
        for(const client of [page,peer])await client.until('L.getCombatState()?.current_character?.water_in_hand?.litres===2','вода в руке у обоих клиентов');
        await page.evaluate(`L.showCombatActionMenu(innerWidth/2,innerHeight/2,${victim.id})`);
        await button(page,'#combat-action-menu [aria-label="Огонь и вода"]');
        await button(page,'#combat-fire-water-menu .fire-pour');
        for(const client of [page,peer])await client.until('!L.getCombatState()?.current_character?.water_in_hand&&L.getCombatState()?.current_character?.effects?.some(e=>e.type==="burning"&&e.remaining===4)','два литра сокращают горение на четыре раунда');
        const watered=await request(`/lobbies/characters/${victim.id}`,null,token,'GET');
        assert.equal(watered.data.inventory.pockets.length,0,'вода расходуется на сервере');
        assert.equal((await state()).current_character.action_points_current,4,'доставание и обливание стоили три ОД');
        // Второй взрыв — мгновенный: тот же персонаж вне боя добавляется решением ГМа.
        const next=await request(`/lobbies/characters/${victim.id}`,null,token,'GET');
        await request(`/lobbies/characters/${victim.id}`,{data:{...next.data,
            skills:{...next.data.skills,other:{tactics:{base:80}}},inventory:{pockets:[{name:'Граната РГН',category:'grenade',quantity:1,attributes:{}}]}}},token,'PUT');
        await request(url+'/combat/adjust',{location_character_id:victimId,action_points:3},token);
        await request(url+'/combat/action',{location_character_id:victimId,action_key:'explosive_attack',explosive_source:'hand',explosive_fuse_mode:'delay',
            item_path:['inventory','pockets',0],target_x:41,target_y:51},token);
        await page.until(`document.querySelector('[data-explosion-choice="include"][data-actor-id="${outsiderId}"]')`,'решение для мгновенного взрыва');
        assert.equal((await request(`/lobbies/characters/${outsider.id}`,null,token,'GET')).data.health.current,700);
        await button(page,`[data-explosion-choice="include"][data-actor-id="${outsiderId}"]`);
        await page.until(`L.getCombatState().turn_order.includes(${outsiderId})&&!L.getCombatState().pending_explosives.length`,'добавление и мгновенный взрыв');
        assert.ok((await request(`/lobbies/characters/${outsider.id}`,null,token,'GET')).data.health.current<700);
        fs.writeFileSync(path.join(safeSession.artifacts,'grenades.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        fs.writeFileSync(path.join(safeSession.artifacts,'grenades.json'),JSON.stringify({scene:'копия Припяти',twoClients:true,ownerReactedAndEscaped:true,victimReactedBeforeDamage:true,heldResourcesRestored:true,reloadDidNotRepeatDamage:true,nativeConcussionRecovery:true,gmExcludedShortFuseTarget:true,gmIncludedInstantTarget:true,chatOpensAtEnd:true,victimHealth:saved.data.health.current},null,2));
        completed=true;console.log('Гранаты: настоящий бросок, две личные реакции, выход из радиуса, ОД/ОП, сетевой урон и reload — проверено.');
    }finally{
        if(!completed){
            fs.writeFileSync(path.join(safeSession.artifacts,'grenade-debug.json'),JSON.stringify({state:await state().catch(e=>({error:e.message})),clients:await Promise.all([page,peer].filter(Boolean).map(c=>c.evaluate('({hud:document.querySelector("#combat-status-panel")?.textContent,hint:L.movementHintText(),events:window.grenadeEvents})').catch(e=>({error:e.message}))))},null,2));
            if(peer)fs.writeFileSync(path.join(safeSession.artifacts,'grenade-failure.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        }
        peer?.close();if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context}).catch(()=>{});protocol.close();
    }
};
