const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const main = fs.readFileSync(path.join(__dirname, '../app/static/js/main.js'), 'utf8');
const helpers = main.slice(main.indexOf('let sceneLoadingGeneration = 0;'),
    main.indexOf('function escapeHtml('));
const worldLoad = main.slice(main.indexOf('function resumeWorldMapLoad()'),
    main.indexOf('function setLocationViewActive('));

function harness() {
    const classes = new Set();
    const screen = { classList: {
        add(name) { classes.add(name); },
        remove(...names) { names.forEach(name => classes.delete(name)); },
    } };
    const title = { textContent: '' };
    const detail = { textContent: '' };
    const retry = { hidden: true };
    const cancel = { hidden: false, textContent: '' };
    const percent = { textContent: '' };
    const fill = { style: {} };
    const progress = { setAttribute(name, value) { this[name] = value; } };
    const elements = new Map([
        ['scene-loading-screen', screen],
        ['scene-loading-title', title],
        ['scene-loading-detail', detail],
        ['scene-loading-retry', retry],
        ['scene-loading-cancel', cancel],
        ['scene-loading-percent', percent],
        ['scene-loading-fill', fill],
        ['scene-loading-progress', progress],
        ['lobby-name', { textContent: 'Тестовое лобби' }],
    ]);
    const frames = [];
    const context = vm.createContext({
        AbortController,
        document: { getElementById: id => elements.get(id) },
        requestAnimationFrame: callback => frames.push(callback),
        window: { isLocationActive: false, worldLoadMetrics: { complete: false }, location: {} },
        console: { error() {} },
    });
    vm.runInContext(`${helpers}\n${worldLoad}`, context);
    const flushFrames = () => {
        while (frames.length) frames.shift()();
    };
    return { context, classes, title, detail, retry, cancel, percent, fill, progress, flushFrames };
}

test('an old loading completion cannot hide the next scene or its error', () => {
    const { context, classes, title, retry, flushFrames } = harness();
    const world = context.showSceneLoading('world');
    context.finishSceneLoading(world);
    const location = context.showSceneLoading('location');
    flushFrames();
    assert.equal(title.textContent, 'Загрузка…');
    context.setSceneLoadingLocationName(location, 'Деревня');
    assert.equal(title.textContent, 'Деревня');
    assert.equal(classes.has('is-hidden'), false);
    context.failSceneLoading(world, 'old error');
    assert.equal(retry.hidden, true);
    context.setSceneLoadingLocationName(world, 'Старое название');
    assert.equal(title.textContent, 'Деревня');
    context.finishSceneLoading(location);
    flushFrames();
    assert.equal(classes.has('is-hidden'), true);
    const retryWorld = context.showSceneLoading('world');
    context.failSceneLoading(retryWorld, 'Загрузка не удалась');
    assert.equal(classes.has('is-hidden'), false);
    assert.equal(classes.has('is-error'), true);
    assert.equal(retry.hidden, false);
});

test('progress is monotonic, accessible and belongs to the current scene', () => {
    const { context, title, percent, fill, progress } = harness();
    const world = context.showSceneLoading('world');
    assert.equal(title.textContent, 'Тестовое лобби');
    context.setSceneLoadingProgress(world, 42.3);
    context.setSceneLoadingProgress(world, 12);
    assert.equal(percent.textContent, '42%');
    assert.equal(fill.style.width, '42%');
    assert.equal(progress['aria-valuenow'], '42');
    context.showSceneLoading('location');
    context.setSceneLoadingProgress(world, 100);
    assert.equal(percent.textContent, '0%');
});

test('cancelling aborts the request and rejects late completion or errors', () => {
    const { context, classes, retry, flushFrames } = harness();
    const world = context.showSceneLoading('world');
    const signal = vm.runInContext('sceneLoadingController.signal', context);
    context.window.cancelSceneLoading();
    assert.equal(signal.aborted, true);
    assert.equal(context.window.location.href, '/');
    context.finishSceneLoading(world);
    context.failSceneLoading(world, 'late failure');
    flushFrames();
    assert.equal(classes.has('is-hidden'), false);
    assert.equal(retry.hidden, true);
});

test('location cancellation returns to the map and remains available on failure', () => {
    const { context, cancel } = harness();
    context.window.MAP_CHUNKS_WIDTH = 2;
    let exits = 0;
    context.window.exitLocation = () => { exits++; };
    const location = context.showSceneLoading('location');
    context.failSceneLoading(location, 'failed');
    assert.equal(cancel.hidden, false);
    context.window.cancelSceneLoading();
    assert.equal(exits, 1);
    assert.equal(context.isCurrentSceneLoading(location), false);
    assert.equal(context.window.locationDirectOpenPending, false);
});

test('world screen waits for all chunks and retries after a paused location load', async () => {
    const { context, classes, retry, flushFrames } = harness();
    let loads = 0;
    let travelRefreshes = 0;
    context.loadAllChunks = async () => {
        loads++;
        context.window.worldLoadMetrics = loads === 1
            ? { pausedForLocation: true, complete: false }
            : { pausedForLocation: false, complete: true };
    };
    context.refreshWorldTravel = () => { travelRefreshes++; };
    context.resumeWorldMapLoad();
    await new Promise(resolve => setImmediate(resolve));
    flushFrames();
    assert.equal(loads, 2);
    assert.equal(travelRefreshes, 1);
    assert.equal(classes.has('is-hidden'), true);
    context.loadAllChunks = async () => {
        context.window.worldLoadMetrics = { complete: false };
    };
    context.resumeWorldMapLoad();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(classes.has('is-error'), true);
    assert.equal(retry.hidden, false);
});

