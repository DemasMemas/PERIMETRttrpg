const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/lobby3d.js'), 'utf8');
const clipStart = source.indexOf('function clipWorldTerrainData(');
const clipCode = source.slice(clipStart, source.indexOf('\n}', clipStart) + 2);


test('smooth terrain picking retains full triangle order and restores render indices on failure', () => {
    const start = source.indexOf('function raycastSmoothTerrain(');
    const end = source.indexOf('\nconst groundMat', start);
    const indices = { array: [3, 4, 5] };
    const geometry = { index: indices, setIndex(index) { this.index = index; } };
    let fail = false;
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, THREE: { Mesh: { prototype: { raycast(ray, hits) {
        assert.equal(this.geometry.index, null);
        if (fail) throw new Error('pick failed');
        hits.push({ faceIndex: 0 });
    } } } } });
    vm.runInContext(clipCode + source.slice(start, end), context);
    const hits = [];
    context.raycastSmoothTerrain.call({ geometry }, {}, hits);
    assert.equal(hits[0].faceIndex, 0);
    assert.equal(geometry.index, indices);
    fail = true;
    assert.throws(() => context.raycastSmoothTerrain.call({ geometry }, {}, []), /pick failed/);
    assert.equal(geometry.index, indices);
});
const fillStart = source.indexOf('function fillChunkTerrainInstances(');
const fillEnd = source.indexOf('\n// --- Функции для чанков ---', fillStart);
const updateStart = source.indexOf('export function updateTileInChunk(');
const updateEnd = source.indexOf('\nexport function setBrushRadius(', updateStart);
assert.ok(fillStart >= 0 && fillEnd > fillStart && updateStart >= 0 && updateEnd > updateStart);

function harness({ deferTimers = false } = {}) {
    class Object3D {
        constructor() {
            this.matrix = {};
            this.position = { set: (x, y, z) => { this.matrix.position = [x, y, z]; } };
            this.scale = { set: (x, y, z) => { this.matrix.scale = [x, y, z]; } };
        }
        updateMatrix() {}
    }
    class Color {
        constructor(hex) { this.hex = hex; }
        setHex(hex) { this.hex = hex; }
    }
    const mesh = () => ({
        visible: true, count: 4, matrices: [], colors: [],
        instanceMatrix: { needsUpdate: false }, instanceColor: { needsUpdate: false },
        setMatrixAt(index, matrix) { this.matrices[index] = { position: [...matrix.position], scale: [...matrix.scale] }; },
        setColorAt(index, color) { this.colors[index] = color.hex; },
    });
    const ground = mesh();
    const water = mesh();
    const tilesData = [
        [{ terrain: 'grass', height: 1 }, { terrain: 'water', height: 2 }],
        [{ terrain: 'rock', height: 3 }, { terrain: 'grass', height: 4 }],
    ];
    const entry = { chunkX: 0, chunkY: 0, ground, water, tilesData, terrainVisible: true, bounds: { max: { y: 4.5 } } };
    const timers = [];
    let terrainBuilds = 0;
    let waterBuilds = 0;
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        THREE: { Object3D, Color }, terrainColors: { grass: 1, rock: 2 },
        CHUNK_SIZE: 2, chunksMap: new Map([['0,0', entry]]),
        markWorldShadowsDirty() {}, updateTileObjectsPositions() {}, refreshWorldFenceNeighborhood() {}, invalidateFenceGround() {},
        rebuildRoadMeshes() {}, refreshRoadNeighborhood() {}, queueWorldBridgeSpanRefresh() {},
        requestAnimationFrame() {}, cancelAnimationFrame() {}, rebuildChunkObjects() {},
        refreshWorldTerrainSurface() {},
        buildSmoothTerrainData: () => {
            terrainBuilds++;
            return { positions: [], tileByFace: [], foundationByTile: new Map() };
        },
        indexWaterGeometry: () => {},
        buildWorldWaterData: () => {
            waterBuilds++;
            return { water: [], bankPositions: [], bankOwners: [], sides: [], regions: new Map() };
        },
        waterLevelForTile: tile => tile.height,
        chunkBounds: [], scene: { remove() {} },
        clearTimeout(id) { if (deferTimers) timers[id - 1] = null; },
        setTimeout(callback) {
            if (!deferTimers) callback();
            timers.push(callback);
            return timers.length;
        },
        brushActive: false,
        lastHoverRaycastAt: 0,
    });
    vm.runInContext(source.slice(fillStart, fillEnd)
        + source.slice(updateStart, updateEnd).replace('export ', ''), context);
    context.queueWorldBridgeSpanRefresh = () => {};
    Object.assign(entry, context.fillChunkTerrainInstances(ground, water, tilesData, 0, 0));
    return {
        context, entry, ground, water,
        get terrainBuilds() { return terrainBuilds; },
        get waterBuilds() { return waterBuilds; },
        flushTimers() {
            const pending = timers.splice(0);
            for (const callback of pending) callback?.();
        },
    };
}

