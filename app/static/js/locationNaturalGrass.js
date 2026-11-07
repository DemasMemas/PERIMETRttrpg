import { acceptsDetail, edgePosition } from './locationGrassDetail.js';

const ROOT = '/static/models/vegetation/polyhaven-d51/grass_medium_01/';
const MODEL = '/static/models/vegetation/grass_medium_01_game_d8.glb';
const CHUNK_SIZE = 12;
export const NATURAL_GRASS_ROOT_EMBED = 0.018;
let assetPromise = null;

// Anchor the rotated/scaled root, rather than the unrotated geometry's minimum Y.
// The terrain samples are the same footprint already used by the slope filter.
export function naturalGrassRootPose(plant, source, height, slopeX = 0, slopeZ = 0) {
    const sinY = Math.sin(plant.turn), cosY = Math.cos(plant.turn);
    const localNormalX = -slopeX * cosY + slopeZ * sinY;
    const localNormalZ = -slopeX * sinY - slopeZ * cosY;
    const limit = value => Math.max(-0.35, Math.min(0.35, value));
    const pitch = limit(Math.atan2(localNormalZ, 1)) + plant.lean;
    const roll = limit(-Math.atan2(localNormalX, Math.hypot(1, localNormalZ))) + plant.lean * 0.55;
    const root = source.bottom * source.scale * plant.size;
    const rootX = root * (-cosY * Math.sin(roll) + sinY * Math.sin(pitch) * Math.cos(roll));
    const rootY = root * Math.cos(pitch) * Math.cos(roll);
    const rootZ = root * (sinY * Math.sin(roll) + cosY * Math.sin(pitch) * Math.cos(roll));
    return { x: plant.x - rootX, y: height - NATURAL_GRASS_ROOT_EMBED * plant.size - rootY,
        z: plant.z - rootZ, pitch, roll };
}

