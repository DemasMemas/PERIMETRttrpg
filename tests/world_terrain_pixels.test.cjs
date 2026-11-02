const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/worldTerrainPixels.js'),'utf8');
const url='data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const tick=()=>new Promise(setImmediate);

function fakePool(){
    const workers=[];
    const createWorker=()=>{
        const worker={jobs:[],terminated:0,
            postMessage(data){if(data.initialize)queueMicrotask(()=>this.onmessage({data:{ready:true}}));else this.jobs.push(...data.jobs);},
            complete(){const job=this.jobs.shift();this.onmessage({data:{index:job.index,image:{name:job.task.name}}});},
            terminate(){this.terminated++;}};
        workers.push(worker);return worker;
    };
    return {workers,createWorker};
}

test('пул ограничивает параллелизм, прогревает материалы после старта задач и закрывает потоки',async()=>{
    const {prepareWorldTerrainPixelImages}=await import(url),pool=fakePool(),results=[],progress=[];
    let started=0;
    const tasks=Array.from({length:5},(_,i)=>({name:'chunk-'+i}));
    const done=prepareWorldTerrainPixelImages(tasks,{createWorker:pool.createWorker,concurrency:2,
        onStarted(){started++;assert.ok(pool.workers.every(w=>w.jobs.length>0));assert.equal(pool.workers.reduce((n,w)=>n+w.jobs.length,0),5);},
        onResult(task,data){results.push(task.name);assert.equal(data.image.name,task.name);},onProgress:f=>progress.push(f)});
    await tick();
    assert.equal(pool.workers.length,2);assert.equal(started,1);
    pool.workers[1].complete();pool.workers[0].complete();pool.workers[0].complete();pool.workers[1].complete();pool.workers[0].complete();
    await done;
    assert.equal(new Set(results).size,5);assert.equal(progress.at(-1),1);
    assert.ok(pool.workers.every(w=>w.terminated===1));
});

test('отмена останавливает все потоки и игнорирует запоздавшие результаты',async()=>{
    const {prepareWorldTerrainPixelImages}=await import(url),pool=fakePool(),controller=new AbortController();
    let results=0;
    const done=prepareWorldTerrainPixelImages([{name:'a'},{name:'b'},{name:'c'}],{
        signal:controller.signal,createWorker:pool.createWorker,concurrency:2,onResult(){results++;}});
    await tick();controller.abort();await done;
    pool.workers.forEach(w=>w.complete());
    assert.equal(results,0);assert.ok(pool.workers.every(w=>w.terminated===1));
});

test('ошибка потока или запрет Worker освобождает весь созданный пул',async()=>{
    const {prepareWorldTerrainPixelImages}=await import(url),pool=fakePool();
    const done=prepareWorldTerrainPixelImages([{name:'a'},{name:'b'}],{createWorker:pool.createWorker,concurrency:2});
    await tick();pool.workers[0].onerror(new Error('Не загружен модуль'));
    await assert.rejects(done,/Не загружен модуль/);
    assert.ok(pool.workers.every(w=>w.terminated===1));
    const partial=fakePool();let created=0;
    await assert.rejects(prepareWorldTerrainPixelImages([{name:'a'},{name:'b'}],{
        concurrency:2,createWorker(){if(created++)throw Error('Worker запрещён');return partial.createWorker();}}),/Worker запрещён/);
    assert.equal(partial.workers[0].terminated,1);
});

test('отменённая заранее или пустая подготовка не запускает поток',async()=>{
    const {prepareWorldTerrainPixelImages}=await import(url),controller=new AbortController();controller.abort();
    const options={createWorker(){assert.fail('Лишний поток');},signal:controller.signal};
    await prepareWorldTerrainPixelImages([{name:'a'}],options);
    await prepareWorldTerrainPixelImages([],options);
});

test('правка рельефа во время подготовки отбрасывает устаревшие пиксели и оставляет штатный расчёт',async()=>{
    const scene=fs.readFileSync(path.join(__dirname,'../app/static/js/lobby3d.js'),'utf8');
    const start=scene.indexOf('async function prepareInitialWorldTerrainPixels('),end=scene.indexOf('\nfunction refreshWorldTerrainPixels(',start);
    const chunks=new Map(Array.from({length:8},(_,x)=>[`${x},0`,{chunkX:x,chunkY:0,tilesData:[[{terrain:'grass'}]]}]));
    const context=vm.createContext({chunksMap:chunks,Worker:class{},performance,window:{worldLoadMetrics:{}},CHUNK_SIZE:32,
        worldGrassGroundPixelsEnabled:false,getGrassGroundPixelData:()=>({plain:null,forest:null}),
        sampleWorldTerrainKind:()=> 'grass',prepareWorldTerrainSurfaceShader(){},console:{warn(){}},
        async prepareWorldTerrainPixelImages(tasks,options){
            chunks.get('0,0').terrainPixelRevision=1;
            for(const task of tasks)options.onResult(task,{image:{data:[1]},profile:{},workMs:1});
        }});
    vm.runInContext(scene.slice(start,end),context);
    await context.prepareInitialWorldTerrainPixels();
    assert.equal(chunks.get('0,0').preparedTerrainPixels,undefined);
    assert.ok(chunks.get('1,0').preparedTerrainPixels);
    context.prepareWorldTerrainPixelImages=async()=>{throw Error('Worker не поддерживается');};
    await context.prepareInitialWorldTerrainPixels(); // Ошибка не выходит в загрузчик карты.
});
