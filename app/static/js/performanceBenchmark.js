import * as THREE from 'three';
import { createGrassDetailGroup } from './locationGrassDetail.js';
import { createAnomalyEffect, animateAnomalyEffects } from './anomalies.js';
import { MAX_ANOMALY_RENDER_LIGHTS } from './locationAnomalyRenderLights.js';
import { ALL_MUTANT_TYPES, MODELED_MUTANT_TYPES, createMutantModel } from './mutantModels.js';
import { findLocationAsset } from './locationAssetCatalog.js';
import { mountLocationModel } from './locationModelAssets.js';
import { createCrateModel, createFenceModel } from './locationSimpleModels.js';

const view = document.getElementById('view');
const scenarioSelect = document.getElementById('scenario');
const resolutionSelect = document.getElementById('resolution');
const readout = document.getElementById('readout');
const results = document.getElementById('results');
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
renderer.useLegacyLights = true;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.setClearColor(0x8c988d);
view.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.fog = new THREE.Fog(0x8c988d, 45, 130);
scene.add(new THREE.HemisphereLight(0xe3e8cf, 0x544637, 1.15));
const sun = new THREE.DirectionalLight(0xffe3b2, 1.45);
sun.position.set(20, 30, 12);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left = sun.shadow.camera.bottom = -65;
sun.shadow.camera.right = sun.shadow.camera.top = 65;
scene.add(sun);
const camera = new THREE.PerspectiveCamera(58, 1, 0.1, 180);

const localTerrain = new THREE.Group();
const worldTerrain = new THREE.Group();
const grass = new THREE.Group();
const localAnomalies = new THREE.Group();
const worldAnomalies = new THREE.Group();
const mutants = new THREE.Group();
const assetProcedural = new THREE.Group();
const assetTest = new THREE.Group();
const assetArt = new THREE.Group();
const assetGroups = { 'asset-procedural': assetProcedural, 'asset-test-glb': assetTest, 'asset-art-glb': assetArt };
const assetLoads = { 'asset-procedural': { done: 24, errors: 0, ms: 0 },
    'asset-test-glb': { done: 0, errors: 0, ms: null },
    'asset-art-glb': { done: 0, errors: 0, ms: null } };
const builtAssetGroups = new Set();
scene.add(localTerrain, worldTerrain, grass, localAnomalies, worldAnomalies, mutants,
    assetProcedural, assetTest, assetArt);
const anomalyRoots = { local: [], world: [] };
const dummy = new THREE.Object3D();

function addTerrain(group, size, varied) {
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1),
        new THREE.MeshLambertMaterial({ color: 0xffffff }), size * size);
    for (let z = 0; z < size; z++) for (let x = 0; x < size; x++) {
        const index = z * size + x;
        const height = varied ? 0.2 + Math.floor(2 + Math.sin(x * 0.16) * Math.cos(z * 0.18)) * 0.3 : 0;
        dummy.position.set(x + 0.5, height - 0.5, z + 0.5);
        dummy.scale.set(1, 1, 1);
        dummy.updateMatrix();
        mesh.setMatrixAt(index, dummy.matrix);
        const tint = new THREE.Color().setHSL((x + z) % 13 < 3 ? 0.11 : 0.26,
            0.37, 0.28 + (index % 5) * 0.006);
        mesh.setColorAt(index, tint);
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.instanceColor.needsUpdate = true;
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    group.add(mesh);
}

function fallbackMutant() {
    const group = new THREE.Group();
    const body = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.3, 0.7, 6),
        new THREE.MeshLambertMaterial({ color: 0x8c7b6b }));
    body.position.y = 0.38;
    body.castShadow = true;
    group.add(body);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.17, 7, 5),
        new THREE.MeshLambertMaterial({ color: 0xa69a86 }));
    head.position.y = 0.85;
    head.castShadow = true;
    group.add(head);
    return group;
}

function addAssetComparison(key) {
    if (builtAssetGroups.has(key)) return;
    builtAssetGroups.add(key);
    const started = performance.now();
    const artIds = ['art_rusty_barrel', 'art_wooden_crate', 'art_chainlink_fence'];
    for (let index = 0; index < 24; index++) {
        const type = index % 3;
        const x = 15 + (index % 8) * 2.5;
        const z = 18 + Math.floor(index / 8) * 3;
        if (key === 'asset-procedural') {
            const procedural = type === 0
                ? new THREE.Mesh(new THREE.CylinderGeometry(0.33, 0.33, 0.9, 16),
                    new THREE.MeshStandardMaterial({ color: 0x86654b, roughness: 0.9 }))
                : type === 1 ? createCrateModel(THREE, 0.8, 0.7, 0.8, '#8b6b4f', false)
                    : createFenceModel(THREE, 2, 1.2, 0.15, '#8b7458');
            procedural.position.set(x, type === 0 ? 0.45 : 0, z);
            assetProcedural.add(procedural);
            continue;
        }
        const id = key === 'asset-test-glb' ? 'box' : artIds[type];
        const asset = findLocationAsset(id);
        const holder = new THREE.Group();
        holder.position.set(x, 0, z);
        assetGroups[key].add(holder);
        const [width, depth, height] = asset.dimensions;
        mountLocationModel(holder, asset, { width, depth, height }, model => {
            holder.add(model);
            assetLoads[key].done++;
            if (assetLoads[key].done === 24) assetLoads[key].ms = Math.round(performance.now() - started);
        }, error => {
            assetLoads[key].done++;
            assetLoads[key].errors++;
            console.warn(`Benchmark asset ${id} failed`, error);
            if (assetLoads[key].done === 24) assetLoads[key].ms = Math.round(performance.now() - started);
        });
    }
}

