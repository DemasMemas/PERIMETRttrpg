const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const load = async name => import(moduleUrl(read(name)));

test('Мелководье примыкает к глубокой воде без полосы сухого грунта', async () => {
    const { buildRoundedWaterData, SHALLOW_WATER_OFFSET } = await load('locationWaterEdge.js');
    const tiles = Array.from({length: 3}, () => Array.from({length: 3}, () => (
        {terrain: 'shallow_water', height: 1.12}
    )));
    tiles[1][1] = {terrain: 'water', height: 1.05};
    const surface = buildRoundedWaterData(tiles, .24, undefined, null, undefined, {shorelineRing: true});
    assert.equal(surface.bank.length, 0);
    const heights = surface.water.filter((_, index) => index % 3 === 1);
    assert.ok(heights.some(height => Math.abs(height - (1.12 + SHALLOW_WATER_OFFSET)) < .00001));
    assert.ok(heights.some(height => height > 1.062 && height < 1.09));

    const { createLocationWaterGeometry } = await import(moduleUrl(read('locationShallowWater.js')
        .replace('./locationWaterEdge.js', moduleUrl(read('locationWaterEdge.js')))
        .replace('./waterGeometryUtils.js', moduleUrl(read('waterGeometryUtils.js')))));
    const THREE = {
        BufferGeometry: class { setAttribute(name, value) { this[name] = value; } },
        Float32BufferAttribute: class { constructor(array) { this.array = new Float32Array(array); } },
    };
    const geometry = createLocationWaterGeometry(THREE, surface.water, tiles, () => 1.12);
    const { position: { array: positions }, waterDepth: { array: depths } } = geometry;
    const samples = (x, z, start, end) => {
        const matches = [];
        for (let i = start; i < end; i += 3) {
            if (positions[i] === x && positions[i + 2] === z) {
                matches.push([positions[i + 1], depths[i / 3 * 2], depths[i / 3 * 2 + 1]]);
            }
        }
        return matches;
    };
    for (const z of [1, 1.25, 1.5, 1.75, 2]) {
        const deep = samples(1, z, 0, surface.water.length);
        const shallow = samples(1, z, surface.water.length, positions.length);
        assert.ok(deep.length && shallow.length);
        for (const point of shallow) assert.ok(deep.some(values => values.every((n, i) => n === point[i])));
    }
    assert.ok(samples(0, 1.5, surface.water.length, positions.length).every(point => point[2] === 0));
});

test('enclosed water has rounded corners and matching bank fill', async () => {
    const { buildRoundedWaterData } = await load('locationWaterEdge.js');
    const tiles = Array.from({ length: 3 }, () => Array.from({ length: 3 }, () => (
        { terrain: 'grass', height: 1 }
    )));
    tiles[1][1] = { terrain: 'water', height: 0.99 };
    const data = buildRoundedWaterData(tiles);
    assert.equal(data.water.length, 16 * 9);
    assert.equal(data.bank.length, 288 * 9);
    assert.equal(data.bankColors.length, data.bank.length);
    assert.equal(data.bankUvs.length, data.bank.length / 3 * 2);
    assert.ok(data.bankUvs.every(value => value >= 0 && value <= 1));
    const shoreTints = [];
    for (let index = 0; index < data.bank.length; index += 3) {
        const x = data.bank[index];
        const z = data.bank[index + 2];
        if (x === 1 || x === 2 || z === 1 || z === 2) shoreTints.push(data.bankColors[index]);
    }
    assert.ok(shoreTints.some(value => value === 1));
    assert.ok(data.bankColors.some(value => value < 0.8));
    assert.ok(data.water.every((number, index) => index % 3 !== 1 || number === 1.002));
    const bankHeights = data.bank.filter((_, index) => index % 3 === 1);
    assert.ok(bankHeights.some(height => height === 0.996));
    assert.ok(bankHeights.some(height => height === 1.006));
    assert.ok(data.water.some((number, index) => index % 3 === 0 && number > 1 && number < 1.1));
});