test('world terrain instances contain only real land and water tiles', () => {
    const { entry, ground, water } = harness();
    assert.equal(ground.count, 3);
    assert.equal(water.count, 1);
    assert.equal(entry.groundIndices[1], -1);
    assert.equal(entry.waterIndices[1], 0);
    assert.deepEqual(ground.matrices[0].scale, [1, 1, 1]);
    assert.deepEqual(water.matrices[0].position, [1.5, 1, 0.5]);
    assert.deepEqual(water.matrices[0].scale, [1, 2, 1]);
    assert.deepEqual(ground.matrices[1].position, [0.5, 1.5, 1.5]);
    assert.deepEqual(ground.matrices[1].scale, [1, 3, 1]);
    assert.match(source, /new THREE\.InstancedMesh\(groundGeo, waterInstanceMaterials, totalTiles\)/);
    assert.match(source, /groundInstances\.frustumCulled = false/);
});

test('baked world surface covers land tops, skips water, and reuses its texture on edits', () => {
    const start = source.indexOf('function refreshWorldTerrainSurface(');
    const end = source.indexOf('\nfunction fillChunkTerrainInstances(', start);
    assert.ok(start >= 0 && end > start);
    let generated = 0;
    let updated = 0;
    let generatedMode = null;
    let updatedMode = null;
    let generatedMask = null;
    let updatedMask = null;
    let createdGeometry = 0;
    let createdMaterial = 0;
    let createdMesh = 0;
    let disposedGeometry = 0;
    let disposedMaterial = 0;
    const metrics = {
        fullLoadMs: null, terrainStages: { geometryMs: 0, groundPixelsMs: 0 },
        groundPixelDetails: {
            fullMs: 0, partialMs: 0, reusedMs: 0,
            fullPasses: 0, partialPasses: 0, reusedPasses: 0,
            fullPixels: 0, partialPixels: 0, terrainLookups: 0,
            photoColorCalls: 0, photoSampledCalls: 0, photoSampledMs: 0,
            photoSamplingEstimateMs: 0, otherProcessingEstimateMs: 0,
        },
    };
    const entry = {
        chunkX: 0, chunkY: 0, terrainVisible: true,
        tilesData: [[{ terrain: 'grass', height: 1 }, { terrain: 'water', height: 1 }],
            [{ terrain: 'sand', height: 2 }, { terrain: 'rock', height: 1 }]],
        smoothTileIndices: new Set([2]),
    };
    const smoothData = { topByVertex: [1, 1, 1],
        positions: [1, 2, 2, 2, 1.5, 2, 1, 2, 3],
        normals: [0, 1, 0, 0, 1, 0, 0, 1, 0] };
    class Geometry {
        constructor() { createdGeometry++; this.attributes = {}; }
        setAttribute(name, value) { this.attributes[name] = value; }
        dispose() { disposedGeometry++; }
    }
    class Texture {
        constructor(data, width, height) { this.image = { data, width, height }; }
        dispose() { this.disposed = true; }
    }
    class Material {
        constructor(options) { createdMaterial++; Object.assign(this, options); }
        dispose() { disposedMaterial++; }
    }
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        CHUNK_SIZE: 2, chunksMap: new Map([['0,0', entry]]),
        window: { worldLoadMetrics: metrics }, performance,
        worldGrassGroundPixelsEnabled: true,
        worldGrassDetail: { apply(material) { material.worldGrassDetail = true; } },
        scene: { add() {}, remove() {} },
        createTerrainPixelsRegion: (...args) => {
            generated++;
            generatedMode = args[5];
            generatedMask = args[7];
            args[6].pixels = 512 * 512;
            args[6].terrainLookups = 2025;
            return { data: new Uint8Array(512 * 512 * 4), width: 512, height: 512 };
        },
        updateTerrainPixelsRegion: (...args) => {
            updated++;
            updatedMode = args[6];
            updatedMask = args[8];
            args[7].pixels = 240;
            return true;
        },
        THREE: { DataTexture: Texture, BufferGeometry: Geometry, MeshStandardMaterial: Material,
            Mesh: class { constructor(geometry, material) {
                createdMesh++;
                this.geometry = geometry; this.material = material;
            } },
            Float32BufferAttribute: class { constructor(array) { this.array = array; } },
            RGBAFormat: 1, UnsignedByteType: 2, LinearFilter: 3,
            LinearMipmapLinearFilter: 4, sRGBEncoding: 5, DoubleSide: 6 },
    });
    vm.runInContext(clipCode + source.slice(start, end), context);
    context.refreshWorldTerrainSurface(entry, smoothData);
    const positions = entry.terrainSurface.geometry.attributes.position.array;
    const uvs = entry.terrainSurface.geometry.attributes.uv.array;
    assert.equal(positions.length / 3, 15);
    assert.deepEqual(Array.from(positions.slice(-9)), [0, 2.002, 1, 1, 1.502, 1, 0, 2.002, 2]);
    assert.equal(uvs[0], 24 / 512);
    assert.equal(uvs[1], 24 / 512);
    assert.equal(entry.terrainSurface.material.map, entry.terrainTexture);
    assert.equal(entry.terrainSurface.material.worldGrassDetail, true);
    assert.equal(generatedMask, true);
    assert.equal(entry.terrainSurface.raycast(), undefined);
    const originalTexture = entry.terrainTexture;
    const originalMesh = entry.terrainSurface;
    const originalMaterial = originalMesh.material;
    entry.terrainTextureRegion = { minX: 0, maxX: 0, minZ: 0, maxZ: 0 };
    context.refreshWorldTerrainSurface(entry, smoothData);
    assert.equal(generated, 1);
    assert.equal(updatedMask, true);
    assert.equal(updated, 1);
    assert.equal(generatedMode, true);
    assert.equal(updatedMode, true);
    assert.equal(entry.terrainTexture, originalTexture);
    assert.equal(entry.terrainSurface, originalMesh);
    assert.equal(entry.terrainSurface.material, originalMaterial);
    assert.equal(entry.terrainTextureRegion, null);
    assert.equal(createdGeometry, 2);
    assert.equal(createdMaterial, 1);
    assert.equal(createdMesh, 1);
    assert.equal(disposedGeometry, 1);
    assert.equal(disposedMaterial, 0);
    context.refreshWorldTerrainSurface(entry, smoothData);
    assert.equal(generated, 1);
    assert.equal(updated, 1);
    assert.equal(createdGeometry, 3);
    assert.deepEqual([metrics.groundPixelDetails.fullPasses,
        metrics.groundPixelDetails.partialPasses, metrics.groundPixelDetails.reusedPasses], [1, 1, 1]);
    assert.deepEqual([metrics.groundPixelDetails.fullPixels,
        metrics.groundPixelDetails.partialPixels, metrics.groundPixelDetails.terrainLookups],
    [512 * 512, 240, 2025]);
});

