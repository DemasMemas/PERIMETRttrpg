const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/lobby3d.js'), 'utf8');

test('облачное поле воспроизводимо и хранит раздельную плотность и освещение', () => {
    const start = source.indexOf('function createCloudTexture()');
    const end = source.indexOf('// Создаём текстуру один раз', start);
    const context = vm.createContext({
        document: {
            createElement() {
                const canvas = {};
                canvas.getContext = () => ({
                    createImageData: (width, height) => ({ data: new Uint8ClampedArray(width * height * 4) }),
                    putImageData(image) { canvas.pixels = image.data; },
                });
                return canvas;
            },
        },
        THREE: { CanvasTexture: class { constructor(image) { this.image = image; } }, RepeatWrapping: 1000 },
    });
    vm.runInContext(source.slice(start, end), context);
    const first = context.createCloudTexture();
    const second = context.createCloudTexture();
    assert.deepEqual(first.image.pixels, second.image.pixels);
    const density = new Set(), lighting = new Set();
    let clear = 0, dense = 0;
    for (let i = 0; i < first.image.pixels.length; i += 4) {
        density.add(first.image.pixels[i]);
        lighting.add(first.image.pixels[i + 1]);
        if (first.image.pixels[i + 3] < 32) clear++;
        if (first.image.pixels[i + 3] > 180) dense++;
    }
    assert.ok(density.size > 100);
    assert.ok(lighting.size > 100);
    assert.ok(clear > 1000 && dense > 1000, 'Есть и разрывы, и плотные облака');
    assert.equal(first.wrapS, 1000);
    assert.equal(first.wrapT, 1000);
});

test('initial neighboring chunks coalesce terrain and road rebuilds', () => {
    const start = source.indexOf('const pendingWorldTerrainChunks = new Set();');
    const end = source.indexOf('// ---', start);
    assert.ok(start >= 0 && end > start);
    const chunks = new Map();
    for (let x = 0; x < 2; x++) {
        for (let y = 0; y < 2; y++) {
            chunks.set(`${x},${y}`, { chunkX: x, chunkY: y, hasRoadNodes: true });
        }
    }
    const terrain = [];
    const roads = [];
    let scheduled = 0;
    const context = vm.createContext({
        chunksMap: chunks,
        CHUNK_SIZE: 32,
        brushActive: false,
        lastHoverRaycastAt: 0,
        performance: { now: () => 1 },
        window: {},
        setTimeout() { scheduled++; return scheduled; },
        clearTimeout() {},
        refreshWorldTerrainChunk(entry) { terrain.push(`${entry.chunkX},${entry.chunkY}`); },
        rebuildRoadMeshes(entry) { roads.push(`${entry.chunkX},${entry.chunkY}`); },
    });
    vm.runInContext(source.slice(start, end), context);
    for (let x = 0; x < 2; x++) {
        for (let y = 0; y < 2; y++) {
            context.refreshWorldTerrainNeighborhood(x, y, null, null, true, true, true, true, true);
        }
    }
    assert.equal(scheduled, 1);
    assert.equal(terrain.length, 0);
    context.flushWorldTerrainRefresh();
    assert.equal(terrain.length, 4);
    assert.equal(roads.length, 4);
    assert.ok([...chunks.values()].every(chunk => chunk.waterShoreDirty && chunk.terrainTextureRegion));
});