function buildScene() {
    addTerrain(localTerrain, 48, false);
    addTerrain(worldTerrain, 96, true);
    const tiles = Array.from({ length: 48 }, () => Array.from({ length: 48 }, () => ({ terrain: 'grass' })));
    grass.add(createGrassDetailGroup(THREE, tiles, () => 0, () => false));
    const kinds = ['electric', 'fire', 'acid', 'radiation', 'psi', 'void'];
    const colors = ['#8eddf4', '#f5a15b', '#99d96c', '#d2e769', '#90b3f2', '#b6a0d4'];
    for (let index = 0; index < 72; index++) {
        const x = 8 + (index % 9) * 4;
        const z = 8 + Math.floor(index / 9) * 4;
        const local = createAnomalyEffect(kinds[index % 6], colors[index % 6], 0.8, `bench-${index}`);
        local.position.set(x, 0.04, z);
        localAnomalies.add(local);
        anomalyRoots.local.push(local);
        const world = createAnomalyEffect(kinds[index % 6], colors[index % 6], 0.8);
        world.position.set(30 + (index % 9) * 4, 0.9, 30 + Math.floor(index / 9) * 4);
        worldAnomalies.add(world);
        anomalyRoots.world.push(world);
    }
    for (let index = 0; index < MAX_ANOMALY_RENDER_LIGHTS; index++) {
        const type = index % 3;
        const anomalyIndex = Math.floor(index / 3) * 6 + type;
        const light = new THREE.PointLight(
            ['#92dcff', '#ff813d', '#a8ec69'][type],
            [0.65, 0.85, 0.45][type],
            [2.7, 3.1, 2.4][type], 2,
        );
        light.position.set(8 + (anomalyIndex % 9) * 4, 0.55, 8 + Math.floor(anomalyIndex / 9) * 4);
        light.castShadow = false;
        localAnomalies.add(light);
    }
    ALL_MUTANT_TYPES.forEach((type, typeIndex) => {
        for (let copy = 0; copy < 5; copy++) {
            const model = createMutantModel(type) || fallbackMutant();
            model.position.set(6 + (typeIndex % 8) * 5 + copy * 0.4, 0,
                11 + Math.floor(typeIndex / 8) * 12 + copy * 1.8);
            mutants.add(model);
        }
    });
    document.getElementById('mutants').replaceChildren(...ALL_MUTANT_TYPES.map(type => {
        const item = document.createElement('li');
        item.textContent = type;
        if (!MODELED_MUTANT_TYPES.includes(type)) {
            item.className = 'fallback';
            item.title = 'Стандартный силуэт, уникальная модель еще не создана';
        }
        return item;
    }));
}

function setScenario(id) {
    if (assetGroups[id]) addAssetComparison(id);
    const world = id.startsWith('world-');
    localTerrain.visible = !world;
    worldTerrain.visible = world;
    grass.visible = id === 'local-grass' || id === 'local-all';
    localAnomalies.visible = id === 'local-anomalies' || id === 'local-all';
    worldAnomalies.visible = id === 'world-anomalies';
    mutants.visible = id === 'local-mutants' || id === 'local-all';
    for (const [key, group] of Object.entries(assetGroups)) group.visible = id === key;
    return world;
}

let started = performance.now();
let measurement = null;
let sequence = null;
let lastFrame = 0;
const percentile = (sorted, fraction) => sorted[Math.min(sorted.length - 1,
    Math.floor((sorted.length - 1) * fraction))] || 0;

