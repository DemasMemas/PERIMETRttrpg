const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const sceneContext = require('./helpers/locationSceneContext.cjs');
const createSceneContext = values => vm.createContext(sceneContext(values));

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');
const source = read('locationScene.js');
const url = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const functionSource = name => {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, name);
    return source.slice(start, source.indexOf('\n}', start) + 2);
};

class Geometry {
    constructor() { this.attributes = {}; }
    setAttribute(name, attribute) { this.attributes[name] = attribute; }
    getAttribute(name) { return this.attributes[name]; }
    computeVertexNormals() {}
    translate() { return this; }
}
class Group {
    constructor() {
        this.children = []; this.userData = {}; this.rotation = {};
        this.position = { set(x, y, z) { Object.assign(this, { x, y, z }); } };
    }
    add(child) { this.children.push(child); }
    traverse(visitor) { visitor(this); this.children.forEach(child => child.traverse(visitor)); }
}
class Mesh extends Group {
    constructor(geometry, material) { super(); Object.assign(this, { geometry, material, isMesh: true }); }
}
const THREE = {
    Group, Mesh, BufferGeometry: Geometry, BoxGeometry: Geometry,
    Float32BufferAttribute: class { constructor(array) { this.array = new Float32Array(array); } },
    MeshStandardMaterial: class { constructor(options) { Object.assign(this, options); } },
    MeshBasicMaterial: class { constructor(options) { Object.assign(this, options); } },
};

function building(type, storeys) {
    const objects = [];
    for (let level = 0; level < storeys; level++) {
        const properties = { building_id: 'house', level };
        const add = (kind, x, z, width, depth, height, rotation = 0) => objects.push({
            id: objects.length + 1, type: kind, tile_x: x, tile_y: z,
            properties: { ...properties, roof_type: type, elevation: 0.3,
                rotation, dimensions: { width, depth, height } },
        });
        add('floor', 5, 5, 4, 4, 0.12);
        add('wall', 5, 3, 4, 0.2, 2.5);
        add('wall', 5, 7, 4, 0.2, 2.5);
        add('wall', 3, 5, 4, 0.2, 2.5, Math.PI / 2);
        add('wall', 7, 5, 4, 0.2, 2.5, Math.PI / 2);
    }
    return { id: 1, grid_width: 12, grid_height: 12, objects,
        tiles_data: Array.from({ length: 12 }, () => Array.from({ length: 12 }, () =>
            ({ terrain: 'grass', height: 1, objects: [] }))),
    };
}

async function harness(data = building('gable', 1)) {
    const architecture = await import(url(read('locationArchitecture.js')));
    const roof = await import(url(read('locationRoof.js').replace("'./locationArchitecture.js'",
        `'${url(read('locationArchitecture.js'))}'`)));
    const transforms = await import(url(read('locationAssetTransforms.js')));
    const objects = data.objects.map(object => {
        const mesh = new Group();
        mesh.userData = { locationObject: object, locationObjectId: object.id };
        return mesh;
    });
    const context = createSceneContext({ ...architecture, ...roof,
        clampAssetElevation: transforms.clampAssetElevation, THREE,
        currentLocationData: data, scene: { add() {}, remove() {} },
        locationObjectMeshes: objects, objectMeshes: [], roofMeshes: [],
        locationBuildingViewsDirty: false, pendingTerrainRefresh: null,
        activeLightingProfile: { local_lights: [] }, characterModels: new Map(), editorSelection: null,
        window: {}, brushObjectMode: false, FLOOR_SURFACE_OFFSET: 0.035,
        getCurrentLocationId: () => 1, isObjectLightSource: () => false,
        visualAssetForLocationObject: () => null, quietHouseColor: color => color,
        getSurfaceTexture: () => ({}), applySurfacePattern() {}, updateRoofVisibility() {},
        alignFloorRugToSurface() {},
        refreshFloorRugHeights() {},
        locationWaterProfiles: null,
        locationWeather: null,
        portableLightRenderer: null,
        disposeLocationObject: object => { object.disposed = true; },
        refreshTerrainFoundation: () => false, updateTileCubes: () => false,
        ...Object.fromEntries(['invalidateMovementMapCache', 'rebuildLocationTerrainSurface',
            'rebuildLocationGrid', 'rebuildGrassDetailMesh', 'clearFogOverlay', 'applyFogOfWar',
            'queueStaticLightFieldRefresh'].map(name => [name, () => {}])),
    });
    for (const name of ['getTileHeight', 'getObjectGridPosition', 'isRainBlocker', 'updateLocationObjectHeight',
        'moveTileDecorWithTerrain', 'terrainEditTouchesRoof', 'terrainEditTouchesLight',
        'terrainEditTouchesWater', 'queueTerrainRefresh', 'flushPendingTerrainRefresh',
        'rebuildLocationRoofs', 'applyLocationBrush', 'applyLocationTilesUpdate']) {
        vm.runInContext(functionSource(name), context);
    }
    for (const object of data.objects) context.updateLocationObjectHeight(object.tile_x, object.tile_y);
    context.rebuildLocationRoofs();
    return { context, data, objects,
        edit(x, z, height, network = false) {
            if (network) context.applyLocationTilesUpdate(1, [{ x, z, height }]);
            else context.applyLocationBrush(x, z, { height }, 0);
        },
        frame() { context.flushPendingTerrainRefresh(); },
    };
}

