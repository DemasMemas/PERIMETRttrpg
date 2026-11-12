import { acceptsDetail, edgePosition } from './locationGrassDetail.js';

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
    const slopeWeight = source.slopeWeight ?? 1;
    const pitch = limit(Math.atan2(localNormalZ, 1)) * slopeWeight + plant.lean;
    const roll = limit(-Math.atan2(localNormalX, Math.hypot(1, localNormalZ))) * slopeWeight + plant.lean * 0.55;
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

function patch(x, z, scale, salt = 81) {
    const ix = Math.floor(x / scale);
    const iz = Math.floor(z / scale);
    const fx = x / scale - ix;
    const fz = z / scale - iz;
    const sx = fx * fx * (3 - 2 * fx);
    const sz = fz * fz * (3 - 2 * fz);
    const a = hash(ix, iz, salt) * (1 - sx) + hash(ix + 1, iz, salt) * sx;
    const b = hash(ix, iz + 1, salt) * (1 - sx) + hash(ix + 1, iz + 1, salt) * sx;
    return a * (1 - sz) + b * sz;
}

function grassCover(x, z) {
    const bendX = (patch(x, z, 4.6, 51) - .5) * 4.0;
    const bendZ = (patch(x, z, 4.6, 52) - .5) * 4.0;
    const broad = patch(x + bendX, z + bendZ, 5.5);
    const detail = patch(x, z, 1.5, 86);
    const t = Math.max(0, Math.min(1, (broad * .82 + detail * .18 - .39) / .23));
    return t * t * (3 - 2 * t);
}

const emptyStats = () => ({ grassTiles: 0, plainTiles: 0, forestTiles: 0,
    densityRejected: 0, blendRejected: 0, floorRejected: 0 });

export function naturalGrassPlacements(tiles, isCovered = () => false, chunk = null) {
    const plants = [];
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
            const candidates = forest ? 5 : 8;
            const fringe = [[1, 0], [-1, 0], [0, 1], [0, -1]].filter(([dx, dz]) => {
                const neighbor = tiles[z + dz]?.[x + dx]?.terrain;
                return neighbor && neighbor !== 'grass' && neighbor !== 'water';
            });
            for (let index = 0; index < candidates; index++) {
                const edge = index === candidates - 1 && fringe.length
                    ? fringe[Math.min(fringe.length - 1,
                        Math.floor(hash(x, z, 105) * fringe.length))] : null;
                const px = edge ? x + (edge[0] > 0 ? 1.02 : edge[0] < 0 ? -0.02
                    : 0.15 + hash(x, z, 210 + index) * 0.7)
                    : index === 0 ? x + 0.22 + hash(x, z, 210) * 0.56
                        : edgePosition(x, hash(x, z, 210 + index), 610 + z * 5 + index);
                const pz = edge ? z + (edge[1] > 0 ? 1.02 : edge[1] < 0 ? -0.02
                    : 0.15 + hash(x, z, 310 + index) * 0.7)
                    : index === 0 ? z + 0.22 + hash(x, z, 310) * 0.56
                        : edgePosition(z, hash(x, z, 310 + index), 710 + x * 5 + index);
                const fullness = grassCover(px, pz);
                const presence = edge ? .18 + fullness * .57 : forest ? .12 + fullness * .55 : .045 + fullness * .81;
                if (hash(x, z, 110 + index) > presence) {
                    count('densityRejected', local);
                    continue;
                }
                if (!acceptsDetail(tiles, 'grass', px, pz, 990 + index)) {
                    count('blendRejected', local);
                    continue;
                }
                // The whole clump must clear the floor, including diagonal
                // leaves at maximum random scale, rotation and slope lean.
                if (isCovered(px, pz, .59)) {
                    count('floorRejected', local);
                    continue;
                }
                if([-.59,0,.59].some(dx=>[-.59,0,.59].some(dz=>['concrete','granite_tiles','metal','wood_floor']
                    .includes(tiles[Math.floor(pz+dz)]?.[Math.floor(px+dx)]?.terrain)))){
                    count('blendRejected',local);
                    continue;
                }
                if (chunk && (Math.floor(px / CHUNK_SIZE) !== chunk.x
                    || Math.floor(pz / CHUNK_SIZE) !== chunk.z)) continue;
                const dryness = patch(px, pz, 7, 97);
                const mix = hash(x, z, 930 + index);
                const kind = mix < (forest ? .14 : .23) * (.20 + fullness * .88)
                    ? 'seeded' : mix > .96 - dryness * (.35 + fullness * .31) ? 'dry' : 'low';
                plants.push({ x: px, z: pz, kind,
                    size: .62 + hash(x, z, 410 + index) * .43 + fullness * .11,
                    width: .85 + hash(x, z, 610 + index) * .25,
                    depth: .85 + hash(x, z, 710 + index) * .25,
                    lean: (hash(x, z, 810 + index) - 0.5) * .19,
                    tone: hash(x, z, 910 + index),
                    turn: hash(x, z, 510 + index) * Math.PI * 2 });
            }
        }
    }
    return { plants, stats, statsByChunk };
}