test('начальная загрузка объединяет разные сетевые пакеты и сохраняет живые правки', async () => {
    const start=source.indexOf('const pendingWorldTerrainChunks = new Set();');
    const end=source.indexOf('// ---',start);
    const chunks=new Map(),timers=[],built=[],roads=[];
    const context=vm.createContext({chunksMap:chunks,CHUNK_SIZE:32,brushActive:false,lastHoverRaycastAt:0,
        performance:{now:()=>1},window:{},setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){},
        refreshWorldTerrainChunk(entry){built.push([entry.chunkX,entry.changed]);},
        rebuildRoadMeshes(entry){roads.push(entry.chunkX);}});
    vm.runInContext(source.slice(start,end),context);
    const finish=context.beginWorldTerrainLoad();
    chunks.set('0,0',{chunkX:0,chunkY:0,hasRoadNodes:true});
    context.refreshWorldTerrainNeighborhood(0,0,null,null,true,true,true,true,true);
    timers.shift()(); // Таймер между двумя HTTP-ответами не строит неполный рельеф.
    assert.equal(built.length,0);
    chunks.get('0,0').changed='правка другого участника';
    chunks.set('1,0',{chunkX:1,chunkY:0,hasRoadNodes:true});
    context.refreshWorldTerrainNeighborhood(1,0,null,null,true,true,true,true,true);
    const ready=context.waitForWorldTerrainRefresh();
    finish();finish(); // Повторное завершение не снимает блокировку другого запуска.
    timers.shift()();
    await ready;
    assert.deepEqual(built,[[0,'правка другого участника'],[1,undefined]]);
    assert.deepEqual(roads,[0,1]);
    const laterFinish=context.beginWorldTerrainLoad();
    context.refreshWorldTerrainNeighborhood(0,0,null,null,true,true,true,true,true);
    timers.shift()();
    assert.equal(built.length,2);
    laterFinish();timers.shift()();
    assert.equal(built.length,4);
    const cancelled=context.beginWorldTerrainLoad();
    context.refreshWorldTerrainNeighborhood(0,0,null,null,true,true,true,true,true);
    timers.shift()();
    cancelled({defer:true});
    assert.equal(timers.length,0,'Отмена не запускает тяжёлое построение в покидаемой сцене');
    assert.equal(built.length,4);
    const resumed=context.beginWorldTerrainLoad();resumed();timers.shift()();
    assert.equal(built.length,6,'Повторное открытие завершает сохранённую очередь');
});

test('anomaly LOD creates only one full effect; the distant marker is batched separately', () => {
    const start = source.indexOf('function createAnomalyLOD(');
    const end = source.indexOf('\nconst FAR_ANOMALY_DISTANCE_SQ', start);
    assert.ok(start >= 0 && end > start);
    let effectsCreated = 0;
    const context = vm.createContext({
        createAnomalyEffect() { effectsCreated += 1; return { position: { set() {} } }; },
        THREE: {
            Color: class { constructor(value) { this.value = value; } },
            LOD: class {
                constructor() { this.levels = []; this.position = { set() {} }; this.userData = {}; }
                addLevel(group, distance) { this.levels.push({ group, distance }); }
            },
        },
    });
    vm.runInContext(source.slice(start, end), context);
    const lod = context.createAnomalyLOD(1, 2, 3, 'electric', '#ffffff', 2);
    assert.equal(effectsCreated, 1);
    assert.equal(lod.levels.length, 1);
    assert.equal(lod.userData.cullRadius, 8);
});

test('static world shadows are refreshed only when caster visibility changes', () => {
    const markStart = source.indexOf('function markWorldShadowsDirty()');
    const markEnd = source.indexOf('\nlet isMouseDown', markStart);
    const visibleStart = source.indexOf('function setVisible(chunk, visible,');
    const visibleEnd = source.indexOf('\nexport function setEditMode', visibleStart);
    assert.ok(markStart >= 0 && markEnd > markStart && visibleStart >= 0 && visibleEnd > visibleStart);
    const renderer = { isUnavailableRenderer: false, shadowMap: { needsUpdate: false } };
    const context = vm.createContext({ renderer, updateFarAnomalyInstances() {} });
    vm.runInContext('let worldShadowsDirty = false; let lastWorldShadowAt = -Infinity; let shadowRefreshesSinceSample = 0; const SHADOW_REFRESH_MS = 250;'
        + source.slice(markStart, markEnd) + source.slice(visibleStart, visibleEnd), context);
    const chunk = { ground: { visible: true }, trees: { visible: true }, houses: { visible: true } };
    context.setVisible(chunk, true, true, true, true);
    context.setVisible(chunk, true, false, true, false);
    context.refreshWorldShadows(99, true);
    assert.equal(renderer.shadowMap.needsUpdate, false);
    context.setVisible(chunk, false);
    assert.equal(renderer.shadowMap.needsUpdate, false);
    context.refreshWorldShadows(100, true);
    assert.equal(renderer.shadowMap.needsUpdate, true);
    assert.equal(chunk.trees.visible, false);
    renderer.shadowMap.needsUpdate = false;
    context.setVisible(chunk, true, false, true, false);
    assert.equal(chunk.ground.visible, true);
    assert.equal(chunk.trees.visible, false);
    context.refreshWorldShadows(200, true);
    assert.equal(renderer.shadowMap.needsUpdate, false);
    context.refreshWorldShadows(350, true);
    assert.equal(renderer.shadowMap.needsUpdate, true);
    renderer.shadowMap.needsUpdate = false;
    context.setVisible(chunk, false);
    context.refreshWorldShadows(351, false);
    assert.equal(renderer.shadowMap.needsUpdate, true);
    assert.match(source, /renderer\.shadowMap\.autoUpdate = false/);
});

