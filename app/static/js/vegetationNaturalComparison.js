import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { findLocationAsset } from './locationAssetCatalog.js';
import { applyLocationPbrMaterial, releaseLocationPbrMaterial } from './locationPbrMaterials.js';
import { createNaturalCoverPlacements, createReducedWeedPlacements,
    groundTintAt } from './vegetationNaturalCover.js';

const view = document.getElementById('view');
const readout = document.getElementById('readout');
const statsBody = document.getElementById('variant-stats');
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.useLegacyLights = true;
view.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x777d74);
scene.add(new THREE.HemisphereLight(0xdce4d9, 0x554a3f, 1.35));
const sun = new THREE.DirectionalLight(0xffe7c4, 1.25);
sun.position.set(-5, 9, 6);
scene.add(sun);
const camera = new THREE.PerspectiveCamera(52, 1, 0.05, 90);
const state = { yaw: 0.25, pitch: 0.75, distance: 12.8, targetX: 0,
    focus: 'all', ground: 'original' };
const centers = { reduced: -4.2, medium01: 0, medium02: 4.2 };
const plots = new Map();
const groundMeshes = [];
const disposable = [];
const naturalPlants = createNaturalCoverPlacements();
const reducedPlants = createReducedWeedPlacements(naturalPlants);
const candidatePlants = naturalPlants.filter((_, index) => index % 3 === 0);
const candidateMeanSpread = candidatePlants.reduce((sum, plant) =>
    sum + (plant.scaleX + plant.scaleZ) / 2, 0) / candidatePlants.length;
let grassReady = false;
let lastReadout = 0;

function prepareGroundGeometry(geometry) {
    const uv = geometry.getAttribute('uv');
    for (let index = 0; index < uv.count; index++) {
        uv.setXY(index, uv.getX(index) * 2, uv.getY(index) * 2);
    }
    geometry.setAttribute('uv2', uv.clone());
    return geometry;
}
const groundGeometry = prepareGroundGeometry(new THREE.PlaneGeometry(4, 4));
const tunedGroundGeometry = prepareGroundGeometry(new THREE.PlaneGeometry(4, 4, 24, 24));
const positions = tunedGroundGeometry.getAttribute('position');
const groundColors = new Float32Array(positions.count * 3);
for (let index = 0; index < positions.count; index++) {
    const tone = groundTintAt(positions.getX(index), -positions.getY(index));
    groundColors[index * 3] = tone.r;
    groundColors[index * 3 + 1] = tone.g;
    groundColors[index * 3 + 2] = tone.b;
}
tunedGroundGeometry.setAttribute('color', new THREE.BufferAttribute(groundColors, 3));
const earthMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1 });
const tunedEarthMaterial = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 1, vertexColors: true,
});
const earth = findLocationAsset('art_dirty_earth');
if (earth?.visual?.maps) applyLocationPbrMaterial(earthMaterial, earth.visual.maps);
applyLocationPbrMaterial(tunedEarthMaterial, {
    color: '/static/textures/vegetation/forrest_ground_01_diff_1k.jpg',
    normal: '/static/textures/vegetation/forrest_ground_01_nor_gl_1k.jpg',
    arm: '/static/textures/vegetation/forrest_ground_01_arm_1k.jpg',
});
disposable.push(groundGeometry, tunedGroundGeometry, earthMaterial, tunedEarthMaterial);

for (const [id, x] of Object.entries(centers)) {
    const group = new THREE.Group();
    group.position.x = x;
    const ground = new THREE.Mesh(groundGeometry, earthMaterial);
    ground.rotation.x = -Math.PI / 2;
    groundMeshes.push(ground);
    group.add(ground);
    plots.set(id, group);
    scene.add(group);
}

function loadTexture(url, srgb = false) {
    return new Promise((resolve, reject) => new THREE.TextureLoader().load(url, texture => {
        texture.flipY = false;
        if (srgb) texture.colorSpace = THREE.SRGBColorSpace;
        texture.minFilter = THREE.LinearMipmapLinearFilter;
        texture.magFilter = THREE.LinearFilter;
        disposable.push(texture);
        resolve(texture);
    }, undefined, reject));
}

