const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    const baseline=!!process.env.VTT_WATER_BASELINE_DIR,mode=process.env.VTT_WATER_REGRESSION_STATE||(baseline?'before':'after'),fast=process.env.VTT_WATER_FINAL_FAST==='1';
    const fix=process.env.VTT_WATER_SHORE_FIX==='1';
    const output=safeSession?.artifacts||process.env.VTT_WATER_REGRESSION_OUTPUT||path.resolve(__dirname,fix?'../docs/material-previews/water-shore-fix':'../docs/material-previews/water-final');fs.mkdirSync(output,{recursive:true});
    const shot=async(name)=>{if(fast||process.env.VTT_WATER_FINAL_VIEW&&process.env.VTT_WATER_FINAL_VIEW!==name&&!(process.env.VTT_WATER_FINAL_VIEW==='world-flat'&&name.startsWith('world-flat-')))return;await pause(400);await page.evaluate('new Promise(requestAnimationFrame)');fs.writeFileSync(path.join(output,mode+'-'+name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));};
    const measure=async(local)=>page.evaluate(`(()=>{const {renderer:r,scene,camera}=${local?'localRender':'{renderer:W.renderer,scene:W.scene,camera:W.camera}'},gl=r.getContext(),p=new Uint8Array(4);const sample=()=>{const start=performance.now();r.render(scene,camera);gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,p);return {ms:performance.now()-start,calls:r.info.render.calls,triangles:r.info.render.triangles,textures:r.info.memory.textures,geometries:r.info.memory.geometries}};sample();const a=[sample(),sample(),sample()].sort((a,b)=>a.ms-b.ms);return {...a[1],fps:1000/a[1].ms,shaderErrors:r.info.programs.filter(p=>p.diagnostics?.runnable===false).length,webgl2:r.capabilities.isWebGL2,device:gl.getParameter(gl.getExtension("WEBGL_debug_renderer_info")?.UNMASKED_RENDERER_WEBGL||gl.RENDERER)}})()`);
    if(process.env.VTT_WATER_REGRESSION_ONLY==='1'){
        await page.evaluate(`window.W=sceneModule;window.T=threeModule;window.look=(x,y,z,tx,ty,tz)=>{W.controls.target.set(tx,ty,tz);W.camera.position.set(x,y,z);W.controls.update();W.camera.updateMatrixWorld(true)};W.setCelestialTime(480)`);
        await page.until('W.getWorldTerrainMaterialDiagnostics().ready','terrain PBR');
        await require('./water_regression_world.browser.cjs')({page,shot,measure,output,mode});return;
    }
    const updates=[];for(let z=0;z<64;z++)for(let x=23;x<45;x++){
        const wet=x>=31&&x<=34;let height=x===30&&z>=15&&z<=18?2.5:1;if(x===33&&z===12)height=1.4;
        updates.push({chunk_x:x>>5,chunk_y:z>>5,tile_x:x%32,tile_y:z%32,updates:{terrain:wet?'water':x<31?'grass':'sand',height,objects:[]}});
    }
    if(fix){
        const materials=['grass','bare_dirt','sand','rock','concrete','swamp'];
        for(let z=0;z<64;z++)for(const x of [29,30,35,36]){
            const m=materials[Math.min(5,Math.floor(z/10))];updates.push({chunk_x:x>>5,chunk_y:z>>5,tile_x:x%32,tile_y:z%32,updates:{terrain:m,height:z>=15&&z<=18&&x===30?2.5:1,objects:[]}});
        }
        for(let z=4;z<16;z++)for(let x=5;x<18;x++){
            const wet=(x>=8&&x<=11&&z>=7&&z<=10)||(x===12&&z===10)||(x===12&&z===11)||(x===13&&z===11);
            updates.push({chunk_x:0,chunk_y:0,tile_x:x,tile_y:z,updates:{terrain:wet?'water':'grass',height:wet&&x===12?1.4:1,objects:[]}});
        }
        updates.push({chunk_x:0,chunk_y:0,tile_x:30,tile_y:6,updates:{terrain:'rock',height:2.8,objects:[]}});
    }
    await request(`/lobbies/${lobby.id}/chunks/batch`,updates,token);
    await page.until("sceneModule.chunksMap.get('1,1')?.waterSurface&&sceneModule.chunksMap.get('0,0').tilesData[16][30].height===2.5",'world final fixture');await pause(500);
    await page.evaluate(`window.W=sceneModule;window.T=threeModule;window.look=(x,y,z,tx,ty,tz)=>{W.controls.target.set(tx,ty,tz);W.camera.position.set(x,y,z);W.controls.update();W.camera.updateMatrixWorld(true)};W.setCelestialTime(480)`);
    const route={waypoints:[[28,7],[38,7]],allow_bridges:true,settings:{scale:1,color:'#756f61'}};
    const plan=await request(`/lobbies/${lobby.id}/world-roads/preview`,route,token);
    await request(`/lobbies/${lobby.id}/world-roads/build`,{...route,signature:plan.signature},token);
    await page.until("W.chunksMap.get('1,0').roads?.some(m=>m.userData.worldBridgeAsset)",'bridge final');
    const worldViews=[['world-equal',[36,2.8,11,32.5,1,7.5]],['world-high',[33,4.4,21,30.8,1.5,17]],['world-levels',[36,3.4,16,33.5,1.2,12.5]],['world-river',[48,34,55,32,1,32]]];
    if(fix)worldViews.push(['world-small',[16,5,16,10,1,9]],['world-corners',[16,3.6,15,12,1,10.5]],['world-chunks',[37,5,38,32,1,32]],['world-bridge-slope',[33,3,11,30.4,1.1,7.5]]);
    for(const [name,cam]of worldViews){await page.evaluate(`look(${cam.join(',')})`);await shot(name);

    }
    if(worldViews.some(([name])=>name===process.env.VTT_WATER_FINAL_VIEW)){console.log('PASS requested world view');return;}
    await page.evaluate('look(36,4,12,32.5,1,7.5);W.directionalLight.position.set(12,23.577,0);W.directionalLight.updateMatrixWorld(true)');await shot('world-strong');
    const worldPerformance=fast||process.env.VTT_WATER_FINAL_VIEW==='world-flat'?null:await measure(false);
    await page.evaluate('W.directionalLight.intensity*=.2;W.ambientLight.intensity*=.3;W.fillLight.intensity*=.2');await shot('world-weak');
    const worldGeometry=await page.evaluate(`[...W.chunksMap.values()].map(e=>({chunk:[e.chunkX,e.chunkY],waterTriangles:(e.waterSurface.geometry.index?.count||e.waterSurface.geometry.attributes.position.count)/3,bankTriangles:(e.waterBank?.geometry.index?.count||e.waterBank?.geometry.attributes.position.count||0)/3,bytes:[e.waterSurface,e.waterBank].filter(Boolean).reduce((n,m)=>n+Object.values(m.geometry.attributes).reduce((s,a)=>s+a.array.byteLength,0)+(m.geometry.index?.array.byteLength||0),0)}))`);
    if(fix){
        await page.evaluate('W.directionalLight.intensity/=.2;W.ambientLight.intensity/=.3;W.fillLight.intensity/=.2;look(36,3.5,44,32.5,1,40.5)');
        const flatRoute={...route,waypoints:[[28,40],[38,40]]};
        const flatPlan=await request(`/lobbies/${lobby.id}/world-roads/preview`,flatRoute,token);
        await page.evaluate(`W.showWorldRoadPreview(${JSON.stringify(flatPlan)},{scale:1})`);
        await page.until('W.scene.children.some(m=>m.userData.roadRoutePreview&&m.userData.worldBridgeAsset)','flat bridge preview');await shot('world-flat-preview');
        await page.evaluate('W.removeWorldRoadPreview()');
        await request(`/lobbies/${lobby.id}/world-roads/build`,{...flatRoute,signature:flatPlan.signature},token);
        if(process.env.VTT_WATER_FINAL_VIEW==='world-flat'||process.env.VTT_WATER_FINAL_WORLD_ONLY==='1'){
            await page.call('Page.reload');await page.until('window.isGM','flat bridge reload');
            await page.evaluate("(async()=>{window.W=await import('/static/js/lobby3d.js');window.look=(x,y,z,tx,ty,tz)=>{W.controls.target.set(tx,ty,tz);W.camera.position.set(x,y,z);W.controls.update();W.camera.updateMatrixWorld(true)}})()");
            await page.until("W.chunksMap.get('1,1')?.roads?.some(m=>m.userData.worldBridgeAsset)",'saved flat bridge meshes');await page.until('W.getWorldTerrainMaterialDiagnostics().ready','reloaded terrain textures');
            await page.evaluate('W.setCelestialTime(480);W.directionalLight.position.set(12,23.577,0);look(36,3.5,44,32.5,1,40.5)');
        }else await page.until("W.chunksMap.get('1,1').tilesData[8][0].objects.some(o=>o.type==='road')",'flat bridge build');
        await pause(600);await shot('world-flat-bridge');
        await page.evaluate('look(43,16,62,32,1,43)');await shot('world-materials');
    }
    if(process.env.VTT_WATER_REGRESSION_MAP){await require('./water_regression_world.browser.cjs')({page,shot,measure,output,mode});}
    if(process.env.VTT_WATER_FINAL_VIEW==='world-flat'){console.log('PASS flat bridge and preview views');return;}
    if(process.env.VTT_WATER_FINAL_WORLD_ONLY==='1'){
        fs.writeFileSync(path.join(output,mode+'-world-results.json'),JSON.stringify({worldPerformance,worldGeometry},null,2)+'\n');console.log('PASS final world boundary and material');return;
    }
    if(fast&&!baseline){
        await request(`/lobbies/${lobby.id}/chunks/1/1/tile/0/0`,{height:1.7},token,'PATCH');
        await page.until("W.chunksMap.get('1,1').tilesData[0][0].height===1.7",'world height edit');
        for(const terrain of ['water','sand']){
            await request(`/lobbies/${lobby.id}/chunks/1/1/tile/3/0`,{terrain},token,'PATCH');
            await page.until(`W.chunksMap.get('1,1').tilesData[0][3].terrain===${JSON.stringify(terrain)}`,'world shore edit');
        }
        const savedWorld=await page.evaluate("JSON.stringify([...W.chunksMap.values()].sort((a,b)=>a.chunkY-b.chunkY||a.chunkX-b.chunkX).map(e=>e.tilesData))");
        await page.call('Page.reload');await page.until('window.isGM','full reload');
        await page.evaluate("(async()=>{window.W=window.sceneModule=await import('/static/js/lobby3d.js');window.T=await import('three')})()");
        await page.until("W.chunksMap.size===4&&W.chunksMap.get('1,0').roads?.some(m=>m.userData.worldBridgeAsset)",'reloaded river and bridge');
        assert.deepEqual(JSON.parse(await page.evaluate("JSON.stringify([...W.chunksMap.values()].sort((a,b)=>a.chunkY-b.chunkY||a.chunkX-b.chunkX).map(e=>e.tilesData))")),JSON.parse(savedWorld));
    }
    const tiles=Array.from({length:32},()=>Array.from({length:32},()=>({terrain:'grass',height:1,objects:[]})));
    const rect=(x0,z0,x1,z1,terrain,height)=>{for(let z=z0;z<=z1;z++)for(let x=x0;x<=x1;x++)tiles[z][x]={terrain,height,objects:[]};};
    rect(3,3,6,6,'water',1);rect(11,2,18,8,'sand',1);rect(13,4,16,6,'water',1);
    for(const [x,z]of [[23,5],[25,5],[24,4]])tiles[z][x]={terrain:'sand',height:1,objects:[]};tiles[5][24]={terrain:'water',height:1,objects:[]};
    rect(3,13,7,17,'water',1);rect(2,13,2,17,'grass',2.5);rect(1,12,1,18,'grass',1.6);
    rect(13,13,17,17,'water',1);tiles[15][15].height=1.6;tiles[14][24]={terrain:'water',height:1,objects:[]};tiles[15][25]={terrain:'water',height:1.8,objects:[]};
    tiles[10][0]={terrain:'water',height:1.4,objects:[]};
    if(fix){
        const materials=['grass','bare_dirt','sand','rock','concrete','swamp'];
        for(let i=0;i<6;i++){
            const x=2+i*5;rect(x,23,x+3,29,materials[i],1);rect(x+1,25,x+2,27,'water',1);
            tiles[26][x+2].height=i%2?1.25:1;
        }
    }
    if(process.env.VTT_WATER_REGRESSION_SNOW==='1'){
        rect(19,10,22,18,'snow',1.5);rect(20,12,21,16,'water',1);
    }
    const loc=await request(`/lobbies/${lobby.id}/locations`,{name:'Final water acceptance',world_tile_x:25,world_tile_z:15,grid_width:32,grid_height:32,tiles_data:tiles},token);
    await page.evaluate(`(()=>{const add=T.Scene.prototype.add;window.restoreWaterObserver=()=>T.Scene.prototype.add=add;T.Scene.prototype.add=function(...objects){for(const root of objects)root.traverse(m=>{if(!m.isMesh)return;const old=m.onBeforeRender;m.onBeforeRender=function(renderer,scene,camera,...args){old?.call(this,renderer,scene,camera,...args);if(window.isLocationActive&&renderer!==W.renderer)window.localRender={renderer,scene,camera}}});return add.apply(this,objects)}})()`);
    await page.evaluate(`window.enterLocation(${loc.id})`);await page.until(`window.currentLocationId===${loc.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')&&Boolean(window.localRender)`,'local final');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.lookLocal=(x,y,z,tx,ty,tz)=>{locationControls.target.set(tx,ty,tz);locationControls.object.position.set(x,y,z);locationControls.update();locationControls.object.updateMatrixWorld(true)};window.waterTop=()=>localRender.scene.children.find(m=>m.isMesh&&!m.isInstancedMesh&&m.material?.customProgramCacheKey?.().includes('world-aligned-water-'));window.waterY=(x,z)=>{const a=waterTop().geometry.attributes.position,ys=[];for(let i=0;i<a.count;i++)if(a.getX(i)>x+.1&&a.getX(i)<x+.9&&a.getZ(i)>z+.1&&a.getZ(i)<z+.9)ys.push(a.getY(i));return Math.min(...ys)};document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();document.querySelector('#panel-characters:not(.collapsed) .toggle-btn')?.click()})()`);
    await page.until('L.getLocationTerrainMaterialDiagnostics()?.ready&&Boolean(waterTop())','local final PBR');
    const localViews=[['local-pond',[9,6,12,5,1,5]],['local-high',[10,5,21,4,1.4,15]],['local-sand',[19,5.5,12,15,1,5]],['local-levels',[20,5,22,15.5,1.3,15.5]],['local-diagonal',[29,6,21,25,1.3,15]]];
    if(fix)localViews.push(['local-materials',[20,14,39,16,1,26]],['local-corners',[19,4,21,15.5,1.2,15.5]]);
    if(process.env.VTT_WATER_REGRESSION_SNOW==='1')localViews.push(['local-snow',[26,7,22,20.5,1.2,14]]);
    for(const [name,cam]of localViews){await page.evaluate(`lookLocal(${cam.join(',')})`);await shot(name);}
    if(localViews.some(([name])=>name===process.env.VTT_WATER_FINAL_VIEW)){console.log('PASS requested local view');return;}
    await page.evaluate(`lookLocal(9,4,10,5,1,5);window.localLights=localRender.scene.children.filter(m=>m.isLight);window.lightValues=localLights.map(l=>({intensity:l.intensity,position:l.position.toArray()}));const sun=localLights.find(l=>l.isDirectionalLight);if(sun){sun.intensity=1.2;sun.position.set(12,23.577,0);sun.updateMatrixWorld(true)}`);await shot('local-strong');
    const localPerformance=fast?null:await measure(true);
    await page.evaluate('localLights.forEach(l=>l.intensity*=.2)');await shot('local-weak');
    await page.evaluate('localLights.forEach((l,i)=>{l.intensity=lightValues[i].intensity;l.position.fromArray(lightValues[i].position);l.updateMatrixWorld(true)})');
    const localGeometry=await page.evaluate(`(()=>{const s=new Set(),materials=new Set();localRender.scene.traverse(m=>{if(!m.isMesh)return;if(m.geometry)s.add(m.geometry);for(const mat of [].concat(m.material||[]))materials.add(mat)});return {geometryAttributeBytes:[...s].reduce((n,g)=>n+Object.values(g.attributes).reduce((s,a)=>s+a.array.byteLength,0)+(g.index?.array.byteLength||0),0),materialCount:materials.size}})()`);
    const levelBefore=await page.evaluate('waterY(24,5)');
    const regressions={};
    if(!baseline){
        await page.evaluate('L.setLocationEditMode(true);L.applyLocationBrush(23,5,{terrain:"grass"},0);L.setLocationEditMode(false)');await pause(600);
        const levelAfter=await page.evaluate('waterY(24,5)');assert.ok(Math.abs(levelAfter-levelBefore)<1e-6,'shore tint must not move water');regressions.stableShoreLevel={before:levelBefore,after:levelAfter};
        const saved=await request(`/lobbies/${lobby.id}/locations/${loc.id}`,null,token,'GET');
        assert.equal(saved.tiles_data[5][24].height,1);assert.equal(saved.tiles_data[5][24].water_level_offset,-.2);
        await page.evaluate(`L.applyLocationLighting(${JSON.stringify({...saved.lighting,dark_tiles:[{x:0,z:10}]})},false);L.applyLocationLighting(${JSON.stringify(saved.lighting)},false)`);
        const sideColor=await page.evaluate(`(()=>{const m=localRender.scene.children.find(m=>m.isInstancedMesh&&m.geometry.attributes.waterSideMask);return new T.Color().fromArray(m.instanceColor.array,(10*32)*3).getHexString()})()`);
        assert.notEqual(sideColor,'ffffff');regressions.repeatedDarkness=sideColor;
        await page.evaluate('L.setLocationEditMode(true);L.applyLocationBrush(5,5,{height:1.5},0);L.setLocationEditMode(false)');await page.until('waterY(5,5)>1.2','height brush final');regressions.brush=true;
        const target=await(await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(base+'/lobbies/'+lobby.id+'/page')}`,{method:'PUT'})).json(),peer=await connect(target.webSocketDebuggerUrl);
        try{
            await peer.call('Page.enable');await peer.call('Runtime.enable');await peer.until('window.isGM','peer');
            if(fix){
                await peer.evaluate("(async()=>{window.P=await import('/static/js/lobby3d.js')})()");
                await peer.until("P.chunksMap.get('1,1')?.tilesData[8][0].objects.some(o=>o.type==='road')",'peer saved flat bridge');
                await request(`/lobbies/${lobby.id}/chunks/0/0/tile/30/7`,{height:1.2},token,'PATCH');
                await peer.until("P.chunksMap.get('0,0').tilesData[7][30].height===1.2",'peer bridge bank height');
                await page.until("W.chunksMap.get('0,0').tilesData[7][30].height===1.2",'local client world bank');regressions.worldPeerSync=true;
            }
            await peer.evaluate(`window.enterLocation(${loc.id})`);await peer.until(`window.currentLocationId===${loc.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'peer local');
            await peer.evaluate("window.waterEvents=[];window.socket.on('location_tiles_updated',e=>waterEvents.push(e));true");await page.call('Page.bringToFront');
            await page.evaluate('L.setLocationEditMode(true);L.applyLocationBrush(24,6,{terrain:"water"},0);L.setLocationEditMode(false)');
            await peer.until('waterEvents.some(e=>e.updates.some(t=>t.x===24&&t.z===6&&t.water_level_offset===-.2))','new joined water offset sync');regressions.peerSync=true;
            const record=await request(`/lobbies/${lobby.id}/locations/${loc.id}`,null,token,'GET');assert.equal(record.tiles_data[6][24].water_level_offset,-.2);
            await page.call('Page.bringToFront');await page.evaluate(`window.enterLocation(${loc.id})`);await page.until(`window.currentLocationId===${loc.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'reopen local');await page.evaluate('new Promise(requestAnimationFrame)');assert.ok(Math.abs(await page.evaluate('waterY(24,5)')-levelBefore)<1e-6);regressions.reload=true;
        }finally{await peer.call('Page.close').catch(()=>{});peer.close();}
    }
    const compatibility=await page.evaluate(`(()=>{const check=r=>({webgl2:r.capabilities.isWebGL2,shaderErrors:r.info.programs.filter(p=>p.diagnostics?.runnable===false).length});return {world:check(W.renderer),local:check(localRender.renderer)}})()`);
    for(const scene of Object.values(compatibility)){assert.equal(scene.shaderErrors,0);if(process.env.VTT_FORCE_WEBGL1==='1')assert.equal(scene.webgl2,false);}
    const result={compatibility,baseline,worldPerformance,localPerformance,worldGeometry,localGeometry,levelBefore,regressions,forcedWebGL1:process.env.VTT_FORCE_WEBGL1==='1'};
    fs.writeFileSync(path.join(output,mode+(fast?'-fast':'')+'-results.json'),JSON.stringify(result,null,2)+'\n');console.log('PASS water FINAL:',JSON.stringify(result));
};