function hash(x, z, salt) {
    let value = Math.imul(x + salt * 37, 374761393) ^ Math.imul(z + salt * 53, 668265263);
    value = Math.imul(value ^ (value >>> 13), 1274126177);
    return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

function patch(x, z) {
    const ix = Math.floor(x / 4);
    const iz = Math.floor(z / 4);
    const fx = x / 4 - ix;
    const fz = z / 4 - iz;
    const sx = fx * fx * (3 - 2 * fx);
    const sz = fz * fz * (3 - 2 * fz);
    const a = hash(ix, iz, 81) * (1 - sx) + hash(ix + 1, iz, 81) * sx;
    const b = hash(ix, iz + 1, 81) * (1 - sx) + hash(ix + 1, iz + 1, 81) * sx;
    return a * (1 - sz) + b * sz;
}

const emptyStats = () => ({ grassTiles: 0, plainTiles: 0, forestTiles: 0,
    densityRejected: 0, blendRejected: 0, floorRejected: 0 });

export function naturalGrassPlacements(tiles, isCovered = () => false, chunk = null) {
    const medium01 = [];
    const stats = emptyStats();
    const statsByChunk = new Map();
    const count = (field, local) => { if (local) { stats[field]++; local[field]++; } };
    // Roots can extend 0.28m beyond their source tile. Include the adjoining
    // source tiles, then keep only roots belonging to the requested render chunk.
    const minZ = chunk ? Math.max(0, chunk.z * CHUNK_SIZE - 1) : 0;
    const maxZ = chunk ? Math.min(tiles.length, (chunk.z + 1) * CHUNK_SIZE + 1) : tiles.length;
    for (let z = minZ; z < maxZ; z++) {
        const minX = chunk ? Math.max(0, chunk.x * CHUNK_SIZE - 1) : 0;
        const maxX = chunk ? Math.min(tiles[z].length, (chunk.x + 1) * CHUNK_SIZE + 1) : tiles[z].length;
        for (let x = minX; x < maxX; x++) {
            const key = `${Math.floor(x / CHUNK_SIZE)},${Math.floor(z / CHUNK_SIZE)}`;
            const ownStats = !chunk || key === `${chunk.x},${chunk.z}`;
            if (ownStats && !statsByChunk.has(key)) statsByChunk.set(key, emptyStats());
            const local = ownStats ? statsByChunk.get(key) : null;
            const tile = tiles[z][x];
            if (tile.terrain !== 'grass') continue;
            count('grassTiles', local);
            const forest = tile.grass_subtype === 'forest';
            count(forest ? 'forestTiles' : 'plainTiles', local);
            const density = patch(x + 0.5, z + 0.5);
            const candidates = forest ? 4 : 5;
            const fringe = [[1, 0], [-1, 0], [0, 1], [0, -1]].filter(([dx, dz]) => {
                const neighbor = tiles[z + dz]?.[x + dx]?.terrain;
                return neighbor && neighbor !== 'grass' && neighbor !== 'water';
            });
            for (let index = 0; index < candidates; index++) {
                const chance = forest ? 0.36 + density * 0.28 : 0.38 + density * 0.3;
                const edge = index === candidates - 1 && fringe.length
                    ? fringe[Math.min(fringe.length - 1,
                        Math.floor(hash(x, z, 105) * fringe.length))] : null;
                if (index && !edge && hash(x, z, 110 + index) >= chance) {
                    count('densityRejected', local);
                    continue;
                }
                const px = edge ? x + (edge[0] > 0 ? 1.02 : edge[0] < 0 ? -0.02
                    : 0.15 + hash(x, z, 210 + index) * 0.7)
                    : index === 0 ? x + 0.32 + hash(x, z, 210) * 0.36
                        : edgePosition(x, hash(x, z, 210 + index), 610 + z * 5 + index);
                const pz = edge ? z + (edge[1] > 0 ? 1.02 : edge[1] < 0 ? -0.02
                    : 0.15 + hash(x, z, 310 + index) * 0.7)
                    : index === 0 ? z + 0.32 + hash(x, z, 310) * 0.36
                        : edgePosition(z, hash(x, z, 310 + index), 710 + x * 5 + index);
                if (!acceptsDetail(tiles, 'grass', px, pz, 990 + index)) {
                    count('blendRejected', local);
                    continue;
                }
                // The whole clump must clear the floor, including diagonal
                // leaves at maximum random scale, rotation and slope lean.
                if (isCovered(px, pz, 0.45)) {
                    count('floorRejected', local);
                    continue;
                }
                if([-.4,0,.4].some(dx=>[-.4,0,.4].some(dz=>['concrete','granite_tiles','metal','wood_floor']
                    .includes(tiles[Math.floor(pz+dz)]?.[Math.floor(px+dx)]?.terrain)))){
                    count('blendRejected',local);
                    continue;
                }
                if (chunk && (Math.floor(px / CHUNK_SIZE) !== chunk.x
                    || Math.floor(pz / CHUNK_SIZE) !== chunk.z)) continue;
                medium01.push({ x: px, z: pz,
                    size: 0.86 + hash(x, z, 410 + index) * 0.28,
                    width: 0.9 + hash(x, z, 610 + index) * 0.25,
                    depth: 0.9 + hash(x, z, 710 + index) * 0.25,
                    lean: (hash(x, z, 810 + index) - 0.5) * 0.16,
                    tone: hash(x, z, 910 + index),
                    turn: hash(x, z, 510 + index) * Math.PI * 2 });
            }
        }
    }
    return { medium01, stats, statsByChunk };
}

export function loadNaturalGrassAssets(THREE, outputEncoding = THREE.LinearEncoding) {
    if (assetPromise) return assetPromise;
    assetPromise = import('https://unpkg.com/three@0.128.0/examples/jsm/loaders/GLTFLoader.js')
        .then(({ GLTFLoader }) => {
            const loader = new GLTFLoader();
            const textures = new THREE.TextureLoader();
            const fail = (url, reject) => error => reject(new Error(
                `Failed to load grass asset ${url}: ${error?.message || error?.type || error}`));
            const model = url => new Promise((resolve, reject) => loader.load(
                url, resolve, undefined, fail(url, reject)));
            const texture = url => new Promise((resolve, reject) => textures.load(
                url, resolve, undefined, fail(url, reject)));
            return Promise.all([
                model(MODEL),
                texture(`${ROOT}textures/grass_medium_01_diff_1k.jpg`),
                texture(`${ROOT}textures/grass_medium_01_alpha_1k.png`),
                texture(`${ROOT}textures/grass_medium_01_nor_gl_1k.jpg`),
            ]);
        }).then(([model, map, alphaMap, normalMap]) => {
            let mesh = null;
            model.scene.traverse(child => {
                if (child.isMesh && child.name === 'grass_medium_01_mid_a_LOD0') mesh = child;
            });
            if (!mesh) throw new Error('Missing optimized grass mesh');
            for (const texture of [map, alphaMap, normalMap]) {
                texture.flipY = false;
                texture.minFilter = THREE.LinearMipmapLinearFilter;
                texture.magFilter = THREE.LinearFilter;
            }
            // Keep the atlas in the renderer's output space. The local scene currently
            // outputs linear; decoding sRGB without re-encoding made these leaves black.
            map.encoding = outputEncoding;
            const material = new THREE.MeshStandardMaterial({
                map, alphaMap, normalMap,
                normalScale: new THREE.Vector2(0.35, 0.35),
                alphaTest: 0.45, transparent: false, depthWrite: true,
                side: THREE.DoubleSide, roughness: 0.95, metalness: 0,
            });
            mesh.geometry.computeBoundingBox();
            const bounds = mesh.geometry.boundingBox;
            return { medium01: { geometry: mesh.geometry, material,
                scale: 0.18 / (bounds.max.y - bounds.min.y),
                spread: 0.34 / Math.max(bounds.max.x - bounds.min.x,
                    bounds.max.z - bounds.min.z), bottom: bounds.min.y } };
        }).catch(error => {
            assetPromise = null;
            throw error;
        });
    return assetPromise;
}

// Enclose the source box after the exact Float32 transform sent to the GPU.
function expandGrassBounds(bounds, box, matrix, offset) {
    const center = [(box.min.x + box.max.x) / 2, (box.min.y + box.max.y) / 2,
        (box.min.z + box.max.z) / 2];
    const half = [(box.max.x - box.min.x) / 2, (box.max.y - box.min.y) / 2,
        (box.max.z - box.min.z) / 2];
    for (let axis = 0; axis < 3; axis++) {
        let position = matrix[offset + 12 + axis], extent = 0;
        for (let column = 0; column < 3; column++) {
            const value = matrix[offset + column * 4 + axis];
            position += value * center[column];
            extent += Math.abs(value) * half[column];
        }
        bounds[axis] = Math.min(bounds[axis], position - extent);
        bounds[axis + 3] = Math.max(bounds[axis + 3], position + extent);
    }
}

function refreshGrassBounds(THREE, group) {
    // r128 tests geometry.boundingSphere, not a sphere on InstancedMesh.
    // Geometry is shared: its chunk-local bound must enclose every live chunk,
    // including unchanged chunks at different heights after a local edit.
    for (const [kind, { geometry }] of Object.entries(group.userData.naturalGrassResources)) {
        const bounds = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
        for (const mesh of group.children) if (mesh.userData.naturalGrassKind === kind) {
            const box = mesh.userData.naturalGrassBounds;
            for (let axis = 0; axis < 3; axis++) {
                bounds[axis] = Math.min(bounds[axis], box[axis]);
                bounds[axis + 3] = Math.max(bounds[axis + 3], box[axis + 3]);
            }
        }
        if (!Number.isFinite(bounds[0])) continue;
        // Leave 2 cm beyond the transformed box for frustum-plane roundoff.
        geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(
            (bounds[0] + bounds[3]) / 2, (bounds[1] + bounds[4]) / 2,
            (bounds[2] + bounds[5]) / 2), Math.hypot(bounds[3] - bounds[0],
            bounds[4] - bounds[1], bounds[5] - bounds[2]) / 2 + 0.02);
    }
}

