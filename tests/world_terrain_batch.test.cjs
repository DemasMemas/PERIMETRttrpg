const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const directory = path.join(__dirname, '../app/static/js');
const source = fs.readFileSync(path.join(directory, 'lobby3d.js'), 'utf8');
const moduleUrl = name => {
    let code = fs.readFileSync(path.join(directory, name), 'utf8');
    if (name === 'locationTerrainTexture.js') {
        code = code.replace("'./locationGrassSubtype.js'", `'${moduleUrl('locationGrassSubtype.js')}'`);
    }
    if (name === 'worldWaterGeometry.js') for (const dependency of ['locationWaterEdge.js','locationTerrain.js','waterGeometryUtils.js']) {
        code = code.replace(`'./${dependency}'`, `'${moduleUrl(dependency)}'`);
    }
    return `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
};

// Run the real queue, geometry and pixel generators; only Three.js and the clock
// are replaced. No rendering or GPU timing is implied by these tests.
async function harness(keys = ['0,0'], size = 32) {
    const terrain = await import(moduleUrl('locationTerrain.js'));
    const pixels = await import(moduleUrl('locationTerrainTexture.js'));
    const water = await import(moduleUrl('locationWaterEdge.js'));
    const topology = await import(moduleUrl('worldRoadTopology.js'));
    const worldWater = await import(moduleUrl('worldWaterGeometry.js'));
    const chunksMap = new Map();
    const timers = new Map();
    const builds = new Map();
    const generated = new Map();
    const buildRegions = [];
    let timerId = 0;
    class Object3D {
        constructor() {
            this.matrix = {};
            this.position = { set: (...values) => { this.matrix.position = values; } };
            this.scale = { set: (...values) => { this.matrix.scale = values; } };
        }
        updateMatrix() {}
    }
    class Geometry {
        constructor() { this.attributes = {}; }
        setAttribute(name, attribute) { this.attributes[name] = attribute; }
        setIndex(index) { this.index = index; }
        getAttribute(name) { return this.attributes[name]; }
        computeVertexNormals() {}
        dispose() { this.disposed = true; }
    }
    class Material {
        constructor(options) { Object.assign(this, options); }
        dispose() { this.disposed = true; }
    }
    const mesh = () => ({
        instanceMatrix: {}, instanceColor: {}, matrices: [],
        setMatrixAt(index, matrix) { this.matrices[index] = structuredClone(matrix); },
        setColorAt() {},
    });
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        window: {},
        CHUNK_SIZE: size, chunksMap, chunkBounds: [], performance,
        scene: { add() {}, remove() {} },
        terrainColors: { grass: 0x3a5f0b, sand: 0xc2b280, rock: 0x808080, water: 0x1e90ff },
        smoothGroundMat: {}, waterMat: {}, worldWaterBankMat: {},
        raycastSmoothTerrain() {},
        worldGrassGroundPixelsEnabled: false, worldGrassDetail: { apply() {} },
        ...pixels, ...water, ...worldWater, ...topology, isSmoothTerrainTile: terrain.isSmoothTerrainTile,
        // These CPU comparisons inspect expanded water vertices, as before.
        indexWaterGeometry() {},
        buildSmoothTerrainData(padded, ...args) {
            const key = padded[1][1].chunkKey;
            let included = 0;
            for (let z = 0; z < padded.length; z++) for (let x = 0; x < padded[z].length; x++) {
                if (args[0](x, z)) included++;
            }
            buildRegions.push({ key, included });
            builds.set(key, (builds.get(key) || 0) + 1);
            const result = terrain.buildSmoothTerrainData(padded, ...args);
            generated.set(key, { padded, result });
            return result;
        },
        THREE: {
            Object3D, BufferGeometry: Geometry, MeshStandardMaterial: Material,
            Float32BufferAttribute: class { constructor(array) { this.array = new Float32Array(array); } },
            Color: class {
                constructor(hex) { this.setHex(hex || 0); }
                setHex(hex) { this.r = (hex >> 16 & 255) / 255; this.g = (hex >> 8 & 255) / 255; this.b = (hex & 255) / 255; }
            },
            DataTexture: class {
                constructor(data, width, height) { this.image = { data, width, height }; }
                dispose() { this.disposed = true; }
            },
            Mesh: class extends Object3D {
                constructor(geometry, material) { super(); this.geometry = geometry; this.material = material; }
            },
        },
        setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
        clearTimeout(id) { timers.delete(id); },
        brushActive: false, isMouseDown: false, lastProcessedTileKey: null,
        controlsDisabled: false, globalMouseUpHandler: null,
        lastHoverRaycastAt: 0, lastChunkVisibilityAt: 0,
        markWorldShadowsDirty() {}, updateTileObjectsPositions() {}, refreshWorldFenceNeighborhood() {}, invalidateFenceGround() {},
        rebuildRoadMeshes() {}, refreshRoadNeighborhood() {},
    });
    // Bridge-span invalidation reads tiles through the normal world lookup.
    const tileLookup = source.slice(source.indexOf('function loadedWorldTile('),
        source.indexOf('\nfunction disposeRoadMeshes('));
    vm.runInContext(tileLookup, context);
    const start = source.indexOf('function refreshWorldTerrainSurface(');
    const end = source.indexOf('\n// --- Функции для чанков ---', start);
    const updateStart = source.indexOf('export function updateTileInChunk(');
    const updateEnd = source.indexOf('\nexport function setBrushRadius(', updateStart);
    const finishStart = source.indexOf('function finishWorldBrushStroke(');
    const finishEnd = source.indexOf('\nfunction canvasMouseDownHandler(', finishStart);
    vm.runInContext(source.slice(start, end)
        + source.slice(updateStart, updateEnd).replace('export ', '')
        + source.slice(finishStart, finishEnd), context);
    for (const key of keys) {
        const [chunkX, chunkY] = key.split(',').map(Number);
        const entry = { chunkX, chunkY, terrainVisible: true, ground: mesh(), water: mesh(),
            bounds: { max: { y: 1.5 } },
            tilesData: Array.from({ length: size }, () => Array.from({ length: size }, () =>
                ({ terrain: 'grass', height: 1, chunkKey: key }))),
        };
        chunksMap.set(key, entry);
        Object.assign(entry, context.fillChunkTerrainInstances(entry.ground, entry.water,
            entry.tilesData, chunkX, chunkY));
    }
    return {
        context, chunksMap, builds, generated, buildRegions, pixels, terrain,
        edit(x, z, updates) {
            context.updateTileInChunk(Math.floor(x / size), Math.floor(z / size), x % size, z % size, updates);
        },
        flush() {
            const callbacks = [...timers.values()]; timers.clear();
            callbacks.forEach(callback => callback());
        },
        paint(left, top, kind) {
            for (let z = top; z < top + 3; z++) {
                for (let x = left; x < left + 3; x++) this.edit(x, z, { terrain: kind });
            }
        },
    };
}

function assertLiveHeightGeometry(h, entry) {
    const size = entry.tilesData.length;
    const padded = Array.from({ length: size + 2 }, (_, z) => Array.from({ length: size + 2 }, (_, x) => {
        const wx = entry.chunkX * size + x - 1, wz = entry.chunkY * size + z - 1;
        const cx = Math.floor(wx / size), cy = Math.floor(wz / size);
        return h.chunksMap.get(`${cx},${cy}`)?.tilesData[wz - cy * size]?.[wx - cx * size] || null;
    }));
    const expected = h.context.clipWorldTerrainData(h.terrain.buildSmoothTerrainData(padded),
        (x, z) => x > 0 && x <= size && z > 0 && z <= size);
    const triangles = data => {
        const byTile = new Map();
        data.tileByFace.forEach((tile, face) => {
            const key = `${tile.x},${tile.z}`;
            if (!byTile.has(key)) byTile.set(key, []);
            for (let v = face * 3; v < face * 3 + 3; v++) byTile.get(key).push(...new Float32Array([
                ...data.positions.slice(v * 3, v * 3 + 3),
                ...data.normals.slice(v * 3, v * 3 + 3), data.topByVertex[v]]));
        });
        return [...byTile].sort(([a], [b]) => a.localeCompare(b));
    };
    assert.deepEqual(triangles(entry.smoothTerrainData), triangles(expected));
    for (let z = 0; z < size; z++) for (let x = 0; x < size; x++) {
        const index = entry.groundIndices[z * size + x];
        if (index < 0) continue;
        const foundation = expected.foundationByTile.get(`${x + 1},${z + 1}`);
        const height = foundation === undefined ? entry.tilesData[z][x].height : Math.max(0.05, foundation - 0.085);
        assert.equal(entry.ground.matrices[index].scale[1], height);
        assert.equal(entry.ground.matrices[index].position[1], height / 2);
    }
    assert.equal(entry.terrainPreviewRegion, null);
    const attributes = entry.terrainSurface?.geometry.attributes;
    if (attributes) {
        const positions = attributes.position.array, uvs = attributes.uv.array;
        for (let vertex = 0; vertex < positions.length / 3; vertex++) {
            assert.ok(Math.abs(uvs[vertex * 2]
                - (positions[vertex * 3] - entry.chunkX * size + 2) * 12 / 512) < 1e-6);
            assert.ok(Math.abs(uvs[vertex * 2 + 1]
                - (positions[vertex * 3 + 2] - entry.chunkY * size + 2) * 12 / 512) < 1e-6);
        }
    }
    return padded;
}

// Compare against the previous invalidation rule using the same real generators
// and queue. Counters cover water resources only, not terrain or GPU timings.
async function waterHarness(keys = ['0,0'], waterTiles = [[11, 11]], legacy = false) {
    const h = await harness(keys);
    if (legacy) {
        const start = source.indexOf('export function updateTileInChunk(');
        const end = source.indexOf('\nexport function setBrushRadius(', start);
        vm.runInContext(source.slice(start, end).replace('export ', '')
            .replace('const waterNearby = wasWater || isWater ||', 'const waterNearby = changedTerrain ||'), h.context);
    }
    for (const [x, z] of waterTiles) {
        h.chunksMap.get(`${Math.floor(x / 32)},${Math.floor(z / 32)}`).tilesData[z % 32][x % 32].terrain = 'water';
    }
    for (const entry of h.chunksMap.values()) {
        Object.assign(entry, h.context.fillChunkTerrainInstances(entry.ground, entry.water,
            entry.tilesData, entry.chunkX, entry.chunkY));
        entry.waterShoreDirty = true;
        h.context.refreshWorldTerrainChunk(entry);
    }
    const counts = { refreshes: 0, generations: 0, meshes: 0, disposed: 0 };
    const generate = h.context.buildWorldWaterData;
    h.context.buildWorldWaterData = (...args) => {
        counts.generations++;
        return generate(...args);
    };
    const refresh = h.context.refreshWorldWaterChunk;
    h.context.refreshWorldWaterChunk = (entry, ...args) => {
        counts.refreshes++;
        const old = [entry.waterSurface, entry.waterBank];
        refresh(entry, ...args);
        counts.disposed += old.filter(mesh => mesh?.geometry.disposed).length;
        counts.meshes += [entry.waterSurface, entry.waterBank].filter((mesh, i) => mesh && mesh !== old[i]).length;
    };
    return Object.assign(h, { counts });
}

function assertWaterEquivalent(actual, previous) {
    for (const [key, entry] of actual.chunksMap) {
        const reference = previous.chunksMap.get(key);
        for (const field of ['waterSurface', 'waterBank']) {
            assert.equal(Boolean(entry[field]), Boolean(reference[field]), `${key} ${field}`);
            if (!entry[field]) continue;
            const mesh = entry[field];
            for (const name of ['position', 'color', 'uv']) {
                assert.deepEqual(mesh.geometry.attributes[name]?.array,
                    reference[field].geometry.attributes[name]?.array, `${key} ${field} ${name}`);
            }
            assert.equal(mesh.material, field === 'waterSurface' ? actual.context.waterMat : actual.context.worldWaterBankMat);
            assert.equal(Boolean(mesh.geometry.disposed), false);
        }
        assert.deepEqual(entry.water.matrices, reference.water.matrices);
    }
}

for (const [name, keys, x, z] of [
    ['inside a chunk', ['0,0'], 20, 20],
    ['at a two-chunk boundary', ['0,0', '1,0'], 31, 20],
    ['at a four-chunk corner', ['0,0', '1,0', '0,1', '1,1'], 31, 31],
]) {
    test(`dry terrain far from water skips water resources ${name}`, async () => {
        const pools = keys.flatMap(key => {
            const [cx, cz] = key.split(',').map(Number);
            return Array.from({ length: 16 }, (_, i) => [cx * 32 + 10 + i % 4, cz * 32 + 10 + Math.floor(i / 4)]);
        });
        const h = await waterHarness(keys, pools);
        const previous = await waterHarness(keys, pools, true);
        const meshes = [...h.chunksMap.values()].flatMap(entry => [entry.waterSurface, entry.waterBank]);
        for (const run of [h, previous]) {
            run.context.brushActive = true;
            run.edit(x, z, { terrain: 'sand' });
            run.context.refreshWorldTerrainBrushPreview();
            run.context.finishWorldBrushStroke(); run.flush();
        }
        assert.deepEqual(h.counts, { refreshes: 0, generations: 0, meshes: 0, disposed: 0 });
        assert.deepEqual(previous.counts, {
            refreshes: keys.length, generations: keys.length, meshes: keys.length*2, disposed: keys.length*2,
        });
        assert.deepEqual([...h.chunksMap.values()].flatMap(entry => [entry.waterSurface, entry.waterBank]), meshes);
        assertWaterEquivalent(h, previous);
        assertLiveHeightGeometry(h, h.chunksMap.get('0,0'));
    });
}

test('shore soil, land heights, water heights and isolated water removal match previous geometry', async () => {
    const h = await waterHarness();
    const previous = await waterHarness(['0,0'], [[11, 11]], true);
    for (const run of [h, previous]) run.context.brushActive = true;
    for (const [x, z, update] of [
        [10, 11, { terrain: 'sand' }], [10, 11, { height: 4 }],
        [10, 10, { height: 3 }], [11, 11, { height: 5 }],
        [11, 11, { terrain: 'grass' }], [11, 11, { terrain: 'water' }],
    ]) {
        for (const run of [h, previous]) {
            run.edit(x, z, update);
            assert.equal(run.chunksMap.get('0,0').waterShoreDirty, true);
            run.context.refreshWorldTerrainBrushPreview();
        }
        assertWaterEquivalent(h, previous);
        assert.deepEqual(h.counts, previous.counts);
    }
    for (const run of [h, previous]) { run.context.finishWorldBrushStroke(); run.flush(); }
    assertWaterEquivalent(h, previous);
    assert.deepEqual(h.counts, { refreshes: 6, generations: 5, meshes: 10, disposed: 10 });
});

test('connected shoreline crosses the 55 percent threshold without changing previous classification', async () => {
    const tiles = [[10, 10], [11, 10], [12, 10]];
    const h = await waterHarness(['0,0'], tiles);
    const previous = await waterHarness(['0,0'], tiles, true);
    for (const run of [h, previous]) {
        for (const [x, z] of [[10, 9], [11, 9], [12, 9], [10, 11]]) run.edit(x, z, { terrain: 'sand' });
        run.flush();
        run.context.brushActive = true;
    }
    for (const kind of ['sand', 'grass']) {
        for (const run of [h, previous]) {
            run.edit(11, 11, { terrain: kind });
            run.context.refreshWorldTerrainBrushPreview();
            const regions = run.context.classifyWaterRegions(run.chunksMap.get('0,0').tilesData);
            assert.deepEqual([...regions.values()], [kind, kind, kind]);
        }
        assertWaterEquivalent(h, previous);
    }
    for (const run of [h, previous]) { run.context.finishWorldBrushStroke(); run.flush(); }
    assert.deepEqual(h.counts, previous.counts);
    assertWaterEquivalent(h, previous);
});

test('diagonal and cross-chunk connected shores keep the padded-ring dependencies', async () => {
    const keys = ['0,0', '1,0', '0,1', '1,1'];
    const tiles = [[32, 32], [33, 32], [33, 31], [33, 30]];
    const h = await waterHarness(keys, tiles);
    const previous = await waterHarness(keys, tiles, true);
    for (const run of [h, previous]) run.context.brushActive = true;
    for (const [x, z, update] of [
        [31, 31, { terrain: 'sand' }], [31, 32, { height: 4 }],
        [32, 31, { terrain: 'swamp' }], [33, 31, { height: 3 }],
    ]) {
        for (const run of [h, previous]) {
            run.edit(x, z, update);
            run.context.refreshWorldTerrainBrushPreview();
        }
        assertWaterEquivalent(h, previous);
        assert.deepEqual(h.counts, previous.counts);
    }
    for (const run of [h, previous]) { run.context.finishWorldBrushStroke(); run.flush(); }
    assertWaterEquivalent(h, previous);
});

test('dry events cannot clear queued shore changes and multiple edits coalesce in a stroke', async () => {
    const h = await waterHarness();
    const previous = await waterHarness(['0,0'], [[11, 11]], true);
    for (const run of [h, previous]) {
        run.context.brushActive = true;
        run.edit(10, 11, { terrain: 'sand' });
        run.edit(11, 10, { terrain: 'rock' });
        run.edit(20, 20, { terrain: 'sand' });
        assert.equal(run.chunksMap.get('0,0').waterShoreDirty, true);
        run.context.refreshWorldTerrainBrushPreview();
        assert.equal(run.counts.generations, 1);
        run.edit(21, 20, { terrain: 'sand' });
        run.context.refreshWorldTerrainBrushPreview();
        run.context.finishWorldBrushStroke(); run.flush();
    }
    assert.equal(h.counts.generations, 1);
    assert.equal(previous.counts.generations, 2);
    assertWaterEquivalent(h, previous);
    assertLiveHeightGeometry(h, h.chunksMap.get('0,0'));
});

test('loading a neighbor after a dry boundary edit still refreshes water and shores', async () => {
    const keys = ['0,0', '1,0'];
    const tiles = [[31, 10], [32, 10], [33, 10]];
    const h = await waterHarness(keys, tiles);
    const previous = await waterHarness(keys, tiles, true);
    for (const run of [h, previous]) {
        const neighbor = run.chunksMap.get('1,0');
        run.chunksMap.delete('1,0');
        run.context.refreshWorldTerrainNeighborhood(0, 0, null, null, false, false, true, true);
        run.edit(31, 20, { terrain: 'sand' }); run.flush();
        const before = run.counts.generations;
        run.chunksMap.set('1,0', neighbor);
        // addChunk's existing load invalidation (without unrelated object creation).
        run.context.refreshWorldTerrainNeighborhood(1, 0, null, null, true, true, true, true, true);
        run.flush();
        assert.equal(run.counts.generations - before, 2);
    }
    assertWaterEquivalent(h, previous);
});

test('standalone terrain events flush without a brush and retain necessary shore updates', async () => {
    const h = await waterHarness();
    const previous = await waterHarness(['0,0'], [[11, 11]], true);
    for (const [x, z, update, total] of [
        [20, 20, { terrain: 'sand' }, 0],
        [20, 20, { height: 4 }, 0],
        [10, 11, { terrain: 'sand' }, 1],
        [11, 11, { height: 3 }, 2],
    ]) {
        for (const run of [h, previous]) { run.edit(x, z, update); run.flush(); }
        assert.equal(h.counts.generations, total);
        assertWaterEquivalent(h, previous);
    }
    assert.equal(previous.counts.generations, 3);
});

test('height preview raises and lowers mixed soils with correct normals and foundations without repainting', async () => {
    const h = await harness();
    const entry = h.chunksMap.get('0,0');
    entry.tilesData[10][10].terrain = 'sand';
    entry.tilesData[10][11].terrain = 'rock';
    h.context.refreshWorldTerrainChunk(entry);
    h.buildRegions.length = 0;
    const texture = entry.terrainTexture;
    const pixels = texture.image.data.slice();
    const material = entry.terrainSurface.material;
    h.context.updateTerrainPixelsRegion = () => { throw new Error('height repainted terrain pixels'); };
    h.context.brushActive = true;
    for (const height of [5, 2, 7, 1]) {
        h.edit(10, 10, { height });
        assert.equal(entry.terrainTextureRegion, null);
        assert.ok(entry.terrainPreviewRegion);
        h.context.refreshWorldTerrainBrushPreview();
        assertLiveHeightGeometry(h, entry);
        assert.equal(entry.terrainTexture, texture);
        assert.equal(entry.terrainSurface.material, material);
        assert.deepEqual(texture.image.data, pixels);
    }
    assert.ok(h.buildRegions.every(region => region.included < 1024));
    const previews = h.buildRegions.length;
    h.context.refreshWorldTerrainBrushPreview();
    assert.equal(h.buildRegions.length, previews);
    h.context.finishWorldBrushStroke(); h.flush();
    assertLiveHeightGeometry(h, entry);
    assert.equal(h.buildRegions.filter(region => region.included === 1156).length, 1);
});

test('fast 3x3 height stroke coalesces live geometry and roads while retaining object and shadow updates', async () => {
    const h = await harness();
    const entry = h.chunksMap.get('0,0');
    h.context.refreshWorldTerrainChunk(entry);
    h.buildRegions.length = 0;
    entry.hasRoadNodes = true;
    const roads = [], objects = [];
    let shadows = 0;
    h.context.rebuildRoadMeshes = target => roads.push(target);
    h.context.updateTileObjectsPositions = (...args) => objects.push(args);
    h.context.markWorldShadowsDirty = () => { shadows++; };
    h.context.brushActive = true;
    for (let z = 10; z < 13; z++) for (let x = 10; x < 13; x++) h.edit(x, z, { height: 4 });
    assert.equal(h.buildRegions.length, 0);
    h.context.refreshWorldTerrainBrushPreview();
    assert.equal(h.buildRegions.length, 1);
    assert.equal(h.buildRegions[0].included, 81);
    assertLiveHeightGeometry(h, entry);
    assert.deepEqual(roads, [entry]);
    assert.equal(objects.length, 9);
    assert.equal(shadows, 9);
    h.edit(16, 10, { height: 6 });
    h.context.refreshWorldTerrainBrushPreview();
    assertLiveHeightGeometry(h, entry);
    assert.deepEqual(roads, [entry, entry]);
    h.context.finishWorldBrushStroke(); h.flush();
    assert.equal(roads.length, 2);
    assert.equal(h.buildRegions.filter(region => region.included === 1156).length, 1);
});

for (const [label, keys, x, z] of [
    ['edge', ['0,0', '1,0'], 31, 10],
    ['corner', ['0,0', '1,0', '0,1', '1,1'], 31, 31],
]) {
    test(`height preview keeps ${label} slopes consistent before release`, async () => {
        const h = await harness(keys);
        for (const entry of h.chunksMap.values()) h.context.refreshWorldTerrainChunk(entry);
        h.buildRegions.length = 0;
        h.context.brushActive = true;
        h.edit(x, z, { height: 5 });
        h.context.refreshWorldTerrainBrushPreview();
        for (const entry of h.chunksMap.values()) assertLiveHeightGeometry(h, entry);
        assert.equal(h.buildRegions.length, keys.length);
        assert.ok(h.buildRegions.every(region => region.included < 1024));
        h.context.finishWorldBrushStroke(); h.flush();
        for (const entry of h.chunksMap.values()) {
            assertLiveHeightGeometry(h, entry);
            assert.equal(h.buildRegions.filter(region => region.key === `${entry.chunkX},${entry.chunkY}`
                && region.included === 1156).length, 1);
        }
    });
}

test('height preview refreshes water levels and the shore including diagonal land heights', async () => {
    const h = await harness();
    const entry = h.chunksMap.get('0,0');
    h.edit(11, 11, { terrain: 'water' }); h.flush();
    h.buildRegions.length = 0;
    h.context.brushActive = true;
    for (const [x, z, height] of [[10, 10, 5], [11, 11, 3], [10, 11, 2]]) {
        const oldBank = entry.waterBank?.geometry;
        h.edit(x, z, { height });
        assert.equal(entry.waterShoreDirty, true);
        h.context.refreshWorldTerrainBrushPreview();
        if(oldBank)assert.equal(oldBank.disposed, true);
        const padded = assertLiveHeightGeometry(h, entry);
        const waterTiles=Array.from({length:36},(_,z)=>Array.from({length:36},(_,x)=>h.context.loadedWorldTile(x-2,z-2)||null));
        const expected = h.context.buildWorldWaterData(waterTiles,
            (x, z) => x >= 2 && x < 34 && z >= 2 && z < 34,-2,-2);
        assert.deepEqual(entry.waterSurface.geometry.attributes.position.array, new Float32Array([...expected.water,...expected.sides]));
        assert.deepEqual(entry.waterBank.geometry.attributes.position.array, new Float32Array(expected.bankPositions));
        assert.equal(entry.waterShoreDirty, false);
        const level = entry.tilesData[11][11].height - 0.01;
        assert.equal(entry.water.matrices[entry.waterIndices[11 * 32 + 11]].position[1], level / 2);
    }
    h.context.finishWorldBrushStroke(); h.flush();
    assert.equal(h.buildRegions.filter(region => region.included === 1156).length, 1);
});

test('single terrain edit completes on its timer and resolves existing refresh waiters', async () => {
    const h = await harness();
    h.edit(10, 10, { terrain: 'sand' });
    assert.equal(h.builds.size, 0);
    let done = false;
    const ready = h.context.waitForWorldTerrainRefresh().then(() => { done = true; });
    await Promise.resolve();
    assert.equal(done, false);
    h.flush();
    await ready;
    assert.equal(h.builds.get('0,0'), 1);
    h.edit(10, 10, { terrain: 'sand' }); // Same REST/Socket.IO result, either arrival order.
    h.flush();
    assert.equal(h.builds.get('0,0'), 1);
});

test('active stroke updates pixels before release, once per frame, with one final full build', async () => {
    const h = await harness();
    const entry = h.chunksMap.get('0,0');
    h.context.refreshWorldTerrainChunk(entry);
    h.buildRegions.length = 0;
    const texture = entry.terrainTexture;
    const oldPixels = texture.image.data.slice();
    h.context.brushActive = true;
    h.paint(10, 10, 'sand');
    assert.deepEqual(texture.image.data, oldPixels);
    h.context.refreshWorldTerrainBrushPreview();
    assert.equal(entry.terrainTexture, texture);
    assert.notDeepEqual(texture.image.data, oldPixels);
    assert.equal(entry.terrainTextureRegion, null);
    assert.equal(h.buildRegions.length, 1);
    assert.equal(h.buildRegions[0].included, 81);
    h.context.refreshWorldTerrainBrushPreview();
    assert.equal(h.buildRegions.length, 1);
    h.edit(14, 10, { terrain: 'rock' });
    h.context.refreshWorldTerrainBrushPreview();
    assert.equal(h.buildRegions.length, 2);
    assert.ok(h.buildRegions.every(region => region.included < 1024));
    h.context.finishWorldBrushStroke();
    h.flush();
    assert.equal(h.buildRegions.filter(region => region.included === 1156).length, 1);
    const expected = h.pixels.createTerrainPixelsRegion((x, z) => entry.tilesData
        [Math.max(0, Math.min(31, z))][Math.max(0, Math.min(31, x))].terrain,
        -2, -2, 512, 12, false, null, true);
    assert.deepEqual(texture.image.data, expected.data);
});

test('live sand and water edits replace stale slopes, land caps and shores before release', async () => {
    const h = await harness();
    const entry = h.chunksMap.get('0,0');
    entry.tilesData[10][10].height = 5;
    h.context.refreshWorldTerrainChunk(entry);
    h.buildRegions.length = 0;
    h.context.brushActive = true;
    const triangles = data => {
        const byTile = new Map();
        data.tileByFace.forEach((tile, face) => {
            const key = `${tile.x},${tile.z}`;
            if (!byTile.has(key)) byTile.set(key, []);
            for (let v = face * 3; v < face * 3 + 3; v++) {
                byTile.get(key).push(...new Float32Array([
                    ...data.positions.slice(v * 3, v * 3 + 3),
                    ...data.normals.slice(v * 3, v * 3 + 3), data.topByVertex[v]]));
            }
        });
        return [...byTile].sort(([a], [b]) => a.localeCompare(b));
    };
    for (const kind of ['sand', 'water', 'grass']) {
        h.edit(10, 10, { terrain: kind });
        const oldGeometry = entry.terrainSurface.geometry;
        h.context.refreshWorldTerrainBrushPreview();
        assert.equal(oldGeometry.disposed, true);
        const { padded } = h.generated.get('0,0');
        const expected = h.context.clipWorldTerrainData(h.terrain.buildSmoothTerrainData(padded),
            (x, z) => x > 0 && x <= 32 && z > 0 && z <= 32);
        assert.deepEqual(triangles(entry.smoothTerrainData), triangles(expected));
        assert.equal(entry.water.count, kind === 'water' ? 1 : 0);
        assert.equal(Boolean(entry.waterSurface), kind === 'water');
        assert.equal(Boolean(entry.waterBank), kind === 'water');
        assert.equal(entry.smoothTileIndices.has(10 * 32 + 10), kind !== 'water');
        if (kind === 'water') assert.equal(entry.groundIndices[10 * 32 + 10], -1);
    }
    assert.ok(h.buildRegions.every(region => region.included < 1024));
    h.context.finishWorldBrushStroke(); h.flush();
    assert.equal(h.buildRegions.filter(region => region.included === 1156).length, 1);
});

test('live preview refreshes both sides of chunk corners without consuming the final queue', async () => {
    const keys = ['0,0', '1,0', '0,1', '1,1'];
    const h = await harness(keys);
    for (const entry of h.chunksMap.values()) h.context.refreshWorldTerrainChunk(entry);
    h.buildRegions.length = 0;
    h.context.brushActive = true;
    h.paint(30, 30, 'water');
    h.context.refreshWorldTerrainBrushPreview();
    for (const entry of h.chunksMap.values()) {
        assert.equal(entry.terrainTextureRegion, null);
        assert.ok(entry.waterSurface);
        assert.ok(entry.waterBank,'live preview includes the rounded shore');
        const p=entry.waterBank.geometry.attributes.position.array;
        for(let i=1;i<p.length;i+=3)assert.ok(p[i]>=1-1e-6&&p[i]<=1.012+1e-6,'level shore has no depression');
    }
    assert.equal(h.buildRegions.length, 4);
    assert.ok(h.buildRegions.every(region => region.included < 1024));
    h.context.finishWorldBrushStroke(); h.flush();
    for (const key of keys) assert.equal(h.buildRegions
        .filter(region => region.key === key && region.included === 1156).length, 1);
});

test('live preview can create and release a land texture in a previously water-only chunk', async () => {
    const h = await harness(['0,0'], 4);
    const entry = h.chunksMap.get('0,0');
    for (let z = 0; z < 4; z++) for (let x = 0; x < 4; x++) h.edit(x, z, { terrain: 'water' });
    h.flush();
    assert.equal(entry.terrainTexture, null);
    h.context.brushActive = true;
    h.edit(1, 1, { terrain: 'sand' });
    h.context.refreshWorldTerrainBrushPreview();
    const texture = entry.terrainTexture;
    assert.ok(texture);
    assert.ok(entry.terrainSurface);
    assert.equal(entry.terrainSurface.material.map, texture);
    h.edit(1, 1, { terrain: 'water' });
    h.context.refreshWorldTerrainBrushPreview();
    assert.equal(texture.disposed, true);
    assert.equal(entry.terrainTexture, null);
    assert.equal(entry.terrainSurface, null);
    assert.ok(entry.waterSurface);
    h.context.finishWorldBrushStroke(); h.flush();
    assert.equal(entry.water.count, 16);
});

test('3x3 terrain strokes rebuild once per completed packet and retain rapid later edits', async () => {
    const h = await harness();
    h.context.brushActive = true;
    h.paint(10, 10, 'sand');
    h.flush();
    assert.equal(h.builds.size, 0);
    h.context.finishWorldBrushStroke();
    h.flush();
    assert.equal(h.builds.get('0,0'), 1);
    h.paint(10, 10, 'sand'); // Echo of the first packet does not rebuild.
    h.flush();
    assert.equal(h.builds.get('0,0'), 1);
    h.context.brushActive = true;
    h.paint(10, 10, 'rock');
    h.context.finishWorldBrushStroke();
    h.edit(10, 10, { terrain: 'grass' }); // Arrives before the release timer executes.
    h.flush();
    assert.equal(h.builds.get('0,0'), 2);
    assert.equal(h.chunksMap.get('0,0').tilesData[10][10].terrain, 'grass');
    assert.equal(h.chunksMap.get('0,0').tilesData[12][12].terrain, 'rock');
    h.flush();
    assert.equal(h.builds.get('0,0'), 2);
});

for (const [label, keys, left, top] of [
    ['edge', ['0,0', '1,0'], 30, 10],
    ['corner', ['0,0', '1,0', '0,1', '1,1'], 30, 30],
]) {
    test(`3x3 ${label} packet rebuilds each affected chunk exactly once`, async () => {
        const h = await harness([...keys, '3,3']);
        for (const key of keys) h.context.refreshWorldTerrainChunk(h.chunksMap.get(key));
        h.builds.clear();
        h.paint(left, top, 'sand');
        h.flush();
        assert.equal(h.builds.size, keys.length);
        for (const key of keys) assert.equal(h.builds.get(key), 1);
        for (const key of keys) {
            const entry = h.chunksMap.get(key);
            const { padded } = h.generated.get(key);
            const sample = (x, z) => {
                const cx = Math.floor(x / 32), cy = Math.floor(z / 32);
                return h.chunksMap.get(`${cx},${cy}`)?.tilesData[z - cy * 32]?.[x - cx * 32]?.terrain
                    || entry.tilesData[Math.max(0, Math.min(31, z - entry.chunkY * 32))]
                        [Math.max(0, Math.min(31, x - entry.chunkX * 32))].terrain;
            };
            for (let z = 0; z < 34; z++) for (let x = 0; x < 34; x++) {
                const wx = entry.chunkX * 32 + x - 1, wz = entry.chunkY * 32 + z - 1;
                const cx = Math.floor(wx / 32), cy = Math.floor(wz / 32);
                assert.equal(padded[z][x], h.chunksMap.get(`${cx},${cy}`)?.tilesData[wz - cy * 32]?.[wx - cx * 32] || null);
            }
            const expected = h.pixels.createTerrainPixelsRegion(sample,
                entry.chunkX * 32 - 2, entry.chunkY * 32 - 2, 512, 12, false, null, true);
            assert.deepEqual(entry.terrainTexture.image.data, expected.data);
        }
    });
}

test('batched steep sand retains generated positions, normals, foundations and picking bounds', async () => {
    const h = await harness(['0,0'], 4);
    const entry = h.chunksMap.get('0,0');
    entry.tilesData[1][1].height = 5;
    h.edit(1, 1, { terrain: 'sand' });
    h.edit(2, 1, { terrain: 'sand' });
    h.flush();
    assert.equal(h.builds.get('0,0'), 1);
    const { padded, result } = h.generated.get('0,0');
    const expected = h.terrain.buildSmoothTerrainData(padded, (x, z) => x > 0 && x <= 4 && z > 0 && z <= 4);
    const grass = padded.map(row => row.map(tile => tile ? { ...tile, terrain: 'grass' } : null));
    assert.ok(expected.positions.length > h.terrain.buildSmoothTerrainData(grass,
        (x, z) => x > 0 && x <= 4 && z > 0 && z <= 4).positions.length);
    assert.deepEqual(Array.from(entry.smoothTerrain.geometry.attributes.position.array), Array.from(new Float32Array(expected.positions)));
    assert.deepEqual(Array.from(entry.smoothTerrain.geometry.attributes.normal.array), Array.from(new Float32Array(expected.normals)));
    const rendered = entry.smoothTerrain.geometry.index;
    assert.ok(rendered.length < expected.positions.length / 3, 'covered land tops are not submitted twice');
    assert.ok(rendered.every(index => expected.topByVertex[index] < 0.5), 'all exposed side triangles remain in the original attributes');
    assert.ok(result.positions.length > 0);
    for (const [index, foundation] of entry.smoothTileFoundation) {
        const x = index % 4, z = Math.floor(index / 4);
        assert.equal(foundation, Math.max(0.05, expected.foundationByTile.get(`${x + 1},${z + 1}`) - 0.085));
        assert.equal(entry.ground.matrices[entry.groundIndices[index]].position[1], foundation / 2);
    }
    assert.equal(entry.smoothTerrainBounds.mesh, entry.smoothTerrain);
    assert.equal(entry.terrainSurface.receiveShadow, true);
});

test('batched pixels preserve the texture and equal a fresh full image including grass alpha', async () => {
    const h = await harness();
    const entry = h.chunksMap.get('0,0');
    h.context.refreshWorldTerrainChunk(entry);
    h.builds.clear();
    const texture = entry.terrainTexture;
    const mesh = entry.terrainSurface;
    const geometry = mesh.geometry;
    h.paint(10, 10, 'sand');
    h.edit(20, 20, { terrain: 'rock' });
    h.edit(11, 11, { height: 3 }); // A later height update must retain the pending texture region.
    assert.deepEqual(JSON.parse(JSON.stringify(entry.terrainTextureRegion)), { minX: 10, maxX: 20, minZ: 10, maxZ: 20 });
    h.flush();
    assert.equal(h.builds.get('0,0'), 1);
    assert.equal(entry.terrainTexture, texture);
    assert.equal(entry.terrainSurface, mesh);
    assert.equal(geometry.disposed, true);
    assert.equal(entry.terrainTextureRegion, null);
    const sample = (x, z) => entry.tilesData[Math.max(0, Math.min(31, z))][Math.max(0, Math.min(31, x))].terrain;
    const expected = h.pixels.createTerrainPixelsRegion(sample, -2, -2, 512, 12, false, null, true);
    assert.deepEqual(texture.image.data, expected.data);
});

test('land-water-land packets retain compact indices, water sides and final land surface', async () => {
    const h = await harness(['0,0'], 4);
    const entry = h.chunksMap.get('0,0');
    for (const kind of ['water', 'sand']) {
        for (let z = 0; z < 4; z++) for (let x = 0; x < 4; x++) h.edit(x, z, { terrain: kind });
        h.flush();
        assert.equal(entry.ground.count, kind === 'water' ? 0 : 16);
        assert.equal(entry.water.count, kind === 'water' ? 16 : 0);
        assert.equal(Boolean(entry.terrainSurface), kind !== 'water');
        assert.equal(Boolean(entry.terrainTexture), kind !== 'water');
        assert.equal(Boolean(entry.waterSurface), kind === 'water');
        assert.ok(Array.from(kind === 'water' ? entry.waterIndices : entry.groundIndices).every(index => index >= 0));
    }
    assert.equal(h.builds.get('0,0'), 2);
    h.edit(1, 1, { terrain: 'water' });
    h.edit(1, 1, { terrain: 'rock' });
    h.flush();
    assert.equal(h.builds.get('0,0'), 3);
    assert.equal(entry.water.count, 0);
    assert.equal(entry.ground.count, 16);
    assert.equal(entry.waterIndices[5], -1);
    assert.ok(entry.groundIndices[5] >= 0);
});