test('Незначительный рельеф не отбрасывает полосы, а высокие грани сохраняются в проходе теней', () => {
    const start=source.indexOf('function partitionWorldTerrainShadowIndices(');
    const end=source.indexOf('\nfunction refreshWorldTerrainSurface(',start);
    const heights=[1,1.05,1,1.1,1.7,1.1,1.7,1.7,1.7];
    const geometry={index:{array:new Uint16Array([0,1,2,3,4,5,6,7,8])},
        attributes:{position:{getY:index=>heights[index]}},userData:{}};
    const context=vm.createContext({worldTerrainMinShadowRise:.075});
    vm.runInContext(source.slice(start,end),context);
    assert.equal(context.partitionWorldTerrainShadowIndices(geometry),true);
    assert.equal(geometry.userData.terrainShadowStart,6);
    assert.deepEqual([...geometry.index.array.slice(6)],[3,4,5]);
    assert.deepEqual([...geometry.index.array].sort((a,b)=>a-b),[0,1,2,3,4,5,6,7,8]);
    const low={index:{array:new Uint16Array([0,1,2])},attributes:geometry.attributes,userData:{}};
    assert.equal(context.partitionWorldTerrainShadowIndices(low),false);
});

test('Близкие тени и средние модели сохраняют точку опоры подробного дерева', async () => {
    const THREE = await import('../app/static/vendor/three-0.162.0/build/three.module.min.js');
    const { getTreeGeometry } = await import('../app/static/js/locationHouseholdModels.js');
    for (const species of ['oak', 'birch', 'pine']) {
        const id = `${species}_green`;
        const full = getTreeGeometry(THREE, id);
        const shadow = getTreeGeometry(THREE, id, 'nearShadow');
        const middle = getTreeGeometry(THREE, id, 'middle');
        for (const geometry of [middle, shadow]) {
            assert.ok(geometry.attributes.position.count < full.attributes.position.count * .20);
            const trunk = geometry.userData.movementTrunk;
            const reference = full.userData.movementTrunk;
            for (let axis = 0; axis < 3; axis++) {
                assert.ok(Math.abs((trunk.min[axis] + trunk.max[axis] - reference.min[axis] - reference.max[axis]) / 2) < .016);
            }
        }
    }
});

test('Камера теней уточняет близкий рельеф и сохраняет направление света при перемещении обзора', async () => {
    const THREE=await import('../app/static/vendor/three-0.162.0/build/three.module.min.js');
    const start=source.indexOf('let worldShadowRadius =');
    const end=source.indexOf('\nconst fillLight',start);
    const camera=new THREE.PerspectiveCamera(45,16/9,.1,5000);
    camera.position.set(32,15,32);
    const controls={target:new THREE.Vector3(15,1,15)},light=new THREE.DirectionalLight();
    light.position.set(256,300,256);light.shadow.mapSize.set(4096,4096);
    const renderer={shadowMap:{needsUpdate:false}};
    let invalidations=0;
    const context=vm.createContext({THREE,camera,controls,directionalLight:light,renderer,
        markWorldShadowsDirty(){invalidations++;}});
    const direction=light.position.clone().sub(light.target.position);
    vm.runInContext(source.slice(start,end),context);
    context.fitWorldShadowCamera();
    assert.ok(light.shadow.camera.right<50);
    assert.ok(light.position.clone().sub(light.target.position).distanceTo(direction)<1e-9);
    assert.equal(renderer.shadowMap.needsUpdate,false,'Перенос камеры теней ждёт общего планировщика');
    context.fitWorldShadowCamera();
    assert.equal(renderer.shadowMap.needsUpdate,false);assert.equal(invalidations,1);
    camera.position.x+=20;controls.target.x+=20;context.fitWorldShadowCamera();
    assert.equal(invalidations,2);
    assert.ok(light.position.clone().sub(light.target.position).distanceTo(direction)<1e-9);
});