export function addNaturalGrassInstances(THREE, group, placements, assets, getHeight) {
    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    const state = group.userData.naturalGrassChunks ??= new Map();
    const resources = group.userData.naturalGrassResources ??= {};
    group.userData.naturalGrassBaseTufts ??= group.userData.detailCounts.tufts;
    for (const [key, stats] of placements.statsByChunk || []) {
        state.set(key, { stats, created: 0, added: 0, mesh: null });
    }
    for (const kind of ['medium01']) {
        for (const plant of placements[kind]) {
            const key = `${Math.floor(plant.x / CHUNK_SIZE)},${Math.floor(plant.z / CHUNK_SIZE)}`;
            if (!state.has(key)) state.set(key, { stats: emptyStats(), created: 0, added: 0, mesh: null });
            state.get(key).created++;
        }
        const slopes = new Map();
        const plants = placements[kind].filter(plant => {
            const center = getHeight(plant.x, plant.z);
            const left = getHeight(plant.x - 0.14, plant.z), right = getHeight(plant.x + 0.14, plant.z);
            const near = getHeight(plant.x, plant.z - 0.14), far = getHeight(plant.x, plant.z + 0.14);
            slopes.set(plant, { height: center, x: (right - left) / 0.28, z: (far - near) / 0.28 });
            return Math.max(Math.abs(center - left), Math.abs(center - right),
                Math.abs(center - near), Math.abs(center - far)) < 0.18;
        });
        if (!plants.length) continue;
        const source = assets[kind];
        if (!resources[kind]) {
            const geometry = source.geometry.clone();
            const material = source.material.clone();
            const textures = ['map', 'alphaMap', 'normalMap'].filter(key => material[key]).map(key => {
                material[key] = material[key].clone();
                material[key].needsUpdate = true;
                return material[key];
            });
            resources[kind] = { geometry, material, textures };
        }
        const { geometry, material } = resources[kind];
        const chunks = new Map();
        for (const plant of plants) {
            const cx = Math.floor(plant.x / CHUNK_SIZE);
            const cz = Math.floor(plant.z / CHUNK_SIZE);
            const key = `${cx},${cz}`;
            if (!chunks.has(key)) chunks.set(key, { cx, cz, plants: [] });
            chunks.get(key).plants.push(plant);
        }
        for (const { cx, cz, plants: chunkPlants } of chunks.values()) {
            const mesh = new THREE.InstancedMesh(geometry, material, chunkPlants.length);
            mesh.position.set(cx * CHUNK_SIZE, 0, cz * CHUNK_SIZE);
            const bounds = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
            for (let index = 0; index < chunkPlants.length; index++) {
                const plant = chunkPlants[index];
                const slope = slopes.get(plant);
                const pose = naturalGrassRootPose(plant, source, slope.height, slope.x, slope.z);
                dummy.position.set(pose.x - mesh.position.x, pose.y, pose.z - mesh.position.z);
                dummy.rotation.set(pose.pitch, plant.turn, pose.roll, 'YXZ');
                dummy.scale.set(source.spread * plant.size * plant.width,
                    source.scale * plant.size,
                    source.spread * plant.size * plant.depth);
                dummy.updateMatrix();
                mesh.setMatrixAt(index, dummy.matrix);
                expandGrassBounds(bounds, source.geometry.boundingBox, mesh.instanceMatrix.array, index * 16);
                color.setRGB(...(plant.tone < 0.16 ? [1.08, 1.03, 0.94]
                    : plant.tone < 0.42 ? [0.98, 1.03, 0.98]
                        : [1, 1, 1]));
                mesh.setColorAt(index, color);
            }
            mesh.instanceMatrix.needsUpdate = true;
            mesh.instanceColor.needsUpdate = true;
            mesh.userData.naturalGrassKind = kind;
            mesh.userData.naturalGrassChunk = `${cx},${cz}`;
            mesh.userData.naturalGrassBounds = bounds;
            mesh.frustumCulled = true;
            mesh.castShadow = false;
            mesh.receiveShadow = false;
            mesh.raycast = () => {};
            group.add(mesh);
            Object.assign(state.get(`${cx},${cz}`), { mesh, added: chunkPlants.length });
        }
    }
    refreshGrassBounds(THREE, group);
    return naturalGrassSummary(group, assets);
}

