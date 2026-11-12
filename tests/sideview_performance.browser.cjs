// Боковая Припять, неподвижная и движущаяся камера, настоящие фонари.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'sideview');assert.ok(safeSession.verified);
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:900,deviceScaleFactor:1,mobile:false});
    const globalReference=await page.evaluate(`(async()=>{const world=await import('/static/js/lobby3d.js'),frames=[];let last;
        for(let i=0;i<100;i++)await new Promise(resolve=>requestAnimationFrame(t=>{if(last)frames.push(t-last);last=t;resolve()}));
        frames.sort((a,b)=>a-b);return{scope:'Глобальная карта изолированного тестового мира',chunks:world.chunksMap.size,
            frameMean:frames.reduce((a,b)=>a+b,0)/frames.length,p95:frames[Math.floor(frames.length*.95)],calls:world.renderer.info.render.calls}})()`);
    const {fixture,sha256}=loadFixture(),api=`/lobbies/${lobby.id}`;
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    await request(api+'/time',{game_day:1,game_time_minutes:720},token,'PATCH');
    await request(api+'/weather',{sun:{enabled:true,intensity:.7},rain:{enabled:true,intensity:.5},fog:{enabled:false,intensity:.5},emission:{enabled:false,intensity:.5}},token,'PATCH');
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Боковая Припять'});
    await page.until(`window.currentLocationId===${location.id}&&window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена загружена');
    await page.evaluate("(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');window.FG=L.sceneGraphicsForTest();return true})()");
    const templates=await request(api+'/templates',null,token,'GET');
    const head=templates.global.find(item=>item.name==='Налобный фонарик'&&item.category!=='weapon_module');
    const hand=templates.global.find(item=>item.name==='Фонарик'&&item.category!=='weapon_module');assert.ok(head&&hand);
    const actors=[];
    for(let n=0;n<2;n++){
        const items=[head,hand].map((t,i)=>({id:`side-lamp-${n}-${i}`,templateId:t.id,name:t.name,category:t.category,quantity:1,weight:t.weight,volume:t.volume,attributes:structuredClone(t.attributes||{})}));
        const actor=await request(api+'/characters',{name:'Носитель света '+n,data:{inventory:{pockets:items,backpack:[]}}},token);
        await request(loc+'/spawn_character',{character_id:actor.id,tile_x:33+n*4,tile_y:20},token);
        await request(loc+'/characters/'+actor.id+'/facing',{facing_x:0,facing_y:1},token,'PATCH');actors.push({actor,items});
        for(let i=0;i<2;i++){
            const fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');
            await request('/lobbies/characters/'+actor.id+'/portable-lights',{operation:'prepare',path:['inventory','pockets',i],source_id:items[i].id,
                character_revision:fresh.data._revision,mount:i===0?'head':'hand'},token);
        }
    }
    const toggle=async(n,i)=>{const {actor,items}=actors[n],fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET');
        await request('/lobbies/characters/'+actor.id+'/portable-lights',{operation:'toggle',path:['inventory','pockets',i],source_id:items[i].id,character_revision:fresh.data._revision},token);};
    const views=[{name:'overhead',position:[86,49,86],target:[36,1,36]},
        {name:'side-front',position:[36,11,-48],target:[36,3,36]},
        {name:'side-flank',position:[-48,11,36],target:[36,3,36]},
        {name:'side-back',position:[36,11,120],target:[36,3,36]},
        {name:'side-close',position:[36,8,108],target:[36,3,36]}];
    const camera=async view=>page.evaluate(`(()=>{locationControls.enableDamping=false;locationControls.update();locationControls.target.set(...${JSON.stringify(view.target)});locationControls.object.position.set(...${JSON.stringify(view.position)});locationControls.update();locationControls.enableDamping=true;return true})()`);
    const wait=async count=>page.evaluate(`new Promise(resolve=>{let n=0;function f(){if(++n===${count})resolve(true);else requestAnimationFrame(f)}requestAnimationFrame(f)})`);
    const shot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const metrics=[];
    const measure=async(label,{warm=30,duration=0}={})=>{
        if(warm)await wait(warm);
        const result=await page.evaluate(`(async()=>{
            const {renderer,camera,sun}=FG,stats=a=>{if(!a.length)return null;const mean=a.reduce((n,v)=>n+v,0)/a.length;a.sort((a,b)=>a-b);return{mean,median:a[Math.floor(a.length*.5)],p95:a[Math.floor(a.length*.95)],max:a[a.length-1]}};
            const gl=renderer.getContext(),ext=renderer.capabilities.isWebGL2&&gl.getExtension('EXT_disjoint_timer_query_webgl2');
            const cpu=[],gpu=[],queries=[],frames=[],speed=[],original=renderer.render;let last,lastPosition,sunUpdates=0;const started=performance.now();
            renderer.render=function(...args){const at=performance.now(),q=ext&&gl.createQuery();if(q)gl.beginQuery(ext.TIME_ELAPSED_EXT,q);
                if(sun.shadow.needsUpdate)sunUpdates++;
                try{return original.apply(this,args)}finally{if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);queries.push(q)}cpu.push(performance.now()-at)}};
            try{for(let i=0;${duration?`performance.now()-started<${duration}`:'i<160'};i++)await new Promise(resolve=>requestAnimationFrame(t=>{
                if(last){const dt=t-last;frames.push(dt);speed.push(camera.position.distanceTo(lastPosition)/(dt/1000))}last=t;lastPosition=camera.position.clone();resolve()}));}
            finally{renderer.render=original;}
            if(ext){for(let i=0;i<8;i++)await new Promise(requestAnimationFrame);for(const q of queries){if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)&&!gl.getParameter(ext.GPU_DISJOINT_EXT))gpu.push(gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q)}}
            return{frames:stats(frames),renderCpu:stats(cpu),gpu:stats(gpu),speed:stats(speed),gaps:frames.filter(x=>x>25).length,
                calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,batches:FG.scene.children.filter(n=>n.userData.locationRenderBatch).length,
                lamps:L.getLocationPortableLightDiagnostics().lights,sunUpdates};
        })()`);metrics.push({label,...result});return result;
    };
    for(const view of views){
        await camera(view);
        const row=await measure('rain/off/'+view.name);
        assert.ok(row.sunUpdates<=2,'Неподвижные окна не должны обновлять солнечную тень каждый кадр');
        await shot(view.name+'-off');
    }
    // Контроль той же сцены и камеры: стоимость утраченного объединения.
    await camera(views[4]);
    await page.evaluate('window.savedBatchPrepare=FG.batches.prepare;FG.batches.prepare=()=>{};true');
    try{await measure('control/no-batches/side-close');}
    finally{await page.evaluate('FG.batches.prepare=savedBatchPrepare;delete window.savedBatchPrepare;true');}
    // Самостоятельный профиль главного потока; не смешивается с GPU-замером.
    await camera(views[1]);await wait(30);await page.call('Profiler.enable');await page.call('Profiler.start');await wait(140);
    const profile=(await page.call('Profiler.stop')).profile;await page.call('Profiler.disable');
    fs.writeFileSync(path.join(safeSession.artifacts,'side-cpu.cpuprofile'),JSON.stringify(profile));
    const key=async(type,code)=>page.call('Input.dispatchKeyEvent',{type,key:code==='KeyW'?'w':'a',code,windowsVirtualKeyCode:code==='KeyW'?87:65});
    const motion=async label=>{await camera(views[3]);await wait(30);await key('keyDown','KeyW');
        try{const row=await measure(label,{warm:0,duration:450});assert.ok(row.speed.median>1,'Камера действительно двигалась');}
        finally{await key('keyUp','KeyW');}};
    const orbit=async label=>{await camera(views[3]);await wait(30);
        await page.call('Input.dispatchMouseEvent',{type:'mousePressed',x:960,y:500,button:'right',clickCount:1});
        try{await Promise.all([measure(label,{warm:0,duration:450}),(async()=>{for(let i=1;i<=22;i++){
            await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:960+i*5,y:500,button:'right',buttons:2});await new Promise(r=>setTimeout(r,18));}})()]);}
        finally{await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:1070,y:500,button:'right',clickCount:1});}
    };
    await motion('rain/off/wasd');await orbit('rain/off/orbit');
    await camera(views[1]);await toggle(0,0);await page.until('L.getLocationPortableLightDiagnostics().lights===1','Один фонарь');
    await measure('rain/one/side-front');await shot('side-front-one');
    await motion('rain/one/wasd');await orbit('rain/one/orbit');
    await camera(views[1]);for(const[n,i]of [[0,1],[1,0],[1,1]])await toggle(n,i);
    await page.until('L.getLocationPortableLightDiagnostics().lights===4','Четыре фонаря');await measure('rain/four/side-front');await shot('side-front-four');
    await camera(views[4]);await measure('rain/four/side-close');await shot('side-close-four');
    await motion('rain/four/wasd');await orbit('rain/four/orbit');
    await camera(views[1]);
    // Синхронная проверка: объединение деталей не меняет вид исходной сцены.
    const pixels=await page.evaluate(`(()=>{
        const {scene,renderer,camera,batches}=FG,target=new T.WebGLRenderTarget(720,450),old=renderer.getRenderTarget();
        const sample=()=>{renderer.render(scene,camera);const out=new Uint8Array(720*450*4);renderer.readRenderTargetPixels(target,0,0,720,450,out);return out};
        try{batches.restore();renderer.setRenderTarget(target);const original=sample();batches.prepare();let actual;try{actual=sample()}finally{batches.restore()}
            let bad=0,total=0,max=0;for(let i=0;i<actual.length;i++){const d=Math.abs(original[i]-actual[i]);if(d>4)bad++;total+=d;max=Math.max(max,d)}
            return{badShare:bad/actual.length,mean:total/actual.length,max};
        }finally{renderer.setRenderTarget(old);target.dispose()}
    })()`);assert.ok(pixels.badShare<.005,JSON.stringify(pixels));
    const errors=await page.evaluate('FG.renderer.info.programs.filter(p=>p.diagnostics?.runnable===false).length');assert.equal(errors,0);
    assert.equal(await page.evaluate("document.querySelectorAll('.weather-audio-unlock').length"),0,'Кнопка погоды не перекрывает игру');
    fs.writeFileSync(path.join(safeSession.artifacts,'sideview-results.json'),JSON.stringify({sha256,viewport:[1920,900],globalReference,metrics,pixels},null,2));
    console.log('Боковая Припять: неподвижные ракурсы, WASD, 0/1/4 фонаря, GPU/CPU и сравнение изображения.');
};