test('water-only world chunks skip terrain texture and release it across land transitions', () => {
    const start = source.indexOf('function refreshWorldTerrainSurface(');
    const end = source.indexOf('\nfunction fillChunkTerrainInstances(', start);
    const counts = { generated: 0, textures: 0, geometries: 0, materials: 0, meshes: 0,
        disposedTextures: 0, disposedGeometries: 0, disposedMaterials: 0, removed: 0 };
    const entry = {
        chunkX: 0, chunkY: 0, terrainVisible: true,
        tilesData: Array.from({ length: 2 }, () => Array.from({ length: 2 }, () =>
            ({ terrain: 'water', height: 1 }))),
        smoothTileIndices: new Set(),
    };
    class Geometry {
        setAttribute() {}
        dispose() { counts.disposedGeometries++; }
        constructor() { counts.geometries++; }
    }
    class Texture {
        constructor(data, width, height) {
            counts.textures++;
            this.image = { data, width, height };
        }
        dispose() { counts.disposedTextures++; }
    }
    class Material {
        constructor(options) { counts.materials++; Object.assign(this, options); }
        dispose() { counts.disposedMaterials++; }
    }
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        CHUNK_SIZE: 2, chunksMap: new Map([['0,0', entry]]),
        worldGrassGroundPixelsEnabled: true,
        worldGrassDetail: { apply(material) { material.worldGrassDetail = true; } },
        scene: { add() {}, remove() { counts.removed++; } },
        createTerrainPixelsRegion: () => {
            counts.generated++;
            return { data: new Uint8Array(512 * 512 * 4), width: 512, height: 512 };
        },
        updateTerrainPixelsRegion: () => true,
        THREE: { DataTexture: Texture, BufferGeometry: Geometry, MeshStandardMaterial: Material,
            Mesh: class { constructor(geometry, material) {
                counts.meshes++;
                this.geometry = geometry;
                this.material = material;
            } },
            Float32BufferAttribute: class { constructor(array) { this.array = array; } },
            RGBAFormat: 1, UnsignedByteType: 2, LinearFilter: 3,
            LinearMipmapLinearFilter: 4, sRGBEncoding: 5, DoubleSide: 6 },
    });
    vm.runInContext(clipCode + source.slice(start, end), context);
    const smoothData = { topByVertex: [], positions: [], normals: [] };
    context.refreshWorldTerrainSurface(entry, smoothData);
    assert.deepEqual([counts.generated, counts.textures, counts.geometries,
        counts.materials, counts.meshes], [0, 0, 0, 0, 0]);
    assert.equal(entry.terrainTexture, null);
    assert.equal(entry.terrainSurface, undefined);

    entry.tilesData[0][0].terrain = 'grass';
    context.refreshWorldTerrainSurface(entry, smoothData);
    const firstTexture = entry.terrainTexture;
    assert.ok(entry.terrainSurface);
    assert.equal(entry.terrainSurface.material.map, firstTexture);
    assert.equal(counts.generated, 1);
    assert.deepEqual([counts.textures, counts.geometries, counts.materials, counts.meshes], [1, 1, 1, 1]);

    entry.tilesData[0][0].terrain = 'water';
    entry.terrainTextureRegion = { minX: 0, maxX: 0, minZ: 0, maxZ: 0 };
    context.refreshWorldTerrainSurface(entry, smoothData);
    assert.equal(entry.terrainSurface, null);
    assert.equal(entry.terrainTexture, null);
    assert.equal(entry.terrainTextureRegion, null);
    assert.deepEqual([counts.disposedTextures, counts.disposedGeometries,
        counts.disposedMaterials, counts.removed], [1, 1, 1, 1]);
    assert.equal(counts.generated, 1);

    entry.tilesData[0][0].terrain = 'grass';
    context.refreshWorldTerrainSurface(entry, smoothData);
    assert.notEqual(entry.terrainTexture, firstTexture);
    assert.equal(entry.terrainSurface.material.map, entry.terrainTexture);
    assert.equal(counts.generated, 2);
    assert.deepEqual([counts.textures, counts.geometries, counts.materials, counts.meshes], [2, 2, 2, 2]);
});