export function loadNaturalGrassAssets(THREE) {
    if (assetPromise) return assetPromise;
    assetPromise = import('./locationGrassModels.js')
        .then(({ createNaturalGrassAssets }) => createNaturalGrassAssets(THREE))
        .catch(error => {
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

export function addNaturalGrassInstances(THREE, group, placements, assets, getHeight) {
    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    const state = group.userData.naturalGrassChunks ??= new Map();
    const resources = group.userData.naturalGrassResources ??= {};
    group.userData.naturalGrassBaseTufts ??= group.userData.detailCounts.tufts;
    for (const [key, stats] of placements.statsByChunk || []) {
        state.set(key, { stats, created: {}, meshes: [] });
    }
    for (const [kind, source] of Object.entries(assets)) {
        const slopes = new Map();
        const chunks = new Map();
        for (const plant of placements.plants) {
            if (plant.kind !== kind) continue;
            const cx = Math.floor(plant.x / CHUNK_SIZE), cz = Math.floor(plant.z / CHUNK_SIZE);
            const key = `${cx},${cz}`;
            if (!state.has(key)) state.set(key, { stats: emptyStats(), created: {}, meshes: [] });
            const created = state.get(key).created;
            created[kind] = (created[kind] || 0) + 1;
            const center = getHeight(plant.x, plant.z);
            const left = getHeight(plant.x - 0.14, plant.z), right = getHeight(plant.x + 0.14, plant.z);
            const near = getHeight(plant.x, plant.z - 0.14), far = getHeight(plant.x, plant.z + 0.14);
            if (Math.max(Math.abs(center - left), Math.abs(center - right),
                Math.abs(center - near), Math.abs(center - far)) >= 0.18) continue;
            slopes.set(plant, { height: center, x: (right - left) / 0.28, z: (far - near) / 0.28 });
            if (!chunks.has(key)) chunks.set(key, { cx, cz, plants: [] });
            chunks.get(key).plants.push(plant);
        }
        if (!chunks.size) continue;
        if (!resources[kind]) {
            resources[kind] = { geometry: source.geometry.clone(), material: source.material.clone() };
        }
        const { geometry, material } = resources[kind];
        for (const [key, { cx, cz, plants: chunkPlants }] of chunks) {
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
                const tone = .78 + plant.tone * .34;
                color.setRGB(tone, tone * (1.05 - plant.tone * .12), tone * .97);
                mesh.setColorAt(index, color);
            }
            mesh.instanceMatrix.needsUpdate = true;
            mesh.instanceColor.needsUpdate = true;
            // Сфера участка уже учитывает матрицы травинок. Общая геометрия
            // остаётся в своих координатах, иначе Three применит матрицы дважды.
            mesh.boundingSphere = new THREE.Sphere(new THREE.Vector3(
                (bounds[0] + bounds[3]) / 2, (bounds[1] + bounds[4]) / 2,
                (bounds[2] + bounds[5]) / 2), Math.hypot(bounds[3] - bounds[0],
                bounds[4] - bounds[1], bounds[5] - bounds[2]) / 2 + 0.02);
            mesh.userData.naturalGrassKind = kind;
            mesh.userData.naturalGrassChunk = key;
            mesh.frustumCulled = true;
            mesh.castShadow = false;
            mesh.receiveShadow = false;
            mesh.raycast = () => {};
            group.add(mesh);
            state.get(key).meshes.push(mesh);
        }
    }
    return naturalGrassSummary(group, assets);
}

function naturalGrassSummary(group, assets) {
    const stats = emptyStats();
    const zeroCounts = () => Object.fromEntries(Object.keys(assets).map(kind => [kind, 0]));
    const created = zeroCounts(), added = zeroCounts(), chunks = zeroCounts();
    for (const entry of group.userData.naturalGrassChunks.values()) {
        for (const field of Object.keys(stats)) stats[field] += entry.stats[field];
        for (const [kind, count] of Object.entries(entry.created)) created[kind] += count;
        for (const mesh of entry.meshes) {
            added[mesh.userData.naturalGrassKind] += mesh.count;
            chunks[mesh.userData.naturalGrassKind]++;
        }
    }
    group.userData.naturalGrassCounts = added;
    group.userData.detailCounts.tufts = group.userData.naturalGrassBaseTufts
        + Object.values(added).reduce((sum, count) => sum + count, 0);
    return { stats, created, added, chunks,
        slopeRejected: Object.fromEntries(Object.keys(assets).map(kind => [kind, created[kind] - added[kind]])),
        triangles: Object.fromEntries(Object.entries(assets).map(([kind, { geometry }]) => {
            const triangles = (geometry.index ? geometry.index.count
                : geometry.getAttribute('position').count) / 3;
            return [kind, added[kind] * triangles];
        })) };
}

function releaseUnusedGrassResources(group) {
    for (const [kind, resources] of Object.entries(group.userData.naturalGrassResources || {})) {
        if (group.children.some(mesh => mesh.userData.naturalGrassKind === kind)) continue;
        resources.geometry.dispose();
        resources.material.dispose();
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
        for (const previous of group.userData.naturalGrassChunks.get(key)?.meshes || []) {
            group.remove(previous);
            previous.dispose?.();
        }
    }
    // When every chunk is affected (e.g. a small location's shared corner),
    // generate once instead of repeatedly visiting overlapping source halos.
    if (keys.size === Math.ceil(tiles.length / CHUNK_SIZE) * Math.ceil(tiles[0].length / CHUNK_SIZE)) {
        addNaturalGrassInstances(THREE, group, naturalGrassPlacements(tiles, isCovered), assets, getHeight);
    } else {
        for (const key of keys) {
            const [x, z] = key.split(',').map(Number);
            addNaturalGrassInstances(THREE, group,
                naturalGrassPlacements(tiles, isCovered, { x, z }), assets, getHeight);
        }
    }
    const meshes = [...keys].flatMap(key => group.userData.naturalGrassChunks.get(key)?.meshes || []);
    releaseUnusedGrassResources(group);
    return { build: naturalGrassSummary(group, assets), meshes };
}
