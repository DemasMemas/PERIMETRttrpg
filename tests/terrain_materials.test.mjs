import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { TERRAIN_MATERIALS, terrainMaterialIndex, adjustTerrainAlbedo,
    addTerrainMaterialOptions } from '../app/static/js/terrainMaterialCatalog.js';
import { createTerrainMaterialLayer } from '../app/static/js/terrainMaterialLayer.js';

test('catalog reuses metal and distinguishes stone tops, walls and new saved terrain strings', () => {
    assert.equal(terrainMaterialIndex('rock'), 1);
    assert.equal(terrainMaterialIndex({ terrain: 'rock' }, true), 8);
    for (const kind of ['concrete', 'gravel', 'snow', 'metal', 'bare_dirt', 'granite_tiles', 'wood_floor']) assert.ok(terrainMaterialIndex(kind) > 0);
    for (const kind of ['grass', 'water', 'legacy_unknown', null]) assert.equal(terrainMaterialIndex(kind), 0);
    assert.equal(TERRAIN_MATERIALS[6].normal, '/static/materials/art/rusty_metal/normal.jpg');
    assert.equal(TERRAIN_MATERIALS[5].asset, 'snow001');
    assert.equal(terrainMaterialIndex('snow'), 6, 'old saved snow keeps its material index');
    assert.equal(TERRAIN_MATERIALS[1].asset, 'dense_sand');
    assert.equal(TERRAIN_MATERIALS[3].asset, 'concrete_floor_worn_001');
    assert.equal(TERRAIN_MATERIALS[4].asset, 'gravel_stones');
    assert.equal(TERRAIN_MATERIALS[terrainMaterialIndex('bare_dirt') - 1].asset, 'sand_03');
    assert.equal(TERRAIN_MATERIALS[terrainMaterialIndex('granite_tiles') - 1].asset, 'concrete_pavement_02');
    assert.match(TERRAIN_MATERIALS[terrainMaterialIndex('wood_floor') - 1].color, /aged_wood/);
    assert.equal(terrainMaterialIndex('ice'), 12);
    assert.equal(TERRAIN_MATERIALS[11].asset, 'ice001');
    assert.equal(TERRAIN_MATERIALS[11].arm, '/static/materials/terrain/ice001/arm.png');
    assert.equal(terrainMaterialIndex({ terrain: null }), 0);
});

test('every terrain resource exists; new source files match their verified import manifest', () => {
    for (const entry of TERRAIN_MATERIALS) {
        for (const channel of entry.roughnessInColorAlpha ? ['color', 'normal'] : ['color', 'normal', 'arm']) {
            assert.ok(fs.statSync(new URL(`../app${entry[channel]}`, import.meta.url)).size > 0);
        }
        if (!entry.color.startsWith('/static/materials/terrain/')) continue;
        const directory = new URL(`../app/static/materials/terrain/${entry.asset}/`, import.meta.url);
        const manifest = JSON.parse(fs.readFileSync(new URL('material.json', directory)));
        assert.equal(manifest.normalFormat, 'OpenGL');
        assert.equal(manifest.resolution, '1k');
        for (const entry of Object.values(manifest.maps)) {
            const data = fs.readFileSync(new URL(entry.file, directory));
            assert.equal(crypto.createHash('sha256').update(data).digest('hex'), entry.sha256);
        }
    }
});