test('same-kind edit updates one instance; land-water edit rebuilds compact indices', () => {
    const { context, entry, ground, water, flushTimers } = harness({ deferTimers: true });
    const originalIndices = entry.groundIndices;
    context.updateTileInChunk(0, 0, 0, 0, { terrain: 'rock', height: 5 });
    assert.equal(entry.groundIndices, originalIndices);
    assert.deepEqual(ground.matrices[0].scale, [1, 5, 1]);
    assert.equal(ground.colors[0], 2);
    assert.equal(entry.bounds.max.y, 5.5);

    context.updateTileInChunk(0, 0, 0, 0, { terrain: 'water' });
    assert.notEqual(entry.groundIndices, originalIndices);
    assert.equal(ground.count, 2);
    assert.equal(water.count, 2);
    assert.equal(entry.groundIndices[0], -1);
    assert.equal(entry.waterIndices[0], 0);
    flushTimers();
    assert.deepEqual(water.matrices[0].scale, [1, 4.99, 1]);
});

test('world slopes rebuild near edits and chunk changes without replacing flat terrain instances', () => {
    assert.match(source, /refreshWorldTerrainNeighborhood\(cx, cy, null, null, false, false, true, true\)/);
    assert.match(source, /refreshWorldTerrainNeighborhood\(chunkX, chunkY, tileX, tileY,\s*true/);
    assert.match(source, /setTimeout\(flushWorldTerrainRefresh, 100\)/);
    assert.match(source, /window\.addEventListener\('pointerup'/);
    assert.match(source, /worldTerrainRefreshTimer = setTimeout\(flushWorldTerrainRefresh, 0\)/);
    assert.match(source, /buildWorldWaterData\(waterTiles,/);
    assert.match(source, /entry\.ground\.setMatrixAt\(index, dummy\.matrix\)/);
    assert.match(source, /data\.foundationByTile\.get\(`\$\{x \+ 1\},\$\{z \+ 1\}`\)/);
    assert.match(source, /Math\.max\(0\.05, foundation - 0\.085\)/);
    assert.match(source, /chunk\.smoothTerrain\.visible = groundVisible/);
    assert.match(source, /chunkBounds\.push\(entry\.smoothTerrainBounds\)/);
    assert.match(source, /uvs\.set\(\[data\.positions\[index \* 3\] - x,/);
    assert.doesNotMatch(source, /applyTerrainBlend\(/);
});

test('shore support stays anchored during queued height edits', () => {
    const { context, entry, ground } = harness({ deferTimers: true });
    // The smoothed shore rests on a shorter support than its stored center height.
    entry.smoothTileFoundation = new Map([[0, 0.7]]);
    context.refreshWorldTerrainNeighborhood = () => {};
    for (const height of [5, 2]) {
        context.updateTileInChunk(0, 0, 0, 0, { height });
        const matrix = ground.matrices[0];
        assert.equal(matrix.scale[1], 0.7);
        assert.equal(matrix.position[1] - matrix.scale[1] / 2, 0,
            'pending terrain rebuild must not lift the support above the ground');
    }
    assert.equal(entry.tilesData[0][0].height, 2);
});

test('height brush coalesces chunk rebuilds and ignores the echoed batch', () => {
    const world = harness({ deferTimers: true });
    world.context.brushActive = true;
    world.context.updateTileInChunk(0, 0, 0, 0, { height: 5 });
    world.context.updateTileInChunk(0, 0, 0, 0, { height: 6 });
    world.context.updateTileInChunk(0, 0, 1, 1, { height: 7 });
    assert.equal(world.terrainBuilds, 0);
    assert.equal(world.waterBuilds, 0);
    world.flushTimers();
    assert.equal(world.terrainBuilds, 0);
    world.context.brushActive = false;
    world.flushTimers();
    assert.equal(world.terrainBuilds, 1);
    assert.equal(world.waterBuilds, 1);
    world.context.updateTileInChunk(0, 0, 0, 0, { height: 6 });
    world.context.updateTileInChunk(0, 0, 1, 1, { height: 7 });
    world.flushTimers();
    assert.equal(world.terrainBuilds, 1);
    assert.equal(world.waterBuilds, 1);
});

test('releasing the world brush schedules its pending terrain refresh without camera movement', () => {
    const start = source.indexOf('function finishWorldBrushStroke(');
    const end = source.indexOf('\nfunction canvasMouseDownHandler(', start);
    assert.ok(start >= 0 && end > start);
    let scheduled = 0;
    let removed = 0;
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        brushActive: true, isMouseDown: true, lastProcessedTileKey: '0,0,1,1',
        window: {},
        controlsDisabled: true, controls: { enabled: false },
        globalMouseUpHandler() {},
        document: { removeEventListener() { removed++; } },
        pendingWorldTerrainChunks: new Set(['0,0']),
        clearTimeout() {}, setTimeout(callback, delay) { scheduled++; assert.equal(delay, 0); return 7; },
        flushWorldTerrainRefresh() {}, worldTerrainRefreshTimer: 3,
    });
    vm.runInContext(clipCode + source.slice(start, end), context);
    context.finishWorldBrushStroke();
    assert.equal(context.brushActive, false);
    assert.equal(context.controls.enabled, true);
    assert.equal(context.globalMouseUpHandler, null);
    assert.equal(context.worldTerrainRefreshTimer, 7);
    assert.equal(scheduled, 1);
    assert.equal(removed, 1);
});

test('slope overlay hides only its cube and restores it when the slope disappears', () => {
    const start = source.indexOf('function refreshWorldTerrainChunk(');
    const end = source.indexOf('\n// --- Функции для чанков ---', start);
    let sloped = true;
    let disposed = false;
    const matrices = [];
    const entry = {
        chunkX: 0, chunkY: 0, terrainVisible: true,
        tilesData: Array.from({ length: 2 }, () => Array.from({ length: 2 }, () => (
            { terrain: 'grass', height: 1 }
        ))),
        groundIndices: [0, 1, 2, 3], bounds: { max: { y: 1.5 } },
        ground: { instanceMatrix: {}, setMatrixAt(index, matrix) {
            matrices[index] = { scale: [...matrix.scale], position: [...matrix.position] };
        } },
    };
    class Geometry {
        constructor() { this.attributes = {}; }
        setAttribute(name, attribute) { this.attributes[name] = attribute; }
        setIndex(index) { this.index = index; }
        getAttribute(name) { return this.attributes[name]; }
        computeVertexNormals() {}
        dispose() { disposed = true; }
    }
    class Object3D {
        constructor() {
            this.matrix = {};
            this.position = { set: (...values) => { this.matrix.position = values; } };
            this.scale = { set: (...values) => { this.matrix.scale = values; } };
        }
        updateMatrix() {}
    }
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        CHUNK_SIZE: 2, chunksMap: new Map([['0,0', entry]]), chunkBounds: [],
        scene: { add() {}, remove() {} }, terrainColors: { grass: 0x123456 }, smoothGroundMat: {},
        refreshWorldTerrainSurface() {},
        raycastSmoothTerrain() {},
        buildSmoothTerrainData: () => sloped ? {
            positions: [1, 1, 1, 2, 1.5, 1, 1, 1, 2], normals: [0,1,0,0,1,0,0,1,0], topByVertex: [1, 1, 1],
            tileByFace: [{ x: 1, z: 1 }],
            tileByVertex: Array.from({ length: 3 }, () => ({ x: 1, z: 1 })),
            foundationByTile: new Map([['1,1', 0.8]]),
        } : { positions: [], tileByFace: [], foundationByTile: new Map() },
        THREE: {
            Object3D, BufferGeometry: Geometry,
            Float32BufferAttribute: class { constructor(data) { this.data = this.array = data; } },
            Color: class { setHex() { this.r = this.g = this.b = 0.5; } },
            Mesh: class { constructor(geometry) {
                this.geometry = geometry;
                this.position = { set() {} };
            } },
        },
    });
    vm.runInContext(clipCode + source.slice(start, end), context);
    context.refreshWorldTerrainChunk(entry);
    assert.ok(Math.abs(matrices[0].scale[1] - 0.715) < 1e-9);
    assert.ok(Math.abs(matrices[0].position[1] - matrices[0].scale[1] / 2) < 1e-9);
    assert.deepEqual(matrices[1].scale, [1, 1, 1]);
    assert.equal(matrices[1].position[1], 0.5);
    assert.equal(context.chunkBounds.length, 1);
    sloped = false;
    context.refreshWorldTerrainChunk(entry);
    assert.equal(disposed, true);
    assert.deepEqual(matrices[0].scale, [1, 1, 1]);
    assert.equal(matrices[0].position[1], 0.5);
    assert.equal(context.chunkBounds.length, 0);
});

async function neighborRefreshHarness(terrain = 'grass') {
    const terrainSource = fs.readFileSync(path.join(__dirname, '../app/static/js/locationTerrain.js'), 'utf8');
    const terrainUrl = `data:text/javascript;base64,${Buffer.from(terrainSource).toString('base64')}`;
    const { isSmoothTerrainTile } = await import(terrainUrl);
    const start = source.indexOf('function refreshWorldTerrainChunk(');
    const end = source.indexOf('\nfunction refreshWorldWaterChunk(', start);
    const metrics = { fullLoadMs: null, terrainFullRefreshes: 0, terrainPartialRefreshes: 0,
        terrainStages: { geometryMs: 0, groundPixelsMs: 0, waterMs: 0, dependentMeshesMs: 0 } };
    const counts = { top: 0, pixels: 0, water: 0, smooth: 0 };
    const entry = {
        chunkX: 0, chunkY: 0, terrainVisible: true, waterShoreDirty: true,
        tilesData: Array.from({ length: 2 }, () => Array.from({ length: 2 }, () =>
            ({ terrain, height: 1 }))),
        groundIndices: terrain === 'water' ? [-1, -1, -1, -1] : [0, 1, 2, 3],
        bounds: { max: { y: 1.5 } },
        ground: { instanceMatrix: {}, setMatrixAt() {} },
        terrainTexture: terrain === 'water' ? null : { image: {} },
    };
    const chunksMap = new Map([['0,0', entry]]);
    class Object3D {
        constructor() {
            this.matrix = {};
            this.position = { set() {} };
            this.scale = { set() {} };
        }
        updateMatrix() {}
    }
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        window: { worldLoadMetrics: metrics }, performance,
        CHUNK_SIZE: 2, chunksMap, chunkBounds: [], isSmoothTerrainTile,
        scene: { add() {}, remove() {} }, terrainColors: { grass: 1, sand: 2 },
        THREE: { Object3D },
        buildSmoothTerrainData: () => {
            counts.smooth++;
            return { positions: [], tileByFace: [], foundationByTile: new Map() };
        },
        refreshWorldTerrainSurface() { counts.top++; },
        refreshWorldTerrainPixels() { counts.pixels++; entry.terrainTextureRegion = null; },
        refreshWorldWaterChunk(target) { counts.water++; target.waterShoreDirty = false; },
    });
    vm.runInContext(clipCode + source.slice(start, end), context);
    return { context, entry, chunksMap, metrics, counts };
}

