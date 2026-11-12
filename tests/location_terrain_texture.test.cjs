const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/locationTerrainTexture.js'), 'utf8');
const subtypeSource = fs.readFileSync(path.join(__dirname, '../app/static/js/locationGrassSubtype.js'), 'utf8');
const scene = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
const subtypeUrl = `data:text/javascript;base64,${Buffer.from(subtypeSource).toString('base64')}`;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(
    source.replace("'./locationGrassSubtype.js'", `'${subtypeUrl}'`)).toString('base64')}`;

test('terrain texture varies by soil and blends adjacent cells continuously', async () => {
    const { createLocationTerrainPixels, updateLocationTerrainPixels } = await import(moduleUrl);
    const tiles = [[...['grass', 'sand', 'swamp', 'rock'].map(terrain => ({ terrain }))]];
    const image = createLocationTerrainPixels(tiles, 20);
    assert.equal(image.width, 80);
    assert.equal(image.height, 20);
    const color = (x, z = 10) => Array.from(image.data.slice((z * image.width + x) * 4,
        (z * image.width + x) * 4 + 3));
    const grass = color(10);
    const sand = color(30);
    const swamp = color(50);
    const rock = color(70);
    assert.ok(grass[0] > grass[1]);
    assert.ok(sand[0] > grass[0] + 80);
    assert.ok(swamp[0] < sand[0] - 60);
    assert.ok(Math.abs(rock[0] - rock[1]) < 20);
    assert.ok(Math.abs(color(19)[0] - color(20)[0]) < 25);
    assert.deepEqual(createLocationTerrainPixels(tiles, 20).data, image.data);
    tiles[0][1].terrain = 'grass';
    updateLocationTerrainPixels(tiles, image, [{ x: 1, z: 0 }]);
    assert.deepEqual(image.data, createLocationTerrainPixels(tiles, 20).data);
});

test('grass soil is muted and has deterministic dry patches', async () => {
    const { createLocationTerrainPixels } = await import(moduleUrl);
    const tiles = Array.from({ length: 12 }, () => Array.from({ length: 12 }, () => ({ terrain: 'grass' })));
    const image = createLocationTerrainPixels(tiles, 12);
    let minRed = 255;
    let maxRed = 0;
    let greenBias = 0;
    for (let index = 0; index < image.data.length; index += 4) {
        minRed = Math.min(minRed, image.data[index]);
        maxRed = Math.max(maxRed, image.data[index]);
        greenBias += image.data[index + 1] - image.data[index];
    }
    assert.ok(maxRed - minRed > 20);
    assert.ok(greenBias / (image.data.length / 4) < 20);
    assert.deepEqual(image.data, createLocationTerrainPixels(structuredClone(tiles), 12).data);
    assert.match(scene, /terrainSurfaceTexture\.colorSpace = THREE\.SRGBColorSpace/);
});

test('legacy grass stays plain and forest subtype changes only grass surface', async () => {
    const { createLocationTerrainPixels } = await import(moduleUrl);
    const plain = [[{ terrain: 'grass' }, { terrain: 'sand' }]];
    const explicitPlain = [[{ terrain: 'grass', grass_subtype: 'plain' }, { terrain: 'sand' }]];
    const forest = [[{ terrain: 'grass', grass_subtype: 'forest' }, { terrain: 'sand' }]];
    assert.deepEqual(createLocationTerrainPixels(plain, 20).data,
        createLocationTerrainPixels(explicitPlain, 20).data);
    assert.deepEqual(createLocationTerrainPixels(forest, 20).data,
        createLocationTerrainPixels(plain, 20).data);
});

test('фотографии травяного грунта загружаются отдельно от маски и подложки подлокации', async () => {
    const { loadGrassGroundPixels, createLocationTerrainPixels,
        updateLocationTerrainPixels } = await import(moduleUrl);
    const previousImage = global.Image;
    const previousDocument = global.document;
    const urls = [];
    global.Image = class {
        set src(url) {
            this.url = url;
            urls.push(url);
            queueMicrotask(() => this.onload());
        }
    };
    global.document = { createElement: () => ({ getContext: () => ({
        drawImage(image) { this.source = image.url; },
        getImageData() {
            const data = new Uint8ClampedArray(512 * 512 * 4);
            const color = this.source.includes('forrest_ground')
                ? [55, 80, 52] : [155, 125, 98];
            for (let index = 0; index < data.length; index += 4) data.set([...color, 255], index);
            return { data };
        },
    }) }) };
    const tiles = [[...Array.from({ length: 2 }, () => ({ terrain: 'grass' })),
        ...Array.from({ length: 2 }, () => ({ terrain: 'grass', grass_subtype: 'forest' }))]];
    const before = createLocationTerrainPixels(tiles, 20, true);
    try {
        await loadGrassGroundPixels();
        assert.equal(urls.length, 2);
        assert.ok(urls.some(url => url.includes('dirty_earth')));
        assert.ok(urls.some(url => url.includes('forrest_ground')));
        const image = createLocationTerrainPixels(tiles, 20, true);
        assert.deepEqual(image.data, before.data);
        assert.deepEqual(image.grassBlendData, before.grassBlendData);
        const mask = (x, channel) => image.grassBlendData[(10 * image.width + x) * 4 + channel];
        assert.ok(mask(10, 0) > 240 && mask(10, 1) < 15);
        assert.ok(mask(70, 1) > 240 && mask(70, 0) < 15);
        assert.ok(mask(40, 0) > 0 && mask(40, 1) > 0);
        tiles[0][0].grass_subtype = 'forest';
        updateLocationTerrainPixels(tiles, image, [{ x: 0, z: 0 }]);
        assert.ok(mask(10, 1) > 240 && mask(10, 0) < 15);
        assert.match(scene, /loadGrassGroundPixels\(\)\.then/);
        assert.match(scene, /createLocationTerrainPixels\(tiles, 32, true\)/);
        assert.match(scene, /terrainGrassBlendTexture\.needsUpdate = true/);
    } finally {
        global.Image = previousImage;
        global.document = previousDocument;
    }
});

test('маска на стыке хранит вес травы отдельно от цвета песка и болотной земли', async () => {
    const { createLocationTerrainPixels, updateLocationTerrainPixels } = await import(moduleUrl);
    for (const terrain of ['sand', 'swamp']) {
        const tiles = [[{ terrain: 'grass' }, { terrain }]];
        const pure = createLocationTerrainPixels([[{ terrain }, { terrain }]], 32, true);
        const edge = createLocationTerrainPixels(tiles, 32, true);
        let mixed = 0;
        for (let i = 0; i < edge.data.length; i += 4) {
            const grass = edge.grassBlendData[i];
            if (grass === 0 || grass === 255) continue;
            assert.deepEqual(edge.data.subarray(i, i + 4), pure.data.subarray(i, i + 4));
            mixed++;
        }
        assert.ok(mixed > 100);
        tiles[0][0].grass_subtype = 'forest';
        updateLocationTerrainPixels(tiles, edge, [{ x: 0, z: 0 }]);
        assert.deepEqual(edge, createLocationTerrainPixels(tiles, 32, true));
    }
});

test('world grass uses shared ground photos without changing other soils or chunk seams', async () => {
    const { loadGrassGroundPixels, createTerrainPixelsRegion,
        updateTerrainPixelsRegion } = await import(`${moduleUrl}#world-ground-photos`);
    const previousImage = global.Image;
    const previousDocument = global.document;
    const urls = [];
    global.Image = class {
        set src(url) {
            this.url = url;
            urls.push(url);
            queueMicrotask(() => this.onload());
        }
    };
    global.document = { createElement: () => ({ getContext: () => ({
        drawImage(image) { this.source = image.url; },
        getImageData() {
            const data = new Uint8ClampedArray(512 * 512 * 4);
            const forest = this.source.includes('forrest_ground');
            for (let z = 0; z < 512; z++) {
                for (let x = 0; x < 512; x++) {
                    const index = (z * 512 + x) * 4;
                    data[index] = forest ? 50 + (x % 64) : 140 + (x % 32);
                    data[index + 1] = forest ? 75 + (z % 64) : 110 + (z % 32);
                    data[index + 2] = forest ? 45 : 85;
                    data[index + 3] = 255;
                }
            }
            return { data };
        },
    }) }) };
    try {
        await Promise.all([loadGrassGroundPixels(), loadGrassGroundPixels()]);
        await loadGrassGroundPixels();
        assert.equal(urls.length, 2);
        const overrides = new Map();
        const terrain = (x, z) => overrides.get(`${x},${z}`)
            || (x < 4 ? 'grass' : z < 2 ? 'sand' : 'swamp');
        const left = createTerrainPixelsRegion(terrain, -1, -1, 128, 12, true);
        const right = createTerrainPixelsRegion(terrain, 3, -1, 128, 12, true);
        const procedural = createTerrainPixelsRegion(terrain, -1, -1, 128, 12, false);
        const color = (image, x, z) => Array.from(image.data.slice((z * image.width + x) * 4,
            (z * image.width + x) * 4 + 3));
        assert.notDeepEqual(color(left, 18, 18), color(procedural, 18, 18));
        assert.deepEqual(color(left, 82, 18), color(procedural, 82, 18));
        assert.deepEqual(color(left, 82, 60), color(procedural, 82, 60));
        assert.notDeepEqual(color(left, 58, 18), color(procedural, 58, 18));
        for (let z = 0; z < 64; z++) {
            for (let x = 0; x < 48; x++) {
                assert.deepEqual(color(left, x + 48, z), color(right, x, z));
            }
        }
        overrides.set('4,1', 'grass');
        assert.equal(updateTerrainPixelsRegion(left, terrain, -1, -1,
            { minX: 4, maxX: 4, minZ: 1, maxZ: 1 }, 12, true), true);
        assert.deepEqual(left.data, createTerrainPixelsRegion(terrain, -1, -1, 128, 12, true).data);
    } finally {
        global.Image = previousImage;
        global.document = previousDocument;
    }
});

