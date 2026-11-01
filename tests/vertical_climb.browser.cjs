const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');

module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession,location,actor,player})=>{
    const url=`/lobbies/${lobby.id}/locations/${location.id}`,sheetUrl=`/lobbies/characters/${actor.id}`;
    const version=await (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    let peer,context;
    const results=[];
    const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'vertical-climb.json'),JSON.stringify({scene:'копия Припяти',twoClients:true,results},null,2));
    const click=async(client,selector)=>{
        await client.call('Page.bringToFront');
        const point=await client.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled)throw Error('Кнопка недоступна');const r=e.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()`);
        await client.call('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
        await client.call('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
    };
    try {
        context=await protocol.call('Target.createBrowserContext');
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer=await connect(tabs.find(t=>t.id===tab.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
        await peer.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
        await peer.until('typeof enterLocation==="function"&&window.socket?.connected','клиент игрока готов');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${location.id})})()`);
        for(const client of [page,peer]) {
            await client.until(`(()=>{try{return L.characterPose(${actor.id})&&window.locationControls}catch{return false}})()`,'Припять загружена');
            await client.evaluate(`window.climbMoves=[];window.climbRejected=[];void socket.on('character_moved',e=>climbMoves.push(e));void socket.on('movement_rejected',e=>climbRejected.push(e))`);
        }
        const barrier=await request(url+'/objects',{type:'box',name:'Проверка перелезания',tile_x:42,tile_y:48,
            properties:{traversal_category:'auto',dimensions:{width:.8,depth:.8,height:.8}}},token);
        let actorLocationId;
        const setup=async({height=.8,bonus=2,mp=true,x=41,y=48}={})=>{
            const state=await request(url+'/combat',null,token,'GET');
            if(state.status==='active')await request(url+'/combat/end',{},token);
            await request(`/lobbies/${lobby.id}/locations/objects/${barrier.id}`,{properties:{dimensions:{width:.8,depth:.8,height},traversal_category:'auto'}},token,'PATCH');
            const fresh=await request(sheetUrl,null,token,'GET');
            await request(sheetUrl,{data:{...fresh.data,weapons:[],skills:{physical:{strength:{base:10,bonus:0},agility:{base:10+bonus*2,bonus:0}}},
                health:{current:700,max:700,painLevel:0,effects:[],psyState:0}}},token,'PUT');
            await request(url+'/spawn_character',{character_id:actor.id,tile_x:x,tile_y:y,assign_to_user_id:player.user_id},token);
            for(const client of [page,peer])await client.until(`L.characterPose(${actor.id}).x===${x}&&L.characterPose(${actor.id}).y===${y}`,'исходная позиция');
            const idle=await request(url+'/combat',null,token,'GET');
            actorLocationId=idle.characters.find(c=>c.character_id===actor.id).location_character_id;
            await request(url+'/combat/start',{location_character_ids:[actorLocationId],initiator_location_character_id:actorLocationId,initiator_first:true},token);
            if(mp)await request(url+'/combat/action',{location_character_id:actorLocationId,action_key:'convert_free_action_to_movement'},player.access_token);
            return (await request(url+'/combat',null,token,'GET')).current_character;
        };
        const waitPose=async(x,y)=>{
            for(const client of [page,peer])await client.until(`(()=>{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id});return Math.abs(p.x-${x})<.0001&&Math.abs(p.y-${y})<.0001&&!s.animating&&Math.abs(s.height-s.renderHeight)<.00001})()`,'конечная позиция и высота у двух клиентов');
        };
        for(const [height,bonus,payment,expectedOp,expectedAp] of [[.8,2,'movement',3,0],[.8,2,'action',0,1],[1.2,2,'movement',10,0],[1.2,2,'action',0,3],[.8,-2,'movement',7,0],[.8,10,'movement',0,0]]) {
            const before=await setup({height,bonus,mp:expectedOp>0||payment==='action'});
            const preview=(await request(url+'/movement-preview',{character_id:actor.id,special_action:'climb',object_id:barrier.id},player.access_token)).route;
            assert.ok(preview);
            await peer.evaluate(`locationControls.target.set(42.5,1.5,48.5);locationControls.object.position.set(47,11,56);locationControls.update();void L.beginObjectClimbForTest(${barrier.id},${actor.id})`);
            await peer.until('document.querySelector("#traversal-payment-modal [data-traversal-payment]")','выбор оплаты');
            if(height===.8&&bonus===2&&payment==='movement')fs.writeFileSync(path.join(safeSession.artifacts,'climb-payment.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
            await click(peer,`[data-traversal-payment="${payment}"]`);
            await waitPose(...preview.path.at(-1));
            const after=(await request(url+'/combat',null,token,'GET')).current_character;
            assert.ok(Math.abs(before.movement_points_current-after.movement_points_current-expectedOp)<1e-6);
            assert.equal(before.action_points_current-after.action_points_current,expectedAp);
            assert.equal(before.free_actions_current,after.free_actions_current);
            assert.ok(Math.abs(after.movement_distance_this_turn-preview.distance)<1e-6);
            for(const client of [page,peer])await client.until(`climbMoves.some(e=>e.character_id===${actor.id}&&e.path.some(p=>p.height>${1+height-.01}))`,'траектория проходит над реальной высотой препятствия');
            results.push({kind:'object',height,bonus,payment,before,after,preview});save();
        }
        await setup({height:2.3,mp:false});
        assert.equal((await request(url+'/movement-preview',{character_id:actor.id,special_action:'climb',object_id:barrier.id},token)).route,null);
        await page.call('Page.bringToFront');
        await page.evaluate(`L.setLocationEditMode(true);window.selectLocationEditorTool('select');locationControls.target.set(42.5,2,48.5);locationControls.object.position.set(47,11,56);locationControls.update();locationControls.object.updateMatrixWorld(true)`);
        await page.until(`L.objectRenderBoundsForTest(${barrier.id})?.size[1]>2.1`,'новая высота объекта отрисована');
        const projected=await page.evaluate(`(()=>{const center=L.objectRenderBoundsForTest(${barrier.id}).center;const p=new T.Vector3(...center).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...projected,buttons:0});
        await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...projected,button:'left',clickCount:1});
        await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',...projected,button:'left',clickCount:1});
        const picked=await page.evaluate(`(()=>{if(!document.querySelector('[data-traversal-category]'))L.selectEditorAsset(${projected.x},${projected.y});return L.getSelectedLocationEditorAsset()})()`);
        assert.equal(picked?.selectionKey,`structure:${barrier.id}`,JSON.stringify({picked,projected}));
        await page.until('document.querySelector("[data-traversal-category]")','переопределение категории ГМа');
        await page.evaluate(`(()=>{const e=document.querySelector('[data-traversal-category]');e.value='low';e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
        await page.until(`L.getSelectedLocationEditorAsset()?.traversalCategory==='low'`,'категория сохранена');
        const changed=(await request(url,null,token,'GET')).objects.find(o=>o.id===barrier.id);
        assert.equal(changed.properties.dimensions.height,2.3);
        assert.equal(changed.properties.traversal_category,'low');
        assert.equal((await request(url+'/movement-preview',{character_id:actor.id,special_action:'climb',object_id:barrier.id},token)).route.climb_cost,5);
        results.push({kind:'gm-override',height:2.3,category:'low',geometryUnchanged:true});save();
        await page.evaluate('L.setLocationEditMode(false)');
        const tiles=(await request(url,null,token,'GET')).tiles_data;tiles[48][45].height=2.5;
        await request(url,{tiles_data:tiles},token,'PUT');
        await setup({x:44,y:48});
        const moveByPointer=async(x,y,expectedAction,capture=false)=>{
            await peer.call('Page.bringToFront');
            const route=(await request(url+'/movement-preview',{character_id:actor.id,x,y,floor_level:0},player.access_token)).route;
            assert.equal(route.special_action,expectedAction);
            await peer.evaluate(`L.beginCharacterMoveMode(${actor.id},'walk');locationControls.target.set(${x+.5},${route.surfaces.at(-1).height},${y+.5});locationControls.object.position.set(${x-5},10,${y+8});locationControls.update();locationControls.object.updateMatrixWorld(true)`);
            const pointer=await peer.evaluate(`(()=>{const p=new T.Vector3(${x+.5},${route.surfaces.at(-1).height},${y+.5}).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
            await peer.evaluate(`L.updateMovementPreview(${pointer.x},${pointer.y})`);
            await peer.until(`L.navigationPreviewSettled(${route.path[0][0]},${route.path[0][1]},${x},${y},${actor.id},0)`,'предпросмотр явного действия');
            await peer.evaluate(`L.commitMovementPreview(${pointer.x},${pointer.y})`);
            await peer.until('document.querySelector("#traversal-payment-modal [data-traversal-payment=action]")','подтверждение подъёма/спуска');
            if(capture)await peer.evaluate(`(()=>{const started=performance.now();window.climbFrameCapture=null;const sample=()=>{const s=L.characterSpatialPose(${actor.id});
                if(s.animating&&s.renderHeight>1.7&&s.x>42.2&&s.x<42.8){window.climbFrameCapture={pose:s,image:L.captureCurrentSceneForTest()};return;}
                if(performance.now()-started>8000){window.climbFrameCapture={error:'Не получен кадр пересечения',pose:s,hidden:document.hidden,path:climbMoves.at(-1)?.path};return;}requestAnimationFrame(sample);};requestAnimationFrame(sample)})()`);
            await click(peer,'[data-traversal-payment=action]');
            if(capture) {
                await peer.until('window.climbFrameCapture','кадр модели над препятствием');
                const frame=await peer.evaluate('window.climbFrameCapture');
                assert.ok(frame.image,JSON.stringify({error:frame.error,pose:frame.pose,hidden:frame.hidden,path:frame.path}));
                fs.writeFileSync(path.join(safeSession.artifacts,'normal-climb-mid.png'),Buffer.from(frame.image.split(',')[1],'base64'));
                results.push({kind:'rendered-normal-climb',pose:frame.pose});save();
            }
            await waitPose(x,y);
            return route;
        };
        const up=await moveByPointer(45,48,'climb_up');
        let after=(await request(url+'/combat',null,token,'GET')).current_character;
        assert.equal(after.action_points_current,2);assert.equal(after.movement_points_current,10);
        const down=await moveByPointer(44,48,'descend');
        after=(await request(url+'/combat',null,token,'GET')).current_character;
        assert.equal(after.action_points_current,1);assert.equal(after.movement_points_current,10);
        assert.equal(after.movement_distance_this_turn,2);
        for(const client of [page,peer])await client.until(`climbMoves.some(e=>e.character_id===${actor.id}&&e.x===44&&e.y===48&&e.fall===null)`,'управляемый спуск без падения');
        results.push({kind:'terrain',up,down,after});save();
        tiles[48][45].height=3;
        await request(url,{tiles_data:tiles},token,'PUT');
        assert.equal((await request(url+'/movement-preview',{character_id:actor.id,x:45,y:48,floor_level:0},token)).route,null);
        await peer.evaluate(`void socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:45,y:48,special_action:'climb_up',climb_payment:'action'})`);
        await peer.until(`climbRejected.some(e=>e.character_id===${actor.id})`,'подъём от 2 м отклонён');
        const rejected=(await request(url+'/combat',null,token,'GET')).current_character;
        assert.equal(rejected.action_points_current,1);assert.equal(rejected.x,44);
        results.push({kind:'two-meter-rejection',unchanged:true});save();
        await setup();
        await request(`/lobbies/${lobby.id}/locations/objects/${barrier.id}`,{properties:{dimensions:{width:.2,depth:100,height:.8}}},token,'PATCH');
        await request(url+'/combat/action',{location_character_id:actorLocationId,action_key:'change_facing',facing_x:1,facing_y:0,payment:'action'},player.access_token);
        const beforeRoute=(await request(url+'/combat',null,token,'GET')).current_character;
        const ordinary=await moveByPointer(43,48,undefined,true);
        assert.equal(ordinary.climbs.length,1);
        const afterRoute=(await request(url+'/combat',null,token,'GET')).current_character;
        assert.ok(Math.abs(beforeRoute.movement_points_current-afterRoute.movement_points_current-2)<.00001);
        assert.equal(beforeRoute.action_points_current-afterRoute.action_points_current,1);
        results.push({kind:'ordinary-route-climb',ordinary,before:beforeRoute,after:afterRoute});save();
        await request(url+'/combat/end_turn',{location_character_id:actorLocationId},player.access_token);
        await request(url+'/combat/action',{location_character_id:actorLocationId,action_key:'change_facing',facing_x:0,facing_y:1,payment:'action'},player.access_token);
        await request(`/lobbies/${lobby.id}/locations/objects/${barrier.id}`,{properties:{dimensions:{width:.8,depth:.8,height:.8}}},token,'PATCH');
        tiles[48][45].height=3.5;
        await request(url,{tiles_data:tiles},token,'PUT');
        await setup({x:45,y:48,mp:false});
        await moveByPointer(44,48,'descend');
        const fall=await peer.evaluate(`climbMoves.filter(e=>e.character_id===${actor.id}&&e.fall).at(-1)?.fall`);
        assert.equal(fall.height,2.5);assert.equal(fall.classified_height,2);assert.equal(fall.difficulty,8);
        results.push({kind:'uncontrolled-fall',fall});save();
        await request(url+'/combat/end',{},token);
        const fresh=await request(sheetUrl,null,token,'GET');
        await request(sheetUrl,{data:{...fresh.data,skills:{physical:{strength:{base:10,bonus:0}}},health:{current:700,max:700,painLevel:0,effects:[],psyState:0}}},token,'PUT');
        console.log('PASS: Припять, оба способа оплаты/бонусы, выбор кнопками, категория ГМа, подъём/спуск указателем и падение у двух клиентов');
    } finally {
        peer?.close();if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});protocol.close();
    }
};
