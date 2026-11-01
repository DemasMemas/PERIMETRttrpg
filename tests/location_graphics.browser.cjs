// Одинаковые камеры и полная копия Припяти; рабочая БД не используется.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
module.exports=async({page,safeSession,request,token,lobby,location,actor})=>{
    await page.evaluate(`Boolean(window.G=L.sceneGraphicsForTest())`);
    const views=[
        {name:'overview',target:[36,1,36],offset:[30,37,35],level:0},
        {name:'roofs',target:[29,3,34],offset:[11,10,14],level:0},
        {name:'lamps',target:[39,2,15],offset:[6,7,9],level:0},
        {name:'litter',target:[32.5,1.1,47.5],offset:[1.3,1.8,2.3],level:0},
        {name:'opening',target:[30,3.5,11],offset:[4,7,6],level:1},
        {name:'motion',target:[29,3,34],offset:[11,10,14],level:0,motion:true},
        {name:'walking-2048',target:[41,1,48],offset:[6,9,8],level:0,walking:true,mapSize:2048},
        {name:'walking-4096',target:[41,1,48],offset:[6,9,8],level:0,walking:true,mapSize:4096},
    ];
    const result={views:[],diagnostics:await page.evaluate(`(()=>{
        const lamps=G.roots.filter(r=>r.userData.locationObject.type==='household_street_lamp');
        return {size:G.renderer.getSize(new T.Vector2()).toArray(),pixelRatio:G.renderer.getPixelRatio(),
            sun:{bias:G.sun.shadow.bias,normalBias:G.sun.shadow.normalBias,map:G.sun.shadow.mapSize.toArray(),extent:G.sun.shadow.camera.right},
            lamps:lamps.map(r=>{const o=r.userData.locationObject;let meshes=0,casters=0;r.traverse(m=>{if(m.isMesh){meshes++;casters+=Number(m.castShadow)}});return{x:o.tile_x,y:o.tile_y,meshes,casters}})};
    })()`)};
    assert.ok(result.diagnostics.lamps.length>1);
    for(const view of views){
        await page.evaluate(`(()=>{L.setLocationViewLevel(${view.level});G.controls.target.set(...${JSON.stringify(view.target)});G.camera.position.copy(G.controls.target).add(new T.Vector3(...${JSON.stringify(view.offset)}));G.controls.update();})()`);
        if(view.mapSize)await page.evaluate(`(()=>{G.sun.shadow.mapSize.set(${view.mapSize},${view.mapSize});G.sun.shadow.map?.dispose();G.sun.shadow.map=null;G.sun.shadow.needsUpdate=true;})()`);
        await new Promise(r=>setTimeout(r,600));
        const measurement=await page.evaluate(`(async()=>{
            const times=[],cpu=[],calls=[],cache=[],batch=[],dirty=[],gpu=[],pending=[];
            const gl=G.renderer.getContext(),ext=G.renderer.capabilities.isWebGL2&&gl.getExtension('EXT_disjoint_timer_query_webgl2');
            const actor=[...G.characters.values()][0].model,origin=actor.position.clone();
            const oldCache=G.cache.update,oldBatch=G.batches.prepare,oldRender=G.renderer.render;
            G.cache.update=function(...args){const t=performance.now();try{return oldCache.apply(this,args)}finally{cache.push(performance.now()-t);dirty.push(G.sun.shadow.needsUpdate)}};
            G.batches.prepare=function(...args){const t=performance.now();try{return oldBatch.apply(this,args)}finally{batch.push(performance.now()-t)}};
            G.renderer.render=function(...args){const t=performance.now(),q=ext&&gl.createQuery();if(q)gl.beginQuery(ext.TIME_ELAPSED_EXT,q);
                try{return oldRender.apply(this,args)}finally{if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);pending.push(q);}cpu.push(performance.now()-t);calls.push(G.renderer.info.render.calls)}};
            try{let previous;for(let i=0;i<180;i++)await new Promise(done=>requestAnimationFrame(t=>{if(previous)times.push(t-previous);previous=t;
                if(${Boolean(view.walking)})actor.position.x=origin.x+Math.sin(i*.04)*.5;
                if(${Boolean(view.motion)}){const angle=.65+i*.006;G.camera.position.copy(G.controls.target).add(new T.Vector3(Math.sin(angle)*18,10,Math.cos(angle)*18));G.controls.update();}done()}));}
            finally{G.cache.update=oldCache;G.batches.prepare=oldBatch;G.renderer.render=oldRender;actor.position.copy(origin);}
            if(ext){await new Promise(r=>setTimeout(r,100));for(const q of pending){if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)&&!gl.getParameter(ext.GPU_DISJOINT_EXT))gpu.push(gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q)}}
            const stats=a=>{a.sort((a,b)=>a-b);return {median:a[Math.floor(a.length*.5)],p95:a[Math.floor(a.length*.95)],mean:a.reduce((n,v)=>n+v,0)/a.length}};
            return {frameMs:stats(times),renderCpuMs:stats(cpu),gpuMs:gpu.length?stats(gpu):null,cacheMs:stats(cache),batchMs:stats(batch),drawCalls:stats(calls),shadowUpdates:dirty.filter(Boolean).length,frames:dirty.length};
        })()`);
        result.views.push({name:view.name,...measurement});
        fs.writeFileSync(path.join(safeSession.artifacts,view.name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('GRAPHICS '+view.name+' '+JSON.stringify(measurement));
    }
    const opening=await page.evaluate(`(()=>{L.setLocationViewLevel(1);const r=G.roots.find(r=>r.userData.locationObject.type==='floor_opening'&&r.children.some(m=>m.material?.userData.openingFracture));
        const meshes=[];r.traverse(m=>{if(m.isMesh)meshes.push({cast:m.castShadow,receive:m.receiveShadow,colorWrite:m.material.colorWrite,depthWrite:m.material.depthWrite,visible:m.material.visible,fracture:Boolean(m.material.userData.openingFracture)})});
        r.updateMatrixWorld(true);const center=r.getWorldPosition(new T.Vector3());const hits=new T.Raycaster(center.clone().add(new T.Vector3(0,2,0)),new T.Vector3(0,-1,0)).intersectObject(r,true);
        return {meshes,pickable:hits.length>0};})()`);
    assert.ok(opening.pickable,'Проём остаётся доступен для выбора');
    assert.ok(opening.meshes.some(m=>!m.visible&&!m.cast&&!m.receive),'Невидимая поверхность выбора сохранена');
    assert.ok(opening.meshes.some(m=>m.fracture&&m.cast&&m.receive&&m.visible),'Обломки видимы и отбрасывают тени');
    assert.ok(opening.meshes.length<=2,'Обломки одного проёма объединены для отрисовки');
    result.opening=opening;
    result.partitions=await page.evaluate(`(()=>{
        const roots=G.roots.filter(r=>{const o=r.userData.locationObject,p=o.properties||{};return o.type==='wall'&&p.layout_generation?.version===1&&['damaged','ruins'].includes(p.layout_generation.condition)&&p.dimensions.height<2.4;});
        return roots.map(r=>{const ys=new Set();r.traverse(m=>{if(!m.isMesh)return;const p=m.geometry.attributes.position;for(let i=0;i<p.count;i++)ys.add(p.getY(i).toFixed(5))});return{id:r.userData.locationObjectId,levels:ys.size};});
    })()`);
    assert.ok(result.partitions.length>0);assert.ok(result.partitions.every(p=>p.levels>6),'Неполные перегородки исходной Припяти имеют рваный профиль');
    result.floorAttachments=[];
    for(const level of [0,1,0]){
        await page.evaluate(`(()=>{G.controls.target.set(30,2,32);G.camera.position.set(34,10,38);G.controls.update();L.setLocationViewLevel(${level});})()`);
        await new Promise(r=>setTimeout(r,250));
        const state=await page.evaluate(`(()=>{const roots=G.roots.filter(r=>r.userData.locationObject.properties?.building_id==='pripyat-4'&&r.userData.locationObject.properties.level===1&&r.userData.floorAttachedMeshes?.length);
            const meshes=roots.flatMap(r=>r.userData.floorAttachedMeshes);
            const visible=meshes.filter(m=>m.visible).length,shadowVisible=G.shadowPass.run(()=>meshes.filter(m=>m.visible).length);
            const root=roots.slice().sort((a,b)=>a.position.distanceTo(G.controls.target)-b.position.distanceTo(G.controls.target))[0],p=root.position.clone().project(G.camera),rect=G.renderer.domElement.getBoundingClientRect();
            const hit=G.pickAtScreen(rect.left+(p.x+1)*rect.width/2,rect.top+(1-p.y)*rect.height/2);
            const target=G.sun.shadow.map,center=new T.Vector3(30,3.4,33).project(G.sun.shadow.camera),data=new Uint8Array(256*256*4);
            const sx=Math.max(0,Math.min(target.width-256,Math.round((center.x+1)*target.width/2)-128)),sy=Math.max(0,Math.min(target.height-256,Math.round((center.y+1)*target.height/2)-128));
            G.renderer.readRenderTargetPixels(target,sx,sy,256,256,data);let hash=2166136261;for(const byte of data)hash=Math.imul(hash^byte,16777619);
            let floorCasters=0;G.shadowPass.run(()=>G.roots.filter(r=>r.userData.locationObject.type==='floor'&&r.userData.locationObject.properties?.building_id==='pripyat-4'&&r.userData.locationObject.properties.level===1).forEach(r=>r.traverseVisible(m=>{if(m.isMesh&&m.castShadow)floorCasters++})));
            return {count:meshes.length,visible,shadowVisible,floorCasters,shadowHash:hash>>>0,pickRoots:roots.filter(r=>r.visible).length,hitType:hit?.userData.locationObject.type,hitLevel:hit?.userData.locationObject.properties.level};})()`);
        assert.ok(state.count>0);assert.equal(state.visible,level?state.count:0);assert.equal(state.shadowVisible,state.count,'Скрытие камерой не убирает физические тени этажа');
        assert.equal(state.pickRoots,level?state.pickRoots:0,'Проёмы верхнего этажа не перехватывают выбор снизу');
        if(!level)assert.notEqual(state.hitLevel,1,'Настоящий выбор лучом не возвращает проём над персонажем');
        assert.ok(state.floorCasters>0,'Уцелевшее перекрытие закрывает солнце');
        if(result.floorAttachments.length)assert.equal(state.shadowHash,result.floorAttachments[0].shadowHash,'Карта физических теней одинакова при переключении этажей');
        result.floorAttachments.push({level,...state});
        fs.writeFileSync(path.join(safeSession.artifacts,level?'attached-own-floor.png':'attached-lower-floor.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    await page.evaluate(`(()=>{L.setLocationViewLevel(0);G.controls.target.set(32.5,1.1,47.5);G.camera.position.set(33.8,2.9,49.8);G.controls.update();})()`);
    await new Promise(r=>setTimeout(r,300));
    result.smallShadows=await page.evaluate(`(()=>{
        const renderer=G.renderer,size=renderer.getDrawingBufferSize(new T.Vector2()),previous=renderer.getRenderTarget();
        const target=new T.WebGLRenderTarget(size.x,size.y),pixels=new Uint8Array(size.x*size.y*4),other=new Uint8Array(pixels.length),out=[];
        const sunDelta=G.sun.position.clone().sub(G.sun.target.position);
        try{for(const type of ['household_bottle','household_tin_can']){
            const root=G.roots.find(r=>r.userData.locationObject.type===type&&r.userData.locationObject.tile_x===32&&r.userData.locationObject.tile_y===47),casters=[];
            root.traverse(m=>{if(m.isMesh&&m.castShadow)casters.push(m)});
            const bounds=new T.Box3().setFromObject(root),center=bounds.getCenter(new T.Vector3()),ground=bounds.min.y-.04;
            center.addScaledVector(sunDelta,-(center.y-ground)/sunDelta.y);center.y=ground;center.project(G.camera);
            const px=Math.round((center.x+1)*size.x/2),py=Math.round((center.y+1)*size.y/2);
            renderer.setRenderTarget(target);G.sun.shadow.needsUpdate=true;renderer.render(G.scene,G.camera);renderer.readRenderTargetPixels(target,0,0,size.x,size.y,pixels);
            casters.forEach(m=>m.castShadow=false);
            try{G.sun.shadow.needsUpdate=true;renderer.render(G.scene,G.camera);renderer.readRenderTargetPixels(target,0,0,size.x,size.y,other);}
            finally{casters.forEach(m=>m.castShadow=true);G.sun.shadow.needsUpdate=true;}
            let changed=0,max=0;for(let y=Math.max(0,py-45);y<Math.min(size.y,py+45);y++)for(let x=Math.max(0,px-45);x<Math.min(size.x,px+45);x++){
                const i=(y*size.x+x)*4,d=Math.max(...[0,1,2].map(c=>Math.abs(pixels[i+c]-other[i+c])));if(d>4)changed++;max=Math.max(max,d);
            }out.push({type,changedPixels:changed,maxDifference:max});
        }return out;}finally{renderer.setRenderTarget(previous);target.dispose();}
    })()`);
    for(const shadow of result.smallShadows)assert.ok(shadow.changedPixels>0,'Есть видимая тень '+shadow.type);
    fs.writeFileSync(path.join(safeSession.artifacts,'graphics.json'),JSON.stringify(result,null,2));
    // Сравнение пикселей и правок объектов проверяет, что оптимизация обхода
    // сохраняет исходное изображение, обновления геометрии и видимость этажей.
    await page.evaluate('Boolean(window.__quarterPerf=G)');
    await require('./location_render_batches.browser.cjs')(page);
    // Новая генерация в копии существующей Припяти, после сравнения скорости.
    const url='/lobbies/'+lobby.id+'/locations/'+location.id;
    const old=await page.evaluate(`G.roots.filter(r=>r.userData.locationObject.properties?.building_id==='pripyat-7').map(r=>({id:r.userData.locationObject.id,level:r.userData.locationObject.properties.level||0}))`);
    const plan=await page.evaluate(`(async()=>{const H=await import('/static/js/locationHouseGenerator.js');return H.planBuildingShell({minX:24,minZ:53,width:12,depth:10,storeys:2,basements:0,wealth:'ordinary',condition:'ruins',buildingId:'graphics-ruins',seed:'damage-20261102',roofType:'flat'});})()`);
    assert.equal(plan.error,undefined);assert.ok(plan.objects.some(o=>o.properties.structural_debris));
    for(const level of [...new Set(old.map(o=>o.level))])await request(url+'/objects/batch',{object_ids:old.filter(o=>o.level===level).map(o=>o.id),level},token,'DELETE');
    await request(url+'/objects/batch',{objects:plan.objects},token);
    await page.until(`L.sceneGraphicsForTest().roots.filter(r=>r.userData.locationObject.properties?.building_id==='graphics-ruins').length===${plan.objects.length}`,'Разрушенный дом отрисован после сетевого события');
    await page.evaluate('Boolean(window.G=L.sceneGraphicsForTest())');
    await new Promise(r=>setTimeout(r,1200));
    const debrisMeshes=await page.evaluate(`G.roots.filter(r=>r.userData.locationObject.properties?.structural_debris).map(r=>{let n=0;r.traverse(m=>{if(m.isMesh)n++});return n})`);
    assert.ok(debrisMeshes.length>0&&debrisMeshes.every(n=>n===1),'Каждый завал с арматурой объединён в одну сетку');
    result.generatedViews=[];
    for(const [name,level,target,offset] of [
        ['generated-ruins',0,[30,1,58],[8,10,13]],
        ['generated-fractures',1,[31,3.5,59],[3,5,7]],
        ['generated-wall-close',0,[34,1,57],[3,3,5]],
    ]){
        await page.evaluate(`(()=>{L.setLocationViewLevel(${level});G.controls.target.set(...${JSON.stringify(target)});G.camera.position.copy(G.controls.target).add(new T.Vector3(...${JSON.stringify(offset)}));G.controls.update();})()`);
        await new Promise(r=>setTimeout(r,400));
        const frames=await page.evaluate(`(async()=>{const times=[];let last;for(let i=0;i<120;i++)await new Promise(done=>requestAnimationFrame(t=>{if(last)times.push(t-last);last=t;done()}));times.sort((a,b)=>a-b);return{median:times[59],p95:times[113],mean:times.reduce((a,b)=>a+b,0)/times.length}})()`);
        result.generatedViews.push({name,frameMs:frames});
        fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    const saved=await request(url,null,token,'GET');
    result.generated={objects:plan.objects.length,debris:saved.objects.filter(o=>o.properties?.structural_debris).length};
    assert.ok(result.generated.debris>0,'Завалы сохранены в изолированной БД');
    const routeBefore=await request(url+'/movement-preview',{character_id:actor.id,x:46,y:48,floor_level:0},token);
    const sample=saved.objects.find(o=>o.properties?.structural_debris);
    assert.ok(routeBefore.route?.path.length>=2);
    const segment=Math.floor((routeBefore.route.path.length-1)/2),a=routeBefore.route.path[segment],b=routeBefore.route.path[segment+1];
    const px=(a[0]+b[0])/2,py=(a[1]+b[1])/2;
    await request(url+'/objects/batch',{objects:[{type:sample.type,name:'Проверочный проходимый завал',tile_x:Math.floor(px),tile_y:Math.floor(py),
        properties:{...sample.properties,building_id:null,level:0,offset_x:px-Math.floor(px),offset_z:py-Math.floor(py)}}]},token);
    const routeAfter=await request(url+'/movement-preview',{character_id:actor.id,x:46,y:48,floor_level:0},token);
    assert.ok(routeBefore.route&&routeAfter.route);
    assert.deepEqual(routeAfter.route,routeBefore.route,'Завал на пути не меняет маршрут, стоимость и перелезания');
    result.debrisPassable=true;
    fs.writeFileSync(path.join(safeSession.artifacts,'graphics.json'),JSON.stringify(result,null,2));
    await require('./location_ruin_assets.browser.cjs')({page,request,token,lobby,location,safeSession});
};
