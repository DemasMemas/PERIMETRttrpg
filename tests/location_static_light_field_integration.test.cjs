const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');
const url = text => 'data:text/javascript;base64,' + Buffer.from(text).toString('base64');
const integration = import(url(read('locationStaticLightField.js')
    .replace("from './locationArchitecture.js'", `from '${url(read('locationArchitecture.js'))}'`)
    .replace("from './locationDirectionalLightFieldPrototype.js'", `from '${url(read('locationDirectionalLightFieldPrototype.js'))}'`)));
const three = { DataTexture: class {
    constructor(data, width, height) { this.image = { data, width, height }; }
    dispose() { this.disposed = true; }
}, RGBAFormat: 1, ClampToEdgeWrapping: 2, LinearFilter: 3 };
const lamp = (overrides = {}) => ({ kind: 'point', x: 3, z: 3, y: 1.5,
    height: 1.5, color: '#ffffff', intensity: 2, distance: 7, ...overrides });
const data = objects => ({ grid_width: 12, grid_height: 6, objects });
function wall() { return { id: 1, type: 'wall', tile_x: 5, tile_y: 3,
    properties: { building_id: 'house', level: 0, rotation: -Math.PI / 2,
        offset_z: -0.5, dimensions: { width: 6, depth: .2, height: 2.4 } } }; }
function door(open) { return { id: 2, type: 'door', tile_x: 5, tile_y: 3,
    properties: { building_id: 'house', level: 0, rotation: -Math.PI / 2,
        offset_z: -.5, is_open: open, dimensions: { width: 1, depth: .18, height: 2 } } }; }

test('all saved point and spot lamps enter the field, including outside the old dynamic pool', async () => {
    const { createLocationDirectionalLightField } = await integration;
    const sources = Array.from({ length: 20 }, (_, i) => lamp({ x: 1 + i % 10,
        kind: i % 3 ? 'point' : 'spot', azimuth: 0 }));
    const c = createLocationDirectionalLightField(three, data([]), sources, () => 0);
    assert.equal(c.inputs.lamps.length, 20);
    assert.ok(c.stats.lampStats.every(item => item.route === 'field' && item.contributions > 0));
    assert.ok(c.field.sample(4, 0, 3, { x: 0, y: 1, z: 0 })[0] > 0);
    c.dispose(); assert.equal(c.texture.disposed, true);
});

test('door and wall edits update the existing atlas and preserve fractional source values', async () => {
    const { createLocationDirectionalLightField } = await integration;
    const c = createLocationDirectionalLightField(three, data([wall(), door(false)]), [lamp()], () => 0);
    const texture = c.texture, pixels = c.field.pixels;
    assert.deepEqual(c.field.sample(7, 0, 3, { x: 0, y: 1, z: 0 }), [0, 0, 0]);
    assert.equal(c.update(data([wall(), door(true)]), [lamp({ x: 3.125, intensity: 2.345 })]), true);
    assert.equal(c.texture, texture); assert.equal(c.field.pixels, pixels);
    assert.equal(c.inputs.lamps[0].x, 3.125); assert.equal(c.inputs.lamps[0].intensity, 2.345);
    assert.ok(c.field.sample(7, 0, 3, { x: 0, y: 1, z: 0 })[0] > 0);
    c.update(data([wall(), door(false)]), [lamp()]);
    assert.deepEqual(c.field.sample(7, 0, 3, { x: 0, y: 1, z: 0 }), [0, 0, 0]);
    c.dispose();
});

