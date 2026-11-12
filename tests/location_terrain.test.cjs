const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');
const load = async name => import(`data:text/javascript;base64,${Buffer.from(read(name)).toString('base64')}`);

test('рельеф колей остаётся визуальным и сходится с соседним покрытием', async () => {
    const terrain = await load('locationTerrain.js');
    const { TERRAIN_MATERIALS, TERRAIN_OPTIONS } = await load('terrainMaterialCatalog.js');
    const samples = fs.readFileSync(path.join(__dirname, '../app/static/materials/terrain/local-relief.bin'));
    const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../app/static/materials/terrain/local-relief.sources.json')));
    const oldFetch = globalThis.fetch;
    globalThis.fetch = async url => {
        assert.equal(url, '/static/materials/terrain/local-relief.bin');
        return { ok: true, arrayBuffer: async () => Uint8Array.from(samples).buffer };
    };
    try {
        assert.equal(await terrain.loadLocalTerrainRelief(TERRAIN_MATERIALS), true);
    } finally {
        globalThis.fetch = oldFetch;
    }
    const tiles = [[{ terrain: 'muddy_tracks', height: 1 }, { terrain: 'grass', height: 1 }]];
    const { positions } = terrain.buildTerrainSurfaceData(tiles);
    const inner = [];
    for (let i = 0; i < positions.length; i += 3) {
        const [x, y, z] = positions.slice(i, i + 3);
        if (x === 1) assert.equal(y, 1.002);
        if (x > 0 && x < 1 && z > 0 && z < 1) inner.push(y);
    }
    assert.ok(Math.max(...inner) - Math.min(...inner) > .04);
    assert.ok(inner.every(y => y >= 1.002 && y < 1.077001));
    assert.equal(terrain.terrainSurfaceHeight(tiles, 0, 0, .5, .5), 1);
    for (const sloped of [false, true]) {
        const crossing = Array.from({ length: 3 }, (_, z) => Array.from({ length: 3 }, (_, x) => ({
            terrain: x === 0 && z === 0 ? 'grass' : 'muddy_tracks',
            height: 1 + (sloped ? x * .3 : 0),
        })));
        const smooth = terrain.buildSmoothTerrainData(crossing);
        function boundary(tileX) {
            const { positions } = terrain.buildTerrainSurfaceData(crossing, smooth,
                { minX: tileX, maxX: tileX, minZ: 1, maxZ: 1 });
            const points = new Map();
            for (let i = 0; i < positions.length; i += 3) {
                const [x, y, z] = positions.slice(i, i + 3);
                if (x === 1) points.set(z, y);
            }
            return [...points].sort((a, b) => a[0] - b[0]);
        }
        assert.ok(boundary(0).length > 2);
        assert.deepEqual(boundary(0), boundary(1), 'Соседи делят край, примыкающий к углу с другим материалом');
    }
    const terrainKinds = ['grass', 'rock', 'sand', 'swamp', ...TERRAIN_OPTIONS.map(([kind]) => kind)];
    const profiles = Object.keys(manifest.sources);
    assert.deepEqual(profiles.sort(), terrainKinds.filter(kind => terrain.hasLocalTerrainRelief(kind)).sort());
    assert.equal(samples.length, profiles.length * 64 * 64);
    for (const kind of ['grass', 'rock', 'sand', 'swamp', 'muddy_tracks']) assert.ok(profiles.includes(kind), kind);
    for (const kind of profiles) {
        const area = Array.from({ length: 3 }, () => Array.from({ length: 3 }, () => ({ terrain: kind, height: 1 })));
        const { positions } = terrain.buildTerrainSurfaceData(area);
        assert.ok(positions.some((height, index) => index % 3 === 1 && height > 1.004), kind);
    }
    for (const kind of ['shallow_water', 'brown_mud_leaves_01', 'water']) assert.equal(terrain.hasLocalTerrainRelief(kind), false, kind);
});

test('neighboring tiles differing by one meter share their edge without a manual surface flag', async () => {
    const { buildSmoothTerrainData, isSmoothTerrainTile } = await load('locationTerrain.js');
    const tiles = [[
        { terrain: 'grass', height: 1 },
        { terrain: 'grass', height: 2 },
    ]];
    assert.equal(isSmoothTerrainTile(tiles, 0, 0), true);
    assert.equal(isSmoothTerrainTile(tiles, 1, 0), true);
    const data = buildSmoothTerrainData(tiles);
    assert.equal(data.tileByFace.length, data.positions.length / 9);
    assert.equal(data.tileByVertex.length, data.positions.length / 3);
    const top = [];
    for (let i = 0; i < data.positions.length; i += 3) {
        const [x, y, z] = data.positions.slice(i, i + 3);
        if (x === 1 && y > 0) top.push(y);
        if (x === 0.5 && z === 0.5) assert.equal(y, 1);
        if (x === 1.5 && z === 0.5) assert.equal(y, 2);
    }
    assert.ok(top.length > 0);
    assert.ok(top.every(y => y === 1.5));
    assert.ok(data.foundationByTile.get('0,0') < 1);
    assert.ok(data.foundationByTile.get('0,0') > 0.9);
    assert.ok(data.foundationByTile.get('1,0') < 1.5);
});