test('arriving flat neighbor repaints the boundary without rebuilding flat terrain', async () => {
    const { context, entry, chunksMap, metrics, counts } = await neighborRefreshHarness();
    context.refreshWorldTerrainChunk(entry);
    assert.deepEqual([metrics.terrainFullRefreshes, metrics.terrainPartialRefreshes], [1, 0]);
    const neighbor = { chunkX: 1, chunkY: 0, tilesData: Array.from({ length: 2 }, () =>
        Array.from({ length: 2 }, () => ({ terrain: 'sand', height: 1 }))) };
    chunksMap.set('1,0', neighbor);
    entry.terrainTextureRegion = { minX: 2, maxX: 3, minZ: 0, maxZ: 1 };
    entry.waterShoreDirty = true;
    context.refreshWorldTerrainChunk(entry, true);
    assert.deepEqual([metrics.terrainFullRefreshes, metrics.terrainPartialRefreshes], [1, 1]);
    assert.deepEqual([counts.top, counts.smooth, counts.pixels, counts.water], [1, 1, 1, 2]);
    assert.equal(entry.terrainTextureRegion, null);
    context.refreshWorldTerrainChunk(entry, true);
    assert.deepEqual([metrics.terrainFullRefreshes, metrics.terrainPartialRefreshes], [1, 2]);
    assert.equal(counts.top, 1);
});