test('tone corrections retain detail, lighten gravel and stone, and preserve the saved tile and dirt look', () => {
    const adjusted = (kind, values) => {
        const entry = TERRAIN_MATERIALS.find(entry => entry.terrain === kind);
        const data = new Uint8ClampedArray(values);
        adjustTerrainAlbedo(data, entry);
        return [...data];
    };
    const rock = adjusted('rock', [90, 50, 30, 255, 140, 100, 80, 255]);
    assert.equal(rock[0], rock[1]); assert.equal(rock[1], rock[2]);
    assert.ok(rock[0] > 90 && rock[4] > rock[0]);
    const gravel = adjusted('gravel', [70, 65, 60, 255]);
    assert.ok(gravel[0] > 100 && gravel[1] > 100);
    const mud = adjusted('swamp', [110, 60, 30, 255]);
    assert.ok(mud[0] - mud[1] < 20 && mud[1] > 60);
    const snow = adjusted('snow', [40, 45, 50, 255, 210, 210, 210, 255]);
    assert.ok(snow[0] < 50 && snow[4] > 150 && snow[4] > snow[0], 'snow no longer forces every pixel near white');
    assert.equal(adjusted('snow', [220, 230, 240, 119])[3], 119, 'albedo processing preserves packed roughness');
    const ice = adjusted('ice', [20, 30, 40, 255, 230, 240, 250, 255]);
    assert.ok(ice[0] >= 145 && ice[4] - ice[0] < 85, 'лёд светлый, без прежних чёрных пятен и резкого контраста');
    assert.ok(ice[2] > ice[0], 'сохраняется холодный оттенок');
    for (const kind of ['bare_dirt', 'granite_tiles', 'metal']) {
        assert.deepEqual(adjusted(kind, [110, 70, 30, 255]), [110, 70, 30, 255]);
    }
    assert.equal(TERRAIN_MATERIALS[1].repeat, 6);
    assert.ok(TERRAIN_MATERIALS[1].warp > 0);
    assert.ok(TERRAIN_MATERIALS[6].variation > 0);
});

test('local editor gains distinct surface IDs without duplicating options', () => {
    const oldDocument = globalThis.document;
    globalThis.document = { createElement: () => ({}) };
    try {
        const select = { options: [{ value: 'concrete', textContent: 'Old label' }],
            appendChild(option) { this.options.push(option); } };
        addTerrainMaterialOptions(select); addTerrainMaterialOptions(select);
        for (const value of ['concrete', 'granite_tiles', 'bare_dirt', 'wood_floor', 'ice']) {
            assert.equal(select.options.filter(option => option.value === value).length, 1);
        }
        assert.equal(select.options[0].textContent, 'Промышленный бетон');
    } finally { globalThis.document = oldDocument; }
});

function environment(fail = false) {
    const loaded = [], contexts = [];
    globalThis.Image = class {
        set src(url) {
            loaded.push(url);
            queueMicrotask(() => fail ? this.onerror() : this.onload());
        }
    };
    globalThis.document = { createElement() {
        const context = { save() {}, beginPath() {}, rect() {}, clip() {}, translate() {}, scale() {}, restore() {}, drawImage() {},
            getImageData() { return { data: new Uint8ClampedArray([120, 80, 30, 255]) }; },
            putImageData(image) { context.gray = image.data; } };
        contexts.push(context);
        return { getContext: () => context };
    } };
    class Texture {
        constructor(data, width, height) { this.image = { data, width, height }; }
        clone() { return Object.assign(new Texture(), this); }
        dispose() { this.disposed = true; this.disposeCount = (this.disposeCount || 0) + 1; }
    }
    const THREE = { CanvasTexture: class extends Texture { constructor(canvas) { super(); this.image = canvas; } }, DataTexture: Texture,
        Vector2: class { constructor(x, y) { this.set(x, y); } set(x, y) { this.x = x; this.y = y; } },
        NearestFilter: 1, LinearFilter: 2, LinearMipmapLinearFilter: 3 };
    return { THREE, loaded, contexts };
}
function shader() {
    return { uniforms: {}, vertexShader: '#include <common>\n#include <project_vertex>',
        fragmentShader: ['common', 'map_fragment', 'color_fragment', 'roughnessmap_fragment',
            'metalnessmap_fragment', 'normal_fragment_maps', 'aomap_fragment'].map(c => `#include <${c}>`).join('\n') };
}
function compile(layer, options) {
    const material = { onBeforeCompile(shader) { shader.preexisting = true; } };
    layer.apply(material, options);
    const result = shader();
    material.onBeforeCompile(result);
    assert.equal(result.preexisting, true);
    return { material, ...result };
}

