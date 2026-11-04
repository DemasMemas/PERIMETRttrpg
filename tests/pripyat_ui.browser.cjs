// Read-only export of the saved Pripyat is mounted in an isolated test database.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    const fixturePath=safeSession?path.resolve(__dirname,'../tmp/pripyat-regression.json'):process.env.VTT_PRIPYAT_FIXTURE||'tmp/pripyat-regression.json';
    const artifactDir=safeSession?safeSession.artifacts:'tmp/pripyat-ui';
    const fixture=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'surface') {
        // Меняется только копия в памяти; исходный экспорт остаётся неизменным.
        for (const row of fixture.tiles_data) for (const [x,terrain] of [[42,'snow'],[43,'sand'],[44,'swamp']]) row[x].terrain=terrain;
        fixture.objects.push({type:'tree',name:'№102 проверочный куст',tile_x:45,tile_y:48,properties:{
            visual_asset:'nature_bush_green',passable:true,dimensions:{width:.8,depth:.8,height:1.3}}});
        fs.writeFileSync(path.join(safeSession.artifacts,'surface-fixture.json'),JSON.stringify({modifiedColumns:{42:'snow',43:'sand',44:'swamp'},bush:[45,48],originalUnchanged:true},null,2));
    }
    const baseline=process.env.VTT_PRIPYAT_BASELINE==='1',api=`/lobbies/${lobby.id}`;
    const loc=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:8,world_tile_z:8},token);
    const url=api+'/locations/'+loc.id;
    await request(url+'/objects/batch',{objects:fixture.objects},token);
    await request('/auth/register',{username:'pripyat-player',email:'pripyat@example.test',password:'pripyat-password'});
    const player=await request('/auth/login',{username:'pripyat-player',password:'pripyat-password'});
    await request(api+'/join',{},player.access_token);
    const actor=await request(api+'/characters',{name:'Pripyat regression actor',data:{}},player.access_token);
    await request(url+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48,assign_to_user_id:player.user_id},token);
    if (safeSession && ['movement','surface'].includes(process.env.VTT_SAFE_SCENARIO)) {
        await request(url+'/characters/'+actor.id+'/facing',{facing_x:1,facing_y:0},token,'PATCH');
    }
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${loc.id})})()`);
    await page.until(`window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Pripyat loaded');
    await page.evaluate(`L.setLocationEditMode(false);window.contextEvents=[];window.addEventListener('contextmenu',e=>contextEvents.push({prevented:e.defaultPrevented,target:e.target.tagName}),false)`);
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'grenades') {
        await require('./grenades.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession,location:loc,actor,player});
        await require('./grenade_scene.browser.cjs')({page,request,token,lobby,base,safeSession});
        return;
    }
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'slippery') {
        await require('./slippery_movement.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession,location:loc,actor,player});
        return;
    }
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'ice') {
        await require('./ice_terrain.browser.cjs')({page,safeSession,request,token,lobby,location:loc,actor});
        return;
    }
    if(safeSession&&process.env.VTT_SAFE_SCENARIO==='anomaly-boar'){
        await require('./anomaly_boar.browser.cjs')({page,request,token,lobby,location:loc,actor,base,safeSession});return;
    }
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'graphics') {
        await require('./location_graphics.browser.cjs')({page,safeSession,request,token,lobby,location:loc,actor});
        return;
    }
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'jump') {
        await require('./character_jumps.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession,location:loc,actor,player});
        return;
    }
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'climb') {
        await require('./vertical_climb.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession,location:loc,actor,player});
        await require('./movement_walls.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession,fixture,location:loc,actor,player});
        return;
    }
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'psi') {
        await require('./psi_turn_phases.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession,location:loc,actor,player});
        return;
    }
    if (safeSession && process.env.VTT_SAFE_SCENARIO === 'walls') {
        await require('./movement_walls.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession,fixture,location:loc,actor,player});
        return;
    }
    if(safeSession&&['movement','surface'].includes(process.env.VTT_SAFE_SCENARIO)){
        await require('./movement_regression.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession,fixture,location:loc,actor,player});
        return;
    }
    const results={routes:[]};
    for(const [name,x,y] of [['short',46,48],['long',60,48],['obstacles',22,36]]){
        const result=await page.evaluate(`(async()=>{const t=performance.now(),r=await fetch('${url}/movement-preview',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('access_token')},body:JSON.stringify({character_id:${actor.id},x:${x},y:${y},floor_level:0})}),data=await r.json();return {name:'${name}',ms:performance.now()-t,route:data.route}})()`);
        assert.ok(result.route,'reachable '+name);results.routes.push(result);console.log('PRIPYAT_ROUTE '+JSON.stringify(result));
    }
    const aim=(x,y,z)=>page.evaluate(`(()=>{locationControls.object.updateMatrixWorld(true);const p=new T.Vector3(${x},${y},${z}).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
    results.movementReady=[];
    for(const [name,x,y] of [['short',46,48],['long',60,48],['obstacles',22,36]]) {
        const point=await aim(x+.5,1,y+.5);
        const ready=await page.evaluate(`(async()=>{const t=performance.now();L.beginCharacterMoveMode(${actor.id});L.updateMovementPreview(${point.x},${point.y});await new Promise(done=>{const check=()=>L.navigationPreviewSettled(41,48,${x},${y},${actor.id},0)?done():requestAnimationFrame(check);requestAnimationFrame(check)});return {name:'${name}',ms:performance.now()-t,hint:L.movementHintText()}})()`);
        results.movementReady.push(ready);console.log('PRIPYAT_MOVEMENT_READY '+JSON.stringify(ready));
    }
    if(!baseline) {
        const refreshed=await request(url+'/combat',null,token,'GET');
        await page.evaluate(`L.setCombatState(${JSON.stringify(refreshed)})`);
        await page.until(`L.navigationPreviewSettled(41,48,22,36,${actor.id},0)`,'stationary pointer route refresh after state update');
    }
    const actorPoint=await aim(41.5,2.1,48.5),target=await aim(46.5,1,48.5);
    const right=async p=>{for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...p,button:'right',buttons:type==='mousePressed'?2:0,clickCount:1});};
    await right(actorPoint);
    await page.until(`document.getElementById('combat-action-menu')?.style.display==='block'`,'native actor menu');
    const button=await page.evaluate(`(()=>{const r=document.querySelector('#combat-action-menu button').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await right(button);results.context=await page.evaluate('contextEvents');
    if(!baseline) {
        await page.evaluate(`(()=>{const e=document.createElement('textarea');e.id='test-outside-game';e.style.cssText='position:fixed;left:8px;top:300px;width:100px;height:40px;z-index:99999';document.body.appendChild(e)})()`);
        await right({x:30,y:320});
        assert.equal(await page.evaluate('contextEvents.at(-1).prevented'),false,'native menu stays available outside game roots');
        await page.call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
        await page.evaluate(`document.getElementById('test-outside-game').remove()`);
    }
    await page.evaluate(`document.body.click();L.showFacingMenu(${actor.id})`);
    results.preview=await page.evaluate(`(async()=>{const t=performance.now();L.updateFacingPreview(${target.x},${target.y});${baseline?'':`await new Promise(resolve=>{const check=()=>L.previewComplete()?resolve():requestAnimationFrame(check);requestAnimationFrame(check)});`}return {ms:performance.now()-t,...L.previewDiagnostics()}})()`);
    if(!baseline){
        assert.ok(results.preview.count>0,'future sight cells restored');
        assert.deepEqual(await page.evaluate('L.previewTiles()'),await page.evaluate(`L.expectedPreview(${actor.id},46,48)`),'preview matches actual visibility rules');
        results.repeated=[];
        for(const [x,z] of [[46.5,50.5],[45.5,52.5],[46.5,48.5]]){
            const p=await aim(x,1,z);
            results.repeated.push(await page.evaluate(`(async()=>{const t=performance.now();L.updateFacingPreview(${p.x},${p.y});await new Promise(r=>{const check=()=>L.previewComplete()?r():requestAnimationFrame(check);requestAnimationFrame(check)});return performance.now()-t})()`));
        }
        fs.mkdirSync(artifactDir,{recursive:true});
        fs.writeFileSync(path.join(artifactDir,'facing.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        results.continuous=await page.evaluate(`(async()=>{previewRenderSamples=[];const canvas=document.querySelector('#location-canvas canvas'),r=canvas.getBoundingClientRect(),t=performance.now();for(let i=0;i<24;i++){const a=i*Math.PI/12,p=new T.Vector3(41.5+5*Math.cos(a),1,48.5+5*Math.sin(a)).project(locationControls.object);for(let j=0;j<9;j++)canvas.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:r.left+(p.x+1)*r.width/2,clientY:r.top+(1-p.y)*r.height/2}));await new Promise(requestAnimationFrame);}await new Promise(done=>{const check=()=>L.previewComplete()?done():requestAnimationFrame(check);requestAnimationFrame(check)});return {events:216,ms:performance.now()-t,renders:previewRenderSamples.length,maxHandlerMs:Math.max(...previewRenderSamples)}})()`);
    }
    await page.call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
    assert.equal(await page.evaluate('L.previewDiagnostics().pending'),false);
    assert.deepEqual(await page.evaluate(`L.characterPose(${actor.id})`),{x:41,y:48,fx:0,fy:1},'Esc keeps authoritative pose');
    if(!baseline){
        const hiddenTarget=await request(api+'/characters',{name:'Pripyat hidden target',data:{}},token);
        await request(`/lobbies/characters/${hiddenTarget.id}`,{visible_to:[player.user_id],editable_to:[]},token,'PUT');
        await request(url+'/spawn_character',{character_id:hiddenTarget.id,tile_x:45,tile_y:49},token);
        const info=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
        const protocol=await connect(info.webSocketDebuggerUrl),isolation=await protocol.call('Target.createBrowserContext');
        const targetTab=await protocol.call('Target.createTarget',{url:base,browserContextId:isolation.browserContextId});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        const peer=await connect(tabs.find(t=>t.id===targetTab.targetId).webSocketDebuggerUrl);
        try {
            await peer.call('Page.enable');await peer.call('Runtime.enable');
            await peer.call('Fetch.enable',{patterns:[{urlPattern:'*/static/js/*',requestStage:'Request'},
                {urlPattern:'*unpkg.com/three@0.128.0/*',requestStage:'Request'},{urlPattern:'*cdnjs.cloudflare.com/ajax/libs/socket.io/*',requestStage:'Request'}]});
            await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
            await peer.call('Page.navigate',{url:base+api+'/page'});
            await peer.until(`typeof enterLocation==='function'&&!window.isGM`,'Pripyat player');
            await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${loc.id})})()`);
            await peer.until(`window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Pripyat player loaded');
            results.sightCases=[];
            for(const client of [page,peer]) {
                await client.call('Page.bringToFront');
                for(const [name,x,y,level] of [['open',41,48,0],['interior',29,34,0],['upper',11,9,1],['top',11,9,2]]) {
                    const height=await client.evaluate(`L.previewFixturePose(${actor.id},${x},${y},${level})`);
                    // Pan the usual camera without changing its angle or zoom.
                    await client.evaluate(`(()=>{const delta=new T.Vector3(${x}+.5-locationControls.target.x,${height}-locationControls.target.y,${y}+.5-locationControls.target.z);locationControls.object.position.add(delta);locationControls.target.add(delta);locationControls.update();locationControls.object.updateMatrixWorld(true)})()`);
                    await new Promise(r=>setTimeout(r,120));
                    const snapshot=await client.evaluate('L.visibilitySnapshot()');
                    if(client===peer&&name==='open')assert.equal(await peer.evaluate(`L.characterVisible(${hiddenTarget.id})`),false,'target outside the current cone starts hidden');
                    const state=await client.evaluate(`(async()=>{L.showFacingMenu(${actor.id});const p=new T.Vector3(${x+3}+.5,${height},${y}+.5).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect(),t=performance.now();L.updateFacingPreview(r.left+(p.x+1)*r.width/2,r.top+(1-p.y)*r.height/2);await new Promise(done=>{const check=()=>L.previewComplete()?done():requestAnimationFrame(check);requestAnimationFrame(check)});return {name:'${name}',level:${level},ms:performance.now()-t,...L.previewDiagnostics()}})()`);
                    assert.deepEqual(await client.evaluate('L.previewTiles()'),await client.evaluate(`L.expectedPreview(${actor.id},${x+3},${y})`),name+' sight matches shared rules');
                    assert.equal(await client.evaluate('L.visibilitySnapshot()'),snapshot,'preview cannot reveal objects or write fog memory');
                    if(client===peer&&name==='open')assert.equal(await peer.evaluate(`L.characterVisible(${hiddenTarget.id})`),false,'future cone must not reveal the target before confirmation');
                    if(['interior','upper'].includes(name))fs.writeFileSync(path.join(artifactDir,`${client===page?'gm':'player'}-${name}.png`),Buffer.from((await client.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
                    await client.call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
                    assert.equal(await client.evaluate('L.previewDiagnostics().count'),0);
                    results.sightCases.push({...state,role:client===page?'gm':'player'});
                }
                await client.evaluate(`L.previewFixturePose(${actor.id},41,48,0)`);
                await client.evaluate(`(()=>{const d=new T.Vector3(41.5-locationControls.target.x,1-locationControls.target.y,48.5-locationControls.target.z);locationControls.object.position.add(d);locationControls.target.add(d);locationControls.update();locationControls.object.updateMatrixWorld(true)})()`);
            }
            // Ordinary movement remains server-authoritative and reaches both clients.
            await peer.evaluate(`socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${loc.id},character_id:${actor.id},x:46,y:48,floor_level:0,movement_mode:'walk'});true`);
            for(const client of [page,peer])await client.until(`L.characterPose(${actor.id}).x===46`,'Pripyat move synchronized');
            const combat=await request(url+'/combat',null,token,'GET');
            await request(url+'/combat/start',{location_character_ids:combat.characters.map(c=>c.location_character_id),initiator_location_character_id:combat.characters.find(c=>c.character_id===actor.id).location_character_id,initiator_first:true},token);
            for(const client of [page,peer])await client.until(`L.getCombatState()?.status==='active'`,'Pripyat combat synchronized');
            results.tooltips=[];
            await peer.evaluate(`L.showCombatActionMenu(innerWidth/2,innerHeight/2,${actor.id})`);
            for(const name of ['Утешить','Получить ОП','Должен это сделать']) {
                const p=await peer.evaluate(`(()=>{const r=document.querySelector('#combat-action-menu [aria-label="${name}"]').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
                await peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...p,buttons:0});
                const text=await peer.evaluate(`document.getElementById('combat-menu-tooltip').textContent`);
                assert.equal(text.split('\n')[0],name);
                if(name==='Утешить')assert.ok(!text.includes('3 ОД'),'fixed price is only on the button');
                if(name==='Получить ОП')assert.equal((text.match(/1 СД или 2 ОД/g)||[]).length,1);
                if(name==='Должен это сделать')assert.equal((text.match(/Должен это сделать/g)||[]).length,1);
                results.tooltips.push({name,text});
            }
            await peer.evaluate('document.body.click()');
            await peer.call('Page.bringToFront');
            const click=await peer.evaluate(`(()=>{L.showFacingMenu(${actor.id});const p=new T.Vector3(46.5,1,53.5).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
            console.log('PRIPYAT_COMBAT_POINTER '+JSON.stringify(await peer.evaluate(`({point:${JSON.stringify(click)},element:document.elementFromPoint(${click.x},${click.y})?.tagName,pending:L.previewDiagnostics().pending})`)));
            await peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...click,buttons:0});
            await peer.until('L.previewComplete()','combat future field');
            for(const type of ['mousePressed','mouseReleased'])await peer.call('Input.dispatchMouseEvent',{type,...click,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
            await peer.until(`[...document.querySelectorAll('button')].some(b=>b.textContent==='1 СД'&&b.getClientRects().length)`,'combat turn payment');
            await peer.evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent==='1 СД'&&b.getClientRects().length).click()`);
            for(const client of [page,peer])await client.until(`L.getCombatState().current_character.facing_y>0&&L.getCombatState().current_character.free_actions_current===0`,'turn synchronized and paid');
            await request(url+'/combat/end',{},token);
            console.log('PRIPYAT_SIGHT_CASES '+JSON.stringify(results.sightCases));
            console.log('PRIPYAT_TWO_CLIENTS movement, real left-click turn, payment, combat synchronization: passed');
        } finally {peer.close();await protocol.call('Target.disposeBrowserContext',{browserContextId:isolation.browserContextId});protocol.close();}
        results.renderSamples=await page.evaluate('previewRenderSamples');
    }
    console.log('PRIPYAT_UI '+JSON.stringify({context:results.context,preview:results.preview}));
    const out=path.resolve(artifactDir);fs.mkdirSync(out,{recursive:true});
    fs.writeFileSync(path.join(out,process.env.VTT_PRIPYAT_LEGACY==='1'?'legacy.json':baseline?'before.json':'after.json'),JSON.stringify(results,null,2));
    fs.writeFileSync(path.join(out,baseline?'before.png':'after.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    if(!baseline){assert.ok(results.context.length>=2);assert.ok(results.context.every(e=>e.prevented),'all gameplay right clicks prevent native menu');}
};