function naturalGrassSummary(group, assets) {
    const stats = emptyStats();
    let created = 0, added = 0, chunks = 0;
    for (const entry of group.userData.naturalGrassChunks.values()) {
        for (const field of Object.keys(stats)) stats[field] += entry.stats[field];
        created += entry.created;
        added += entry.added;
        if (entry.mesh) chunks++;
    }
    const renderedCounts = { medium01: added };
    group.userData.naturalGrassCounts = renderedCounts;
    group.userData.detailCounts.tufts = group.userData.naturalGrassBaseTufts + added;
    return { stats, created: { medium01: created },
        added: renderedCounts,
        slopeRejected: { medium01: created - added },
        chunks: { medium01: chunks },
        triangles: Object.fromEntries(['medium01'].map(kind => {
            const geometry = assets[kind].geometry;
            const triangles = (geometry.index ? geometry.index.count
                : geometry.getAttribute('position').count) / 3;
            return [kind, Math.round(renderedCounts[kind] * triangles)];
        })) };
}

function releaseUnusedGrassResources(group) {
    for (const [kind, resources] of Object.entries(group.userData.naturalGrassResources || {})) {
        if (group.children.some(mesh => mesh.userData.naturalGrassKind === kind)) continue;
        resources.geometry.dispose();
        resources.material.dispose();
        // One wrapper set per group, shared by its chunks; CPU atlas images stay cached.
        resources.textures.forEach(texture => texture.dispose());
        delete group.userData.naturalGrassResources[kind];
    }
}