test('arriving elevated neighbor rebuilds a newly sloped boundary', async () => {
    const { context, entry, chunksMap, metrics, counts } = await neighborRefreshHarness();
    context.refreshWorldTerrainChunk(entry);
    chunksMap.set('1,0', { chunkX: 1, chunkY: 0, tilesData: Array.from({ length: 2 }, () =>
        Array.from({ length: 2 }, () => ({ terrain: 'grass', height: 3 }))) });
    context.refreshWorldTerrainChunk(entry, true);
    assert.deepEqual([metrics.terrainFullRefreshes, metrics.terrainPartialRefreshes], [2, 0]);
    assert.deepEqual([counts.top, counts.smooth], [2, 2]);
});

test('diagonal elevated neighbor rebuilds the shared corner', async () => {
    const { context, entry, chunksMap, metrics } = await neighborRefreshHarness();
    context.refreshWorldTerrainChunk(entry);
    chunksMap.set('1,1', { chunkX: 1, chunkY: 1, tilesData: Array.from({ length: 2 }, () =>
        Array.from({ length: 2 }, () => ({ terrain: 'rock', height: 3 }))) });
    context.refreshWorldTerrainChunk(entry, true);
    assert.deepEqual([metrics.terrainFullRefreshes, metrics.terrainPartialRefreshes], [2, 0]);
});

test('water-only chunk updates shore after neighboring land arrives without ground texture', async () => {
    const { context, entry, chunksMap, metrics, counts } = await neighborRefreshHarness('water');
    context.refreshWorldTerrainChunk(entry);
    chunksMap.set('1,0', { chunkX: 1, chunkY: 0, tilesData: Array.from({ length: 2 }, () =>
        Array.from({ length: 2 }, () => ({ terrain: 'sand', height: 1 }))) });
    entry.waterShoreDirty = true;
    context.refreshWorldTerrainChunk(entry, true);
    assert.deepEqual([metrics.terrainFullRefreshes, metrics.terrainPartialRefreshes], [1, 1]);
    assert.deepEqual([counts.top, counts.water, counts.pixels], [1, 2, 0]);
});