function chunksHarness() {
    const source = fs.readFileSync(path.join(__dirname, '../app/static/js/lobbyData.js'), 'utf8');
    let finishTerrain;
    const terrain = new Promise(resolve => { finishTerrain = resolve; });
    let respond;
    const response = new Promise(resolve => { respond = resolve; });
    const chunksMap = new Map();
    const context = vm.createContext({
        window: { MAP_CHUNKS_WIDTH: 1, MAP_CHUNKS_HEIGHT: 1 },
        currentLobbyId: 1, chunksMap,
        performance: { now: () => 1, mark() {}, measure() {} },
        Server: { getChunks: () => response },
        loadGrassGroundPixels: async () => {},
        setWorldGrassGroundPixelsEnabled() {},
        waitForWorldTerrainRefresh: () => terrain,
        beginWorldTerrainLoad: () => () => {},
        prepareInitialWorldTerrainPixels: async () => {},
        addChunk(x, y) { chunksMap.set(`${x},${y}`, {}); },
        console: { info() {}, error() {}, warn() {} },
    });
    vm.runInContext(source.slice(source.indexOf('let loadingAllChunks = null;')).replace('export ', ''), context);
    return { context, chunksMap, finishTerrain, respond: () => respond([{ chunk_x: 0, chunk_y: 0, data: [] }]) };
}

test('world progress reaches 100 only after terrain assembly, including shared callers', async () => {
    const { context, respond, finishTerrain } = chunksHarness();
    const samples = [];
    const loading = context.loadAllChunks();
    assert.equal(context.loadAllChunks({ onProgress: value => samples.push(value) }), loading);
    respond();
    await new Promise(setImmediate);
    assert.equal(samples.at(-1), 90);
    assert.equal(context.window.worldLoadMetrics.complete, undefined);
    finishTerrain();
    await loading;
    assert.equal(samples.at(-1), 100);
    assert.equal(context.window.worldLoadMetrics.complete, true);
});

test('aborted world load discards late chunks and can be restarted', async () => {
    const { context, chunksMap, respond, finishTerrain } = chunksHarness();
    const controller = new AbortController();
    const loading = context.loadAllChunks({ signal: controller.signal });
    controller.abort();
    const retry = context.loadAllChunks();
    respond();
    await loading;
    assert.equal(chunksMap.size, 0);
    finishTerrain();
    await retry;
    assert.equal(chunksMap.size, 1);
    assert.equal(context.window.worldLoadMetrics.complete, true);
});

test('отмена сохраняет готовые чанки и отбрасывает запоздавший пакет; повтор достраивает карту', async () => {
    const { context, chunksMap, finishTerrain } = chunksHarness();
    let finished=0;
    context.beginWorldTerrainLoad=()=>()=>{finished++;};
    context.window.MAP_CHUNKS_WIDTH=4;
    const responses=[];
    context.Server.getChunks=(id,minX,maxX,minY,maxY)=>new Promise(resolve=>responses.push(()=>resolve(
        Array.from({length:maxX-minX+1},(_,i)=>({chunk_x:minX+i,chunk_y:0,data:[]}))
    )));
    // Сохранённый ранее чанк остаётся доступен после отмены.
    chunksMap.set('0,0',{});
    const controller=new AbortController();
    const loading=context.loadAllChunks({signal:controller.signal});
    responses.shift()();
    await new Promise(setImmediate);
    assert.equal(context.window.worldLoadMetrics.requests,1);
    assert.equal(chunksMap.size,2);
    controller.abort();
    responses.shift()();
    await loading;
    assert.equal(finished,1,'Отмена освобождает очередь рельефа');
    assert.deepEqual([...chunksMap.keys()],['0,0','1,0']);
    const retry=context.loadAllChunks();
    while(responses.length)responses.shift()();
    finishTerrain();
    await retry;
    assert.equal(finished,2);
    assert.equal(chunksMap.size,4);
    assert.equal(context.window.worldLoadMetrics.complete,true);
});

test('ошибка одного пакета оставляет карту неполной; повтор получает недостающие чанки', async () => {
    const { context, chunksMap, finishTerrain } = chunksHarness();
    context.window.MAP_CHUNKS_WIDTH=4;
    let fail=true;
    context.Server.getChunks=async(id,minX,maxX)=>{
        if(fail&&minX===2)throw Error('Разрыв соединения');
        return Array.from({length:maxX-minX+1},(_,i)=>({chunk_x:minX+i,chunk_y:0,data:[]}));
    };
    finishTerrain();
    await context.loadAllChunks();
    assert.equal(chunksMap.size,2);
    assert.equal(context.window.worldLoadMetrics.complete,false);
    assert.equal(context.window.worldLoadMetrics.failedRequests,1);
    fail=false;
    await context.loadAllChunks();
    assert.equal(chunksMap.size,4);
    assert.equal(context.window.worldLoadMetrics.chunks,2);
    assert.equal(context.window.worldLoadMetrics.complete,true);
});
