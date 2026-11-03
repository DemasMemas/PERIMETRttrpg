const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/lobby3d.js'), 'utf8');
const topology = fs.readFileSync(path.join(__dirname, '../app/static/js/worldRoadTopology.js'), 'utf8')
    .replaceAll('export ', '');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));

// Exercise real object groups, road topology and RAF scheduling. Three.js and
// the inside of an anomaly effect are substitutes; counters are not GPU timings.
function setup() {
    const m = { rebuilds: 0, created: 0, disposed: 0, matrixWrites: 0, roadBuilds: 0,
        effectsCreated: 0, effectsDisposed: 0, scheduled: 0, cancelled: 0 };
    class Vector3 {
        constructor(x = 0, y = 0, z = 0) { this.set(x, y, z); }
        set(x, y, z) { Object.assign(this, { x, y, z }); return this; }
        setScalar(n) { return this.set(n, n, n); }
        copy(p) { return this.set(p.x, p.y, p.z); }
        clone() { return new Vector3(this.x, this.y, this.z); }
        addScaledVector(p, n) { this.x += p.x * n; this.y += p.y * n; this.z += p.z * n; return this; }
        add(p) { return this.addScaledVector(p, 1); }
        subVectors(a, b) { return this.copy(a).addScaledVector(b, -1); }
        multiplyScalar(n) { this.x *= n; this.y *= n; this.z *= n; return this; }
        length() { return Math.hypot(this.x, this.y, this.z); }
        normalize() { return this.multiplyScalar(1 / (this.length() || 1)); }
    }
    class Object3D {
        constructor() {
            this.position = new Vector3(); this.scale = new Vector3(); this.rotation = new Vector3();
            this.quaternion = { setFromUnitVectors() {} }; this.matrix = { fromArray(elements) { this.elements = elements; } };
        }
        updateMatrix() {
            this.matrix = { fromArray(elements) { this.elements = elements; }, position: [this.position.x, this.position.y, this.position.z],
                scale: [this.scale.x, this.scale.y, this.scale.z],
                rotation: [this.rotation.x, this.rotation.y, this.rotation.z] };
        }
    }
    class InstancedMesh extends Object3D {
        constructor(geometry, material, count) {
            super();
            Object.assign(this, { geometry, material, count, instanceMatrix: {} });
            m.created++;
        }
        setMatrixAt(index, matrix) {
            this.matrices ??= [];
            this.matrices[index] = structuredClone(Object.fromEntries(Object.entries(matrix).filter(([, value]) => typeof value !== 'function')));
            m.matrixWrites++;
        }
        setColorAt() { this.instanceColor = {}; }
        dispose() {
            assert.equal(this.disposed, undefined, 'instance buffers disposed twice');
            this.disposed = true;
            m.disposed++;
        }
    }
    const frames = new Map();
    let frameId = 0;
    const landmarkTypes = ['forest', 'hamlet', 'village', 'factory', 'base', 'camp'];
    const shared = { dispose() { throw new Error('shared resource disposed'); } };
    const context = vm.createContext({refreshWorldIceSurface() {},clearWorldIceSurface() {},
        CHUNK_SIZE: 32, chunksMap: new Map(), sampleWorldGroundHeight: () => 1,
        THREE: { Vector3, Object3D, InstancedMesh, InstancedBufferAttribute: class {},
            SphereGeometry: class {}, MeshBasicMaterial: class {}, Color: class {},
            MathUtils: { degToRad: n => n * Math.PI / 180 } },
        treeGeo: shared, treeMat: shared, houseGeo: shared, houseMat: shared,
        fenceGeo: shared, fenceMat: shared, landmarkMat: shared,
        roadPadGeometry: shared, roadLinkGeometry: shared, roadMaterial: shared,
        WORLD_LANDMARK_TYPES: landmarkTypes,
        WORLD_LANDMARKS: Object.fromEntries(landmarkTypes.map(type => [type, { geometry: shared }])),
        scene: { add() {}, remove() {} },
        // This fixture contains ordinary roads only; bridges have a separate renderer suite.
        createWorldRoadBridgeMeshes: () => [], loadedWorldTile: () => null,
        // Substitute only the merged surface allocation, retaining real road topology.
        roadSurfaceMesh(pads, links) {
            if (!pads.length && !links.length) return null;
            const mesh = new InstancedMesh(null, shared, 1);
            mesh.userData = { roadSurface: true };
            mesh.geometry = { dispose: () => mesh.dispose() };
            return mesh;
        },
        getBaseGroundOffset: () => 0.5, getDefaultColorForType: () => '#808080',
        markWorldShadowsDirty() {}, queueWorldBridgeSpanRefresh() {}, invalidateFenceGround() {}, refreshWorldFenceNeighborhood() {}, refreshFenceChunkNeighbors() {}, fenceGroundSamplers: new Map(), worldWaterProfiles: { updateRegion() {} }, lastChunkVisibilityAt: 0, lastHoverRaycastAt: 0,
        createAnomalyLOD(x, y, z) {
            m.effectsCreated++;
            return { position: new Vector3(x, y, z), userData: {}, traverse(callback) {
                callback({ geometry: { dispose() { m.effectsDisposed++; } }, material: { dispose() {} } });
            } };
        },
        requestAnimationFrame(callback) {
            m.scheduled++; frames.set(++frameId, callback); return frameId;
        },
        cancelAnimationFrame(id) { m.cancelled++; frames.delete(id); },
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/js/worldFenceLine.js'), 'utf8').replaceAll('export ', '')
        + slice('function setFenceMatrix(', '\nexport function removeFenceLinePreview(')
        + topology
        + slice('function createAnomalyFieldLOD(', '\nfunction updateFarAnomalyInstances(')
        + slice('function createLandmarkMeshes(', '\nexport function addChunk(')
        + slice('function rebuildChunkObjects(', '\nexport function setBrushRadius(').replaceAll('export ', ''),
        context);
    const rebuild = context.rebuildChunkObjects;
    context.rebuildChunkObjects = (entry, groups) => { m.rebuilds++; rebuild(entry, groups); };
    const roads = context.rebuildRoadMeshes;
    context.rebuildRoadMeshes = entry => { m.roadBuilds++; roads(entry); };
    const tiles = () => Array.from({ length: 32 }, () => Array.from({ length: 32 }, () =>
        ({ height: 1, terrain: 'grass', objects: [] })));
    const main = { chunkX: 0, chunkY: 0, tilesData: tiles(), pendingRebuild: null,
        hasRoadNodes: true, terrainVisible: true };
    for (const [x, type] of [[10, 'tree'], [12, 'house'], [14, 'fence'], [16, 'forest'], [18, 'road'], [19, 'road']]) {
        main.tilesData[10][x].objects = [{ type }];
    }
    main.tilesData[10][20].anomaly_field = { name: 'test', rank: 1, field_type: 'electric' };
    const neighbor = { ...main, chunkX: 1, tilesData: tiles() };
    neighbor.tilesData[10][0].objects = [{ type: 'road' }];
    main.objectStateSnapshots = context.createWorldObjectStateSnapshots(main.tilesData);
    neighbor.objectStateSnapshots = context.createWorldObjectStateSnapshots(neighbor.tilesData);
    context.chunksMap.set('0,0', main); context.chunksMap.set('1,0', neighbor);
    context.rebuildChunkObjects(main); context.rebuildChunkObjects(neighbor);
    for (const key of Object.keys(m)) m[key] = 0;
    return {
        m, main, context, neighbor,
        update: (objects, x = 10) => context.updateTileInChunk(0, 0, x, 10, { objects }),
        field: field => context.updateTileInChunk(0, 0, 20, 10, { anomaly_field: field }),
        flush() {
            const callbacks = [...frames.values()]; frames.clear();
            callbacks.forEach(callback => callback());
        },
    };
}

const clone = value => JSON.parse(JSON.stringify(value));

test('independent identical objects and fields do not rebuild any object resources', () => {
    const h = setup();
    const oldTrees = h.main.trees, oldLOD = h.main.anomalyLODs[0], oldRoad = h.main.roads[0];
    h.update(clone(h.main.tilesData[10][10].objects));
    h.field({ field_type: 'electric', rank: 1, name: 'test' });
    h.flush();
    assert.ok(Object.values(h.m).every(value => value === 0));
    assert.equal(h.main.trees, oldTrees);
    assert.equal(h.main.anomalyLODs[0], oldLOD);
    assert.equal(h.main.roads[0], oldRoad);
});

test('identical echoes before RAF preserve the scheduled handle and after RAF allocate nothing', () => {
    const h = setup();
    h.update([{ type: 'tree', x: 0.2, properties: { tags: ['a', 'b'] } }]);
    const pending = h.main.pendingRebuild;
    h.update([{ properties: { tags: ['a', 'b'] }, x: 0.2, type: 'tree' }]);
    assert.equal(h.main.pendingRebuild, pending);
    assert.equal(h.m.cancelled, 0);
    h.flush();
    assert.equal(h.m.rebuilds, 1);
    const after = { ...h.m };
    h.update(clone(h.main.tilesData[10][10].objects));
    h.flush();
    assert.deepEqual(h.m, after);
    h.field({ name: 'test', rank: 3, field_type: 'electric' });
    const fieldPending = h.main.pendingRebuild;
    const cancellations = h.m.cancelled;
    h.field(clone(h.main.tilesData[10][20].anomaly_field));
    assert.equal(h.main.pendingRebuild, fieldPending);
    assert.equal(h.m.cancelled, cancellations);
    h.flush();
    const afterField = { ...h.m };
    h.field(clone(h.main.tilesData[10][20].anomaly_field)); h.flush();
    assert.deepEqual(h.m, afterField);
});

test('nested object and anomaly changes through existing references are not skipped', () => {
    const h = setup();
    const initial = h.main.tilesData[10][10].objects;
    initial[0].properties = { tags: ['a'] };
    h.update(initial); h.flush();
    initial[0].properties.tags.push('b');
    h.update(initial); h.flush();
    assert.equal(h.m.rebuilds, 2);
    const field = h.main.tilesData[10][20].anomaly_field;
    field.rank = 3;
    h.field(field); h.flush();
    assert.equal(h.m.rebuilds, 3);
    field.conditions = { ranks: [1, 2] };
    h.field(field); h.flush();
    field.conditions.ranks[1] = 4;
    h.field(field); h.flush();
    assert.equal(h.m.rebuilds, 5);
    assert.equal(h.main.objectStateSnapshots[10][20].anomaly_field.conditions.ranks[1], 4);
});

for (const [label, values] of [['A-B-C', [0, 0.1, 0.2]], ['A-B-A', [0, 0.1, 0]]]) {
    for (const betweenFrames of [false, true]) {
        test(`${label} retains the final event ${betweenFrames ? 'across frames' : 'within one frame'}`, () => {
            const h = setup();
            for (const x of values) {
                h.update([{ type: 'tree', x }]);
                if (betweenFrames) h.flush();
            }
            h.flush();
            assert.equal(h.m.rebuilds, betweenFrames ? 3 : 1);
            assert.equal(h.main.tilesData[10][10].objects[0].x, values.at(-1));
            assert.equal(h.main.objectStateSnapshots[10][10].objects[0].x, values.at(-1));
            const before = { ...h.m };
            h.update([{ type: 'tree', x: values.at(-1) }]); h.flush();
            assert.deepEqual(h.m, before);
        });
    }
}

test('mixed events apply other fields even when object data are identical', () => {
    const h = setup();
    h.context.updateTileInChunk(0, 0, 10, 10, {
        objects: clone(h.main.tilesData[10][10].objects), name: 'Updated', radiation: 12,
    });
    h.flush();
    assert.equal(h.main.tilesData[10][10].name, 'Updated');
    assert.equal(h.main.tilesData[10][10].radiation, 12);
    assert.equal(h.m.rebuilds, 0);
    h.context.updateTileInChunk(0, 0, 20, 10, {
        anomaly_field: clone(h.main.tilesData[10][20].anomaly_field), objects: [{ type: 'fence' }],
    });
    h.flush();
    assert.equal(h.m.rebuilds, 1);
    assert.equal(h.main.fences.count, 2);
    h.context.updateTileInChunk(0, 0, 20, 10, {
        objects: clone(h.main.tilesData[10][20].objects),
        anomaly_field: { ...h.main.tilesData[10][20].anomaly_field, rank: 4 },
    });
    h.flush();
    assert.equal(h.m.rebuilds, 2);
});

test('add, move between tiles and remove the last tree retain compact instance indices', () => {
    const h = setup();
    h.update([]); h.flush();
    assert.equal(h.main.trees, null);
    h.update([{ type: 'tree' }]); h.flush();
    assert.equal(h.main.trees.count, 1);
    h.update([]);
    h.update([{ type: 'tree', rotation: 90 }], 11);
    h.flush();
    assert.equal(h.main.trees.count, 1);
    assert.equal(h.main.objectIndices.trees[10 * 32 + 10].length, 0);
    assert.deepEqual(Array.from(h.main.objectIndices.trees[10 * 32 + 11]), [0]);
    assert.equal(h.m.rebuilds, 3);
    h.update([], 11); h.flush();
    assert.equal(h.main.trees, null);
    assert.equal(h.m.rebuilds, 4);
});

test('boundary echoes skip all road work but real changes preserve existing neighbor rebuilding', () => {
    const h = setup();
    h.update([{ type: 'tree' }], 31); h.flush();
    assert.equal(h.m.rebuilds, 1);
    assert.equal(h.m.roadBuilds, 3);
    assert.equal(h.m.created, 4); // One tree and three merged road surfaces.
    const after = { ...h.m };
    h.update([{ type: 'tree' }], 31); h.flush();
    assert.deepEqual(h.m, after);
    h.update([], 31); h.flush();
    assert.equal(h.m.rebuilds, 2);
    assert.equal(h.m.roadBuilds, 6);
});

test('array order and nested road exits remain significant', () => {
    const h = setup();
    h.update([{ type: 'tree', name: 'a' }, { type: 'tree', name: 'b' }]); h.flush();
    h.update([{ type: 'tree', name: 'b' }, { type: 'tree', name: 'a' }]); h.flush();
    assert.equal(h.m.rebuilds, 2);
    const road = h.main.tilesData[10][18].objects;
    road[0].manual_connections = ['E'];
    h.update(road, 18); h.flush();
    const after = { ...h.m };
    h.update(clone(road), 18); h.flush();
    assert.deepEqual(h.m, after);
    road[0].manual_connections.push('N');
    h.update(road, 18); h.flush();
    assert.equal(h.m.rebuilds, 4);
    assert.deepEqual(Array.from(h.main.tilesData[10][18].objects[0].manual_connections), ['E', 'N']);
});

test('identical object fields do not suppress height work or the existing height-dependent anomaly rebuild', () => {
    const h = setup();
    const terrainCalls = [], objectMoves = [];
    h.main.groundIndices = Int16Array.from({ length: 1024 }, (_, i) => i);
    h.main.waterIndices = new Int16Array(1024).fill(-1);
    h.main.bounds = { max: { y: 1.5 } };
    h.main.ground = { instanceMatrix: {}, instanceColor: {}, setMatrixAt() {}, setColorAt() {} };
    h.context.terrainColors = { grass: 0x3a5f0b };
    h.context.refreshWorldTerrainNeighborhood = (...args) => terrainCalls.push(args);
    h.context.updateTileObjectsPositions = (...args) => objectMoves.push(args);
    h.context.updateTileInChunk(0, 0, 10, 10, {
        objects: clone(h.main.tilesData[10][10].objects), height: 3,
    });
    h.flush();
    assert.equal(h.main.tilesData[10][10].height, 3);
    assert.equal(h.m.rebuilds, 0);
    assert.equal(objectMoves[0][3], 3);
    assert.equal(terrainCalls[0][5], true); // Height must still invalidate roads.
    h.context.updateTileInChunk(0, 0, 20, 10, {
        anomaly_field: clone(h.main.tilesData[10][20].anomaly_field), height: 4,
    });
    h.flush();
    assert.equal(h.main.tilesData[10][20].height, 4);
    assert.equal(h.m.rebuilds, 1);
    assert.equal(h.m.effectsCreated, 1);
});

test('moving between chunks preserves the last event and the destination road', () => {
    const h = setup();
    h.update([]);
    h.context.updateTileInChunk(1, 0, 0, 10, { objects: [{ type: 'road' }, { type: 'tree' }] });
    h.flush();
    assert.equal(h.main.trees, null);
    assert.equal(h.neighbor.trees.count, 1);
    assert.equal(h.neighbor.hasRoadNodes, true);
    assert.deepEqual(Array.from(h.neighbor.objectIndices.trees[10 * 32]), [0]);
    assert.equal(h.m.rebuilds, 2);
    const after = { ...h.m };
    h.update([]);
    h.context.updateTileInChunk(1, 0, 0, 10, { objects: clone(h.neighbor.tilesData[10][0].objects) });
    h.flush();
    assert.deepEqual(h.m, after);
});

test('removing the last anomaly releases its effect and far instances only once', () => {
    const h = setup();
    h.field(null); h.flush();
    assert.equal(h.main.anomalyLODs.length, 0);
    assert.equal(h.main.farAnomalies, null);
    assert.equal(h.m.effectsDisposed, 1);
    const after = { ...h.m };
    h.field(null); h.flush();
    assert.deepEqual(h.m, after);
    h.field({ name: 'test', rank: 1, field_type: 'electric' }); h.flush();
    assert.equal(h.main.anomalyLODs.length, 1);
    assert.ok(h.main.farAnomalies);
});

test('tree changes preserve houses, fences, landmarks, their indices and anomaly LOD state', () => {
    const h = setup();
    const { houses, fences, landmarks, landmarkIndices, anomalyLODs, farAnomalies } = h.main;
    const houseIndices = h.main.objectIndices.houses;
    const fenceIndices = h.main.objectIndices.fences;
    const trees = h.main.trees;
    const active = [anomalyLODs[0]];
    h.main.farAnomalyActive = active;
    h.update([{ type: 'tree', x: 0.2 }]); h.flush();
    assert.equal(trees.disposed, true);
    assert.equal(h.main.houses, houses);
    assert.equal(h.main.fences, fences);
    assert.equal(h.main.landmarks, landmarks);
    assert.equal(h.main.landmarkIndices, landmarkIndices);
    assert.equal(h.main.objectIndices.houses, houseIndices);
    assert.equal(h.main.objectIndices.fences, fenceIndices);
    assert.equal(h.main.anomalyLODs, anomalyLODs);
    assert.equal(h.main.farAnomalies, farAnomalies);
    assert.equal(h.main.farAnomalyActive, active);
    assert.equal(h.m.effectsCreated, 0);
    assert.equal(h.m.effectsDisposed, 0);
    assert.equal(h.m.created, 2); // Tree plus one merged road surface.
    assert.equal(h.main.trees.matrices[0].position[0], 10.7);
});

test('anomaly changes preserve every ordinary mesh and road while replacing private effects', () => {
    const h = setup();
    const { trees, houses, fences, landmarks, objectIndices, roads, farAnomalies, anomalyLODs } = h.main;
    h.field({ name: 'test', rank: 4, field_type: 'fire' }); h.flush();
    assert.equal(h.main.trees, trees);
    assert.equal(h.main.houses, houses);
    assert.equal(h.main.fences, fences);
    assert.equal(h.main.landmarks, landmarks);
    assert.equal(h.main.objectIndices.trees, objectIndices.trees);
    assert.equal(h.main.roads, roads);
    assert.equal(farAnomalies.disposed, true);
    assert.notEqual(h.main.anomalyLODs[0], anomalyLODs[0]);
    assert.equal(h.m.effectsDisposed, 1);
    assert.equal(h.m.effectsCreated, 1);
    assert.equal(h.m.created, 1);
    assert.equal(h.m.roadBuilds, 0);
});

test('several groups accumulate in the same RAF while unchanged groups and array order survive', () => {
    const h = setup();
    const { fences, landmarks, anomalyLODs } = h.main;
    h.update([{ type: 'tree', rotation: 45 }, { type: 'house', name: 'selected' }]);
    h.update([{ type: 'house', color: '#123456' }], 12);
    h.field({ name: 'test', rank: 2, field_type: 'electric' });
    h.flush();
    assert.equal(h.m.rebuilds, 1);
    assert.equal(h.main.fences, fences);
    assert.equal(h.main.landmarks, landmarks);
    assert.notEqual(h.main.anomalyLODs, anomalyLODs);
    assert.equal(h.main.tilesData[10][10].objects[1].name, 'selected');
    assert.deepEqual(Array.from(h.main.objectIndices.houses[10 * 32 + 10]), [0]);
    assert.deepEqual(Array.from(h.main.objectIndices.houses[10 * 32 + 12]), [1]);
    const trees = h.main.trees, houses = h.main.houses;
    // Reordering different types changes list selection indices, not each group's instance order.
    h.update([...h.main.tilesData[10][10].objects].reverse()); h.flush();
    assert.equal(h.main.trees, trees);
    assert.equal(h.main.houses, houses);
    assert.equal(h.main.tilesData[10][10].objects[0].name, 'selected');
});

test('landmark types can be created and cleared without replacing other landmark groups', () => {
    const h = setup();
    const forest = h.main.landmarks.forest;
    const trees = h.main.trees;
    h.update([{ type: 'camp', scale: 1.3 }], 22); h.flush();
    const camp = h.main.landmarks.camp;
    const campIndices = h.main.landmarkIndices.camp;
    assert.ok(camp);
    assert.equal(h.main.landmarks.forest, forest);
    assert.equal(h.main.trees, trees);
    h.update([{ type: 'forest', rotation: 90 }], 16); h.flush();
    assert.equal(forest.disposed, true);
    assert.equal(h.main.landmarks.camp, camp);
    assert.equal(h.main.landmarkIndices.camp, campIndices);
    const newForest = h.main.landmarks.forest;
    h.update([], 16); h.flush();
    assert.equal(newForest.disposed, true);
    assert.equal(h.main.landmarks.forest, null);
    assert.ok(h.main.landmarkIndices.forest.every(indices => indices.length === 0));
    assert.equal(h.main.landmarks.camp, camp);
    assert.equal(h.m.effectsCreated, 0);
});

test('deleting the last legacy group leaves other buffers and selection indices intact', () => {
    const h = setup();
    const { trees, houses, fences } = h.main;
    const houseIndices = h.main.objectIndices.houses;
    h.update([]); h.flush();
    assert.equal(trees.disposed, true);
    assert.equal(h.main.trees, null);
    assert.ok(h.main.objectIndices.trees.every(indices => indices.length === 0));
    assert.equal(h.main.houses, houses);
    assert.equal(h.main.fences, fences);
    assert.equal(h.main.objectIndices.houses, houseIndices);
    h.update([{ type: 'tree', color: '#123456' }]); h.flush();
    assert.equal(h.main.trees.count, 1);
    assert.equal(h.main.houses, houses);
    assert.deepEqual(Array.from(h.main.objectIndices.trees[10 * 32 + 10]), [0]);
});

test('existing height handling moves retained instances and rebuilds only their anomaly group', () => {
    const h = setup();
    h.main.tilesData[10][20].objects = [{ type: 'tree' }];
    h.context.rebuildChunkObjects(h.main);
    for (const key of Object.keys(h.m)) h.m[key] = 0;
    const trees = h.main.trees;
    const houses = h.main.houses;
    h.main.groundIndices = Int16Array.from({ length: 1024 }, (_, i) => i);
    h.main.waterIndices = new Int16Array(1024).fill(-1);
    h.main.ground = { instanceMatrix: {}, instanceColor: {}, setMatrixAt() {}, setColorAt() {} };
    h.main.bounds = { max: { y: 1.5 } };
    h.context.terrainColors = { grass: 1 };
    const terrainCalls = [];
    h.context.refreshWorldTerrainNeighborhood = (...args) => terrainCalls.push(args);
    vm.runInContext(slice('function updateTileObjectsPositions(', 'function rebuildChunkObjects('), h.context);
    h.context.updateTileInChunk(0, 0, 20, 10, { height: 3 }); h.flush();
    assert.equal(h.main.trees, trees);
    assert.equal(h.main.houses, houses);
    const index = h.main.objectIndices.trees[10 * 32 + 20][0];
    assert.equal(trees.matrices[index].position[1], 3.5);
    assert.equal(h.main.anomalyLODs[0].position.y, 3.12);
    assert.equal(h.m.created, 1);
    assert.equal(h.m.effectsCreated, 1);
    assert.equal(terrainCalls[0][5], true);
});

test('chunk removal cancels dirty groups and releases retained as well as replaced buffers once', () => {
    const h = setup();
    h.update([{ type: 'tree', x: 0.2 }]); h.flush();
    const removable = { dispose() {} };
    h.main.ground = removable; h.main.water = removable;
    const meshes = [h.main.trees, h.main.houses, h.main.fences, h.main.landmarks.forest,
        h.main.farAnomalies, ...h.main.roads];
    h.context.chunkBounds = [];
    h.context.refreshWorldTerrainNeighborhood = () => {};
    vm.runInContext(slice('export function removeChunk(', 'function updateTileObjectsPositions(')
        .replace('export ', ''), h.context);
    h.field({ name: 'test', rank: 3, field_type: 'fire' });
    assert.ok(h.main.pendingObjectGroups.has('anomalies'));
    h.context.removeChunk(0, 0);
    assert.equal(h.context.chunksMap.has('0,0'), false);
    assert.equal(h.main.pendingObjectGroups, null);
    assert.equal(h.main.objectStateSnapshots, null);
    assert.equal(h.main.pendingRebuild, null);
    assert.ok(meshes.every(mesh => mesh.disposed));
    const after = { ...h.m };
    h.flush();
    assert.deepEqual(h.m, after);
});