test('полуденное солнце сохраняет тень перепада высоты на глобальной карте', async () => {
    const THREE=await import('../app/static/vendor/three-0.162.0/build/three.module.min.js');
    const begin=source.indexOf('function updateCelestialPositions()');
    const end=source.indexOf('\nexport function setCelestialTime',begin);
    const light=new THREE.DirectionalLight();
    const context=vm.createContext({THREE,celestialMinutes:780,MIN_WORLD_SHADOW_LIGHT_Y:.35,MAX_WORLD_SHADOW_LIGHT_SLOPE:1,
        sunDisc:{position:new THREE.Vector3()},moonDisc:{position:new THREE.Vector3()},stars:{rotation:{}},
        worldSkySphere:{material:{uniforms:{starRotation:{value:0}}}},
        cloudField:{worldCloudProjection:{value:new THREE.Vector2()}},MAX_CHUNK_X:1,MAX_CHUNK_Y:1,CHUNK_SIZE:32,
        directionalLight:light,markWorldShadowsDirty(){}});
    vm.runInContext(source.slice(begin,end),context);
    context.updateCelestialPositions();
    const direction=light.position.clone().sub(light.target.position);
    const projectedLength=1.5*Math.hypot(direction.x,direction.z)/direction.y;
    assert.ok(projectedLength>=1.5-1e-9);
    assert.ok(direction.y>0);
});

test('distant anomalies share one per-chunk draw while nearby effects remain individual', () => {
    const start = source.indexOf('const FAR_ANOMALY_DISTANCE_SQ =');
    const end = source.indexOf('\nfunction animateChunkAnomalies', start);
    assert.ok(start >= 0 && end > start);
    const added = [];
    class InstancedMesh {
        constructor(geometry, material, capacity) {
            this.capacity = capacity;
            this.instanceMatrix = {};
            this.matrices = [];
            this.colors = [];
            this.matrixWrites = 0;
        }
        setMatrixAt(index, matrix) { this.matrices[index] = matrix; this.matrixWrites += 1; }
        setColorAt(index, color) { this.colors[index] = color; }
    }
    const camera = { position: { x: 0 } };
    const context = vm.createContext({
        camera,
        chunkFrustum: { intersectsSphere(sphere) { return sphere.center.x !== 300; } },
        anomalyVisibilitySphere: { center: { copy(position) { this.x = position.x; } }, radius: 0 },
        scene: { add(mesh) { added.push(mesh); } },
        THREE: {
            SphereGeometry: class {}, MeshBasicMaterial: class {}, InstancedMesh,
            InstancedBufferAttribute: class {},
            Object3D: class {
                constructor() {
                    this.position = { copy(position) { this.x = position.x; } };
                    this.scale = { setScalar(scale) { this.value = scale; } };
                }
                updateMatrix() { this.matrix = { x: this.position.x, scale: this.scale.value }; }
            },
        },
    });
    vm.runInContext(source.slice(start, end), context);
    const visibleNearAnomalies = vm.runInContext('visibleNearAnomalies', context);
    const lod = (x, color) => ({
        position: { x, distanceToSquared(other) { return (x - other.x) ** 2; } },
        userData: { farScale: 1, farColor: color, cullRadius: 4 },
        visible: true,
    });
    const chunk = { anomalyLODs: [lod(50, 'red'), lod(250, 'blue'), lod(300, 'green')], farAnomalyActive: [] };
    chunk.farAnomalies = context.createFarAnomalyInstances(chunk.anomalyLODs);
    assert.equal(added.length, 1);
    assert.equal(chunk.farAnomalies.capacity, 3);
    context.updateFarAnomalyInstances(chunk, true);
    assert.equal(chunk.farAnomalies.count, 1);
    assert.equal(chunk.farAnomalies.visible, true);
    assert.equal(chunk.anomalyLODs[0].visible, true);
    assert.equal(chunk.anomalyLODs[1].visible, false);
    assert.equal(chunk.anomalyLODs[2].visible, false);
    assert.deepEqual(chunk.farAnomalies.matrices.slice(0, 1), [{ x: 250, scale: 1 }]);
    assert.equal(visibleNearAnomalies.length, 1);
    assert.equal(visibleNearAnomalies[0], chunk.anomalyLODs[0]);
    const previousWrites = chunk.farAnomalies.matrixWrites;
    context.updateFarAnomalyInstances(chunk, true);
    assert.equal(chunk.farAnomalies.matrixWrites, previousWrites);
    context.updateFarAnomalyInstances(chunk, false);
    assert.equal(chunk.farAnomalies.visible, false);
    assert.ok(chunk.anomalyLODs.every(anomaly => !anomaly.visible));
    camera.position.x = 250;
    visibleNearAnomalies.length = 0;
    context.updateFarAnomalyInstances(chunk, true);
    assert.equal(chunk.farAnomalies.count, 1);
    assert.equal(chunk.farAnomalies.visible, true);
    assert.equal(chunk.anomalyLODs[0].visible, false);
    assert.equal(chunk.anomalyLODs[1].visible, true);
    assert.equal(chunk.anomalyLODs[2].visible, false);
    assert.equal(visibleNearAnomalies.length, 1);
    assert.equal(visibleNearAnomalies[0], chunk.anomalyLODs[1]);
});

