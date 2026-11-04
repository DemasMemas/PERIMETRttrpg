import { createWaterProfileLayer } from './waterProfiles.js';
import { createWorldInstanceBatches } from './worldInstanceBatches.js';
import * as THREE from 'three';
import { OrbitControls } from 'https://unpkg.com/three@0.128.0/examples/jsm/controls/OrbitControls.js';
import { updateRain } from './weather.js';
import { createAnomalyEffect, animateAnomalyEffects } from './anomalies.js';
import { createFpsCounter } from './fpsCounter.js';
import { horizontalCameraAxes, isTypingTarget } from './cameraPan.js';
import { roadNode, roadConnectsAt, roadSurfaceHeight } from './worldRoadTopology.js';
import { buildRoadSurface, ROAD_SURFACE_LIFT } from './worldRoadSurface.js';
import { loadWorldRoadBridge, createWorldRoadBridgeMeshes } from './worldRoadBridge.js';
import { installSecondaryClick } from './locationEditorPointer.js';
import { objectFromWorldInstance } from './worldObjectSelection.js';
import { createWaterMaterial } from './waterSurface.js';
import { createTerrainMaterialLayer } from './terrainMaterialLayer.js';
import { addTerrainMaterialOptions } from './terrainMaterialCatalog.js';
import { applySurfacePattern, getSurfaceTexture } from './locationTerrainDetail.js';
import { createTerrainPixelsRegion, getGrassGroundImages, getGrassGroundPixelData,
    updateTerrainPixelsRegion } from './locationTerrainTexture.js';
import { prepareWorldTerrainPixelImages } from './worldTerrainPixels.js';
import { createWorldGrassDetail } from './worldGrassDetail.js';
import { createPo2Fence } from './worldPo2Fence.js';
import { buildSmoothTerrainData, isSmoothTerrainTile, createTerrainHeightSampler } from './locationTerrain.js';
import { fenceMatrixElements } from './worldFenceLine.js';
import { buildWorldWaterData, indexWaterGeometry } from './worldWaterGeometry.js';
import {
    createCompatibleWebGLRenderer,
    createUnavailableRenderer,
    showWebGLUnavailable,
} from './webglSupport.js';

const CHUNK_SIZE = 32;
const worldTerrainMaterials = createTerrainMaterialLayer(THREE, { world: true });
worldTerrainMaterials.resize(512, 512);
for (const id of ['tile-type-select', 'tile-edit-terrain']) {
    addTerrainMaterialOptions(document.getElementById(id), { world: true });
}
export const getWorldTerrainMaterialDiagnostics = () => worldTerrainMaterials.diagnostics();
export const chunksMap = new Map();
let worldGrassGroundPixelsEnabled = false;
const worldGrassDetail = createWorldGrassDetail(THREE);
worldTerrainMaterials.setShoreGrass(worldGrassDetail.textures());

export function setWorldGrassGroundPixelsEnabled(enabled) {
    worldGrassGroundPixelsEnabled = enabled === true;
    worldGrassDetail.setImages(worldGrassGroundPixelsEnabled ? getGrassGroundImages() : null);
}

let lastMouseX = 0, lastMouseY = 0;
let lastModifiers = { alt: false, shift: false };
const globalCameraKeys = new Set();
let lastChunkVisibilityAt = -Infinity;
let lastHoverRaycastAt = -Infinity;
const CHUNK_VISIBILITY_REFRESH_MS = 500;
const HOVER_RAYCAST_REFRESH_MS = 250;
const MOVING_CAMERA_RAYCAST_REFRESH_MS = 100;
const SHADOW_REFRESH_MS = 250;
let cameraDragActive = false;
let hoverRaycastPending = false;
let worldShadowsDirty = false;
let lastWorldShadowAt = -Infinity;
let shadowRefreshesSinceSample = 0;
let worldPerfSampleAt = performance.now();
let worldFrameWorkMs = 0;
let worldSampleFrames = 0;

let postRenderCallbacks = [];

const MIN_CHUNK = 0;
let MAX_CHUNK_X = 15;
let MAX_CHUNK_Y = 15;

const chunkBounds = [];
const worldBrushPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -1);

const terrainColors = {
    grass: 0x3a5f0b,
    sand: 0xC2B280,
    rock: 0x808080,
    swamp: 0x4B3B2A,
    concrete: 0xb3b1a8, gravel: 0x82796b, snow: 0xd4d9d9, ice: 0xc2cfd1, metal: 0x85654d,
    bare_dirt: 0x7a694f, granite_tiles: 0xb3b1a8, wood_floor: 0x775635,
    water: 0x1E90FF
};

const treeGeo = new THREE.ConeGeometry(0.3, 1, 8);
const houseGeo = new THREE.BoxGeometry(0.6, 0.6, 0.6);
const po2Fence = createPo2Fence(THREE, () => {
    worldShadowsDirty = true;
    if (previewObject?.userData.previewType === 'fence') {
        previewObject.material.map = po2Fence.material.map;
        previewObject.material.needsUpdate = true;
    }
    if(fenceLinePreview){fenceLinePreview.material.map=po2Fence.material.map;fenceLinePreview.material.needsUpdate=true;}
});
const fenceGeo = po2Fence.geometry;
export const getWorldFenceDiagnostics = () => po2Fence.diagnostics();
function landmarkGeometry(parts) {
    const vertices = [];
    const normals = [];
    const vertex = new THREE.Vector3();
    const normal = new THREE.Vector3();
    for (const [geometry, x, y, z, sx = 1, sy = 1, sz = 1, rotation = 0] of parts) {
        const flat = geometry.index ? geometry.toNonIndexed() : geometry;
        const transform = new THREE.Matrix4().compose(
            new THREE.Vector3(x, y, z),
            new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), rotation),
            new THREE.Vector3(sx, sy, sz),
        );
        const normalTransform = new THREE.Matrix3().getNormalMatrix(transform);
        const positions = flat.getAttribute('position');
        const sourceNormals = flat.getAttribute('normal');
        for (let index = 0; index < positions.count; index++) {
            vertex.fromBufferAttribute(positions, index).applyMatrix4(transform);
            normal.fromBufferAttribute(sourceNormals, index).applyMatrix3(normalTransform).normalize();
            vertices.push(vertex.x, vertex.y, vertex.z);
            normals.push(normal.x, normal.y, normal.z);
        }
        if (flat !== geometry) flat.dispose();
        geometry.dispose();
    }
    const merged = new THREE.BufferGeometry();
    merged.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    merged.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    return merged;
}

const WORLD_LANDMARKS = {
    forest: { geometry: landmarkGeometry([
        [new THREE.ConeGeometry(0.26, 1.0, 6), -0.23, 0.19, 0.02],
        [new THREE.ConeGeometry(0.29, 1.18, 7), 0.16, 0.28, -0.16],
        [new THREE.ConeGeometry(0.23, 0.86, 6), 0.22, 0.12, 0.24],
    ]), width: 1.05, height: 1.5, depth: 1.05, color: '#244832' },
    hamlet: { geometry: landmarkGeometry([
        [new THREE.BoxGeometry(0.55, 0.28, 0.45), 0, -0.12, 0],
        [new THREE.ConeGeometry(0.42, 0.25, 4), 0, 0.13, 0, 1, 1, 1, Math.PI / 4],
    ]), width: 0.75, height: 0.52, depth: 0.75, color: '#aa8665' },
    village: { geometry: landmarkGeometry([
        [new THREE.BoxGeometry(0.35, 0.24, 0.3), -0.23, -0.13, -0.22],
        [new THREE.ConeGeometry(0.27, 0.2, 4), -0.23, 0.08, -0.22, 1, 1, 1, Math.PI / 4],
        [new THREE.BoxGeometry(0.38, 0.28, 0.32), 0.23, -0.11, -0.1],
        [new THREE.ConeGeometry(0.3, 0.22, 4), 0.23, 0.13, -0.1, 1, 1, 1, Math.PI / 4],
        [new THREE.BoxGeometry(0.32, 0.23, 0.28), 0, -0.15, 0.27],
        [new THREE.ConeGeometry(0.25, 0.2, 4), 0, 0.05, 0.27, 1, 1, 1, Math.PI / 4],
    ]), width: 1.05, height: 0.53, depth: 1.05, color: '#b39474' },
    road: { geometry: new THREE.BoxGeometry(0.92, 0.035, 0.25), width: 0.92, height: 0.035, depth: 0.25, color: '#756f61' },
    factory: { geometry: landmarkGeometry([
        [new THREE.BoxGeometry(0.75, 0.42, 0.58), 0, -0.17, 0],
        [new THREE.CylinderGeometry(0.075, 0.085, 0.48, 6), -0.22, 0.27, -0.12],
        [new THREE.CylinderGeometry(0.06, 0.07, 0.34, 6), 0.21, 0.2, -0.12],
    ]), width: 0.8, height: 0.85, depth: 0.58, color: '#66747b' },
    base: { geometry: landmarkGeometry([
        [new THREE.CylinderGeometry(0.45, 0.5, 0.23, 8), 0, -0.13, 0],
        [new THREE.CylinderGeometry(0.16, 0.2, 0.39, 6), 0, 0.13, 0],
        [new THREE.ConeGeometry(0.24, 0.16, 6), 0, 0.4, 0],
    ]), width: 1, height: 0.75, depth: 1, color: '#6c765d' },
    camp: { geometry: landmarkGeometry([
        [new THREE.ConeGeometry(0.33, 0.43, 4), -0.17, 0, -0.1, 1, 1, 1, Math.PI / 4],
        [new THREE.ConeGeometry(0.25, 0.33, 4), 0.22, -0.05, 0.18, 1, 1, 1, Math.PI / 4],
    ]), width: 1, height: 0.5, depth: 0.85, color: '#9c8362' },
};
const WORLD_LANDMARK_TYPES = Object.keys(WORLD_LANDMARKS).filter(type => type !== 'road');
const landmarkMat = new THREE.MeshStandardMaterial();
const roadMaterial = new THREE.MeshStandardMaterial({ roughness: 1, vertexColors: true });
applySurfacePattern(roadMaterial, getSurfaceTexture(THREE, 'road'), 0.12, 'top');
let roadBridgeRequested=false;
let activeRoadPreview=null;
function ensureRoadBridge() {
    if(roadBridgeRequested)return;
    roadBridgeRequested=true;
    loadWorldRoadBridge().then(()=>{
        for(const entry of chunksMap.values())if(entry.hasRoadNodes)rebuildRoadMeshes(entry);
        if(activeRoadPreview)showWorldRoadPreview(activeRoadPreview.plan,activeRoadPreview.settings);
    }).catch(error=>{roadBridgeRequested=false;console.error('Не удалось загрузить Modular Road Bridge',error);});
}
const roadPreviewMaterial=roadMaterial.clone();
applySurfacePattern(roadPreviewMaterial, getSurfaceTexture(THREE, 'road'), 0.12, 'top');
roadPreviewMaterial.transparent=true;roadPreviewMaterial.opacity=.85;roadPreviewMaterial.depthWrite=false;roadPreviewMaterial.depthTest=false;
const roadPreviewMeshes=[];
function roadSurfaceMesh(pads,links,material,bridgeOptions=null) {
    const bridgeApproaches=[];
    for(const link of links.filter(l=>l.bridge))for(const [position,toward] of [[link.from,link.to],[link.to,link.from]]){
        const tile=loadedWorldTile(Math.floor(position.x),Math.floor(position.z));
        if(tile&&tile.terrain!=='water')bridgeApproaches.push({position,toward});
    }
    // The connecting link can belong to the water node in the next chunk.
    const nodeAt=(x,z)=>roadNode(loadedWorldTile(x,z));
    for(const pad of pads.filter(p=>!p.bridge)){
        const x=Math.floor(pad.position.x),z=Math.floor(pad.position.z);
        for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]]){
            const tile=loadedWorldTile(x+dx,z+dz);
            if(tile?.terrain!=='water')continue;
            const node=roadNode(tile);
            if(node?.road?.bridge&&roadConnectsAt(nodeAt,x,z,dx,dz))bridgeApproaches.push({
                position:pad.position,toward:{x:x+dx+.5+Number(node.road.x||0),z:z+dz+.5+Number(node.road.z||0)},
            });
        }
    }
    const data=buildRoadSurface({pads:pads.filter(p=>!p.bridge),links:links.filter(l=>!l.bridge),getTile:loadedWorldTile,bridgeApproaches});
    if(!data.positions.length)return null;
    const geometry=new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.BufferAttribute(data.positions,3));
    geometry.setAttribute('color',new THREE.BufferAttribute(data.colors,3));geometry.computeVertexNormals();
    const mesh=new THREE.Mesh(geometry,material);mesh.userData.roadSurface=true;
    mesh.frustumCulled=false;mesh.castShadow=false;scene.add(mesh);return mesh;
}
export function removeWorldRoadPreview(){activeRoadPreview=null;for(const mesh of roadPreviewMeshes){scene.remove(mesh);if(mesh.userData.roadSurface)mesh.geometry.dispose();else mesh.dispose();}roadPreviewMeshes.length=0;}
export function showWorldRoadPreview(plan,settings) {
    removeWorldRoadPreview();
    activeRoadPreview={plan,settings};
    const pads=[],links=[],bridges=[],nodes=plan.path;
    const point=p=>new THREE.Vector3(p.x+.5+Number(p.offset_x||0),roadSurfaceHeight(p,p),p.y+.5+Number(p.offset_z||0));
    const width=Number(settings.scale||1);
    const seen=new Set();
    nodes.forEach((p,i)=>{
        const key=`${p.x},${p.y}`;
        if(!seen.has(key)){
            const nodeWidth=Number(p.scale||width);
            pads.push({position:point(p),scale:nodeWidth,color:'#55dbe8',bridge:p.bridge});seen.add(key);
            if(p.bridge)bridges.push({x:p.x,z:p.y});
        }
        if(i)links.push({from:point(nodes[i-1]),to:point(p),widthScale:(Number(p.scale||width)+Number(nodes[i-1].scale||width))/2,
            color:'#55dbe8',bridge:p.bridge||nodes[i-1].bridge});
    });
    const tiles=new Map(nodes.map(p=>[`${p.x},${p.y}`,{...loadedWorldTile(p.x,p.y),...p,terrain:p.bridge?'water':loadedWorldTile(p.x,p.y)?.terrain||'grass',
        objects:[{type:'road',bridge:p.bridge,bridge_height:p.bridge_height,x:p.offset_x,z:p.offset_z,scale:p.scale}]}]));
    const edges=new Set();
    for(let i=1;i<nodes.length;i++){const a=nodes[i-1],b=nodes[i];edges.add(`${a.x},${a.y}:${b.x},${b.y}`);edges.add(`${b.x},${b.y}:${a.x},${a.y}`);}
    if(bridges.length)ensureRoadBridge();
    const bridgeOptions={nodes:bridges,getTile:(x,z)=>tiles.get(`${x},${z}`)||loadedWorldTile(x,z),
        connected:(x,z,dx,dz)=>edges.has(`${x},${z}:${x+dx},${z+dz}`),sampleHeight:sampleWorldGroundHeight};
    const frames=createWorldRoadBridgeMeshes(bridgeOptions,true);
    const surface=roadSurfaceMesh(pads,links,roadPreviewMaterial,bridgeOptions);
    for(const mesh of [surface,...frames])if(mesh){scene.add(mesh);mesh.renderOrder=20;mesh.userData.roadRoutePreview=true;roadPreviewMeshes.push(mesh);}
}

const treeMat = new THREE.MeshStandardMaterial();
const houseMat = new THREE.MeshStandardMaterial();
const fenceMat = po2Fence.material;

const scene = new THREE.Scene();
// Одна программа материалов и для ясной погоды, и для тумана: меняется только uniform.
scene.fog = new THREE.FogExp2(0xcccccc, 0);
// Keep chunk meshes/instance indices for picking and editing, but submit identical
// visible object instances together. Terrain, water and road meshes are untouched.
const worldInstanceBatches = createWorldInstanceBatches(THREE, scene, chunksMap);
scene.onBeforeRender = () => worldInstanceBatches.prepare();
scene.onAfterRender = () => worldInstanceBatches.restore();
scene.background = new THREE.Color(0x050510);

window.isLocationActive = false;

function createCloudTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = 1024;
    canvas.height = 512;
    const ctx = canvas.getContext('2d');
    let seed = 34171;
    const random = () => {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        return seed / 4294967296;
    };
    const puff = (x, y, radiusX, radiusY, opacity) => {
        for (const offset of [-canvas.width, 0, canvas.width]) {
            const wrappedX = x + offset;
            if (wrappedX + radiusX < 0 || wrappedX - radiusX > canvas.width) continue;
            ctx.save();
            ctx.translate(wrappedX, y);
            ctx.scale(1, radiusY / radiusX);
            const gradient = ctx.createRadialGradient(0, 0, 0, 0, 0, radiusX);
            gradient.addColorStop(0, `rgba(255,255,255,${opacity})`);
            gradient.addColorStop(0.45, `rgba(255,255,255,${opacity * 0.7})`);
            gradient.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.fillStyle = gradient;
            ctx.beginPath();
            ctx.arc(0, 0, radiusX, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
        }
    };
    for (let i = 0; i < 65; i++) {
        const x = random() * canvas.width;
        const y = 20 + random() * (canvas.height - 40);
        const width = 75 + random() * 90;
        puff(x, y, width, 12 + random() * 19, 0.18);
        for (let j = 0; j < 5; j++) {
            const offset = (j - 2) * width * 0.28;
            puff(x + offset, y - random() * 12, width * (0.32 + random() * 0.22),
                15 + random() * 19, 0.23 + random() * 0.12);
        }
    }

    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.ClampToEdgeWrapping;
    texture.repeat.set(3, 1); // небольшое повторение для сглаживания шва
    return texture;
}

// Создаём текстуру один раз
const cloudTexture = createCloudTexture();

// Единое небо: время плавно смешивает день и ночь, облачность меняет покрытие.
const worldSkySphere = (() => {
    const geometry = new THREE.SphereGeometry(980, 64, 40);
    const material = new THREE.ShaderMaterial({
        uniforms: {
            cloudTexture: { value: cloudTexture },
            dayTopColor: { value: new THREE.Color(0x5c9db8) },
            dayBottomColor: { value: new THREE.Color(0xd5d5bd) },
            nightTopColor: { value: new THREE.Color(0x08111d) },
            nightBottomColor: { value: new THREE.Color(0x263646) },
            daylight: { value: 1 },
            clarity: { value: 1 },
            starlight: { value: 0 },
            starRotation: { value: 0 }
        },
        vertexShader: `
            varying vec2 vUv;
            varying vec3 vPosition;
            void main() {
                vUv = uv;
                vPosition = position;
                gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
            }
        `,
        fragmentShader: `
            uniform sampler2D cloudTexture;
            uniform vec3 dayTopColor;
            uniform vec3 dayBottomColor;
            uniform vec3 nightTopColor;
            uniform vec3 nightBottomColor;
            uniform float daylight;
            uniform float clarity;
            uniform float starlight;
            uniform float starRotation;
            varying vec2 vUv;
            varying vec3 vPosition;

            void main() {
                vec3 direction = normalize(vPosition);
                float h = smoothstep(-0.08, 0.8, direction.y);
                vec3 dayColor = mix(dayBottomColor, dayTopColor, h);
                vec3 nightColor = mix(nightBottomColor, nightTopColor, h);
                vec3 skyColor = mix(nightColor, dayColor, daylight);
                float cloudPattern = smoothstep(0.04, 0.38, texture2D(cloudTexture, vUv).a);
                float cloudCover = mix(0.82 + cloudPattern * 0.18, cloudPattern * 0.5, clarity);
                vec3 cloudColor = mix(vec3(0.09, 0.11, 0.14), vec3(0.52, 0.56, 0.59), daylight);
                cloudColor += vec3(0.1, 0.1, 0.09) * daylight * cloudPattern;
                vec3 finalColor = mix(skyColor, cloudColor, cloudCover);
                vec3 rotatedStars = vec3(
                    direction.x * cos(starRotation) + direction.y * sin(starRotation),
                    direction.y * cos(starRotation) - direction.x * sin(starRotation),
                    direction.z
                );
                vec2 skyUv = vec2(
                    atan(rotatedStars.z, rotatedStars.x) / 6.2831853 + 0.5,
                    asin(rotatedStars.y) / 3.14159265 + 0.5
                );
                vec2 starGrid = skyUv * vec2(180.0, 90.0);
                vec2 starCell = floor(starGrid);
                float starSeed = fract(sin(dot(starCell, vec2(12.9898, 78.233))) * 43758.5453);
                float starShape = 1.0 - smoothstep(0.04, 0.16, length(fract(starGrid) - 0.5));
                float star = step(0.975, starSeed) * starShape * starlight * clarity * (1.0 - cloudCover);
                finalColor += vec3(0.9, 0.95, 1.0) * star;
                gl_FragColor = vec4(finalColor, 1.0);
            }
        `,
        side: THREE.BackSide
    });
    return new THREE.Mesh(geometry, material);
})();

