const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');

module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession,location,actor,player})=>{
    const url=`/lobbies/${lobby.id}/locations/${location.id}`,sheetUrl=`/lobbies/characters/${actor.id}`;
    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    let peer,context,actorId;
    const results=[];
    const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'jumps.json'),JSON.stringify({scene:'полная копия Припяти',twoClients:true,controlledD20:true,results},null,2));
    const click=async(client,point,button='left')=>{
        await client.call('Page.bringToFront');
        await client.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
        await client.call('Input.dispatchMouseEvent',{type:'mousePressed',...point,button,buttons:button==='right'?2:1,clickCount:1});
        await client.call('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button,buttons:0,clickCount:1});
    };
    const button=async(client,selector)=>{
        await client.until(`!!document.querySelector(${JSON.stringify(selector)})&&!document.querySelector(${JSON.stringify(selector)}).disabled`,'кнопка прыжка');
        return client.evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    };
    const world=async(client,x,y,height)=>client.evaluate(`(()=>{locationControls.object.updateMatrixWorld(true);const p=new T.Vector3(${x+.5},${height},${y+.5}).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
    const roll=async value=>request(`/__testing_jump_rolls/${safeSession.runId}`,{rolls:[value]},token);
    const act=payload=>request(url+'/combat/action',{location_character_id:actorId,...payload},player.access_token);
    const settle=async(x,y,level=0)=>{
        for(const client of [page,peer])await client.until(`(()=>{try{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id});return Math.abs(p.x-${x})<1e-7&&Math.abs(p.y-${y})<1e-7&&s.level===${level}&&!s.animating&&Math.abs(s.renderHeight-s.height)<1e-6}catch{return false}})()`,'одинаковое приземление и завершение анимации');
    };
    const setup=async({x=41,y=48,level=0,strength=10}={})=>{
        const state=await request(url+'/combat',null,token,'GET');
        if(state.status==='active')await request(url+'/combat/end',{},token);
        const fresh=await request(sheetUrl,null,token,'GET');
        await request(sheetUrl,{data:{...fresh.data,weapons:[],equipment:{},
            skills:{physical:{strength:{base:strength,bonus:0},agility:{base:10,bonus:0}}},
            health:{current:700,max:700,painLevel:0,psyState:0,effects:[]}}},token,'PUT');
        await request(url+'/spawn_character',{character_id:actor.id,tile_x:x,tile_y:y,floor_level:level,force_relocate:true},token);
        actorId=(await request(url+'/combat',null,token,'GET')).characters.find(c=>c.character_id===actor.id).location_character_id;
        await request(url+'/combat/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        for(const client of [page,peer])await client.evaluate(`L.setLocationEditMode(false);L.setLocationViewLevel(${level});locationControls.object.position.set(${x+.5},${level*2.4+12},${y+10});locationControls.target.set(${x+.5},${level*2.4+1},${y+.5});locationControls.update()`);
        await settle(x,y,level);
    };
    try{
        context=await protocol.call('Target.createBrowserContext');
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer=await connect(tabs.find(t=>t.id===tab.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
        await peer.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
        await peer.until('typeof enterLocation==="function"&&window.socket?.connected','второй клиент готов');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${location.id})})()`);
        for(const client of [page,peer]){
            await client.until(`(()=>{try{return Boolean(L.characterPose(${actor.id})&&window.locationControls)}catch{return false}})()`,'Припять загружена');
            await client.evaluate(`window.jumpMoves=[];window.jumpFrames=[];window.jumpObjectUpdates=[];void socket.on('character_moved',e=>{if(e.jump){jumpMoves.push(e);const until=performance.now()+800;const frame=()=>{try{jumpFrames.push(L.characterSpatialPose(${actor.id}).renderHeight)}catch{}if(performance.now()<until)requestAnimationFrame(frame)};requestAnimationFrame(frame)}});void socket.on('location_object_updated',e=>jumpObjectUpdates.push(e))`);
        }
        await setup();
        const centre=await peer.evaluate(`(()=>{locationControls.object.updateMatrixWorld(true);const v=new T.Vector3(...L.characterModelCenterForTest(${actor.id})).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.x+(v.x+1)*r.width/2,y:r.y+(1-v.y)*r.height/2}})()`);
        await click(peer,centre,'right');
        await click(peer,await button(peer,'[aria-label="Прыжок"]'));
        await click(peer,await button(peer,'[data-jump-mode="walk"]'));
        const height=await peer.evaluate(`L.characterSpatialPose(${actor.id}).height`);
        const destination=await world(peer,42.7,48,height);
        await peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...destination});
        await peer.until(`L.movementHintText().includes('Прыжок: 1.7')&&L.movementHintText().includes('СЛ10')`,'серверный дробный предпросмотр прыжка');
        fs.writeFileSync(path.join(safeSession.artifacts,'jump-preview.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await roll(10);await click(peer,destination);await settle(42.7,48);
        let state=await request(url+'/combat',null,token,'GET');
        assert.equal(state.current_character.action_points_current,3);
        for(const client of [page,peer])assert.ok(await client.evaluate(`jumpMoves.some(e=>e.jump&&Math.abs(e.x-42.7)<1e-7)&&Math.max(...jumpFrames)>${height+.15}`),'реальное событие и дуга прыжка');
        results.push({case:'native standing jump',x:42.7,ap:3});save();

        await setup();
        const origin=await peer.evaluate(`(()=>{locationControls.object.updateMatrixWorld(true);const v=new T.Vector3(...L.characterModelCenterForTest(${actor.id})).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.x+(v.x+1)*r.width/2,y:r.y+(1-v.y)*r.height/2}})()`);
        await click(peer,origin,'right');await click(peer,await button(peer,'[aria-label="Прыжок"]'));
        await click(peer,await button(peer,'[data-jump-mode="run"]'));
        await peer.until(`window.locationCombatState?.current_character?.movement_mode_this_turn==='run'`,'бег выбран без метров разбега');
        state=await request(url+'/combat',null,token,'GET');
        assert.equal(state.current_character.x,41);assert.equal(state.current_character.movement_distance_this_turn,0);
        assert.equal(state.current_character.action_points_current,3);
        await roll(5);
        const runningTarget=await world(peer,44.5,48,height);
        await peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...runningTarget});
        await peer.until(`L.movementHintText().includes('СЛ6')`,'предпросмотр прыжка с разбега');
        await click(peer,runningTarget);await settle(42.75,48);
        state=await request(url+'/combat',null,token,'GET');
        assert.equal(state.current_character.action_points_current,1);
        assert.ok(!state.current_character.unstable_jump);
        results.push({case:'native run without metres, failed jump',x:42.75,ap:1});save();

        await setup();
        const hiddenAnomaly=await request(url+'/objects',{type:'anomaly',name:'Проверка края в аномалии',tile_x:43,tile_y:48,
            properties:{anomaly_key:'morozilnik',gm_hidden:true,level:0,dimensions:{width:2,depth:1,height:1}}},token);
        await roll(9);
        const edge=await act({action_key:'jump',target_x:42.7,target_y:48});
        assert.equal(edge.jump.outcome,'unstable_edge');
        for(const client of [page,peer])await client.until(`window.locationCombatState?.current_character?.unstable_jump&&window.locationCombatState.current_character.active_anomaly?.key==='morozilnik'&&jumpObjectUpdates.some(e=>e.object?.id===${hiddenAnomaly.id}&&!e.object.properties.gm_hidden)`,'край в аномалии и её раскрытие у двух клиентов');
        assert.equal(await peer.evaluate(`L.startCharacterMoveMode(${actor.id})`),false);
        await peer.evaluate('window.__jumpReload=true');await peer.call('Page.reload');
        await peer.until('!window.__jumpReload&&typeof enterLocation==="function"&&window.socket?.connected','перезагрузка на краю');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${location.id})})()`);
        await peer.until(`window.locationCombatState?.current_character?.unstable_jump`,'состояние края сохранено после reload');
        await settle(42.7,48);
        await peer.evaluate(`locationControls.object.position.set(43,12,58);locationControls.target.set(43,1,48.5);locationControls.update()`);
        await roll(11);
        const edgeCentre=await peer.evaluate(`(()=>{locationControls.object.updateMatrixWorld(true);const v=new T.Vector3(...L.characterModelCenterForTest(${actor.id})).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.x+(v.x+1)*r.width/2,y:r.y+(1-v.y)*r.height/2}})()`);
        await click(peer,edgeCentre,'right');
        assert.ok(await peer.evaluate(`!document.querySelector('[aria-label="Вырваться"]')`),'выход из аномалии не перекрывает проверку равновесия');
        await click(peer,await button(peer,'[aria-label="Равновесие"]'));
        await settle(42.2,48);
        const stumbled=await request(url+'/combat',null,token,'GET');
        assert.equal(stumbled.current_character.action_points_current,3);
        assert.ok(!stumbled.current_character.unstable_jump);
        assert.ok(!stumbled.current_character.active_anomaly,'оступание вывело из зоны без сохранения старой аномалии');
        results.push({case:'edge reload and free stumble',x:42.2,ap:3});save();

        await setup({x:27.2,y:14,level:1,strength:14});
        await peer.evaluate(`void socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:27.2,y:14,floor_level:1,movement_mode:'run'})`);
        await peer.until(`window.locationCombatState?.current_character?.movement_mode_this_turn==='run'`,'бег перед межэтажным проёмом');
        const preview=await request(url+'/movement-preview',{character_id:actor.id,special_action:'jump',x:31.8,y:14},token);
        assert.equal(preview.route.floor_level,1);
        await roll(5);
        const gap=await act({action_key:'jump',target_x:31.8,target_y:14});
        assert.equal(gap.jump.outcome,'midpoint');assert.equal(gap.jump.landing.x,29.5);
        assert.equal(gap.jump.landing.level,0);assert.ok(gap.jump.fall);
        await settle(29.5,14,0);
        for(const client of [page,peer])await client.evaluate('L.setLocationViewLevel(0)');
        fs.writeFileSync(path.join(safeSession.artifacts,'jump-pripyat-gap.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        results.push({case:'actual pripyat-1 opening',preview:preview.route,jump:gap.jump});save();
        const evidence=await request(`/__testing_jump_rolls/${safeSession.runId}`,null,token,'GET');
        assert.deepEqual(evidence.pending,[]);assert.deepEqual(evidence.draws,[10,5,9,11,5]);
        console.log('PASS: Припять, настоящие кнопки/предпросмотр, прыжок с места, выбор бега без метров, точная середина, край/reload/бесплатное оступание, падение в исходный проём, два клиента');
    }finally{
        peer?.close();if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});protocol.close();
    }
};