test('летняя подлокация освобождает каналы текстур для фонарей, редактор возвращает снег и лёд', async()=>{
    const {THREE}=environment(),layer=createTerrainMaterialLayer(THREE);await layer.ready;
    layer.resize(2,1);layer.fill([[{terrain:'grass'},{terrain:'concrete'}]]);
    const initial=compile(layer),key=initial.material.customProgramCacheKey();
    assert.doesNotMatch(initial.fragmentShader,/uniform sampler2D terrain(?:Snow|Ice)/);
    layer.update(0,0,{terrain:'snow'});
    assert.equal(initial.material.needsUpdate,true);assert.notEqual(initial.material.customProgramCacheKey(),key);
    const snowy=compile(layer);assert.match(snowy.fragmentShader,/uniform sampler2D terrainSnowNormal/);
    assert.doesNotMatch(snowy.fragmentShader,/uniform sampler2D terrainIceNormal/);
    layer.update(1,0,{terrain:'ice'});
    const winter=compile(layer);assert.match(winter.fragmentShader,/uniform sampler2D terrainIceNormal/);
    assert.match(winter.fragmentShader,/uniform sampler2D terrainSnowNormal/);layer.dispose();
});

test('only global shores and grass walls next to water use the existing earth side texture',async()=>{
    const {THREE}=environment(),world=createTerrainMaterialLayer(THREE,{world:true}),local=createTerrainMaterialLayer(THREE);
    await world.ready;world.resize(4,4);world.update(1,1,{terrain:'water'});local.update(0,0,{terrain:'water'});
    const bank=compile(world,{sides:true,smooth:true,shore:true}),walls=compile(world,{sides:true}),unchanged=compile(local,{sides:true});
    assert.equal(bank.uniforms.terrainKinds.value.image.data[(1*4+1)*4+2],255);
    assert.equal(unchanged.uniforms.terrainKinds.value.image.data[2],255);
    assert.match(bank.fragmentShader,/float nearWater = 1\.0;/);
    assert.match(walls.fragmentShader,/nearWater = max\(nearWater,texture2D/);
    assert.doesNotMatch(unchanged.fragmentShader,/nearWater/);
    world.update(1,1,{terrain:'grass'});assert.equal(bank.uniforms.terrainKinds.value.image.data[(1*4+1)*4+2],0);
});

test('shore shoulders reuse existing grass resources and select UV projection from the geometric face',async()=>{
    const {THREE,loaded}=environment(),layer=createTerrainMaterialLayer(THREE);
    await layer.ready;const count=loaded.length,earth={},forest={},ready={value:1};
    layer.setShoreGrass({earth,forest,ready});
    const bank=compile(layer,{sides:true,smooth:true,shore:true}),ordinary=compile(layer,{sides:true});
    assert.equal(bank.uniforms.terrainShoreEarth.value,earth);
    assert.equal(bank.uniforms.terrainShoreForest.value,forest);
    assert.equal(bank.uniforms.terrainShoreGrassReady,ready);
    assert.equal(loaded.length,count,'no extra asset or texture fetch');
    assert.match(bank.fragmentShader,/cross\(dFdx\(vTerrainMaterialPosition\),dFdy\(vTerrainMaterialPosition\)\)/);
    assert.doesNotMatch(ordinary.fragmentShader,/float grassShoulder/);
    assert.match(bank.fragmentShader,/shoreGrassWeight>0.005/);
});

test('world and local layers share atlas images and release local renderer texture wrappers independently', async () => {
    const { THREE, loaded, contexts } = environment();
    const world = createTerrainMaterialLayer(THREE, {world:true}), local = createTerrainMaterialLayer(THREE);
    await world.ready;
    assert.equal(loaded.length, 35, 'Snow001: два ресурса; остальные материалы, включая лёд: по три');
    assert.equal(contexts.length, 7);
    assert.equal(world.diagnostics().atlasBytes, 64 * 1024 * 1024);
    assert.equal(world.diagnostics().iceBytes, Math.ceil(2 * 512 ** 2 * 4 * 4 / 3));
    assert.equal(world.diagnostics().materials, 12);
    world.resize(64, 32); local.resize(20, 20);
    world.fill([[{ terrain: 'rock' }, { terrain: 'sand' }]], 31, 10);
    const a = compile(world), b = compile(local);
    assert.equal(a.uniforms.terrainIceColorRoughness.value.image.width, 512);
    assert.equal(contexts[5].gray[3], 80, 'в альфа-канал льда упакован зелёный канал шероховатости ARM');
    const ice = TERRAIN_MATERIALS.find(entry => entry.terrain === 'ice');
    assert.equal(ice.repeat, 4, 'принятый локальный масштаб сохранён');
    assert.equal(ice.world.repeat * 100, 120, 'глобальный рисунок укрупнён до полотна 120 м');
    assert.match(a.fragmentShader, /if \(id == 12\.0\) return vec4\(1\.200,0\.020,/);
    assert.match(b.fragmentShader, /if \(id == 12\.0\) return vec4\(4\.000,0\.140,/);
    assert.match(a.fragmentShader, /color = terrainWorldSnowColor\(snowColor, snowUv\)/);
    assert.doesNotMatch(a.fragmentShader, /vec3\(0\.70, 0\.76, 0\.79\)/);
    assert.doesNotMatch(b.fragmentShader, /terrainWorldSnowColor/);
    for (const name of ['terrainAtlasColor','terrainAtlasNormal','terrainAtlasArm','terrainSnowColorRoughness','terrainSnowNormal','terrainIceColorRoughness','terrainIceNormal']) {
        assert.notEqual(a.uniforms[name].value, b.uniforms[name].value);
        assert.equal(a.uniforms[name].value.image, b.uniforms[name].value.image);
        assert.equal(b.uniforms[name].value.needsUpdate, true, 'local wrapper receives completed asynchronous atlas');
    }
    const lookup = a.uniforms.terrainKinds.value;
    assert.deepEqual([...lookup.image.data.slice((10 * 64 + 31) * 4, (10 * 64 + 31) * 4 + 2)], [1, 8]);
    assert.deepEqual([...lookup.image.data.slice((10 * 64 + 32) * 4, (10 * 64 + 32) * 4 + 2)], [2, 2]);
    const before = lookup.image.data.slice();
    world.update(32, 10, { terrain: 'concrete', height: 3 });
    const changed = [...lookup.image.data.keys()].filter(i => lookup.image.data[i] !== before[i]);
    assert.deepEqual(changed, [(10 * 64 + 32) * 4, (10 * 64 + 32) * 4 + 1]);
    lookup.needsUpdate = false;
    world.update(32, 10, { terrain: 'concrete', height: 5 });
    assert.equal(lookup.needsUpdate, false, 'height never uploads unchanged material IDs');
    world.clear(31, 10, 2, 1);
    assert.equal(lookup.image.data[(10 * 64 + 31) * 4], 0);
    world.dispose();
    assert.equal(lookup.disposed, true);
    assert.notEqual(a.uniforms.terrainAtlasColor.value.disposed, true);
    assert.equal(local.diagnostics().lookupBytes, 1600);
    local.dispose(); local.dispose();
    for (const name of ['terrainAtlasColor','terrainAtlasNormal','terrainAtlasArm','terrainSnowColorRoughness','terrainSnowNormal','terrainIceColorRoughness','terrainIceNormal']) {
        assert.equal(b.uniforms[name].value.disposeCount, 1, 'local renderer callbacks are released exactly once');
        assert.notEqual(a.uniforms[name].value.disposed, true, 'world textures stay valid');
    }
    const reopened = createTerrainMaterialLayer(THREE);
    await reopened.ready;
    const c = compile(reopened);
    assert.equal(c.uniforms.terrainAtlasColor.value.image, a.uniforms.terrainAtlasColor.value.image);
    assert.notEqual(c.uniforms.terrainAtlasColor.value, b.uniforms.terrainAtlasColor.value);
    assert.equal(loaded.length, 35, 'reopening reuses loaded images');
    reopened.dispose();
});

test('PBR shader retains scene callbacks and uses continuous gradients, geometric normals and side ownership', async () => {
    const { THREE } = environment();
    const layer = createTerrainMaterialLayer(THREE);
    await layer.ready;
    const top = compile(layer), sides = compile(layer, { sides: true, smooth: true });
    assert.match(top.fragmentShader, /texture2DGradEXT/);
    assert.match(top.fragmentShader, /dFdx\(terrainUv\)/);
    const gradientStart = top.fragmentShader.indexOf('vec2 terrainDx = dFdx(terrainUv)');
    const materialBranch = top.fragmentShader.indexOf('if (terrainAtlasReady > 0.5)');
    assert.ok(gradientStart >= 0 && gradientStart < materialBranch);
    const sampler = top.fragmentShader.slice(top.fragmentShader.indexOf('vec4 terrainAtlasSample('),
        top.fragmentShader.indexOf('vec3 terrainPerturbNormal('));
    assert.doesNotMatch(sampler, /dFdx|dFdy/, 'atlas gradients must not depend on material branches');
    assert.match(top.fragmentShader, /roughnessFactor = mix/);
    assert.match(top.fragmentShader, /metalnessFactor = mix/);
    assert.match(top.fragmentShader, /reflectedLight.indirectDiffuse/);
    assert.match(top.fragmentShader, /terrainPerturbNormal/);
    assert.match(sides.fragmentShader, /vTerrainOwner\.z < 0\.5/);
    assert.match(sides.fragmentShader, /vTerrainMaterialPosition\.zy/);
    assert.match(sides.vertexShader, /instanceMatrix \* terrainPosition/);
    assert.equal(top.material.extensions.shaderTextureLOD, true);
    assert.match(top.fragmentShader, /ids.x == ids.y/);
    assert.match(top.fragmentShader, /terrainCoverage/);
});

test('failed image loading keeps the original terrain shader path active', async () => {
    const { THREE } = environment(true);
    const warn = console.warn;
    console.warn = () => {};
    try {
        const layer = createTerrainMaterialLayer(THREE);
        assert.equal(await layer.ready, false);
        assert.equal(layer.diagnostics().ready, false);
        assert.match(layer.diagnostics().error, /Terrain map unavailable/);
        assert.equal(compile(layer).uniforms.terrainAtlasReady.value, 0);
    } finally { console.warn = warn; }
});

test('прозрачность задаётся отдельному слою льда, сохраняя непрозрачность грунта', async () => {
    const { THREE } = environment(), layer = createTerrainMaterialLayer(THREE, {world:true});
    await layer.ready;
    const ground=compile(layer), glaze=compile(layer,{iceGlaze:true});
    assert.doesNotMatch(ground.fragmentShader,/diffuseColor\.a \*=/);
    assert.match(glaze.fragmentShader,/diffuseColor\.a \*= clamp\(terrainIceOpacity, 0\.0, 0\.58\)/);
    assert.notEqual(ground.material.customProgramCacheKey(),glaze.material.customProgramCacheKey());
    layer.dispose();
});


test('temporary Base PBR retains source Normal ARM and leaves local swamp settings intact', async () => {
    const { THREE } = environment();
    const world = createTerrainMaterialLayer(THREE, { world: true });
    const local = createTerrainMaterialLayer(THREE);
    await world.ready;
    const a = compile(world), b = compile(local);
    assert.equal(a.uniforms.terrainAtlasColor.value.image, b.uniforms.terrainAtlasColor.value.image);
    assert.doesNotMatch(a.fragmentShader, /float wet = smoothstep/);
    assert.match(a.fragmentShader, /if \(id == 3\.0\) return vec4\(4\.000,0\.550,0\.000,0\.000\)/);
    assert.match(b.fragmentShader, /if \(id == 3\.0\) return vec4\(4\.000,0\.550,0\.000,0\.000\)/);
    assert.doesNotMatch(b.fragmentShader, /float wet = smoothstep/);
    assert.notEqual(a.material.customProgramCacheKey(), b.material.customProgramCacheKey());
    const swamp = TERRAIN_MATERIALS.find(entry => entry.terrain === 'swamp');
    assert.equal(swamp.asset, 'mud_forest');
    assert.deepEqual(swamp.albedo, { saturation: 0.3, gain: 1.1, lift: 0.2, tint: [0.88, 1, 0.86] });
    assert.equal(swamp.normalStrength, 0.55);
    assert.equal(TERRAIN_MATERIALS.find(entry => entry.terrain === 'snow').repeat, 3);
    const sand = new Uint8ClampedArray([150, 140, 120, 255]);
    adjustTerrainAlbedo(sand, TERRAIN_MATERIALS.find(entry => entry.terrain === 'sand'));
    assert.ok(sand[0] > sand[1] && sand[1] > sand[2], 'sand remains beige rather than blue-gray');
    assert.ok(sand[0] - sand[2] < 25 && sand[2] > 150, 'sand remains light and subdued');
    const wood = new Uint8ClampedArray([180, 95, 40, 255, 110, 60, 25, 255]);
    adjustTerrainAlbedo(wood, TERRAIN_MATERIALS.find(entry => entry.terrain === 'wood_floor'));
    assert.ok(wood[0] - wood[1] < 50);
    assert.ok(wood[0] > wood[2] && wood[0] > wood[4]);
    assert.equal(wood[3], 255);
});


test('world editor excludes building floors while saved world material IDs remain supported', () => {
    const oldDocument = globalThis.document;
    globalThis.document = { createElement: () => ({}) };
    try {
        const select = { options: [], appendChild(option) {
            option.remove = () => { this.options = this.options.filter(item => item !== option); };
            this.options.push(option);
        } };
        // Also remove options if a list was already populated for a local editor.
        addTerrainMaterialOptions(select);
        addTerrainMaterialOptions(select, { world: true });
        addTerrainMaterialOptions(select, { world: true });
        for (const kind of ['wood_floor', 'granite_tiles']) {
            assert.equal(select.options.some(option => option.value === kind), false);
        }
        assert.ok(select.options.some(option => option.value === 'bare_dirt'));
        assert.ok(select.options.some(option => option.value === 'concrete'));
        assert.equal(terrainMaterialIndex({ terrain: 'granite_tiles' }), 10);
        assert.equal(terrainMaterialIndex({ terrain: 'wood_floor' }), 11);
        assert.equal(select.options.find(o => o.value === 'ice')?.textContent, 'Лёд');
        addTerrainMaterialOptions(select);
        for (const kind of ['wood_floor', 'granite_tiles']) {
            assert.equal(select.options.filter(option => option.value === kind).length, 1);
        }
    } finally { globalThis.document = oldDocument; }
});


test('quality regression: swamp uses real pixel gradients; sand and snow retain detail without a single short repeat', async () => {
    const { THREE } = environment();
    const layer = createTerrainMaterialLayer(THREE, { world: true });
    await layer.ready;
    const world = compile(layer).fragmentShader;
    assert.doesNotMatch(world, /length\(dx\)|vec2\(0\.035|materialNormal\.xy \*= 0\.3/);
    assert.match(world, /turn \* dx \* 1\.37/);
    assert.match(world, /turn \* dy \* 1\.37/);
    assert.match(world, /if \(id == 2\.0 \|\| id == 6\.0\)/);
    assert.match(world, /arm = vec3\(1\.0, clamp\(snow\.a, 0\.88, 0\.98\), 0\.0\)/);
    assert.match(world, /uv \* 0\.0375/);
    const snow = TERRAIN_MATERIALS.find(entry => entry.terrain === 'snow');
    const shades = new Uint8ClampedArray([20,20,20,255,180,180,180,255,230,230,230,255]);
    adjustTerrainAlbedo(shades,snow);
    assert.ok(shades[0] < 30, 'no white floor is imposed on the new footprint-free source');
    assert.ok(shades[8] > shades[4] && shades[4] > shades[0], 'snow retains relief and tonal detail');
    for(const kind of ['sand','snow']) {
        const entry=TERRAIN_MATERIALS.find(entry=>entry.terrain===kind);
        assert.ok((512-16)/entry.repeat >= 80, 'near-view texel density cannot regress to 27-31 texels per tile');
    }
});
