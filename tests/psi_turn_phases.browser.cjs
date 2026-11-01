const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

module.exports = async ({page,request,token,lobby,base,connect,debugPort,safeSession,location,actor,player}) => {
    const url=`/lobbies/${lobby.id}/locations/${location.id}`,sheetUrl=`/lobbies/characters/${actor.id}`;
    const version=await (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    let peer,context;
    try {
        context=await protocol.call('Target.createBrowserContext');
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer=await connect(tabs.find(item=>item.id===tab.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
        await peer.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
        await peer.until('typeof enterLocation==="function"&&window.socket?.connected','второй клиент готов');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');await enterLocation(${location.id})})()`);
        const subscribe=async client=>{
            await client.until('window.locationControls&&window.socket?.connected','Припять загружена');
            const ack=await client.evaluate(`new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Нет подписки')),10000);socket.emit('join_character',{character_id:${actor.id},token:localStorage.getItem('access_token')},ack=>{clearTimeout(timer);resolve(ack?.ok===true)})})`);
            assert.equal(ack,true);
            await client.evaluate(`window.psiStates=[];window.psiSheets=[];void socket.on('combat_state_updated',s=>psiStates.push(s));void socket.on('character_data_updated',e=>{if(e.character_id===${actor.id})psiSheets.push(e.updates.data)})`);
        };
        for(const client of [page,peer]) await subscribe(client);
        const npc=await request(`/lobbies/${lobby.id}/characters`,{name:'Ожидающий персонаж',data:{basic:{is_npc:true},health:{current:700,max:700,effects:[]}}},token);
        await request(url+'/spawn_character',{character_id:npc.id,tile_x:43,tile_y:48},token);
        const idle=await request(url+'/combat',null,token,'GET');
        const actorId=idle.characters.find(c=>c.character_id===actor.id).location_character_id;
        const npcId=idle.characters.find(c=>c.character_id===npc.id).location_character_id;
        const setActor=async (firearm=false,helmet=false,psy=50)=>{
            const fresh=await request(sheetUrl,null,token,'GET');
            await request(sheetUrl,{data:{...fresh.data,activeWeaponIndex:0,
                weapons:[{id:'psi-test-weapon',name:firearm?'Проверочный пистолет':'Проверочный клинок',
                    category:firearm?'firearm':'melee_weapon',ergonomics:0,ammo:1,durability:100,maxDurability:100,
                    attributes:{damage:firearm?50:20,armor_piercing:20,allowed_attacks:['Вспарывающий','Режущий','Колющий','Рубящий']}}],
                equipment:helmet?{helmet:{name:'Защита ушей',durability:100,maxDurability:100,
                    protection:{physical:40},attributes:{protection_zones:['ears']}}}:{},
                skills:{physical:{strength:{base:10,bonus:0},will:{base:10,bonus:0}}},
                health:{current:700,max:700,psyState:psy,painLevel:0,effects:[],zones:{head:{current:50,max:50}}}}},token,'PUT');
        };
        const start=first=>request(url+'/combat/start',{location_character_ids:[actorId,npcId],initiator_location_character_id:first,initiator_first:true},token);
        const end=(id,access=token)=>request(url+'/combat/end_turn',{location_character_id:id},access);
        await setActor();
        await start(npcId);
        let sheet=await request(sheetUrl,null,token,'GET');
        assert.equal(sheet.data.health.current,700,'ожидание своего хода не наносит урон');
        assert.equal(sheet.data.health.psyState,50);
        assert.ok(!sheet.data.health.combatMeta?.lastPsiTurnStart);
        const started=await end(npcId);
        assert.equal(started.current_location_character_id,actorId);
        assert.equal(started.current_character.action_points_current,5);
        assert.equal(started.current_character.free_actions_current,1);
        for(const client of [page,peer]) await client.until(`psiSheets.some(s=>s.health.current===680&&s.health.psyState===50&&s.health.zones.head.current===30)&&psiStates.some(s=>s.current_location_character_id===${actorId}&&s.psy_turn_start?.attack_type==='Рубящий')`,'бесплатная атака в начале хода у двух клиентов');
        for(let i=0;i<2;i++) await request(url+'/combat',null,token,'GET');
        sheet=await request(sheetUrl,null,token,'GET');
        assert.equal(sheet.data.health.current,680,'чтение не повторяет атаку');
        const finished=await end(actorId,player.access_token);
        assert.equal(finished.psy_crisis.reset_damage,500);
        for(const client of [page,peer]) await client.until('psiSheets.some(s=>s.health.current===180&&s.health.psyState===0&&s.health.painLevel===10)','сброс с уроном и Болью в конце собственного хода у двух клиентов');
        const afterReset=await request(sheetUrl,null,token,'GET');
        await peer.evaluate('window.__psiBeforeReload=true');
        await peer.call('Page.reload');
        await peer.until('!window.__psiBeforeReload&&typeof enterLocation==="function"&&window.socket?.connected','перезагрузка игрока');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');await enterLocation(${location.id})})()`);
        await subscribe(peer);
        const restored=await peer.evaluate(`(async()=>{const r=await fetch('${sheetUrl}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}});return (await r.json()).data.health})()`);
        assert.equal(restored.current,180);assert.equal(restored.psyState,0);assert.equal(restored.painLevel,10);
        await request(url+'/combat/end',{},token);
        await setActor(true,true);
        const protectedStart=await start(actorId);
        assert.equal(protectedStart.psy_turn_start.failure_reason,'armor_not_penetrated');
        assert.equal(protectedStart.psy_turn_start.armor_zone,'ears');
        assert.equal(protectedStart.psy_turn_start.armor,40);
        for(const client of [page,peer]) await client.until(`psiSheets.some(s=>s.weapons[0].ammo===0&&s.health.psyState===50)&&psiStates.some(s=>s.psy_turn_start?.failure_reason==='armor_not_penetrated')`,'шлем блокирует выстрел по ушам у двух клиентов');
        await request(url+'/combat/end',{},token);
        await setActor(true,false);
        await start(actorId);
        for(const client of [page,peer]) await client.until(`psiSheets.some(s=>s.weapons[0].ammo===0&&s.health.effects.some(e=>e.type==='death'&&e.source==='psy_state_suicide'))`,'выстрел без клина и защиты убивает в начале хода');
        const lethal=await request(sheetUrl,null,token,'GET');
        assert.equal(lethal.data.health.psyState,50,'мёртвому сброс не наносится');
        await request(url+'/combat/end',{},token);
        const sources = await require('./combat_sources.browser.cjs')({page,peer,request,token,lobby,
            url,actor,actorId,npc,npcId,player,setActor});
        fs.writeFileSync(path.join(safeSession.artifacts,'psi-phases.json'),JSON.stringify({
            scene:'копия Припяти',twoClients:true,start:started.psy_turn_start,reset:finished.psy_crisis,
            afterReset:afterReset.data.health,reloaded:restored,helmet:protectedStart.psy_turn_start,
            sources,lethal:{psyState:lethal.data.health.psyState,ammo:lethal.data.weapons[0].ammo,
                dead:lethal.data.health.effects.some(e=>e.type==='death')},
        },null,2));
        console.log('PASS: Припять, два клиента, атака в начале хода, сброс с Болью в конце, reload, защита ушей и смертельный выстрел');
    } finally {
        peer?.close();
        if(context) await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});
        protocol.close();
    }
};
