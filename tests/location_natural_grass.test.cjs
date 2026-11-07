const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '../app/static/js');
const source = fs.readFileSync(path.join(root, 'locationNaturalGrass.js'), 'utf8');
const detailSource = fs.readFileSync(path.join(root, 'locationGrassDetail.js'), 'utf8');
const detailUrl = `data:text/javascript;base64,${Buffer.from(detailSource).toString('base64')}`;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source.replace(
    "'./locationGrassDetail.js'", `'${detailUrl}'`)).toString('base64')}`;
test('whole natural grass clumps clear hard paving, not only their centre roots',async()=>{
    const {naturalGrassPlacements}=await import(moduleUrl);
    for(const terrain of ['concrete','granite_tiles','metal','wood_floor']){
        const tiles=Array.from({length:10},()=>Array.from({length:10},(_,x)=>({terrain:x<5?'grass':terrain})));
        const plan=naturalGrassPlacements(tiles);assert.ok(plan.medium01.length>0);
        for(const p of plan.medium01)for(const dx of [-.4,0,.4])for(const dz of [-.4,0,.4])assert.notEqual(tiles[Math.floor(p.z+dz)]?.[Math.floor(p.x+dx)]?.terrain,terrain);
    }
});

test('optimized game grass keeps a small UV-mapped Poly Haven mesh', () => {
    const binary = fs.readFileSync(path.join(__dirname,
        '../app/static/models/vegetation/grass_medium_01_game_d8.glb'));
    assert.equal(binary.toString('utf8', 0, 4), 'glTF');
    const jsonLength = binary.readUInt32LE(12);
    const gltf = JSON.parse(binary.toString('utf8', 20, 20 + jsonLength));
    assert.equal(gltf.nodes[0].name, 'grass_medium_01_mid_a_LOD0');
    assert.equal(gltf.meshes.length, 1);
    const primitive = gltf.meshes[0].primitives[0];
    assert.ok(Number.isInteger(primitive.attributes.TEXCOORD_0));
    assert.equal(gltf.accessors[primitive.indices].count / 3, 457);
    assert.equal(gltf.materials?.length || 0, 0);
});

test('natural grass uses deterministic placements with forest more open than plain', async () => {
    const { naturalGrassPlacements } = await import(moduleUrl);
    const tiles = Array.from({ length: 20 }, () => Array.from({ length: 20 },
        () => ({ terrain: 'grass' })));
    const plain = naturalGrassPlacements(tiles);
    assert.deepEqual(naturalGrassPlacements(tiles), plain);
    assert.equal(plain.stats.grassTiles, 400);
    assert.equal(plain.stats.plainTiles, 400);
    assert.equal(plain.stats.forestTiles, 0);
    tiles.forEach(row => row.forEach(tile => { tile.grass_subtype = 'forest'; }));
    const forest = naturalGrassPlacements(tiles);
    assert.equal(forest.stats.forestTiles, 400);
    assert.ok(forest.medium01.length < plain.medium01.length);
    assert.ok(plain.medium01.length > 600);
    assert.ok(forest.medium01.length >= 400);
});

test('forest tiles stay populated and GLB grass feathers onto dry neighbors', async () => {
    const { naturalGrassPlacements } = await import(moduleUrl);
    const forest = Array.from({ length: 3 }, () => Array.from({ length: 7 },
        () => ({ terrain: 'grass', grass_subtype: 'forest' })));
    const placed = naturalGrassPlacements(forest);
    assert.ok(placed.medium01.length >= 21);
    const mixed = Array.from({ length: 12 }, () => Array.from({ length: 12 }, (_, x) =>
        ({ terrain: x < 6 ? 'grass' : 'sand' })));
    const fringe = naturalGrassPlacements(mixed).medium01;
    assert.ok(fringe.some(plant => mixed[Math.floor(plant.z)]?.[Math.floor(plant.x)]?.terrain === 'sand'));
    mixed.forEach(row => { row[6].terrain = 'water'; });
    const withWater = naturalGrassPlacements(mixed).medium01;
    assert.ok(withWater.every(plant => mixed[Math.floor(plant.z)]?.[Math.floor(plant.x)]?.terrain !== 'water'));
});

test('natural grass respects non-grass cells and floor coverage', async () => {
    const { naturalGrassPlacements } = await import(moduleUrl);
    const tiles = Array.from({ length: 8 }, () => Array.from({ length: 8 },
        () => ({ terrain: 'grass' })));
    tiles[0][0] = { terrain: 'sand' };
    const covered = naturalGrassPlacements(tiles, () => true);
    assert.equal(covered.medium01.length, 0);
    assert.equal(covered.stats.grassTiles, 63);
    assert.ok(covered.stats.floorRejected > 0);
});

test('full grass location uses instanced model without procedural grass blades', async () => {
    const { naturalGrassPlacements } = await import(moduleUrl);
    const { grassPlacements } = await import(detailUrl);
    const tiles = Array.from({ length: 48 }, () => Array.from({ length: 48 },
        () => ({ terrain: 'grass' })));
    const model = naturalGrassPlacements(tiles).medium01.length;
    const details = grassPlacements(tiles, () => false, false);
    const triangles = model * 457;
    assert.equal(details.tufts.length, 0);
    assert.ok(model > 5000 && model < 8000);
    assert.ok(triangles < 3600000);
    const scene = fs.readFileSync(path.join(root, 'locationScene.js'), 'utf8');
    assert.match(scene, /createGrassDetailGroup\(THREE, tiles, getHeight, isCovered, true\)/);
});

test('optimized grass adds one shared instanced model at ground height', async () => {
    const { addNaturalGrassInstances } = await import(moduleUrl);
    class Object3D {
        constructor() {
            this.position = { set: (x, y, z) => { this.xyz = [x, y, z]; } };
            this.rotation = { set() {} };
            this.scale = { set(x, y, z) { this.value = [x, y, z]; } };
        }
        updateMatrix() { this.matrix = { position: this.xyz, scale: this.scale.value }; }
    }
    class InstancedMesh {
        constructor(geometry, material, count) {
            this.geometry = geometry;
            this.material = material;
            this.count = count;
            this.position = { set(x, y, z) { this.x = x; this.y = y; this.z = z; } };
            this.userData = {};
            this.instanceMatrix = { array: new Float32Array(count * 16) };
            this.instanceColor = {};
            this.transforms = [];
        }
        setMatrixAt(index, matrix) {
            this.transforms[index] = matrix;
            const [x, y, z] = matrix.position, [sx, sy, sz] = matrix.scale;
            this.instanceMatrix.array.set([sx,0,0,0, 0,sy,0,0, 0,0,sz,0, x,y,z,1], index * 16);
        }
        setColorAt(index, color) { this.color = color.rgb; }
    }
    class Color { setRGB(...rgb) { this.rgb = rgb; } }
    const THREE = { Object3D, InstancedMesh, Color, Sphere: class {}, Vector3: class {} };
    const geometry = triangles => ({ clone() { return this; },
        boundingBox: { min: { x: -.5, y: 0, z: -.5 }, max: { x: .5, y: 1, z: .5 } },
        index: { count: triangles * 3 } });
    const material = () => ({ clone() { return { ...this }; } });
    const assets = {
        medium01: { geometry: geometry(457), material: material(), bottom: 0,
            scale: 1, spread: 1 },
    };
    const group = { children: [], userData: { detailCounts: { tufts: 0 } },
        add(mesh) { this.children.push(mesh); } };
    const placements = { medium01: [{ x: 2.5, z: 3.5, size: 1,
        width: 1, depth: 1, lean: 0, tone: 0.5, turn: 0 }] };
    const result = addNaturalGrassInstances(THREE, group, placements, assets, () => 1);
    assert.deepEqual(result.added, { medium01: 1 });
    assert.deepEqual(result.triangles, { medium01: 457 });
    assert.equal(group.children.length, 1);
    assert.equal(group.children[0].transforms[0].position[1], 0.982);
    assert.deepEqual(group.children[0].color, [1, 1, 1]);
    assert.equal(group.children[0].instanceColor.needsUpdate, true);
});