export function disposeNaturalGrassInstances(group) {
    for (const mesh of [...group.children]) {
        if (!mesh.userData.naturalGrassKind) continue;
        group.remove(mesh);
        mesh.dispose?.(); // Release instance buffers, keeping shared resources until the last chunk.
    }
    releaseUnusedGrassResources(group);
    group.userData.naturalGrassChunks?.clear();
}

export function updateNaturalGrassInstances(THREE, group, tiles, assets, getHeight, isCovered, changed) {
    const keys = new Set();
    for (const { x, z } of changed) {
        // Height interpolation uses neighboring tiles, plus the +/-0.14m slope
        // probes. Terrain blending and displaced roots also fit within this halo.
        for (let cz = Math.max(0, Math.floor((z - 2) / CHUNK_SIZE));
            cz <= Math.min(Math.ceil(tiles.length / CHUNK_SIZE) - 1, Math.floor((z + 2) / CHUNK_SIZE)); cz++) {
            for (let cx = Math.max(0, Math.floor((x - 2) / CHUNK_SIZE));
                cx <= Math.min(Math.ceil(tiles[0].length / CHUNK_SIZE) - 1, Math.floor((x + 2) / CHUNK_SIZE)); cx++) {
                keys.add(`${cx},${cz}`);
            }
        }
    }
    for (const key of keys) {
        const previous = group.userData.naturalGrassChunks.get(key)?.mesh;
        if (previous) { group.remove(previous); previous.dispose?.(); }
    }
    // When every chunk is affected (e.g. a small location's shared corner),
    // generate once instead of repeatedly visiting overlapping source halos.
    if (keys.size === group.userData.naturalGrassChunks.size) {
        addNaturalGrassInstances(THREE, group, naturalGrassPlacements(tiles, isCovered), assets, getHeight);
    } else {
        for (const key of keys) {
            const [x, z] = key.split(',').map(Number);
            addNaturalGrassInstances(THREE, group,
                naturalGrassPlacements(tiles, isCovered, { x, z }), assets, getHeight);
        }
    }
    const meshes = [...keys].map(key => group.userData.naturalGrassChunks.get(key)?.mesh).filter(Boolean);
    releaseUnusedGrassResources(group);
    return { build: naturalGrassSummary(group, assets), meshes };
}