// Звёзды (только для ночи)
let stars = null;
function createStars() {
    const geometry = new THREE.BufferGeometry();
    const vertices = [];
    for (let i = 0; i < 1900; i++) {
        const azimuth = Math.random() * Math.PI * 2;
        // The world camera looks down, so its visible horizon is below the camera.
        const elevation = -0.75 + Math.random() * 1.35;
        const horizontal = Math.sqrt(1 - elevation * elevation) * 900;
        vertices.push(
            Math.cos(azimuth) * horizontal,
            elevation * 900,
            Math.sin(azimuth) * horizontal,
        );
    }
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    const material = new THREE.PointsMaterial({
        color: 0xe5eef5, size: 2.6, sizeAttenuation: false, depthWrite: false,
        fog: false, transparent: true, opacity: 0,
    });
    stars = new THREE.Points(geometry, material);
    stars.renderOrder = 1;
    scene.add(stars);
}

scene.add(worldSkySphere);
createStars();

const sunDisc = new THREE.Mesh(
    new THREE.SphereGeometry(10, 20, 12),
    new THREE.MeshBasicMaterial({ color: 0xffe6a0, fog: false, depthWrite: false, transparent: true }),
);
const moonDisc = new THREE.Mesh(
    new THREE.SphereGeometry(8, 20, 12),
    new THREE.MeshBasicMaterial({ color: 0xd9e5f2, fog: false, depthWrite: false, transparent: true }),
);
sunDisc.renderOrder = 2;
moonDisc.renderOrder = 2;
worldSkySphere.add(sunDisc);
worldSkySphere.add(moonDisc);

let celestialMinutes = 480;
const MIN_WORLD_SHADOW_LIGHT_Y = 0.8;

function updateCelestialPositions() {
    const angle = celestialMinutes >= 360 && celestialMinutes < 1200
        ? (celestialMinutes - 360) * Math.PI / 840
        : Math.PI + ((celestialMinutes + (celestialMinutes < 360 ? 1440 : 0)) - 1200) * Math.PI / 600;
    const sunDirection = new THREE.Vector3(Math.cos(angle), Math.sin(angle), 0.22).normalize();
    const moonDirection = sunDirection.clone().negate();
    sunDisc.position.copy(sunDirection).multiplyScalar(920);
    moonDisc.position.copy(moonDirection).multiplyScalar(920);
    sunDisc.visible = sunDirection.y > 0.02;
    moonDisc.visible = moonDirection.y > 0.02;
    worldSkySphere.material.uniforms.starRotation.value = angle;
    stars.rotation.z = angle;

    const celestialLightDirection = sunDirection.y > 0 ? sunDirection : moonDirection;
    // Keep the visible sun low at dawn/dusk without stretching terrain shadows across several tiles.
    const lightDirection = new THREE.Vector3(
        celestialLightDirection.x,
        Math.max(celestialLightDirection.y, MIN_WORLD_SHADOW_LIGHT_Y),
        celestialLightDirection.z,
    ).normalize();
    const centerX = (MAX_CHUNK_X + 1) * CHUNK_SIZE / 2;
    const centerZ = (MAX_CHUNK_Y + 1) * CHUNK_SIZE / 2;
    directionalLight.target.position.set(centerX, 0, centerZ);
    directionalLight.position.set(
        centerX + lightDirection.x * 500,
        lightDirection.y * 500,
        centerZ + lightDirection.z * 500,
    );
    directionalLight.target.updateMatrixWorld();
    markWorldShadowsDirty();
}

export function setCelestialTime(minutes) {
    celestialMinutes = ((Math.trunc(Number(minutes) || 0) % 1440) + 1440) % 1440;
    updateCelestialPositions();
}

export function setSkyConditions(daylight, clarity, starlight) {
    const day = THREE.MathUtils.clamp(Number(daylight) || 0, 0, 1);
    const clear = THREE.MathUtils.clamp(Number(clarity) || 0, 0, 1);
    const visibleStars = THREE.MathUtils.clamp(Number(starlight) || 0, 0, 1);
    worldSkySphere.material.uniforms.daylight.value = day;
    worldSkySphere.material.uniforms.clarity.value = clear;
    worldSkySphere.material.uniforms.starlight.value = visibleStars;
    stars.material.opacity = visibleStars * clear;
    stars.visible = stars.material.opacity > 0.01;
    sunDisc.material.opacity = (0.06 + 0.94 * clear) * (0.35 + 0.65 * day);
    moonDisc.material.opacity = (0.04 + 0.88 * clear) * (1 - day * 0.7);
    scene.background.copy(new THREE.Color(0x101b29).lerp(new THREE.Color(0x8199a0), day * (0.65 + 0.35 * clear)));
}

// ===== Конец неба =====

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 5000);
camera.position.set(256, 300, 256);
camera.lookAt(256, 0, 256);

const renderer = createCompatibleWebGLRenderer(THREE, {
    antialias: true,
    logarithmicDepthBuffer: true,
}) || createUnavailableRenderer();
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
renderer.shadowMap.enabled = !renderer.isUnavailableRenderer;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
if (!renderer.isUnavailableRenderer) {
    renderer.shadowMap.autoUpdate = false;
    renderer.shadowMap.needsUpdate = true;
}
const globalCanvasContainer = document.getElementById('canvas-container');
globalCanvasContainer.appendChild(renderer.domElement);
const worldFpsCounter = createFpsCounter(globalCanvasContainer, 'Карта');
if (renderer.isUnavailableRenderer) {
    window.webGLUnavailable = true;
    showWebGLUnavailable(globalCanvasContainer);
}

const controls = new OrbitControls(camera, renderer.domElement);
controls.enablePointerCapture = false;
controls.enableDamping = true;
controls.dampingFactor = 0.05;
controls.maxPolarAngle = Math.PI / 2;
controls.target.set(256, 0, 256);

controls.mouseButtons = {
    LEFT: null,
    MIDDLE: THREE.MOUSE.PAN,
    RIGHT: THREE.MOUSE.ROTATE
};

function markWorldShadowsDirty() {
    if (!renderer.isUnavailableRenderer) worldShadowsDirty = true;
}

function refreshWorldShadows(now, cameraMoved) {
    if (!worldShadowsDirty || renderer.isUnavailableRenderer) return;
    if (cameraMoved && now - lastWorldShadowAt < SHADOW_REFRESH_MS) return;
    renderer.shadowMap.needsUpdate = true;
    worldShadowsDirty = false;
    lastWorldShadowAt = now;
    shadowRefreshesSinceSample += 1;
}

let isMouseDown = false;
let lastProcessedTileKey = null;
let controlsDisabled = false;
let brushActive = false;
let globalMouseUpHandler = null;

// --- Освещение и тени ---
const ambientLight = new THREE.AmbientLight(0xffffff, 0.3);
scene.add(ambientLight);

const directionalLight = new THREE.DirectionalLight(0xffffff, 1.2);
directionalLight.position.set(256, 300, 256);
directionalLight.castShadow = true;
directionalLight.shadow.mapSize.width = 4096;
directionalLight.shadow.mapSize.height = 4096;
directionalLight.shadow.camera.near = 0.5;
directionalLight.shadow.camera.far = 1000;
directionalLight.shadow.camera.left = -400;
directionalLight.shadow.camera.right = 400;
directionalLight.shadow.camera.top = 400;
directionalLight.shadow.camera.bottom = -400;
directionalLight.shadow.bias = 0;
directionalLight.shadow.normalBias = 0;
scene.add(directionalLight);
scene.add(directionalLight.target);

const fillLight = new THREE.DirectionalLight(0xffffff, 0.3);
fillLight.position.set(-50, 50, -50);
scene.add(fillLight);

const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let hoveredTile = null;
let editMode = false;
let currentBrushRadius = 0;

const highlightBoxGeo = new THREE.BoxGeometry(1.1, 0.1, 1.1);
const highlightBoxMat = new THREE.MeshBasicMaterial({ color: 0xffff00, transparent: true, opacity: 0.5 });
const highlightBox = new THREE.Mesh(highlightBoxGeo, highlightBoxMat);
const worldSculptBrushGeo = new THREE.RingGeometry(.48,.5,64).rotateX(-Math.PI/2);
export function updateWorldSculptHighlight(x,z,radius=0) {
    if(x==null){highlightBox.visible=false;return;}
    const size=radius*2+1,cx=radius===0?Math.floor(x)+.5:x,cz=radius===0?Math.floor(z)+.5:z;
    const positions=worldSculptBrushGeo.attributes.position;
    for(let i=0;i<positions.count;i++)positions.setY(i,sampleWorldGroundHeight(
        Math.min(window.MAP_CHUNKS_WIDTH*CHUNK_SIZE-.00001,Math.max(0,cx+positions.getX(i)*size)),
        Math.min(window.MAP_CHUNKS_HEIGHT*CHUNK_SIZE-.00001,Math.max(0,cz+positions.getZ(i)*size)))+.045);
    positions.needsUpdate=true;worldSculptBrushGeo.computeBoundingSphere();
    highlightBox.geometry=worldSculptBrushGeo;highlightBox.scale.set(size,1,size);highlightBox.position.set(cx,0,cz);highlightBox.visible=true;
}
highlightBox.position.set(0, 0.05, 0);
scene.add(highlightBox);
highlightBox.visible = false;

const tileInfoDiv = document.getElementById('tile-info');
const tileInfoContent = document.getElementById('tile-info-content');

// ---- Preview object ----
let previewObject = null;
let fenceLinePreview = null;
const fenceGroundSamplers = new Map();
function invalidateFenceGround(x,z) {
    for(let dz=-1;dz<=1;dz++)for(let dx=-1;dx<=1;dx++)fenceGroundSamplers.delete(`${x+dx},${z+dz}`);
}

export function sampleWorldGroundHeight(x,z) {
    const tx=Math.floor(x),tz=Math.floor(z);
    const key=`${tx},${tz}`,cached=fenceGroundSamplers.get(key);
    if(cached)return cached(1+x-tx,1+z-tz);
    const get=(a,b)=>chunksMap.get(`${Math.floor(a/CHUNK_SIZE)},${Math.floor(b/CHUNK_SIZE)}`)?.tilesData[b%CHUNK_SIZE]?.[a%CHUNK_SIZE] || null;
    const tile=get(tx,tz);
    if(!tile)return 1;
    const patch=Array.from({length:3},(_,b)=>Array.from({length:3},(_,a)=>get(tx+a-1,tz+b-1)));
    const sample=createTerrainHeightSampler(patch,1,1);
    if(fenceGroundSamplers.size>=8192)fenceGroundSamplers.clear();
    fenceGroundSamplers.set(key,sample);
    return sample(1+x-tx,1+z-tz);
}

function setFenceMatrix(matrix,object,x,z) {
    matrix.fromArray(fenceMatrixElements(object,x,z,sampleWorldGroundHeight));
    return matrix;
}

export function removeFenceLinePreview() {
    if(!fenceLinePreview)return;
    scene.remove(fenceLinePreview);fenceLinePreview.material.dispose();fenceLinePreview.dispose();fenceLinePreview=null;
}

export function showFenceLinePreview(sections) {
    if(!sections.length){if(fenceLinePreview)fenceLinePreview.visible=false;return;}
    if(!fenceLinePreview || fenceLinePreview.instanceMatrix.count<sections.length){
        removeFenceLinePreview();
        const material=fenceMat.clone();material.transparent=true;material.opacity=.55;material.depthWrite=false;
        fenceLinePreview=new THREE.InstancedMesh(fenceGeo,material,2**Math.ceil(Math.log2(sections.length)));
        fenceLinePreview.userData.fenceLinePreview=true;fenceLinePreview.frustumCulled=false;
        scene.add(fenceLinePreview);
    }
    fenceLinePreview.visible=true;fenceLinePreview.count=sections.length;
    fenceLinePreview.material.map=fenceMat.map;fenceLinePreview.material.color.set(sections[0].object.color||'#ffffff');
    const matrix=new THREE.Matrix4();
    sections.forEach((s,i)=>fenceLinePreview.setMatrixAt(i,setFenceMatrix(matrix,s.object,s.x,s.z)));
    fenceLinePreview.instanceMatrix.needsUpdate=true;
}

const pendingFenceTiles=new Set();
let fenceTerrainFrame=null;
function refreshWorldFenceNeighborhood(x,z,radius=2) {
    for(let dz=-radius;dz<=radius;dz++)for(let dx=-radius;dx<=radius;dx++)pendingFenceTiles.add(`${x+dx},${z+dz}`);
    if(fenceTerrainFrame!==null)return;
    fenceTerrainFrame=requestAnimationFrame(()=>{
        fenceTerrainFrame=null;
        const matrix=new THREE.Matrix4();
        for(const key of pendingFenceTiles){
            const [tx,tz]=key.split(',').map(Number),entry=chunksMap.get(`${Math.floor(tx/32)},${Math.floor(tz/32)}`);
            if(!entry?.fences)continue;
            const tileX=tx-entry.chunkX*32,tileY=tz-entry.chunkY*32;
            let ordinal=0;
            for(const object of entry.tilesData[tileY][tileX].objects||[])if(object.type==='fence'){
                const index=entry.objectIndices.fences[tileY*32+tileX][ordinal++];
                if(index!==undefined)entry.fences.setMatrixAt(index,setFenceMatrix(matrix,object,tx+.5+(object.x||0),tz+.5+(object.z||0)));
            }
            entry.fences.instanceMatrix.needsUpdate=true;
        }
        pendingFenceTiles.clear();markWorldShadowsDirty();
    });
}

function refreshFenceChunkNeighbors(cx,cy) {
    for(const entry of chunksMap.values())if(entry.fences && Math.abs(entry.chunkX-cx)<=1 && Math.abs(entry.chunkY-cy)<=1){
        entry.objectIndices.fences.forEach((indices,i)=>{if(indices.length)refreshWorldFenceNeighborhood(entry.chunkX*32+i%32,entry.chunkY*32+Math.floor(i/32),0)});
    }
}

function getGeometryForType(type) {
    if (WORLD_LANDMARKS[type]) return WORLD_LANDMARKS[type].geometry;
    switch(type) {
        case 'tree': return treeGeo;
        case 'house': return houseGeo;
        case 'fence': return fenceGeo;
        default: return new THREE.BoxGeometry(0.5, 0.5, 0.5);
    }
}

function createAnomalyPreview(x, y, z, type, color, scale) {
    const geo = new THREE.SphereGeometry(0.3, 8);
    const mat = new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.5 });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(x, y, z);
    mesh.scale.set(scale, scale, scale);
    return mesh;
}

export function createPreviewObject(tile, params) {
    const { type, anomalyType, color, offsetX, offsetZ, scale, rotation } = params;
    const worldX = tile.chunkX * CHUNK_SIZE + tile.tileX + 0.5 + offsetX;
    const worldZ = tile.chunkY * CHUNK_SIZE + tile.tileY + 0.5 + offsetZ;
    const height = tile.tileData.height || 1.0;
    const baseHalf = getBaseGroundOffset(type, anomalyType);
    const yPos = type==='road'&&params.bridge ? roadSurfaceHeight(tile.tileData,params) : height + baseHalf * scale;

    if (previewObject?.userData.previewType === type && previewObject.userData.anomalyType === anomalyType) {
        previewObject.position.set(worldX, yPos, worldZ);
        previewObject.scale.setScalar(scale);
        previewObject.rotation.y = THREE.MathUtils.degToRad(rotation);
        previewObject.material.color.set(color);
        if(type==='fence'){previewObject.matrixAutoUpdate=false;setFenceMatrix(previewObject.matrix,params,worldX,worldZ)}
        return;
    }
    removePreviewObject();

    const obj = createWorldObjectPreviewMesh(tile,params);
    scene.add(obj);
    previewObject = obj;
}

export function createWorldObjectPreviewMesh(tile, params) {
    const { type, anomalyType, color, offsetX, offsetZ, scale, rotation } = params;
    const worldX = tile.chunkX * CHUNK_SIZE + tile.tileX + .5 + offsetX;
    const worldZ = tile.chunkY * CHUNK_SIZE + tile.tileY + .5 + offsetZ;
    const height = tile.tileData.height ?? 1;
    const baseHalf = getBaseGroundOffset(type, anomalyType);
    const yPos = type==='road'&&params.bridge ? roadSurfaceHeight(tile.tileData,params) : height+baseHalf*scale;
    let obj;
    if (type === 'anomaly') {
        obj = createAnomalyPreview(worldX, yPos, worldZ, anomalyType, color, scale);
    } else {
        const geo = getGeometryForType(type);
        const mat = type === 'fence' ? fenceMat.clone() : new THREE.MeshStandardMaterial();
        mat.color.set(color);
        mat.transparent = true;
        mat.opacity = 0.5;
        obj = new THREE.Mesh(geo, mat);
        obj.position.set(worldX, yPos, worldZ);
        obj.scale.set(scale, scale, scale);
        obj.rotation.y = THREE.MathUtils.degToRad(rotation);
        if(type==='fence'){obj.matrixAutoUpdate=false;setFenceMatrix(obj.matrix,params,worldX,worldZ)}
    }
    obj.userData.previewType = type;
    obj.userData.anomalyType = anomalyType;
    obj.userData.privateGeometry = type === 'anomaly';
    return obj;
}

export function updatePreviewObject(params) {
    if (!previewObject) return;
    const currentTile = window.currentEditTile;
    if (currentTile) {
        createPreviewObject(currentTile, params);
    }
}

export function removePreviewObject() {
    if (previewObject) {
        scene.remove(previewObject);
        previewObject.material.dispose();
        if (previewObject.userData.privateGeometry) previewObject.geometry.dispose();
        previewObject = null;
    }
}
// ---- End Preview object ----

export function setMapDimensions(widthChunks, heightChunks) {
    worldWaterProfiles.resize(widthChunks*CHUNK_SIZE,heightChunks*CHUNK_SIZE);
    MAX_CHUNK_X = widthChunks - 1;
    MAX_CHUNK_Y = heightChunks - 1;
    worldTerrainMaterials.resize(widthChunks * CHUNK_SIZE, heightChunks * CHUNK_SIZE);
    for (const entry of chunksMap.values()) {
        worldTerrainMaterials.fill(entry.tilesData, entry.chunkX * CHUNK_SIZE, entry.chunkY * CHUNK_SIZE);
    }
    worldWaterProfiles.updateRegion(0,0,widthChunks*CHUNK_SIZE,heightChunks*CHUNK_SIZE);
    updateCelestialPositions();
    console.log(`Map dimensions set: ${widthChunks} x ${heightChunks} chunks`);
}

function getBaseDimensions(type, anomalyType) {
    const base = { width: 0.6, height: 0.6, depth: 0.6 };
    if (WORLD_LANDMARKS[type]) {
        const { width, height, depth } = WORLD_LANDMARKS[type];
        return { width, height, depth };
    }
    if (type === 'tree') {
        base.width = 0.6;
        base.height = 1.0;
        base.depth = 0.6;
    } else if (type === 'house') {
        base.width = 0.6;
        base.height = 0.6;
        base.depth = 0.6;
    } else if (type === 'fence') {
        base.width = 0.14;
        base.height = 0.5;
        base.depth = 0.8;
    } else if (type === 'anomaly') {
        switch(anomalyType) {
            case 'acid':
                base.width = 1.0;
                base.height = 0.1;
                base.depth = 1.0;
                break;
            default:
                base.width = 0.6;
                base.height = 0.6;
                base.depth = 0.6;
                break;
        }
    }
    return base;
}

