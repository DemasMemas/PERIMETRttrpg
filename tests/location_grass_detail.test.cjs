const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/locationGrassDetail.js'), 'utf8');
const scene = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
const water = fs.readFileSync(path.join(__dirname, '../app/static/js/waterSurface.js'), 'utf8');
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
test('grass blending cannot place roots in hard paving',async()=>{
    const {acceptsDetail,grassPlacements}=await import(moduleUrl);
    for(const terrain of ['concrete','granite_tiles','metal','wood_floor']){
        const tiles=Array.from({length:8},()=>Array.from({length:8},(_,x)=>({terrain:x<4?'grass':terrain})));
        for(let z=.05;z<7.9;z+=.13)for(let x=4;x<5;x+=.07)assert.equal(acceptsDetail(tiles,'grass',x,z,12),false);
        const plan=grassPlacements(tiles);assert.ok(plan.tufts.every(p=>tiles[Math.floor(p.z)]?.[Math.floor(p.x)]?.terrain==='grass'));
    }
});

test('grass placements are patchy, batched, stable, and exclude floors and non-grass', async () => {
    const { grassPlacements } = await import(moduleUrl);
    const tiles = Array.from({ length: 20 }, () => Array.from({ length: 20 }, () => ({ terrain: 'grass' })));
    tiles[0][0].terrain = 'water';
    const a = grassPlacements(tiles, x => x < 3);
    const b = grassPlacements(tiles, x => x < 3);
    assert.deepEqual(a, b);
    assert.ok(a.tufts.length > 3000 && a.tufts.length < 5500);
    assert.ok(a.flowers.length > 30 && a.flowers.length < 130);
    assert.ok([...a.tufts, ...a.flowers].every(item => item.x >= 3));
    assert.deepEqual(a, grassPlacements(structuredClone(tiles), x => x < 3));
    const perTile = new Map();
    for (const tuft of a.tufts) {
        const key = `${Math.floor(tuft.x)},${Math.floor(tuft.z)}`;
        perTile.set(key, (perTile.get(key) || 0) + 1);
    }
    assert.ok([...perTile.values()].filter(count => count < 5).length > 30);
    assert.ok([...perTile.values()].filter(count => count > 18).length > 30);
    assert.ok(new Set(a.tufts.map(tuft => `${tuft.width.toFixed(2)},${tuft.height.toFixed(2)}`)).size > 100);
    assert.match(source, /useNaturalGrass \? false : true/);
    assert.match(source, /mesh\.castShadow = false/);
    assert.match(source, /mesh\.raycast = \(\) => \{\}/);
});

test('natural grass removes procedural grass without replacing flowers', async () => {
    const { grassPlacements } = await import(moduleUrl);
    const tiles = Array.from({ length: 20 }, () => Array.from({ length: 20 },
        () => ({ terrain: 'grass' })));
    const full = grassPlacements(tiles);
    const natural = grassPlacements(tiles, () => false, false);
    assert.equal(natural.tufts.length, 0);
    assert.deepEqual(natural.flowers, full.flowers);
    assert.equal(grassPlacements(tiles, () => false, false, false).flowers.length, 0);
    const plain = grassPlacements(tiles, () => false, 'understory');
    assert.ok(plain.tufts.length > 0 && plain.tufts.length < full.tufts.length);
    assert.deepEqual(plain.flowers, full.flowers);
    assert.ok(plain.tufts.every(tuft => tuft.understory && tuft.size < 0.73));
    tiles.forEach(row => row.forEach(tile => { tile.grass_subtype = 'forest'; }));
    const forest = grassPlacements(tiles, () => false, 'understory');
    assert.ok(forest.tufts.length < plain.tufts.length);
    assert.deepEqual(forest.flowers, plain.flowers);
    const mixed = Array.from({ length: 20 }, (_, z) => Array.from({ length: 20 }, (_, x) =>
        ({ terrain: ['grass', 'sand', 'rock', 'water'][(x + z) % 4] })));
    const bounded = grassPlacements(mixed, x => x < 3, 'understory');
    assert.ok(bounded.tufts.filter(tuft => tuft.understory).every(tuft =>
        tuft.x >= 3 && mixed[Math.floor(tuft.z)]?.[Math.floor(tuft.x)]?.terrain === 'grass'));
});