test('optimized world pixels match pre-optimization photo and fallback outputs', async () => {
    const { loadGrassGroundPixels, createTerrainPixelsRegion,
        updateTerrainPixelsRegion } = await import(`${moduleUrl}#pixel-regression`);
    const previousImage = global.Image;
    const previousDocument = global.document;
    global.Image = class {
        set src(url) { this.url = url; queueMicrotask(() => this.onload()); }
    };
    global.document = { createElement: () => ({ getContext: () => ({
        drawImage(image) { this.source = image.url; },
        getImageData() {
            const data = new Uint8ClampedArray(512 * 512 * 4);
            const forest = this.source.includes('forrest_ground');
            for (let z = 0; z < 512; z++) {
                for (let x = 0; x < 512; x++) {
                    const index = (z * 512 + x) * 4;
                    data[index] = forest ? 50 + x % 64 : 140 + x % 32;
                    data[index + 1] = forest ? 75 + z % 64 : 110 + z % 32;
                    data[index + 2] = forest ? 45 : 85;
                    data[index + 3] = 255;
                }
            }
            return { data };
        },
    }) }) };
    const hash = data => crypto.createHash('sha256').update(data).digest('hex');
    try {
        await loadGrassGroundPixels();
        const overrides = new Map();
        const terrain = (x, z) => overrides.get(`${x},${z}`)
            || (x < 9 ? 'grass' : z < 4 ? 'sand' : x < 20 ? 'swamp' : z < 15 ? 'rock' : 'water');
        const profile = { pixels: 0, terrainLookups: 0, photoColorCalls: 0,
            photoSampledCalls: 0, photoSampledMs: 0 };
        const image = createTerrainPixelsRegion(terrain, -2, -2, 512, 12, true, profile);
        assert.equal(hash(image.data), '0f774b7492009f1019c2bb4c7571b7af86d3296cf6217fe07151ba3d2c67ad75');
        assert.equal(profile.pixels, 512 * 512);
        assert.ok(profile.terrainLookups < 2500);
        assert.ok(profile.photoColorCalls > profile.photoSampledCalls && profile.photoSampledCalls > 0);
        assert.equal(hash(createTerrainPixelsRegion(terrain, -2, -2, 128, 12, false).data),
            '3159c9a3a0b2212b65ccbbf956185646222d7261c5293fb198bc7ea364254700');
        overrides.set('9,4', 'grass');
        const partial = { pixels: 0, terrainLookups: 0, photoColorCalls: 0,
            photoSampledCalls: 0, photoSampledMs: 0 };
        assert.equal(updateTerrainPixelsRegion(image, terrain, -2, -2,
            { minX: 9, maxX: 9, minZ: 4, maxZ: 4 }, 12, true, partial), true);
        assert.ok(partial.pixels > 0 && partial.pixels < profile.pixels);
        assert.deepEqual(image.data, createTerrainPixelsRegion(terrain, -2, -2, 512, 12, true).data);
    } finally {
        global.Image = previousImage;
        global.document = previousDocument;
    }
});