function getBaseHalfHeight(type, anomalyType) {
    const dims = getBaseDimensions(type, anomalyType);
    return dims.height / 2;
}

function getBaseGroundOffset(type, anomalyType) {
    const landmark = WORLD_LANDMARKS[type];
    if (!landmark) return getBaseHalfHeight(type, anomalyType);
    if (!landmark.geometry.boundingBox) landmark.geometry.computeBoundingBox();
    return -landmark.geometry.boundingBox.min.y;
}

export function getObjectHalfHeight(type, anomalyType) {
    return getBaseHalfHeight(type, anomalyType);
}

export function getObjectHeightOffset(type, anomalyType) {
    return getBaseGroundOffset(type, anomalyType);
}

function getDefaultColorForType(type) {
    if (WORLD_LANDMARKS[type]) return WORLD_LANDMARKS[type].color;
    switch(type) {
        case 'tree': return '#2d5a27';
        case 'house': return '#8B4513';
        case 'fence': return '#ffffff';
        case 'anomaly': return '#00FFFF';
        default: return '#ffffff';
    }
}

// --- Текстуры для аномалий ---
function createSparkTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = 8;
    canvas.height = 8;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'white';
    ctx.beginPath();
    ctx.arc(4, 4, 2, 0, Math.PI * 2);
    ctx.fill();
    return new THREE.CanvasTexture(canvas);
}

function createFireTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 16;
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createRadialGradient(8, 8, 0, 8, 8, 8);
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(0.4, 'rgba(255,200,0,0.8)');
    gradient.addColorStop(0.8, 'rgba(255,0,0,0.3)');
    gradient.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 16, 16);
    return new THREE.CanvasTexture(canvas);
}

function createNoiseTexture() {
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    const imageData = ctx.createImageData(canvas.width, canvas.height);
    for (let i = 0; i < imageData.data.length; i += 4) {
        const val = Math.random() * 255;
        imageData.data[i] = val;
        imageData.data[i+1] = val;
        imageData.data[i+2] = val;
        imageData.data[i+3] = 255;
    }
    ctx.putImageData(imageData, 0, 0);
    return new THREE.CanvasTexture(canvas);
}

// --- Аномалии ---
function createElectricAnomaly(color) {
    const group = new THREE.Group();
    const col = new THREE.Color(color);
    const coreGeo = new THREE.SphereGeometry(0.2, 8);
    const coreMat = new THREE.MeshStandardMaterial({ color: col, emissive: col, transparent: true, opacity: 0.7 });
    const core = new THREE.Mesh(coreGeo, coreMat);
    group.add(core);

    const particleCount = 15;
    const positions = new Float32Array(particleCount * 3);
    const colors = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i++) {
        const r = 0.3 + Math.random() * 0.3;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.random() * Math.PI * 2;
        positions[i*3] = Math.sin(theta) * Math.cos(phi) * r;
        positions[i*3+1] = Math.sin(theta) * Math.sin(phi) * r;
        positions[i*3+2] = Math.cos(theta) * r;
        const c = col.clone().lerp(new THREE.Color(0xffffff), Math.random() * 0.5);
        colors[i*3] = c.r;
        colors[i*3+1] = c.g;
        colors[i*3+2] = c.b;
    }
    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    particleGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const particleMat = new THREE.PointsMaterial({ size: 0.05, vertexColors: true, blending: THREE.AdditiveBlending, transparent: true, map: createSparkTexture() });
    const particles = new THREE.Points(particleGeo, particleMat);
    group.add(particles);

    const lightningCount = 3;
    for (let j = 0; j < lightningCount; j++) {
        const points = [];
        const start = new THREE.Vector3(0, 0, 0);
        const end = new THREE.Vector3(
            (Math.random() - 0.5) * 0.6,
            (Math.random() - 0.5) * 0.6,
            (Math.random() - 0.5) * 0.6
        ).normalize().multiplyScalar(0.5);
        const segments = 4;
        points.push(start);
        for (let i = 1; i < segments; i++) {
            const t = i / segments;
            const p = start.clone().lerp(end, t);
            p.x += (Math.random() - 0.5) * 0.1;
            p.y += (Math.random() - 0.5) * 0.1;
            p.z += (Math.random() - 0.5) * 0.1;
            points.push(p);
        }
        points.push(end);
        const lineGeo = new THREE.BufferGeometry().setFromPoints(points);
        const lineMat = new THREE.LineBasicMaterial({ color: 0xffffff });
        const line = new THREE.Line(lineGeo, lineMat);
        group.add(line);
    }
    return group;
}

function createFireAnomaly(color) {
    const group = new THREE.Group();
    const coreGeo = new THREE.SphereGeometry(0.2, 6);
    const coreMat = new THREE.MeshStandardMaterial({ color: 0xff5500, emissive: 0xff2200, transparent: true, opacity: 0.8 });
    const core = new THREE.Mesh(coreGeo, coreMat);
    group.add(core);

    const particleCount = 20;
    const positions = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i++) {
        const r = 0.2 + Math.random() * 0.3;
        const angle = Math.random() * Math.PI * 2;
        const height = Math.random() * 0.8;
        positions[i*3] = Math.cos(angle) * r;
        positions[i*3+1] = height;
        positions[i*3+2] = Math.sin(angle) * r;
    }
    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const particleMat = new THREE.PointsMaterial({ size: 0.1, color: 0xffaa00, blending: THREE.AdditiveBlending, map: createFireTexture() });
    const particles = new THREE.Points(particleGeo, particleMat);
    group.add(particles);
    return group;
}

function createAcidAnomaly(color) {
    const group = new THREE.Group();
    const col = new THREE.Color(color);
    const puddleGeo = new THREE.CircleGeometry(0.5, 8);
    const puddleMat = new THREE.MeshStandardMaterial({ color: col, emissive: col.clone().multiplyScalar(0.3), transparent: true, opacity: 0.6, side: THREE.DoubleSide });
    const puddle = new THREE.Mesh(puddleGeo, puddleMat);
    puddle.rotation.x = -Math.PI / 2;
    puddle.position.y = 0.05;
    group.add(puddle);

    const bubbleCount = 5;
    for (let i = 0; i < bubbleCount; i++) {
        const bubbleGeo = new THREE.SphereGeometry(0.05, 4);
        const bubbleMat = new THREE.MeshStandardMaterial({ color: 0x88ff88, emissive: 0x224422, transparent: true, opacity: 0.5 });
        const bubble = new THREE.Mesh(bubbleGeo, bubbleMat);
        bubble.position.set(
            (Math.random() - 0.5) * 0.4,
            0.1 + Math.random() * 0.1,
            (Math.random() - 0.5) * 0.4
        );
        group.add(bubble);
    }
    return group;
}

function createVoidAnomaly(color) {
    const group = new THREE.Group();
    const col = new THREE.Color(color);
    const geo = new THREE.SphereGeometry(0.3, 16);
    const noiseTex = createNoiseTexture();
    const mat = new THREE.MeshPhongMaterial({ map: noiseTex, color: col, emissive: col.clone().multiplyScalar(0.5), transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending, side: THREE.DoubleSide });
    const sphere = new THREE.Mesh(geo, mat);
    group.add(sphere);

    const pointCount = 8;
    const pointPositions = [];
    for (let i = 0; i < pointCount; i++) {
        const r = 0.2;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        pointPositions.push(
            Math.sin(phi) * Math.cos(theta) * r,
            Math.sin(phi) * Math.sin(theta) * r,
            Math.cos(phi) * r
        );
    }
    const pointGeo = new THREE.BufferGeometry();
    pointGeo.setAttribute('position', new THREE.Float32BufferAttribute(pointPositions, 3));
    const pointMat = new THREE.PointsMaterial({ size: 0.05, color: 0xffffff, blending: THREE.AdditiveBlending });
    const points = new THREE.Points(pointGeo, pointMat);
    group.add(points);
    return group;
}

function createAnomalyLOD(x, y, z, type = 'electric', baseColor = '#00ffff', scale = 1.0) {
    const lod = new THREE.LOD();
    const color = new THREE.Color(baseColor);

    const nearGroup = createAnomalyEffect(type, color, scale);
    nearGroup.position.set(0, 0, 0);
    lod.addLevel(nearGroup, 0);

    lod.position.set(x, y, z);
    lod.userData.farColor = color;
    lod.userData.farScale = scale;
    lod.userData.cullRadius = Math.max(1, 4 * scale);
    return lod;
}

function createAnomalyFieldLOD(tile, x, z) {
    const field = tile.anomaly_field;
    if (!field?.name) return null;
    const type = String(field.field_type || '').toLowerCase();
    const visual = type.includes('электр') ? ['electric', '#7fd6f3']
        : type.includes('термич') ? ['fire', '#e69967']
            : type.includes('химич') ? ['acid', '#9ecb80']
                : type.includes('радио') ? ['radiation', '#bbd565']
                    : type.includes('пси') ? ['psi', '#8eace4']
                        : ['void', '#c3a4d5'];
    const rank = Math.max(1, Math.min(4, Number(field.rank) || 1));
    const lod = createAnomalyLOD(
        x, (tile.height || 1) + 0.12, z, visual[0], visual[1], 0.55 + rank * 0.12,
    );
    lod.userData.anomalyField = field.name;
    return lod;
}

const FAR_ANOMALY_DISTANCE_SQ = 85 * 85;
const farAnomalyGeo = new THREE.SphereGeometry(0.2, 4);
const farAnomalyMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
const visibleNearAnomalies = [];

function createFarAnomalyInstances(lods) {
    if (!lods.length) return null;
    const mesh = new THREE.InstancedMesh(farAnomalyGeo, farAnomalyMat, lods.length);
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(lods.length * 3), 3);
    mesh.count = 0;
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    scene.add(mesh);
    return mesh;
}

function disposeChunkAnomalyLODs(lods) {
    (lods || []).forEach(lod => {
        scene.remove(lod);
        lod.traverse(node => {
            node.geometry?.dispose();
            const materials = Array.isArray(node.material) ? node.material : [node.material];
            materials.forEach(material => {
                material?.map?.dispose();
                material?.dispose();
            });
        });
    });
}

function updateFarAnomalyInstances(chunk, visible) {
    if (!chunk.anomalyLODs?.length) return;
    if (!visible) {
        chunk.anomalyLODs.forEach(lod => lod.visible = false);
        if (chunk.farAnomalies) chunk.farAnomalies.visible = false;
        return;
    }
    const far = [];
    for (const lod of chunk.anomalyLODs) {
        anomalyVisibilitySphere.center.copy(lod.position);
        anomalyVisibilitySphere.radius = lod.userData.cullRadius;
        if (!chunkFrustum.intersectsSphere(anomalyVisibilitySphere)) {
            lod.visible = false;
            continue;
        }
        const distant = lod.position.distanceToSquared(camera.position) >= FAR_ANOMALY_DISTANCE_SQ;
        lod.visible = !distant;
        if (distant) far.push(lod);
        else visibleNearAnomalies.push(lod);
    }
    const mesh = chunk.farAnomalies;
    if (!mesh) return;
    const previous = chunk.farAnomalyActive || [];
    const changed = far.length !== previous.length || far.some((lod, index) => lod !== previous[index]);
    if (changed) {
        const dummy = new THREE.Object3D();
        far.forEach((lod, index) => {
            dummy.position.copy(lod.position);
            dummy.scale.setScalar(lod.userData.farScale);
            dummy.updateMatrix();
            mesh.setMatrixAt(index, dummy.matrix);
            mesh.setColorAt(index, lod.userData.farColor);
        });
        mesh.count = far.length;
        mesh.instanceMatrix.needsUpdate = true;
        mesh.instanceColor.needsUpdate = true;
        chunk.farAnomalyActive = far;
    }
    mesh.visible = mesh.count > 0;
}

function animateChunkAnomalies(time) {
    animateAnomalyEffects(visibleNearAnomalies, time);
}

const worldWaterTileAt=(x,z)=>chunksMap.get(`${Math.floor(x/CHUNK_SIZE)},${Math.floor(z/CHUNK_SIZE)}`)
    ?.tilesData[((z%CHUNK_SIZE)+CHUNK_SIZE)%CHUNK_SIZE]?.[((x%CHUNK_SIZE)+CHUNK_SIZE)%CHUNK_SIZE];
export const worldWaterProfiles=createWaterProfileLayer(THREE,worldWaterTileAt);
const waterMat = createWaterMaterial({ world:true,profileLayer:worldWaterProfiles });
const waterTexture = waterMat.map;
const waterHiddenTopMat = new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false });
// Retain compact box instances for picking; draw only the joined surface/rim.
waterHiddenTopMat.visible = false;
const waterInstanceMaterials = Array(6).fill(waterHiddenTopMat);
const worldWaterBankMat = new THREE.MeshStandardMaterial({
    map: getSurfaceTexture(THREE, 'local_terrain'), vertexColors: true,
    side: THREE.DoubleSide, roughness: 0.97,
});
const groundGeo = new THREE.BoxGeometry(1, 1, 1);
// Terrain surfaces already cover the box tops. Keep the full box for picking,
// but submit only its sides to avoid shading the buried caps a second time.
const groundSideGeo = groundGeo.clone();
const groundSideIndices = [];
for (let i = 0; i < groundGeo.index.count; i += 3) {
    const a = groundGeo.index.getX(i);
    if (Math.abs(groundGeo.attributes.normal.getY(a)) < 0.5) {
        groundSideIndices.push(a, groundGeo.index.getX(i + 1), groundGeo.index.getX(i + 2));
    }
}
groundSideGeo.setIndex(groundSideIndices);
groundSideGeo.clearGroups();

function raycastGroundInstances(raycaster, intersections) {
    const renderedGeometry = this.geometry;
    this.geometry = groundGeo;
    try {
        THREE.InstancedMesh.prototype.raycast.call(this, raycaster, intersections);
    } finally {
        this.geometry = renderedGeometry;
    }
}

function raycastSmoothTerrain(raycaster, intersections) {
    // Rendering omits top triangles already covered by terrainSurface. Picking
    // keeps the original triangle order, including tileByFace/brush metadata.
    const renderedIndex = this.geometry.index;
    this.geometry.setIndex(null);
    try {
        THREE.Mesh.prototype.raycast.call(this, raycaster, intersections);
    } finally {
        this.geometry.setIndex(renderedIndex);
    }
}
const groundMat = new THREE.MeshStandardMaterial({ map: getSurfaceTexture(THREE, 'local_terrain') });
const smoothGroundMat = new THREE.MeshStandardMaterial({
    map: getSurfaceTexture(THREE, 'local_terrain'), vertexColors: true, side: THREE.DoubleSide,
});
worldTerrainMaterials.apply(groundMat, { sides: true });
worldTerrainMaterials.apply(smoothGroundMat, { sides: true, smooth: true });
let worldTerrainSidesPrepared = false;
let worldIceSurfaceMaterial = null;

function clearWorldIceSurface(entry) {
    if (!entry.iceSurface) return;
    scene.remove(entry.iceSurface);
    entry.iceSurface.geometry.dispose();
    entry.iceSurface = null;
}

function refreshWorldIceSurface(entry, source) {
    // Прозрачен только верхний слой льда. Остальной грунт сохраняет обычный проход.
    if (!entry.tilesData.some(row => row.some(tile => tile.terrain === 'ice'))) {
        clearWorldIceSurface(entry);
        return;
    }
    const p = source.getAttribute('position'), n = source.getAttribute('normal');
    const positions = [], normals = [];
    for (let i = 0; i < p.count; i += 3) {
        const x = Math.floor((p.getX(i) + p.getX(i + 1) + p.getX(i + 2)) / 3) - entry.chunkX * CHUNK_SIZE;
        const z = Math.floor((p.getZ(i) + p.getZ(i + 1) + p.getZ(i + 2)) / 3) - entry.chunkY * CHUNK_SIZE;
        if (entry.tilesData[z]?.[x]?.terrain !== 'ice') continue;
        for (let j = i; j < i + 3; j++) {
            positions.push(p.getX(j), p.getY(j) + 0.002, p.getZ(j));
            normals.push(n.getX(j), n.getY(j), n.getZ(j));
        }
    }
    if (!positions.length) { clearWorldIceSurface(entry); return; }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    if (entry.iceSurface) {
        entry.iceSurface.geometry.dispose();
        entry.iceSurface.geometry = geometry;
    } else {
        if (!worldIceSurfaceMaterial) {
            worldIceSurfaceMaterial = new THREE.MeshStandardMaterial({
                transparent: true, depthWrite: false, roughness: 0.3,
                polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
            });
            worldTerrainMaterials.apply(worldIceSurfaceMaterial, { iceGlaze: true });
        }
        entry.iceSurface = new THREE.Mesh(geometry, worldIceSurfaceMaterial);
        entry.iceSurface.receiveShadow = true;
        entry.iceSurface.frustumCulled = false;
        entry.iceSurface.raycast = () => {};
        scene.add(entry.iceSurface);
    }
    entry.iceSurface.visible = entry.terrainVisible;
}
function prepareWorldTerrainSides() {
    if (worldTerrainSidesPrepared || renderer.isUnavailableRenderer) return;
    worldTerrainSidesPrepared = true;
    const geometry = new THREE.BufferGeometry();
    for (const [name,size] of [['position',3],['normal',3],['color',3],['uv',2],['terrainTop',1],['terrainTileCoord',2]]) {
        geometry.setAttribute(name,new THREE.Float32BufferAttribute(new Float32Array(size*3),size));
    }
    const mesh = new THREE.Mesh(geometry,smoothGroundMat);
    mesh.receiveShadow = true;
    scene.add(mesh);
    try { renderer.compile(scene,camera); }
    finally { scene.remove(mesh);geometry.dispose(); }
}
worldTerrainMaterials.apply(worldWaterBankMat, { sides:true, smooth:true, shore:true });

function refreshWorldTerrainSurface(entry, smoothData) {
    const metrics = typeof window !== 'undefined' && window.worldLoadMetrics?.fullLoadMs === null
        ? window.worldLoadMetrics : null;
    let geometryStartedAt = metrics ? performance.now() : 0;
    const baseX = entry.chunkX * CHUNK_SIZE;
    const baseZ = entry.chunkY * CHUNK_SIZE;
    const textureOriginX = baseX - 2;
    const textureOriginZ = baseZ - 2;
    const pixelsPerTile = 12;
    const textureSize = 512;
    const positions = [];
    const normals = [];
    const uvs = [];
    const addVertex = (x, y, z, nx, ny, nz) => {
        positions.push(x, y, z);
        normals.push(nx, ny, nz);
        uvs.push((x - textureOriginX) * pixelsPerTile / textureSize,
            (z - textureOriginZ) * pixelsPerTile / textureSize);
    };
    for (let z = 0; z < CHUNK_SIZE; z++) {
        for (let x = 0; x < CHUNK_SIZE; x++) {
            const index = z * CHUNK_SIZE + x;
            const tile = entry.tilesData[z][x];
            if (tile.terrain === 'water' || entry.smoothTileIndices.has(index)) continue;
            const left = baseX + x;
            const near = baseZ + z;
            const y = (Number(tile.height) || 1) + 0.002;
            addVertex(left, y, near, 0, 1, 0);
            addVertex(left, y, near + 1, 0, 1, 0);
            addVertex(left + 1, y, near, 0, 1, 0);
            addVertex(left + 1, y, near, 0, 1, 0);
            addVertex(left, y, near + 1, 0, 1, 0);
            addVertex(left + 1, y, near + 1, 0, 1, 0);
        }
    }
    for (let index = 0; index < smoothData.topByVertex.length; index += 3) {
        if (smoothData.topByVertex[index] < 0.5) continue;
        for (let corner = 0; corner < 3; corner++) {
            const offset = (index + corner) * 3;
            addVertex(smoothData.positions[offset] + baseX - 1,
                smoothData.positions[offset + 1] + 0.002,
                smoothData.positions[offset + 2] + baseZ - 1,
                smoothData.normals[offset], smoothData.normals[offset + 1], smoothData.normals[offset + 2]);
        }
    }
    if (!positions.length) {
        clearWorldIceSurface(entry);
        if (entry.terrainSurface) {
            scene.remove(entry.terrainSurface);
            entry.terrainSurface.geometry.dispose();
            entry.terrainSurface.material.dispose();
            entry.terrainSurface = null;
        }
        entry.terrainTexture?.dispose();
        entry.terrainTexture = null;
        entry.terrainTextureRegion = null;
        if (metrics) metrics.terrainStages.geometryMs += performance.now() - geometryStartedAt;
        return;
    }
    if (metrics) metrics.terrainStages.geometryMs += performance.now() - geometryStartedAt;
    refreshWorldTerrainPixels(entry);
    geometryStartedAt = metrics ? performance.now() : 0;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    refreshWorldIceSurface(entry, geometry);
    if (entry.terrainSurface) {
        const previousGeometry = entry.terrainSurface.geometry;
        entry.terrainSurface.geometry = geometry;
        previousGeometry.dispose();
        entry.terrainSurface.visible = entry.terrainVisible;
        if (metrics) metrics.terrainStages.geometryMs += performance.now() - geometryStartedAt;
        return;
    }
    const material = createWorldTerrainSurfaceMaterial(entry.terrainTexture, entry.materialLayer);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.frustumCulled = false;
    mesh.visible = entry.terrainVisible;
    mesh.receiveShadow = true;
    mesh.raycast = () => {};
    scene.add(mesh);
    entry.terrainSurface = mesh;
    if (metrics) metrics.terrainStages.geometryMs += performance.now() - geometryStartedAt;
}