test('a chunk slope uses a loaded neighbor without generating its tiles twice', async () => {
    const { buildSmoothTerrainData } = await load('locationTerrain.js');
    const tiles = Array.from({ length: 4 }, () => Array.from({ length: 4 }, () => (
        { terrain: 'grass', height: 1 }
    )));
    tiles[1][3].height = 3;
    const data = buildSmoothTerrainData(tiles, (x, z) => x > 0 && x <= 2 && z > 0 && z <= 2);
    assert.ok(data.tileByFace.length > 0);
    assert.ok(data.tileByFace.every(({ x, z }) => x <= 2 && z <= 2));
    const sharedEdgeHeights = [];
    for (let index = 0; index < data.positions.length; index += 3) {
        if (data.topByVertex[index / 3] && data.positions[index] === 3
            && data.positions[index + 2] === 1.5) sharedEdgeHeights.push(data.positions[index + 1]);
    }
    assert.ok(sharedEdgeHeights.length > 0);
    assert.ok(sharedEdgeHeights.every(value => value === 2));
});

test('grass height sampler follows the rendered slope and caches its ring', async () => {
    const { createTerrainSurfaceHeightSampler } = await load('locationTerrain.js');
    const sample = createTerrainSurfaceHeightSampler([[
        { terrain: 'grass', height: 1 }, { terrain: 'grass', height: 3 },
    ]]);
    assert.equal(sample(0.5, 0.5), 1);
    assert.equal(sample(1, 0.5), 2);
    assert.equal(sample(1.5, 0.5), 3);
    assert.ok(sample(0.75, 0.5) > 1 && sample(0.75, 0.5) < 2);
});

test('a trench at -3 keeps its top and walls below the old zero plane', async () => {
    const { buildSmoothTerrainData, createTerrainSurfaceHeightSampler,
        terrainFoundationHeight } = await load('locationTerrain.js');
    const tiles = [[{ terrain: 'sand', height: 1 },
        { terrain: 'sand', height: 0 }, { terrain: 'sand', height: -3 }]];
    const foundation = terrainFoundationHeight(tiles);
    assert.equal(foundation, -3.05);
    const sample = createTerrainSurfaceHeightSampler(tiles);
    assert.equal(sample(1.5, 0.5), 0);
    assert.equal(sample(2.5, 0.5), -3);
    assert.ok(sample(2, 0.5) < 0);
    const mesh = buildSmoothTerrainData(tiles, () => true, foundation);
    assert.ok(mesh.positions.some((value, index) => index % 3 === 1 && value === -3.05));
    assert.ok(mesh.topByVertex.some((top, index) => top
        && mesh.positions[index * 3 + 1] === -3));
    assert.ok(mesh.positions.every(Number.isFinite));
    assert.ok(Array.from(mesh.normals).every(Number.isFinite));
    assert.equal(terrainFoundationHeight([[{ terrain: 'grass', height: 0.5 }]]), 0);
    assert.equal(terrainFoundationHeight([[{ terrain: 'grass', height: null }]]), 0);
    assert.equal(createTerrainSurfaceHeightSampler([[{ terrain: 'grass', height: null }]])(0.5, 0.5), 1);
    tiles[0][1].height = 1;
    tiles[0][2].height = 1;
    assert.equal(terrainFoundationHeight(tiles), 0);
    assert.equal(buildSmoothTerrainData(tiles).positions.length, 0);
});

