const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');
const dataUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const architectureUrl = dataUrl(read('locationArchitecture.js'));
const darknessUrl = dataUrl(read('locationDarkness.js').replace('./locationArchitecture.js', architectureUrl));

const wall = { type: 'wall', tile_x: 2, tile_y: 2,
    properties: { dimensions: { width: 3, depth: 0.2, height: 2.4 } } };
const door = { type: 'door', tile_x: 2, tile_y: 2,
    properties: { dimensions: { width: 0.9, depth: 0.18, height: 2 }, is_open: false } };

test('prepared sight rays preserve wall openings and ignore walls on another storey', async () => {
    const {lightPathOpen}=await import(darknessUrl);
    const {structurePose,doorOnWall}=await import(architectureUrl);
    const record={object:wall,pose:structurePose(wall),bottom:1,top:3.4,minX:1,maxX:4,minZ:2.4,maxZ:2.6,openings:[]};
    const a={x:2.5,z:1.5},b={x:2.5,z:3.5};
    assert.equal(lightPathOpen(a,b,[],{walls:[record],fromHeight:2.65,toHeight:2.65}),false);
    assert.equal(lightPathOpen(a,b,[],{walls:[record],fromHeight:5.05,toHeight:5.05}),true);
    record.openings=[doorOnWall({...door,properties:{...door.properties,is_open:true}},wall)];
    assert.equal(lightPathOpen(a,b,[],{walls:[record],fromHeight:2.65,toHeight:2.65}),true);
    assert.equal(lightPathOpen(a,b,[],{walls:[record],fromHeight:3.2,toHeight:3.2}),false);
});

test('dark cells stay dim until a lamp has a clear path through the wall', async () => {
    const { computeDarknessFactors, lightPathOpen } = await import(darknessUrl);
    const cells = [{ x: 2, z: 3 }];
    const source = { x: 2.5, z: 1.5, distance: 6, intensity: 3 };
    const unlit = computeDarknessFactors(5, 5, cells, [wall, door], [], 1);
    assert.ok(Math.abs(unlit[3 * 5 + 2] - 0.07) < 1e-6);
    assert.equal(unlit[1 * 5 + 2], 1);
    const closed = computeDarknessFactors(5, 5, cells, [wall, door], [source], 0);
    assert.ok(Math.abs(closed[3 * 5 + 2] - 0.07) < 1e-6);
    const opened = { ...door, properties: { ...door.properties, is_open: true } };
    assert.equal(lightPathOpen(source, { x: 2.5, z: 3.5 }, [wall, opened]), true);
    const lit = computeDarknessFactors(5, 5, cells, [wall, opened], [source], 0);
    assert.ok(lit[3 * 5 + 2] > closed[3 * 5 + 2]);
});

test('an exterior opening admits daylight, but a free-standing door does not', async () => {
    const { computeDarknessFactors } = await import(darknessUrl);
    const cells = [{ x: 2, z: 3 }];
    const opened = { ...door, properties: { ...door.properties, is_open: true } };
    const withWall = computeDarknessFactors(5, 5, cells, [wall, opened], [], 1);
    const withoutWall = computeDarknessFactors(5, 5, cells, [opened], [], 1);
    assert.ok(withWall[3 * 5 + 2] > withoutWall[3 * 5 + 2]);
    assert.ok(Math.abs(withoutWall[3 * 5 + 2] - 0.07) < 1e-6);
});

test('opening an elevated window also admits light without making it a doorway', async () => {
    const { computeDarknessFactors } = await import(darknessUrl);
    const window = { ...door, type: 'window', properties: {
        dimensions: { width: 1.2, depth: 0.14, height: 1 }, elevation: 1, is_open: true,
    } };
    const open = computeDarknessFactors(5, 5, [{ x: 2, z: 3 }], [wall, window], [], 1);
    window.properties.is_open = false;
    const closed = computeDarknessFactors(5, 5, [{ x: 2, z: 3 }], [wall, window], [], 1);
    assert.ok(open[3 * 5 + 2] > closed[3 * 5 + 2]);
});

test('darkness brush paints and erases square areas without leaving the map', async () => {
    const { paintDarkTiles } = await import(darknessUrl);
    const painted = paintDarkTiles([], 5, 4, 0, 0, 1);
    assert.deepEqual(painted, [{ x: 0, z: 0 }, { x: 1, z: 0 }, { x: 0, z: 1 }, { x: 1, z: 1 }]);
    assert.equal(paintDarkTiles(painted, 5, 4, 0, 0, 1), painted);
    const erased = paintDarkTiles(painted, 5, 4, 1, 1, 0, true);
    assert.deepEqual(erased, [{ x: 0, z: 0 }, { x: 1, z: 0 }, { x: 0, z: 1 }]);
    assert.equal(paintDarkTiles(erased, 5, 4, 1, 1, 0, true), erased);
});

test('sunlight casts shadows and local sources refresh the permanent field', () => {
    const scene = read('locationScene.js');
    const roof = read('locationRoof.js');
    assert.match(scene, /locationDirectionalLight\.castShadow = true/);
    assert.match(roof, /shadowCaster\.castShadow = true/);
    assert.match(roof, /roof\.userData\.visualMeshes = gable \? \[roofFace, gable\] : \[roofFace\]/);
    assert.match(scene, /child\.castShadow = !\['floor_opening', 'household_floor_rug', 'anomaly'\]\.includes\(obj\.type\) && !child\.userData\.fireFlame/);
    assert.match(scene, /child\.receiveShadow = true/);
    assert.match(scene, /function refreshObjectLightSource\([^)]*\) \{\s*queueStaticLightFieldRefresh\(\)/);
});

test('editor forwards painted cells to lighting save and map pointer handling', () => {
    const main = read('main.js');
    const scene = read('locationScene.js');
    const editor = read('locationLightingEditor.js');
    assert.match(editor, /value\.dark_tiles = getDarkTiles\(\)/);
    assert.match(main, /window\.locationDarknessPaintAtTile = \(x, z\)/);
    assert.match(main, /saveLocationEnvironment\(settings, environmentBase\)/);
    assert.match(scene, /window\.locationDarknessPaintAtTile\?\.\(hoveredTileCoords\.x, hoveredTileCoords\.z\)/);
    assert.match(main, /data-darkness-radius/);
    assert.match(main, /paintLocationDarknessAtTile\(x, z, radius,/);
    assert.match(scene, /refreshDarknessEditorOverlay\(profile\.dark_tiles \|\| \[\]\)/);
});