function createWorldTerrainSurfaceMaterial(texture, layer) {
    const material = new THREE.MeshStandardMaterial({
        map: texture, side: THREE.DoubleSide, roughness: 0.97,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
    });
    worldGrassDetail.apply(material);
    layer?.apply(material);
    return material;
}

let worldTerrainWarmupMaterial = null;
function prepareWorldTerrainSurfaceShader() {
    if (worldTerrainWarmupMaterial || renderer.isUnavailableRenderer) return;
    const startedAt = performance.now();
    const texture = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1,
        THREE.RGBAFormat, THREE.UnsignedByteType);
    texture.encoding = THREE.sRGBEncoding;
    worldTerrainWarmupMaterial = createWorldTerrainSurfaceMaterial(texture, worldTerrainMaterials);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 0, 1], 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0, 1, 0, 0, 1, 0, 0, 1, 0], 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(9).fill(1), 3));
    geometry.setAttribute('terrainTop', new THREE.Float32BufferAttribute([0, 0, 0], 1));
    geometry.setAttribute('terrainTileCoord', new THREE.Float32BufferAttribute([0, 0, 0, 0, 0, 0], 2));
    const mesh = new THREE.Mesh(geometry, worldTerrainWarmupMaterial);
    mesh.receiveShadow = true;
    const bank = new THREE.Mesh(geometry, worldWaterBankMat);
    bank.receiveShadow = true;
    const water = new THREE.Mesh(geometry, waterMat);
    scene.add(mesh, bank, water);
    try { renderer.compile(scene, camera); }
    catch (error) {
        worldTerrainWarmupMaterial.dispose();
        texture.dispose();
        worldTerrainWarmupMaterial = null;
        throw error;
    } finally {
        // Материал удерживает готовую программу для настоящих поверхностей.
        scene.remove(mesh, bank, water);
        geometry.dispose();
        if (window.worldLoadMetrics) window.worldLoadMetrics.terrainMaterialWarmupMs = performance.now() - startedAt;
    }
}

function sampleWorldTerrainKind(entry, x, z) {
    const chunk = chunksMap.get(`${Math.floor(x / CHUNK_SIZE)},${Math.floor(z / CHUNK_SIZE)}`);
    const tile = chunk?.tilesData[z - chunk.chunkY * CHUNK_SIZE]?.[x - chunk.chunkX * CHUNK_SIZE];
    if (tile) return tile.terrain;
    const localX = Math.max(0, Math.min(CHUNK_SIZE - 1, x - entry.chunkX * CHUNK_SIZE));
    const localZ = Math.max(0, Math.min(CHUNK_SIZE - 1, z - entry.chunkY * CHUNK_SIZE));
    return entry.tilesData[localZ]?.[localX]?.terrain || 'grass';
}

async function prepareInitialWorldTerrainPixels({ signal, onProgress } = {}) {
    const entries = [...chunksMap.values()].filter(entry => !entry.terrainTexture
        && entry.tilesData.some(row => row.some(tile => tile.terrain !== 'water')));
    if (entries.length < 8 || typeof Worker === 'undefined' || signal?.aborted) return;
    const startedAt = performance.now();
    const metrics = window.worldLoadMetrics;
    const originalEntries = new Map(entries.map(entry => [`${entry.chunkX},${entry.chunkY}`, entry]));
    const tasks = entries.map(entry => {
        const originX = entry.chunkX * CHUNK_SIZE - 2, originZ = entry.chunkY * CHUNK_SIZE - 2;
        const minX = Math.floor(originX + 0.5 / 12 - 0.5), minZ = Math.floor(originZ + 0.5 / 12 - 0.5);
        const maxX = Math.floor(originX + 511.5 / 12 - 0.5) + 1, maxZ = Math.floor(originZ + 511.5 / 12 - 0.5) + 1;
        return { key: `${entry.chunkX},${entry.chunkY}`, revision: entry.terrainPixelRevision || 0,
            originX, originZ, minX, minZ, textureSize: 512, pixelsPerTile: 12,
            photos: worldGrassGroundPixelsEnabled,
            rows: Array.from({ length: maxZ - minZ + 1 }, (_, z) =>
                Array.from({ length: maxX - minX + 1 }, (_, x) => sampleWorldTerrainKind(entry, minX + x, minZ + z))) };
    });
    try {
        await prepareWorldTerrainPixelImages(tasks, { signal, groundPixels: getGrassGroundPixelData(), onProgress,
            onStarted: prepareWorldTerrainSurfaceShader,
            onResult(task, result) {
                const entry = chunksMap.get(task.key);
                // Правка другого участника за время расчёта отменяет старый результат.
                if (entry !== originalEntries.get(task.key) || entry.terrainTexture
                    || (entry.terrainPixelRevision || 0) !== task.revision) return;
                entry.preparedTerrainPixels = result;
                if (metrics) {
                    metrics.terrainPixelWorkerChunks = (metrics.terrainPixelWorkerChunks || 0) + 1;
                    metrics.terrainPixelWorkerWorkMs = (metrics.terrainPixelWorkerWorkMs || 0) + result.workMs;
                }
            } });
    } catch (error) {
        // Недоступный Worker не мешает игре: обычная сборка рассчитает остальные изображения.
        if (!signal?.aborted) console.warn('Фоновый расчёт текстур недоступен; используем основной поток', error);
    } finally {
        if (metrics) metrics.terrainPixelPreparationMs = performance.now() - startedAt;
    }
}

function refreshWorldTerrainPixels(entry) {
    const baseX = entry.chunkX * CHUNK_SIZE;
    const baseZ = entry.chunkY * CHUNK_SIZE;
    const textureOriginX = baseX - 2;
    const textureOriginZ = baseZ - 2;
    const pixelsPerTile = 12;
    const textureSize = 512;
    const sampleTerrain = (x, z) => sampleWorldTerrainKind(entry, x, z);
    const metrics = typeof window !== 'undefined' && window.worldLoadMetrics?.fullLoadMs === null
        ? window.worldLoadMetrics : null;
    const startedAt = metrics ? performance.now() : 0;
    const profile = metrics ? {
        pixels: 0, terrainLookups: 0, photoColorCalls: 0,
        photoSampledCalls: 0, photoSampledMs: 0,
    } : null;
    let pass = 'reused';
    if (!entry.terrainTexture) {
        pass = 'full';
        const prepared = entry.preparedTerrainPixels;
        entry.preparedTerrainPixels = null;
        if (prepared && profile) Object.assign(profile, prepared.profile);
        const image = prepared?.image || createTerrainPixelsRegion(sampleTerrain, textureOriginX, textureOriginZ,
            textureSize, pixelsPerTile, worldGrassGroundPixelsEnabled, profile, true);
        const texture = new THREE.DataTexture(image.data, image.width, image.height,
            THREE.RGBAFormat, THREE.UnsignedByteType);
        texture.magFilter = THREE.LinearFilter;
        texture.minFilter = THREE.LinearMipmapLinearFilter;
        texture.generateMipmaps = true;
        texture.encoding = THREE.sRGBEncoding;
        texture.needsUpdate = true;
        entry.terrainTexture = texture;
    } else if (entry.terrainTextureRegion) {
        if (updateTerrainPixelsRegion(entry.terrainTexture.image,
            sampleTerrain, textureOriginX, textureOriginZ, entry.terrainTextureRegion, pixelsPerTile,
            worldGrassGroundPixelsEnabled, profile, true)) {
            entry.terrainTexture.needsUpdate = true;
            pass = 'partial';
        }
    }
    entry.terrainTextureRegion = null;
    if (metrics) {
        const elapsed = performance.now() - startedAt;
        metrics.terrainStages.groundPixelsMs += elapsed;
        const details = metrics.groundPixelDetails;
        details[`${pass}Ms`] += elapsed;
        details[`${pass}Passes`]++;
        if (pass !== 'reused') {
            details[`${pass}Pixels`] += profile.pixels;
            details.terrainLookups += profile.terrainLookups;
            details.photoColorCalls += profile.photoColorCalls;
            details.photoSampledCalls += profile.photoSampledCalls;
            details.photoSampledMs += profile.photoSampledMs;
            // Sampled per-call timing is an estimate, not a separate GPU measurement.
            const photoEstimate = profile.photoSampledCalls
                ? Math.min(elapsed, profile.photoSampledMs * profile.photoColorCalls / profile.photoSampledCalls)
                : 0;
            details.photoSamplingEstimateMs += photoEstimate;
            details.otherProcessingEstimateMs += elapsed - photoEstimate;
        }
    }
}

function fillChunkTerrainInstances(ground, water, tilesData, chunkX, chunkY, visible = true) {
    const size = tilesData.length;
    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    const groundIndices = new Int16Array(size * size).fill(-1);
    const waterIndices = new Int16Array(size * size).fill(-1);
    let landCount = 0;
    let waterCount = 0;

    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const tile = tilesData[y][x];
            const tileIndex = y * size + x;
            const height = tile.height || 1.0;
            dummy.position.set(chunkX * size + x + 0.5, height / 2, chunkY * size + y + 0.5);
            if (tile.terrain === 'water') {
                dummy.scale.set(1, height, 1);
                dummy.updateMatrix();
                waterIndices[tileIndex] = waterCount;
                water.setMatrixAt(waterCount++, dummy.matrix);
            } else {
                dummy.scale.set(1, height, 1);
                dummy.updateMatrix();
                groundIndices[tileIndex] = landCount;
                ground.setMatrixAt(landCount, dummy.matrix);
                color.setHex(terrainColors[tile.terrain] || 0x3a5f0b);
                ground.setColorAt(landCount++, color);
            }
        }
    }

    ground.count = landCount;
    water.count = waterCount;
    ground.visible = visible && landCount > 0;
    water.visible = visible && waterCount > 0;
    ground.instanceMatrix.needsUpdate = landCount > 0;
    ground.instanceColor.needsUpdate = landCount > 0;
    water.instanceMatrix.needsUpdate = waterCount > 0;
    return { groundIndices, waterIndices };
}

function clipWorldTerrainData(data, include) {
    const result={positions:[],normals:[],topByVertex:[],tileByFace:[],tileByVertex:[],foundationByTile:new Map()};
    for(const [key,height] of data.foundationByTile){const [x,z]=key.split(',').map(Number);if(include(x,z))result.foundationByTile.set(key,height);}
    data.tileByFace.forEach((tile,face)=>{
        if(!include(tile.x,tile.z))return;
        result.tileByFace.push(tile);
        for(let corner=0;corner<3;corner++){
            const i=face*3+corner;result.tileByVertex.push(tile);result.topByVertex.push(data.topByVertex[i]);
            for(let axis=0;axis<3;axis++){result.positions.push(data.positions[i*3+axis]);result.normals.push(data.normals[i*3+axis]);}
        }
    });
    return result;
}

function buildWorldTerrainPreviewData(entry, padded) {
    const region = entry.terrainPreviewRegion;
    const baseX = entry.chunkX * CHUNK_SIZE - 1;
    const baseZ = entry.chunkY * CHUNK_SIZE - 1;
    // Shape depends on adjacent tiles; shared vertex normals need another ring.
    const minX = region.minX - baseX - 2, maxX = region.maxX - baseX + 2;
    const minZ = region.minZ - baseZ - 2, maxZ = region.maxZ - baseZ + 2;
    const affected = (x, z) => x >= minX && x <= maxX && z >= minZ && z <= maxZ;
    // Neighbor faces contribute to normals even across the chunk boundary.
    const patch = clipWorldTerrainData(buildSmoothTerrainData(padded, (x, z) =>
        x >= minX - 1 && x <= maxX + 1 && z >= minZ - 1 && z <= maxZ + 1),
        (x,z)=>x>0&&x<=CHUNK_SIZE&&z>0&&z<=CHUNK_SIZE);
    const previous = entry.smoothTerrainData;
    const result = { positions: [], normals: [], topByVertex: [], tileByFace: [],
        tileByVertex: [], foundationByTile: new Map(previous.foundationByTile) };
    for (const key of result.foundationByTile.keys()) {
        const [x, z] = key.split(',').map(Number);
        if (affected(x, z)) result.foundationByTile.delete(key);
    }
    for (const [key, height] of patch.foundationByTile) {
        const [x, z] = key.split(',').map(Number);
        if (affected(x, z)) result.foundationByTile.set(key, height);
    }
    for (const [data, replace] of [[previous, false], [patch, true]]) {
        data.tileByFace.forEach((tile, face) => {
            if (affected(tile.x, tile.z) !== replace) return;
            result.tileByFace.push(tile);
            for (let vertex = 0; vertex < 3; vertex++) {
                const index = face * 3 + vertex;
                result.tileByVertex.push(tile);
                result.topByVertex.push(data.topByVertex[index]);
                for (let axis = 0; axis < 3; axis++) {
                    result.positions.push(data.positions[index * 3 + axis]);
                    result.normals.push(data.normals[index * 3 + axis]);
                }
            }
        });
    }
    return result;
}

function refreshWorldTerrainChunk(entry, neighborOnly = false, preview = false) {
    const metrics = typeof window !== 'undefined' && window.worldLoadMetrics?.fullLoadMs === null
        ? window.worldLoadMetrics : null;
    let stageStartedAt = metrics ? performance.now() : 0;
    const { chunkX, chunkY, tilesData } = entry;
    const padded = Array.from({ length: CHUNK_SIZE + 2 }, (_, z) => (
        Array.from({ length: CHUNK_SIZE + 2 }, (_, x) => {
            const worldX = chunkX * CHUNK_SIZE + x - 1;
            const worldZ = chunkY * CHUNK_SIZE + z - 1;
            const neighbor = chunksMap.get(`${Math.floor(worldX / CHUNK_SIZE)},${Math.floor(worldZ / CHUNK_SIZE)}`);
            return neighbor?.tilesData[worldZ - neighbor.chunkY * CHUNK_SIZE]?.[worldX - neighbor.chunkX * CHUNK_SIZE]
                || null;
        })
    ));
    for (const row of padded) {
        for (const tile of row) {
            if (tile) entry.bounds.max.y = Math.max(entry.bounds.max.y, (Number(tile.height) || 1) + 0.5);
        }
    }
    const border = worldTerrainOuterRing(padded);
    if (neighborOnly && entry.terrainBorder && !worldTerrainBoundaryNeedsGeometry(entry.terrainBorder, border, padded)) {
        entry.terrainBorder = border;
        if (metrics) {
            metrics.terrainStages.geometryMs += performance.now() - stageStartedAt;
            metrics.terrainPartialRefreshes++;
        }
        if (entry.waterShoreDirty) {
            stageStartedAt = metrics ? performance.now() : 0;
            refreshWorldWaterChunk(entry, padded);
            if (metrics) metrics.terrainStages.waterMs += performance.now() - stageStartedAt;
        }
        if (entry.terrainTexture) refreshWorldTerrainPixels(entry);
        return;
    }
    entry.terrainBorder = border;
    if (metrics && !preview) metrics.terrainFullRefreshes++;
    if (entry.smoothTerrain) {
        scene.remove(entry.smoothTerrain);
        entry.smoothTerrain.geometry.dispose();
        entry.smoothTerrain = null;
    }
    if (entry.smoothTerrainBounds) {
        const index = chunkBounds.indexOf(entry.smoothTerrainBounds);
        if (index >= 0) chunkBounds.splice(index, 1);
        entry.smoothTerrainBounds = null;
    }
    const data = preview ? buildWorldTerrainPreviewData(entry, padded)
        : clipWorldTerrainData(buildSmoothTerrainData(padded, () => true),
            (x, z) => x > 0 && x <= CHUNK_SIZE && z > 0 && z <= CHUNK_SIZE);
    entry.terrainPreviewRegion = null;
    entry.smoothTerrainData = data;
    const smoothTiles = new Set(data.tileByFace.map(({ x, z }) => (z - 1) * CHUNK_SIZE + x - 1));
    entry.smoothTileIndices = smoothTiles;
    entry.smoothTileFoundation = new Map();
    if (metrics) {
        metrics.terrainStages.geometryMs += performance.now() - stageStartedAt;
        stageStartedAt = performance.now();
    }
    const dummy = new THREE.Object3D();
    for (let z = 0; z < CHUNK_SIZE; z++) {
        for (let x = 0; x < CHUNK_SIZE; x++) {
            const index = entry.groundIndices[z * CHUNK_SIZE + x];
            if (index < 0) continue;
            const height = Number(tilesData[z][x].height) || 1;
            const tileIndex = z * CHUNK_SIZE + x;
            if (smoothTiles.has(tileIndex)) {
                const foundation = data.foundationByTile.get(`${x + 1},${z + 1}`);
                entry.smoothTileFoundation.set(tileIndex, Math.max(0.05, foundation - 0.085));
            }
            const foundationHeight = entry.smoothTileFoundation.get(tileIndex) ?? height;
            dummy.position.set(chunkX * CHUNK_SIZE + x + 0.5, foundationHeight / 2,
                chunkY * CHUNK_SIZE + z + 0.5);
            dummy.scale.set(1, foundationHeight, 1);
            dummy.updateMatrix();
            entry.ground.setMatrixAt(index, dummy.matrix);
        }
    }
    entry.ground.instanceMatrix.needsUpdate = true;
    if (metrics) {
        metrics.terrainStages.dependentMeshesMs += performance.now() - stageStartedAt;
        stageStartedAt = performance.now();
    }
    if (entry.waterShoreDirty) refreshWorldWaterChunk(entry, padded);
    if (metrics) {
        metrics.terrainStages.waterMs += performance.now() - stageStartedAt;
        stageStartedAt = performance.now();
    }
    refreshWorldTerrainSurface(entry, data);
    if (!data.positions.length) return;
    stageStartedAt = metrics ? performance.now() : 0;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.positions, 3));
    geometry.setAttribute('terrainTop', new THREE.Float32BufferAttribute(data.topByVertex, 1));
    const colors = new Float32Array(data.positions.length);
    const coords = new Float32Array(data.tileByVertex.length * 2);
    const uvs = new Float32Array(data.tileByVertex.length * 2);
    const color = new THREE.Color();
    data.tileByVertex.forEach(({ x, z }, index) => {
        color.setHex(terrainColors[padded[z][x].terrain] || terrainColors.grass);
        colors.set([color.r, color.g, color.b], index * 3);
        coords.set([chunkX * CHUNK_SIZE + x - 1, chunkY * CHUNK_SIZE + z - 1], index * 2);
        uvs.set([data.positions[index * 3] - x,
            data.positions[index * 3 + 2] - z], index * 2);
    });
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geometry.setAttribute('terrainTileCoord', new THREE.Float32BufferAttribute(coords, 2));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(data.normals, 3));
    // Every terrainTop triangle is also drawn by refreshWorldTerrainSurface,
    // slightly above this mesh. Submit exposed sides once instead of shading
    // the same opaque land top twice. All CPU attributes remain unchanged.
    const sideIndices = [];
    for (let i = 0; i < data.topByVertex.length; i += 3) {
        if (data.topByVertex[i] < 0.5) sideIndices.push(i, i + 1, i + 2);
    }
    const maxIndex = sideIndices[sideIndices.length - 1] || 0;
    if (maxIndex < 65535 || renderer.capabilities.isWebGL2 || renderer.extensions.has('OES_element_index_uint')) {
        geometry.setIndex(sideIndices);
    }
    // Retain references to the existing CPU attributes, not a second geometry.
    entry.smoothTerrainData = { ...data,
        positions: geometry.getAttribute('position').array,
        normals: geometry.getAttribute('normal').array,
        topByVertex: geometry.getAttribute('terrainTop').array };
    const mesh = new THREE.Mesh(geometry, smoothGroundMat);
    mesh.raycast = raycastSmoothTerrain;
    mesh.position.set(chunkX * CHUNK_SIZE - 1, 0, chunkY * CHUNK_SIZE - 1);
    mesh.frustumCulled = false;
    mesh.receiveShadow = true;
    mesh.visible = entry.terrainVisible;
    scene.add(mesh);
    entry.smoothTerrain = mesh;
    entry.smoothTerrainBounds = { box: entry.bounds, mesh, key: `${chunkX},${chunkY}` };
    chunkBounds.push(entry.smoothTerrainBounds);
    if (metrics) metrics.terrainStages.geometryMs += performance.now() - stageStartedAt;
}