test('basements, decor and explicit lamp levels are present; adding a floor requests a new atlas', async () => {
    const { createLocationDirectionalLightField, locationStaticLightFieldInputs } = await integration;
    const input = { ...data([{ type: 'wall', tile_x: 1, tile_y: 1,
        properties: { level: -1, dimensions: { width: 1, height: 2, depth: .2 } } }]),
        tiles_data: [[{ objects: [{ type: 'box', level: 1 }] }]] };
    const prepared = locationStaticLightFieldInputs(input, [lamp({ level: 2 })], () => 0);
    assert.deepEqual(prepared.levels.map(l => l.id), [-1, 0, 1, 2]);
    const c = createLocationDirectionalLightField(three, input, [lamp({ level: 2 })], () => 0);
    assert.equal(c.stats.lampStats[0].route, 'field');
    const pixels = c.field.pixels;
    assert.equal(c.update(input, [lamp({ level: 3 })]), false);
    assert.equal(c.field.pixels, pixels);
    c.dispose();
});

test('texture/memory checks precede allocation and do not choose a legacy lighting mode', async () => {
    const { createLocationDirectionalLightField } = await integration;
    let allocations = 0;
    const limited = { ...three, DataTexture: class { constructor() { allocations++; } } };
    assert.throws(() => createLocationDirectionalLightField(limited, data([]), [], () => 0, 16), /limit/);
    assert.throws(() => createLocationDirectionalLightField(limited,
        { grid_width: 200, grid_height: 200, objects: [{ type: 'box', properties: { level: 10 } }] },
        [], () => 0), /limit/);
    assert.equal(allocations, 0);
});

test('scene traversal attaches floors, water, decor and characters at their level, excluding effects', () => {
    const scene = read('locationScene.js');
    const body = scene.slice(scene.indexOf('function forEachStaticLightFieldMaterial('), scene.indexOf('function refreshStaticLightField('));
    const material = id => ({ id });
    const root = (id, object) => ({ userData: { locationObject: object, sourceObject: object },
        traverse(fn) { fn({ isMesh: true, userData: {}, material: material(id) }); } });
    const effect = root('effect', { type: 'anomaly' });
    const context = { locationTileMesh: root('terrain'), waterTileMesh: null, smoothTerrainMesh: null,
        terrainSurfaceMesh: null, waterSurfaceMesh: root('water'), waterBankMesh: null, sandBankMesh: null,
        waterDetailGroup: null, grassDetailGroup: root('grass'),
        locationObjectMeshes: [root('wall', { type: 'wall', properties: { level: 1 } }), effect],
        objectMeshes: [root('decor', { type: 'box', level: -1 }), effect], anomalyEffectMeshes: [effect],
        characterModels: new Map([[1, { model: root('character'), floorLevel: 2 }]]),
        usesStaticLightField: o => o?.type && o.type !== 'anomaly',
        structureLevel: o => o?.properties?.level ?? o?.level ?? 0 };
    vm.runInNewContext(body, context);
    const visited = [];
    context.forEachStaticLightFieldMaterial((m, level) => visited.push([m.id, level]));
    assert.deepEqual(visited, [['terrain', 0], ['water', 0], ['grass', 0], ['wall', 1], ['decor', -1], ['character', 2]]);
});

test('refresh failure retains the last valid field; only scene disposal detaches it', () => {
    const source = read('locationScene.js');
    const start = source.indexOf('function refreshStaticLightField(');
    const end = source.indexOf('export function getLocationStaticLightFieldDiagnostics(', start);
    let disposed = 0, detached = 0, notices = 0;
    const field = { update() { throw Error('refresh failed'); }, detach() { detached++; }, dispose() { disposed++; } };
    const context = { locationStaticLightField: field, portableLightRenderer: null, currentLocationData: data([]),
        locationStaticLightFieldRefreshTimer: 9, getLocationStaticFieldSources: () => [],
        console: { error() {} }, showNotification() { notices++; }, clearTimeout() {},
        forEachStaticLightFieldMaterial: fn => fn({}), performance };
    vm.runInNewContext(source.slice(start, end), context);
    context.refreshStaticLightField();
    assert.equal(context.locationStaticLightField, field); assert.equal(disposed, 0); assert.equal(notices, 1);
    context.disposeLocationLightField();
    assert.equal(context.locationStaticLightField, null); assert.equal(disposed, 1); assert.equal(detached, 1);
    assert.equal(context.locationStaticLightFieldRefreshTimer, null);
});
