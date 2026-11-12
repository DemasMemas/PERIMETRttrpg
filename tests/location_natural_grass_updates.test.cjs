const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const directory = path.join(__dirname, '../app/static/js');
const read = name => fs.readFileSync(path.join(directory, name), 'utf8');
const moduleUrl = name => {
    let code = read(name);
    for (const match of [...code.matchAll(/from ['"]\.\/([^'"]+)['"]/g)]) {
        code = code.replace(match[0], `from '${moduleUrl(match[1])}'`);
    }
    return `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
};
const source = read('locationScene.js');
const functionSource = name => {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, name);
    return source.slice(start, source.indexOf('\n}', start) + 2);
};

// Real placement, height sampling, queue and scene refresh. Only Three.js resources
// are replaced; matrix composition includes the generator's YXZ Euler rotation.
class Object3D {
    constructor() {
        const vector = () => ({ x: 0, y: 0, z: 0,
            set(x, y, z) { Object.assign(this, { x, y, z }); },
            setScalar(s) { this.set(s, s, s); } });
        this.position = vector(); this.scale = vector(); this.rotation = vector();
        this.children = []; this.userData = {}; this.visible = true;
    }
    add(child) { child.parent?.remove(child); child.parent = this; this.children.push(child); }
    remove(child) { this.children = this.children.filter(item => item !== child); child.parent = null; }
    traverse(visit) { visit(this); this.children.forEach(child => child.traverse(visit)); }
    updateMatrix() {
        const a = this.rotation.x / 2, b = this.rotation.y / 2, c = this.rotation.z / 2;
        const s1 = Math.sin(a), s2 = Math.sin(b), s3 = Math.sin(c);
        const c1 = Math.cos(a), c2 = Math.cos(b), c3 = Math.cos(c);
        const x = s1*c2*c3+c1*s2*s3, y = c1*s2*c3-s1*c2*s3;
        const z = c1*c2*s3-s1*s2*c3, w = c1*c2*c3+s1*s2*s3;
        const { x: sx, y: sy, z: sz } = this.scale;
        this.matrix = [
            (1-2*(y*y+z*z))*sx, 2*(x*y+z*w)*sx, 2*(x*z-y*w)*sx, 0,
            2*(x*y-z*w)*sy, (1-2*(x*x+z*z))*sy, 2*(y*z+x*w)*sy, 0,
            2*(x*z+y*w)*sz, 2*(y*z-x*w)*sz, (1-2*(x*x+y*y))*sz, 0,
            this.position.x, this.position.y, this.position.z, 1,
        ];
    }
}
class Attribute {
    constructor(array, size) { this.array = new Float32Array(array); this.count = array.length / size; }
}
class Geometry {
    constructor() {
        this.attributes = {}; this.disposals = 0;
        this.boundingBox = { min: { x: -.5, y: -.2, z: -.5 }, max: { x: .5, y: 1, z: .5 } };
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
    computeVertexNormals() {}
    clone() { const result = new Geometry(); result.index = this.index; return result; }
    dispose() { this.disposals++; }
}
class Material {
    constructor(options = {}) { Object.assign(this, options); this.userData = {}; this.disposals = 0; }
    clone() { return new Material({ map: this.map }); }
    dispose() { this.disposals++; }
}
class Color {
    constructor(hex = 0xffffff) { this.setRGB((hex >> 16 & 255) / 255, (hex >> 8 & 255) / 255, (hex & 255) / 255); }
    setRGB(r, g, b) { Object.assign(this, { r, g, b }); return this; }
    setHSL(h, s, l) { return this.setRGB(h, s, l); }
}
class Mesh extends Object3D {
    constructor(geometry, material) { super(); Object.assign(this, { geometry, material }); }
}
class InstancedMesh extends Mesh {
    constructor(geometry, material, count) {
        super(geometry, material); this.count = count; this.disposals = 0;
        this.instanceMatrix = { array: new Float32Array(count * 16) };
        this.instanceColor = { array: new Float32Array(count * 3) };
    }
    setMatrixAt(index, matrix) { this.instanceMatrix.array.set(matrix, index * 16); }
    setColorAt(index, color) { this.instanceColor.array.set([color.r, color.g, color.b], index * 3); }
    dispose() { this.disposals++; }
}
const THREE = { Object3D, Group: Object3D, Mesh, InstancedMesh, Color,
    BufferGeometry: Geometry, IcosahedronGeometry: Geometry, Float32BufferAttribute: Attribute,
    MeshStandardMaterial: Material, MeshLambertMaterial: Material,
    Sphere: class { constructor(center, radius) { Object.assign(this, { center, radius }); } },
    Vector3: class { constructor(x, y, z) { Object.assign(this, { x, y, z }); } } };
const tilesFor = size => Array.from({ length: size }, () => Array.from({ length: size }, () =>
    ({ terrain: 'grass', height: 1 })));
const meshes = h => h.context.grassDetailGroup.children.filter(mesh => mesh.userData.naturalGrassKind);

async function harness(tiles, objects = [], kinds = ['low']) {
    const [grass, terrain, detail, architecture, water] = await Promise.all([
        import(moduleUrl('locationNaturalGrass.js')), import(moduleUrl('locationTerrain.js')),
        import(moduleUrl('locationGrassDetail.js')), import(moduleUrl('locationArchitecture.js')),
        import(moduleUrl('waterProfiles.js')),
    ]);
    const assets = Object.fromEntries(kinds.map(kind => {
        const geometry = new Geometry(); geometry.index = { count: 160 * 3 };
        return [kind, { geometry, material: new Material(), bottom: -0.2, scale: 0.1, spread: 0.3 }];
    }));
    const context = vm.createContext({ window:{dispatchEvent(){}},Event:class{},THREE, ...grass, ...terrain, ...detail, ...architecture,
        currentLocationData: { tiles_data: tiles, objects, grid_width: tiles[0].length, grid_height: tiles.length },
        scene: new Object3D(),
        grassDetailGroup: null, naturalGrassBuild: null, naturalGrassAssets: assets,
        locationWaterProfiles: null,
        normalizeWaterProfile: water.normalizeWaterProfile,
        resolvedLightingProfile: null,
        addNaturalSwampModels() {},
        createWaterMaterial: () => new Material(),
        naturalGrassLoading: false, grassGroundReady: true, grassGroundLoading: false,
        naturalGrassReported: true, renderer: { outputColorSpace: '' }, pendingTerrainRefresh: null, objectEdit: null,
        perchedCrowIdles: new Map(),
        activeLightingProfile: { local_lights: [] }, characterModels: new Map(), editorSelection: null,
        getCurrentLocationId: () => 1, isObjectLightSource: () => false,
        refreshTerrainFoundation: () => false, updateTileCubes: () => false,
        ...Object.fromEntries(['updateLocationObjectHeight', 'moveTileDecorWithTerrain',
            'invalidateMovementMapCache', 'rebuildLocationTerrainSurface', 'rebuildWaterSurfaceMesh',
            'rebuildLocationGrid', 'clearFogOverlay', 'applyFogOfWar', 'queueStaticLightFieldRefresh', 'updateRoofVisibility']
            .map(name => [name, () => {}])),
        releaseLocationModel() {}, isSharedLocationModelGeometry: () => false, releaseLocationPbrMaterial() {},
        attachStaticLightFieldToRoot(root) { root.traverse(mesh => {
            if (mesh.material) mesh.material.userData.lightField = 'same-field';
        }); },
    });
    for (const name of ['disposeObject', 'rebuildGrassDetailMesh', 'queueTerrainRefresh', 'flushPendingTerrainRefresh',
        'getTileHeight', 'terrainEditTouchesLight', 'terrainEditTouchesRoof', 'terrainEditTouchesWater',
        'applyLocationTilesUpdate']) {
        vm.runInContext(functionSource(name), context);
    }
    context.rebuildGrassDetailMesh();
    return { context, tiles, assets, grass, terrain, kinds,
        edit(changed) { context.rebuildGrassDetailMesh(changed); },
    };
}

function snapshot(h) {
    return new Map(meshes(h).map(mesh => [h.kinds.length === 1 ? mesh.userData.naturalGrassChunk
        : `${mesh.userData.naturalGrassChunk}:${mesh.userData.naturalGrassKind}`, mesh]));
}
async function assertFullEquivalent(h) {
    const full = await harness(h.tiles, h.context.currentLocationData.objects, h.kinds);
    const expected = snapshot(full), actual = snapshot(h);
    assert.deepEqual([...actual.keys()].sort(), [...expected.keys()].sort());
    for (const [key, mesh] of actual) {
        const reference = expected.get(key);
        assert.equal(mesh.count, reference.count, key);
        assert.deepEqual([mesh.position.x, mesh.position.y, mesh.position.z],
            [reference.position.x, reference.position.y, reference.position.z], key);
        assert.equal(Buffer.compare(Buffer.from(mesh.instanceMatrix.array.buffer),
            Buffer.from(reference.instanceMatrix.array.buffer)), 0, `matrices: ${key}`);
        assert.deepEqual(mesh.instanceColor.array, reference.instanceColor.array, key);
        assert.equal(mesh.castShadow, false); assert.equal(mesh.receiveShadow, false);
        assert.equal(mesh.frustumCulled, reference.frustumCulled);
        assert.equal(mesh.frustumCulled, true);
        assert.deepEqual(mesh.boundingSphere, reference.boundingSphere);
        assert.equal(mesh.material.userData.lightField, 'same-field');
        assert.equal(mesh.geometry.disposals, 0); assert.equal(mesh.material.disposals, 0);
    }
    assert.deepEqual(JSON.parse(JSON.stringify(h.context.naturalGrassBuild)),
        JSON.parse(JSON.stringify(full.context.naturalGrassBuild)));
    assert.equal(h.context.grassDetailGroup.userData.detailCounts.tufts,
        full.context.grassDetailGroup.userData.detailCounts.tufts);
    full.context.disposeObject(full.context.grassDetailGroup);
}

test('building slab replacement updates only affected grass chunks and matches a full rebuild',async()=>{
    const tiles=Array.from({length:48},()=>Array.from({length:48},()=>({terrain:'grass',height:1,objects:[]})));
    const floor={id:1,type:'floor',tile_x:8,tile_y:8,properties:{building_id:'b',level:0,rotation:.4,dimensions:{width:6,depth:5,height:.12}}};
    const h=await harness(tiles,[floor]);
    vm.runInContext(functionSource('buildingGrassChangedTiles'),h.context);
    // The helper uses the same structure accessors as floorVisualPose.
    const architecture=await import(moduleUrl('locationArchitecture.js'));
    Object.assign(h.context,architecture);
    const old=snapshot(h),moved={...floor,tile_x:15};
    const changed=h.context.buildingGrassChangedTiles([floor],[moved],[floor,moved]);
    h.context.currentLocationData.objects=[moved];h.context.rebuildGrassDetailMesh(changed);
    const after=snapshot(h);
    assert.ok([...old].some(([k,m])=>after.get(k)===m),'far chunks retain their instances');
    await assertFullEquivalent(h);
    h.context.disposeObject(h.context.grassDetailGroup);
});

test('rotated grass roots sit just below flat and edited sloped terrain without moving the soil', async () => {
    const tiles = tilesFor(20);
    const h = await harness(tiles);
    const verify = () => {
        const sample = h.terrain.createTerrainSurfaceHeightSampler(tiles);
        let checked = 0;
        for (const mesh of meshes(h)) for (let i = 0; i < mesh.count; i++) {
            const m = mesh.instanceMatrix.array.subarray(i * 16, i * 16 + 16);
            const bottom = h.assets.low.bottom;
            const rootX = mesh.position.x + m[12] + m[4] * bottom;
            const rootY = m[13] + m[5] * bottom;
            const rootZ = mesh.position.z + m[14] + m[6] * bottom;
            const clearance = rootY - sample(rootX, rootZ);
            assert.ok(clearance < -0.011 && clearance > -0.022, `root clearance ${clearance}`);
            checked++;
        }
        assert.ok(checked > 100);
    };
    verify();
    assert.ok(tiles.every(row => row.every(tile => tile.height === 1)));
    for (let z = 7; z < 14; z++) for (let x = 7; x < 14; x++) tiles[z][x].height = 1 + (x - 7) * 0.08;
    const saved = JSON.stringify(tiles);
    h.edit([{ x: 10, z: 10 }]);
    // Regenerate the same affected regions for the height-edited scene.
    h.edit(Array.from({length:7},(_,z)=>Array.from({length:7},(_,x)=>({x:x+7,z:z+7}))).flat());
    verify();
    assert.equal(JSON.stringify(tiles), saved);
    await assertFullEquivalent(h);
});

test('chunk culling bounds contain tilted plants at edges and arbitrary edited heights', async () => {
    const h = await harness(tilesFor(40));
    const distant = snapshot(h).get('3,3'), geometry = distant.geometry;
    const verify = () => {
        const { min, max } = h.assets.low.geometry.boundingBox;
        for (const mesh of meshes(h)) {
            assert.equal(mesh.geometry, geometry, 'keep shared geometry');
            const { center, radius } = mesh.boundingSphere;
            for (let i = 0; i < mesh.count; i++) {
                const m = mesh.instanceMatrix.array.subarray(i * 16, i * 16 + 16);
                for (const x of [min.x, max.x]) for (const y of [min.y, max.y]) for (const z of [min.z, max.z]) {
                    const px = m[0]*x + m[4]*y + m[8]*z + m[12];
                    const py = m[1]*x + m[5]*y + m[9]*z + m[13];
                    const pz = m[2]*x + m[6]*y + m[10]*z + m[14];
                    assert.ok(Math.hypot(px-center.x, py-center.y, pz-center.z) <= radius);
                }
            }
        }
    };
    verify();
    for (const height of [80, -60, 1]) {
        const changed = [];
        for (let z = 12; z < 24; z++) for (let x = 12; x < 24; x++) {
            h.tiles[z][x].height = height; changed.push({ x, z });
        }
        h.edit(changed); verify();
        assert.equal(snapshot(h).get('3,3'), distant, 'distant chunk survives bound refresh');
        await assertFullEquivalent(h);
    }
});

for (const size of [20, 40, 80]) for (const [label, x, z, replaced] of [
    ['inside', 6, 6, 1], ['edge', 11, 6, 2], ['corner', 11, 11, 4],
]) {
    test(`local natural grass ${size}x${size}, ${label}: exact instances and untouched distant chunks`, async () => {
        const h = await harness(tilesFor(size));
        const previous = snapshot(h);
        const shared = meshes(h)[0]; const geometry = shared.geometry, material = shared.material;
        h.tiles[z][x].terrain = 'sand'; h.edit([{ x, z }]);
        let removed = 0;
        for (const [key, old] of previous) {
            if (snapshot(h).get(key) === old) assert.equal(old.disposals, 0);
            else { removed++; assert.equal(old.disposals, 1); }
        }
        assert.equal(removed, replaced);
        assert.ok(meshes(h).every(mesh => mesh.geometry === geometry && mesh.material === material));
        await assertFullEquivalent(h);
    });
}

test('slopes, negative heights, forest, water and map edges equal full refresh over rapid edits', async () => {
    const h = await harness(tilesFor(40), [{ type: 'floor', tile_x: 17, tile_y: 17,
        properties: { rotation: 0.3, dimensions: { width: 5, depth: 4, height: 0.12 } } }]);
    for (const [x, z, update] of [
        [11, 11, { height: 3 }], [12, 12, { height: -3 }], [11, 11, { terrain: 'sand' }],
        [12, 11, { terrain: 'water' }], [12, 12, { terrain: 'rock' }],
        [10, 12, { terrain: 'swamp' }], [13, 11, { grass_subtype: 'forest' }],
        [11, 11, { terrain: 'grass', height: 1 }], [0, 0, { height: -1 }],
        [39, 39, { terrain: 'sand' }], [0, 11, { height: 3 }],
    ]) {
        Object.assign(h.tiles[z][x], update); h.edit([{ x, z }]);
        await assertFullEquivalent(h);
    }
});

test('continuous 3x3 strokes coalesce in the existing queue and retain the latest data', async () => {
    const h = await harness(tilesFor(40));
    for (const kind of ['sand', 'grass', 'water', 'grass']) {
        const previous = snapshot(h);
        const material = meshes(h)[0].material;
        for (let z = 10; z <= 12; z++) for (let x = 10; x <= 12; x++) {
            h.tiles[z][x].terrain = kind;
            h.context.queueTerrainRefresh([{ x, z }], { grass: true });
        }
        h.context.flushPendingTerrainRefresh();
        assert.equal([...previous].filter(([key, mesh]) => snapshot(h).get(key) !== mesh).length, 4);
        assert.ok(meshes(h).every(mesh => mesh.material === material));
        h.context.flushPendingTerrainRefresh();
        await assertFullEquivalent(h);
    }
});

test('removing the last grass chunk releases owned resources once and permits regrowth', async () => {
    const tiles = tilesFor(40); tiles.forEach(row => row.forEach(tile => { tile.terrain = 'water'; }));
    for (let z = 0; z < 12; z++) for (let x = 0; x < 12; x++) tiles[z][x].terrain = 'grass';
    const h = await harness(tiles);
    assert.equal(meshes(h).length, 1);
    const old = meshes(h)[0], geometry = old.geometry, material = old.material;
    assert.notEqual(material, h.assets.low.material, 'участки используют материал своей сцены');
    const changed = [];
    for (let z = 0; z < 12; z++) for (let x = 0; x < 12; x++) {
        tiles[z][x].terrain = 'water'; changed.push({ x, z });
    }
    h.edit(changed);
    assert.equal(meshes(h).length, 0); assert.equal(old.disposals, 1);
    assert.equal(geometry.disposals, 1); assert.equal(material.disposals, 1);
    assert.equal(h.assets.low.geometry.disposals, 0); assert.equal(h.assets.low.material.disposals, 0);
    await assertFullEquivalent(h);
    for (let z = 4; z < 8; z++) for (let x = 4; x < 8; x++) tiles[z][x].terrain = 'grass';
    h.edit([{ x: 6, z: 6 }]);
    assert.equal(meshes(h).length, 1); assert.notEqual(meshes(h)[0].geometry, geometry);
    await assertFullEquivalent(h);
    const replacement = meshes(h)[0]; h.context.disposeObject(h.context.grassDetailGroup);
    assert.equal(replacement.disposals, 1);
    assert.equal(replacement.geometry.disposals, 1); assert.equal(replacement.material.disposals, 1);
    assert.equal(geometry.disposals, 1); assert.equal(h.assets.low.material.disposals, 0);
});

test('кисть обновляет все три формы травы только в затронутых участках', async () => {
    const h = await harness(tilesFor(40), [], ['low', 'dry', 'seeded']);
    const before = snapshot(h);
    h.tiles[12][12].terrain = 'sand';
    h.edit([{ x: 12, z: 12 }]);
    const after = snapshot(h);
    for (const kind of h.kinds) {
        assert.notEqual(before.get(`1,1:${kind}`), after.get(`1,1:${kind}`));
        assert.equal(before.get(`3,3:${kind}`), after.get(`3,3:${kind}`));
    }
    await assertFullEquivalent(h);
});

test('full refresh after floor changes and scene cleanup retain correct grass ownership', async () => {
    const h = await harness(tilesFor(40));
    const previous = meshes(h), geometry = previous[0].geometry, material = previous[0].material;
    h.context.currentLocationData.objects.push({ type: 'floor', tile_x: 12, tile_y: 12,
        properties: { dimensions: { width: 8, depth: 8, height: 0.12 } } });
    h.context.rebuildGrassDetailMesh();
    assert.ok(previous.every(mesh => mesh.disposals === 1));
    assert.equal(geometry.disposals, 1); assert.equal(material.disposals, 1);
    await assertFullEquivalent(h);
    const roots = meshes(h); const resources = roots[0];
    h.context.disposeObject(h.context.grassDetailGroup);
    assert.ok(roots.every(mesh => mesh.disposals === 1));
    assert.equal(resources.geometry.disposals, 1); assert.equal(resources.material.disposals, 1);
    assert.equal(h.context.grassDetailGroup.userData.naturalGrassChunks.size, 0);
});

test('mixed terrain around displaced roots and slope probes remains exact after batched edits', async () => {
    const tiles = tilesFor(40);
    const kinds = ['grass', 'grass', 'grass', 'sand', 'rock', 'swamp', 'water'];
    for (let z = 0; z < 40; z++) for (let x = 0; x < 40; x++) {
        tiles[z][x].terrain = kinds[(x * 7 + z * 13) % kinds.length];
        tiles[z][x].height = (x + z) % 9 === 0 ? -3 : 1;
        if ((x + z) % 3 === 0) tiles[z][x].grass_subtype = 'forest';
    }
    const h = await harness(tiles);
    let seed = 381;
    const next = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed; };
    for (let packet = 0; packet < 8; packet++) {
        const changed = [];
        for (let index = 0; index < 8; index++) {
            const x = next() % 40, z = next() % 40;
            Object.assign(tiles[z][x], { terrain: kinds[next() % kinds.length],
                height: [-3, 0, 1, 3][next() % 4] });
            changed.push({ x, z });
        }
        h.edit(changed);
        await assertFullEquivalent(h);
    }
});

test('network water-height changes refresh neighboring grass slope probes through the existing queue', async () => {
    const tiles = tilesFor(40);
    for (const row of tiles) row[11].terrain = 'water';
    const h = await harness(tiles);
    const profileUpdates = [];
    h.context.locationWaterProfiles = { updateRegion(x, z) { profileUpdates.push([x, z]); } };
    const initial = meshes(h).reduce((sum, mesh) => sum + mesh.count, 0);
    h.context.applyLocationTilesUpdate(1, Array.from({ length: 10 }, (_, z) => ({ x: 11, z, height: 3 })));
    h.context.flushPendingTerrainRefresh();
    await assertFullEquivalent(h);
    assert.notEqual(meshes(h).reduce((sum, mesh) => sum + mesh.count, 0), initial);
    h.context.applyLocationTilesUpdate(1, Array.from({ length: 10 }, (_, z) => ({ x: 11, z, height: 1 })));
    h.context.flushPendingTerrainRefresh();
    await assertFullEquivalent(h);
    assert.equal(meshes(h).reduce((sum, mesh) => sum + mesh.count, 0), initial);
    const expectedRegions = Array.from({ length: 10 }, (_, z) => [11, z]);
    assert.deepEqual(profileUpdates, [...expectedRegions, ...expectedRegions]);
});