function worldTerrainOuterRing(padded) {
    const size = padded.length;
    const ring = new Map();
    for (let z = 0; z < size; z++) {
        for (let x = 0; x < size; x++) {
            if (x !== 0 && z !== 0 && x !== size - 1 && z !== size - 1) continue;
            const tile = padded[z][x];
            const height = Number(tile?.height);
            ring.set(z * size + x, tile ? {
                terrain: tile.terrain, height: Number.isFinite(height) ? height : 1,
            } : null);
        }
    }
    return ring;
}

function worldTerrainBoundaryNeedsGeometry(previous, current, padded) {
    const size = padded.length;
    const changed = [];
    for (const [index, next] of current) {
        const old = previous.get(index);
        if (old?.terrain !== next?.terrain || old?.height !== next?.height) changed.push(index);
    }
    if (!changed.length) return false;
    const oldPadded = padded.map(row => row.slice());
    for (const [index, tile] of previous) oldPadded[Math.floor(index / size)][index % size] = tile;
    for (const index of changed) {
        const x = index % size;
        const z = Math.floor(index / size);
        for (let nz = Math.max(1, z - 1); nz <= Math.min(size - 2, z + 1); nz++) {
            for (let nx = Math.max(1, x - 1); nx <= Math.min(size - 2, x + 1); nx++) {
                if (isSmoothTerrainTile(oldPadded, nx, nz) || isSmoothTerrainTile(padded, nx, nz)) return true;
            }
        }
    }
    return false;
}

function refreshWorldWaterChunk(entry, padded) {
    if (entry.waterSurface) {
        scene.remove(entry.waterSurface);
        entry.waterSurface.geometry.dispose();
        entry.waterSurface = null;
    }
    if (entry.waterBank) {
        scene.remove(entry.waterBank);
        entry.waterBank.geometry.dispose();
        entry.waterBank = null;
    }
    entry.waterShoreDirty = false;
    if (!entry.water.count) return;
    const { chunkX, chunkY, tilesData } = entry;
    // A halo water tile also needs its own immediate land neighbours when
    // generating normals. Two cells still use only the same 3x3 chunk set.
    const originX = chunkX * CHUNK_SIZE - 2;
    const originZ = chunkY * CHUNK_SIZE - 2;
    const waterTiles=Array.from({length:CHUNK_SIZE+4},(_,z)=>
        Array.from({length:CHUNK_SIZE+4},(_,x)=>{
            const wx=originX+x,wz=originZ+z,cx=Math.floor(wx/CHUNK_SIZE),cz=Math.floor(wz/CHUNK_SIZE);
            return chunksMap.get(`${cx},${cz}`)?.tilesData[wz-cz*CHUNK_SIZE]?.[wx-cx*CHUNK_SIZE]||null;
        }));
    const data = buildWorldWaterData(waterTiles,
        (x, z) => x >= 2 && x < CHUNK_SIZE+2 && z >= 2 && z < CHUNK_SIZE+2,
        originX, originZ);
    const dummy = new THREE.Object3D();
    for (let z = 0; z < CHUNK_SIZE; z++) {
        for (let x = 0; x < CHUNK_SIZE; x++) {
            const index = entry.waterIndices[z * CHUNK_SIZE + x];
            if (index < 0) continue;
            const level = Number(tilesData[z][x].height) || 1;
            dummy.position.set(chunkX * CHUNK_SIZE + x + 0.5, (level - 0.01) / 2,
                chunkY * CHUNK_SIZE + z + 0.5);
            dummy.scale.set(1, Math.max(0.05, level - 0.01), 1);
            dummy.updateMatrix();
            entry.water.setMatrixAt(index, dummy.matrix);
        }
    }
    entry.water.instanceMatrix.needsUpdate = true;
    if (data.water.length) {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute([...data.water,...data.sides], 3));
        geometry.setAttribute('normal', new THREE.Float32BufferAttribute(data.waterNormals, 3));
        indexWaterGeometry(geometry);
        const mesh = new THREE.Mesh(geometry, waterMat);
        mesh.position.set(originX, 0, originZ);
        mesh.frustumCulled = false;
        mesh.visible = entry.terrainVisible;
        mesh.raycast = () => {};
        mesh.castShadow = false;
        mesh.receiveShadow = false;
        scene.add(mesh);
        entry.waterSurface = mesh;
    }
    const bankPositions = data.bankPositions;
    if (bankPositions.length) {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(bankPositions, 3));
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(
            Array(bankPositions.length).fill(1), 3));
        geometry.setAttribute('terrainTileCoord', new THREE.Float32BufferAttribute(data.bankOwners, 2));
        geometry.setAttribute('terrainTop', new THREE.Float32BufferAttribute(Array(bankPositions.length/3).fill(0), 1));
        const uvs = [];
        for (let index = 0; index < bankPositions.length; index += 3) {
            uvs.push((bankPositions[index] + originX) * 0.25,
                (bankPositions[index + 2] + originZ) * 0.25);
        }
        geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
        geometry.setAttribute('normal',new THREE.Float32BufferAttribute(data.bankNormals,3));
        indexWaterGeometry(geometry);
        const mesh = new THREE.Mesh(geometry, worldWaterBankMat);
        mesh.position.set(originX, 0, originZ);
        mesh.frustumCulled = false;
        mesh.visible = entry.terrainVisible;
        mesh.raycast = () => {};
        mesh.receiveShadow = true;
        scene.add(mesh);
        entry.waterBank = mesh;
    }
}

const pendingWorldTerrainChunks = new Set();
const pendingWorldTerrainFullChunks = new Set();
const pendingWorldRoadChunks = new Set();
let worldTerrainRefreshTimer = null;
const worldTerrainRefreshWaiters = [];
let worldTerrainLoadDepth = 0;

// Сетевые пакеты не должны повторно строить одни и те же стыки. При этом
// сами чанки остаются доступны для приходящих правок других участников.
function beginWorldTerrainLoad() {
    worldTerrainLoadDepth++;
    let finished = false;
    return ({ defer = false } = {}) => {
        if (finished) return;
        finished = true;
        worldTerrainLoadDepth--;
        if (!worldTerrainLoadDepth && defer) {
            clearTimeout(worldTerrainRefreshTimer);
            worldTerrainRefreshTimer = null;
            return;
        }
        if (!worldTerrainLoadDepth && !worldTerrainRefreshTimer
            && (pendingWorldTerrainChunks.size || pendingWorldRoadChunks.size)) {
            worldTerrainRefreshTimer = setTimeout(flushWorldTerrainRefresh, 0);
        }
    };
}

function queueWorldBridgeSpanRefresh(worldX,worldZ) {
    const queue=[[worldX,worldZ],[worldX-1,worldZ],[worldX+1,worldZ],[worldX,worldZ-1],[worldX,worldZ+1]],seen=new Set();
    const nodeAt=(x,z)=>roadNode(loadedWorldTile(x,z));
    let found=false;
    for(let head=0;head<queue.length;head++){
        const [x,z]=queue[head],key=`${x},${z}`;
        if(seen.has(key))continue;
        seen.add(key);
        if(!nodeAt(x,z)?.road?.bridge)continue;
        found=true;pendingWorldRoadChunks.add(`${Math.floor(x/CHUNK_SIZE)},${Math.floor(z/CHUNK_SIZE)}`);
        for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]])if(roadConnectsAt(nodeAt,x,z,dx,dz))queue.push([x+dx,z+dz]);
    }
    if(found&&!worldTerrainRefreshTimer)worldTerrainRefreshTimer=setTimeout(flushWorldTerrainRefresh,100);
}

function refreshWorldTerrainBrushPreview() {
    if (!brushActive) return;
    for (const key of pendingWorldTerrainChunks) {
        const entry = chunksMap.get(key);
        if (!entry?.terrainPreviewRegion || !entry.smoothTerrainData) continue;
        refreshWorldTerrainChunk(entry, false, true);
        if (pendingWorldRoadChunks.has(key)) {
            if (entry.hasRoadNodes) rebuildRoadMeshes(entry);
            pendingWorldRoadChunks.delete(key);
        }
    }
}

function waitForWorldTerrainRefresh() {
    if (!worldTerrainRefreshTimer && !pendingWorldTerrainChunks.size && !pendingWorldRoadChunks.size) {
        return Promise.resolve();
    }
    return new Promise(resolve => worldTerrainRefreshWaiters.push(resolve));
}

function flushWorldTerrainRefresh() {
    worldTerrainRefreshTimer = null;
    if (worldTerrainLoadDepth) return;
    if (brushActive) {
        worldTerrainRefreshTimer = setTimeout(flushWorldTerrainRefresh, 100);
        return;
    }
    const startedAt = typeof performance !== 'undefined' ? performance.now() : 0;
    const refreshed = pendingWorldTerrainChunks.size;
    for (const key of pendingWorldTerrainChunks) {
        const entry = chunksMap.get(key);
        if (entry) refreshWorldTerrainChunk(entry, !pendingWorldTerrainFullChunks.has(key));
    }
    const metrics = typeof window !== 'undefined' ? window.worldLoadMetrics : null;
    const roadStartedAt = metrics && metrics.fullLoadMs === null ? performance.now() : 0;
    for (const key of pendingWorldRoadChunks) {
        const entry = chunksMap.get(key);
        if (entry?.hasRoadNodes) rebuildRoadMeshes(entry);
    }
    if (roadStartedAt) metrics.terrainStages.dependentMeshesMs += performance.now() - roadStartedAt;
    pendingWorldTerrainChunks.clear();
    pendingWorldTerrainFullChunks.clear();
    pendingWorldRoadChunks.clear();
    lastHoverRaycastAt = -Infinity;
    if (metrics && metrics.fullLoadMs === null) {
        metrics.terrainRefreshMs += performance.now() - startedAt;
        metrics.terrainRefreshes += refreshed;
    }
    worldTerrainRefreshWaiters.splice(0).forEach(resolve => resolve());
}

function refreshWorldTerrainNeighborhood(cx, cy, tileX = null, tileZ = null,
    deferred = false, refreshRoads = false, refreshWater = false, refreshTexture = false,
    loadRefresh = false) {
    const offsetsX = tileX === null ? [-1, 0, 1]
        : tileX === 0 ? [-1, 0] : tileX === CHUNK_SIZE - 1 ? [0, 1] : [0];
    const offsetsZ = tileZ === null ? [-1, 0, 1]
        : tileZ === 0 ? [-1, 0] : tileZ === CHUNK_SIZE - 1 ? [0, 1] : [0];
    for (const dz of offsetsZ) {
        for (const dx of offsetsX) {
            const entry = chunksMap.get(`${cx + dx},${cy + dz}`);
            if (!entry) continue;
            if (refreshWater) entry.waterShoreDirty = true;
            if (refreshTexture) {
                entry.terrainPixelRevision = (entry.terrainPixelRevision || 0) + 1;
                entry.preparedTerrainPixels = null;
            }
            // Height changes need live geometry, but do not change texture pixels.
            const regionFields = refreshTexture ? ['terrainTextureRegion'] : [];
            if (deferred && !loadRefresh) regionFields.push('terrainPreviewRegion');
            for (const field of regionFields) {
                const minX = cx * CHUNK_SIZE + (tileX ?? 0);
                const maxX = cx * CHUNK_SIZE + (tileX ?? CHUNK_SIZE - 1);
                const minZ = cy * CHUNK_SIZE + (tileZ ?? 0);
                const maxZ = cy * CHUNK_SIZE + (tileZ ?? CHUNK_SIZE - 1);
                const region = entry[field];
                entry[field] = region ? {
                    minX: Math.min(region.minX, minX), maxX: Math.max(region.maxX, maxX),
                    minZ: Math.min(region.minZ, minZ), maxZ: Math.max(region.maxZ, maxZ),
                } : { minX, maxX, minZ, maxZ };
            }
            if (deferred) {
                const key = `${entry.chunkX},${entry.chunkY}`;
                pendingWorldTerrainChunks.add(key);
                if (!loadRefresh || (dx === 0 && dz === 0)) pendingWorldTerrainFullChunks.add(key);
                if (refreshRoads && entry.hasRoadNodes) pendingWorldRoadChunks.add(key);
            } else {
                pendingWorldTerrainChunks.delete(`${entry.chunkX},${entry.chunkY}`);
                pendingWorldTerrainFullChunks.delete(`${entry.chunkX},${entry.chunkY}`);
                pendingWorldRoadChunks.delete(`${entry.chunkX},${entry.chunkY}`);
                refreshWorldTerrainChunk(entry);
            }
        }
    }
    if (deferred) {
        if (loadRefresh) {
            if (!worldTerrainRefreshTimer) {
                worldTerrainRefreshTimer = setTimeout(flushWorldTerrainRefresh, 16);
            }
        } else {
            clearTimeout(worldTerrainRefreshTimer);
            worldTerrainRefreshTimer = setTimeout(flushWorldTerrainRefresh, 100);
        }
    }
}

// --- Функции для чанков ---
function createLandmarkMeshes(tilesData, types = WORLD_LANDMARK_TYPES) {
    const counts = Object.fromEntries(types.map(type => [type, 0]));
    for (const row of tilesData) {
        for (const tile of row) {
            for (const object of tile.objects || []) {
                if (counts[object.type] !== undefined) counts[object.type]++;
            }
        }
    }
    return Object.fromEntries(types.map(type => {
        const count = counts[type];
        if (!count) return [type, null];
        const mesh = new THREE.InstancedMesh(WORLD_LANDMARKS[type].geometry, landmarkMat, count);
        mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3);
        mesh.castShadow = type !== 'road';
        mesh.receiveShadow = false;
        return [type, mesh];
    }));
}

function fillLandmarkMeshes(meshes, tilesData, cx, cy, types = WORLD_LANDMARK_TYPES) {
    const size = tilesData.length;
    const indices = Object.fromEntries(types.map(type => [
        type, Array.from({ length: size * size }, () => []),
    ]));
    const nextIndex = Object.fromEntries(types.map(type => [type, 0]));
    const dummy = new THREE.Object3D();
    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const tile = tilesData[y][x];
            for (const object of tile.objects || []) {
                const mesh = meshes[object.type];
                if (!mesh) continue;
                const scale = object.scale || 1;
                dummy.position.set(
                    cx * size + x + 0.5 + (object.x || 0),
                    (tile.height || 1) + getBaseGroundOffset(object.type) * scale,
                    cy * size + y + 0.5 + (object.z || 0),
                );
                dummy.rotation.set(0, THREE.MathUtils.degToRad(object.rotation || 0), 0);
                dummy.scale.setScalar(scale);
                dummy.updateMatrix();
                const index = nextIndex[object.type]++;
                mesh.setMatrixAt(index, dummy.matrix);
                mesh.setColorAt(index, new THREE.Color(object.color || getDefaultColorForType(object.type)));
                indices[object.type][y * size + x].push(index);
            }
        }
    }
    for (const mesh of Object.values(meshes)) {
        if (!mesh) continue;
        mesh.instanceMatrix.needsUpdate = true;
        mesh.instanceColor.needsUpdate = true;
        scene.add(mesh);
    }
    return indices;
}

function loadedWorldTile(x, z) {
    const cx = Math.floor(x / CHUNK_SIZE);
    const cy = Math.floor(z / CHUNK_SIZE);
    return chunksMap.get(`${cx},${cy}`)?.tilesData?.[z - cy * CHUNK_SIZE]?.[x - cx * CHUNK_SIZE] || null;
}

function disposeRoadMeshes(entry) {
    for (const mesh of entry.roads || []) {
        scene.remove(mesh);
        if(mesh.userData?.roadSurface)mesh.geometry.dispose();else mesh.dispose();
    }
    entry.roads = [];
}

function refreshRoadNeighborhood(cx, cy) {
    for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
            const entry = chunksMap.get(`${cx + dx},${cy + dy}`);
            if (entry?.hasRoadNodes) rebuildRoadMeshes(entry);
        }
    }
}

function rebuildRoadMeshes(entry) {
    disposeRoadMeshes(entry);
    if (!entry.hasRoadNodes) return;
    const pads = [];
    const links = [];
    const bridges=[];
    const directions = [[1, 0], [0, 1], [1, 1], [-1, 1]];
    const point = (x, z, tile, node) => new THREE.Vector3(
        x + 0.5 + Number(node.road?.x || 0),
        roadSurfaceHeight(tile,node.road),
        z + 0.5 + Number(node.road?.z || 0),
    );
    const roadNodeCache = new Map();
    const nodeAt = (x, z) => {
        const key = `${x},${z}`;
        if (!roadNodeCache.has(key)) roadNodeCache.set(key, roadNode(loadedWorldTile(x, z)));
        return roadNodeCache.get(key);
    };
    for (let localZ = 0; localZ < CHUNK_SIZE; localZ++) {
        for (let localX = 0; localX < CHUNK_SIZE; localX++) {
            const x = entry.chunkX * CHUNK_SIZE + localX;
            const z = entry.chunkY * CHUNK_SIZE + localZ;
            const tile = entry.tilesData[localZ]?.[localX];
            const node = nodeAt(x, z);
            if (!node) continue;
            const center = point(x, z, tile, node);
            if(tile.terrain==='water'&&node.road?.bridge){
                bridges.push({x,z});
            }
            let connections = 0;
            for (const [dx, dz] of directions) {
                const neighborTile = loadedWorldTile(x + dx, z + dz);
                const neighbor = nodeAt(x + dx, z + dz);
                if (!roadConnectsAt(nodeAt, x, z, dx, dz)) continue;
                connections++;
                links.push({ from: center, to: point(x + dx, z + dz, neighborTile, neighbor),
                    bridge:Boolean((tile.terrain==='water'&&node.road?.bridge)||(loadedWorldTile(x+dx,z+dz)?.terrain==='water'&&neighbor.road?.bridge)),
                    color: node.road?.color || neighbor.road?.color || '#756f61',
                    widthScale: (Number(node.road?.scale || 1) + Number(neighbor.road?.scale || 1)) / 2 });
            }
            for (const [dx, dz] of directions) {
                if (roadConnectsAt(nodeAt, x - dx, z - dz, dx, dz)) connections++;
            }
            if (node.road || connections) pads.push({ position: center, color: node.road?.color || '#756f61',
                scale: Number(node.road?.scale || 1),bridge:Boolean(tile.terrain==='water'&&node.road?.bridge) });
            if (node.road && !connections) {
                const angle = THREE.MathUtils.degToRad(Number(node.road.rotation || 0));
                const direction = new THREE.Vector3(Math.cos(angle), 0, -Math.sin(angle));
                links.push({ from: center.clone().addScaledVector(direction, -0.4),
                    to: center.clone().addScaledVector(direction, 0.4),
                    color: node.road.color || '#756f61', widthScale: Number(node.road.scale || 1) });
            }
        }
    }
    if(bridges.length)ensureRoadBridge();
    for(const bridgeMesh of createWorldRoadBridgeMeshes({nodes:bridges,getTile:loadedWorldTile,
        connected:(x,z,dx,dz)=>roadConnectsAt(nodeAt,x,z,dx,dz),sampleHeight:sampleWorldGroundHeight,
        owns:(x,z)=>Math.floor(x/CHUNK_SIZE)===entry.chunkX&&Math.floor(z/CHUNK_SIZE)===entry.chunkY})){
        bridgeMesh.visible=entry.terrainVisible;scene.add(bridgeMesh);entry.roads.push(bridgeMesh);
    }
    const surface=roadSurfaceMesh(pads,links,roadMaterial);
    if(surface){surface.visible=entry.terrainVisible;entry.roads.push(surface);}
}

