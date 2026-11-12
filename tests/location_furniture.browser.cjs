// VTT_FURNITURE=1 VTT_BROWSER_GPU=1 node tests/world_object_editor.browser.cjs
// Add VTT_BLD_STRESS=1 VTT_BLD_CYCLES=20 to sample both clients' retained heap.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
module.exports=async({page,request,token,lobby,base,connect,debugPort})=>{
    const stress=process.env.VTT_BLD_STRESS==='1',baseline=process.env.VTT_BLD_BASELINE==='1';
    const cycles=Number(process.env.VTT_BLD_CYCLES||3);
    assert.ok(Number.isInteger(cycles)&&cycles>=3&&cycles<=100,'VTT_BLD_CYCLES must be an integer from 3 to 100');
    const target=await page.call('Target.createTarget',{url:`${base}/lobbies/${lobby.id}/page`,background:true});
    const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
    const peer=await connect(tabs.find(t=>t.id===target.targetId).webSocketDebuggerUrl);
    const instrument=async p=>{
        await p.until('window.enterLocation&&window.socket?.connected','game client ready');
        await p.evaluate(`(async()=>{
            window.T=await import('three');window.L=await import('/static/js/locationScene.js');window.F=await import('/static/js/locationFurnitureGenerator.js');
            const add=T.Scene.prototype.add;T.Scene.prototype.add=function(...roots){for(const root of roots)root.traverse(m=>{if(!m.isMesh)return;
                const before=m.onBeforeRender;m.onBeforeRender=function(r,s,c,...args){before.call(this,r,s,c,...args);if(window.isLocationActive)window.furnitureRender={r,s,c}}});return add.apply(this,roots)};
            window.furnitureEvents=[];socket.on('location_objects_changed',d=>furnitureEvents.push(d));
            socket.on('joined_location',d=>window.furnitureJoined=d);
        })()`);
    };
    const rootIds=p=>p.evaluate(`furnitureRender.s.children.filter(m=>m.userData.locationObject?.properties?.furniture_generation).map(m=>m.userData.locationObjectId).sort((a,b)=>a-b)`);
    try {
        await instrument(page);await instrument(peer);
        if(process.env.VTT_BLD_TRANSITIONS){
            await require('./location_lifecycle.browser.cjs')({page,peer,request,token,lobby});
            if(process.env.VTT_BLD_LIFECYCLE_DIAGNOSTIC==='1'||process.env.VTT_BLD_LIFECYCLE_ONLY==='1')return;
        }
        await page.call('Page.bringToFront');
        const cases=stress?[{shape:'rectangle',wealth:'rich',condition:'intact'},{shape:'L',wealth:'rich',condition:'damaged'},
            {shape:'U',wealth:'poor',condition:'ruins'},{shape:'C',wealth:'poor',condition:'intact'}]
            :[{shape:'rectangle',wealth:'rich',condition:'intact'},{shape:'L',wealth:'ordinary',condition:'damaged'},{shape:'U',wealth:'poor',condition:'ruins'}];
        for(const [index,spec] of cases.entries()) {
            const buildingId=`furniture-${index}`;
            const loc=await request(`/lobbies/${lobby.id}/locations`,{name:`Furniture ${spec.shape} ${spec.condition}`,world_tile_x:15+index,world_tile_z:15,
                grid_width:stress?36:24,grid_height:stress?36:24,tiles_data:Array.from({length:stress?36:24},()=>Array.from({length:stress?36:24},()=>({terrain:stress?'grass':'bare_dirt',height:1,objects:[]})))},token);
            const url=`/lobbies/${lobby.id}/locations/${loc.id}`,detail=()=>request(url,null,token,'GET');
            const architecture=await page.evaluate(`(async()=>{const H=await import('/static/js/locationHouseGenerator.js'),I=await import('/static/js/locationInteriorGenerator.js');
                const shell=H.planBuildingShell({minX:2,minZ:2,width:${stress?16:12},depth:${stress?16:12},storeys:${stress?4:2},basements:${stress?2:1},buildingId:'${buildingId}',roofType:'gable',seed:'furniture',groundHeight:()=>1,...${JSON.stringify(spec)}}).objects;
                for(const level of ${stress?'[-2,-1,0,1,2,3]':'[-1,0,1]'})shell.push(...I.planBuildingInterior(shell,{buildingId:'${buildingId}',level,seed:'furniture'}).objects);return shell})()`);
            const original=(await request(url+'/objects/batch',{objects:architecture},token)).objects;
            for(const p of [page,peer]){
                await p.call('Page.bringToFront');
                await p.evaluate(`(async()=>{if(window.isLocationActive)exitLocation();await enterLocation(${loc.id});return true})()`);
                await p.until(`window.furnitureRender&&furnitureJoined?.location_id===${loc.id}&&L.getLocationBuildingIds().includes('${buildingId}')`,'joined furniture location');
            }
            await page.call('Page.bringToFront');
            if(stress)await page.until(`L.getLocationGrassDiagnostics().kinds.low?.added>0`,'natural grass ready');
            await page.evaluate(`L.setLocationEditMode(true);selectLocationEditorTool('building');L.setLocationViewLevel(0);
                locationControls.target.set(5,1,5);locationControls.object.position.set(10,12,13);locationControls.update();
                document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();document.querySelector('#panel-characters:not(.collapsed) .toggle-btn')?.click();true`);
            await pause(250);
            if(index===0) {
                assert.ok(await page.evaluate(`!!document.querySelector('[data-location-tool="building"]')`));
                await page.evaluate(`selectLocationEditorTool('assets');true`);
                assert.equal(await page.evaluate(`document.getElementById('loc-generate-furniture').closest('.location-house-generator').hidden`),true);
                await page.evaluate(`selectLocationEditorTool('building');document.activeElement?.blur();true`);
                const floorPosition=await page.evaluate(`(()=>{const r=document.getElementById('location-floor-menu').getBoundingClientRect();return [r.x,r.y,r.width,r.height]})()`);
                assert.equal(await page.evaluate(`document.getElementById('location-floor-menu').parentElement.id`),'location-container');
                assert.equal(await page.evaluate(`document.getElementById('location-building-actions').parentElement.id`),'loc-building-floor-controls');
                assert.equal(await page.evaluate(`!!document.getElementById('loc-structure-new-building')`),false);
                assert.equal(await page.evaluate(`getComputedStyle(document.querySelector('.location-building-management')).display`),'grid');
                assert.deepEqual(await page.evaluate(`[...document.getElementById('loc-structure-level').options].map(o=>o.textContent)`),['Подвал 1','1-й этаж','2-й этаж']);
                assert.equal(await page.evaluate(`document.querySelectorAll('#location-floor-menu [data-floor-create],#location-floor-menu [data-floor-delete],#location-floor-menu [data-building-delete]').length`),0);
                await page.evaluate(`L.setLocationEditMode(false);true`);
                assert.equal(await page.evaluate(`document.getElementById('location-floor-menu').parentElement.id`),'location-container');
                assert.deepEqual(await page.evaluate(`(()=>{const r=document.getElementById('location-floor-menu').getBoundingClientRect();return [r.x,r.y,r.width,r.height]})()`),floorPosition,'floor navigation keeps the same screen position outside editing');
                assert.equal(await page.evaluate(`document.getElementById('location-building-actions').hidden`),true);
                await page.evaluate(`L.setLocationEditMode(true);true`);
                const position=()=>page.evaluate('locationControls.object.position.toArray()');
                const start=await position();
                await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyW',key:'w',windowsVirtualKeyCode:87});
                await pause(200);
                assert.notDeepEqual(await position(),start,'held W pans the camera');
                await page.evaluate(`document.getElementById('loc-generate-furniture').click();true`);
                await page.until('!!document.querySelector("dialog[open]")','dialog interrupts movement');
                await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyW',key:'w',windowsVirtualKeyCode:87});
                await pause(100);const stopped=await position();await pause(220);
                assert.deepEqual(await position(),stopped,'releasing W after opening construction dialog stops movement');
                await page.evaluate(`document.querySelector('dialog').close();document.querySelector('[data-house-draw]').focus();true`);
                await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyD',key:'d',windowsVirtualKeyCode:68});
                await pause(100);
                await page.evaluate(`document.querySelector('[data-house-draw]').addEventListener('keyup',e=>e.stopPropagation(),{once:true});true`);
                await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyD',key:'d',windowsVirtualKeyCode:68});
                await pause(100);const released=await position();await pause(220);
                assert.deepEqual(await position(),released,'UI cannot swallow the movement key release');
                assert.equal(await page.evaluate(`document.querySelectorAll('.location-asset-card').length`),0,'construction contains no asset cards');
                assert.equal(await page.evaluate(`['.location-asset-search','.location-asset-tabs','.location-asset-grid'].every(s=>getComputedStyle(document.querySelector(s)).display==='none')`),true,'construction hides the asset library');
                await page.evaluate(`selectLocationEditorTool('assets');document.querySelector('.location-asset-search').focus();true`);
                assert.equal(await page.evaluate(`document.querySelector('.location-asset-grid').hidden`),false);
                assert.ok(await page.evaluate(`document.querySelectorAll('.location-asset-card').length`)>0,'assets remain available in Assets');
                assert.equal(await page.evaluate(`['Постройки','PBR-материалы'].every(name=>[...document.querySelectorAll('.location-asset-tabs button')].some(b=>b.textContent===name))`),true);
                await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyW',key:'w',text:'w',windowsVirtualKeyCode:87});
                await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyW',key:'w',windowsVirtualKeyCode:87});
                await pause(150);assert.deepEqual(await position(),released,'text input does not pan');
                await page.evaluate(`document.querySelector('.location-asset-search').value='';document.activeElement.blur();locationControls.target.set(5,1,5);locationControls.object.position.set(10,12,13);locationControls.update();true`);
                console.log('BUILDING KEYBOARD PASS');
                await page.evaluate(`selectLocationEditorTool('assets');
                    document.querySelector('.location-asset-search').value='Стена';document.querySelector('.location-asset-search').dispatchEvent(new Event('input'));
                    [...document.querySelectorAll('.location-asset-card')].find(c=>c.textContent.trim().endsWith('Стена')).click();
                    locationControls.target.set(20,1,20);locationControls.object.position.set(20,14,28);locationControls.update();
                    document.getElementById('panel-tools').style.visibility='hidden';true`);
                await pause(250);
                const point=await page.evaluate(`(()=>{const p=new T.Vector3(20.5,1.04,20.5).project(locationControls.object),r=furnitureRender.r.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
                await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await pause(100);
                for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1,...point});
                await page.until(`furnitureRender.s.children.some(m=>m.userData.locationObject?.type==='wall'&&m.userData.locationObject.tile_x===20&&m.userData.locationObject.tile_y===20)`,'manual wall from Assets');
                original.push((await detail()).objects.find(o=>o.type==='wall'&&o.tile_x===20&&o.tile_y===20));
                await page.evaluate(`L.clearLocationAssetPreset();document.getElementById('panel-tools').style.visibility='';selectLocationEditorTool('building');
                    locationControls.target.set(5,1,5);locationControls.object.position.set(10,12,13);locationControls.update();true`);
                console.log('BUILDING MANUAL WALL PASS');
            }
            const stats=()=>page.evaluate(`(()=>{const {r,s,c}=furnitureRender;r.render(s,c);return {calls:r.info.render.calls,triangles:r.info.render.triangles,geometries:r.info.memory.geometries,textures:r.info.memory.textures,camera:c.position.toArray(),target:locationControls.target.toArray(),level:L.getLocationViewLevel()}})()`);
            const before=await stats();
            const open=async(scope='floor',level=0,roomId=null,empty=false)=>{
                await page.evaluate(`L.setLocationEditMode(true);selectLocationEditorTool('building');L.setLocationViewLevel(${level});window.dispatchEvent(new CustomEvent('location-editor-active-building',{detail:{buildingId:'${buildingId}',level:${level}}}));document.querySelector('#panel-tools.collapsed .toggle-btn')?.click();document.getElementById('loc-generate-furniture').scrollIntoView({block:'center'});true`);
                await pause(180);
                const button=await page.evaluate(`(()=>{const b=document.getElementById('loc-generate-furniture'),r=b.getBoundingClientRect();return {visible:!b.closest('[hidden]')&&r.width>0&&r.height>0,x:r.x+r.width/2,y:r.y+r.height/2}})()`);
                assert.equal(button.visible,true,'furniture button is visible in the normal editor');
                for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1,x:button.x,y:button.y});
                await page.until('!!document.querySelector("[data-furniture-preview]")','furniture dialog');
                await page.evaluate(`(()=>{const f=document.querySelector('dialog form');f.elements.scope.value='${scope}';f.elements.scope.dispatchEvent(new Event('change',{bubbles:true}));
                    ${roomId?`f.elements.roomId.value=${JSON.stringify(roomId)};`:''}f.elements.seed.value='browser-${index}-${scope}';f.elements.empty.checked=${empty};
                    f.dispatchEvent(new Event('input'));document.querySelector('[data-furniture-preview]').click()})()`);
                assert.equal(await page.evaluate(`document.querySelector('[data-furniture-create]').disabled`),false);
            };
            const save=async()=>{
                const eventCount=await peer.evaluate('furnitureEvents.length');
                page.events.delete('Page.javascriptDialogOpening');
                await page.evaluate(`setTimeout(()=>document.querySelector('dialog form').requestSubmit(document.querySelector('[data-furniture-create]')),0);true`);
                for(let i=0;i<100&&!page.events.has('Page.javascriptDialogOpening');i++)await pause(50);
                assert.ok(page.events.get('Page.javascriptDialogOpening')?.message.includes('Ручные и отредактированные предметы сохраняются'));
                await page.call('Page.handleJavaScriptDialog',{accept:true});
                await page.until(`!document.getElementById('loc-generate-furniture').disabled`,'furniture operation complete');
                await peer.until(`furnitureEvents.length>${eventCount}`,'peer furniture batch');
                await pause(150);
                assert.equal(await peer.evaluate('furnitureEvents.length'),eventCount+1,'one editor save emits one synchronized batch');
                const expected=(await detail()).objects.filter(o=>o.properties.furniture_generation).map(o=>o.id).sort((a,b)=>a-b);
                for(const p of [page,peer]){
                    await p.until(`furnitureRender.s.children.filter(m=>m.userData.locationObject?.properties?.furniture_generation).length===${expected.length}`,'all furniture meshes synchronized');
                    assert.deepEqual(await rootIds(p),expected);
                }
            };
            await open('room');
            assert.equal((await detail()).objects.length,original.length,'preview does not persist');
            await page.evaluate(`document.querySelector('dialog').close();true`);
            assert.equal((await detail()).objects.length,original.length,'cancel preserves empty building');
            await open('room');await save();
            await open('floor');await save();
            await open('building');await save();
            const furnished=(await detail()).objects;
            const furniture=furnished.filter(o=>o.properties.furniture_generation);
            if(index===0)assert.ok(furniture.some(o=>o.properties.level===-1)&&furniture.some(o=>o.properties.level===1));
            assert.deepEqual(furnished.filter(o=>original.some(a=>a.id===o.id)),original,'architecture and layout unchanged');
            const after=await stats();
            assert.deepEqual(after.camera,before.camera,'performance uses the same camera');
            assert.deepEqual(after.target,before.target);
            assert.equal(after.level,before.level);
            if(!stress) {
                const generated=await page.evaluate(`(async()=>{
                    const before=furnitureRender.s.children.filter(m=>m.userData.locationObjectId).map(m=>[m.userData.locationObjectId,m.uuid]);
                    const success=await L.generateLocationBuildingAnomalies('${buildingId}',{seed:'browser-anomalies'});
                    const objects=furnitureRender.s.children.filter(m=>m.userData.locationObject?.type==='anomaly');
                    return {success,ids:objects.map(m=>m.userData.locationObjectId),levels:objects.map(m=>m.userData.locationObject.properties.level),
                        effects:objects.every(m=>m.children.some(c=>c.userData.anomalyEffect)),
                        preserved:before.every(([id,uuid])=>furnitureRender.s.children.some(m=>m.userData.locationObjectId===id&&m.uuid===uuid))};
                })()`);
                assert.equal(generated.success,true);assert.equal(generated.effects,true);assert.equal(generated.preserved,true);
                assert.ok(generated.ids.length>0);
                await peer.until(`furnitureRender.s.children.filter(m=>m.userData.locationObject?.type==='anomaly').length===${generated.ids.length}`,'anomalies reach peer');
                assert.equal(await page.evaluate(`L.generateLocationBuildingAnomalies('${buildingId}',{seed:'browser-anomalies'})`),false,'repeating generation adds no duplicates');
                assert.equal(await page.evaluate(`socket.io.engine.transport.name`),'websocket');
                console.log('BUILDING ANOMALIES PASS',JSON.stringify({spec,...generated}));
            }
            if(stress){
                const samples=[],memory=[];
                for(let iteration=0;iteration<cycles;iteration++){
                    const result=await page.evaluate(`(async()=>{
                        const {Server}=await import('/static/js/api.js'),I=await import('/static/js/locationInteriorGenerator.js');
                        const read=async()=>await(await fetch('${url}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}})).json();
                        const source=(await read()).objects,s=furnitureRender.s;
                        const roots=new Map(s.children.filter(m=>m.userData.locationObjectId).map(m=>[m.userData.locationObjectId,m]));
                        const grass=()=>{const result=[];s.traverse(m=>{if(m.userData.naturalGrassKind==='low')result.push([m.userData.naturalGrassChunk,m.uuid])});return result.sort()};
                        const oldGrass=grass(),oldGeometries=furnitureRender.r.info.memory.geometries;
                        const plan=F.planBuildingFurniture(source,{buildingId:'${buildingId}',scope:'floor',level:1,seed:'repeat'});
                        const started=performance.now();const change=await Server.changeLocationObjects(${lobby.id},${loc.id},{objects:plan.objects,replace_furniture:plan.replacement});L.applyLocationObjectsChange(change);
                        const furnitureMs=performance.now()-started;
                        const structural=source.filter(o=>['floor','wall','door','stairs','ladder','hatch','floor_opening','window','roof'].includes(o.type));
                        const replacedStructure=structural.filter(o=>s.children.find(m=>m.userData.locationObjectId===o.id)!==roots.get(o.id)).length;
                        const grassPreserved=JSON.stringify(oldGrass)===JSON.stringify(grass());
                        let layoutMs=0;
                        if(${iteration}===0){
                            const next=(await read()).objects,layout=I.planBuildingInterior(next,{buildingId:'${buildingId}',level:1,seed:'repeat'});
                            const layoutStart=performance.now(),layoutChange=await Server.changeLocationObjects(${lobby.id},${loc.id},{objects:layout.objects,replace_layout:{building_id:'${buildingId}',level:1,object_ids:layout.replacedIds}});L.applyLocationObjectsChange(layoutChange);
                            layoutMs=performance.now()-layoutStart;
                        }
                        const untouched=source.filter(o=>o.properties.level!==1);
                        const saved=(await read()).objects;
                        const unchanged=untouched.every(o=>JSON.stringify(saved.find(p=>p.id===o.id))===JSON.stringify(o));
                        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
                        const ids=s.children.filter(m=>m.userData.locationObjectId).map(m=>m.userData.locationObjectId);
                        return {furnitureMs,layoutMs,replacedStructure,grassPreserved,unchanged,oldGeometries,geometries:furnitureRender.r.info.memory.geometries,
                            calls:furnitureRender.r.info.render.calls,duplicateIds:ids.length-new Set(ids).size,objects:saved.length,savedIds:saved.map(o=>o.id).sort((a,b)=>a-b)};
                    })()`);
                    await peer.until(`JSON.stringify(furnitureRender.s.children.filter(m=>m.userData.locationObjectId).map(m=>m.userData.locationObjectId).sort((a,b)=>a-b))===${JSON.stringify(JSON.stringify(result.savedIds))}`,'layout and furniture IDs synchronized to peer');
                    delete result.savedIds;
                    samples.push(result);
                    assert.equal(result.duplicateIds,0);assert.equal(result.unchanged,true);
                    if(!baseline){assert.equal(result.replacedStructure,0,'furniture must retain architecture meshes');assert.equal(result.grassPreserved,true,'furniture must retain natural grass chunks');}
                    if(cycles>3&&(iteration===4||iteration===9||iteration===cycles-1)){
                        const clients=[];
                        for(const p of [page,peer]){
                            // The test's socket history deliberately retains entire payloads;
                            // release it before measuring the application, on both clients.
                            await p.evaluate('furnitureEvents.length=0;performance.clearMeasures();performance.clearResourceTimings();true');
                            await p.call('HeapProfiler.collectGarbage');
                            const heap=await p.call('Runtime.getHeapUsage'),dom=await p.call('Memory.getDOMCounters');
                            clients.push({usedBytes:heap.usedSize,...dom});
                        }
                        memory.push({cycle:iteration+1,clients});
                        console.log('BUILDING MEMORY',JSON.stringify({spec,...memory.at(-1)}));
                    }
                }
                console.log('BUILDING STRESS',JSON.stringify({spec,baseline,samples}));
                if(!baseline)assert.ok(Math.abs(samples[2].geometries-samples[1].geometries)<=4,'repeated identical edits do not accumulate geometries');
                if(!baseline)assert.ok(Math.abs(samples.at(-1).geometries-samples[1].geometries)<=4,'extended edits do not accumulate geometries');
                if(memory.length>1)for(let client=0;client<2;client++){
                    const first=memory[0].clients[client],last=memory.at(-1).clients[client];
                    // A coarse regression guard, not a proof that every allocation is freed.
                    assert.ok(last.usedBytes-first.usedBytes<8*1024*1024,'retained heap growth after warmup stays below 8 MiB');
                }
            }
            if(stress&&index===0){
                const copied=await page.evaluate(`(async()=>{
                    const {Server}=await import('/static/js/api.js'),A=await import('/static/js/locationArchitecture.js');
                    const read=async()=>await(await fetch('${url}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}})).json();
                    const before=(await read()).objects,plan=A.storeyPlan({buildingId:'${buildingId}',sourceLevel:3,targetLevel:4,mode:'copy'},before);
                    if(plan.error)throw Error(plan.error);
                    const change=await Server.changeLocationObjects(${lobby.id},${loc.id},{objects:plan.objects});L.applyLocationObjectsChange(change);
                    const withCopy=(await read()).objects;
                    const deletion=await Server.changeLocationObjects(${lobby.id},${loc.id},{building_id:'${buildingId}',level:4},'DELETE');L.applyLocationObjectsChange(deletion);
                    const after=(await read()).objects;
                    return {created:change.objects.length,deletedId:deletion.deleted_ids[0],newIds:change.objects.every(o=>!before.some(p=>p.id===o.id)),
                        persisted:withCopy.some(o=>o.properties.level===4),restored:JSON.stringify(before)===JSON.stringify(after)};
                })()`);
                assert.ok(copied.created>0&&copied.newIds&&copied.persisted&&copied.restored);
                for(const p of [page,peer])await p.until(`furnitureEvents.some(e=>e.deleted_ids.includes(${copied.deletedId}))`,'copy deletion fully applied before terrain snapshots');
                for(const p of [page,peer])await p.evaluate(`window.grassBefore={};furnitureRender.s.traverse(m=>{if(m.userData.naturalGrassKind==='low')grassBefore[m.userData.naturalGrassChunk]=m.uuid});true`);
                await page.evaluate(`L.applyLocationTilesUpdate(${loc.id},[{x:30,z:30,height:1.2}]);socket.emit('update_location_tiles',{token:localStorage.getItem('access_token'),location_id:${loc.id},updates:[{x:30,z:30,height:1.2}]});true`);
                for(const p of [page,peer]){
                    await p.call('Page.bringToFront');
                    await p.until(`(()=>{const meshes=[];furnitureRender.s.traverse(m=>{if(m.userData.naturalGrassKind==='low')meshes.push(m)});
                        return meshes.some(m=>m.userData.naturalGrassChunk==='2,2'&&m.uuid!==grassBefore['2,2'])&&meshes.some(m=>m.userData.naturalGrassChunk==='0,0'&&m.uuid===grassBefore['0,0'])})()`,'local terrain refresh retains distant 12m chunks');
                }
                await page.call('Page.bringToFront');
                const roof=(await detail()).objects.find(o=>o.type==='roof'&&o.properties.roof_ground_anchor),anchor=roof.properties.roof_ground_anchor;
                const rootY=()=>page.evaluate(`furnitureRender.s.children.find(m=>m.userData.locationObjectId===${roof.id}).position.y`);
                const oldY=await rootY();
                for(const height of [1.25,1]){
                    await page.evaluate(`L.applyLocationTilesUpdate(${loc.id},[{x:${anchor.x},z:${anchor.z},height:${height}}]);socket.emit('update_location_tiles',{token:localStorage.getItem('access_token'),location_id:${loc.id},updates:[{x:${anchor.x},z:${anchor.z},height:${height}}]});true`);
                    for(const p of [page,peer]){
                        await p.call('Page.bringToFront');
                        await p.until(`Math.abs(furnitureRender.s.children.find(m=>m.userData.locationObjectId===${roof.id}).position.y-(${oldY+height-1}))<.001`,'roof follows raised and lowered terrain on both clients');
                    }
                    await page.call('Page.bringToFront');
                }
                console.log('BUILDING COPY TERRAIN PASS',JSON.stringify(copied));
            }
            if(index===0){
                const item=furniture.find(o=>o.properties.level===0&&o.type==='household_armchair');assert.ok(item);
                const handTemplate=furniture.find(o=>o.properties.level===0&&o.id!==item.id);
                const manual={...handTemplate,name:'Hand placed furniture',properties:{...handTemplate.properties}};delete manual.id;delete manual.revision;delete manual.properties.furniture_generation;
                const hand=await request(url+'/objects',manual,token);
                await page.until(`furnitureRender.s.children.some(m=>m.userData.locationObjectId===${hand.id})`,'manual object received');
                const x=item.tile_x+.5+item.properties.offset_x,z=item.tile_y+.5+item.properties.offset_z,y=1+item.properties.elevation+item.properties.dimensions.height*.6;
                await page.evaluate(`L.setLocationEditMode(true);selectLocationEditorTool('select');L.setLocationViewLevel(0);locationControls.target.set(${x},1,${z});locationControls.object.position.set(${x+1},14,${z+1});locationControls.update();document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click();true`);
                await pause(200);
                const point=await page.evaluate(`(()=>{const p=new T.Vector3(${x},${y},${z}).project(locationControls.object),r=furnitureRender.r.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
                await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await pause(100);
                for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1,...point});
                assert.ok(await page.evaluate(`!!L.getSelectedLocationEditorAsset()`),'ordinary furniture is selectable');
                assert.equal(await page.evaluate(`(()=>{const a=L.getSelectedLocationEditorAsset();return L.updateSelectedLocationAssetDetails({...a,color:'#7d6c57'})})()`),true);
                const edited=(await detail()).objects.filter(o=>o.properties.color==='#7d6c57');assert.equal(edited.length,1);
                const selectedItem=edited[0];
                assert.equal(selectedItem.id,item.id,'generated furniture is edited through the normal inspector');
                await peer.until(`furnitureRender.s.children.some(m=>m.userData.locationObjectId===${selectedItem.id}&&m.userData.locationObject.properties.color==='#7d6c57')`,'manual furniture edit reaches peer');
                await open('room',0,item.properties.furniture_generation.room_id);await save();
                const regenerated=(await detail()).objects;
                assert.ok(regenerated.some(o=>o.id===hand.id));
                assert.ok(regenerated.some(o=>o.id===selectedItem.id&&o.properties.color==='#7d6c57'));
                const other=furniture.filter(o=>(!stress||o.properties.level!==1)&&(o.properties.level!==0||o.properties.furniture_generation.room_id!==item.properties.furniture_generation.room_id));
                assert.ok(other.every(o=>regenerated.some(p=>p.id===o.id)));
            }
            for(const level of [0,1,-1])for(const view of [[10,12,13],[4,10,13]]){
                await page.evaluate(`L.setLocationEditMode(false);L.setLocationViewLevel(${level});locationControls.target.set(5,${1+level*2.4},5);locationControls.object.position.set(${view[0]},${view[1]+level*2.4},${view[2]});locationControls.update();true`);
                await page.evaluate(`document.getElementById('panel-tools').style.visibility='hidden';true`);
                await pause(160);
                const file=path.join(os.tmpdir(),`vtt-furniture-${index}-${level}-${view[0]}.png`);
                fs.writeFileSync(file,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
                console.log('FURNITURE FRAME',file);
            }
            await page.evaluate(`document.getElementById('panel-tools').style.visibility='';true`);
            const saved=(await detail()).objects;
            for(const p of [page,peer]){
                await p.call('Page.bringToFront');
                await p.evaluate(`(async()=>{exitLocation();await enterLocation(${loc.id});return true})()`);
                await p.until(`furnitureJoined?.location_id===${loc.id}&&L.getLocationBuildingIds().includes('${buildingId}')`,'furnished location reopened');
                assert.deepEqual(await rootIds(p),saved.filter(o=>o.properties.furniture_generation).map(o=>o.id).sort((a,b)=>a-b));
            }
            await page.call('Page.bringToFront');
            console.log('FURNITURE PERFORMANCE',JSON.stringify({spec,objects:furniture.length,before,after,
                planning:await page.evaluate(`performance.getEntriesByName('location-furniture:plan').map(e=>e.duration)`) }));
            if(index===0){
                await open('building',0,null,true);await save();
                const empty=(await detail()).objects;
                assert.ok(saved.filter(o=>!o.properties.furniture_generation||o.properties.color==='#7d6c57').every(o=>empty.some(p=>p.id===o.id)),'manual furniture and current architecture survive clearing generated furniture');
            }
            if(process.env.VTT_BLD_TRANSITIONS)for(const p of [page,peer]){
                await p.call('Page.bringToFront');
                await p.evaluate(`window.retiredLocationRefs=[new WeakRef(furnitureRender.s),new WeakRef(locationControls.object),new WeakRef(document.querySelector('#location-canvas canvas'))];exitLocation();window.furnitureRender=null;true`);
                await p.evaluate('document.body.getBoundingClientRect();new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
                await pause(80);await p.call('HeapProfiler.collectGarbage');
                assert.deepEqual(await p.evaluate("retiredLocationRefs.map((ref,i)=>ref.deref()===undefined?null:['scene','camera','canvas'][i]).filter(Boolean)"),[],'furnished scene and grass renderer are collectible after exit');
            }
        }
        console.log('FURNITURE BROWSER PASS');
    }finally{peer.close();await page.call('Target.closeTarget',{targetId:target.targetId}).catch(()=>{});}
};