test('chunk arrival order schedules full builds only for new chunks; edits still force full builds', () => {
    const start = source.indexOf('const pendingWorldTerrainChunks = new Set();');
    const end = source.indexOf('\n// ---', start);
    assert.ok(start >= 0 && end > start);
    for (const order of [[0, 1, 2, 3], [3, 2, 1, 0]]) {
        const chunksMap = new Map();
        const calls = [];
        const roads = [];
        const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
            CHUNK_SIZE: 2, chunksMap, brushActive: false, performance,
            refreshWorldTerrainChunk(entry, neighborOnly) {
                calls.push({ key: `${entry.chunkX},${entry.chunkY}`, neighborOnly });
            },
            rebuildRoadMeshes(entry) { roads.push(`${entry.chunkX},${entry.chunkY}`); },
            setTimeout() { return 1; }, clearTimeout() {}, lastHoverRaycastAt: 0,
        });
        vm.runInContext(clipCode + source.slice(start, end), context);
        for (const index of order) {
            const x = index % 2;
            const y = Math.floor(index / 2);
            chunksMap.set(`${x},${y}`, { chunkX: x, chunkY: y, hasRoadNodes: true });
            context.refreshWorldTerrainNeighborhood(x, y, null, null,
                true, true, true, true, true);
            context.flushWorldTerrainRefresh();
        }
        assert.equal(calls.length, 10);
        assert.equal(calls.filter(call => !call.neighborOnly).length, 4);
        assert.equal(calls.filter(call => call.neighborOnly).length, 6);
        assert.equal(roads.length, 10);
        context.refreshWorldTerrainNeighborhood(0, 0, 0, 0,
            true, false, true, true, false);
        context.flushWorldTerrainRefresh();
        assert.deepEqual(calls.at(-1), { key: '0,0', neighborOnly: false });
    }
});

test('world landmarks use their actual lowest vertex as the ground offset', () => {
    const start = source.indexOf('function getBaseHalfHeight(');
    const end = source.indexOf('\nfunction getDefaultColorForType(', start);
    assert.ok(start >= 0 && end > start);
    const landmark = { geometry: { boundingBox: null, computeBoundingBox() { this.boundingBox = { min: { y: -0.31 } }; } } };
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        WORLD_LANDMARKS: { forest: landmark },
        getBaseDimensions: type => ({ height: type === 'tree' ? 1 : 1.5 }),
    });
    vm.runInContext(clipCode + source.slice(start, end).replaceAll('export function ', 'function '), context);
    assert.equal(context.getObjectHeightOffset('forest'), 0.31);
    assert.equal(context.getObjectHeightOffset('tree'), 0.5);
});

test('removing a chunk releases instance buffers but keeps shared terrain resources', () => {
    const start = source.indexOf('export function removeChunk(');
    const end = source.indexOf('\n// --- Функция для обновления позиций', start);
    assert.ok(start >= 0 && end > start);
    const removed = [];
    const cancelled = [];
    const roads = [];
    const mesh = () => ({
        geometry: { dispose() { throw new Error('shared geometry disposed'); } },
        material: { dispose() { throw new Error('shared material disposed'); } },
        dispose() { this.disposed = true; },
    });
    const entry = { ground: mesh(), water: mesh(), trees: mesh(), farAnomalies: mesh(), pendingRebuild: 7, anomalyLODs: [] };
    entry.objectStateSnapshots = [[{ objects: [{ type: 'tree' }] }]];
    const chunksMap = new Map([['0,0', entry]]);
    const east = { hasRoadNodes: true };
    const diagonal = { hasRoadNodes: true };
    chunksMap.set('1,0', east);
    chunksMap.set('1,1', diagonal);
    chunksMap.set('0,1', { hasRoadNodes: false });
    chunksMap.set('2,0', { hasRoadNodes: true });
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        chunksMap, chunkBounds: [{ key: '0,0' }], fenceGroundSamplers: new Map(), refreshFenceChunkNeighbors() {},
        scene: { remove(object) { removed.push(object); } },
        cancelAnimationFrame(id) { cancelled.push(id); },
        disposeChunkAnomalyLODs() {},
        disposeRoadMeshes() {},
        rebuildRoadMeshes(target) {
            assert.equal(chunksMap.has('0,0'), false);
            roads.push(target);
        },
        markWorldShadowsDirty() {}, lastChunkVisibilityAt: 0, lastHoverRaycastAt: 0,
        CHUNK_SIZE: 2, THREE: {},
        refreshWorldTerrainNeighborhood() {},
    });
    const roadStart = source.indexOf('function refreshRoadNeighborhood(');
    const roadEnd = source.indexOf('\nfunction rebuildRoadMeshes(', roadStart);
    assert.ok(roadStart >= 0 && roadEnd > roadStart);
    vm.runInContext(source.slice(roadStart, roadEnd), context);
    vm.runInContext(clipCode + source.slice(start, end).replace('export ', ''), context);
    context.removeChunk(0, 0);
    assert.equal(chunksMap.has('0,0'), false);
    assert.deepEqual(cancelled, [7]);
    assert.equal(entry.objectStateSnapshots, null);
    assert.equal(entry.ground.disposed, true);
    assert.equal(entry.water.disposed, true);
    assert.equal(entry.trees.disposed, true);
    assert.equal(entry.farAnomalies.disposed, true);
    assert.equal(removed.length, 4);
    assert.deepEqual(roads, [east, diagonal]);
});

test('boundary terrain and height edits refresh loaded cardinal and diagonal roads', () => {
    const { context, entry } = harness();
    const roads = [];
    entry.hasRoadNodes = true;
    const east = { chunkX: 1, chunkY: 0, hasRoadNodes: true };
    const diagonal = { chunkX: 1, chunkY: 1, hasRoadNodes: true };
    context.chunksMap.set('1,0', east);
    context.chunksMap.set('1,1', diagonal);
    context.chunksMap.set('2,0', { hasRoadNodes: true });
    context.rebuildRoadMeshes = target => roads.push(target);
    // Isolate road invalidation from terrain generation in this boundary-edit test.
    context.refreshWorldTerrainNeighborhood = () => {};
    const start = source.indexOf('function refreshRoadNeighborhood(');
    const end = source.indexOf('\nfunction rebuildRoadMeshes(', start);
    assert.ok(start >= 0 && end > start);
    vm.runInContext(clipCode + source.slice(start, end), context);
    context.updateTileInChunk(0, 0, 1, 1, { terrain: 'rock', height: 5 });
    assert.deepEqual(roads, [entry, east, diagonal]);
    assert.equal(entry.roadBoundaryDirty, false);
});

