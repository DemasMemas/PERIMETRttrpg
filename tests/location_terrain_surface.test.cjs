const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const directory = path.join(__dirname, '../app/static/js');
const source = fs.readFileSync(path.join(directory, 'locationScene.js'), 'utf8');
function functionSource(name) {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, name);
    return source.slice(start, source.indexOf('\n}', start) + 2);
}
// Expand the shared builder in the existing allocation/lifecycle harness; the
// benchmark still compares the old sampled flat loop with the current fast loop.
const terrainSource = fs.readFileSync(path.join(directory, 'locationTerrain.js'), 'utf8');
const builderStart = terrainSource.indexOf('export function buildTerrainSurfaceData(');
const builder = terrainSource.slice(builderStart, terrainSource.indexOf('\n}', builderStart));
const builderBody = builder.slice(builder.indexOf(') {') + 3)
    .replace('    const width = tiles[0].length, height = tiles.length;',
        '    const bounds = { minX:0, minZ:0, maxX:width-1, maxZ:height-1 }; const limitRelief = undefined;')
    .replace('tileHeight(tiles, x, z)', 'getTileHeight(x, z)')
    .replace('    return { positions, normals, uvs, indices };', '');
const surfaceSource = functionSource('rebuildLocationTerrainSurface').replace(
    /    const \{ positions, normals, uvs, indices \} = buildTerrainSurfaceData\([^\n]+\);/, builderBody);

// Previous flat-tile vertex loop. The rest of the real surface builder (including
// slope copying, attributes, indices and geometry replacement) runs unchanged.
const previousVertices = `            const first = positions.length / 3;
            const landHeightAt = (px, pz) => sampleHeight(
                Math.max(x + 0.00001, Math.min(x + 0.99999, px)),
                Math.max(z + 0.00001, Math.min(z + 0.99999, pz)),
            );
            for (let row = 0; row <= subdivisions; row++) {
                for (let column = 0; column <= subdivisions; column++) {
                    const px = x + column / subdivisions;
                    const pz = z + row / subdivisions;
                    const py = landHeightAt(px, pz);
                    positions.push(px, py + 0.002, pz);
                    uvs.push(px / width, pz / height);
                    const left = landHeightAt(px - 0.04, pz);
                    const right = landHeightAt(px + 0.04, pz);
                    const near = landHeightAt(px, pz - 0.04);
                    const far = landHeightAt(px, pz + 0.04);
                    const normal = new THREE.Vector3(left - right, 0.08, near - far).normalize();
                    normals.push(normal.x, normal.y, normal.z);
                }
            }
`;
const vertexStart = surfaceSource.indexOf('            const first = positions.length / 3;');
const vertexEnd = surfaceSource.indexOf('            for (let row = 0; row < subdivisions;', vertexStart);
assert.ok(vertexStart >= 0 && vertexEnd > vertexStart);
const previousSource = (surfaceSource.slice(0, vertexStart) + previousVertices + surfaceSource.slice(vertexEnd))
    .replace('    const positions = [];',
        '    const sampleHeight = createTerrainSurfaceHeightSampler(tiles);\n    const positions = [];');

class Attribute {
    constructor(values, itemSize) {
        this.array = new Float32Array(values);
        this.itemSize = itemSize;
        this.count = values.length / itemSize;
        this.normalized = false;
    }
    getX(index) { return this.array[index * this.itemSize]; }
    getY(index) { return this.array[index * this.itemSize + 1]; }
    getZ(index) { return this.array[index * this.itemSize + 2]; }
}
class Geometry {
    constructor() { this.attributes = {}; this.disposeCount = 0; }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
    setIndex(values) {
        const Index = values.some(value => value > 65535) ? Uint32Array : Uint16Array;
        this.index = { array: new Index(values), itemSize: 1, count: values.length };
    }
    dispose() { this.disposeCount++; }
}
class Vector3 {
    constructor(x, y, z) { Object.assign(this, { x, y, z }); }
    normalize() {
        const inverseLength = 1 / (Math.hypot(this.x, this.y, this.z) || 1);
        this.x *= inverseLength; this.y *= inverseLength; this.z *= inverseLength;
        return this;
    }
}
let terrainPromise;
async function surfaceHarness(tiles, previous = false) {
    terrainPromise ??= import(`data:text/javascript;base64,${Buffer.from(
        fs.readFileSync(path.join(directory, 'locationTerrain.js'), 'utf8')).toString('base64')}`);
    const terrain = await terrainPromise;
    const smooth = terrain.buildSmoothTerrainData(tiles, () => true, terrain.terrainFoundationHeight(tiles));
    const geometry = new Geometry();
    for (const [name, values, size] of [
        ['position', smooth.positions, 3], ['normal', smooth.normals, 3], ['terrainTop', smooth.topByVertex, 1],
    ]) geometry.setAttribute(name, new Attribute(values, size));
    const smoothMesh = smooth.positions.length
        ? { geometry, userData: { tileByFace: smooth.tileByFace } } : null;
    const counts = { samplers: 0, samples: 0 };
    const sampleFactory = (...args) => {
        counts.samplers++;
        const sample = terrain.createTerrainSurfaceHeightSampler(...args);
        return (...coords) => { counts.samples++; return sample(...coords); };
    };
    const surface = { geometry: new Geometry(), material: {}, userData: { retained: true } };
    // Only Three.js resources are replaced. Both versions execute as normal Node
    // functions, avoiding VM-crossing overhead in the temporary CPU benchmark.
    const run = new Function('THREE', 'terrain', 'createTerrainSurfaceHeightSampler',
        'currentLocationData', 'smoothTerrainMesh', 'initialSurface', `
        const { isSmoothTerrainTile } = terrain;
        const localTerrainRelief = () => null;
        const updateRoofVisibility = {};
        let terrainSurfaceMesh = initialSurface;
        const terrainSurfaceTexture = {}, terrainGrassBlendTexture = {};
        const scene = { remove() {} };
        const disposeObject = mesh => mesh.geometry.dispose();
        ${functionSource('getTileHeight')}
        ${previous ? previousSource : surfaceSource}
        return { rebuild: () => rebuildLocationTerrainSurface(true, true),
            mesh: () => terrainSurfaceMesh, terrainSurfaceTexture, terrainGrassBlendTexture };
    `)({ BufferGeometry: Geometry, Float32BufferAttribute: Attribute, Vector3 }, terrain,
        sampleFactory, { tiles_data: tiles, grid_width: tiles[0].length, grid_height: tiles.length },
        smoothMesh, surface);
    return { ...run, counts, surface, terrain, smoothMesh };
}
const flatTiles = (width, height = width, tileHeight = 1) => Array.from({ length: height }, () =>
    Array.from({ length: width }, () => ({ terrain: 'grass', height: tileHeight })));