export function addChunk(cx, cy, tilesData) {
    fenceGroundSamplers.clear();
    if (cx < MIN_CHUNK || cx > MAX_CHUNK_X || cy < MIN_CHUNK || cy > MAX_CHUNK_Y) return;

    const key = `${cx},${cy}`;
    if (chunksMap.has(key)) return;
    const size = tilesData.length;
    const totalTiles = size * size;

    const groundInstances = new THREE.InstancedMesh(groundSideGeo, groundMat, totalTiles);
    groundInstances.raycast = raycastGroundInstances;
    groundInstances.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(totalTiles * 3), 3);

    const waterInstances = new THREE.InstancedMesh(groundGeo, waterInstanceMaterials, totalTiles);

    groundInstances.castShadow = false;
    groundInstances.receiveShadow = true;
    groundInstances.frustumCulled = false;
    waterInstances.castShadow = false;
    waterInstances.receiveShadow = false;
    waterInstances.frustumCulled = false;

    let treeCount = 0, houseCount = 0, fenceCount = 0;
    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const tile = tilesData[y][x];
            if (tile.objects) {
                tile.objects.forEach(obj => {
                    if (obj.type === 'tree') treeCount++;
                    else if (obj.type === 'house') houseCount++;
                    else if (obj.type === 'fence') fenceCount++;
                });
            }
        }
    }

    const treeInstances = treeCount > 0 ? new THREE.InstancedMesh(treeGeo, treeMat, treeCount) : null;
    if (treeInstances) {
        treeInstances.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(treeCount * 3), 3);
        treeInstances.castShadow = true;
        treeInstances.receiveShadow = false;
    }

    const houseInstances = houseCount > 0 ? new THREE.InstancedMesh(houseGeo, houseMat, houseCount) : null;
    if (houseInstances) {
        houseInstances.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(houseCount * 3), 3);
        houseInstances.castShadow = true;
        houseInstances.receiveShadow = false;
    }

    const fenceInstances = fenceCount > 0 ? new THREE.InstancedMesh(fenceGeo, fenceMat, fenceCount) : null;
    if (fenceInstances) {
        fenceInstances.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(fenceCount * 3), 3);
        fenceInstances.castShadow = true;
        fenceInstances.receiveShadow = false;
    }

    const dummy = new THREE.Object3D();
    let minX = Infinity, maxX = -Infinity, minY = 0, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity;

    let treeIdx = 0, houseIdx = 0, fenceIdx = 0;
    const anomalyLODs = [];

    const objectIndices = {
        trees: new Array(size * size).fill().map(() => []),
        houses: new Array(size * size).fill().map(() => []),
        fences: new Array(size * size).fill().map(() => [])
    };

    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const tile = tilesData[y][x];
            const worldX = cx * size + x + 0.5;
            const worldZ = cy * size + y + 0.5;
            const height = tile.height || 1.0;
            const tileIndex = y * size + x;

            minX = Math.min(minX, worldX - 0.5);
            maxX = Math.max(maxX, worldX + 0.5);
            maxY = Math.max(maxY, height + 0.5);
            minZ = Math.min(minZ, worldZ - 0.5);
            maxZ = Math.max(maxZ, worldZ + 0.5);

            const fieldLOD = createAnomalyFieldLOD(tile, worldX, worldZ);
            if (fieldLOD) {
                scene.add(fieldLOD);
                anomalyLODs.push(fieldLOD);
            }

            if (tile.objects) {
                tile.objects.forEach(obj => {
                    if (obj.type !== 'anomaly') {
                        const baseHalf = getBaseGroundOffset(obj.type);
                        const yPos = height + baseHalf * (obj.scale || 1.0);
                        dummy.rotation.set(0, THREE.MathUtils.degToRad(obj.rotation || 0), 0);
                        dummy.position.set(
                            worldX + (obj.x || 0),
                            yPos,
                            worldZ + (obj.z || 0)
                        );
                        dummy.scale.set(obj.scale || 1, obj.scale || 1, obj.scale || 1);
                        dummy.updateMatrix();

                        const objColor = new THREE.Color(obj.color || getDefaultColorForType(obj.type));

                        if (obj.type === 'tree' && treeInstances) {
                            treeInstances.setMatrixAt(treeIdx, dummy.matrix);
                            treeInstances.setColorAt(treeIdx, objColor);
                            objectIndices.trees[tileIndex].push(treeIdx);
                            treeIdx++;
                        } else if (obj.type === 'house' && houseInstances) {
                            houseInstances.setMatrixAt(houseIdx, dummy.matrix);
                            houseInstances.setColorAt(houseIdx, objColor);
                            objectIndices.houses[tileIndex].push(houseIdx);
                            houseIdx++;
                        } else if (obj.type === 'fence' && fenceInstances) {
                            setFenceMatrix(dummy.matrix,obj,worldX+(obj.x||0),worldZ+(obj.z||0));
                            fenceInstances.setMatrixAt(fenceIdx, dummy.matrix);
                            fenceInstances.setColorAt(fenceIdx, objColor);
                            objectIndices.fences[tileIndex].push(fenceIdx);
                            fenceIdx++;
                        }
                    }
                });
            }
        }
    }

    const terrainIndices = fillChunkTerrainInstances(groundInstances, waterInstances, tilesData, cx, cy);

    if (treeInstances) {
        treeInstances.instanceMatrix.needsUpdate = true;
        treeInstances.instanceColor.needsUpdate = true;
    }
    if (houseInstances) {
        houseInstances.instanceMatrix.needsUpdate = true;
        houseInstances.instanceColor.needsUpdate = true;
    }
    if (fenceInstances) {
        fenceInstances.instanceMatrix.needsUpdate = true;
        fenceInstances.instanceColor.needsUpdate = true;
    }

    scene.add(groundInstances);
    scene.add(waterInstances);
    if (treeInstances) scene.add(treeInstances);
    if (houseInstances) scene.add(houseInstances);
    if (fenceInstances) scene.add(fenceInstances);
    const landmarkMeshes = createLandmarkMeshes(tilesData);
    const landmarkIndices = fillLandmarkMeshes(landmarkMeshes, tilesData, cx, cy);

    const farAnomalies = createFarAnomalyInstances(anomalyLODs);

    const box = new THREE.Box3(new THREE.Vector3(minX, minY, minZ), new THREE.Vector3(maxX, maxY, maxZ));
    chunkBounds.push({ box, mesh: groundInstances, key });
    if (waterInstances) chunkBounds.push({ box, mesh: waterInstances, key });

    chunksMap.set(key, {
        materialLayer: worldTerrainMaterials,
        ground: groundInstances,
        water: waterInstances,
        trees: treeInstances,
        houses: houseInstances,
        fences: fenceInstances,
        landmarks: landmarkMeshes,
        landmarkIndices,
        anomalyLODs: anomalyLODs,
        farAnomalies,
        farAnomalyActive: [],
        tilesData: tilesData,
        objectStateSnapshots: createWorldObjectStateSnapshots(tilesData),
        hasRoadNodes: tilesData.some(row => row.some(tile => Boolean(roadNode(tile)))),
        chunkX: cx,
        chunkY: cy,
        terrainVisible: true,
        waterShoreDirty: true,
        ...terrainIndices,
        bounds: box,
        objectIndices: objectIndices,
        pendingRebuild: null,
        roadBoundaryDirty: false
    });
    worldTerrainMaterials.fill(tilesData, cx * CHUNK_SIZE, cy * CHUNK_SIZE);
    fenceGroundSamplers.clear();
    refreshFenceChunkNeighbors(cx,cy);
    refreshWorldTerrainNeighborhood(cx, cy, null, null, true, true, true, true, true);
    prepareWorldTerrainSides();
    worldWaterProfiles.updateRegion(cx*CHUNK_SIZE,cy*CHUNK_SIZE,CHUNK_SIZE,CHUNK_SIZE);
    lastChunkVisibilityAt = -Infinity;
    lastHoverRaycastAt = -Infinity;
    markWorldShadowsDirty();
}

export function removeChunk(cx, cy) {
    fenceGroundSamplers.clear();
    const key = `${cx},${cy}`;
    const entry = chunksMap.get(key);
    if (!entry) return;
    clearWorldIceSurface(entry);
    entry.materialLayer?.clear(cx * CHUNK_SIZE, cy * CHUNK_SIZE, CHUNK_SIZE, CHUNK_SIZE);
    if (entry.pendingRebuild) {
        cancelAnimationFrame(entry.pendingRebuild);
        entry.pendingRebuild = null;
    }

    for (let i = chunkBounds.length - 1; i >= 0; i--) {
        if (chunkBounds[i].key === key) chunkBounds.splice(i, 1);
    }

    scene.remove(entry.ground);
    scene.remove(entry.water);
    if (entry.waterSurface) {
        scene.remove(entry.waterSurface);
        entry.waterSurface.geometry.dispose();
    }
    if (entry.waterBank) {
        scene.remove(entry.waterBank);
        entry.waterBank.geometry.dispose();
    }
    if (entry.smoothTerrain) {
        scene.remove(entry.smoothTerrain);
        entry.smoothTerrain.geometry.dispose();
    }
    if (entry.terrainSurface) {
        scene.remove(entry.terrainSurface);
        entry.terrainSurface.geometry.dispose();
        entry.terrainSurface.material.dispose();
    }
    entry.terrainTexture?.dispose();
    if (entry.trees) scene.remove(entry.trees);
    if (entry.houses) scene.remove(entry.houses);
    if (entry.fences) scene.remove(entry.fences);
    entry.ground.dispose();
    entry.water.dispose();
    entry.trees?.dispose();
    entry.houses?.dispose();
    entry.fences?.dispose();
    for (const mesh of Object.values(entry.landmarks || {})) {
        if (mesh) {
            scene.remove(mesh);
            mesh.dispose();
        }
    }
    disposeRoadMeshes(entry);
    disposeChunkAnomalyLODs(entry.anomalyLODs);
    if (entry.farAnomalies) {
        scene.remove(entry.farAnomalies);
        entry.farAnomalies.dispose();
    }

    chunksMap.delete(key);
    worldWaterProfiles.updateRegion(cx*CHUNK_SIZE,cy*CHUNK_SIZE,CHUNK_SIZE,CHUNK_SIZE);
    refreshFenceChunkNeighbors(cx,cy);
    entry.objectStateSnapshots = null;
    entry.pendingObjectGroups = null;
    refreshWorldTerrainNeighborhood(cx, cy, null, null, false, false, true, true);
    refreshRoadNeighborhood(cx, cy);
    lastChunkVisibilityAt = -Infinity;
    lastHoverRaycastAt = -Infinity;
    markWorldShadowsDirty();
}

// --- Функция для обновления позиций объектов на тайле при изменении высоты ---
function updateTileObjectsPositions(entry, tileX, tileY, newHeight) {
    const size = CHUNK_SIZE;
    const tileIndex = tileY * size + tileX;
    const worldX = entry.chunkX * size + tileX + 0.5;
    const worldZ = entry.chunkY * size + tileY + 0.5;

    const dummy = new THREE.Object3D();

    const tile = entry.tilesData[tileY][tileX];
    if (!tile.objects) return;

    let treeIdxPos = 0, houseIdxPos = 0, fenceIdxPos = 0;
    const landmarkPositions = Object.fromEntries(WORLD_LANDMARK_TYPES.map(type => [type, 0]));

    tile.objects.forEach(obj => {
        const baseHalf = getBaseGroundOffset(obj.type, obj.anomalyType);
        const yPos = newHeight + baseHalf * (obj.scale || 1.0);

        dummy.rotation.set(0, THREE.MathUtils.degToRad(obj.rotation || 0), 0);
        dummy.position.set(
            worldX + (obj.x || 0),
            yPos,
            worldZ + (obj.z || 0)
        );
        dummy.scale.set(obj.scale || 1, obj.scale || 1, obj.scale || 1);
        dummy.updateMatrix();

        if (obj.type === 'tree' && entry.trees) {
            const idx = entry.objectIndices.trees[tileIndex][treeIdxPos++];
            entry.trees.setMatrixAt(idx, dummy.matrix);
        } else if (obj.type === 'house' && entry.houses) {
            const idx = entry.objectIndices.houses[tileIndex][houseIdxPos++];
            entry.houses.setMatrixAt(idx, dummy.matrix);
        } else if (obj.type === 'fence' && entry.fences) {
            setFenceMatrix(dummy.matrix,obj,worldX+(obj.x||0),worldZ+(obj.z||0));
            const idx = entry.objectIndices.fences[tileIndex][fenceIdxPos++];
            entry.fences.setMatrixAt(idx, dummy.matrix);
        } else if (entry.landmarks?.[obj.type]) {
            const idx = entry.landmarkIndices[obj.type][tileIndex][landmarkPositions[obj.type]++];
            entry.landmarks[obj.type].setMatrixAt(idx, dummy.matrix);
        }
    });

    if (entry.trees) entry.trees.instanceMatrix.needsUpdate = true;
    if (entry.houses) entry.houses.instanceMatrix.needsUpdate = true;
    if (entry.fences) entry.fences.instanceMatrix.needsUpdate = true;
    for (const mesh of Object.values(entry.landmarks || {})) {
        if (mesh) mesh.instanceMatrix.needsUpdate = true;
    }
}

// --- Пересборка отмеченных групп; без списка выполняется полная объектная сборка ---
function rebuildChunkObjects(entry, groups = null) {
    const updatesGroup = type => !groups || groups.has(type);
    const updateTrees = updatesGroup('tree');
    const updateHouses = updatesGroup('house');
    const updateFences = updatesGroup('fence');
    const updateAnomalies = updatesGroup('anomalies');
    const landmarkTypes = WORLD_LANDMARK_TYPES.filter(updatesGroup);
    if (updateTrees && entry.trees) {
        scene.remove(entry.trees);
        entry.trees.dispose();
    }
    if (updateHouses && entry.houses) {
        scene.remove(entry.houses);
        entry.houses.dispose();
    }
    if (updateFences && entry.fences) {
        scene.remove(entry.fences);
        entry.fences.dispose();
    }
    for (const type of landmarkTypes) {
        const mesh = entry.landmarks?.[type];
        if (mesh) {
            scene.remove(mesh);
            mesh.dispose();
        }
    }
    if (updateAnomalies) {
        disposeChunkAnomalyLODs(entry.anomalyLODs);
        if (entry.farAnomalies) {
            scene.remove(entry.farAnomalies);
            entry.farAnomalies.dispose();
        }
    }

    const tilesData = entry.tilesData;
    const size = CHUNK_SIZE;
    let treeCount = 0, houseCount = 0, fenceCount = 0;

    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const tile = tilesData[y][x];
            if (tile.objects) {
                tile.objects.forEach(obj => {
                    if (obj.type === 'tree') treeCount++;
                    else if (obj.type === 'house') houseCount++;
                    else if (obj.type === 'fence') fenceCount++;
                });
            }
        }
    }

    const treeInstances = updateTrees
        ? (treeCount > 0 ? new THREE.InstancedMesh(treeGeo, treeMat, treeCount) : null) : entry.trees;
    if (updateTrees && treeInstances) {
        treeInstances.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(treeCount * 3), 3);
        treeInstances.castShadow = true;
        treeInstances.receiveShadow = false;
    }

    const houseInstances = updateHouses
        ? (houseCount > 0 ? new THREE.InstancedMesh(houseGeo, houseMat, houseCount) : null) : entry.houses;
    if (updateHouses && houseInstances) {
        houseInstances.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(houseCount * 3), 3);
        houseInstances.castShadow = true;
        houseInstances.receiveShadow = false;
    }

    const fenceInstances = updateFences
        ? (fenceCount > 0 ? new THREE.InstancedMesh(fenceGeo, fenceMat, fenceCount) : null) : entry.fences;
    if (updateFences && fenceInstances) {
        fenceInstances.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(fenceCount * 3), 3);
        fenceInstances.castShadow = true;
        fenceInstances.receiveShadow = false;
    }

    const dummy = new THREE.Object3D();
    let treeIdx = 0, houseIdx = 0, fenceIdx = 0;
    const anomalyLODs = updateAnomalies ? [] : entry.anomalyLODs;

    const objectIndices = { ...entry.objectIndices };
    if (updateTrees) objectIndices.trees = Array.from({ length: size * size }, () => []);
    if (updateHouses) objectIndices.houses = Array.from({ length: size * size }, () => []);
    if (updateFences) objectIndices.fences = Array.from({ length: size * size }, () => []);

    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const tile = tilesData[y][x];
            const worldX = entry.chunkX * size + x + 0.5;
            const worldZ = entry.chunkY * size + y + 0.5;
            const height = tile.height || 1.0;
            const tileIndex = y * size + x;

            const fieldLOD = updateAnomalies ? createAnomalyFieldLOD(tile, worldX, worldZ) : null;
            if (fieldLOD) {
                scene.add(fieldLOD);
                anomalyLODs.push(fieldLOD);
            }

            if (tile.objects) {
                tile.objects.forEach(obj => {
                    if (obj.type !== 'anomaly' && updatesGroup(obj.type)) {
                        const baseHalf = getBaseGroundOffset(obj.type);
                        const yPos = height + baseHalf * (obj.scale || 1.0);
                        dummy.rotation.set(0, THREE.MathUtils.degToRad(obj.rotation || 0), 0);
                        dummy.position.set(
                            worldX + (obj.x || 0),
                            yPos,
                            worldZ + (obj.z || 0)
                        );
                        dummy.scale.set(obj.scale || 1, obj.scale || 1, obj.scale || 1);
                        dummy.updateMatrix();

                        const objColor = new THREE.Color(obj.color || getDefaultColorForType(obj.type));

                        if (obj.type === 'tree' && treeInstances) {
                            treeInstances.setMatrixAt(treeIdx, dummy.matrix);
                            treeInstances.setColorAt(treeIdx, objColor);
                            objectIndices.trees[tileIndex].push(treeIdx);
                            treeIdx++;
                        } else if (obj.type === 'house' && houseInstances) {
                            houseInstances.setMatrixAt(houseIdx, dummy.matrix);
                            houseInstances.setColorAt(houseIdx, objColor);
                            objectIndices.houses[tileIndex].push(houseIdx);
                            houseIdx++;
                        } else if (obj.type === 'fence' && fenceInstances) {
                            setFenceMatrix(dummy.matrix,obj,worldX+(obj.x||0),worldZ+(obj.z||0));
                            fenceInstances.setMatrixAt(fenceIdx, dummy.matrix);
                            fenceInstances.setColorAt(fenceIdx, objColor);
                            objectIndices.fences[tileIndex].push(fenceIdx);
                            fenceIdx++;
                        }
                    }
                });
            }
        }
    }

    if (updateTrees && treeInstances) {
        treeInstances.instanceMatrix.needsUpdate = true;
        treeInstances.instanceColor.needsUpdate = true;
        scene.add(treeInstances);
    }
    if (updateHouses && houseInstances) {
        houseInstances.instanceMatrix.needsUpdate = true;
        houseInstances.instanceColor.needsUpdate = true;
        scene.add(houseInstances);
    }
    if (updateFences && fenceInstances) {
        fenceInstances.instanceMatrix.needsUpdate = true;
        fenceInstances.instanceColor.needsUpdate = true;
        scene.add(fenceInstances);
    }
    if (landmarkTypes.length) {
        const landmarkMeshes = createLandmarkMeshes(tilesData, landmarkTypes);
        const landmarkIndices = fillLandmarkMeshes(landmarkMeshes, tilesData, entry.chunkX, entry.chunkY, landmarkTypes);
        entry.landmarks = { ...entry.landmarks, ...landmarkMeshes };
        entry.landmarkIndices = { ...entry.landmarkIndices, ...landmarkIndices };
    }

    entry.trees = treeInstances;
    entry.houses = houseInstances;
    entry.fences = fenceInstances;
    if (updatesGroup('roads')) rebuildRoadMeshes(entry);
    if (updateAnomalies) {
        entry.anomalyLODs = anomalyLODs;
        entry.farAnomalies = createFarAnomalyInstances(anomalyLODs);
        entry.farAnomalyActive = [];
    }
    entry.objectIndices = objectIndices;
    lastChunkVisibilityAt = -Infinity;
    lastHoverRaycastAt = -Infinity;
    markWorldShadowsDirty();
}