test('local height controls and brush allow negative land without changing the global editor', () => {
    const scene = read('locationScene.js');
    const main = read('main.js');
    const shortcuts = read('ui_interactions.js');
    const lobby = fs.readFileSync(path.join(__dirname, '../app/templates/lobby.html'), 'utf8');
    assert.match(scene, /Math\.max\(tile\.terrain === 'water' \? 0\.5 : -3\.0,/);
    assert.match(scene, /refreshTerrainFoundation\(\)/);
    assert.match(main, /id="loc-edit-height" min="-3\.0" max="3\.0"/);
    assert.match(shortcuts, /Math\.max\(-3\.0, current - 0\.1\)/);
    assert.match(lobby, /id="tile-height" min="0\.5" max="3\.0"/);
    assert.match(lobby, /id="tile-edit-height" min="0\.5" max="3\.0"/);
});

test('steep hill rounds its crest without opening a gap at the shared edge', async () => {
    const { buildSmoothTerrainData, createTerrainSurfaceHeightSampler, buildFogSurfaceData } = await load('locationTerrain.js');
    const tiles = [[{ terrain: 'grass', height: 1 }, { terrain: 'grass', height: 3 }]];
    const sample = createTerrainSurfaceHeightSampler(tiles);
    assert.equal(sample(1, 0.5), 2);
    assert.ok(sample(1.25, 0.5) > 2.5);
    assert.ok(sample(0.75, 0.5) < 1.5);
    const mesh = buildSmoothTerrainData(tiles);
    assert.ok(mesh.tileByFace.filter(({ x }) => x === 1).length > 8);
    assert.equal(mesh.normals.length, mesh.positions.length);
    for (let index = 0; index < mesh.topByVertex.length; index++) {
        if (mesh.topByVertex[index]) assert.ok(mesh.normals[index * 3 + 1] > 0);
    }
    const crest = new Map();
    for (let index = 0; index < mesh.topByVertex.length; index++) {
        if (!mesh.topByVertex[index]) continue;
        const point = mesh.positions.slice(index * 3, index * 3 + 3);
        const normal = Array.from(mesh.normals.slice(index * 3, index * 3 + 3));
        const key = point.join(':');
        if (crest.has(key)) assert.deepEqual(normal, crest.get(key));
        else crest.set(key, normal);
    }
    const fog = buildFogSurfaceData(tiles);
    assert.ok(fog.tileIndices[1].length > 24);
    const diagonal = [[{ terrain: 'grass', height: 3 }, { terrain: 'grass', height: 1 }],
        [{ terrain: 'grass', height: 1 }, { terrain: 'grass', height: 1 }]];
    const diagonalMesh = buildSmoothTerrainData(diagonal);
    assert.ok(diagonalMesh.tileByFace.filter(({ x, z }) => x === 1 && z === 1).length > 8);
});

test('sand cliffs have finer top bands without changing their shared edge height', async () => {
    const { buildSmoothTerrainData, createTerrainSurfaceHeightSampler } = await load('locationTerrain.js');
    const sand = [[{ terrain: 'sand', height: 1 }, { terrain: 'sand', height: 3 }]];
    const grass = [[{ terrain: 'grass', height: 1 }, { terrain: 'grass', height: 3 }]];
    const sandMesh = buildSmoothTerrainData(sand);
    const grassMesh = buildSmoothTerrainData(grass);
    assert.ok(sandMesh.tileByFace.length > grassMesh.tileByFace.length);
    assert.equal(createTerrainSurfaceHeightSampler(sand)(1, 0.5), 2);
});

test('diagonal height changes include flat corner neighbors in the smooth mesh', async () => {
    const { buildSmoothTerrainData, isSmoothTerrainTile } = await load('locationTerrain.js');
    const tiles = [
        [{ terrain: 'sand', height: 1 }, { terrain: 'sand', height: 1 }],
        [{ terrain: 'sand', height: 1 }, { terrain: 'rock', height: 3 }],
    ];
    assert.equal(isSmoothTerrainTile(tiles, 0, 0), true);
    const data = buildSmoothTerrainData(tiles);
    assert.ok(data.tileByFace.some(({ x, z }) => x === 0 && z === 0));
});

test('isolated flat terrain stays outside the smooth mesh; taller steps still blend', async () => {
    const { buildSmoothTerrainData, isSmoothTerrainTile } = await load('locationTerrain.js');
    const data = buildSmoothTerrainData([[{ terrain: 'rock', height: 2.6 }]]);
    assert.equal(data.positions.length, 0);
    const cliff = [[{ terrain: 'rock', height: 1 }, { terrain: 'rock', height: 2.01 }]];
    assert.equal(isSmoothTerrainTile(cliff, 0, 0), true);
    assert.equal(isSmoothTerrainTile(cliff, 1, 0), true);
    const slope = buildSmoothTerrainData(cliff);
    assert.ok(slope.positions.length > 0);
    assert.ok(slope.positions.some((height, index) => index % 3 === 1 && height === 1.505));
    const scene = read('locationScene.js');
    assert.match(scene, /isSmoothTerrainTile\(currentLocationData\.tiles_data, x, z\)/);
    assert.match(scene, /hit\?\.object\?\.userData\?\.tileByFace\?\.\[hit\.faceIndex\]/);
    assert.match(scene, /rebuildSmoothTerrainMesh\(\)/);
});

test('a changed height also refreshes neighboring tile geometry', () => {
    const scene = read('locationScene.js');
    assert.match(scene, /for \(let dz = -1; dz <= 1; dz\+\+\)/);
    assert.match(scene, /neighbors\.add\(nz \* currentLocationData\.grid_width \+ nx\)/);
    assert.match(scene, /for \(const index of neighbors\)[\s\S]*?updateTileInstance/);
    assert.match(scene, /updateTileCubes\(changed\)/);
    assert.match(scene, /if \(work\.smooth && !localSculpt\) rebuildSmoothTerrainMesh\(true\)/);
});

test('fast brush movement fills intermediate cells and combines work before rendering', () => {
    const scene = read('locationScene.js');
    const source = scene.match(/function brushStrokeTiles\([\s\S]*?\r?\n}\r?\n\r?\nfunction setupCharacterDragging/);
    assert.ok(source);
    const line = new Function(`${source[0].replace(/\r?\n\r?\nfunction setupCharacterDragging$/, '')}; return brushStrokeTiles;`)();
    assert.deepEqual(line(2, 2, 5, 2), [[2, 2], [3, 2], [4, 2], [5, 2]]);
    assert.deepEqual(line(2, 2, 2, 5), [[2, 2], [2, 3], [2, 4], [2, 5]]);
    assert.equal(line(1, 1, 4, 3).length, 4);
    assert.match(scene, /applyLocationBrush\(path, null, updates, brushRadius\)/);
    assert.match(scene, /flushPendingTerrainRefresh\(\);[\s\S]*?renderer\.render\(scene, camera\)/);
    assert.match(scene, /if \(work\.surface && !localSculpt\) rebuildLocationTerrainSurface\(true, true\)/);
    assert.match(scene, /reusableMesh\.geometry\.dispose\(\);\s*reusableMesh\.geometry = geometry/);
});

test('steep top faces retain terrain blending while exposed vertical sides do not', async () => {
    const { buildSmoothTerrainData } = await load('locationTerrain.js');
    const mesh = buildSmoothTerrainData([[{ terrain: 'grass', height: 1 },
        { terrain: 'sand', height: 3 }]]);
    assert.equal(mesh.topByVertex.length, mesh.positions.length / 3);
    assert.ok(mesh.topByVertex.includes(1));
    assert.ok(mesh.topByVertex.includes(0));
    const scene = read('locationScene.js');
    assert.match(scene, /setAttribute\('terrainTop'/);
    assert.doesNotMatch(scene, /applyTerrainBlend\(material, locationBlendMapUniform, locationBlendSizeUniform, true, true\)/);
});

test('terrain grid follows flat tiles, smooth slopes, and cliff tops', async () => {
    const { buildTerrainGridData } = await load('locationTerrain.js');
    const flat = buildTerrainGridData([[{ terrain: 'grass', height: 1 }]]);
    assert.equal(flat.positions.length / 6, 8);
    assert.ok(flat.positions.filter((_, index) => index % 3 === 1).every(height => height === 1.025));
    assert.equal(flat.tileByVertex.length, flat.positions.length / 3);

    const slope = buildTerrainGridData([[
        { terrain: 'grass', height: 1 }, { terrain: 'grass', height: 2 },
    ]]);
    const sharedEdgeHeights = [];
    for (let index = 0; index < slope.positions.length; index += 3) {
        if (slope.positions[index] === 1) sharedEdgeHeights.push(slope.positions[index + 1]);
    }
    assert.ok(sharedEdgeHeights.length > 0);
    assert.ok(sharedEdgeHeights.every(height => height === 1.525));

    const cliff = buildTerrainGridData([[
        { terrain: 'grass', height: 1 }, { terrain: 'grass', height: 2.1 },
    ]]);
    const cliffHeights = cliff.positions.filter((_, index) => index % 3 === 1);
    assert.ok(cliffHeights.includes(1.025));
    assert.ok(cliffHeights.includes(2.125));
});

test('terrain grid omits covered floor tiles and rebuilds when surfaces change', async () => {
    const { buildTerrainGridData } = await load('locationTerrain.js');
    const tiles = [[{ terrain: 'grass', height: 1 }, { terrain: 'sand', height: 1 }]];
    const grid = buildTerrainGridData(tiles, x => x !== 0);
    assert.ok(grid.positions.length > 0);
    assert.ok(grid.tileByVertex.every(tile => tile.x === 1));
    const scene = read('locationScene.js');
    assert.match(scene, /const uncovered = \(x, z\) => !floors\.some/);
    const start = scene.indexOf('function flushPendingTerrainRefresh()');
    const end = scene.indexOf('function updateSmoothTerrainColors(', start);
    assert.ok(start >= 0 && end > start);
    const flush = flags => {
        const calls = [];
        calls.surfaceEvents=[];
        const mark = name => (...args) => calls.push({ name, args });
        const context = vm.createContext({
            window:{dispatchEvent:event=>calls.surfaceEvents.push(event.type)},
            Event:class{constructor(type){this.type=type;}},
            pendingTerrainRefresh: { changed: new Map([['1,0', { x: 1, z: 0 }]]), ...flags },
            scene: {}, currentLocationData: { grid_width: 2 }, editorSelection: null, objectEdit: null,
            characterModels: new Map(),
            rebuildSmoothTerrainMesh: mark('smooth'), updateSmoothTerrainColors: mark('smoothColors'),
            updateLocationTerrainSurfaceTexture: mark('texture'),
            rebuildLocationTerrainSurface: mark('surface'), rebuildWaterSurfaceMesh: mark('water'),
            rebuildLocationGrid: mark('grid'), updateLocationGridColors: mark('gridColors'),
            rebuildGrassDetailMesh: mark('grass'), rebuildLocationRoofs: mark('roofs'),
            clearFogOverlay: mark('fogGeometry'), applyLocationLighting: mark('lighting'),
            applyFogOfWar: mark('fog'), updateEditorSelectionVisual: mark('selection'),
        });
        vm.runInContext(scene.slice(start, end), context);
        context.flushPendingTerrainRefresh();
        assert.equal(context.pendingTerrainRefresh, null);
        return calls;
    };
    const heightCalls = flush({ smooth: true, surface: true, water: true, grid: true });
    assert.deepEqual(heightCalls.map(call => call.name), ['smooth', 'surface', 'water', 'grid']);
    assert.deepEqual(heightCalls[1].args, [true, true]);
    assert.deepEqual(heightCalls.surfaceEvents,['map-surface-changed']);
    const paintCalls = flush({ terrainColor: true, texture: true, grass: true });
    assert.deepEqual(paintCalls.map(call => call.name),
        ['smoothColors', 'texture', 'gridColors', 'grass']);
    assert.equal(paintCalls[1].args[0][0].x, 1);
    assert.deepEqual(paintCalls.surfaceEvents,[]);
    assert.match(scene, /updateLocationGridColors\(\)/);
    assert.match(scene, /locationGridMesh\.raycast = \(\) => \{\}/);
    assert.doesNotMatch(scene, /gridLines\.push\(x, -0\.1/);
});

test('one connected water surface follows the median shoreline and manual height remains available', async () => {
    const { waterSurfaceAdjustments } = await load('locationTerrain.js');
    const tiles = [
        [{ terrain: 'grass', height: 1 }, { terrain: 'grass', height: 1.1 }, { terrain: 'grass', height: 3 }],
        [{ terrain: 'grass', height: 1 }, { terrain: 'water', height: 0.65 }, { terrain: 'water', height: 0.65 }],
        [{ terrain: 'grass', height: 1 }, { terrain: 'grass', height: 1.05 }, { terrain: 'grass', height: 3 }],
    ];
    const changes = waterSurfaceAdjustments(tiles);
    assert.equal(changes.length, 2);
    assert.equal(changes[0].height, changes[1].height);
    assert.ok(changes[0].height >= 1 && changes[0].height <= 1.1);
    assert.equal(tiles[1][1].height, 0.65);
    const allWater = waterSurfaceAdjustments([[
        { terrain: 'water', height: 0.7 }, { terrain: 'water', height: 1.3 },
    ]]);
    assert.equal(allWater.length, 2);
    assert.ok(allWater.every(item => item.height === 1));
    const separate = waterSurfaceAdjustments([[
        { terrain: 'grass', height: 1 }, { terrain: 'water', height: 0.5 },
        { terrain: 'grass', height: 1 }, { terrain: 'water', height: 0.3 },
        { terrain: 'grass', height: 1 },
    ]], new Set(['1,0']));
    assert.deepEqual(separate.map(item => item.x), [1]);
    const scene = read('locationScene.js');
    assert.match(scene, /if \(waterTopologyChanged && changed\.length\) \{\s*const changedByTile/);
});

test('floor grid lies above the slab and is clipped to its footprint', async () => {
    const { buildFloorGridData } = await load('locationTerrain.js');
    const floor = {
        x: 2.5, z: 2.5, width: 2.4, depth: 2.4, top: 1.005,
        axis: { x: 1, z: 0 }, normal: { x: 0, z: 1 },
    };
    const grid = buildFloorGridData([floor]);
    assert.equal(grid.positions.length / 6, 4);
    assert.ok(grid.positions.filter((_, index) => index % 3 === 1)
        .every(y => Math.abs(y - 1.03) < 1e-8));
    assert.ok(grid.positions.filter((_, index) => index % 3 === 0).every(x => x >= 1.3 && x <= 3.7));
    assert.ok(grid.positions.filter((_, index) => index % 3 === 2).every(z => z >= 1.3 && z <= 3.7));
    assert.ok(grid.tileByVertex.every(vertex => vertex.floor));
    const rotated = buildFloorGridData([{
        ...floor, axis: { x: Math.SQRT1_2, z: -Math.SQRT1_2 },
        normal: { x: Math.SQRT1_2, z: Math.SQRT1_2 },
    }]);
    assert.ok(rotated.positions.length > 0);
    assert.equal(rotated.tileByVertex.length, rotated.positions.length / 3);
    const scene = read('locationScene.js');
    assert.match(scene, /const floorData = buildFloorGridData\(floors\)/);
    assert.match(scene, /const color = floor \? floorGridColor/);
    assert.match(scene, /\['wall', 'door', 'window', 'floor', 'stairs', 'ladder', 'hatch', 'floor_opening'\]\.includes\(source\.type\)/);
    assert.match(scene, /if \(preset === 'floor'\) return \{ width: structureWidth, depth: structureDepth, height: 0\.12 \}/);
});

test('fog surface follows smoothed tile heights without gaps on shared edges', async () => {
    const { buildFogSurfaceData } = await load('locationTerrain.js');
    const tiles = [[
        { terrain: 'grass', height: 1 }, { terrain: 'grass', height: 2 },
    ]];
    const fog = buildFogSurfaceData(tiles);
    assert.equal(fog.tileIndices.length, 2);
    assert.equal(fog.tileIndices[0].length, 24);
    assert.equal(fog.tileIndices[1].length, 24);
    const shared = [];
    for (let index = 0; index < fog.positions.length; index += 3) {
        if (fog.positions[index] === 1) shared.push(fog.positions[index + 1]);
    }
    assert.ok(shared.length > 0);
    assert.ok(shared.every(y => Math.abs(y - 1.545) < 1e-8));
    const scene = read('locationScene.js');
    assert.match(scene, /fogSurfaceData \?\?= buildFogSurfaceData\(currentLocationData\.tiles_data, waterFogSurfaces\)/);
    assert.match(scene, /if \(work\.fogGeometry && !localSculpt\) clearFogOverlay\(\)/);
});

function viewToggleHarness(isGM, exitWidth = 145.2) {
    const scene = read('locationScene.js');
    const start = scene.indexOf('function getLocationViewToggleGroup()');
    const end = scene.indexOf('export function destroyLocationScene()', start);
    assert.ok(start >= 0 && end > start);
    const nodes = new Map();
    const makeElement = () => ({
        id: '', style: {}, attributes: {}, children: [],
        setAttribute(name, value) { this.attributes[name] = value; },
        appendChild(child) { this.children.push(child); nodes.set(child.id, child); },
        prepend(child) { this.children.unshift(child); nodes.set(child.id, child); },
        remove() { nodes.delete(this.id); },
        getBoundingClientRect() { return { width: exitWidth }; },
    });
    nodes.set('location-container', makeElement());
    nodes.set('exit-location-btn', makeElement());
    const calls = { grid: 0, roof: 0 };
    const locationData = { tiles_data: [[{ terrain: 'grass' }]] };
    const context = vm.createContext({
        document: { getElementById: id => nodes.get(id), createElement: makeElement },
        window: { isGM }, locationGridVisible: false, locationRoofsVisible: true,
        currentLocationData: locationData,
        rebuildLocationGrid() { calls.grid++; },
        updateRoofVisibility() { calls.roof++; },
    });
    vm.runInContext(scene.slice(start, end).replaceAll('export function', 'function'), context);
    return { context, nodes, calls, locationData };
}

test('players and GMs can toggle the terrain grid without changing location data', () => {
    const main = read('main.js');
    const start = main.indexOf('window.enterLocation = async function');
    const end = main.indexOf('\n};', start);
    assert.ok(start >= 0 && end > start);
    const entry = main.slice(start, end);
    const gmTools = entry.search(/if\s*\(window\.isGM\b/);
    assert.ok(gmTools > 0);
    assert.match(entry.slice(0, gmTools), /addLocationRoofButton\(\)/);
    assert.match(entry.slice(0, gmTools), /addLocationGridButton\(\)/);
    assert.equal([...entry.matchAll(/addLocationGridButton\(\)/g)].length, 1);
    for (const isGM of [false, true]) {
        const { context, calls, locationData } = viewToggleHarness(isGM);
        const before = JSON.stringify(locationData);
        const button = context.addLocationGridButton();
        assert.equal(button.attributes['aria-pressed'], 'false');
        button.onclick();
        assert.equal(button.attributes['aria-pressed'], 'true');
        assert.equal(calls.grid, 1);
        assert.equal(JSON.stringify(locationData), before);
    }
    assert.match(main, /document\.getElementById\('location-grid-btn'\)\?\.remove\(\)/);
});

test('roof and grid toggles share a gap and stay beside the exit for both roles', () => {
    for (const isGM of [false, true]) {
        const { context, nodes, calls } = viewToggleHarness(isGM);
        const grid = context.addLocationGridButton();
        const roof = context.addLocationRoofButton();
        const group = nodes.get('location-view-toggle-group');
        assert.equal(group.style.right, isGM ? '271px' : '174px');
        assert.equal(group.style.gap, '8px');
        assert.deepEqual(group.children.map(child => child.id),
            ['location-roof-btn', 'location-grid-btn']);
        roof.onclick();
        assert.equal(roof.attributes['aria-pressed'], 'false');
        assert.equal(calls.roof, 1);
        assert.equal(grid.attributes['aria-pressed'], 'false');
    }
    assert.match(read('main.js'),
        /document\.getElementById\('location-view-toggle-group'\)\?\.remove\(\)/);
});

test('switching between global map and location hides only global UI and restores it', () => {
    const main = read('main.js');
    const scene = read('locationScene.js');
    const css = fs.readFileSync(path.join(__dirname, '../app/static/style.css'), 'utf8');
    const start = main.indexOf('function setLocationViewActive(');
    const end = main.indexOf('window.enterLocation = async function', start);
    assert.ok(start >= 0 && end > start);
    const classes = new Set();
    const nodes = new Map([
        ['canvas-container', { style: { display: 'block' } }],
        ['location-container', { style: { display: 'none' } }],
    ]);
    const restored=[];
    for(const id of ['chat-toggle','common-tools-toggle','edit-toggle'])nodes.set(id,{id});
    const menu={insertBefore:(node,anchor)=>restored.push([node.id,anchor.id])};
    const context = vm.createContext({ closeSettings:()=>{}, document: {
        querySelector:selector=>selector==='.lobby-menu'?menu:null,
        body: { classList: { toggle(name, enabled) {
            if (enabled) classes.add(name);
            else classes.delete(name);
        } } },
        getElementById: id => nodes.get(id),
    } });
    vm.runInContext(main.slice(start, end), context);
    for (let iteration = 0; iteration < 3; iteration++) {
        context.setLocationViewActive(true);
        assert.equal(classes.has('location-view-active'), true);
        assert.equal(nodes.get('canvas-container').style.display, 'none');
        assert.equal(nodes.get('location-container').style.display, 'block');
        context.setLocationViewActive(false);
        assert.equal(classes.has('location-view-active'), false);
        assert.equal(nodes.get('canvas-container').style.display, 'block');
        assert.equal(nodes.get('location-container').style.display, 'none');
    }
    assert.equal(restored.length,6);
    assert.deepEqual(restored.slice(0,2),[['chat-toggle','edit-toggle'],['common-tools-toggle','edit-toggle']]);
    assert.match(main, /setLocationViewActive\(true\);\s*hideGlobalCanvas\(\)/);
    assert.match(main, /setLocationViewActive\(false\);\s*showGlobalCanvas\(\)/);
    for (const selector of ['.lobby-menu', '#settings-panel', '#tile-info', '.world-group-context-menu']) {
        assert.ok(css.includes(`body.location-view-active ${selector}`));
    }
    assert.match(css, /body\.location-view-active \.world-group-context-menu \{\s*display: none !important;/);
    assert.doesNotMatch(css, /body\.location-view-active #panel-tools/);
    assert.match(scene, /createCompatibleWebGLRenderer\(THREE, \{ antialias: true, alpha: false \}\)/);
});

test('flat terrain and water tiles fill their cells without permanent grid gaps', () => {
    const scene = read('locationScene.js');
    assert.match(scene, /const geometry = new THREE\.BoxGeometry\(1, 1, 1\)/);
    assert.match(scene, /const waterMaterial = new THREE\.MeshStandardMaterial\(\{ color: 0xffffff \}\)/);
    assert.match(scene, /WATER_SIDE_COLORS = \{ grass: 0x183c4a/);
    assert.match(scene, /const waterBoxGeometry = geometry\.clone\(\)/);
    assert.match(scene, /waterTileMesh = new THREE\.InstancedMesh\(waterBoxGeometry, \[/);
    assert.match(scene, /Math\.max\(0\.05, waterLevelForTile\(tile, waterKind\) - 0\.01\)/);
    assert.match(scene, /Math\.max\(0\.05, height - terrainFoundationY\)/);
    assert.doesNotMatch(scene, /new THREE\.BoxGeometry\(0\.98, 1, 0\.98\)/);
});

test('floor is passable and does not charge climb movement in the client', () => {
    const scene = read('locationScene.js');
    const context=vm.createContext({getObjectTraversalHeight:()=>2.4});
    const setStart=scene.indexOf('const PASSABLE_OBJECT_TYPES =');
    vm.runInContext(scene.slice(setStart,scene.indexOf(';',setStart)+1),context);
    const start=scene.indexOf('function getMovementObjectProfile(');
    vm.runInContext(scene.slice(start,scene.indexOf('\n}',start)+2),context);
    for(const properties of [{},{passable:false},{traversal_category:'blocked'}]){
        const result=context.getMovementObjectProfile({type:'floor',properties});
        assert.equal(result.blocked,false);assert.equal(result.climbCost,0);
    }
});

test('terrain cap uses only a constant depth bias while floor materials stay independent', () => {
    const scene = read('locationScene.js');
    const capStart = scene.indexOf('function createLocationGroundMaterial(');
    const capEnd = scene.indexOf('function rebuildLocationTerrainSurface(', capStart);
    assert.ok(capStart >= 0 && capEnd > capStart);
    const map = {};
    const context = vm.createContext({ terrainSurfaceTexture: map, locationDarknessTexture: null,
        currentLocationData: { grid_width: 8, grid_height: 8 }, locationTerrainMaterials: null,
        applySurfaceDarkness() {}, ensureTerrainGrassDetail() {},
        THREE: { DoubleSide: 2, Vector2: class {},
            MeshStandardMaterial: class {
                constructor(options) { Object.assign(this, { depthTest: true, depthWrite: true }, options); }
            } },
    });
    vm.runInContext(scene.slice(capStart, capEnd), context);
    const cap = context.createLocationGroundMaterial(true);
    assert.equal(cap.map, map);
    assert.equal(cap.polygonOffset, true);
    assert.equal(cap.polygonOffsetFactor, 0);
    assert.equal(cap.polygonOffsetUnits, -1);
    assert.equal(cap.depthTest, true);
    assert.equal(cap.depthWrite, true);
    assert.equal(context.createLocationGroundMaterial().polygonOffset, false);
    assert.match(scene, /const material = createLocationGroundMaterial\(true\)/);
    const object = scene.slice(scene.indexOf('function createLocationObjectMesh('),
        scene.indexOf('function darknessFactorAt('));
    assert.match(object, /const material = hasSeparateMaterials \? null\s*: new THREE\.MeshStandardMaterial/);
    assert.match(object, /if \(usePbrMaterial && material\) \{\s*applyLocationPbrMaterial\(material, asset\.visual\.maps, renderer\)/);
});

test('covered lower terrain tops do not draw color or depth while keeping pick geometry', () => {
    const scene = read('locationScene.js');
    assert.match(scene, /landTopMaterial = new THREE\.MeshBasicMaterial\(\{ colorWrite: false, depthWrite: false \}\)/);
    assert.match(scene, /locationTileMesh = new THREE\.InstancedMesh\(geometry, \[\s*material, material, landTopMaterial, material, material, material,/);
    assert.match(scene, /tileCubes = \[locationTileMesh, waterTileMesh\]/);
    assert.match(scene, /tileCubes\.push\(smoothTerrainMesh\)/);
    const smooth = scene.slice(scene.indexOf('function rebuildSmoothTerrainMesh('),
        scene.indexOf('function updateLocationTerrainSurfaceTexture('));
    assert.match(smooth, /geometry\.setAttribute\('terrainTop', new THREE\.Float32BufferAttribute\(data\.topByVertex, 1\)\)/);
    const shaderSetup = smooth.slice(smooth.indexOf('    material.customProgramCacheKey ='),
        smooth.indexOf('    smoothTerrainMesh = new THREE.Mesh('));
    const material = {};
    vm.runInNewContext(`(function(material) { ${shaderSetup} })(material)`, { material });
    const shader = {
        vertexShader: '#include <common>\n#include <begin_vertex>',
        fragmentShader: '#include <common>\n#include <clipping_planes_fragment>',
    };
    material.onBeforeCompile(shader);
    assert.match(shader.vertexShader, /attribute float terrainTop; varying float vTerrainTop;/);
    assert.match(shader.vertexShader, /vTerrainTop = terrainTop;/);
    assert.match(shader.fragmentShader, /if \(vTerrainTop > 0\.5\) discard;/);
    assert.match(shader.fragmentShader, /#include <clipping_planes_fragment>/);
    assert.match(smooth, /smoothTerrainMesh\.userData\.tileByFace = data\.tileByFace/);
});