function assertSameGeometry(actual, expected) {
    assert.equal(Boolean(actual), Boolean(expected));
    if (!actual) return;
    assert.deepEqual(Object.keys(actual.attributes), Object.keys(expected.attributes));
    for (const name of ['position', 'normal', 'uv', 'index']) {
        const a = name === 'index' ? actual.index : actual.attributes[name];
        const b = name === 'index' ? expected.index : expected.attributes[name];
        assert.equal(a.itemSize, b.itemSize, name);
        assert.equal(a.count, b.count, name);
        assert.equal(a.normalized, b.normalized, name);
        assert.equal(a.array.constructor, b.array.constructor, name);
        assert.equal(Buffer.compare(Buffer.from(a.array.buffer), Buffer.from(b.array.buffer)), 0,
            `${name} differs from the previous algorithm`);
    }
}

const cases = [
    ['flat 20x20', () => flatTiles(20)],
    ['flat 40x40', () => flatTiles(40)],
    ['flat 80x80 with 32-bit indices', () => flatTiles(80)],
    ['one raised tile', () => { const t = flatTiles(20); t[10][10].height = 2; return t; }],
    ...[40, 80].map(size => [`one raised tile on ${size}x${size}`, () => {
        const t = flatTiles(size); t[10][10].height = 2; return t;
    }]),
    ['neighboring heights', () => {
        const t = flatTiles(20); t[10][10].height = 1.2; t[10][11].height = 1.6; t[11][10].height = 2; return t;
    }],
    ['steep rock slope', () => {
        const t = flatTiles(20); t[10][10] = { terrain: 'rock', height: 3 }; return t;
    }],
    ['steep sand slope', () => {
        const t = flatTiles(20); t[10][10] = { terrain: 'sand', height: 3 }; return t;
    }],
    ['flat negative height', () => flatTiles(9, 7, -3)],
    ['negative trench and zero', () => {
        const t = flatTiles(20); t[10][10].height = -3; t[10][11].height = 0; return t;
    }],
    ['edges and corners', () => {
        const t = flatTiles(13, 7); t[0][0].height = 3; t[6][12].height = -2;
        t[0][6].height = 2; t[3][0].height = 0; return t;
    }],
    ['water next to flat and sloped land', () => {
        const t = flatTiles(20); t[10][10] = { terrain: 'water', height: 0.5 };
        t[10][11] = { terrain: 'sand', height: 3 }; t[11][11].terrain = 'swamp'; return t;
    }],
    ['water only', () => flatTiles(4).map(row => row.map(tile => ({ ...tile, terrain: 'water' })))],
    ['single flat cell', () => flatTiles(1)],
    ['heights within the existing smoothness epsilon', () => {
        const t = flatTiles(4); t[1][1].height += 0.0000005; return t;
    }],
];
for (const [name, fixture] of cases) {
    test(`local surface fast path is byte-identical: ${name}`, async () => {
        const tiles = fixture();
        const before = await surfaceHarness(tiles, true);
        const after = await surfaceHarness(tiles);
        const previousGeometry = after.surface.geometry;
        const material = after.surface.material;
        before.rebuild(); after.rebuild();
        assertSameGeometry(after.mesh()?.geometry, before.mesh()?.geometry);
        const flatCount = tiles.reduce((sum, row, z) => sum + row.filter((tile, x) =>
            tile.terrain !== 'water' && !after.terrain.isSmoothTerrainTile(tiles, x, z)).length, 0);
        assert.equal(before.counts.samples, flatCount * 125);
        assert.deepEqual(after.counts, { samplers: 0, samples: 0 });
        assert.equal(previousGeometry.disposeCount, 1);
        if (after.mesh()) {
            assert.equal(after.mesh(), after.surface);
            const replacement = after.mesh().geometry;
            after.rebuild();
            assert.equal(replacement.disposeCount, 1);
            assert.equal(after.mesh().material, material);
            assertSameGeometry(after.mesh().geometry, before.mesh().geometry);
        }
    });
}

test('local flat heights retain existing numeric normalization and defaults', async () => {
    for (const height of [undefined, null, NaN, Infinity, -Infinity, '2.5', '-3', 0, -0]) {
        const tiles = flatTiles(3, 2, height);
        const before = await surfaceHarness(tiles, true);
        const after = await surfaceHarness(tiles);
        before.rebuild(); after.rebuild();
        assertSameGeometry(after.mesh().geometry, before.mesh().geometry);
    }
});