test('local pond continues the surrounding ground texture to its banks and updates when painted', async () => {
    const { createLocationTerrainPixels, updateLocationTerrainPixels } = await import(moduleUrl);
    const resolution = 40;
    const tiles = Array.from({ length: 5 }, () => Array.from({ length: 5 }, () => ({ terrain: 'grass' })));
    const dry = createLocationTerrainPixels(tiles, resolution, true);
    tiles[2][2].terrain = 'water';
    const pond = createLocationTerrainPixels(tiles, resolution, true);
    const pixelOffset = (x, z) => (Math.floor(z * resolution) * pond.width + Math.floor(x * resolution)) * 4;
    for (const [x, z] of [[1.98, 2.5], [2.02, 2.5], [2.32, 2.5], [2.25, 2.25], [2.68, 2.5]]) {
        const offset = pixelOffset(x, z);
        assert.deepEqual(pond.data.slice(offset, offset + 3), dry.data.slice(offset, offset + 3));
        assert.equal(pond.grassBlendData[offset], 255);
    }
    assert.ok(pond.grassBlendData[pixelOffset(2.5, 2.5)] < 40);
    tiles[2][3].terrain = 'sand';
    tiles[3][2].terrain = 'rock';
    updateLocationTerrainPixels(tiles, pond, [{ x: 3, z: 2 }, { x: 2, z: 3 }]);
    assert.ok(pond.grassBlendData[pixelOffset(2.32, 2.5)] > 245);
    assert.ok(pond.grassBlendData[pixelOffset(2.68, 2.5)] < 10);
    const rebuilt = createLocationTerrainPixels(tiles, resolution, true);
    assert.deepEqual(pond.data, rebuilt.data);
    assert.deepEqual(pond.grassBlendData, rebuilt.grassBlendData);
});