function loadModel(url) {
    return new Promise((resolve, reject) => new GLTFLoader().load(url, resolve, undefined, reject));
}

function addInstances(id, plants, geometry, material, kind = 'bermuda', size = 1) {
    const mesh = new THREE.InstancedMesh(geometry, material, plants.length);
    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    for (let index = 0; index < plants.length; index++) {
        const plant = plants[index];
        dummy.position.set(plant.x, 0.003, plant.z);
        dummy.rotation.set(plant.tiltX, plant.turn, plant.tiltZ, 'YXZ');
        if (kind === 'medium') {
            dummy.scale.set(plant.scaleX * size.horizontal,
                THREE.MathUtils.clamp(plant.scaleY, 0.92, 1.08) * size.vertical,
                plant.scaleZ * size.horizontal);
        } else {
            const scale = kind === 'broadleaf' ? 1.05 : 1;
            dummy.scale.set(plant.scaleX * scale, plant.scaleY * scale, plant.scaleZ * scale);
        }
        dummy.updateMatrix();
        mesh.setMatrixAt(index, dummy.matrix);
        if (kind === 'broadleaf') {
            color.setHex(plant.tone < 0.17 ? 0xada88b : plant.tone < 0.37 ? 0xbcc4a8 : 0xe0dfc6);
        } else if (kind === 'bermuda') {
            color.setRGB(plant.tone < 0.17 ? 1.04 : 1.16,
                plant.tone < 0.17 ? 0.96 : 1.13, plant.tone < 0.17 ? 0.82 : 1.07);
        } else {
            color.setRGB(1, 1, 1);
        }
        mesh.setColorAt(index, color);
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    plots.get(id).add(mesh);
    return mesh;
}

function resize() {
    const width = Math.max(1, view.clientWidth);
    const height = Math.max(1, view.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
}

const resizeObserver = new ResizeObserver(resize);
resizeObserver.observe(view);
resize();

function frame() {
    const halfFov = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const span = state.focus === 'all' ? 7.1 : 2.3;
    const distance = Math.max(state.distance, span / (halfFov * camera.aspect));
    const horizontal = Math.cos(state.pitch) * distance;
    camera.position.set(state.targetX + Math.sin(state.yaw) * horizontal,
        Math.sin(state.pitch) * distance,
        Math.cos(state.yaw) * horizontal);
    camera.lookAt(state.targetX, 0, 0);
    renderer.render(scene, camera);
    const now = performance.now();
    if (grassReady && now - lastReadout > 500) {
        lastReadout = now;
        readout.textContent = `Видимые draw calls: ${renderer.info.render.calls}; `
            + `треугольников в кадре: ${renderer.info.render.triangles}. `
            + `Грунт: ${state.ground === 'original' ? 'исходный Dirt' : 'Forest Ground 01'}; `
            + 'трава сгруппирована в InstancedMesh.';
    }
}
renderer.setAnimationLoop(frame);

let drag = null;
view.addEventListener('pointerdown', event => {
    drag = { x: event.clientX, y: event.clientY, yaw: state.yaw, pitch: state.pitch };
    view.setPointerCapture(event.pointerId);
});
view.addEventListener('pointermove', event => {
    if (!drag) return;
    state.yaw = drag.yaw - (event.clientX - drag.x) * 0.008;
    state.pitch = THREE.MathUtils.clamp(drag.pitch + (event.clientY - drag.y) * 0.006, 0.12, 1.56);
});
view.addEventListener('pointerup', () => { drag = null; });
view.addEventListener('pointercancel', () => { drag = null; });
view.addEventListener('wheel', event => {
    event.preventDefault();
    state.distance = THREE.MathUtils.clamp(state.distance * Math.exp(event.deltaY * 0.001), 3, 30);
}, { passive: false });

document.querySelectorAll('[data-angle]').forEach(button => button.addEventListener('click', () => {
    state.pitch = { top: 1.55, game: 0.75, middle: 0.5 }[button.dataset.angle];
    document.querySelectorAll('[data-angle]').forEach(item => {
        item.setAttribute('aria-pressed', String(item === button));
    });
}));
document.querySelectorAll('[data-distance]').forEach(button => button.addEventListener('click', () => {
    state.distance = button.dataset.distance === 'close' ? 3.3 : 5.7;
}));
document.querySelectorAll('[data-focus]').forEach(button => button.addEventListener('click', () => {
    const focus = button.dataset.focus;
    state.focus = focus;
    state.targetX = centers[focus] ?? 0;
    state.distance = focus === 'all' ? 12.8 : 5.7;
    for (const [id, plot] of plots) plot.visible = focus === 'all' || focus === id;
    document.querySelectorAll('[data-focus]').forEach(item => {
        item.setAttribute('aria-pressed', String(item === button));
    });
}));

document.querySelectorAll('[data-ground]').forEach(button => button.addEventListener('click', () => {
    state.ground = button.dataset.ground;
    const tuned = state.ground === 'tuned';
    for (const ground of groundMeshes) {
        ground.geometry = tuned ? tunedGroundGeometry : groundGeometry;
        ground.material = tuned ? tunedEarthMaterial : earthMaterial;
    }
    document.querySelectorAll('[data-ground]').forEach(item => {
        item.setAttribute('aria-pressed', String(item === button));
    });
}));

const params = new URLSearchParams(location.search);
const requestedFocus = params.get('focus');
const requestedAngle = params.get('angle');
const requestedGround = params.get('ground');
const requestedDistance = params.get('distance');
if (centers[requestedFocus] !== undefined) {
    document.querySelector(`[data-focus="${requestedFocus}"]`).click();
}
if (['top', 'game', 'middle'].includes(requestedAngle)) {
    document.querySelector(`[data-angle="${requestedAngle}"]`).click();
}
if (['original', 'tuned'].includes(requestedGround)) {
    document.querySelector(`[data-ground="${requestedGround}"]`).click();
}
if (['middle', 'close'].includes(requestedDistance)) {
    document.querySelector(`[data-distance="${requestedDistance}"]`).click();
}

const root = '/static/textures/vegetation/';
const mediumRoot = '/static/models/vegetation/polyhaven-d51/';
Promise.all([
    loadModel('/static/models/vegetation/bermuda_d4_tuft.glb'),
    loadModel('/static/models/vegetation/weed_d42_tuft.glb'),
    loadTexture(`${root}grass_bermuda_01_diff_2k.jpg`, true),
    loadTexture(`${root}grass_bermuda_01_alpha_2k.png`),
    loadTexture(`${root}grass_bermuda_01_nor_gl_2k.jpg`),
    loadTexture(`${root}weed_plant_02_diff_1k.jpg`, true),
    loadTexture(`${root}weed_plant_02_alpha_1k.png`),
    loadTexture(`${root}weed_plant_02_nor_gl_1k.jpg`),
    loadModel(`${mediumRoot}grass_medium_01/grass_medium_01_1k.gltf`),
    loadModel(`${mediumRoot}grass_medium_02/grass_medium_02_1k.gltf`),
    loadTexture(`${mediumRoot}grass_medium_01/textures/grass_medium_01_alpha_1k.png`),
    loadTexture(`${mediumRoot}grass_medium_02/textures/grass_medium_02_alpha_1k.png`),
]).then(([bermudaModel, weedModel, color, alpha, normal, weedColor, weedAlpha, weedNormal,
    medium01Model, medium02Model, medium01Alpha, medium02Alpha]) => {
    function sourceGeometry(model) {
        let geometry = null;
        model.scene.traverse(object => {
            if (object.isMesh) geometry = object.geometry;
        });
        if (!geometry) throw new Error('No vegetation mesh in GLB');
        return geometry;
    }
    const bermudaGeometry = sourceGeometry(bermudaModel);
    const weedGeometry = sourceGeometry(weedModel);
    const bermudaMaterial = new THREE.MeshStandardMaterial({
        map: color, alphaMap: alpha, normalMap: normal,
        alphaTest: 0.48, transparent: false, side: THREE.DoubleSide,
        metalness: 0, roughness: 0.95, normalScale: new THREE.Vector2(0.65, 0.65),
    });
    const weedMaterial = new THREE.MeshStandardMaterial({
        map: weedColor, alphaMap: weedAlpha, normalMap: weedNormal,
        alphaTest: 0.5, transparent: false, side: THREE.DoubleSide,
        metalness: 0, roughness: 0.95, normalScale: new THREE.Vector2(0.45, 0.45),
    });
    disposable.push(bermudaGeometry, weedGeometry, bermudaMaterial, weedMaterial);
    addInstances('reduced', reducedPlants.bermuda, bermudaGeometry, bermudaMaterial);
    addInstances('reduced', reducedPlants.broadleaf, weedGeometry, weedMaterial, 'broadleaf');

    function addPolyHavenCandidate(id, model, meshName, alphaMap) {
        const meshes = [];
        model.scene.traverse(object => { if (object.isMesh) meshes.push(object); });
        const source = meshes.find(object => object.name === meshName);
        if (!source) throw new Error(`Missing Poly Haven mesh: ${meshName}`);
        for (const mesh of meshes) {
            if (mesh !== source && mesh.geometry !== source.geometry) mesh.geometry.dispose();
        }
        const material = source.material;
        material.alphaMap = alphaMap;
        material.alphaTest = 0.45;
        material.transparent = false;
        material.depthWrite = true;
        material.side = THREE.DoubleSide;
        material.needsUpdate = true;
        disposable.push(source.geometry, material);
        for (const texture of new Set([material.map, material.normalMap,
            material.roughnessMap, material.metalnessMap])) {
            if (texture) disposable.push(texture);
        }
        source.geometry.computeBoundingBox();
        const bounds = source.geometry.boundingBox;
        const span = new THREE.Vector3();
        bounds.getSize(span);
        // Match visible height and average footprint without changing the shared roots.
        const size = {
            horizontal: 0.34 / (Math.max(span.x, span.z) * candidateMeanSpread),
            vertical: 0.18 / bounds.max.y,
        };
        addInstances(id, candidatePlants, source.geometry, material, 'medium', size);
        return source.geometry;
    }
    const medium01Geometry = addPolyHavenCandidate('medium01', medium01Model,
        'grass_medium_01_mid_a_LOD0', medium01Alpha);
    const medium02Geometry = addPolyHavenCandidate('medium02', medium02Model,
        'grass_medium_02_d', medium02Alpha);
    const countTriangles = geometry => geometry.index ? geometry.index.count / 3
        : geometry.getAttribute('position').count / 3;
    const bTris = countTriangles(bermudaGeometry);
    const wTris = countTriangles(weedGeometry);
    for (const [label, count, triangles, calls] of [
        ['A · D.4.3 Reduced Weed', reducedPlants.bermuda.length + reducedPlants.broadleaf.length,
            reducedPlants.bermuda.length * bTris + reducedPlants.broadleaf.length * wTris, 2],
        ['B · Grass Medium 01', candidatePlants.length,
            candidatePlants.length * countTriangles(medium01Geometry), 1],
        ['C · Grass Medium 02', candidatePlants.length,
            candidatePlants.length * countTriangles(medium02Geometry), 1],
    ]) {
        const row = document.createElement('tr');
        for (const value of [label, count, triangles, calls]) {
            const cell = document.createElement('td');
            cell.textContent = value;
            row.appendChild(cell);
        }
        statsBody.appendChild(row);
    }
    grassReady = true;
    frame();
    window.dispatchEvent(new Event('vegetation-natural-ready'));
}).catch(error => {
    readout.textContent = `Не удалось загрузить растительность: ${error.message}`;
    console.error(error);
});

window.addEventListener('beforeunload', () => {
    renderer.setAnimationLoop(null);
    resizeObserver.disconnect();
    releaseLocationPbrMaterial(earthMaterial);
    releaseLocationPbrMaterial(tunedEarthMaterial);
    for (const resource of disposable) resource.dispose();
    renderer.dispose();
});