test('water adopts its dominant shore across a connected basin', async () => {
    const { buildRoundedWaterData, classifyWaterRegions, waterLevelForTile } = await load('locationWaterEdge.js');
    const basin = kind => {
        const tiles = Array.from({ length: 5 }, () => Array.from({ length: 5 }, () => (
            { terrain: kind, height: 1 }
        )));
        for (let z = 1; z < 4; z++) {
            for (let x = 1; x < 4; x++) tiles[z][x] = { terrain: 'water', height: 1 };
        }
        return tiles;
    };
    for (const kind of ['swamp', 'sand', 'rock', 'grass']) {
        const tiles = basin(kind);
        const regions = classifyWaterRegions(tiles);
        assert.equal(regions.size, 9);
        assert.equal(regions.get('2,2'), kind);
        const data = buildRoundedWaterData(tiles);
        assert.equal(data.waterColors.length, data.water.length);
        assert.equal(data.regions.get('2,2'), kind);
        const level = waterLevelForTile(tiles[2][2], kind);
        assert.equal(level, kind === 'sand' ? 0.8 : 1);
        assert.ok(data.water.every((value, index) => index % 3 !== 1
            || Math.abs(value - (level + 0.012)) < 0.000001));
    }
    const grassColors = buildRoundedWaterData(basin('grass')).waterColors;
    assert.ok(grassColors.every(value => value === 1));
    assert.notDeepEqual(buildRoundedWaterData(basin('swamp')).waterColors, grassColors);
    assert.notDeepEqual(buildRoundedWaterData(basin('rock')).waterColors, grassColors);
    assert.notDeepEqual(buildRoundedWaterData(basin('sand')).waterColors, grassColors);
    const sandData = buildRoundedWaterData(basin('sand'));
    assert.equal(sandData.bank.length, 0);
    assert.ok(sandData.sandBank.length > 0);
    assert.equal(sandData.sandBankColors.length, sandData.sandBank.length);
    assert.ok(sandData.sandBank.some((value, index) => index % 3 === 1 && value < 0.9));
    const scene = read('locationScene.js');
    assert.match(scene, /createLocationWaterGeometry\(THREE, data\.water, waterTiles, landSurfaceHeightAt, data\.waterMargins\)/);
    assert.match(scene, /waterLevelForTile\(tile, waterKind\)/);
    assert.match(scene, /createWaterDetailGroup\(THREE, tiles, waterRegions, settings\?\.natural_detail,/);
});

test('world chunks can reuse rounded shoreline geometry without drawing neighbor tiles', async () => {
    const { buildRoundedWaterData } = await load('locationWaterEdge.js');
    const tiles = Array.from({ length: 4 }, () => Array.from({ length: 4 }, () => (
        { terrain: 'sand', height: 1 }
    )));
    tiles[1][1] = { terrain: 'water', height: 1 };
    tiles[1][2] = { terrain: 'water', height: 1 };
    const data = buildRoundedWaterData(tiles, 0.24,
        (x, z) => x === 1 && z === 1, [0, 0.075, 0.23, 0.5, 0.77, 0.925, 1],
        tile => tile.height);
    assert.equal(data.water.length, 12 * 9);
    assert.ok(data.water.every((value, index) => index % 3 !== 1 || value === 1.012));
    assert.ok(data.sandBank.length > 0);
    assert.equal(data.bankBaseColors.length, data.bank.length);
    const world = read('lobby3d.js');
    assert.match(world, /new THREE\.InstancedMesh\(groundGeo, waterInstanceMaterials, totalTiles\)/);
    assert.match(world, /refreshWorldWaterChunk\(entry, padded\)/);
    assert.match(world, /mesh\.position\.set\(originX, 0, originZ\)/);
});

test('an unloaded neighboring chunk does not remove the water top or abort road setup', async () => {
    const { buildRoundedWaterData, classifyWaterRegions } = await load('locationWaterEdge.js');
    const water = { terrain: 'water', height: 1 };
    const tiles = [[null, null, null], [null, water, null], [null, null, null]];
    assert.equal(classifyWaterRegions(tiles).size, 1);
    const data = buildRoundedWaterData(tiles, 0.24, (x, z) => x === 1 && z === 1);
    assert.equal(data.water.length, 8 * 9);
    assert.equal(data.bank.length + data.sandBank.length, 0);
    const world = read('lobby3d.js');
    assert.match(world, /refreshWorldTerrainNeighborhood\(cx, cy, null, null, false, false, true, true\);\s*refreshRoadNeighborhood\(cx, cy\)/);
});

test('adjoining water cells keep their shared edge and rebuild without separate objects', async () => {
    const { buildRoundedWaterData } = await load('locationWaterEdge.js');
    const tiles = [
        [{ terrain: 'sand', height: 1 }, { terrain: 'sand', height: 1 }, { terrain: 'sand', height: 1 }, { terrain: 'sand', height: 1 }],
        [{ terrain: 'sand', height: 1 }, { terrain: 'water', height: 1 }, { terrain: 'water', height: 1 }, { terrain: 'sand', height: 1 }],
        [{ terrain: 'sand', height: 1 }, { terrain: 'sand', height: 1 }, { terrain: 'sand', height: 1 }, { terrain: 'sand', height: 1 }],
    ];
    const data = buildRoundedWaterData(tiles);
    assert.equal((data.bank.length + data.sandBank.length) / 9, 576);
    assert.ok(data.water.length > 0);
    const scene = read('locationScene.js');
    assert.match(scene, /rebuildWaterSurfaceMesh\(\)/);
    assert.match(scene, /waterSurfaceMesh\.raycast = \(\) => \{\}/);
    assert.match(scene, /waterBankMesh\.raycast = \(\) => \{\}/);
});

test('straight shoreline corners inset consistently across neighboring water cells', async () => {
    const { buildRoundedWaterData } = await load('locationWaterEdge.js');
    const land = { terrain: 'sand', height: 1 };
    const water = { terrain: 'water', height: 1 };
    const data = buildRoundedWaterData([[land, land], [water, water]]);
    const corners = [];
    for (let index = 0; index < data.water.length; index += 9) {
        const centerX = data.water[index];
        const centerZ = data.water[index + 2];
        for (const offset of [3, 6]) {
            if (Math.abs(data.water[index + offset] - 1) < 0.000001
                && Math.abs(data.water[index + offset + 2] - 1.075) < 0.000001) {
                corners.push(centerX, centerZ);
            }
        }
    }
    assert.ok(corners.includes(0.5) && corners.includes(1.5));
});

test('raised water has a sloping shore backed by a solid basin', async () => {
    const { buildRoundedWaterData } = await load('locationWaterEdge.js');
    const tiles = Array.from({ length: 3 }, () => Array.from({ length: 3 }, () => (
        { terrain: 'swamp', height: 0.6 }
    )));
    tiles[1][1] = { terrain: 'water', height: 1.4 };
    const data = buildRoundedWaterData(tiles);
    const heights = data.bank.filter((_, index) => index % 3 === 1);
    assert.ok(heights.some(height => Math.abs(height - 0.606) < 0.000001));
    assert.ok(heights.some(height => Math.abs(height - 1.406) < 0.000001));
    const scene = read('locationScene.js');
    assert.match(scene, /const waterBoxGeometry = geometry\.clone\(\)/);
    assert.match(scene, /waterTileMesh = new THREE\.InstancedMesh\(waterBoxGeometry, \[/);
    assert.doesNotMatch(scene, /waterMaterial\.colorWrite = false/);
    assert.doesNotMatch(scene, /waterTileMesh\?\.material\.map/);
});

test('legacy dense banks remain available while local water keeps its side geometry', async () => {
    const { buildRoundedWaterData } = await load('locationWaterEdge.js');
    const basin = landHeight => {
        const tiles = Array.from({ length: 3 }, () => Array.from({ length: 3 }, () => (
            { terrain: 'grass', height: landHeight }
        )));
        tiles[1][1] = { terrain: 'water', height: 1 };
        return buildRoundedWaterData(tiles);
    };
    const level = basin(1);
    assert.ok(level.bank.some((y, index) => index % 3 === 1
        && Math.abs(level.water[1] - y - 0.006) < 0.000001));
    const slope = basin(1.4);
    assert.ok(slope.bank.some((y, index) => index % 3 === 1 && y > slope.water[1]));
    assert.ok(slope.bank.some((y, index) => index % 3 === 1 && y < slope.water[1]));

    const scene = read('locationScene.js');
    const waterBuild = scene.slice(scene.indexOf('function rebuildWaterSurfaceMesh()'),
        scene.indexOf('export function getLocationGrassDiagnostics()'));
    assert.match(waterBuild, /const waterMaterial = createWaterMaterial\(\{ profileLayer: locationWaterProfiles,\s+reflection: locationWaterReflection, hasShallow, hasShore: true \}\)/);
    assert.doesNotMatch(waterBuild, /waterMaterial\.polygonOffset/);
    assert.doesNotMatch(waterBuild, /waterMaterial\.depthTest = false|waterMaterial\.depthWrite = false/);
    assert.match(scene, /waterTileMesh = new THREE\.InstancedMesh\(waterBoxGeometry, \[\s*waterMaterial, waterMaterial, waterTopMaterial,\s*waterMaterial, waterMaterial, waterMaterial,/);
    assert.match(scene, /waterTopMaterial = new THREE\.MeshBasicMaterial\(\{ colorWrite: false, depthWrite: false \}\)/);
    assert.match(scene, /waterLevelForTile\(tile, waterKind\) - 0\.01/);
    assert.match(waterBuild, /waterBankMesh = new THREE\.Mesh\(bankGeometry, bankMaterial\)/);
    assert.match(waterBuild, /sandBankMesh = new THREE\.Mesh\(bankGeometry, bankMaterial\)/);
});

test('local shoreline ring covers every corner pattern without hidden water overlap', async () => {
    const { buildRoundedWaterData, waterLevelForTile } = await load('locationWaterEdge.js');
    const triangleArea = (positions, index) => Math.abs(
        (positions[index + 3] - positions[index]) * (positions[index + 8] - positions[index + 2])
        - (positions[index + 5] - positions[index + 2]) * (positions[index + 6] - positions[index])) / 2;
    const strictlyInside = (px, pz, positions, index) => {
        const ax = positions[index];
        const az = positions[index + 2];
        const bx = positions[index + 3];
        const bz = positions[index + 5];
        const cx = positions[index + 6];
        const cz = positions[index + 8];
        const denominator = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz);
        const a = ((bz - cz) * (px - cx) + (cx - bx) * (pz - cz)) / denominator;
        const b = ((cz - az) * (px - cx) + (ax - cx) * (pz - cz)) / denominator;
        return a > 0.000001 && b > 0.000001 && a + b < 0.999999;
    };
    for (let mask = 0; mask < 256; mask++) {
        const tiles = Array.from({ length: 3 }, () => Array.from({ length: 3 }, () => (
            { terrain: 'water', height: 1 }
        )));
        let bit = 0;
        for (let z = 0; z < 3; z++) {
            for (let x = 0; x < 3; x++) {
                if (x === 1 && z === 1) continue;
                if (mask & (1 << bit)) tiles[z][x] = { terrain: 'grass', height: 1.4 };
                bit++;
            }
        }
        const data = buildRoundedWaterData(tiles, 0.24, (x, z) => x === 1 && z === 1,
            null, waterLevelForTile, { shorelineRing: true });
        const waterArea = data.water.reduce((sum, _, index) =>
            index % 9 === 0 ? sum + triangleArea(data.water, index) : sum, 0);
        const bankArea = data.bank.reduce((sum, _, index) =>
            index % 9 === 0 ? sum + triangleArea(data.bank, index) : sum, 0);
        assert.ok(Math.abs(waterArea + bankArea - 1) < 0.000001, `gap or overlap: ${mask}`);
        assert.ok(data.bank.length / 9 <= 192, `excessive shore triangles: ${mask}`);
        for (let index = 0; index < data.bank.length; index += 9) {
            assert.ok(triangleArea(data.bank, index) > 1e-10, `degenerate triangle: ${mask}`);
            const points = [0, 3, 6].map(offset => ({
                x: data.bank[index + offset], y: data.bank[index + offset + 1],
                z: data.bank[index + offset + 2],
            }));
            const winding = (points[1].x - points[0].x) * (points[2].z - points[0].z)
                - (points[1].z - points[0].z) * (points[2].x - points[0].x);
            assert.ok(winding < 0, `reversed shore face: ${mask}`);
            for (let edge = 0; edge < 3; edge++) {
                const a = points[edge];
                const b = points[(edge + 1) % 3];
                assert.ok(Math.hypot(b.x - a.x, b.z - a.z) <= Math.SQRT2/4+1e-8,
                    `oversized shore edge: ${mask}`);
                assert.ok(Number.isFinite(a.y), `invalid shore height: ${mask}`);
            }
            const px = (data.bank[index] + data.bank[index + 3] + data.bank[index + 6]) / 3;
            const pz = (data.bank[index + 2] + data.bank[index + 5] + data.bank[index + 8]) / 3;
            for (let face = 0; face < data.water.length; face += 9) {
                assert.equal(strictlyInside(px, pz, data.water, face), false,
                    `bank triangle below water: ${mask}`);
                for (const point of points) {
                    assert.equal(strictlyInside(point.x, point.z, data.water, face), false,
                        `shore vertex projects inside water: ${mask}`);
                }
            }
        }
    }
    const scene = read('locationScene.js');
    assert.match(scene, /const landSurfaceHeightAt = createTerrainSurfaceHeightSampler\(waterTiles\)/);
    assert.match(scene, /buildRoundedWaterData\(waterTiles, 0\.24, undefined, null, waterLevelForTile, \{\s*shorelineRing: true,\s*landSurfaceHeightAt,/);
});

test('adjacent water cells share one three-dimensional bank corner across uneven land', async () => {
    const { buildRoundedWaterData, waterLevelForTile } = await load('locationWaterEdge.js');
    const { createTerrainSurfaceHeightSampler } = await load('locationTerrain.js');
    for (const kind of ['grass', 'sand']) {
        const tiles = Array.from({ length: 3 }, (_, z) => Array.from({ length: 4 }, (_, x) => (
            { terrain: kind, height: z === 0 && x === 1 ? 1.4 : z === 0 && x === 2 ? 0.6 : 1 }
        )));
        tiles[1][1] = { terrain: 'water', height: 1 };
        tiles[1][2] = { terrain: 'water', height: 1 };
        const landSurfaceHeightAt = createTerrainSurfaceHeightSampler(tiles);
        const cornerHeights = cellX => {
            const data = buildRoundedWaterData(tiles, 0.24,
                (x, z) => x === cellX && z === 1, null, waterLevelForTile,
                { shorelineRing: true, landSurfaceHeightAt });
            const bank = kind === 'sand' ? data.sandBank : data.bank;
            const heights = [];
            for (let index = 0; index < bank.length; index += 3) {
                const px = bank[index];
                const pz = bank[index + 2];
                if (pz === 1 && px > 1.05 && px < 2.95) {
                    const expected = landSurfaceHeightAt(px, 0.99999);
                    assert.ok(Math.abs(bank[index + 1] - expected - .002) < 0.0001,
                        `bank edge detaches from smoothed land: ${kind}`);
                }
                if (Math.abs(bank[index] - 2) < 0.000001
                    && Math.abs(bank[index + 2] - 1) < 0.000001) {
                    heights.push(bank[index + 1]);
                }
            }
            return heights;
        };
        const left = cornerHeights(1);
        const right = cornerHeights(2);
        assert.ok(left.length > 0 && right.length > 0, kind);
        for (const y of [...left, ...right]) assert.ok(Math.abs(y - 1.002) < 0.0001, kind);
    }
});

test('neighboring water levels share a sloped three-dimensional edge without shoreline end caps', async () => {
    const { buildRoundedWaterData, waterLevelForTile, waterExposedSideMask } = await load('locationWaterEdge.js');
    const { createTerrainSurfaceHeightSampler } = await load('locationTerrain.js');
    const tiles = Array.from({ length: 3 }, () => Array.from({ length: 4 }, (_, x) => (
        { terrain: 'grass', height: [0.6, 1.8, 1.2, 0.8][x] }
    )));
    tiles[1][1] = { terrain: 'water', height: 1 };
    tiles[1][2] = { terrain: 'water', height: 1.6 };
    const landSurfaceHeightAt = createTerrainSurfaceHeightSampler(tiles);
    const sharedEdges = [];
    for (const cellX of [1, 2]) {
        const data = buildRoundedWaterData(tiles, 0.24,
            (x, z) => x === cellX && z === 1, null, waterLevelForTile,
            { shorelineRing: true, landSurfaceHeightAt });
        const level = waterLevelForTile(tiles[1][cellX], 'grass') + 0.012;
        assert.ok(data.water.some((value, index) => index % 3 === 1
            && Math.abs(value - level) < 1e-8), 'cell center retains its saved level');
        const edgeKey=(a,b)=>[a,b].map(p=>p.map(v=>v.toFixed(7)).join(',')).sort().join('|');
        const waterEdges=new Map(),bankEdges=new Set();
        for(const [field,target] of [[data.water,waterEdges],[data.bank,bankEdges]])for(let i=0;i<field.length;i+=9)for(let k=0;k<3;k++){
            const a=field.slice(i+k*3,i+k*3+3),b=field.slice(i+(k+1)%3*3,i+(k+1)%3*3+3),key=edgeKey(a,b);
            if(target instanceof Map)target.set(key,{a,b,count:(target.get(key)?.count||0)+1});else target.add(key);
        }
        for(const [key,{a,b,count}]of waterEdges){
            if(count!==1||[cellX,cellX+1].some(x=>Math.abs(a[0]-x)<1e-8&&Math.abs(b[0]-x)<1e-8)
                ||[1,2].some(z=>Math.abs(a[2]-z)<1e-8&&Math.abs(b[2]-z)<1e-8))continue;
            assert.ok(bankEdges.has(key),'every exposed interior water edge shares its full 3D bank edge');
        }
        const edges = [];
        for (let index = 0; index < data.water.length; index += 9) {
            for (let side = 0; side < 3; side++) {
                const a = index + side * 3;
                const b = index + (side + 1) % 3 * 3;
                if (Math.abs(data.water[a] - 2) < 1e-8
                    && Math.abs(data.water[b] - 2) < 1e-8) {
                    assert.ok(Math.abs(data.water[a + 1] - 1.312) < 1e-8);
                    assert.ok(Math.abs(data.water[b + 1] - 1.312) < 1e-8);
                    edges.push([data.water[a + 2], data.water[b + 2]].sort((a, b) => a - b));
                }
            }
        }
        edges.sort((a, b) => a[0] - b[0]);
        assert.ok(edges.length>0);
        for(let i=1;i<edges.length;i++)assert.ok(Math.abs(edges[i-1][1]-edges[i][0])<1e-8,'no gaps along shared water edge');
        sharedEdges.push(edges);
        assert.ok(data.bank.length > 0);
        for (let index = 0; index < data.bank.length; index += 9) {
            const positions = data.bank;
            const projectedArea = (positions[index + 3] - positions[index])
                * (positions[index + 8] - positions[index + 2])
                - (positions[index + 5] - positions[index + 2])
                * (positions[index + 6] - positions[index]);
            assert.ok(Math.abs(projectedArea) > 1e-8,
                `stray vertical shoreline face beside water cell ${cellX}`);
        }
    }
    assert.deepEqual(sharedEdges[0], sharedEdges[1]);
    assert.equal(waterExposedSideMask(tiles, 1, 1) & 1, 0, 'east internal side hidden');
    assert.equal(waterExposedSideMask(tiles, 2, 1) & 2, 0, 'west internal side hidden');
    assert.equal(waterExposedSideMask(tiles, 1, 1) & 2, 2, 'land-facing side preserved');
    assert.equal(waterExposedSideMask(tiles, 2, 1) & 1, 1, 'land-facing side preserved');
});

test('exterior water sides reach the surface at higher, lower and level corners', async () => {
    const { classifyWaterRegions, connectedWaterHeightAt, waterSideCornerDrops,
        waterExposedSideMask } = await load('locationWaterEdge.js');
    const tiles = Array.from({ length: 3 }, () => Array.from({ length: 4 }, () => (
        { terrain: 'grass', height: 1 }
    )));
    tiles[1][1] = { terrain: 'water', height: 1 };
    tiles[1][2] = { terrain: 'water', height: 1.6 };
    const regions = classifyWaterRegions(tiles);
    const highBoxTop = 1.59;
    const high = waterSideCornerDrops(tiles, 2, 1, regions, highBoxTop);
    const low = waterSideCornerDrops(tiles, 1, 1, regions, 0.99);
    assert.ok(Math.abs(connectedWaterHeightAt(tiles, 2, 1, 2, 1, regions) - 1.3) < 1e-8);
    assert.ok(Math.abs(highBoxTop * (1 - high[0]) - 1.312) < 1e-8);
    assert.ok(Math.abs(highBoxTop * (1 - high[3]) - 1.312) < 1e-8);
    assert.ok([high[1], high[2]].every(drop => Math.abs(highBoxTop * (1 - drop) - 1.612) < 1e-8));
    assert.ok([low[1], low[2]].every(drop => Math.abs(.99 * (1 - drop) - 1.312) < 1e-8));
    assert.equal(waterExposedSideMask(tiles, 2, 1) & 2, 0, 'inner side stays hidden');
    assert.equal(waterExposedSideMask(tiles, 2, 1) & 8, 8, 'outer bank side stays visible');
    tiles[1][2].height = 1;
    assert.ok(waterSideCornerDrops(tiles, 2, 1, regions, .99)
        .every(drop => Math.abs(.99 * (1 - drop) - 1.012) < 1e-8));
    const scene = read('locationScene.js');
    assert.match(scene, /waterBoxGeometry\.setAttribute\('waterCornerDrops'/);
    assert.match(scene, /transformed\.y -= cornerDrop/);
    assert.match(scene, /waterSideCornerDrops\(currentLocationData\.tiles_data,/);
});

test('connected water levels meet at one corner height across four cells', async () => {
    const { buildRoundedWaterData, waterExposedSideMask } = await load('locationWaterEdge.js');
    const tiles = Array.from({ length: 4 }, () => Array.from({ length: 4 }, () => (
        { terrain: 'grass', height: 1 }
    )));
    const levels = [[1, 1.4], [1.8, 2.2]];
    for (let dz = 0; dz < 2; dz++) for (let dx = 0; dx < 2; dx++) {
        tiles[1 + dz][1 + dx] = { terrain: 'water', height: levels[dz][dx] };
    }
    for (let dz = 0; dz < 2; dz++) for (let dx = 0; dx < 2; dx++) {
        const x = 1 + dx;
        const z = 1 + dz;
        const data = buildRoundedWaterData(tiles, 0.24, (tx, tz) => tx === x && tz === z,
            null, tile => tile.height, { shorelineRing: true });
        const center = [];
        for (let index = 0; index < data.water.length; index += 3) {
            if (Math.abs(data.water[index] - 2) < 1e-8
                && Math.abs(data.water[index + 2] - 2) < 1e-8) center.push(data.water[index + 1]);
        }
        assert.ok(center.length > 0, `missing shared corner for ${x},${z}`);
        for (const height of center) assert.ok(Math.abs(height - 1.612) < 1e-8);
        assert.ok(data.water.some((value, index) => index % 3 === 1
            && Math.abs(value - levels[dz][dx] - 0.012) < 1e-8), 'center keeps its level');
        const internalBits = (dx === 0 ? 1 : 2) | (dz === 0 ? 4 : 8);
        assert.equal(waterExposedSideMask(tiles, x, z) & internalBits, 0);
    }
});

test('one swamp and three water cells close their shared corner in 3D', async () => {
    const { buildRoundedWaterData, connectedWaterHeightAt, waterLevelForTile } = await load('locationWaterEdge.js');
    const { createTerrainSurfaceHeightSampler, isSmoothTerrainTile } = await load('locationTerrain.js');
    const cells = [[1, 1], [2, 1], [1, 2], [2, 2]];
    for (let landIndex = 0; landIndex < cells.length; landIndex++) {
        for (const levels of [[1, 1, 1], [0.8, 1.3, 1.8]]) {
            const tiles = Array.from({ length: 4 }, () => Array.from({ length: 4 }, () => (
                { terrain: 'water', height: 1 }
            )));
            let levelIndex = 0;
            const [landX, landZ] = cells[landIndex];
            tiles[landZ][landX] = { terrain: 'swamp', height: 2 };
            cells.forEach(([x, z], index) => {
                if (index !== landIndex) tiles[z][x] = {
                    terrain: 'water', height: levels[levelIndex++],
                };
            });
            assert.equal(isSmoothTerrainTile(tiles, landX, landZ), false);
            const landSurfaceHeightAt = createTerrainSurfaceHeightSampler(tiles);
            const data = buildRoundedWaterData(tiles, 0.24, () => true,
                null, waterLevelForTile, {
                    shorelineRing: true,
                    landSurfaceHeightAt,
                });
            const key=p=>p.map(v=>v.toFixed(6)).join(',');
            const banks=new Set();for(let i=0;i<data.bank.length;i+=3)banks.add(key(data.bank.slice(i,i+3)));
            assert.ok(banks.has(key([2,2.002,2])),'bank joins the actual land corner');
            assert.ok(!data.water.some((v,i)=>i%3===0&&Math.abs(v-2)<1e-8&&Math.abs(data.water[i+2]-2)<1e-8),
                'no water overlaps the land corner');
            for(let i=0;i<data.bank.length;i+=9){
                const a=data.bank.slice(i,i+3),b=data.bank.slice(i+3,i+6),c=data.bank.slice(i+6,i+9);
                assert.ok(Math.abs((b[0]-a[0])*(c[2]-a[2])-(b[2]-a[2])*(c[0]-a[0]))>1e-10,'no vertical or degenerate corner fins');
            }
        }
    }
});

test('diagonally touching but disconnected water cells keep separate levels', async () => {
    const { buildRoundedWaterData, waterExposedSideMask } = await load('locationWaterEdge.js');
    const tiles = Array.from({ length: 4 }, () => Array.from({ length: 4 }, () => (
        { terrain: 'grass', height: 1 }
    )));
    tiles[1][1] = { terrain: 'water', height: 1 };
    tiles[2][2] = { terrain: 'water', height: 1.8 };
    for (const [x, z, level] of [[1, 1, 1], [2, 2, 1.8]]) {
        const data = buildRoundedWaterData(tiles, 0.24, (tx, tz) => tx === x && tz === z,
            null, tile => tile.height, { shorelineRing: true });
        assert.ok(data.water.length > 0);
        assert.ok(data.water.every((value, index) => index % 3 !== 1
            || Math.abs(value - level - 0.012) < 1e-8));
        assert.equal(waterExposedSideMask(tiles, x, z), 15, 'all four sides face land');
    }
});

test('water side mask is per instance and refreshes with edited neighboring tiles', () => {
    const scene = read('locationScene.js');
    const build = scene.slice(scene.indexOf('const waterMaterial = new THREE.MeshStandardMaterial'),
        scene.indexOf('tileCubes = [locationTileMesh, waterTileMesh]'));
    assert.match(build, /attribute float waterSideMask/);
    assert.match(build, /float sideMask = floor\(vWaterSideMask \+ 0\.5\)/);
    assert.match(build, /if \(sideBit > 0\.0 && mod\(floor\(sideMask \/ sideBit\), 2\.0\) < 0\.5\) discard/);
    assert.match(build, /waterBoxGeometry\.setAttribute\('waterSideMask',\s*new THREE\.InstancedBufferAttribute/);
    const update = scene.slice(scene.indexOf('function updateTileCubes(changed)'),
        scene.indexOf('function refreshTerrainFoundation()'));
    assert.match(update, /for \(let dz = -1; dz <= 1; dz\+\+\)/);
    assert.match(update, /updateTileInstance\(index, nx, nz/);
    assert.match(update, /getAttribute\('waterSideMask'\)\.needsUpdate = true/);
    const instance = scene.slice(scene.indexOf('function updateTileInstance('),
        scene.indexOf('function updateTileInstance(') + 3000);
    assert.match(instance, /waterExposedSideMask\(currentLocationData\.tiles_data, x, z, true\)/);
});

test('diagonal land does not pull a shore triangle across two water neighbors', async () => {
    const { buildRoundedWaterData, waterLevelForTile } = await load('locationWaterEdge.js');
    const tiles = Array.from({ length: 3 }, () => Array.from({ length: 3 }, () => (
        { terrain: 'water', height: 1 }
    )));
    tiles[0][2] = { terrain: 'grass', height: 1.8 };
    tiles[2][1] = { terrain: 'grass', height: 1.2 };
    const data = buildRoundedWaterData(tiles, 0.24, (x, z) => x === 1 && z === 1,
        null, waterLevelForTile, { shorelineRing: true });
    assert.ok(data.bank.length > 0);
    let cornerCaps = 0;
    for (let index = 0; index < data.bank.length; index += 9) {
        const x = (data.bank[index] + data.bank[index + 3] + data.bank[index + 6]) / 3;
        const z = (data.bank[index + 2] + data.bank[index + 5] + data.bank[index + 8]) / 3;
        if (z > 1.5 || x < 1.5) continue;
        cornerCaps++;
        for (const offset of [0, 3, 6]) {
            assert.ok(data.bank[index + offset] >= 1.5 - 1e-8
                && data.bank[index + offset + 2] <= 1.5 + 1e-8,
            'shore triangle extends beyond the short diagonal corner cap');
        }
    }
    assert.ok(cornerCaps>0,'a continuous local collar closes the diagonal corner');
});

test('local shore ring joins uneven grass and sand to water without changing neighboring tops', async () => {
    const { buildRoundedWaterData, waterLevelForTile } = await load('locationWaterEdge.js');
    for (const kind of ['grass', 'sand']) {
        const tiles = Array.from({ length: 3 }, () => Array.from({ length: 4 }, () => (
            { terrain: kind, height: 1.4 }
        )));
        tiles[1][1] = { terrain: 'water', height: 1 };
        tiles[1][2] = { terrain: 'water', height: 1 };
        const data = buildRoundedWaterData(tiles, 0.24, () => true,
            null, waterLevelForTile, { shorelineRing: true });
        const bank = kind === 'sand' ? data.sandBank : data.bank;
        const shoulder = kind === 'sand' ? data.sandBankShoulders : data.bankShoulders;
        assert.ok(bank.length > 0);
        assert.equal(shoulder.length, bank.length / 3);
        assert.ok(shoulder.some(value => value === 1) && shoulder.some(value => value === 0));
        assert.equal(data.waterMargins.length, data.water.length / 3);
        assert.ok(data.waterMargins.some(value => value === 0) && data.waterMargins.some(value => value > 0));
        const waterHeight = waterLevelForTile(tiles[1][1], kind) + 0.012;
        assert.ok(bank.some((value, index) => index % 3 === 1
            && Math.abs(value - waterHeight) < 0.000001));
        assert.ok(bank.some((value, index) => index % 3 === 1 && value > waterHeight + 0.1));
        assert.ok(data.water.every((value, index) => index % 3 !== 1
            || Math.abs(value - waterHeight) < 0.000001));
        assert.ok(data.water.length/9>0&&data.water.length/9<=128,'bounded surface tessellation');
    }
});

test('three water cells do not leave detached water patches over the land corner', async () => {
    const { buildRoundedWaterData } = await load('locationWaterEdge.js');
    const water = { terrain: 'water', height: 1 };
    const land = { terrain: 'sand', height: 1 };
    const data = buildRoundedWaterData([[land, water], [water, water]]);
    for (let index = 0; index < data.water.length; index += 3) {
        assert.ok(!(data.water[index] < 1 && data.water[index + 2] < 1));
    }
    assert.ok(data.bank.length + data.sandBank.length > 0);
    assert.equal(data.water.length % 9, 0);
    const upperRightTriangles = [];
    for (let index = 0; index < data.water.length; index += 9) {
        if (data.water[index] === 1.5 && data.water[index + 2] === 0.5) {
            upperRightTriangles.push(data.water.slice(index, index + 9));
        }
    }
    assert.ok(upperRightTriangles.some(triangle => (
        (triangle[3] === 1 && triangle[5] === 1)
        || (triangle[6] === 1 && triangle[8] === 1)
    )));
});

test('darkness preserves the water material shader', async () => {
    const { applySurfaceDarkness } = await load('locationDarknessSurface.js');
    const material = {
        customProgramCacheKey: () => 'water',
        onBeforeCompile(shader) { shader.fragmentShader += '\nwater-pattern'; },
    };
    applySurfaceDarkness(material, {}, { x: 3, y: 3 });
    const shader = {
        uniforms: {},
        vertexShader: '#include <common>\n#include <project_vertex>',
        fragmentShader: '#include <common>\n#include <color_fragment>',
    };
    material.onBeforeCompile(shader);
    assert.match(material.customProgramCacheKey(), /water:location-surface-plain/);
    assert.match(shader.fragmentShader, /water-pattern/);
    assert.match(shader.fragmentShader, /locationDarknessMap/);
});
