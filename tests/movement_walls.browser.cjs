const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

module.exports = async ({page,request,token,lobby,base,connect,debugPort,safeSession,location,actor,player,fixture}) => {
    const url = `/lobbies/${lobby.id}/locations/${location.id}`;
    await request(url+'/spawn_character', {character_id:actor.id,tile_x:5,tile_y:5,assign_to_user_id:player.user_id},token);
    const version = await (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol = await connect(version.webSocketDebuggerUrl);
    let peer, context;
    try {
        context = await protocol.call('Target.createBrowserContext');
        const target = await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer = await connect(targets.find(item=>item.id===target.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable'); await peer.call('Runtime.enable');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
        await peer.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
        await peer.until('typeof enterLocation==="function" && window.socket?.connected','второй клиент готов');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');await enterLocation(${location.id})})()`);
        for (const client of [page,peer]) {
            await client.until(`(()=>{try{return L.characterPose(${actor.id}).x===5 && L.characterPose(${actor.id}).y===5}catch{return false}})()`,'исходная позиция у стены');
            await client.evaluate(`window.wallMoves=[];void window.socket.on('character_moved',event=>wallMoves.push(event))`);
        }
        const before = await request(url+'/movement-preview',{character_id:actor.id,x:5,y:6,floor_level:0},token);
        assert.ok(before.route,'проход вдоль реальной стены доступен');
        assert.equal(before.route.cost,1);
        await peer.evaluate(`L.findMovementPath(5,5,5,6,${actor.id},0)`);
        await peer.until(`L.navigationPreviewSettled(5,5,5,6,${actor.id},0)`,'получен серверный preview');
        const preview = await peer.evaluate(`L.findMovementPath(5,5,5,6,${actor.id},0)`);
        assert.deepEqual(preview,before.route);
        const across = await request(url+'/movement-preview',{character_id:actor.id,x:6,y:5,floor_level:0},token);
        assert.ok(!across.route || across.route.cost>1,'прямой проход через стену запрещён');
        await peer.evaluate(`void window.socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:5,y:6,floor_level:0})`);
        for (const client of [page,peer]) {
            await client.until(`L.characterPose(${actor.id}).x===5 && L.characterPose(${actor.id}).y===6 && wallMoves.some(e=>e.character_id===${actor.id}&&e.x===5&&e.y===6)`,'реальное перемещение и событие у двух клиентов');
        }
        const event = await peer.evaluate(`wallMoves.find(e=>e.character_id===${actor.id}&&e.x===5&&e.y===6)`);
        assert.equal(event.movement_cost,before.route.cost);
        const idle = await request(url+'/combat',null,token,'GET');
        const placed = idle.characters.find(c=>c.character_id===actor.id);
        const started=await request(url+'/combat/start',{location_character_ids:[placed.location_character_id],
            initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
        if(started.current_character.facing_x!==0||started.current_character.facing_y!==1)
            await request(url+'/combat/action',{location_character_id:placed.location_character_id,action_key:'change_facing',facing_x:0,facing_y:1,payment:'action'},player.access_token);
        await request(url+'/combat/action',{location_character_id:placed.location_character_id,
            action_key:'convert_free_action_to_movement'},player.access_token);
        const beforeBackward = await request(url+'/combat',null,token,'GET');
        await peer.evaluate(`void window.socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:5,y:5,floor_level:0,movement_mode:'walk'})`);
        for (const client of [page,peer]) {
            await client.until(`L.characterPose(${actor.id}).x===5 && L.characterPose(${actor.id}).y===5
                && L.characterPose(${actor.id}).fx===0 && L.characterPose(${actor.id}).fy===1
                && wallMoves.some(e=>e.character_id===${actor.id}&&e.y===5&&e.movement_mode==='backward_sideways')`,'шаг назад сохраняет взгляд у двух клиентов');
        }
        const afterBackward = await request(url+'/combat',null,token,'GET');
        assert.equal(afterBackward.current_character.movement_mode_this_turn,'backward_sideways');
        assert.equal(afterBackward.current_character.movement_points_current,beforeBackward.current_character.movement_points_current-2);
        assert.equal(afterBackward.current_character.action_points_current,beforeBackward.current_character.action_points_current);
        await request(url+'/combat/end',{},token);
        await request(url+'/spawn_character',{character_id:actor.id,tile_x:6.5,tile_y:3.5,assign_to_user_id:player.user_id},token);
        for(const client of [page,peer]) await client.until(`L.characterPose(${actor.id}).x===6.5&&L.characterPose(${actor.id}).y===3.5`,'дробная исходная позиция');
        const fractional = await request(url+'/movement-preview',{character_id:actor.id,x:6.5,y:5.25,floor_level:0},token);
        assert.ok(fractional.route,'исходный смещённый проём доступен');
        assert.ok(Math.abs(fractional.route.distance-1.75)<1e-8,'длина через проём не округляется');
        await peer.evaluate(`void window.socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:6.5,y:5.25,floor_level:0,movement_mode:'walk'})`);
        for(const client of [page,peer]) await client.until(`L.characterPose(${actor.id}).x===6.5&&L.characterPose(${actor.id}).y===5.25`,'проход через дверь у двух клиентов');
        await request(url+'/combat/start',{location_character_ids:[placed.location_character_id],initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
        for(const y of [5.125,5.25]) {
            await peer.evaluate(`void window.socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:6.5,y:${y},floor_level:0,movement_mode:'correction'})`);
            for(const client of [page,peer]) await client.until(`L.characterPose(${actor.id}).x===6.5&&L.characterPose(${actor.id}).y===${y}`,'дробная корректировка у двух клиентов');
        }
        const corrected=await request(url+'/combat',null,token,'GET');
        assert.equal(corrected.current_character.free_actions_current,0);
        assert.equal(corrected.current_character.movement_points_current,0);
        assert.equal(corrected.current_character.correction_distance_this_turn,.25);
        assert.equal(corrected.current_character.movement_distance_this_turn,.25);
        assert.equal(corrected.current_character.movement_mode_this_turn,null);
        await request(url+'/combat/action',{location_character_id:placed.location_character_id,action_key:'convert_free_action_to_movement'},player.access_token);
        const quarterBefore = await request(url+'/combat',null,token,'GET');
        await peer.evaluate(`void window.socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:6.5,y:5.5,floor_level:0,movement_mode:'walk'})`);
        for(const client of [page,peer]) await client.until(`L.characterPose(${actor.id}).x===6.5&&L.characterPose(${actor.id}).y===5.5`,'шаг 0,25 м у двух клиентов');
        const quarterAfter = await request(url+'/combat',null,token,'GET');
        assert.equal(quarterAfter.current_character.movement_points_current,quarterBefore.current_character.movement_points_current-.25);
        assert.equal(quarterAfter.current_character.movement_distance_this_turn,.5);
        await peer.evaluate('window.__wallBeforeReload=true');
        await peer.call('Page.reload');
        await peer.until('!window.__wallBeforeReload && typeof enterLocation==="function" && window.socket?.connected','перезагрузка второго клиента');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');await enterLocation(${location.id})})()`);
        await peer.until(`(()=>{try{const p=L.characterPose(${actor.id});return p.x===6.5&&p.y===5.5&&p.fx===0&&p.fy===1}catch{return false}})()`,'дробная позиция и взгляд сохранены после reload');
        await peer.evaluate(`locationControls.object.position.set(7,12,16);locationControls.target.set(7,1,6);locationControls.update()`);
        fs.writeFileSync(path.join(safeSession.artifacts,'fractional-character.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await request(url+'/combat/end',{},token);
        const sheet = await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
        await request(`/lobbies/characters/${actor.id}`,{data:{...sheet.data,weapons:[{
            id:'wall-test-rifle',name:'Проверочная винтовка',category:'weapon',ergonomics:0,
            weight:1,durability:100,maxDurability:100,ammo:3,
        }]}},token,'PUT');
        await request(url+'/spawn_character',{character_id:actor.id,tile_x:6.5,tile_y:3.5,assign_to_user_id:player.user_id},token);
        const enemy = await request(`/lobbies/${lobby.id}/characters`,{name:'Цель за углом',data:{}},token);
        await request(url+'/spawn_character',{character_id:enemy.id,tile_x:6.5,tile_y:4.5},token);
        const participants = (await request(url+'/combat',null,token,'GET')).characters;
        const enemyPlaced = participants.find(c=>c.character_id===enemy.id);
        await request(url+'/combat/start',{location_character_ids:[placed.location_character_id,enemyPlaced.location_character_id],
            initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
        const act = payload=>request(url+'/combat/action',{location_character_id:placed.location_character_id,...payload},player.access_token);
        await act({action_key:'draw_weapon',weapon_index:0});
        await act({action_key:'aim',weapon_index:0,target_character_id:enemy.id});
        const pendingId = 'walls-corner-aim';
        const paying = await act({action_key:'aim',weapon_index:0,target_character_id:enemy.id,pending_action_id:pendingId});
        assert.equal(paying.pending_action,true);
        await request(url+'/spawn_character',{character_id:enemy.id,tile_x:9,tile_y:5.5},token);
        await request(url+'/combat/end_turn',{location_character_id:enemyPlaced.location_character_id},token);
        const cancelled = await request(url+'/combat',null,token,'GET');
        assert.equal(cancelled.current_character.character_id,actor.id);
        assert.equal(cancelled.current_character.action_points_current,5);
        assert.equal(cancelled.current_character.deferred_action,null);
        await request(url+'/spawn_character',{character_id:enemy.id,tile_x:6.5,tile_y:4.5},token);
        const lateResume = await peer.evaluate(`(async()=>{const r=await fetch('${url}/combat/action',{
            method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('access_token')},
            body:JSON.stringify({location_character_id:${placed.location_character_id},action_key:'aim',resume_pending_action_id:'${pendingId}'})});return r.status})()`);
        assert.equal(lateResume,409,'восстановление условий не возвращает отменённое действие');
        await request(url+'/combat/end',{},token);
        for(const client of [page,peer]) await client.evaluate(`window.stairMoves=[];void window.socket.on('character_moved',e=>stairMoves.push(e))`);
        const stairs = [];
        for(const [sourceIndex,source] of fixture.objects.entries()) {
            if(source.type!=='stairs') continue;
            const p=source.properties,a=Number(p.rotation)||0,depth=p.dimensions.depth/2+.4;
            const x=source.tile_x+(p.offset_x||0),y=source.tile_y+(p.offset_z||0),level=p.level||0;
            const low={x:x-Math.sin(a)*depth,y:y-Math.cos(a)*depth,level};
            const high={x:x+Math.sin(a)*depth,y:y+Math.cos(a)*depth,level:level+1};
            await request(url+'/spawn_character',{character_id:actor.id,tile_x:low.x,tile_y:low.y,floor_level:low.level,assign_to_user_id:player.user_id},token);
            const steps=[];
            for(const [name,from,to] of [['up',low,high],['down',high,low]]) {
                for(const client of [page,peer]) await client.until(`(()=>{try{const p=L.characterPose(${actor.id});return Math.abs(p.x-${from.x})<1e-8&&Math.abs(p.y-${from.y})<1e-8&&L.characterSpatialPose(${actor.id}).level===${from.level}}catch{return false}})()`,'исходная площадка лестницы');
                const route=await request(url+'/movement-preview',{character_id:actor.id,x:to.x,y:to.y,floor_level:to.level},token);
                assert.ok(route.route,`лестница ${sourceIndex} ${name}: preview`);
                await peer.evaluate(`void window.socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:${to.x},y:${to.y},floor_level:${to.level},movement_mode:'walk'})`);
                for(const client of [page,peer]) await client.until(`(()=>{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id});return Math.abs(p.x-${to.x})<1e-8&&Math.abs(p.y-${to.y})<1e-8&&s.level===${to.level}&&!s.animating&&Math.abs(s.renderHeight-s.height)<1e-6&&stairMoves.some(e=>e.character_id===${actor.id}&&e.floor_level===${to.level}&&Math.abs(e.y-${to.y})<1e-8)})()`,'реальный переход этажа и завершение анимации у двух клиентов');
                const spatial=await peer.evaluate(`L.characterSpatialPose(${actor.id})`);
                assert.ok(Math.abs(spatial.x-(to.x+.5))<1e-6&&Math.abs(spatial.z-(to.y+.5))<1e-6);
                await peer.evaluate(`L.setLocationViewLevel(${to.level});locationControls.object.position.set(${x+.5},${spatial.height+9},${y+9});locationControls.target.set(${x+.5},${spatial.height},${y+.5});locationControls.update()`);
                if(name==='up'&&[15,49].includes(sourceIndex)) fs.writeFileSync(path.join(safeSession.artifacts,`stair-${sourceIndex}-up.png`),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
                steps.push({direction:name,from,to,cost:route.route.cost,spatial});
            }
            stairs.push({sourceIndex,building:p.building_id,steps});
            fs.writeFileSync(path.join(safeSession.artifacts,'stairs.json'),JSON.stringify(stairs,null,2));
            console.log(`PASS: лестница ${sourceIndex}, ${p.building_id}, этажи ${level}↔${level+1}, два клиента`);
        }
        assert.equal(stairs.length,7);
        await require('./navigation_reported.browser.cjs')({page,peer,request,token,lobby,safeSession,location,actor,player});
        fs.writeFileSync(path.join(safeSession.artifacts,'wall-geometry.json'),JSON.stringify({
            scene:'копия Припяти 72×72',wallSourceIndex:4,from:[5,5,0],to:[5,6,0],preview,event,
            across:across.route,twoClients:true,reloaded:true,stairCoverage:'all_saved_pripyat_stairs',
            correction:corrected.current_character,
            backward:{before:beforeBackward.current_character,after:afterBackward.current_character},
            fractional:fractional.route,quarter:{before:quarterBefore.current_character,after:quarterAfter.current_character},
            cancelledAtTurnStart:{actionId:pendingId,remainingAp:cancelled.current_character.action_points_current,lateResume},
            stairsChecked:stairs.length,
        },null,2));
        console.log('PASS: Припять, взгляд, дробный проход/ОП, два клиента, reload и окончательная отмена действия за углом');
    } finally {
        peer?.close();
        if(context) await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});
        protocol.close();
    }
};
