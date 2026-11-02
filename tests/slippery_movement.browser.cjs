// Настоящие движения на копии Припяти, управляемый серверный d20, два клиента.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession,location,actor,player})=>{
    const url=`/lobbies/${lobby.id}/locations/${location.id}`,results=[];
    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    let peer,context,actorId,completed=false;
    const state=()=>request(url+'/combat',null,token,'GET');
    const dice=async values=>request(`/__testing_jump_rolls/${safeSession.runId}`,{rolls:values},token);
    const click=async(client,p)=>{
        await client.call('Page.bringToFront');
        await client.call('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',buttons:1,clickCount:1});
        await client.call('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',buttons:0,clickCount:1});
    };
    const enter=async client=>{
        await client.until('typeof enterLocation==="function"&&window.socket?.connected','клиент комнаты');
        await client.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${location.id})})()`);
        await client.until('window.locationControls&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','Припять загружена');
        await client.evaluate(`L.setLocationEditMode(false);locationControls.target.set(41.5,1,50.5);locationControls.object.position.set(47,12,59);locationControls.update();document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click()`);
        await client.evaluate(`window.slipEvents=[];void socket.on('character_moved',e=>{if(e.character_id===${actor.id})slipEvents.push(e)})`);
    };
    const settled=async(y,x=41)=>{
        for(const client of [page,peer])await client.until(`(()=>{try{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id}),c=L.getCombatState()?.current_character;return Math.abs(p.x-${x})<.015&&Math.abs(p.y-${y})<.015&&!s.animating&&c?.character_id===${actor.id}&&Math.abs(c.x-${x})<.015&&Math.abs(c.y-${y})<.015}catch{return false}})()`,'положение и состояние у двух клиентов');
    };
    const setup=async(y=48,budget=10,x=41)=>{
        if((await state()).status==='active')await request(url+'/combat/end',{},token);
        const fresh=await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
        await request(`/lobbies/characters/${actor.id}`,{data:{...fresh.data,weapons:[],equipment:{},
            skills:{physical:{agility:{base:10,bonus:0},will:{base:10,bonus:0}}},
            health:{current:700,max:700,painLevel:0,psyState:0,effects:[]}}},token,'PUT');
        await request(url+'/spawn_character',{character_id:actor.id,tile_x:x,tile_y:y,floor_level:0,force_relocate:true},token);
        actorId=(await state()).characters.find(c=>c.character_id===actor.id).location_character_id;
        await request(url+'/combat/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        await request(url+'/combat/action',{location_character_id:actorId,action_key:'convert_free_action_to_movement'},player.access_token);
        const current=(await state()).current_character;
        if(current.movement_points_current!==budget)await request(url+'/combat/adjust',{location_character_id:actorId,movement_points:budget-current.movement_points_current},token);
        for(const client of [page,peer]){
            await client.call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
            await client.evaluate(`L.setLocationEditMode(false);locationControls.target.set(${x+.5},1,${y+2.5});locationControls.object.position.set(${x+6},12,${y+11});locationControls.update();slipEvents=[]`);
            await client.until(`L.getCombatState()?.current_character?.movement_points_current===${budget}`,'бюджет движения');
        }
        await settled(y,x);
    };
    const nativeMove=async(y,mode='run',expectRisk=true,x=41,previewLabel=null,beforeClick=null,expectedBlockedReason=null)=>{
        const label={run:'Бег',sprint:'Спринт',walk:'Ходьба'}[mode];
        await peer.call('Page.bringToFront');
        await peer.evaluate(`L.showMovementTypeMenu(${actor.id})`);
        const p=await peer.evaluate(`(()=>{const b=[...document.querySelectorAll('#combat-movement-type-menu .movement-type-options button')].find(b=>b.querySelector('strong')?.textContent.trim()===${JSON.stringify(label)});if(!b||b.disabled)throw Error('Режим недоступен: '+b?.textContent);const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await click(peer,p);
        await peer.until(`L.movementReadinessSnapshot().mode==='${mode}'`,'режим выбран');
        const target=await peer.evaluate(`(()=>{locationControls.object.updateMatrixWorld(true);const p=new T.Vector3(${x+.5},1,${y+.5}).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()`);
        await peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...target,buttons:0});
        await peer.until(expectedBlockedReason
            ? `L.movementHintText().includes(${JSON.stringify(expectedBlockedReason)})`
            : `L.movementHintText().startsWith(${JSON.stringify((previewLabel||label)+':')})&&L.movementHintText().includes(${JSON.stringify(expectRisk?'Лёд: Ловкость СЛ12':'ОП')})`,'авторитетный предпросмотр');
        if(beforeClick)await beforeClick();
        await click(peer,target);
    };
    try{
        const patch=[];for(let y=49;y<=53;y++)for(let x=39;x<=43;x++)patch.push([x,y]);
        await page.evaluate(`L.setLocationEditMode(true);L.applyLocationBrush(${JSON.stringify(patch)},null,{terrain:'ice',height:1},0)`);
        await page.until(`(async()=>{const r=await fetch('${url}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}});return (await r.json()).tiles_data[49][41].terrain==='ice'})()`,'покрытие сохранено');
        await page.until(`L.getLocationTerrainMaterialDiagnostics()?.ready`,'материалы готовы');
        context=(await protocol.call('Target.createBrowserContext')).browserContextId;
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer=await connect(tabs.find(t=>t.id===tab.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
        await peer.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
        await enter(peer);
        await page.evaluate(`window.slipEvents=[];void socket.on('character_moved',e=>{if(e.character_id===${actor.id})slipEvents.push(e)})`);
        await setup(48,3.75);await dice([11]);await nativeMove(52);await settled(50.25);
        for(const client of [page,peer]){
            assert.equal(await client.evaluate('L.getCombatState().current_character.movement_mode_this_turn'),'walk');
            assert.equal(await client.evaluate('L.getCombatState().current_character.movement_points_current'),0);
            assert.equal(await client.evaluate('slipEvents.at(-1).slip_checks[0].roll'),11);
            assert.equal(await client.evaluate('slipEvents.at(-1).slip_stopped'),true);
        }
        results.push({case:'native_failure_partial',y:50.25,cost:3.75});
        fs.writeFileSync(path.join(safeSession.artifacts,'slip-partial.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await setup();await dice([20]);await nativeMove(51);await settled(51);
        const count=(await request(`/__testing_jump_rolls/${safeSession.runId}`,null,token,'GET')).draws.length;
        await peer.evaluate('window.slipBeforeReload=true');await peer.call('Page.reload');
        // Во время reload Chrome может уничтожить старый контекст между двумя опросами.
        for(let attempt=0;;attempt++){
            try{
                await peer.until('!window.slipBeforeReload&&typeof enterLocation==="function"&&window.socket?.connected','новая страница после reload');
                break;
            }catch(error){
                if(attempt>=3||!/Inspected target navigated or closed|Execution context was destroyed|Cannot find context/.test(error.message))throw error;
            }
        }
        await enter(peer);
        await nativeMove(52);await settled(52);
        assert.equal((await request(`/__testing_jump_rolls/${safeSession.runId}`,null,token,'GET')).draws.length,count,'после reload на том же льду нового броска нет');
        results.push({case:'success_and_reload',checks:1});
        await setup(50,2.25);await dice([11]);await nativeMove(51);await settled(51);
        assert.equal(await peer.evaluate('L.getCombatState().current_character.movement_points_current'),.25);
        assert.equal(await peer.evaluate('L.getCombatState().current_character.movement_mode_this_turn'),'walk','следующий шаг использует ходьбу');
        await nativeMove(51.1,'walk',false);await settled(51.1);
        assert.ok(Math.abs((await state()).current_character.movement_points_current-.05)<.01);
        results.push({case:'start_on_ice_then_walk',remaining:.05});
        await setup(48,2.5);await dice([11]);await nativeMove(52,'sprint');await settled(49+2/3);
        for(const client of [page,peer]){
            assert.equal(await client.evaluate('L.getCombatState().current_character.action_points_current'),1);
            assert.equal(await client.evaluate('L.getCombatState().current_character.movement_mode_this_turn'),'walk');
            assert.equal(await client.evaluate('L.getCombatState().current_character.movement_points_current'),0);
        }
        results.push({case:'native_sprint_failure_partial',y:49+2/3,actionPoints:1});
        for(const [x,y,cost,mode,label,sx=41,sy=50] of [[42,51,2*Math.SQRT2,'walk','Ходьба'],[40,51,2*Math.SQRT2,'walk','Ходьба'],[42.1,51,4*Math.hypot(1.1,1),'backward_sideways','Спиной / боком'],[42,50,4,'backward_sideways','Спиной / боком'],
            [43.2,52.1,4*Math.hypot(2.1,2),'backward_sideways','Спиной / боком',41.1,50.1],
            [39,52.1,4*Math.hypot(2.1,2),'backward_sideways','Спиной / боком',41.1,50.1],
            [39.1,52.1,4*Math.SQRT2,'walk','Ходьба',41.1,50.1]]){
            await setup(sy,20,sx);await nativeMove(y,'walk',false,x,label,async()=>{
                if(sx!==41)fs.writeFileSync(path.join(safeSession.artifacts,`direction-angle-${x}.png`),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
            });await settled(y,x);
            for(const client of [page,peer]){
                const current=await client.evaluate('L.getCombatState().current_character');
                assert.equal(current.movement_mode_this_turn,mode);
                assert.ok(Math.abs(current.movement_points_current-(20-cost))<.01,'диагональ без удорожания спиной/боком; боковой шаг дороже');
                assert.deepEqual([current.facing_x,current.facing_y],[0,1],'движение сохраняет направление взгляда');
            }
            results.push({case:'native_direction',target:[x,y],mode,cost});
        }
        await setup(50.1,20,41.1);
        await nativeMove(52.1,'run',false,43.2,null,null,'Для бега в этом направлении сначала измените направление взгляда');
        await settled(50.1,41.1);
        assert.equal((await state()).current_character.movement_points_current,20);
        assert.equal((await state()).current_character.action_points_current,5);
        results.push({case:'incompatible_run_explains_facing',resourcesUnchanged:true});
        // Повтор игрового дефекта: выход на дешёвый грунт не должен требовать бокового шага.
        const stripe=[];for(let y=47;y<=57;y++)for(const x of [44,45,46])stripe.push([x,y]);
        await page.evaluate(`L.setLocationEditMode(true);L.applyLocationBrush(${JSON.stringify(patch)},null,{terrain:'grass',height:1},0)`);
        await page.until(`(async()=>{const r=await fetch('${url}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}});return (await r.json()).tiles_data[49][41].terrain==='grass'})()`,'предыдущая кисть сохранена');
        await page.evaluate(`L.applyLocationBrush(${JSON.stringify(stripe)},null,{terrain:'ice',height:1},0)`);
        await page.until(`(async()=>{const r=await fetch('${url}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}}),d=await r.json();return d.tiles_data[49][41].terrain==='grass'&&d.tiles_data[48][45].terrain==='ice'})()`,'полоса льда сохранена');
        await setup(48.1,20,45.1);
        const preview=async mode=>(await request(url+'/movement-preview',{character_id:actor.id,x:45.1,y:55.1,floor_level:0,movement_mode:mode},token)).route;
        const old=await preview('correction'),route=await preview('walk');
        const directions=path=>path.slice(1).map((p,i)=>{const dx=p[0]-path[i][0],dy=p[1]-path[i][1],length=Math.hypot(dx,dy);return !length||dy/length>=Math.SQRT1_2-1e-9});
        assert.ok(directions(old.path).some(v=>!v),'старый поиск выбирал боковой участок ради цены грунта');
        assert.ok(directions(route.path).every(Boolean),'новый поиск сразу ограничивает направление');
        await nativeMove(55.1,'walk',false,45.1,null,async()=>{
            assert.ok(!(await peer.evaluate('L.movementHintText()')).includes('смешивает'));
            fs.writeFileSync(path.join(safeSession.artifacts,'direction-planned.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        });
        await settled(55.1,45.1);
        for(const client of [page,peer]){
            const c=await client.evaluate('L.getCombatState().current_character');
            assert.equal(c.movement_mode_this_turn,'walk');
            assert.ok(Math.abs(c.movement_points_current-(20-route.cost))<.01);
            assert.deepEqual([c.facing_x,c.facing_y],[0,1]);
        }
        results.push({case:'native_direction_aware_planner',oldPath:old.path,path:route.path,distance:route.distance,cost:route.cost});
        await nativeMove(53.1,'walk',false,45.1,null,null,'В этом ходу уже выбрана ходьба вперёд');
        await settled(55.1,45.1);
        assert.ok(Math.abs((await state()).current_character.movement_points_current-(20-route.cost))<.01);
        assert.ok(!(await peer.evaluate('L.movementHintText()')).includes('Путь заблокирован'));
        results.push({case:'committed_walk_explains_facing',resourcesUnchanged:true});
        fs.writeFileSync(path.join(safeSession.artifacts,'slippery.json'),JSON.stringify({scene:'копия Припяти',twoClients:true,results},null,2));
        completed=true;
        console.log('Скольжение: нативное движение, провал/успех, частичный маршрут, остаток ОП, два клиента и reload — проверено.');
    }finally{
        if(!completed){
            const debug=[];
            for(const client of [page,peer].filter(Boolean))debug.push(await client.evaluate(`(()=>{try{return {pose:L.characterPose(${actor.id}),spatial:L.characterSpatialPose(${actor.id}),ready:L.movementReadinessSnapshot(),hint:L.movementHintText(),events:slipEvents.slice(-2),current:L.getCombatState()?.current_character&&{y:L.getCombatState().current_character.y,mode:L.getCombatState().current_character.movement_mode_this_turn,op:L.getCombatState().current_character.movement_points_current}}}catch(e){return {error:e.message}}})()`).catch(e=>({error:e.message})));
            fs.writeFileSync(path.join(safeSession.artifacts,'slip-debug.json'),JSON.stringify(debug,null,2));
        }
        peer?.close();if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context}).catch(()=>{});protocol.close();
    }
};