// Only JSON object fields are copied; terrain and other tile data stay outside the snapshot.
function copyWorldObjectData(value) {
    if (Array.isArray(value)) return value.map(copyWorldObjectData);
    if (value && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copyWorldObjectData(item)]));
    }
    return value;
}

function createWorldObjectStateSnapshots(tilesData) {
    return tilesData.map(row => row.map(tile => ({
        objects: copyWorldObjectData(tile.objects),
        anomaly_field: copyWorldObjectData(tile.anomaly_field),
    })));
}

export function worldObjectDataEqual(left, right) {
    if (left === right) return true;
    if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
    if (Array.isArray(left) !== Array.isArray(right)) return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length
        && keys.every(key => Object.prototype.hasOwnProperty.call(right, key)
            && worldObjectDataEqual(left[key], right[key]));
}

export function updateTileInChunk(chunkX, chunkY, tileX, tileY, updates) {
    const key = `${chunkX},${chunkY}`;
    const entry = chunksMap.get(key);
    if (!entry) return;

    const tile = entry.tilesData[tileY][tileX];
    if(updates.__editorState){
        const state=updates.__editorState;
        const patch={};
        for(const field of Object.keys(state))if(!worldObjectDataEqual(tile[field],state[field]))patch[field]=state[field];
        for(const field of Object.keys(tile))if(!Object.hasOwn(state,field))patch[field]=field==='objects'?[]:field==='radiation'?0:null;
        updateTileInChunk(chunkX,chunkY,tileX,tileY,patch);
        for(const key of Object.keys(tile))if(!Object.hasOwn(state,key))delete tile[key];
        Object.assign(tile,state);
        entry.objectStateSnapshots[tileY][tileX]={objects:copyWorldObjectData(tile.objects),anomaly_field:copyWorldObjectData(tile.anomaly_field)};
        return;
    }
    globalThis.window?.touchWorldEditorCell?.(chunkX,chunkY,tileX,tileY);
    const objectState = updates.objects !== undefined || updates.anomaly_field !== undefined
        ? entry.objectStateSnapshots[tileY][tileX] : null;
    const changedObjects = updates.objects !== undefined && !worldObjectDataEqual(updates.objects, objectState.objects);
    const changedAnomalyField = updates.anomaly_field !== undefined
        && !worldObjectDataEqual(updates.anomaly_field, objectState.anomaly_field);
    const changedTerrain = updates.terrain !== undefined && updates.terrain !== tile.terrain;
    const changedHeight = updates.height !== undefined && Number(updates.height) !== Number(tile.height);
    const wasWater = tile.terrain === 'water';
    if (!changedTerrain && !changedHeight
        && Object.keys(updates).every(key => key === 'terrain' || key === 'height')) return;
    Object.assign(tile, updates);
    if(changedTerrain&&updates.water_profile===undefined)delete tile.water_profile;
    if(changedTerrain||updates.water_profile!==undefined)
        worldWaterProfiles.updateRegion(chunkX*CHUNK_SIZE+tileX,chunkY*CHUNK_SIZE+tileY);
    if(changedHeight||changedTerrain)invalidateFenceGround(chunkX*CHUNK_SIZE+tileX,chunkY*CHUNK_SIZE+tileY);
    if (changedTerrain) entry.materialLayer?.update(chunkX * CHUNK_SIZE + tileX,
        chunkY * CHUNK_SIZE + tileY, tile);
    if (changedObjects || changedAnomalyField || (changedHeight && tile.anomaly_field)) {
        const groups = entry.pendingObjectGroups ??= new Set();
        if (changedObjects) {
            for (const type of ['tree', 'house', 'fence', ...WORLD_LANDMARK_TYPES]) {
                const previous = (objectState.objects || []).filter(object => object.type === type);
                const next = (updates.objects || []).filter(object => object.type === type);
                if (!worldObjectDataEqual(previous, next)) groups.add(type);
            }
            // Keep the existing broad road invalidation until its topology is optimized separately.
            groups.add('roads');
        }
        if (changedAnomalyField || (changedHeight && tile.anomaly_field)) groups.add('anomalies');
    }
    // Advance accepted state immediately, independently of when the existing RAF runs.
    if (changedObjects) objectState.objects = copyWorldObjectData(updates.objects);
    if (changedAnomalyField) objectState.anomaly_field = copyWorldObjectData(updates.anomaly_field);
    if (changedObjects) {
        entry.hasRoadNodes = entry.tilesData.some(row => row.some(item => Boolean(roadNode(item))));
    }
    if (changedObjects || changedHeight) {
        globalThis.window?.syncDirectWorldObjectSelection?.(chunkX, chunkY, tileX, tileY);
    }
    const touchesChunkEdge = tileX === 0 || tileX === CHUNK_SIZE - 1
        || tileY === 0 || tileY === CHUNK_SIZE - 1;
    if (touchesChunkEdge && (changedObjects || changedHeight)) {
        entry.roadBoundaryDirty = true;
    }
    lastHoverRaycastAt = -Infinity;

    if (changedTerrain || changedHeight) {
        if (entry.bounds) {
            entry.bounds.max.y = Math.max(entry.bounds.max.y, (tile.height || 1) + 0.5);
        }
        const tileIndex = tileY * CHUNK_SIZE + tileX;
        const groundIndex = entry.groundIndices[tileIndex];
        const waterIndex = entry.waterIndices[tileIndex];
        const isWater = tile.terrain === 'water';
        if ((isWater && groundIndex >= 0) || (!isWater && waterIndex >= 0)) {
            Object.assign(entry, fillChunkTerrainInstances(
                entry.ground, entry.water, entry.tilesData, chunkX, chunkY, entry.terrainVisible,
            ));
        } else {
            const dummy = new THREE.Object3D();
            const height = tile.height || 1.0;
            // A smoothed shore's support can be shorter than its stored height.
            // Use that height for both its center and scale during queued rebuilds.
            const instanceHeight = isWater ? height : entry.smoothTileFoundation?.get(tileIndex) ?? height;
            dummy.position.set(chunkX * CHUNK_SIZE + tileX + 0.5, instanceHeight / 2, chunkY * CHUNK_SIZE + tileY + 0.5);
            if (isWater) {
                dummy.scale.set(1, height, 1);
                dummy.updateMatrix();
                entry.water.setMatrixAt(waterIndex, dummy.matrix);
                entry.water.instanceMatrix.needsUpdate = true;
            } else {
                dummy.scale.set(1, instanceHeight, 1);
                dummy.updateMatrix();
                entry.ground.setMatrixAt(groundIndex, dummy.matrix);
                entry.ground.setColorAt(groundIndex, new THREE.Color(terrainColors[tile.terrain] || 0x3a5f0b));
                entry.ground.instanceMatrix.needsUpdate = true;
                entry.ground.instanceColor.needsUpdate = true;
            }
        }
        const worldX = chunkX * CHUNK_SIZE + tileX;
        const worldZ = chunkY * CHUNK_SIZE + tileY;
        // Retain removed water; dry shore edits affect the padded grid only next to water.
        const waterNearby = wasWater || isWater || [[-1, -1], [0, -1], [1, -1],
            [-1, 0], [0, 0], [1, 0], [-1, 1], [0, 1], [1, 1]]
            .some(([dx, dz]) => {
                const nx = worldX + dx;
                const nz = worldZ + dz;
                const neighbor = chunksMap.get(`${Math.floor(nx / CHUNK_SIZE)},${Math.floor(nz / CHUNK_SIZE)}`);
                return neighbor?.tilesData[nz - neighbor.chunkY * CHUNK_SIZE]?.[nx - neighbor.chunkX * CHUNK_SIZE]
                    ?.terrain === 'water';
            });
        refreshWorldTerrainNeighborhood(chunkX, chunkY, tileX, tileY,
            true, changedHeight && !changedObjects, waterNearby, changedTerrain);
    }

    if (changedHeight) {
        updateTileObjectsPositions(entry, tileX, tileY, tile.height);
        refreshWorldFenceNeighborhood(chunkX*CHUNK_SIZE+tileX,chunkY*CHUNK_SIZE+tileY);
        if (changedTerrain && !changedObjects) {
            if (touchesChunkEdge) refreshRoadNeighborhood(chunkX, chunkY);
            else if (entry.hasRoadNodes) rebuildRoadMeshes(entry);
        }
        if (entry.roadBoundaryDirty && !changedObjects && !entry.pendingRebuild) {
            entry.roadBoundaryDirty = false;
        }
        lastChunkVisibilityAt = -Infinity;
        markWorldShadowsDirty();
    }
    if(changedTerrain&&!changedHeight)refreshWorldFenceNeighborhood(chunkX*CHUNK_SIZE+tileX,chunkY*CHUNK_SIZE+tileY);
    if(changedHeight||changedTerrain||changedObjects)queueWorldBridgeSpanRefresh(chunkX*CHUNK_SIZE+tileX,chunkY*CHUNK_SIZE+tileY);

    if (changedObjects || changedAnomalyField
        || (changedHeight && tile.anomaly_field)) {
        if (entry.pendingRebuild) {
            cancelAnimationFrame(entry.pendingRebuild);
        }
        entry.pendingRebuild = requestAnimationFrame(() => {
            const groups = entry.pendingObjectGroups;
            entry.pendingObjectGroups = null;
            rebuildChunkObjects(entry, groups);
            if (entry.roadBoundaryDirty) {
                refreshRoadNeighborhood(chunkX, chunkY);
                entry.roadBoundaryDirty = false;
            }
            entry.pendingRebuild = null;
        });
    }
}

export function setBrushRadius(radius) {
    currentBrushRadius = radius;
    lastHoverRaycastAt = -Infinity;
}

const TERRAIN_RENDER_DISTANCE = 380;
const NATURAL_DETAIL_DISTANCE = 190;
const STRUCTURE_DETAIL_DISTANCE = 280;
const ANOMALY_DETAIL_DISTANCE = 320;
const chunkFrustum = new THREE.Frustum();
const chunkProjectionMatrix = new THREE.Matrix4();
const anomalyVisibilitySphere = new THREE.Sphere();

function chunkWithinHorizontalDistance(bounds, focus, distance) {
    const nearestX = Math.max(bounds.min.x, Math.min(focus.x, bounds.max.x));
    const nearestZ = Math.max(bounds.min.z, Math.min(focus.z, bounds.max.z));
    const dx = focus.x - nearestX;
    const dz = focus.z - nearestZ;
    return dx * dx + dz * dz <= distance * distance;
}

function updateChunkVisibility() {
    camera.updateMatrixWorld();
    chunkFrustum.setFromProjectionMatrix(chunkProjectionMatrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
    visibleNearAnomalies.length = 0;

    chunksMap.forEach((chunk) => {
        if (!chunk.bounds) return;

        if (!chunkWithinHorizontalDistance(chunk.bounds, controls.target, TERRAIN_RENDER_DISTANCE)) {
            setVisible(chunk, false);
            return;
        }

        const visible = chunkFrustum.intersectsBox(chunk.bounds);
        setVisible(
            chunk,
            visible,
            visible && chunkWithinHorizontalDistance(chunk.bounds, controls.target, NATURAL_DETAIL_DISTANCE),
            visible && chunkWithinHorizontalDistance(chunk.bounds, controls.target, STRUCTURE_DETAIL_DISTANCE),
            visible && chunkWithinHorizontalDistance(chunk.bounds, controls.target, ANOMALY_DETAIL_DISTANCE),
        );
    });
}

function setVisible(chunk, visible, naturalVisible = false, structureVisible = false, anomalyVisible = false) {
    const groundVisible = visible && chunk.ground?.count !== 0;
    const waterVisible = visible && chunk.water?.count !== 0;
    const visibilityChanged = (chunk.ground && chunk.ground.visible !== groundVisible)
        || (chunk.terrainSurface && chunk.terrainSurface.visible !== groundVisible)
        || (chunk.smoothTerrain && chunk.smoothTerrain.visible !== groundVisible)
        || (chunk.waterSurface && chunk.waterSurface.visible !== waterVisible)
        || (chunk.waterBank && chunk.waterBank.visible !== waterVisible)
        || (chunk.trees && chunk.trees.visible !== naturalVisible)
        || (chunk.houses && chunk.houses.visible !== structureVisible)
        || (chunk.fences && chunk.fences.visible !== naturalVisible)
        || Object.entries(chunk.landmarks || {}).some(([type, mesh]) => (
            mesh && mesh.visible !== (type === 'forest' ? naturalVisible : structureVisible)
        ));
    chunk.terrainVisible = visible;
    if (chunk.ground) chunk.ground.visible = groundVisible;
    if (chunk.terrainSurface) chunk.terrainSurface.visible = groundVisible;
    if (chunk.iceSurface) chunk.iceSurface.visible = groundVisible;
    if (chunk.smoothTerrain) chunk.smoothTerrain.visible = groundVisible;
    if (chunk.water) chunk.water.visible = waterVisible;
    if (chunk.waterSurface) chunk.waterSurface.visible = waterVisible;
    if (chunk.waterBank) chunk.waterBank.visible = waterVisible;
    if (chunk.trees) chunk.trees.visible = naturalVisible;
    if (chunk.houses) chunk.houses.visible = structureVisible;
    if (chunk.fences) chunk.fences.visible = naturalVisible;
    for (const [type, mesh] of Object.entries(chunk.landmarks || {})) {
        if (mesh) mesh.visible = type === 'forest' ? naturalVisible : structureVisible;
    }
    for (const mesh of chunk.roads || []) mesh.visible = visible;
    updateFarAnomalyInstances(chunk, anomalyVisible);
    if (visibilityChanged) markWorldShadowsDirty();
}

export function setEditMode(enabled) {
    editMode = enabled;
    if (!enabled) removePreviewObject();
    if (!enabled && controlsDisabled) {
        controls.enabled = true;
        controlsDisabled = false;
    }
}
export function setTileClickCallback(callback) { window.tileClickCallback = callback; }
export function setWorldTravelTileClickCallback(callback) {
    window.worldTravelTileClickCallback = callback;
}

export function worldPointOnObjectPlane(clientX, clientY, height) {
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.set(((clientX - rect.left) / rect.width) * 2 - 1,
        -((clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(mouse, camera);
    return raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), -height), new THREE.Vector3());
}

export function worldPointOnTerrain(clientX, clientY) {
    worldPointOnObjectPlane(clientX, clientY, 0);
    const hits = [];
    for (const entry of chunksMap.values()) {
        if (!entry.terrainVisible) continue;
        for (const mesh of [entry.terrainSurface || entry.ground, entry.waterSurface || entry.water]) {
            if (!mesh?.visible) continue;
            // Visual surfaces disable routine hover raycasts. Pick them explicitly
            // for G without changing that optimization for other pointer handlers.
            if (mesh.isInstancedMesh) mesh.raycast(raycaster, hits);
            else THREE.Mesh.prototype.raycast.call(mesh, raycaster, hits);
        }
    }
    hits.sort((a, b) => a.distance - b.distance);
    return hits[0]?.point || null;
}

export function pickWorldObject(clientX, clientY) {
    worldPointOnObjectPlane(clientX, clientY, 0);
    const candidates = [];
    const metadata = new Map();
    const legacy = { tree: 'trees', house: 'houses', fence: 'fences' };
    for (const entry of chunksMap.values()) {
        if (!entry.terrainVisible) continue;
        for (const [type, key] of Object.entries(legacy)) {
            const mesh = entry[key];
            if (!mesh?.visible) continue;
            candidates.push(mesh);
            metadata.set(mesh, { entry, type, indices: entry.objectIndices[key] });
        }
        for (const [type, mesh] of Object.entries(entry.landmarks || {})) {
            if (!mesh?.visible) continue;
            candidates.push(mesh);
            metadata.set(mesh, { entry, type, indices: entry.landmarkIndices[type] });
        }
    }
    let nearest = null;
    let distance = Infinity;
    for (const hit of raycaster.intersectObjects(candidates, false)) {
        const info = metadata.get(hit.object);
        const reference = objectFromWorldInstance(info.entry, info.type, hit.instanceId, info.indices);
        if (reference) { nearest = reference; distance = hit.distance; break; }
    }
    // Roads use shared pad/link meshes instead of an instance per saved object.
    // Pick their saved node, rather than treating each generated link as an object.
    const box = new THREE.Box3();
    const point = new THREE.Vector3();
    for (const entry of chunksMap.values()) {
        if (!entry.terrainVisible || !entry.hasRoadNodes) continue;
        for(const hit of raycaster.intersectObjects((entry.roads||[]).filter(m=>m.visible&&m.userData.worldBridgeAsset),false)){
            const owner=hit.object.userData.bridgeOwners[hit.instanceId];
            if(!owner||hit.distance>=distance)continue;
            const tileX=owner.x-entry.chunkX*CHUNK_SIZE,tileY=owner.z-entry.chunkY*CHUNK_SIZE;
            const index=entry.tilesData[tileY]?.[tileX]?.objects?.findIndex(o=>o.type==='road');
            if(index>=0){distance=hit.distance;nearest={chunkX:entry.chunkX,chunkY:entry.chunkY,tileX,tileY,index};}
        }
        for (let y = 0; y < entry.tilesData.length; y++) {
            for (let x = 0; x < entry.tilesData[y].length; x++) {
                const tile = entry.tilesData[y][x];
                (tile.objects || []).forEach((object, index) => {
                    if (object.type !== 'road'||(object.bridge&&tile.terrain==='water')) return;
                    const scale = object.scale || 1;
                    const worldX = entry.chunkX * CHUNK_SIZE + x + 0.5 + (object.x || 0);
                    const worldZ = entry.chunkY * CHUNK_SIZE + y + 0.5 + (object.z || 0);
                    const height = sampleWorldGroundHeight(worldX,worldZ)+ROAD_SURFACE_LIFT;
                    box.min.set(worldX - 0.45 * scale, height - 0.04, worldZ - 0.45 * scale);
                    box.max.set(worldX + 0.45 * scale, height + (object.bridge ? .2 : .08), worldZ + 0.45 * scale);
                    if (!raycaster.ray.intersectBox(box, point)) return;
                    const nextDistance = raycaster.ray.origin.distanceTo(point);
                    if (nextDistance < distance) {
                        distance = nextDistance;
                        nearest = { chunkX: entry.chunkX, chunkY: entry.chunkY, tileX: x, tileY: y, index };
                    }
                });
            }
        }
    }
    return nearest;
}

function performRaycast(clientX, clientY) {
    if (window.isLocationActive) return;
    lastHoverRaycastAt = performance.now();
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);

    const candidates = chunkBounds.filter(entry => entry.mesh.visible && raycaster.ray.intersectsBox(entry.box)).map(e => e.mesh);
    const intersects = raycaster.intersectObjects(candidates);

    if (hoveredTile) {
        highlightBox.visible = false;
        hoveredTile = null;
    }
    if (tileInfoDiv) tileInfoDiv.style.display = 'none';

    const point = intersects[0]?.point
        || (editMode ? raycaster.ray.intersectPlane(worldBrushPlane, new THREE.Vector3()) : null);
    if (point) {

        const globalX = point.x;
        const globalZ = point.z;

        const chunkX = Math.floor(globalX / CHUNK_SIZE);
        const chunkY = Math.floor(globalZ / CHUNK_SIZE);
        const tileX = Math.floor(globalX % CHUNK_SIZE);
        const tileY = Math.floor(globalZ % CHUNK_SIZE);

        if (chunkX >= MIN_CHUNK && chunkX <= MAX_CHUNK_X && chunkY >= MIN_CHUNK && chunkY <= MAX_CHUNK_Y &&
            tileX >= 0 && tileX < CHUNK_SIZE && tileY >= 0 && tileY < CHUNK_SIZE) {

            const key = `${chunkX},${chunkY}`;
            const chunkEntry = chunksMap.get(key);
            if (chunkEntry) {
                const tileData = chunkEntry.tilesData[tileY][tileX];
                hoveredTile = { chunk: chunkEntry, tileX, tileY, tileData,
                    pointX: globalX, pointZ: globalZ };

                const worldX = chunkX * CHUNK_SIZE + tileX + 0.5;
                const worldZ = chunkY * CHUNK_SIZE + tileY + 0.5;
                const height = tileData.height || 1.0;

                const areaSize = window.worldEditorTool === 'objects' && !window.isWorldVegetationBrush?.()
                    ? 1 : 2 * currentBrushRadius + 1;
                if(editMode&&window.worldEditorTool==='sculpt')updateWorldSculptHighlight(globalX,globalZ,currentBrushRadius);
                else {
                    highlightBox.geometry=highlightBoxGeo;
                    highlightBox.scale.set(areaSize, 0.1, areaSize);
                    highlightBox.position.set(worldX, height + 0.1, worldZ);
                    highlightBox.visible = true;
                }

                if (tileInfoDiv && tileInfoContent && !window.hasDirectWorldObjectSelection?.()) {
                    tileInfoContent.innerHTML = `
                        <b>(${chunkX * CHUNK_SIZE + tileX}, ${chunkY * CHUNK_SIZE + tileY})</b><br>
                        Высота: ${window.worldEditorTool==='sculpt' ? Number(Number(tileData.height).toFixed(3)) : tileData.height}<br>
                        Радиация: ${tileData.radiation ?? 0}
                    `;
                    tileInfoDiv.style.display = 'block';
                }
            }
        }
    }
    if (editMode && window.worldEditorTool === 'objects') window.updateWorldObjectPreview?.(hoveredTile);
}