test('only the visible-near list is animated each frame', () => {
    const start = source.indexOf('function animateChunkAnomalies(');
    const end = source.indexOf('\n}', start) + 2;
    assert.ok(start >= 0 && end > start);
    const near = [{}];
    const calls = [];
    const context = vm.createContext({
        visibleNearAnomalies: near,
        animateAnomalyEffects(roots, time) { calls.push([roots, time]); },
    });
    vm.runInContext(source.slice(start, end), context);
    context.animateChunkAnomalies(1234);
    assert.deepEqual(calls, [[near, 1234]]);
});

test('camera visibility pass rebuilds the near-anomaly list instead of accumulating stale entries', () => {
    const start = source.indexOf('function updateChunkVisibility()');
    const end = source.indexOf('\nfunction setVisible(', start);
    assert.ok(start >= 0 && end > start);
    const near = ['stale'];
    const chunk = { bounds: {} };
    const context = vm.createContext({
        camera: { projectionMatrix: {}, matrixWorldInverse: {}, updateMatrixWorld() {} },
        chunkFrustum: { setFromProjectionMatrix() {}, intersectsBox() { return true; } },
        chunkProjectionMatrix: { multiplyMatrices() { return this; } },
        visibleNearAnomalies: near,
        chunksMap: new Map([['one', chunk]]), controls: { target: {} },
        TERRAIN_RENDER_DISTANCE: 380, NATURAL_DETAIL_DISTANCE: 190,
        STRUCTURE_DETAIL_DISTANCE: 280, ANOMALY_DETAIL_DISTANCE: 320,
        chunkWithinHorizontalDistance() { return true; },
        refreshWorldObjectBounds() {},
        setVisible() { near.push('visible'); },
    });
    vm.runInContext(source.slice(start, end), context);
    context.updateChunkVisibility();
    assert.deepEqual(near, ['visible']);
    context.updateChunkVisibility();
    assert.deepEqual(near, ['visible']);
});

test('верх объекта остаётся видимым, когда земля его чанка вышла из кадра', () => {
    const start = source.indexOf('function updateChunkVisibility()');
    const end = source.indexOf('\nfunction setVisible(', start);
    const objectBounds = { isEmpty: () => false };
    const chunk = { bounds: {}, objectBounds };
    let visibility;
    const context = vm.createContext({
        camera: { projectionMatrix: {}, matrixWorldInverse: {}, updateMatrixWorld() {} },
        chunkFrustum: { setFromProjectionMatrix() {}, intersectsBox: box => box === objectBounds },
        chunkProjectionMatrix: { multiplyMatrices() { return this; } },
        visibleNearAnomalies: [], chunksMap: new Map([['one', chunk]]), controls: { target: {} },
        TERRAIN_RENDER_DISTANCE: 380, NATURAL_DETAIL_DISTANCE: 190,
        STRUCTURE_DETAIL_DISTANCE: 280, ANOMALY_DETAIL_DISTANCE: 320,
        chunkWithinHorizontalDistance: () => true, refreshWorldObjectBounds() {},
        setVisible(...args) { visibility = args; },
    });
    vm.runInContext(source.slice(start, end), context);
    context.updateChunkVisibility();
    assert.equal(visibility[1], true);
    assert.equal(visibility[2], true);
    assert.equal(visibility[5], false);
    objectBounds.isEmpty = () => true;
    context.updateChunkVisibility();
    assert.equal(visibility[1], false, 'Пустые границы не включают чанк');
});