test('world ground retains procedural fallback when photos are unavailable', async () => {
    const { loadGrassGroundPixels, createTerrainPixelsRegion } = await import(`${moduleUrl}#world-ground-fallback`);
    const previousImage = global.Image;
    global.Image = class {
        set src(url) { queueMicrotask(() => this.onerror(new Error(`Missing ${url}`))); }
    };
    try {
        await assert.rejects(loadGrassGroundPixels());
        const terrain = () => 'grass';
        assert.deepEqual(createTerrainPixelsRegion(terrain, 0, 0, 32, 12, true).data,
            createTerrainPixelsRegion(terrain, 0, 0, 32, 12, false).data);
    } finally {
        global.Image = previousImage;
    }
});

test('swamp soil has distinct moss and dark wet pockets instead of uniform blur', async () => {
    const { createLocationTerrainPixels } = await import(moduleUrl);
    const tiles = Array.from({ length: 6 }, () => Array.from({ length: 6 }, () => ({ terrain: 'swamp' })));
    const { data } = createLocationTerrainPixels(tiles, 20);
    let wetPixels = 0;
    let mossPixels = 0;
    for (let index = 0; index < data.length; index += 4) {
        if (data[index] < 58 && data[index + 2] > 50) wetPixels++;
        if (data[index + 1] > data[index] + 10) mossPixels++;
    }
    assert.ok(wetPixels > 100);
    assert.ok(mossPixels > 100);
    assert.ok(wetPixels < data.length / 4);
});

test('overlapping world texture regions use identical pixels at chunk seams', async () => {
    const { createTerrainPixelsRegion } = await import(moduleUrl);
    const terrain = (x, z) => (x < 4 ? 'grass' : z < 2 ? 'sand' : 'swamp');
    const left = createTerrainPixelsRegion(terrain, -1, -1, 128, 12);
    const right = createTerrainPixelsRegion(terrain, 3, -1, 128, 12);
    for (let z = 0; z < 64; z++) {
        for (let x = 0; x < 48; x++) {
            const a = (z * left.width + x + 48) * 4;
            const b = (z * right.width + x) * 4;
            assert.deepEqual(left.data.slice(a, a + 4), right.data.slice(b, b + 4));
        }
    }
});