test('world chunks wait for one shared grass image load and keep a consistent fallback', async () => {
    const lobbySource = fs.readFileSync(path.join(__dirname, '../app/static/js/lobbyData.js'), 'utf8');
    const start = lobbySource.indexOf('let loadingAllChunks = null;');
    assert.ok(start >= 0);
    const code = lobbySource.slice(start).replace('export function loadAllChunks(', 'function loadAllChunks(');
    const setup = () => {
        let resolveImages;
        let rejectImages;
        const imagePromise = new Promise((resolve, reject) => {
            resolveImages = resolve;
            rejectImages = reject;
        });
        const events = [];
        const counters = { imageLoads: 0, fetches: 0, warnings: 0, errors: 0 };
        const chunksMap = new Map();
        const window = { MAP_CHUNKS_WIDTH: 1, MAP_CHUNKS_HEIGHT: 1, isLocationActive: false };
        const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
            window, chunksMap, currentLobbyId: 1,
            performance: { now: () => 10, mark() {}, measure() {} },
            Server: { async getChunks(id, minX, maxX, minY, maxY) {
                counters.fetches++;
                const chunks = [];
                for (let x = minX; x <= maxX; x++) {
                    for (let y = minY; y <= maxY; y++) {
                        chunks.push({ chunk_x: x, chunk_y: y, data: [] });
                    }
                }
                return chunks;
            } },
            loadGrassGroundPixels() { counters.imageLoads++; return imagePromise; },
            setWorldGrassGroundPixelsEnabled(enabled) { events.push(`source:${enabled}`); },
            addChunk(x, y) { events.push(`chunk:${x},${y}`); chunksMap.set(`${x},${y}`, {}); },
            waitForWorldTerrainRefresh: async () => {},
            beginWorldTerrainLoad: () => () => {},
        prepareInitialWorldTerrainPixels: async () => {},
            console: { warn() { counters.warnings++; }, error() { counters.errors++; }, info() {} },
        });
        vm.runInContext(code, context);
        return { context, window, events, counters, resolveImages, rejectImages };
    };

    const ready = setup();
    const loading = ready.context.loadAllChunks();
    await new Promise(setImmediate);
    assert.equal(ready.counters.fetches, 1);
    assert.equal(ready.counters.imageLoads, 1);
    assert.deepEqual(ready.events, []);
    ready.resolveImages();
    await loading;
    assert.deepEqual(ready.events, ['source:true', 'chunk:0,0']);
    assert.equal(ready.window.getWorldLoadDiagnostics().grassGroundSource, 'photos');
    ready.window.MAP_CHUNKS_WIDTH = 2;
    await ready.context.loadAllChunks();
    assert.equal(ready.counters.imageLoads, 1);
    assert.deepEqual(ready.events, ['source:true', 'chunk:0,0', 'source:true', 'chunk:1,0']);
    assert.equal(ready.counters.errors, 0);

    const fallback = setup();
    const fallbackLoading = fallback.context.loadAllChunks();
    await new Promise(setImmediate);
    assert.deepEqual(fallback.events, []);
    fallback.rejectImages(new Error('ground image unavailable'));
    await fallbackLoading;
    assert.deepEqual(fallback.events, ['source:false', 'chunk:0,0']);
    assert.equal(fallback.window.getWorldLoadDiagnostics().grassGroundSource, 'procedural-fallback');
    assert.equal(fallback.counters.warnings, 1);
    assert.equal(fallback.counters.errors, 0);
});


test('side-only ground rendering preserves full-box picking and restores geometry on failure', () => {
    const start = source.indexOf('function raycastGroundInstances(');
    const end = source.indexOf('\nconst groundMat', start);
    const rendered = {}, fullBox = {}, ray = {}, hits = [];
    const mesh = { geometry: rendered };
    let fail = false;
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {}, worldWaterProfiles: { updateRegion() {} },
        groundGeo: fullBox,
        THREE: { InstancedMesh: { prototype: { raycast(raycaster, intersections) {
            assert.equal(this, mesh);
            assert.equal(this.geometry, fullBox);
            assert.equal(raycaster, ray);
            assert.equal(intersections, hits);
            if (fail) throw new Error('ray failure');
            hits.push({ instanceId: 19 });
        } } } },
    });
    vm.runInContext(clipCode + source.slice(start, end), context);
    context.raycastGroundInstances.call(mesh, ray, hits);
    assert.equal(hits[0].instanceId, 19);
    assert.equal(mesh.geometry, rendered);
    fail = true;
    assert.throws(() => context.raycastGroundInstances.call(mesh, ray, hits), /ray failure/);
    assert.equal(mesh.geometry, rendered);
});