function finishMeasurement() {
    if (!measurement) return;
    const frames = measurement.frames.sort((a, b) => a - b);
    const elapsed = (performance.now() - measurement.started) / 1000;
    const row = {
        scenario: measurement.scenario,
        resolution: renderer.getPixelRatio(),
        fps: Math.round(frames.length / elapsed),
        medianMs: Number(percentile(frames, 0.5).toFixed(1)),
        p95Ms: Number(percentile(frames, 0.95).toFixed(1)),
        drawCalls: Math.round(measurement.calls / Math.max(1, frames.length)),
        triangles: Math.round(measurement.triangles / Math.max(1, frames.length)),
        materials: (() => {
            const values = new Set();
            assetGroups[measurement.scenario]?.traverse(child => {
                if (child.isMesh) for (const material of Array.isArray(child.material) ? child.material : [child.material]) values.add(material);
            });
            return values.size;
        })(),
        gpuGeometries: renderer.info.memory.geometries,
        gpuTextures: renderer.info.memory.textures,
        initialLoadMs: assetLoads[measurement.scenario]?.ms ?? null,
        loadErrors: assetLoads[measurement.scenario]?.errors ?? 0,
    };
    const item = document.createElement('li');
    item.textContent = `${row.scenario}: ${row.fps} FPS, p95 ${row.p95Ms} мс, ${row.drawCalls} calls`;
    item.title = JSON.stringify(row);
    results.appendChild(item);
    console.info('Graphics benchmark', row);
    measurement = null;
    if (sequence?.length) {
        const next = sequence.shift();
        setTimeout(() => startMeasurement(next), 200);
    }
}

function startMeasurement(id = scenarioSelect.value) {
    scenarioSelect.value = id;
    setScenario(id);
    updateResolution();
    started = performance.now();
    lastFrame = 0;
    measurement = { scenario: id, warmupUntil: started + 2000, started: 0,
        frames: [], calls: 0, triangles: 0 };
}

scenarioSelect.addEventListener('change', () => { setScenario(scenarioSelect.value); started = performance.now(); });
function updateResolution() {
    const limit = scenarioSelect.value.startsWith('world-') ? 1.5 : 2;
    renderer.setPixelRatio(resolutionSelect.value === 'native'
        ? Math.min(window.devicePixelRatio || 1, limit) : 1);
    resize();
}
scenarioSelect.addEventListener('change', updateResolution);
resolutionSelect.addEventListener('change', updateResolution);
document.getElementById('measure').addEventListener('click', () => { sequence = null; startMeasurement(); });
document.getElementById('sequence').addEventListener('click', () => {
    sequence = Array.from(scenarioSelect.options, option => option.value);
    results.replaceChildren();
    startMeasurement(sequence.shift());
});

function resize() {
    const width = Math.max(1, view.clientWidth);
    const height = Math.max(1, view.clientHeight);
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
document.addEventListener('visibilitychange', () => {
    if (document.hidden) { measurement = null; sequence = null; lastFrame = 0; }
});

function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden) return;
    const world = setScenario(scenarioSelect.value);
    const center = world ? 48 : 24;
    const angle = (now - started) * 0.00018;
    const elevation = 0.5 + 0.5 * Math.sin((now - started) * 0.0006);
    const radius = world ? 42 - 20 * elevation : 24 - 15 * elevation;
    camera.position.set(center + Math.cos(angle) * radius,
        world ? 11 + 42 * elevation : 6 + 28 * elevation,
        center + Math.sin(angle) * radius);
    camera.lookAt(center, 0, center);
    if (localAnomalies.visible) animateAnomalyEffects(anomalyRoots.local, now);
    if (worldAnomalies.visible) animateAnomalyEffects(anomalyRoots.world, now);
    renderer.render(scene, camera);
    const ms = lastFrame ? now - lastFrame : 0;
    lastFrame = now;
    if (measurement && assetLoads[measurement.scenario]?.done < 24) {
        measurement.warmupUntil = now + 2000;
    }
    if (measurement && now >= measurement.warmupUntil) {
        if (!measurement.started) measurement.started = now;
        if (ms > 0 && ms < 1000) {
            measurement.frames.push(ms);
            measurement.calls += renderer.info.render.calls;
            measurement.triangles += renderer.info.render.triangles;
        }
        if (now - measurement.started >= 10000) finishMeasurement();
    }
    if (Math.floor(now / 400) !== Math.floor((now - ms) / 400)) {
        readout.textContent = `${scenarioSelect.value}\n`
            + `${ms ? (1000 / ms).toFixed(0) : '-'} FPS  |  ${ms.toFixed(1)} мс/кадр\n`
            + `${renderer.info.render.calls} draw calls  |  ${renderer.info.render.triangles} треугольников\n`
            + `${grass.children[0]?.userData.detailCounts?.tufts || 0} пучков травы в тесте\n`
            + `${renderer.getPixelRatio()}x, ${renderer.domElement.width}×${renderer.domElement.height}`
            + (assetLoads[scenarioSelect.value] ? `\nАссеты: ${assetLoads[scenarioSelect.value].done}/24, ошибок: ${assetLoads[scenarioSelect.value].errors}` : '')
            + (measurement ? '\nИзмерение: 2 с прогрев + 10 с сбор...' : '');
    }
}

updateResolution();
buildScene();
setScenario(scenarioSelect.value);
requestAnimationFrame(frame);