function assertRoofOnWalls(h) {
    assert.equal(h.context.roofMeshes.length, 1);
    const roof = h.context.roofMeshes[0];
    const floor = h.data.objects.find(object => object.id === roof.userData.roofPlan.floorId);
    const walls = h.objects.filter(mesh => mesh.userData.locationObject.type === 'wall'
        && mesh.userData.locationObject.properties.level === floor.properties.level);
    const wallTop = Math.max(...walls.map(mesh => mesh.position.y
        + mesh.userData.locationObject.properties.dimensions.height));
    assert.ok(Math.abs(roof.position.y - wallTop - 0.12) < 1e-9, 'roof must track rendered wall tops');
    assert.equal(roof.position.y, roof.userData.roofPlan.eaveY);
    for (const mesh of h.objects) {
        const object = mesh.userData.locationObject;
        const expected = h.data.tiles_data[object.tile_y][object.tile_x].height
            + object.properties.level * 2.4 + object.properties.elevation
            - (object.type === 'floor' ? 0.12 - 0.035 : 0);
        assert.equal(mesh.position.y, expected, 'objects retain their individual terrain anchors');
    }
    return roof;
}

for (const type of ['gable', 'flat']) for (const storeys of [1, 2]) {
    test(`${type}, ${storeys} storeys: wall anchors update the roof during consecutive brush frames`, async () => {
        const h = await harness(building(type, storeys));
        const initial = assertRoofOnWalls(h).position.y;
        for (const height of [3, 2, -2, 0, 1, 3, 1]) {
            const oldRoof = h.context.roofMeshes[0];
            h.edit(5, 3, height);
            h.frame(); // The existing pre-render flush, while the stroke is still active.
            const roof = assertRoofOnWalls(h);
            assert.equal(oldRoof.disposed, true);
            assert.equal(roof.userData.roofPlan.type, type);
            h.frame(); // No final pointer-up compensation or cumulative displacement.
            assert.equal(h.context.roofMeshes[0], roof);
        }
        assert.equal(assertRoofOnWalls(h).position.y, initial);
    });

    test(`${type}, ${storeys} storeys: lowering the building and reloading preserves roof alignment`, async () => {
        const h = await harness(building(type, storeys));
        const initial = assertRoofOnWalls(h).position.y;
        // Separate pointer events in one frame, with no edit to the floor anchor.
        for (const [x, z] of [[5, 3], [5, 7], [3, 5], [7, 5]]) h.edit(x, z, -2);
        h.frame();
        assert.ok(Math.abs(assertRoofOnWalls(h).position.y - (initial - 3)) < 1e-9);
        const saved = JSON.stringify(h.data);
        const reloaded = await harness(JSON.parse(saved));
        assert.equal(assertRoofOnWalls(reloaded).position.y, assertRoofOnWalls(h).position.y);
        assert.equal(JSON.stringify(h.data), saved, 'rendering does not mutate saved architecture');
        for (const [x, z] of [[5, 3], [5, 7], [3, 5], [7, 5]]) reloaded.edit(x, z, 1, true);
        reloaded.frame();
        assert.equal(assertRoofOnWalls(reloaded).position.y, initial);
    });
}

test('nearby terrain and a floor-only height edit preserve the wall-derived roof height', async () => {
    const h = await harness();
    const original = assertRoofOnWalls(h);
    for (const height of [3, -2, 1]) {
        h.edit(4, 3, height); h.frame();
        assert.equal(assertRoofOnWalls(h), original);
    }
    h.edit(5, 5, 2); h.frame();
    assert.equal(assertRoofOnWalls(h).position.y, original.position.y);
});

test('interleaved brush and network height updates retain all pending roof changes', async () => {
    const h = await harness();
    const initial = assertRoofOnWalls(h).position.y;
    h.edit(5, 3, 3);
    h.edit(5, 3, 3, true); // Identical event cannot clear the pending roof flag.
    h.edit(1, 1, 2, true);
    h.frame();
    assert.equal(assertRoofOnWalls(h).position.y, initial + 2);
    h.edit(5, 3, 2, true); h.frame();
    assert.equal(assertRoofOnWalls(h).position.y, initial + 1);
    h.edit(5, 3, 1, true); h.frame();
    assert.equal(assertRoofOnWalls(h).position.y, initial);
});
