// Реальная загрузка сохранённой глобальной карты через обычный API и Chrome/GPU.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

module.exports=async({page,request,token,base,safeSession})=>{
    const lobby=await request('/lobbies/',{name:'Замер загрузки глобальной карты',map_type:'predefined',chunks_width:8,chunks_height:8},token);
    // Генерация отделена от измерения: пользователь открывает уже сохранённую карту.
    for(let x=0;x<8;x+=2)for(let y=0;y<8;y+=2){
        await request(`/lobbies/${lobby.id}/chunks?min_chunk_x=${x}&max_chunk_x=${x+1}&min_chunk_y=${y}&max_chunk_y=${y+1}`,null,token,'GET');
    }
    console.log('Глобальная карта 8×8 сохранена в собственной тестовой БД.');
    await page.call('Page.addScriptToEvaluateOnNewDocument',{source:`
        window.worldGlTimings={};
        for(const Class of [window.WebGLRenderingContext,window.WebGL2RenderingContext]){
            if(!Class)continue;
            for(const method of ['getProgramInfoLog','getShaderInfoLog','getProgramParameter','compileShader','linkProgram','texImage2D']){
                const original=Class.prototype[method];
                Class.prototype[method]=function(...args){const t=performance.now();try{return original.apply(this,args)}finally{const s=worldGlTimings[method]||={calls:0,ms:0};s.calls++;s.ms+=performance.now()-t;}};
            }
        }
        addEventListener('DOMContentLoaded',()=>{
            window.worldLoadingLabels=[];
            const screen=document.getElementById('scene-loading-screen');
            if(screen)new MutationObserver(()=>worldLoadingLabels.push({at:performance.now(),percent:document.getElementById('scene-loading-percent').textContent,detail:document.getElementById('scene-loading-detail').textContent,hidden:screen.classList.contains('is-hidden')})).observe(screen,{subtree:true,childList:true,attributes:true});
        });
    `});
    const samples=[];
    for(let run=0;run<3;run++){
        if(run===0){await page.call('Profiler.enable');await page.call('Profiler.start');}
        await page.call('Page.navigate',{url:`${base}/lobbies/${lobby.id}/page`});
        let complete=false;
        for(let attempt=0;attempt<480;attempt++){
            await pause(250);
            complete=await page.evaluate(`Boolean(window.worldLoadMetrics?.complete && document.getElementById('scene-loading-screen')?.classList.contains('is-hidden'))`);
            if(complete)break;
        }
        assert.ok(complete,'Глобальная карта не загружена за две минуты');
        const metrics=await page.evaluate('JSON.parse(JSON.stringify(window.worldLoadMetrics))');
        metrics.gl=await page.evaluate('window.worldGlTimings');
        metrics.labels=await page.evaluate('window.worldLoadingLabels');
        metrics.visibleReadyMs=await page.evaluate('performance.now()-window.worldLoadMetrics.startedAt');
        assert.equal(metrics.loadedChunks,64);
        assert.equal(metrics.failedRequests,0);
        samples.push(metrics);
        fs.writeFileSync(path.join(safeSession.artifacts,'world-loading.json'),JSON.stringify({samples},null,2));
        console.log(`Загрузка ${run+1}: ${Math.round(metrics.fullLoadMs)} мс; рельеф ${Math.round(metrics.terrainRefreshMs)} мс; добавление ${Math.round(metrics.addChunkMs)} мс.`);
        if(run===0)await require('./world_weather.browser.cjs')({page,request,token,lobby,base,safeSession});
        if(run===0){
            const {profile}=await page.call('Profiler.stop');
            const counts=new Map();
            for(const n of profile.nodes){const key=n.callFrame.functionName+' '+n.callFrame.url.split('/').pop();counts.set(key,(counts.get(key)||0)+(n.hitCount||0));}
            fs.writeFileSync(path.join(safeSession.artifacts,'cpu-top.json'),JSON.stringify([...counts].sort((a,b)=>b[1]-a[1]).slice(0,40),null,2));
            await page.call('Profiler.disable');
        }
    }
    await page.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js')})()`);
    const state=await page.evaluate(`(()=>{
        const hash=array=>{let h=2166136261;const bytes=new Uint8Array(array.buffer,array.byteOffset,array.byteLength);for(const b of bytes)h=Math.imul(h^b,16777619);return (h>>>0).toString(16)};
        return {camera:sceneModule.camera.position.toArray(),target:sceneModule.controls.target.toArray(),chunks:[...sceneModule.chunksMap].sort(([a],[b])=>a.localeCompare(b)).map(([key,c])=>({key,geometry:hash(c.smoothTerrain.geometry.attributes.position.array),normals:hash(c.smoothTerrain.geometry.attributes.normal.array),pixels:hash(c.terrainTexture.image.data),roads:c.roads?.length||0}))};
    })()`);
    fs.writeFileSync(path.join(safeSession.artifacts,'world-loading.json'),JSON.stringify({samples,state},null,2));
    const screenshot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'world-ready.png'),Buffer.from(screenshot.data,'base64'));

    // Отмена штатной кнопкой после получения первого пакета: запоздавшие
    // ответы не должны достроить карту или помешать следующему открытию.
    const preload=await page.call('Page.addScriptToEvaluateOnNewDocument',{source:`
        const originalFetch=window.fetch;
        let requested=0;
        window.fetch=async(...args)=>{
            if(String(args[0]).includes('/chunks?') && ++requested>1)
                await new Promise(resolve=>window.releaseWorldResponse=resolve);
            return originalFetch(...args);
        };
    `});
    await page.call('Page.navigate',{url:`${base}/lobbies/${lobby.id}/page`});
    await page.until(`window.worldLoadMetrics?.requests===1 && Boolean(window.releaseWorldResponse)`,'первый пакет до отмены');
    assert.equal(await page.evaluate('window.worldLoadMetrics.chunks'),4);
    assert.equal(await page.evaluate('window.worldLoadMetrics.terrainRefreshes'),0);
    await page.evaluate(`document.getElementById('scene-loading-cancel').click()`);
    await page.until(`location.pathname==='/'`,'отмена загрузки возвращает в меню');
    await page.call('Page.removeScriptToEvaluateOnNewDocument',{identifier:preload.identifier});
    await page.call('Page.navigate',{url:`${base}/lobbies/${lobby.id}/page`});
    await page.until(`window.worldLoadMetrics?.complete && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'карта открывается после отмены');
    assert.equal(await page.evaluate('window.worldLoadMetrics.loadedChunks'),64);
    assert.equal(await page.evaluate(`document.getElementById('scene-loading-percent').textContent`),'100%');
    fs.writeFileSync(path.join(safeSession.artifacts,'world-loading.json'),JSON.stringify({samples,state,cancelAndReopen:true},null,2));
    console.log('Отмена загрузки после первого пакета и повторное открытие: пройдены.');
    const holdPixels=await page.call('Page.addScriptToEvaluateOnNewDocument',{source:`
        if(location.pathname.endsWith('/page')){
        const NativeWorker=window.Worker;
        sessionStorage.setItem('pixelWorkersCreated','0');sessionStorage.setItem('pixelWorkersStopped','0');
        window.heldPixelResults=0;
        window.Worker=class{
            constructor(...args){
                this.native=new NativeWorker(...args);
                sessionStorage.setItem('pixelWorkersCreated',String(Number(sessionStorage.getItem('pixelWorkersCreated'))+1));
                this.native.onmessage=e=>{if(e.data.ready)this.onmessage?.(e);else window.heldPixelResults++;};
                this.native.onerror=e=>this.onerror?.(e);this.native.onmessageerror=e=>this.onmessageerror?.(e);
            }
            postMessage(...args){this.native.postMessage(...args);}
            terminate(){sessionStorage.setItem('pixelWorkersStopped',String(Number(sessionStorage.getItem('pixelWorkersStopped'))+1));this.native.terminate();}
        };
        }
    `});
    await page.call('Page.navigate',{url:`${base}/lobbies/${lobby.id}/page`});
    await page.until('window.heldPixelResults>0','фоновый расчёт начался, результаты задержаны');
    const created=await page.evaluate(`Number(sessionStorage.getItem('pixelWorkersCreated'))`);
    assert.ok(created>0);
    await page.evaluate(`document.getElementById('scene-loading-cancel').click()`);
    await page.until(`location.pathname==='/'`,'отмена во время подготовки текстур');
    assert.equal(await page.evaluate(`Number(sessionStorage.getItem('pixelWorkersStopped'))`),created,'Отмена закрывает все рабочие потоки');
    await page.call('Page.removeScriptToEvaluateOnNewDocument',{identifier:holdPixels.identifier});
    await page.call('Page.navigate',{url:`${base}/lobbies/${lobby.id}/page`});
    await page.until(`window.worldLoadMetrics?.complete && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'повтор после отмены фоновых задач');
    fs.writeFileSync(path.join(safeSession.artifacts,'world-loading.json'),JSON.stringify({samples,state,cancelAndReopen:true,cancelPixelsAndReopen:true,stoppedPixelWorkers:created},null,2));
    console.log('Отмена фоновых расчётов, закрытие потоков и повторное открытие: пройдены.');
};