test('world texture updates only the edited neighborhood without changing distant pixels', async () => {
    const { createTerrainPixelsRegion, updateTerrainPixelsRegion } = await import(moduleUrl);
    const kinds = new Map();
    const terrain = (x, z) => kinds.get(`${x},${z}`) || 'grass';
    const image = createTerrainPixelsRegion(terrain, -1, -1, 128, 12);
    const before = image.data.slice();
    kinds.set('3,3', 'sand');
    assert.equal(updateTerrainPixelsRegion(image, terrain, -1, -1,
        { minX: 3, maxX: 3, minZ: 3, maxZ: 3 }, 12), true);
    assert.deepEqual(image.data, createTerrainPixelsRegion(terrain, -1, -1, 128, 12).data);
    assert.deepEqual(image.data.slice(0, 4), before.slice(0, 4));
    assert.equal(updateTerrainPixelsRegion(image, terrain, -1, -1,
        { minX: 100, maxX: 100, minZ: 100, maxZ: 100 }, 12), false);
});

test('land overlay follows slope geometry and water has a solid backing', () => {
    assert.match(scene, /createLocationTerrainPixels\(tiles, 32, true\)/);
    assert.match(fs.readFileSync(path.join(__dirname, '../app/static/js/locationTerrain.js'), 'utf8'), /if \(smoothData\.topByVertex\[face \* 3\] < 0\.5\) continue/);
    assert.match(scene, /new THREE\.InstancedMesh\(waterBoxGeometry, \[/);
    assert.match(scene, /waterBoxGeometry\.setAttribute\('waterSideMask'/);
    assert.match(scene, /waterTopMaterial = new THREE\.MeshBasicMaterial\(\{ colorWrite: false, depthWrite: false \}\)/);
    assert.match(scene, /bankGeometry\.setAttribute\('uv', new THREE\.Float32BufferAttribute\(data\.bankUvs, 2\)\)/);
    assert.match(scene, /map: terrainSurfaceTexture, side: THREE\.DoubleSide/);
    assert.match(scene, /const material = createLocationGroundMaterial\(true\)/);
    assert.equal(scene.match(/const bankMaterial = createLocationGroundMaterial\(\)/g)?.length, 2);
    assert.match(scene, /bankGeometry\.setAttribute\('uv', new THREE\.Float32BufferAttribute\(data\.sandBankUvs, 2\)\)/);
    assert.doesNotMatch(scene, /function updateTerrainSurfaceDarkness\(/);
});

test('local terrain minification uses mipmaps only where the WebGL context supports them', () => {
    const start = scene.indexOf('const image = createLocationTerrainPixels(tiles, 32, true);',
        scene.indexOf('function rebuildLocationTerrainSurface('));
    const last = 'terrainGrassBlendTexture.needsUpdate = true;';
    const end = scene.indexOf(last, start) + last.length;
    assert.ok(start >= 0 && end > start);
    const setup = scene.slice(start, end);
    const build = (size, isWebGL2) => {
        class DataTexture {
            constructor(data, width, height) { this.image = { data, width, height }; }
        }
        const THREE = { DataTexture, RGBAFormat: 1, UnsignedByteType: 2,
            LinearFilter: 3, LinearMipmapLinearFilter: 4, SRGBColorSpace: 'srgb' };
        const image = { data: new Uint8Array(4), grassBlendData: new Uint8Array(4),
            width: size, height: size };
        return vm.runInNewContext(`(() => {
            let terrainSurfaceTexture, terrainGrassBlendTexture;
            ${setup}
            return [terrainSurfaceTexture, terrainGrassBlendTexture];
        })()`, { THREE, renderer: { capabilities: { isWebGL2 } },
            tiles: [], createLocationTerrainPixels: () => image });
    };
    for (const texture of build(1536, true)) {
        assert.equal(texture.minFilter, 4);
        assert.equal(texture.generateMipmaps, true);
        assert.equal(texture.needsUpdate, true);
    }
    for (const texture of build(1536, false)) {
        assert.equal(texture.minFilter, 3);
        assert.equal(texture.generateMipmaps, false);
    }
    for (const texture of build(1024, false)) {
        assert.equal(texture.minFilter, 4);
        assert.equal(texture.generateMipmaps, true);
    }
    assert.match(scene, /terrainGrassBlendTexture\.needsUpdate = true;/);
    assert.match(scene, /terrainSurfaceTexture\.needsUpdate = true;/);
});