function finishWorldBrushStroke(cancel = false) {
    window.endWorldSculptStroke?.(cancel === true || ['blur','pointercancel'].includes(cancel?.type));
    if(window.getWorldEditorHistoryState?.().kind==='brush')void window.endWorldEditorStroke(cancel === true || ['blur','pointercancel'].includes(cancel?.type));
    if (!brushActive && !globalMouseUpHandler) return;
    isMouseDown = false;
    lastProcessedTileKey = null;
    if (controlsDisabled) {
        controls.enabled = true;
        controlsDisabled = false;
    }
    brushActive = false;
    window.endWorldVegetationStroke?.();
    if (globalMouseUpHandler) {
        document.removeEventListener('mouseup', globalMouseUpHandler, { capture: true });
        globalMouseUpHandler = null;
    }
    if (pendingWorldTerrainChunks.size) {
        clearTimeout(worldTerrainRefreshTimer);
        worldTerrainRefreshTimer = setTimeout(flushWorldTerrainRefresh, 0);
    }
}
window.cancelWorldBrushStroke = () => finishWorldBrushStroke(true);

function canvasMouseDownHandler(event) {
    if (window.isLocationActive) return;
    if(window.handleWorldClipboardPointerDown?.(event))return;
    if (event.button === 2 && editMode) {
        finishWorldBrushStroke(true);
        controls.enabled = true;
        controlsDisabled = false;
        brushActive = false;
        return;
    }
    if (event.button !== 0) return;
    event.preventDefault();
    if (event.target.closest('.ui-overlay')) return;

    if (editMode && window.worldEditorTool === 'select' && !window.isWorldTravelSelectionActive?.()
        && window.beginDirectWorldObjectEdit?.(event)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
    }

    performRaycast(event.clientX, event.clientY);

    isMouseDown = true;
    lastProcessedTileKey = null;

    const isEditing = editMode && !window.isWorldTravelSelectionActive?.()
        && ['terrain', 'height', 'sculpt', 'radiation', 'erase', 'objects'].includes(window.worldEditorTool);

    if (isEditing) {
        event.preventDefault();
        event.stopImmediatePropagation();

        controls.enabled = false;
        controlsDisabled = true;
        brushActive = true;
        if (window.worldEditorTool === 'sculpt') {
            if (!window.beginWorldSculptStroke?.(event,hoveredTile)) finishWorldBrushStroke(true);
            return;
        }
        if ((['terrain','height','radiation','erase'].includes(window.worldEditorTool)
            || window.worldEditorTool==='objects'&&window.isWorldVegetationBrush?.())
            && window.beginWorldEditorStroke && !window.beginWorldEditorStroke('brush')) {
            finishWorldBrushStroke(true);return;
        }
        if (window.worldEditorTool === 'objects') window.beginWorldVegetationStroke?.();

        if (hoveredTile && window.tileClickCallback) {
            window.tileClickCallback({
                tile: {
                    chunkX: hoveredTile.chunk.chunkX,
                    chunkY: hoveredTile.chunk.chunkY,
                    tileX: hoveredTile.tileX,
                    tileY: hoveredTile.tileY,
                    tileData: hoveredTile.tileData,
                    pointX: hoveredTile.pointX,
                    pointZ: hoveredTile.pointZ,
                },
                event,
                isDoubleClick: false,
                isDrag: false
            });
            lastProcessedTileKey = `${hoveredTile.chunk.chunkX},${hoveredTile.chunk.chunkY},${hoveredTile.tileX},${hoveredTile.tileY}`;
        }

        if (!globalMouseUpHandler) {
            globalMouseUpHandler = (e) => {
                if (e.button !== 0) return;
                finishWorldBrushStroke();
            };
            document.addEventListener('mouseup', globalMouseUpHandler, { capture: true });
        }
    }
}

renderer.domElement.addEventListener('pointerdown', canvasMouseDownHandler, { capture: true });
window.addEventListener('pointerup', event => {
    if (event.button === 0) finishWorldBrushStroke();
}, { capture: true });
window.addEventListener('pointercancel', finishWorldBrushStroke, { capture: true });
window.addEventListener('blur', finishWorldBrushStroke);
renderer.domElement.addEventListener('contextmenu', event => event.preventDefault());
installSecondaryClick(renderer.domElement);
renderer.domElement.addEventListener('secondaryclick',event=>{
    if(!window.isLocationActive&&editMode&&window.cancelWorldObjectPlacement?.()){
        event.preventDefault();event.stopImmediatePropagation();finishWorldBrushStroke();
    }
});
renderer.domElement.addEventListener('dragstart', event => event.preventDefault());

window.addEventListener('click', (event) => {
    if (window.isLocationActive) return;
    if (event.target.closest('.ui-overlay')) return;
    if (hoveredTile && window.worldTravelTileClickCallback) {
        const consumed = window.worldTravelTileClickCallback({
            tile: {
                chunkX: hoveredTile.chunk.chunkX,
                chunkY: hoveredTile.chunk.chunkY,
                tileX: hoveredTile.tileX,
                tileY: hoveredTile.tileY,
                tileData: hoveredTile.tileData
            },
            event
        });
        if (consumed) return;
    }
});

window.addEventListener('dblclick', (event) => {
    if (window.isLocationActive) return;
    if (editMode && window.worldEditorTool === 'sculpt') return;
    if(editMode&&window.worldEditorTool==='objects'&&window.isWorldRoadRouteTool?.())return;
    if(editMode && window.worldEditorTool==='objects' && window.isWorldFenceLineTool?.())return;
    if (event.target.closest('.ui-overlay')) return;
    if (editMode && hoveredTile && window.tileClickCallback) {
        window.tileClickCallback({
            tile: {
                chunkX: hoveredTile.chunk.chunkX,
                chunkY: hoveredTile.chunk.chunkY,
                tileX: hoveredTile.tileX,
                tileY: hoveredTile.tileY,
                tileData: hoveredTile.tileData
            },
            event,
            isDoubleClick: true,
            isDrag: false
        });
    }
});

let lastTime = performance.now();
function updateGlobalCameraMovement(deltaSeconds) {
    if (window.isLocationActive || globalCameraKeys.size === 0) return;
    const { forward, right } = horizontalCameraAxes(THREE, camera);
    const movement = new THREE.Vector3();
    if (globalCameraKeys.has('KeyW')) movement.add(forward);
    if (globalCameraKeys.has('KeyS')) movement.sub(forward);
    if (globalCameraKeys.has('KeyD')) movement.add(right);
    if (globalCameraKeys.has('KeyA')) movement.sub(right);
    if (movement.lengthSq() === 0) return;

    const distanceToTarget = camera.position.distanceTo(controls.target);
    movement.normalize().multiplyScalar(Math.max(20, distanceToTarget * 0.7) * deltaSeconds);
    const oldTarget = controls.target.clone();
    const nextTarget = oldTarget.clone().add(movement);
    nextTarget.x = THREE.MathUtils.clamp(nextTarget.x, 0.5, (MAX_CHUNK_X + 1) * CHUNK_SIZE - 0.5);
    nextTarget.z = THREE.MathUtils.clamp(nextTarget.z, 0.5, (MAX_CHUNK_Y + 1) * CHUNK_SIZE - 0.5);
    const appliedMovement = nextTarget.sub(oldTarget);
    controls.target.add(appliedMovement);
    camera.position.add(appliedMovement);
}

function animate() {
    const now = performance.now();
    const delta = Math.min(0.1, Math.max(0, (now - lastTime) / 1000));
    lastTime = now;

    requestAnimationFrame(animate);
    // The sublocation has its own renderer; do not render the hidden world behind it.
    if (document.hidden || window.isLocationActive) {
        worldFpsCounter.pause();
        worldPerfSampleAt = now;
        worldFrameWorkMs = 0;
        worldSampleFrames = 0;
        shadowRefreshesSinceSample = 0;
        return;
    }
    const frameWorkStartedAt = performance.now();
    if (!renderer.isUnavailableRenderer) {
        window.updateWorldClipboard?.();
        window.updateWorldSculpt?.(now);
        refreshWorldTerrainBrushPreview();
        updateGlobalCameraMovement(Math.min(0.1, Math.max(0, delta)));
        const cameraMoved = controls.update();
        if (cameraMoved || now - lastChunkVisibilityAt >= CHUNK_VISIBILITY_REFRESH_MS) {
            updateChunkVisibility();
            lastChunkVisibilityAt = now;
        }
        updateRain(delta);
        animateChunkAnomalies(now);
        waterTexture.offset.set((now * 0.000002) % 1, (now * 0.000001) % 1);
        worldSkySphere.position.copy(camera.position);
        stars.position.copy(camera.position);

        if ((lastMouseX !== 0 || lastMouseY !== 0)
            && !cameraDragActive
            && (hoverRaycastPending
                || (cameraMoved && now - lastHoverRaycastAt >= MOVING_CAMERA_RAYCAST_REFRESH_MS)
                || now - lastHoverRaycastAt >= HOVER_RAYCAST_REFRESH_MS)) {
            performRaycast(lastMouseX, lastMouseY);
            hoverRaycastPending = false;
        }
        refreshWorldShadows(now, cameraMoved);
    }

    if (!renderer.isUnavailableRenderer) {
        renderer.render(scene, camera);
        const metrics = window.worldLoadMetrics;
        if (metrics && metrics.firstFrameMs === null && chunksMap.size) {
            metrics.firstFrameMs = now - metrics.startedAt;
            metrics.pageToFirstFrameMs = now;
            performance.mark('world-map-first-frame');
            performance.measure('world-map-to-first-frame', 'world-map-load-start', 'world-map-first-frame');
        }
        worldFpsCounter.frame(now);
        worldFrameWorkMs += performance.now() - frameWorkStartedAt;
        worldSampleFrames += 1;
        if (now - worldPerfSampleAt >= 1000) {
            const renderInfo = renderer.info?.render;
            if (renderInfo) {
                worldFpsCounter.setDetails(
                    `calls ${renderInfo.calls} | tris ${Math.round(renderInfo.triangles / 1000)}k | CPU ${(worldFrameWorkMs / worldSampleFrames).toFixed(1)}ms | shadows ${shadowRefreshesSinceSample}`
                );
            }
            worldPerfSampleAt = now;
            worldFrameWorkMs = 0;
            worldSampleFrames = 0;
            shadowRefreshesSinceSample = 0;
        }
    } else {
        worldFpsCounter.pause();
    }
    postRenderCallbacks.forEach(cb => cb());
}
animate();

window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});

export function getObjectDimensions(type, anomalyType, scale = 1.0) {
    const base = getBaseDimensions(type, anomalyType);
    return {
        width: base.width * scale,
        height: base.height * scale,
        depth: base.depth * scale
    };
}

const highlightWireframeMaterial = new THREE.MeshBasicMaterial({ color: 0xff0000, wireframe: true, transparent: true, opacity: 0.8 });
const highlightWireframeGeometry = new THREE.BoxGeometry(1, 1, 1);
const highlightWireframe = new THREE.Mesh(highlightWireframeGeometry, highlightWireframeMaterial);
scene.add(highlightWireframe);
highlightWireframe.visible = false;
const objectHighlights = [highlightWireframe];

export function showObjectHighlights(items) {
    hideObjectHighlight();
    items.forEach((item, index) => {
        let mesh = objectHighlights[index];
        if (!mesh) {
            mesh = new THREE.Mesh(highlightWireframeGeometry, highlightWireframeMaterial);
            objectHighlights.push(mesh); scene.add(mesh);
        }
        mesh.position.set(item.x, item.y, item.z);
        mesh.scale.set(item.dimensions.width, item.dimensions.height, item.dimensions.depth);
        mesh.rotation.y = THREE.MathUtils.degToRad(item.rotation || 0);
        mesh.visible = true;
    });
}

export function showObjectHighlight(x, y, z, dimensions, rotation = 0) {
    highlightWireframe.position.set(x, y, z);
    highlightWireframe.scale.set(dimensions.width, dimensions.height, dimensions.depth);
    highlightWireframe.rotation.y = THREE.MathUtils.degToRad(rotation);
    highlightWireframe.visible = true;
}

export function hideObjectHighlight() {
    objectHighlights.forEach(mesh => { mesh.visible = false; });
}

export function getHoveredTile() {
    return hoveredTile;
}

window.addEventListener('pointermove', (event) => {
    if (window.isLocationActive) return;
    lastMouseX = event.clientX;
    lastMouseY = event.clientY;
    lastModifiers.alt = event.altKey;
    lastModifiers.shift = event.shiftKey;

    cameraDragActive = Boolean(event.buttons & 6);
    if (cameraDragActive) {
        hoverRaycastPending = true;
        return;
    }
    performRaycast(event.clientX, event.clientY);

    if(window.worldEditorTool==='terrain'&&window.currentTileType==='water'&&document.getElementById('world-water-basin')?.checked)return;
    if ((event.buttons === 1) && brushActive && editMode && hoveredTile && window.applyBrush) {
        const tileKey = `${hoveredTile.chunk.chunkX},${hoveredTile.chunk.chunkY},${hoveredTile.tileX},${hoveredTile.tileY}`;
        if (lastProcessedTileKey === tileKey) return;
        if (window.worldEditorTool === 'objects' && window.isWorldVegetationBrush?.()) {
            lastProcessedTileKey = tileKey;
            window.placeWorldObjectAtTile?.({
                chunkX: hoveredTile.chunk.chunkX, chunkY: hoveredTile.chunk.chunkY,
                tileX: hoveredTile.tileX, tileY: hoveredTile.tileY, tileData: hoveredTile.tileData,
                pointX: hoveredTile.pointX, pointZ: hoveredTile.pointZ,
            });
            return;
        }
        const updates = {};
        if (window.eraserMode) {
            updates.objects = [];
        } else if (window.worldEditorTool === 'terrain') {
            updates.terrain = window.currentTileType;

        } else if (window.worldEditorTool === 'height') {
            updates.height = window.tileHeight;
        } else if (window.worldEditorTool === 'radiation') {
            updates.radiation = window.worldBrushRadiation ?? 0;
        }
        if (Object.keys(updates).length > 0) {
            lastProcessedTileKey = tileKey;
            window.applyBrush(hoveredTile, updates, window.brushRadius);
        }
    }
}, { capture: true });

window.addEventListener('pointerup', () => {
    cameraDragActive = false;
    hoverRaycastPending = true;
});

window.addEventListener('keydown', (e) => {
    const isTyping = isTypingTarget(e.target);
    if (['KeyW', 'KeyA', 'KeyS', 'KeyD'].includes(e.code) && !isTyping && !window.isLocationActive) {
        globalCameraKeys.add(e.code);
        e.preventDefault();
    } else if (e.key === 'Alt') {
        lastModifiers.alt = true;
        e.preventDefault();
    } else if (e.key === 'Shift') {
        lastModifiers.shift = true;
    }
}, { capture: true });

window.addEventListener('keyup', (e) => {
    if (['KeyW', 'KeyA', 'KeyS', 'KeyD'].includes(e.code)) {
        globalCameraKeys.delete(e.code);
    }
    if (e.key === 'Alt') {
        lastModifiers.alt = false;
    } else if (e.key === 'Shift') {
        lastModifiers.shift = false;
    }
}, { capture: true });

window.addEventListener('blur', () => globalCameraKeys.clear());

export function getTileHeightAt(globalX, globalZ) {
    const chunkX = Math.floor(globalX / CHUNK_SIZE);
    const chunkY = Math.floor(globalZ / CHUNK_SIZE);
    const key = `${chunkX},${chunkY}`;
    const chunk = chunksMap.get(key);
    if (!chunk) return 0;
    const tileX = Math.floor(globalX % CHUNK_SIZE);
    const tileY = Math.floor(globalZ % CHUNK_SIZE);
    if (tileX < 0 || tileX >= CHUNK_SIZE || tileY < 0 || tileY >= CHUNK_SIZE) return 0;
    return chunk.tilesData[tileY][tileX].height || 1.0;
}

export { scene, camera, renderer, controls, directionalLight, ambientLight, fillLight, waterMat,
    waitForWorldTerrainRefresh, beginWorldTerrainLoad, prepareInitialWorldTerrainPixels };

// ========== ВРЕМЕННОЕ СКРЫТИЕ ГЛОБАЛЬНОЙ КАРТЫ (ДЛЯ ЛОКАЦИЙ) ==========
let globalCanvasParent = null;
let globalCanvasNextSibling = null;

export function hideGlobalCanvas() {
    window.cancelWorldFenceLine?.();
    window.cancelWorldRoadRoute?.();removeWorldRoadPreview();
    const canvas = renderer?.domElement;
    if (!canvas || !canvas.parentNode) return;
    // Сохраняем родителя и позицию в DOM
    globalCanvasParent = canvas.parentNode;
    globalCanvasNextSibling = canvas.nextSibling;
    canvas.remove();
}

export function showGlobalCanvas() {
    if (!globalCanvasParent || !renderer?.domElement) return;
    // Вставляем обратно на то же место
    if (globalCanvasNextSibling) {
        globalCanvasParent.insertBefore(renderer.domElement, globalCanvasNextSibling);
    } else {
        globalCanvasParent.appendChild(renderer.domElement);
    }
    // Принудительно перерисовываем один раз, чтобы канвас ожил
    renderer.render(scene, camera);
}