test('sand pebbles are sparse, irregular, deterministic, and absent from floors', async () => {
    const { grassPlacements, groundPlacements } = await import(moduleUrl);
    const tiles = Array.from({ length: 8 }, () => Array.from({ length: 8 }, (_, x) => (
        { terrain: ['sand', 'swamp', 'rock', 'grass'][x % 4] }
    )));
    const a = groundPlacements(tiles, x => x < 2);
    assert.deepEqual(a, groundPlacements(tiles, x => x < 2));
    assert.ok(a.pebbles.length > 0);
    assert.ok(a.pebbles.length < 15);
    assert.ok(Object.values(a).flat().every(item => item.x >= 2));
    assert.ok(new Set(a.pebbles.map(item => Math.round((item.x % 1) * 100))).size > 2);
    assert.ok(grassPlacements(tiles).tufts.every(item => !item.rock));
    const rock = Array.from({ length: 20 }, () => Array.from({ length: 20 }, () => ({ terrain: 'rock' })));
    assert.deepEqual(grassPlacements(rock), { tufts: [], flowers: [] });
    assert.deepEqual(grassPlacements(rock, () => false, false, false), { tufts: [], flowers: [] });
    assert.match(source, /new THREE\.InstancedMesh\(new THREE\.IcosahedronGeometry/);
    assert.doesNotMatch(source, /new THREE\.CircleGeometry|new THREE\.LineSegments/);
});

test('sand pebbles settle on gentle ground and skip steep faces', async () => {
    const { settlePebbles } = await import(moduleUrl);
    const pebbles = [{ x: 2.5, z: 3.5, size: 0.5 }];
    assert.equal(settlePebbles(pebbles, () => 2)[0].groundHeight, 2);
    assert.equal(settlePebbles(pebbles, x => x).length, 0);
    assert.equal(pebbles[0].groundHeight, undefined);
});

test('swamp pools and reeds are batched, repeatable, and excluded beneath floors', async () => {
    const { swampPlacements } = await import(moduleUrl);
    const tiles = Array.from({ length: 12 }, () => Array.from({ length: 12 }, () => ({ terrain: 'swamp' })));
    tiles[0][0].terrain = 'water';
    const first = swampPlacements(tiles, x => x < 2);
    assert.deepEqual(first, swampPlacements(tiles, x => x < 2));
    assert.ok(first.pools.length > 10 && first.pools.length < 40);
    assert.ok(first.reeds.length > 45 && first.reeds.length < 190);
    assert.ok([...first.pools, ...first.reeds].every(item => item.x >= 2));
    assert.equal(new Set(first.pools.map(item => `${Math.floor(item.x)},${Math.floor(item.z)}`)).size,
        first.pools.length);
    assert.ok(first.pools.every(item => first.reeds.every(reed =>
        Math.floor(reed.x) !== Math.floor(item.x)
            || Math.floor(reed.z) !== Math.floor(item.z)
            || Math.hypot(reed.x - item.x, reed.z - item.z) > item.radius)));
    assert.match(source, /addModels\?\.\(group, 'swamp_reeds_1', reeds/);
});

test('болотная лужа поворачивает вытянутый контур вместе с углом размещения', async () => {
    const { swampPoolEdge } = await import(moduleUrl);
    const pool = { x: 3.5, z: 4.5, radius: 0.25, turn: 0 };
    const east = swampPoolEdge(pool, 0);
    const north = swampPoolEdge({ ...pool, turn: Math.PI / 2 }, 0);
    assert.ok(east[0] > pool.x);
    assert.ok(Math.abs(east[1] - pool.z) < 1e-9);
    assert.ok(Math.abs(north[0] - pool.x) < 1e-9);
    assert.ok(north[1] > pool.z);
    assert.ok(Math.abs((east[0] - pool.x) - (north[1] - pool.z)) < 1e-9);
});

test('болотные лужи по разные стороны общей грани имеют одно зеркало и один внешний контур', async () => {
    const { settleSwampPools } = await import(moduleUrl);
    const tiles = Array.from({ length: 5 }, () => Array.from({ length: 5 }, () => ({ terrain: 'swamp' })));
    const a = { x: 1.5, z: 1.5, radius: .22, turn: .5, seed: .4 };
    const b = { ...a, x: 2.5, z: 1.65 };
    const [joined, ...rest] = settleSwampPools([a, b], tiles, () => 1, () => false);
    assert.equal(rest.length, 0);
    assert.equal(joined.level, 1.014);
    assert.ok(joined.inner.some(([x]) => x < 2) && joined.inner.some(([x]) => x > 2));
    assert.ok(joined.rim.every(([x, z], i) => {
        const [nx, nz] = joined.rim[(i + 1) % joined.rim.length];
        return (x - joined.x) * (nz - joined.z) > (z - joined.z) * (nx - joined.x);
    }), 'треугольники от центра не пересекаются');
    const settled = (neighbor, height = () => 1, covered = () => false) =>
        settleSwampPools([a, neighbor], tiles, height, covered);
    assert.equal(settled({ ...b, z: 2.5 }).length, 2, 'диагональ');
    assert.equal(settled(b, x => x < 2 ? 1 : 1.12).length, 2, 'ступень');
    assert.equal(settled(b, x => Math.abs(x - 2) < .20 ? 1.10 : 1).length, 2, 'перемычка грунта');
    assert.equal(settled(b, () => 1, x => Math.abs(x - 2) < .12).length, 2, 'пол на стыке');
});

test('в любом квадрате 3×3 м остаётся не более двух болотных луж', async () => {
    const { swampPlacements } = await import(moduleUrl);
    const layouts = [
        Array.from({ length: 12 }, () => Array.from({ length: 12 }, () => ({ terrain: 'swamp' }))),
        Array.from({ length: 24 }, (_, z) => Array.from({ length: 20 }, (_, x) =>
            ({ terrain: (x + z) % 7 === 0 ? 'grass' : 'swamp' }))),
        Array.from({ length: 21 }, (_, z) => Array.from({ length: 18 }, (_, x) =>
            ({ terrain: x > 2 && z > 2 && x < 16 && z < 19 ? 'swamp' : 'water' }))),
    ];
    for (const tiles of layouts) {
        for (const covered of [() => false, x => x < 4]) {
            const first = swampPlacements(tiles, covered);
            assert.deepEqual(first, swampPlacements(tiles, covered));
            assert.ok(first.pools.length > 0);
            for (const pool of first.pools) {
                for (const other of first.pools) {
                    const count = first.pools.filter(item => item.x >= pool.x && item.x < pool.x + 3
                        && item.z >= other.z && item.z < other.z + 3).length;
                    assert.ok(count <= 2, `В квадрате от ${pool.x}, ${other.z} найдено ${count} луж`);
                }
            }
        }
    }
});

test('swamp water is upward-facing and skips steep ground', async () => {
    const { createGrassDetailGroup } = await import(moduleUrl);
    const tiles = Array.from({ length: 12 }, () => Array.from({ length: 12 }, () => ({ terrain: 'swamp' })));
    class Geometry {
        constructor() { this.attributes = {}; }
        setAttribute(name, attribute) { this.attributes[name] = attribute; }
        computeVertexNormals() {}
    }
    class Color {
        constructor(hex = 0xffffff) {
            this.r = ((hex >> 16) & 255) / 255;
            this.g = ((hex >> 8) & 255) / 255;
            this.b = (hex & 255) / 255;
        }
        setHSL(h, s, l) { this.r = h; this.g = s; this.b = l; return this; }
    }
    class Object3D {
        constructor() {
            this.position = { set() {} };
            this.rotation = { set() {} };
            this.scale = { setScalar() {} };
        }
        updateMatrix() { this.matrix = {}; }
    }
    class Material { constructor(options) { Object.assign(this, options); } }
    class Mesh { constructor(geometry, material) { this.geometry = geometry; this.material = material; } }
    class InstancedMesh extends Mesh {
        constructor(geometry, material) {
            super(geometry, material);
            this.instanceMatrix = {};
            this.instanceColor = {};
        }
        setMatrixAt() {}
        setColorAt() {}
    }
    const THREE = {
        Group: class { constructor() { this.children = []; this.userData = {}; } add(mesh) { this.children.push(mesh); } },
        BufferGeometry: Geometry,
        Float32BufferAttribute: class { constructor(array) { this.array = array; } },
        Color, Object3D, Mesh, InstancedMesh, MeshStandardMaterial: Material,
        MeshLambertMaterial: Material, DoubleSide: 2,
    };
    const flat = createGrassDetailGroup(THREE, tiles, () => 1, () => false);
    const pool = flat.children.find(mesh => mesh.constructor === Mesh);
    assert.ok(pool);
    assert.equal(pool.material.transparent, true);
    assert.equal(pool.material.depthWrite, false);
    const alpha = pool.geometry.attributes.color.array.filter((_, index) => index % 4 === 3);
    assert.ok(alpha.includes(0) && alpha.includes(.94));
    const vertices = pool.geometry.attributes.position.array;
    assert.ok(vertices[1] > 1);
    const [ax, az, bx, bz, cx, cz] = [vertices[0], vertices[2], vertices[3],
        vertices[5], vertices[6], vertices[8]];
    assert.ok((bx - ax) * (cz - az) - (bz - az) * (cx - ax) < 0);
    assert.ok(flat.userData.detailCounts.pools > 0);

    const steep = createGrassDetailGroup(THREE, tiles, (x) => x, () => false);
    assert.equal(steep.userData.detailCounts.pools, 0);
    assert.equal(steep.children.some(mesh => mesh.constructor === Mesh), false);
});

test('grass feathers across land borders and reeds stay out of open water', async () => {
    const { grassPlacements, swampPlacements } = await import(moduleUrl);
    const tiles = Array.from({ length: 12 }, (_, z) => Array.from({ length: 12 }, (_, x) => ({
        terrain: z < 2 ? 'water' : x < 6 ? 'grass' : 'swamp',
    })));
    const grass = grassPlacements(tiles).tufts;
    const reeds = swampPlacements(tiles).reeds;
    assert.ok(grass.some(item => item.x > 6 && item.z >= 2));
    assert.ok(reeds.some(item => item.x < 7 && item.z >= 2));
    assert.ok([...grass, ...reeds].every(item => tiles[Math.floor(item.z)]?.[Math.floor(item.x)]?.terrain !== 'water'));
    const grassFringe = grass.filter(item => item.x > 6).map(item => item.x - 6);
    assert.ok(Math.max(...grassFringe) - Math.min(...grassFringe) > 0.06);
    assert.ok(new Set(grassFringe.map(value => Math.round(value * 100))).size > 4);
});

test('local terrain rebuilds instanced plants', () => {
    assert.match(scene, /createGrassDetailGroup\(THREE, tiles/);
    assert.match(scene, /createTerrainSurfaceHeightSampler\(tiles\)/);
    assert.match(scene, /rebuildGrassDetailMesh\(\)/);
});

test('world and local water shaders keep continuous world coordinates at their respective scales', () => {
    const context = vm.createContext({
        THREE: {
            MeshStandardMaterial: class { constructor(options) { Object.assign(this, options); } },
            Vector2: class { constructor(x, y) { Object.assign(this, { x, y }); } },
        },
        createWaterTexture: () => ({ offset: { x: 0, y: 0 } }),
    });
    // Exercise the material factory and its generated shader, not template literals.
    vm.runInContext(water.slice(water.indexOf('export function createWaterMaterial('))
        .replace('export function', 'function'), context);
    for (const [world, scale] of [[true, '0.16'], [false, '0.36']]) {
        const material = context.createWaterMaterial({ world });
        const shader = { uniforms: {}, vertexShader: '#include <common>\n#include <project_vertex>',
            fragmentShader: '#include <common>\n#include <map_fragment>\n#include <normal_fragment_maps>' };
        material.onBeforeCompile(shader);
        assert.match(shader.vertexShader, /waterWorld = instanceMatrix \* waterWorld/);
        assert.match(shader.vertexShader, /vWaterWorldXZ = \(modelMatrix \* waterWorld\)\.xz/);
        assert.ok(shader.fragmentShader.includes(`vWaterWorldXZ * ${scale} + waterPatternOffset`));
        assert.equal(shader.uniforms.waterPatternOffset.value, material.map.offset);
    }
});