test('removing anomaly LODs releases their private geometry, materials and label textures', () => {
    const start = source.indexOf('function disposeChunkAnomalyLODs(');
    const end = source.indexOf('\nfunction updateFarAnomalyInstances(', start);
    assert.ok(start >= 0 && end > start);
    const disposed = [];
    const removed = [];
    const context = vm.createContext({ scene: { remove(lod) { removed.push(lod); } } });
    vm.runInContext(source.slice(start, end), context);
    const node = {
        geometry: { dispose() { disposed.push('geometry'); } },
        material: {
            map: { dispose() { disposed.push('texture'); } },
            dispose() { disposed.push('material'); },
        },
    };
    const lod = { traverse(callback) { callback(node); } };
    context.disposeChunkAnomalyLODs([lod]);
    assert.deepEqual(removed, [lod]);
    assert.deepEqual(disposed, ['geometry', 'texture', 'material']);
});

test('camera drag defers expensive world picking without blocking brush movement', () => {
    const start = source.indexOf("window.addEventListener('pointermove', (event) => {");
    const end = source.indexOf("window.addEventListener('keydown'", start);
    assert.ok(start >= 0 && end > start);
    const handlers = {};
    const calls = [];
    const context = vm.createContext({
        window: {
            isLocationActive: false,
            worldEditorTool: 'terrain', currentTileType: 'grass', brushRadius: 0,
            addEventListener(name, handler) { handlers[name] = handler; },
            applyBrush() { calls.push('brush'); },
        },
        lastMouseX: 0, lastMouseY: 0, lastModifiers: {}, cameraDragActive: false,
        hoverRaycastPending: false, editMode: true, brushActive: true,
        lastProcessedTileKey: null,
        hoveredTile: { chunk: { chunkX: 0, chunkY: 0 }, tileX: 1, tileY: 1 },
        performRaycast() { calls.push('raycast'); },
    });
    vm.runInContext(source.slice(start, end), context);
    handlers.pointermove({ clientX: 10, clientY: 20, buttons: 2, altKey: false, shiftKey: false });
    assert.deepEqual(calls, []);
    assert.equal(context.cameraDragActive, true);
    assert.equal(context.hoverRaycastPending, true);
    context.hoverRaycastPending = false;
    handlers.pointerup();
    assert.equal(context.cameraDragActive, false);
    assert.equal(context.hoverRaycastPending, true);
    handlers.pointermove({ clientX: 11, clientY: 20, buttons: 1, altKey: true, shiftKey: false });
    assert.deepEqual(calls, ['raycast', 'brush']);
    handlers.pointermove({ clientX: 11, clientY: 20, buttons: 1, altKey: true, shiftKey: false });
    assert.deepEqual(calls, ['raycast', 'brush', 'raycast']);
    assert.equal(context.cameraDragActive, false);
});

test('world distance is horizontal and keeps terrain beyond smaller detail ranges', () => {
    const start = source.indexOf('function chunkWithinHorizontalDistance(');
    const end = source.indexOf('\n}', start) + 2;
    assert.ok(start >= 0 && end > start);
    const context = vm.createContext({});
    vm.runInContext(source.slice(start, end), context);
    const bounds = { min: { x: 100, z: 100 }, max: { x: 132, z: 132 } };
    assert.equal(context.chunkWithinHorizontalDistance(bounds, { x: 116, y: 10000, z: 116 }, 190), true);
    assert.equal(context.chunkWithinHorizontalDistance(bounds, { x: 350, y: 0, z: 116 }, 190), false);
    assert.equal(context.chunkWithinHorizontalDistance(bounds, { x: 350, y: 0, z: 116 }, 380), true);
    assert.match(source, /renderer\.setPixelRatio\(Math\.min\(window\.devicePixelRatio \|\| 1, 1\.5\)\)/);
    assert.match(source, /chunkBounds\.filter\(entry => entry\.mesh\.visible && raycaster\.ray\.intersectsBox\(entry\.box\)\)/);
});
