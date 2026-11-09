// VTT_BUILDING_SHELL=1 VTT_BROWSER_GPU=1 node tests/world_object_editor.browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
async function runGrassFloorRegression({page,request,token,lobby,loc}) {
    await page.until('L.getLocationGrassDiagnostics().kinds.medium01?.added>0','grass model loaded');
    const url=`/lobbies/${lobby.id}/locations/${loc.id}`;
    const objects=await page.evaluate(`(async()=>{const G=await import('/static/js/locationHouseGenerator.js');
        const objects=G.planBuildingShell({minX:2,minZ:2,width:10,depth:10,shape:'L',storeys:1,basements:0,wealth:'ordinary',condition:'intact',roofType:'gable',buildingId:'grass',seed:'grass',groundHeight:()=>1}).objects;
        objects.push({type:'floor',tile_x:17,tile_y:16,name:'Rotated floor',properties:{level:0,rotation:.63,dimensions:{width:5,depth:4,height:.12}}});return objects})()`);
    await request(url+'/objects/batch',{objects},token);
    await pause(700);
    for(const camera of [[17,18,22],[6,12,20]]) {
        const result=await page.evaluate(`(async()=>{L.setLocationEditMode(false);L.setLocationViewLevel(0);
            locationControls.target.set(11,1,11);locationControls.object.position.set(${camera.join(',')});locationControls.update();
            const A=await import('/static/js/locationArchitecture.js'),s=shellRender.s;
            const objects=s.children.map(m=>m.userData.locationObject).filter(Boolean);
            const floors=objects.filter(o=>o.type==='floor'&&o.properties.level===0).map(o=>A.floorVisualPose(o,objects));
            const inside=p=>floors.some(f=>{const dx=p.x-f.x,dz=p.z-f.z;return Math.abs(dx*f.axis.x+dz*f.axis.z)<f.width/2-.001&&Math.abs(dx*f.normal.x+dz*f.normal.z)<f.depth/2-.001});
            let instances=0,intruding=0;const matrix=new T.Matrix4(),point=new T.Vector3();s.updateMatrixWorld(true);
            s.traverse(m=>{if(!m.userData.naturalGrassKind)return;const positions=m.geometry.attributes.position;
                for(let i=0;i<m.count;i++){instances++;m.getMatrixAt(i,matrix);matrix.premultiply(m.matrixWorld);
                    for(let j=0;j<positions.count;j++){point.fromBufferAttribute(positions,j).applyMatrix4(matrix);
                        if(point.y>1.036&&inside(point)){intruding++;break}}}});
            shellRender.r.render(s,shellRender.c);return {instances,intruding,floors:floors.length};})()`);
        console.log('GRASS FLOOR GEOMETRY',JSON.stringify({camera,...result}));
        assert.ok(result.instances>100,'outside grass remains populated');
        assert.ok(result.floors>=3,'L wings and rotated floor are present');
        assert.equal(result.intruding,0,'no rendered grass vertices protrude above floor zero');
    }
    console.log('GRASS FLOOR PASS');
}
async function runFurnishedBuilding({page,request,token,lobby,loc,base,connect,debugPort,draw}) {
    const detail=()=>request(`/lobbies/${lobby.id}/locations/${loc.id}`,null,token,'GET');
    const grass=async()=>{
        await page.until('L.getLocationGrassDiagnostics().kinds.medium01?.added>0','grass tufts loaded');
        return page.evaluate(`(()=>{const {r,s,c}=shellRender;r.render(s,c);let count=0,uploaded=0;s.traverse(m=>{
            if(!m.userData.naturalGrassKind)return;count+=m.count;
            if(['map','alphaMap','normalMap'].every(key=>m.material[key].version>0&&r.properties.get(m.material[key]).__webglTexture))uploaded++;
        });return {count,uploaded}})()`);
    };
    const initial=await grass();assert.ok(initial.count>100&&initial.uploaded>0,'tufts must upload their actual color, alpha and normal textures');
    const frame=async name=>{const file=path.join(os.tmpdir(),`vtt-combined-${name}.png`);fs.writeFileSync(file,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));console.log('COMBINED FRAME',file)};
    await page.evaluate('locationControls.target.set(6,1,6);locationControls.object.position.set(10,6,12);locationControls.update();true');
    await pause(200);await frame('grass');
    const visiblePixels=await page.evaluate(`(()=>{const {r,s,c}=shellRender,gl=r.getContext(),w=r.domElement.width,h=r.domElement.height;
        const before=new Uint8Array(w*h*4),after=new Uint8Array(before.length),tufts=[];s.traverse(m=>{if(m.userData.naturalGrassKind)tufts.push(m)});
        r.render(s,c);gl.readPixels(0,0,w,h,gl.RGBA,gl.UNSIGNED_BYTE,before);
        tufts.forEach(m=>m.visible=false);r.render(s,c);gl.readPixels(0,0,w,h,gl.RGBA,gl.UNSIGNED_BYTE,after);
        tufts.forEach(m=>m.visible=true);r.render(s,c);let different=0;
        for(let i=0;i<before.length;i+=4)if(Math.abs(before[i]-after[i])+Math.abs(before[i+1]-after[i+1])+Math.abs(before[i+2]-after[i+2])>12)different++;
        return different})()`);
    assert.ok(visiblePixels>100,'tufts must visibly affect the actual game frame, not only exist in mesh diagnostics');
    console.log('VISIBLE GRASS PIXELS',visiblePixels);
    await page.evaluate('locationControls.target.set(8,1,8);locationControls.object.position.set(17,18,22);locationControls.update();true');
    const target=await page.call('Target.createTarget',{url:`${base}/lobbies/${lobby.id}/page`,background:true});
    const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json(),peer=await connect(tabs.find(t=>t.id===target.targetId).webSocketDebuggerUrl);
    try {
        await peer.until('window.enterLocation&&window.socket?.connected','combined peer ready');
        await peer.call('Page.bringToFront');
        await peer.evaluate(`(async()=>{const T=await import('three');window.L=await import('/static/js/locationScene.js');
            const add=T.Scene.prototype.add;T.Scene.prototype.add=function(...roots){if(roots.some(r=>r.userData.locationObjectId))window.combinedScene=this;return add.apply(this,roots)};
            window.combinedBatches=[];socket.on('location_objects_changed',d=>combinedBatches.push(d));socket.on('joined_location',d=>window.combinedJoined=d);await enterLocation(${loc.id});true})()`);
        await peer.until(`combinedJoined?.location_id===${loc.id}&&socket.io.engine.transport.name==='websocket'`,'peer joined via WebSocket');
        await page.call('Page.bringToFront');
        let original=[];
        for(const [index,shape] of ['L','U'].entries()){
            if(index)await page.evaluate('locationControls.target.set(19,1,19);locationControls.object.position.set(28,18,33);locationControls.update();true');
            await pause(180);
            await draw(index?14.5:2.5,index?23.5:11.5);
            if(!index) {
                assert.equal(await page.evaluate(`document.querySelector('dialog form').elements.contents.value`),'furnished');
                assert.deepEqual(await page.evaluate(`[...document.querySelector('dialog form').elements.buildingType.options].map(o=>o.value)`),['residential','shop','warehouse','workshop','cafe','clinic','office','school','garage','bathhouse']);
                assert.equal(await page.evaluate(`!!document.querySelector('[data-house-seed], [data-house-kind]')`),false);
                assert.equal(await page.evaluate(`document.querySelector('.location-asset-tabs').hidden`),true);
                const density=()=>page.evaluate(`({hidden:document.querySelector('[data-anomaly-density]').hidden,disabled:document.querySelector('dialog form').elements.anomalyDensity.disabled})`);
                assert.deepEqual(await density(),{hidden:true,disabled:true});
                await page.evaluate(`(()=>{const a=document.querySelector('dialog form').elements.anomalies;a.checked=true;a.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
                assert.deepEqual(await density(),{hidden:false,disabled:false});
                assert.ok(await page.evaluate(`(()=>{const c=document.querySelector('dialog form').elements.anomalies,r=c.getBoundingClientRect(),l=c.parentElement.getBoundingClientRect();return r.x>l.x+l.width/2&&Math.abs((r.y+r.height/2)-(l.y+l.height/2))<2})()`),'checkbox is aligned to the right of its label');
                await page.evaluate(`(()=>{const a=document.querySelector('dialog form').elements.anomalies;a.checked=false;a.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
                assert.deepEqual(await density(),{hidden:true,disabled:true});
                if(process.env.VTT_SHELL_INSPECT==='1')fs.writeFileSync(path.join(os.tmpdir(),'vtt-new-building-dialog.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
                const camera=await page.evaluate(`({position:locationControls.object.position.toArray(),target:locationControls.target.toArray()})`);
                const position=()=>page.evaluate('locationControls.object.position.toArray()');
                assert.equal(await page.evaluate(`document.activeElement===document.querySelector('[data-shell-preview]')`),true,'opening the building menu leaves camera controls available');
                for(const [code,key,vk] of [['KeyW','w',87],['KeyA','a',65],['KeyS','s',83],['KeyD','d',68]]) {
                    const start=await position();
                    await page.call('Input.dispatchKeyEvent',{type:'keyDown',code,key,windowsVirtualKeyCode:vk});
                    await pause(150);
                    assert.notDeepEqual(await position(),start,`${key} moves the camera with the new building menu open`);
                    await page.call('Input.dispatchKeyEvent',{type:'keyUp',code,key,windowsVirtualKeyCode:vk});
                    await pause(80);const stopped=await position();await pause(150);
                    assert.deepEqual(await position(),stopped,`${key} stops on release with the menu open`);
                }
                await page.evaluate(`document.querySelector('dialog form').elements.width.focus();true`);
                const start=await position();
                await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyW',key:'w',windowsVirtualKeyCode:87});
                await pause(150);
                await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyW',key:'w',windowsVirtualKeyCode:87});
                assert.deepEqual(await position(),start,'editing a field does not move the camera');
                await page.evaluate(`locationControls.object.position.fromArray(${JSON.stringify(camera.position)});locationControls.target.fromArray(${JSON.stringify(camera.target)});locationControls.update();document.querySelector('[data-shell-preview]').focus();true`);
                console.log('NEW BUILDING MENU WASD PASS');
            }
            const configure=()=>page.evaluate(`(()=>{const f=document.querySelector('dialog form');
                f.elements.buildingType.value='${index===0?'shop':'warehouse'}';f.elements.buildingType.dispatchEvent(new Event('change',{bubbles:true}));
                f.elements.shape.value='${shape}';f.elements.width.value=10;f.elements.depth.value=10;f.elements.storeys.value=2;f.elements.basements.value=1;
                if(!f.dataset.polishChecked){if(f.elements.anomalies.checked)throw Error('Anomalies must be off by default');f.dataset.polishChecked='1';}
                f.elements.anomalies.checked=${index===0};f.elements.anomalyDensity.value='1';
                f.elements.contents.value='furnished';f.elements.wealth.value='rich';f.elements.shape.dispatchEvent(new Event('change',{bubbles:true}));
                f.dispatchEvent(new Event('input'));document.querySelector('[data-shell-preview]').click();true})()`);
            await configure();await page.until('!document.querySelector("[data-shell-create]").disabled','furnished preview ready');
            assert.deepEqual((await detail()).objects,original,'preview does not change saved architecture');
            if(!index){
                await page.evaluate("L.setLocationAssetPreset('wall');document.querySelector('[data-shell-preview]').focus();true");
                assert.equal(await page.evaluate(`document.activeElement===document.querySelector('[data-shell-preview]')`),true);
                await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyE',key:'e',windowsVirtualKeyCode:69});
                await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyE',key:'e',windowsVirtualKeyCode:69});
                assert.equal(await page.evaluate('L.getLocationEditMode()'),false,'E exits editing from the preview button');
                assert.equal(await page.evaluate('!!document.querySelector("dialog[open]")'),false,'leaving editing closes the building preview');
                console.log('BUILDING PREVIEW E PASS');
                assert.equal((await detail()).objects.length,0,'cancel creates nothing');
                await draw();await configure();await page.until('!document.querySelector("[data-shell-create]").disabled','second furnished preview ready');
            }
            const count=await peer.evaluate('combinedBatches.length');
            await page.evaluate("document.querySelector('dialog form').requestSubmit(document.querySelector('[data-shell-create]'));true");
            await page.until(`L.getLocationBuildingIds().length===${index+1}`,'combined building saved');
            const saved=(await detail()).objects,added=saved.filter(o=>!original.some(p=>p.id===o.id));
            assert.ok(added.some(o=>o.properties.layout_rooms?.length),'saved rooms exist');
            assert.ok(added.filter(o=>o.properties.building_generation).every(o=>o.properties.building_generation.buildingType===(index===0?'shop':'warehouse')));
            const furniture=added.filter(o=>o.properties.furniture_generation);assert.ok(furniture.length>0,'furniture saved in the creation batch');
            assert.ok(furniture.some(o=>o.properties.furniture_generation.role===(index===0?'shop':'storage')),'purpose controls the furniture');
            assert.ok(!furniture.some(o=>o.type==='household_cooker'||o.type.startsWith('household_bed_')),'commercial buildings have no residential furniture');
            assert.ok(added.some(o=>o.type==='stairs'&&o.properties.level===0));
            assert.ok(added.filter(o=>o.type==='ladder').every(o=>o.properties.level<0));
            assert.ok(added.some(o=>o.type==='hatch'&&o.properties.level===0&&o.properties.is_open===false));
            assert.equal(added.some(o=>o.type==='anomaly'),index===0,'anomalies follow opt-in in the atomic creation batch');
            assert.deepEqual([...new Set(furniture.map(o=>o.properties.level))].sort((a,b)=>a-b),[-1,0,1]);
            assert.deepEqual(saved.filter(o=>original.some(p=>p.id===o.id)),original,'next creation preserves existing house and furniture');
            const ids=saved.map(o=>o.id).sort((a,b)=>a-b);
            await peer.until(`JSON.stringify(combinedScene.children.filter(m=>m.userData.locationObjectId).map(m=>m.userData.locationObjectId).sort((a,b)=>a-b))===${JSON.stringify(JSON.stringify(ids))}`,'complete house and furniture synchronize together');
            assert.equal(await peer.evaluate('combinedBatches.length'),count+1,'one atomic creation event');
            await frame(shape);original=saved;
        }
        await page.evaluate(`(async()=>{exitLocation();await enterLocation(${loc.id});selectLocationEditorTool('building');document.querySelector('#panel-tools.collapsed .toggle-btn')?.click();true})()`);
        const shown=await page.evaluate(`(()=>{const b=document.getElementById('loc-generate-furniture');b.scrollIntoView();const r=b.getBoundingClientRect();return !b.closest('[hidden]')&&!b.disabled&&r.width>0&&r.height>0})()`);
        assert.equal(shown,true,'furniture action is visible and selects an existing building after reopening');
        const after=await grass();assert.ok(after.uploaded>0&&after.count>100,'tufts survive location exit and reload');
        assert.deepEqual((await detail()).objects,original,'reopening preserves combined generation');
        await page.evaluate(`window.retiredCombined=[new WeakRef(shellRender.s),new WeakRef(locationControls.object),new WeakRef(document.querySelector('#location-canvas canvas'))];exitLocation();window.shellRender=null;true`);
        await page.evaluate('document.body.getBoundingClientRect();new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
        await pause(80);await page.call('HeapProfiler.collectGarbage');
        assert.equal(await page.evaluate('retiredCombined.every(ref=>ref.deref()===undefined)'),true,'uploaded grass and furnished building do not retain the departed renderer');
        console.log('FURNISHED BUILDING PASS',JSON.stringify({initial,after,objects:original.length}));
    }finally{peer.close();await page.call('Target.closeTarget',{targetId:target.targetId}).catch(()=>{});}
}

module.exports = async ({page,request,token,lobby,base,connect,debugPort,safeSession}) => {
    const api = `/lobbies/${lobby.id}`;
    const loc = await request(`${api}/locations`, { name:'Shell acceptance', world_tile_x:12,world_tile_z:12,
        grid_width:24,grid_height:24,tiles_data:Array.from({length:24},()=>Array.from({length:24},()=>({terrain:process.env.VTT_BUILDING_GRASS==='1'||process.env.VTT_BUILDING_FURNISHED==='1'?'grass':'bare_dirt',height:1,objects:[]}))) },token);
    const detail = ()=>request(`${api}/locations/${loc.id}`,null,token,'GET');
    await page.evaluate(`(async()=>{
        window.T=await import('three');const old=T.Scene.prototype.add;
        T.Scene.prototype.add=function(...roots){for(const root of roots)root.traverse(m=>{if(!m.isMesh)return;
            const before=m.onBeforeRender;m.onBeforeRender=function(r,s,c,...args){before.call(this,r,s,c,...args);if(window.isLocationActive)window.shellRender={r,s,c}}});return old.apply(this,roots)};
        window.L=await import('/static/js/locationScene.js');await enterLocation(${loc.id});
        L.setLocationEditMode(true);selectLocationEditorTool('assets');
        locationControls.target.set(8,1,8);locationControls.object.position.set(17,18,22);locationControls.update();
        document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();
        document.querySelector('#panel-characters:not(.collapsed) .toggle-btn')?.click();
    })()`);
    await page.until('window.shellRender&&document.querySelector("[data-house-draw]")','local renderer');
    if(process.env.VTT_BUILDING_INTERIOR==='1')return runInterior({page,request,token,lobby,loc});
    if(process.env.VTT_BUILDING_FIX2==='1')return runFloorFix2({page,request,token,lobby,loc});
    if(process.env.VTT_BUILDING_APERTURE==='1')return runApertureRegression({page,request,token,lobby,loc});
    if(process.env.VTT_BUILDING_GRASS==='1')return runGrassFloorRegression({page,request,token,lobby,loc});
    const stats=()=>page.evaluate(`(async()=>{const frames=[],cpu=[],gpu=[],queries=[];const r=shellRender.r,gl=r.getContext(),ext=gl.getExtension('EXT_disjoint_timer_query_webgl2'),render=r.render;
        r.render=function(...args){const q=ext?gl.createQuery():null;if(q)gl.beginQuery(ext.TIME_ELAPSED_EXT,q);const start=performance.now();const result=render.apply(this,args);cpu.push(performance.now()-start);if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);queries.push(q)}return result};
        let last=performance.now();try{for(let i=0;i<90;i++)await new Promise(resolve=>requestAnimationFrame(t=>{frames.push(t-last);last=t;resolve()}));
        if(ext&&!gl.getParameter(ext.GPU_DISJOINT_EXT))for(const q of queries)if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE))gpu.push(gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);
        let meshes=0;shellRender.s.traverse(o=>{if(o.isMesh)meshes++});const mean=a=>a.length?a.reduce((a,b)=>a+b,0)/a.length:null;
        return {meshes,calls:r.info.render.calls,triangles:r.info.render.triangles,frameMs:mean(frames),cpuRenderMs:mean(cpu),gpuMs:mean(gpu)};
        }finally{r.render=render;for(const q of queries)gl.deleteQuery(q)}})()`);
    await page.evaluate(`window.shellBatches=[];const nativeFetch=window.fetch;window.fetch=async(...args)=>{const start=performance.now(),response=await nativeFetch(...args);if(String(args[0]).includes('/objects/batch')){const body=await response.clone().json();shellBatches.push({method:args[1]?.method,ms:performance.now()-start,timings:body.timings})}return response};true`);
    const emptyStats=await stats();
    const aim=(x,z)=>page.evaluate(`(()=>{const p=new T.Vector3(${x},1.05,${z}).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
    const draw=async(min=2.5,max=11.5)=>{
        await page.evaluate(`L.setLocationEditMode(true);selectLocationEditorTool('building');L.setLocationHouseDrawMode(true);document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();true`);
        const a=await aim(min,min),b=await aim(max,max);
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...a});await pause(100);
        await page.call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...a});
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',buttons:1,...b});
        await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...b});
        await page.until('!!document.querySelector("dialog[open] [data-shell-preview]")','shell options after drag');
    };
    if(process.env.VTT_BUILDING_FURNISHED==='1')return runFurnishedBuilding({page,request,token,lobby,loc,base,connect,debugPort,draw});
    await draw();
    await page.evaluate(`(()=>{const f=document.querySelector('dialog form');f.elements.shape.value='L';f.elements.width.value=10;f.elements.depth.value=10;
        f.elements.storeys.value=2;f.elements.basements.value=2;f.elements.wealth.value='rich';f.elements.condition.value='damaged';
        f.elements.shape.dispatchEvent(new Event('change',{bubbles:true}));
        document.querySelector('[data-shell-preview]').click()})()`);
    assert.equal((await detail()).objects.length,0,'preview must not persist objects');
    await page.until('document.querySelector("[data-shell-create]")?.disabled===false','предпросмотр здания завершён');
    assert.equal(await page.evaluate('document.querySelector("[data-shell-create]").disabled'),false);
    assert.ok(await page.evaluate('document.querySelector("dialog[open]").getBoundingClientRect().bottom <= innerHeight'));
    // Read preview in the ordinary game camera; retain only a temporary inspection frame.
    if(process.env.VTT_SHELL_INSPECT==='1'){
        const file=path.join(safeSession?.artifacts || os.tmpdir(),'vtt-shell-preview.png');
        fs.writeFileSync(file,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('INSPECTION FRAME',file);
    }
    const generationStart=Date.now();
    await page.evaluate(`document.querySelector('dialog form').requestSubmit(document.querySelector('[data-shell-create]'));true`);
    await page.until(`document.body.innerText.includes('Все элементы доступны для ручной правки')`,'shell persisted');
    const generationMs=Date.now()-generationStart;
    const built=await detail();
    console.log('SHELL PERFORMANCE',JSON.stringify({generationMs,objects:built.objects.length,empty:emptyStats,built:await stats(),batches:await page.evaluate('shellBatches'),phases:await page.evaluate("performance.getEntriesByType('measure').filter(e=>e.name.startsWith('location-building:')).map(e=>({name:e.name,ms:e.duration}))")}));
    if(process.env.VTT_SHELL_PERF_ONLY==='1')return;
    assert.ok(built.objects.length>20);
    const buildingId=built.objects[0].properties.building_id;
    assert.ok(built.objects.every(o=>o.properties.building_id===buildingId));
    assert.deepEqual([...new Set(built.objects.map(o=>o.properties.level))].sort((a,b)=>a-b),[-2,-1,0,1]);
    await page.until(`socket.connected&&socket.io.engine.transport.name==='websocket'`,'actual WebSocket');
    const target=await page.call('Target.createTarget',{url:`${base}/lobbies/${lobby.id}/page`,background:true});
    const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
    const peer=await connect(tabs.find(t=>t.id===target.targetId).webSocketDebuggerUrl);
    try {
        await peer.call('Page.bringToFront');
        await peer.until('window.enterLocation&&window.socket?.connected&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','second game client');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');
            window.shellUpdates=[];socket.on('location_object_updated',d=>shellUpdates.push(d));
            socket.on('joined_location',d=>window.shellJoined=d);await enterLocation(${loc.id})})()`);
        await peer.until(`isLocationActive&&shellJoined?.location_id===${loc.id}`,'peer joined location room');
        await peer.until(`socket.io.engine.transport.name==='websocket'`,'peer WebSocket');
        await page.call('Page.bringToFront');
        await peer.evaluate(`window.shellEditorChanges=[];socket.on('location_editor_objects_changed',d=>shellEditorChanges.push(d));true`);
        const pointAt=(x,y,z)=>page.evaluate(`(()=>{const p=new T.Vector3(${x},${y},${z}).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
        const click=async p=>{await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...p});await pause(200);
            for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1,...p});};
        await page.evaluate(`L.setLocationHouseDrawMode(false);selectLocationEditorTool('select');
            locationControls.target.set(5,1,5);locationControls.object.position.set(12,12,14);locationControls.update();true`);
        const extra=await request(`${api}/locations/${loc.id}/objects`,{type:'floor',name:'Temporary level 2',tile_x:3,tile_y:3,
            properties:{level:2,building_id:buildingId,roof_enabled:false,dimensions:{width:2,depth:2,height:.12}}},token);
        for(const level of [-2,-1,0,1,2]){
            await page.evaluate(`L.setLocationViewLevel(${level});locationControls.target.set(3.2,${1+level*2.4},3.2);locationControls.object.position.set(12.2,${12+level*2.4},14.2);locationControls.update();
                document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();true`);await pause(200);
            const point=await pointAt(3.2,1+level*2.4,3.2);
            await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await pause(200);
            assert.deepEqual(await page.evaluate('L.getHoveredTileCoords()'),{x:3,z:3},`cursor on level ${level}`);
            await click(point);
            if(await page.evaluate('L.getSelectedLocationEditorAsset()?.level')!==level)console.log('PICK DIAGNOSTIC',await page.evaluate(`(()=>{const x=${point.x},y=${point.y},r=document.querySelector('#location-canvas canvas').getBoundingClientRect(),ray=new T.Raycaster();ray.setFromCamera(new T.Vector2((x-r.left)/r.width*2-1,-(y-r.top)/r.height*2+1),locationControls.object);return {element:document.elementFromPoint(x,y)?.outerHTML.slice(0,500),tool:locationEditorTool,selected:L.getSelectedLocationEditorAsset(),hits:ray.intersectObjects(shellRender.s.children,true).slice(0,20).map(h=>{let root=h.object;while(root.parent&&root.parent!==shellRender.s)root=root.parent;return {type:root.userData.locationObject?.type,level:root.userData.locationObject?.properties.level,visible:root.visible,childVisible:h.object.visible,opacity:h.object.material?.opacity,distance:h.distance}})}})()`));
            assert.equal(await page.evaluate('L.getSelectedLocationEditorAsset()?.level'),level,`selection isolated on level ${level}`);
        }
        await request(`${api}/locations/${loc.id}/objects/batch`,{object_ids:[extra.id],level:2},token,'DELETE');
        await page.evaluate('L.setLocationViewLevel(-2);true');await pause(200);
        assert.ok(await page.evaluate(`shellRender.s.children.some(m=>m.visible&&m.userData.locationObject?.type==='wall'&&m.userData.locationObject.properties.level===-1)`),'upper basement boundary retained');
        assert.ok(await page.evaluate(`shellRender.s.children.filter(m=>m.userData.locationObject?.type==='floor'&&m.userData.locationObject.properties.level===-1).every(m=>!m.visible)`),'upper slabs hidden');
        await page.evaluate('L.setLocationViewLevel(0);locationControls.target.set(3.2,1,3.2);locationControls.object.position.set(9,8,11);locationControls.update();true');await pause(200);
        assert.ok(await page.evaluate(`shellRender.s.children.some(m=>m.visible&&m.userData.locationObject?.type==='floor'&&m.userData.locationObject.properties.level===-1)`),'basement geometry remains behind the surface opening');
        await page.evaluate(`(()=>{for(const id of ['panel-tools','panel-characters']){const panel=document.getElementById(id);
            if(panel&&!panel.classList.contains('collapsed'))panel.querySelector('.toggle-btn')?.click()}return true})()`);
        await page.until(`['panel-tools','panel-characters'].every(id=>!document.getElementById(id)||document.getElementById(id).classList.contains('collapsed'))`,'инспекторы сцены свёрнуты перед выбором пола');
        const floorCandidates=await page.evaluate(`(async()=>{const {s,c}=shellRender,T=window.T,A=await import('/static/js/locationArchitecture.js');
            const objects=[],roots=[];s.traverse(node=>{if(node.userData?.locationObject){objects.push(node.userData.locationObject);
                if(node.userData.locationObject.type==='floor'&&Number(node.userData.locationObject.properties?.level||0)===0&&node.visible)roots.push(node)}});
            const canvas=document.querySelector('#location-canvas canvas'),rect=canvas.getBoundingClientRect(),out=[];
            for(const root of roots){const object=root.userData.locationObject,pose=A.floorVisualPose(object,objects),parts=A.floorSlabParts(object,objects),
                top=root.position.y+pose.height+.002,key='structure:'+root.userData.locationObjectId;
                for(const part of parts)for(const [u,v]of [[0,0],[-.3,0],[.3,0],[0,-.3],[0,.3],[-.2,-.2],[-.2,.2],[.2,-.2],[.2,.2]]){
                    const along=part.x+u*part.width,across=part.z+v*part.depth,world={x:pose.x+along*pose.axis.x+across*pose.normal.x,
                        y:top,z:pose.z+along*pose.axis.z+across*pose.normal.z},ndc=new T.Vector3(world.x,world.y,world.z).project(c);
                    if(ndc.x<-.95||ndc.x>.95||ndc.y<-.95||ndc.y>.95||ndc.z< -1||ndc.z>1)continue;
                    const x=rect.left+(ndc.x+1)*rect.width/2,y=rect.top+(1-ndc.y)*rect.height/2,target=document.elementFromPoint(x,y);
                    out.push({selectionKey:key,world,x,y,reachable:target===canvas||canvas.contains(target),target:target?.tagName||null,area:pose.width*pose.depth});
                }}
            return out.sort((a,b)=>b.area-a.area)})()`);
        const selectedFloorPoints=[];let floorSelectionKey=null;const triedFloorPoints=[];
        for(const candidate of floorCandidates.slice(0,60)){
            if(floorSelectionKey&&candidate.selectionKey!==floorSelectionKey)continue;
            if(!candidate.reachable){triedFloorPoints.push({selectionKey:candidate.selectionKey,screen:[candidate.x,candidate.y],target:candidate.target,selected:null});continue;}
            await page.evaluate(`(()=>{for(const id of ['panel-tools','panel-characters']){const panel=document.getElementById(id);
                if(panel&&!panel.classList.contains('collapsed'))panel.querySelector('.toggle-btn')?.click()}return true})()`);
            const liveReachable=await page.evaluate(`(()=>{const canvas=document.querySelector('#location-canvas canvas'),target=document.elementFromPoint(${candidate.x},${candidate.y});
                return target===canvas||canvas.contains(target)})()`);
            if(!liveReachable){triedFloorPoints.push({selectionKey:candidate.selectionKey,screen:[candidate.x,candidate.y],selected:null,reason:'точка перекрыта после подготовки кандидатов'});continue;}
            await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:candidate.x,y:candidate.y});
            for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{
                type,x:candidate.x,y:candidate.y,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
            await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
            const selected=await page.evaluate('L.getSelectedLocationEditorAsset()?.selectionKey||null');
            triedFloorPoints.push({selectionKey:candidate.selectionKey,screen:[candidate.x,candidate.y],selected});
            if(selected===candidate.selectionKey){if(floorSelectionKey&&floorSelectionKey!==selected)selectedFloorPoints.length=0;floorSelectionKey=selected;
                if(selectedFloorPoints.some(point=>Math.hypot(point.x-candidate.world.x,point.z-candidate.world.z)<.04))continue;
                selectedFloorPoints.push(candidate.world);if(selectedFloorPoints.length>=3)break;
            }else if(floorSelectionKey&&selected!==floorSelectionKey){floorSelectionKey=null;selectedFloorPoints.length=0;}
        }
        assert.ok(floorSelectionKey&&selectedFloorPoints.length>=3,
            `Три реальные точки клика выбирают один и тот же пол; попытки=${JSON.stringify(triedFloorPoints)}`);
        const readFloorDraw=(selectionKey,points)=>page.evaluate(`(async()=>{
            const {r,s,c}=shellRender,T=window.T,A=await import('/static/js/locationArchitecture.js');
            const id=String(${JSON.stringify(selectionKey)}).replace(/^structure:/,'');let root=null;const objects=[];
            s.traverse(node=>{if(node.userData?.locationObjectId!=null&&String(node.userData.locationObjectId)===id)root=node;
                if(node.userData?.locationObject)objects.push(node.userData.locationObject)});
            if(!root||root.userData.locationObject?.type!=='floor')return {error:'Выбранный пол не найден по selectionKey',id,selectionKey:${JSON.stringify(selectionKey)}};
            const floor=root.userData.locationObject,pose=A.floorVisualPose(floor,objects),points=${JSON.stringify(points)};
            if(L.getSelectedLocationEditorAsset()?.selectionKey!==${JSON.stringify(selectionKey)})
                return {error:'selection changed before draw sampling',id,selectionKey};
            // На время замера убираем только метки редактора, иначе подсветка клетки меняет RGB пола.
            const selectionOverlays=[];s.traverse(node=>{
                if(!node.isMesh||!node.material?.isMeshBasicMaterial)return;
                const color=node.material.color.getHex();
                const ring=node.geometry?.type==='RingGeometry'&&node.renderOrder===100&&[0x8be2cb,0xffb66e].includes(color);
                const hover=node.geometry?.type==='BoxGeometry'&&color===0xffaa00&&node.material.transparent&&node.material.opacity===.5;
                if(ring||hover){selectionOverlays.push([node,node.visible]);node.visible=false;}
            });
            const gl=r.getContext(),w=gl.drawingBufferWidth,h=gl.drawingBufferHeight,unique=new Map(),background=new Uint8Array(4);
            try{r.render(s,c);gl.finish();
                gl.readPixels(2,h-3,1,1,gl.RGBA,gl.UNSIGNED_BYTE,background);
                for(const point of points){const ndc=new T.Vector3(point.x,point.y,point.z).project(c);
                    if(ndc.x<-.95||ndc.x>.95||ndc.y<-.95||ndc.y>.95||ndc.z< -1||ndc.z>1)continue;
                    const px=Math.max(0,Math.min(w-1,Math.floor((ndc.x+1)*.5*w)));
                    const py=Math.max(0,Math.min(h-1,Math.floor((ndc.y+1)*.5*h))),pixel=new Uint8Array(4);
                    gl.readPixels(px,py,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);
                    unique.set(px+','+py,{x:px,y:py,rgb:[pixel[0],pixel[1],pixel[2]]});
                }
            }finally{selectionOverlays.forEach(([node,visible])=>node.visible=visible);r.render(s,c);}
            const samples=[...unique.values()],backgroundRgb=[background[0],background[1],background[2]];
            const lit=samples.filter(p=>p.rgb.reduce((sum,channel)=>sum+channel,0)>24
                &&Math.max(...p.rgb.map((channel,index)=>Math.abs(channel-backgroundRgb[index])))>15);
            return {id,samples,lit:lit.length,background:backgroundRgb,points:points.length,
                dimensions:{width:pose.width,depth:pose.depth},floorPosition:{x:pose.x,z:pose.z}};
        })()`);
        const colorBefore=await page.evaluate(`(()=>{const a=L.getSelectedLocationEditorAsset();window.colorAsset=a;
            if(!a?.selectionKey?.startsWith('structure:')||a.structureType!=='floor')throw Error('Ожидается выбранный объект пола');
            window.colorObjectId=a.selectionKey.slice('structure:'.length);return {color:a.color,selectionKey:a.selectionKey,x:a.x,z:a.z,rotation:a.rotation}})()`);
        assert.equal(colorBefore.selectionKey,floorSelectionKey,'реальные клики выбрали именно пол для RGB-сэмплов');
        const drawBefore=await readFloorDraw(colorBefore.selectionKey,selectedFloorPoints);
        assert.ok(!drawBefore.error&&drawBefore.samples.length===3&&drawBefore.lit>=3,
            `Выбран видимый не чёрный участок пола: ${JSON.stringify(drawBefore)}`);
        assert.equal(await page.evaluate(`L.updateSelectedLocationAssetDetails({...colorAsset,x:colorAsset.x+.25})`),true);
        assert.equal(await page.evaluate(`L.updateSelectedLocationAssetDetails(colorAsset)`),true);
        for(const offset of [.25,0]){
            const target=await page.evaluate(`L.beginMoveSelectedLocationAsset();document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();({x:colorAsset.x+${offset},z:colorAsset.z})`);
            await click(await pointAt(target.x,1,target.z));
            await page.until(`Math.abs(L.getSelectedLocationEditorAsset().x-${target.x})<.01`,'mouse transform persisted');
        }
        for(const [code,key] of [['KeyQ','q'],['KeyE','e']]){
            await page.call('Input.dispatchKeyEvent',{type:'keyDown',code,key});await page.call('Input.dispatchKeyEvent',{type:'keyUp',code,key});await pause(500);
        }
        // Q/E остаются реальными проверяемыми поворотами. Их шаг зависит от камеры,
        // поэтому пара нажатий не обязана взаимно компенсироваться; перед RGB-сравнением
        // возвращаем записанную исходную позу через тот же редактор свойств.
        const originalPose={x:colorBefore.x,z:colorBefore.z,rotation:colorBefore.rotation};
        assert.equal(await page.evaluate(`L.updateSelectedLocationAssetDetails(${JSON.stringify(originalPose)})`),true,
            'Исходная поза пола принята редактором после реальных Q/E');
        await page.until(`(()=>{const a=L.getSelectedLocationEditorAsset(),b=${JSON.stringify(colorBefore)};return a?.selectionKey===b.selectionKey
            &&Math.abs(a.x-b.x)<.01&&Math.abs(a.z-b.z)<.01
            &&Math.abs((((a.rotation-b.rotation+540)%360)-180))<.01})()`,'Исходная поза пола восстановлена после Q/E');
        const colorAfter=await page.evaluate(`(()=>{const a=L.getSelectedLocationEditorAsset();return {color:a?.color,x:a?.x,z:a?.z,rotation:a?.rotation}})()`);
        assert.equal(colorAfter.color,colorBefore.color,'Перемещения и Q/E сохраняют цвет в данных объекта');
        assert.ok(Math.abs(colorAfter.x-colorBefore.x)<.01&&Math.abs(colorAfter.z-colorBefore.z)<.01
            &&Math.abs((((colorAfter.rotation-colorBefore.rotation+540)%360)-180))<.01,
            `Перед сравнением пикселей пол возвращён в исходное положение и угол: ${JSON.stringify({before:colorBefore,after:colorAfter})}`);
        const drawAfter=await readFloorDraw(colorBefore.selectionKey,selectedFloorPoints);
        assert.ok(!drawAfter.error&&drawAfter.samples.length===3&&drawAfter.lit>=3,
            `После перемещений видимый участок пола не чёрный: ${JSON.stringify(drawAfter)}`);
        const rgbDelta=drawBefore.samples.map((before,index)=>before.rgb.map((channel,component)=>
            Math.abs(channel-(drawAfter.samples[index]?.rgb[component]??255))));
        assert.ok(drawBefore.samples.length===drawAfter.samples.length
            &&rgbDelta.every(pixel=>pixel.every(channel=>channel<=8)),
            `Разница отрисованного цвета после возврата не превышает 8/255 на канал: ${JSON.stringify({drawBefore,drawAfter,rgbDelta})}`);
        assert.ok(await page.evaluate(`shellRender.s.children.filter(m=>m.userData.locationObject?.type==='roof').every(m=>m.children.every(c=>!c.isMesh||!c.visible))`),'shell roofs follow ordinary camera cutaway');
        // Возвращаем исходную камеру перед остальными проверками рисования и движения.
        await page.evaluate(`locationControls.target.set(8,1,8);locationControls.object.position.set(17,18,22);locationControls.update();true`);
        const floor=built.objects.find(o=>o.type==='floor'&&o.properties.level===0);
        await request(`${api}/locations/objects/${floor.id}`,{properties:{color:'#ac9872'}},token,'PATCH');
        await peer.until(`shellUpdates.some(d=>JSON.stringify(d).includes('#ac9872'))`,'manual edit delivered to second client');
        await page.evaluate(`L.setLocationViewLevel(-1);true`);await pause(500);
        await page.evaluate(`L.setLocationViewLevel(1);true`);await pause(500);
        await page.evaluate(`L.setLocationViewLevel(0);true`);
        // Repeating at the same site must preserve every object and the manual edit.
        await draw();
        await page.evaluate(`document.querySelector('[data-shell-preview]').click();true`);
        await page.until(`document.querySelector('dialog[open] [data-shell-create]')?.disabled===false`,
            'Предпросмотр повторной постройки готов');
        await page.evaluate(`document.querySelector('dialog[open] [data-shell-create]').click();true`);
        await page.until(`document.body.innerText.includes('На этом месте уже есть')`,'overlap refused');
        const after=await detail();assert.equal(after.objects.length,built.objects.length);
        assert.equal(after.objects.find(o=>o.id===floor.id).properties.color,'#ac9872');
        const actor=await request(`${api}/characters`,{name:'Shell climber',data:{}},token);
        const g=built.objects[0].properties.building_generation;
        const ladder=after.objects.find(object=>object.type==='ladder'&&object.properties.level===-1);
        const stairs=after.objects.find(object=>object.type==='stairs'&&object.properties.level===0);
        assert.ok(ladder&&stairs,'Постройка содержит лестницы из подвала и на верхний этаж');
        const center=object=>({x:object.tile_x+(object.properties.offset_x||0),y:object.tile_y+(object.properties.offset_z||0)});
        const ladderCell=center(ladder);
        const hatch=after.objects.find(object=>object.type==='hatch'&&object.properties.level===0&&
            center(object).x===ladderCell.x&&center(object).y===ladderCell.y);
        assert.ok(hatch&&!hatch.properties.is_open,'Люк над подвальной лестницей изначально закрыт');
        const spawn=(cell,force=false)=>request(`${api}/locations/${loc.id}/spawn_character`,{
            character_id:actor.id,tile_x:cell.x,tile_y:cell.y,floor_level:0,force_relocate:force},token);
        await spawn(ladderCell);
        await page.evaluate(`window.shellMoves=[];window.shellRejects=[];
            socket.on('character_moved',event=>{if(event.character_id===${actor.id})shellMoves.push(event)});
            socket.on('movement_rejected',event=>{if(event.character_id===${actor.id})shellRejects.push(event)});
            L.setLocationEditMode(false);true`);
        const move=async(x,y,level,expectedRejections=[])=>{
            await page.evaluate(`shellMoves.length=0;shellRejects.length=0;
                socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${loc.id},character_id:${actor.id},x:${x},y:${y},floor_level:${level}});true`);
            await page.until(`shellRejects.length>0||shellMoves.some(event=>event.x===${x}&&event.y===${y}&&event.floor_level===${level})`,
                `Перемещение по лестнице в (${x}, ${y}), этаж ${level}`);
            assert.deepEqual(await page.evaluate('shellRejects.map(event=>event.message)'),expectedRejections,
                'Сервер вернул ожидаемый результат перемещения');
            await pause(600);
        };
        await move(ladderCell.x,ladderCell.y,-1,['Path is blocked']);
        await page.evaluate(`window.currentLocationCharacterId=${actor.id};
            L.showObjectContextMenu(650,350,${JSON.stringify(hatch)});true`);
        await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Открыть люк')",
            'Люк можно открыть из контекстного меню');
        await page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Открыть люк').click();true");
        await page.until(`L.sceneGraphicsForTest().roots.some(root=>root.userData.locationObjectId===${hatch.id}
            &&root.userData.locationObject?.properties?.is_open)`,'Открытый люк появился в сцене');
        await move(ladderCell.x,ladderCell.y,-1);
        await move(ladderCell.x,ladderCell.y,0);
        const stairCenter=center(stairs),angle=stairs.properties.rotation||0;
        const beyondEnd=stairs.properties.dimensions.depth/2+.5;
        const landing=sign=>({x:Math.round(stairCenter.x+sign*Math.sin(angle)*beyondEnd),
            y:Math.round(stairCenter.y+sign*Math.cos(angle)*beyondEnd)});
        await spawn(landing(-1),true);
        const top=landing(1);
        await move(top.x,top.y,1);
        const retained=await detail();
        if(process.env.VTT_SHELL_INSPECT==='1'){
            await page.evaluate(`L.setLocationViewLevel(-1);true`);await pause(800);
            fs.writeFileSync(path.join(safeSession?.artifacts || os.tmpdir(),'vtt-shell-basement.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
            await page.evaluate(`L.setLocationViewLevel(1);locationControls.object.position.set(${g.minX+8},12,${g.minZ+10});locationControls.target.set(${g.minX+4},3.4,${g.minZ+4});locationControls.update();true`);await pause(800);
            fs.writeFileSync(path.join(safeSession?.artifacts || os.tmpdir(),'vtt-shell-created.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        }
        await page.evaluate('window.shellReloadMarker=true;true');
        await page.call('Page.reload');
        await page.until('!window.shellReloadMarker&&window.enterLocation&&window.socket?.connected&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','reload');
        await page.evaluate(`(async()=>{window.T=await import('three');const old=T.Scene.prototype.add;T.Scene.prototype.add=function(...roots){for(const root of roots)root.traverse(m=>{if(!m.isMesh)return;const before=m.onBeforeRender;m.onBeforeRender=function(r,s,c,...args){before.call(this,r,s,c,...args);if(window.isLocationActive)window.shellRender={r,s,c}}});return old.apply(this,roots)};await enterLocation(${loc.id})})()`);await page.until('!!(window.isLocationActive&&window.shellRender)','room re-entry');
        assert.deepEqual((await detail()).objects.map(o=>o.id).sort(),retained.objects.map(o=>o.id).sort());
        // A second, free area previews a different shape, wealth and ruin geometry.
        await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');L.setLocationEditMode(true);selectLocationEditorTool('assets');
            const T=await import('three');
            locationControls.target.set(17,1,17);locationControls.object.position.set(26,18,31);locationControls.update();
            document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();
            document.querySelector('#panel-characters:not(.collapsed) .toggle-btn')?.click();window.T=T})()`);
        await pause(700);
        await draw(13.5,22.5);
        await page.evaluate(`(()=>{const f=document.querySelector('dialog form');f.elements.shape.value='C';f.elements.width.value=10;f.elements.depth.value=10;f.elements.wealth.value='poor';f.elements.condition.value='ruins';f.elements.shape.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[data-shell-preview]').click()})()`);
        if(process.env.VTT_SHELL_INSPECT==='1'){
            await pause(700);
            fs.writeFileSync(path.join(safeSession?.artifacts || os.tmpdir(),'vtt-shell-ruins.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        }
        await page.evaluate(`document.querySelector('dialog').close();true`);
        assert.equal((await detail()).objects.length,after.objects.length,'cancelled ruin preview persists nothing');
        await page.evaluate(`window.fixChanges=[];socket.on('location_editor_objects_changed',d=>fixChanges.push(d));L.setLocationViewLevel(-2);selectLocationEditorTool('erase');document.getElementById('loc-erase-structures').checked=true;L.setLocationBrushRadius(0);L.setLocationEraserMode(true);
            locationControls.target.set(3.2,-3.8,3.2);locationControls.object.position.set(12.2,7.2,14.2);locationControls.update();document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();true`);
        const beforeErase=await detail();
        await click(await pointAt(3.2,-3.8,3.2));
        await page.until('fixChanges.some(d=>d.deleted_ids?.length)','Ластик отправил пакет удаления');
        await pause(500);
        const erased=await detail();
        assert.ok(erased.objects.length<beforeErase.objects.length);
        const erasedIds=new Set(erased.objects.map(o=>o.id));
        const removed=beforeErase.objects.filter(o=>!erasedIds.has(o.id));
        assert.ok(removed.every(o=>o.properties.level===-2),'Ластик сохраняет остальные этажи');
        await peer.until(`shellEditorChanges.some(change=>JSON.stringify([...(change.deleted_ids||[])].sort())===
            ${JSON.stringify(JSON.stringify(removed.map(o=>o.id).sort()))})`,'Второй клиент получил тот же пакет удаления');
        await page.evaluate(`window.confirm=()=>true;L.setLocationEraserMode(false);window.dispatchEvent(new CustomEvent('location-editor-active-building',{detail:{buildingId:${JSON.stringify(buildingId)},level:-2}}));document.querySelector('[data-floor-delete]').click();true`);
        await pause(500);
        assert.ok((await detail()).objects.every(o=>o.properties.level!==-2),'floor delete removes one basement');
        assert.equal(await page.evaluate('L.deleteSelectedLocationBuilding(true)'),false,'occupied building deletion is rejected');
        await request(`${api}/locations/${loc.id}/characters/${actor.id}`,null,token,'DELETE');
        await page.evaluate(`document.querySelector('[data-building-delete]').click();true`);
        await pause(500);
        assert.equal((await detail()).objects.length,0,'whole building deleted in one operation');
        await peer.until(`L.getLocationBuildingIds().length===0`,'whole deletion synchronized to peer');
        const emptyAgain=await detail();
        assert.deepEqual(emptyAgain.tiles_data,built.tiles_data,'deletion preserves saved terrain');
        for(const kind of ['flat','shed','gable']){
            const id=`acceptance-${kind}`;
            const count=await page.evaluate(`(async()=>{const G=await import('/static/js/locationHouseGenerator.js'),A=await import('/static/js/api.js');
                const plan=G.planBuildingShell({minX:2,minZ:2,width:10,depth:10,shape:'L',storeys:2,basements:0,roofType:'${kind}',condition:'damaged',buildingId:'${id}',seed:'shell',groundHeight:()=>1});
                if(plan.error)throw Error(plan.error);const change=await A.Server.changeLocationObjects(${lobby.id},${loc.id},{objects:plan.objects});L.applyLocationObjectsChange(change);
                L.setLocationViewLevel(0);L.setLocationEditMode(true);locationControls.target.set(5,1,5);locationControls.object.position.set(9,8,11);locationControls.update();return plan.objects.filter(o=>o.type==='roof').length})()`);
            assert.ok(count>0);
            await pause(300);
            assert.ok(await page.evaluate(`shellRender.s.children.filter(m=>m.userData.locationObject?.type==='roof').every(m=>m.children.every(c=>!c.isMesh||!c.visible))`),`${kind} roof cutaway in game`);
            await page.evaluate(`window.dispatchEvent(new CustomEvent('location-editor-active-building',{detail:{buildingId:'${id}',level:0}}));true`);
            assert.equal(await page.evaluate('L.deleteSelectedLocationBuilding(true)'),true);
        }
        console.log('BUILDING FIX PASS',JSON.stringify({pointerLevels:[-2,-1,0,1,2],floorIsolation:true,color:true,roofCutaway:true,atomicDelete:true,occupiedDeleteBlocked:true}));
        console.log('BUILDING SHELL PASS',JSON.stringify({objects:built.objects.length,levels:[-2,-1,0,1],transport:'websocket',preview:true,manualEdit:true,reload:true,overlapPreserved:true,characterMovement:true}));
    } finally {peer.close();await page.call('Target.closeTarget',{targetId:target.targetId});}
};

async function runInterior({page,request,token,lobby,loc}) {
    const url=`/lobbies/${lobby.id}/locations/${loc.id}`;
    const detail=()=>request(url,null,token,'GET');
    const specs=await page.evaluate(`(async()=>{const H=await import('/static/js/locationHouseGenerator.js');
        return H.planBuildingShell({minX:2,minZ:2,width:12,depth:12,shape:'L',storeys:2,basements:1,
            wealth:'rich',condition:'intact',buildingId:'interior',roofType:'gable',seed:'test',groundHeight:()=>1}).objects})()`);
    const shell=await request(url+'/objects/batch',{objects:specs},token);
    await page.until(`L.getLocationBuildingIds().includes('interior')`,'building received');
    const open=async level=>{
        await page.evaluate(`L.setLocationViewLevel(${level});window.dispatchEvent(new CustomEvent('location-editor-active-building',{detail:{buildingId:'interior',level:${level}}}));document.getElementById('loc-generate-interior').click();true`);
        await page.until('!!document.querySelector("[data-layout-preview]")','layout dialog');
        await page.evaluate(`document.querySelector('[data-layout-preview]').click();true`);
        assert.equal(await page.evaluate(`document.querySelector('[data-layout-create]').disabled`),false);
    };
    const save=async()=>{
        // Schedule the submit so the protocol can accept its native confirmation.
        page.events.delete('Page.javascriptDialogOpening');
        await page.evaluate(`setTimeout(()=>document.querySelector('dialog form').requestSubmit(document.querySelector('[data-layout-create]')),0);true`);
        for(let i=0;i<100&&!page.events.has('Page.javascriptDialogOpening');i++)await pause(50);
        assert.ok(page.events.get('Page.javascriptDialogOpening')?.message.includes('Остальные объекты сохраняются'));
        await page.call('Page.handleJavaScriptDialog',{accept:true});
        await page.until(`!document.getElementById('loc-generate-interior').disabled`,'layout batch saved');
    };
    await open(0);
    assert.equal((await detail()).objects.length,shell.objects.length,'preview does not persist');
    await page.evaluate(`document.querySelector('dialog').close();true`);
    assert.equal((await detail()).objects.length,shell.objects.length,'cancel preserves scene');
    await open(0);await save();
    const first=await detail();
    const layout=first.objects.filter(o=>o.properties.layout_generation);
    assert.ok(layout.length>0);
    assert.ok(shell.objects.every(o=>first.objects.some(p=>p.id===o.id)));
    const manual=await request(url+'/objects',{type:'wall',name:'Manual partition',tile_x:12,tile_y:4,
        properties:{building_id:'interior',level:0,dimensions:{width:1,depth:.14,height:2.4}}},token);
    await open(1);await save();
    const upper=(await detail()).objects.filter(o=>o.properties.layout_generation&&o.properties.level===1);
    await open(-1);await save();
    await open(0);
    await page.evaluate(`document.querySelector('dialog form').elements.seed.value='another';document.querySelector('dialog form').dispatchEvent(new Event('input'));document.querySelector('[data-layout-preview]').click();true`);
    await save();
    const regenerated=await detail();
    assert.ok(regenerated.objects.some(o=>o.id===manual.id));
    assert.ok(upper.every(o=>regenerated.objects.some(p=>p.id===o.id)));
    assert.ok(layout.every(o=>!regenerated.objects.some(p=>p.id===o.id)));
    for(const level of [0,1,-1])for(const camera of [[10,12,13],[4,10,13]]) {
        await page.evaluate(`L.setLocationEditMode(false);L.setLocationViewLevel(${level});locationControls.target.set(5,${1+level*2.4},5);locationControls.object.position.set(${camera[0]},${camera[1]+level*2.4},${camera[2]});locationControls.update();true`);
        await pause(250);
        const file=path.join(safeSession?.artifacts || os.tmpdir(),`vtt-interior-${level}-${camera[0]}.png`);
        fs.writeFileSync(file,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('INTERIOR FRAME',file);
    }
    await page.evaluate(`(async()=>{exitLocation();await enterLocation(${loc.id});return true})()`);
    assert.deepEqual((await detail()).objects.map(o=>o.id).sort((a,b)=>a-b),regenerated.objects.map(o=>o.id).sort((a,b)=>a-b));
    console.log('INTERIOR PASS',JSON.stringify({shell:shell.objects.length,layout:layout.length,upper:upper.length,total:regenerated.objects.length}));
}

async function runFloorFix2({page,request,token,lobby,loc}) {
    const url=`/lobbies/${lobby.id}/locations/${loc.id}`;
    const specs=[];
    for(const level of [-2,-1,0,1,2])specs.push({name:`Floor ${level}`,type:'floor',tile_x:8,tile_y:8,
        properties:{building_id:'fix2',level,roof_enabled:false,dimensions:{width:10,depth:10,height:.12}}});
    specs.push({name:'Neighbor floor',type:'floor',tile_x:19,tile_y:19,properties:{building_id:'neighbor',level:3,roof_enabled:false,dimensions:{width:2,depth:2,height:.12}}});
    const saved=(await request(url+'/objects/batch',{objects:specs},token)).objects;
    await page.evaluate(`L.setLocationEditMode(false);locationControls.target.set(8,1,8);locationControls.object.position.set(18,15,22);locationControls.update();true`);
    const aim=(x,y,z)=>page.evaluate(`(()=>{const p=new T.Vector3(${x},${y},${z}).project(locationControls.object),r=shellRender.r.domElement.getBoundingClientRect();return{x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
    const click=async point=>{await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await pause(120);for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1,...point});};
    for(const level of [-2,-1,0,1,2]) {
        await page.evaluate(`L.setLocationViewLevel(${level});true`);await pause(200);
        const point=await aim(7.98,1.035+level*2.4,8.02);
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await pause(150);
        const actual=await page.evaluate('L.getHoveredTileCoords()');
        console.log('FIX2 POINTER',JSON.stringify({level,actual,expected:{x:7,z:8}}));
        assert.deepEqual(actual,{x:7,z:8},`normal game cursor at level ${level}`);
        const inside=await page.evaluate(`(()=>{const h=shellRender.s.children.find(m=>m.isMesh&&m.material?.color?.getHex()===0xffaa00&&m.geometry?.parameters?.width===1),r=shellRender.r.domElement.getBoundingClientRect(),ray=new T.Raycaster();ray.setFromCamera(new T.Vector2((${point.x}-r.left)/r.width*2-1,-(${point.y}-r.top)/r.height*2+1),locationControls.object);const p=ray.ray.intersectPlane(new T.Plane(new T.Vector3(0,1,0),-h.position.y),new T.Vector3());return {inside:Math.abs(p.x-h.position.x)<=.5&&Math.abs(p.z-h.position.z)<=.5,x:h.position.x,z:h.position.z,y:h.position.y}})()`);
        assert.equal(inside.inside,true,`cursor lies inside visible yellow cell on level ${level}: ${JSON.stringify(inside)}`);
        assert.equal(inside.x,7.5);assert.equal(inside.z,8.5);
        assert.ok(await page.evaluate(`document.getElementById('location-tile-info').innerText.includes('(7, 8)')`));
        await page.evaluate(`L.setLocationEditMode(true);selectLocationEditorTool('assets');L.setLocationAssetPreset('wall');window.dispatchEvent(new CustomEvent('location-editor-active-building',{detail:{buildingId:'fix2',level:${level}}}));document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();true`);
        await click(point);await pause(200);
        const wall=(await request(url,null,token,'GET')).objects.find(o=>o.type==='wall');
        assert.ok(wall,`actual placement on level ${level}`);
        assert.deepEqual([wall.tile_x,wall.tile_y,wall.properties.level],[7,8,level]);
        assert.deepEqual(await page.evaluate('L.getHoveredTileCoords()'),{x:7,z:8});
        await request(url+'/objects/batch',{object_ids:[wall.id],level},token,'DELETE');
        await page.evaluate(`selectLocationEditorTool('select');document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();true`);
        await click(point);
        assert.equal(await page.evaluate('L.getSelectedLocationEditorAsset()?.level'),level,'selection uses the same floor surface');
        await page.evaluate('L.setLocationEditMode(false);true');
    }
    // Repeat with a lower, rotated ordinary game camera and an off-center cell.
    await page.evaluate('locationControls.target.set(8,1,8);locationControls.object.position.set(19,10,18);locationControls.update();true');
    for(const level of [-2,-1,0,1,2]){
        await page.evaluate(`L.setLocationViewLevel(${level});true`);await pause(150);
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...await aim(8.8,1.035+level*2.4,7.2)});await pause(150);
        assert.deepEqual(await page.evaluate('L.getHoveredTileCoords()'),{x:8,z:7},`rotated camera on level ${level}`);
    }
    await page.evaluate(`window.fix2Changes=[];socket.on('location_objects_changed',d=>fix2Changes.push(d));window.confirmCount=0;window.confirm=()=>{confirmCount++;return true};L.setLocationEditMode(true);selectLocationEditorTool('assets');L.clearLocationAssetPreset();true`);
    for(const level of [-2,-1,0,1,2]){
        await page.evaluate(`L.setLocationViewLevel(${level});window.dispatchEvent(new CustomEvent('location-editor-active-building',{detail:{buildingId:'fix2',level:${level}}}));true`);
        const blocked=[-1,0,1].includes(level);
        assert.equal(await page.evaluate('document.querySelector("[data-floor-delete]").disabled'),blocked,`delete button for level ${level}`);
        if(blocked){
            assert.ok(await page.evaluate('document.querySelector("[data-floor-delete-reason]").innerText.length>0'));
            await page.evaluate('document.querySelector("[data-floor-delete]").click();true');
            assert.equal(await page.evaluate('L.deleteSelectedLocationBuilding(false)'),false);
            const status=await page.evaluate(`(async()=>{const r=await fetch('${url}/objects/batch',{method:'DELETE',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('access_token')},body:JSON.stringify({building_id:'fix2',level:${level}})});const body=await r.json();return {status:r.status,error:body.error}})()`);
            assert.equal(status.status,409);assert.ok(status.error);
        }
    }
    assert.equal(await page.evaluate('confirmCount'),0,'blocked actions never ask for confirmation');
    assert.deepEqual((await request(url,null,token,'GET')).objects.map(o=>o.id),saved.map(o=>o.id),'blocked deletion keeps all data');
    for(const level of [2,-2]){
        await page.evaluate(`L.setLocationViewLevel(${level});window.dispatchEvent(new CustomEvent('location-editor-active-building',{detail:{buildingId:'fix2',level:${level}}}));document.querySelector('[data-floor-delete]').click();true`);
        const id=saved.find(o=>o.properties.building_id==='fix2'&&o.properties.level===level).id;
        await page.until(`fix2Changes.some(c=>c.deleted_ids.includes(${id}))`,'allowed outer storey deleted');
        const remaining=(await request(url,null,token,'GET')).objects;
        assert.ok(remaining.every(o=>o.id!==id));
        assert.ok(remaining.some(o=>o.properties.level===0&&o.properties.building_id==='fix2'));
    }
    for(const level of [1,-1]){
        await page.evaluate(`L.setLocationViewLevel(${level});window.dispatchEvent(new CustomEvent('location-editor-active-building',{detail:{buildingId:'fix2',level:${level}}}));true`);
        assert.equal(await page.evaluate('document.querySelector("[data-floor-delete]").disabled'),false,'new outermost floor becomes available');
    }
    assert.equal(await page.evaluate('confirmCount'),2);
    assert.equal(await page.evaluate('L.deleteSelectedLocationBuilding(true)'),true,'base level may be removed with its whole building');
    const final=(await request(url,null,token,'GET')).objects;
    assert.ok(final.every(o=>o.properties.building_id==='neighbor'));
    console.log('BUILDING FIX2 PASS',JSON.stringify({levels:[-2,-1,0,1,2],cameras:2,hover:true,highlight:true,coordinates:true,placement:true,floorDeletePolicy:true}));
}

async function runApertureRegression({page,request,token,lobby,loc}) {
    const url=`/lobbies/${lobby.id}/locations/${loc.id}`;
    const objects=await page.evaluate(`(async()=>{const G=await import('/static/js/locationHouseGenerator.js');return G.planBuildingShell({minX:2,minZ:2,width:10,depth:10,shape:'L',storeys:2,basements:2,wealth:'ordinary',condition:'intact',roofType:'gable',buildingId:'aperture',seed:'joint',groundHeight:()=>1}).objects})()`);
    const saved=(await request(url+'/objects/batch',{objects},token)).objects;
    await page.evaluate(`L.setLocationEditMode(false);L.setLocationViewLevel(0);locationControls.target.set(4.5,1,4.5);locationControls.object.position.set(10,8,12);locationControls.update();true`);
    await pause(700);
    const below=await page.evaluate(`shellRender.s.children.filter(m=>m.userData.locationObject?.properties.level<0).map(m=>({id:m.userData.locationObjectId,visible:m.visible}))`);
    assert.ok(below.length&&below.every(m=>m.visible),'basement meshes are available behind the opening');
    for(const [x,y,z] of [[6.5,7,7.5],[2,7,7]]) {
        await page.evaluate(`locationControls.object.position.set(${x},${y},${z});locationControls.update();true`);await pause(300);
        const colors=await page.evaluate(`(()=>{const {r,s,c}=shellRender,gl=r.getContext(),size=r.getDrawingBufferSize(new T.Vector2());
            const opening=s.children.find(m=>m.userData.locationObject?.type==='floor_opening'&&m.userData.locationObject.properties.level===0);
            const q=new T.Vector3(opening.position.x,1.035,opening.position.z).project(c);
            const px=Math.floor((q.x+1)*size.x/2),py=Math.floor((q.y+1)*size.y/2),pixel=()=>{r.render(s,c);const a=new Uint8Array(4);gl.readPixels(px,py,1,1,gl.RGBA,gl.UNSIGNED_BYTE,a);return Array.from(a)};
            const visible=pixel(),basement=s.children.filter(m=>m.userData.locationObject?.properties.level<0),states=basement.map(m=>m.visible);
            basement.forEach(m=>m.visible=false);const hidden=pixel();basement.forEach((m,i)=>m.visible=states[i]);r.render(s,c);return {visible,hidden}})()`);
        console.log('APERTURE PIXEL',JSON.stringify({camera:[x,y,z],...colors}));
        if(JSON.stringify(colors.visible)===JSON.stringify(colors.hidden))console.log('APERTURE RAYS',await page.evaluate(`(()=>{const {r,s,c}=shellRender,opening=s.children.find(m=>m.userData.locationObject?.type==='floor_opening'&&m.userData.locationObject.properties.level===0),q=new T.Vector3(opening.position.x,1.035,opening.position.z).project(c),ray=new T.Raycaster();ray.setFromCamera(new T.Vector2(q.x,q.y),c);return ray.intersectObjects(s.children,true).slice(0,20).map(h=>{let root=h.object;while(root.parent&&root.parent!==s)root=root.parent;return {rootType:root.userData.locationObject?.type,level:root.userData.locationObject?.properties.level,rootVisible:root.visible,visible:h.object.visible,opacity:h.object.material?.opacity,distance:h.distance,point:h.point.toArray(),material:h.object.material?.type}})})()`));
        assert.notDeepEqual(colors.visible,colors.hidden,'rendered opening contains basement geometry rather than an empty background');
    }
    const seam=await page.evaluate(`(()=>{const floors=shellRender.s.children.filter(m=>m.userData.locationObject?.type==='floor'&&m.userData.locationObject.properties.level===0);
        return [3.2,3.5,4,4.5,5].flatMap(x=>[5.99,6,6.01].map(z=>{const ray=new T.Raycaster(new T.Vector3(x,2,z),new T.Vector3(0,-1,0));return {x,z,hits:ray.intersectObjects(floors,true).length}}))})()`);
    assert.ok(seam.every(p=>p.hits>0),'rendered floor meshes close the entire L-wing seam');
    await page.evaluate(`L.setLocationEditMode(true);selectLocationEditorTool('select');document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();locationControls.target.set(3.2,1,3.2);locationControls.object.position.set(9,8,11);locationControls.update();true`);
    await pause(300);
    const aim=async(x,y,z)=>page.evaluate(`(()=>{const p=new T.Vector3(${x},${y},${z}).project(locationControls.object),r=shellRender.r.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
    const point=await aim(3.2,1.035,3.2);
    await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await pause(200);
    const highlightY=await page.evaluate(`shellRender.s.children.find(m=>m.isMesh&&m.material?.color?.getHex()===0xffaa00&&m.geometry?.parameters?.width===1).position.y`);
    assert.ok(Math.abs(highlightY-1.031)<1e-6,`floor zero highlight returned to its previous height, actual=${highlightY}, tile=${JSON.stringify(await page.evaluate('L.getHoveredTileCoords()'))}`);
    for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1,...point});
    assert.equal(await page.evaluate('L.getSelectedLocationEditorAsset()?.level'),0,'basement geometry cannot steal the surface selection');
    const reloaded=await request(url,null,token,'GET');
    assert.deepEqual(reloaded.objects.map(o=>o.id),saved.map(o=>o.id),'view fixes never change saved objects');
    console.log('APERTURE AND L-FLOOR PASS',JSON.stringify({cameras:2,basementPixels:true,seamSamples:seam.length,highlightZeroRestored:true,activeFloorSelection:true}));
}
