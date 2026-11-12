const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function moduleUrl(name) {
    return `data:text/javascript;base64,${fs.readFileSync(path.join(__dirname,
        '../app/static/js', name)).toString('base64')}`;
}

const source = fs.readFileSync(path.join(__dirname,
    '../app/static/js/locationDirectionalLightFieldPrototype.js'), 'utf8');
const prototype = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function lamp(overrides = {}) {
    return { id: 'lamp', level: 0, x: 2.25, y: 1.4, z: 3.25,
        color: '#ffc080', intensity: 2.5, distance: 6, ...overrides };
}

test('freestanding vertical and horizontal faces respond to actual light direction', async () => {
    const { createDirectionalLightField } = await prototype;
    const field = createDirectionalLightField({ width: 8, height: 8,
        levels: [{ id: 0, y: 0 }] });
    field.rebuild({ lamps: [lamp()] });
    const toward = field.sample(3.25, 1.4, 3.25, { x: -1, y: 0, z: 0 });
    const away = field.sample(3.25, 1.4, 3.25, { x: 1, y: 0, z: 0 });
    assert.ok(toward[0] > 0.5, `freestanding cabinet face was dark: ${toward}`);
    assert.deepEqual(away, [0, 0, 0]);
    assert.ok(toward[0] > toward[2], 'lamp color was lost');
    const upper = field.sample(3.25, 2.8, 3.25, { x: 0, y: -1, z: 0 });
    const top = field.sample(3.25, 2.8, 3.25, { x: 0, y: 1, z: 0 });
    assert.ok(upper[0] > 0, 'underside above lamp should be lit');
    assert.deepEqual(top, [0, 0, 0]);
});

test('three height slices interpolate and lamps at different heights light different faces', async () => {
    const { createDirectionalLightField } = await prototype;
    const field = createDirectionalLightField({ width: 8, height: 8,
        levels: [{ id: 0, y: 0 }] });
    field.rebuild({ lamps: [lamp({ y: 2.6, x: 3.25 })] });
    const topOfTable = field.sample(3.25, 1.4, 3.25, { x: 0, y: 1, z: 0 })[0];
    const nearCeiling = field.sample(3.25, 2.8, 3.25, { x: 0, y: 1, z: 0 })[0];
    assert.ok(topOfTable > 0);
    assert.equal(nearCeiling, 0, 'top slice should not invent a light from above');
    const low = field.sample(3.25, 0, 3.25, { x: 0, y: 1, z: 0 })[0];
    const middle = field.sample(3.25, 0.7, 3.25, { x: 0, y: 1, z: 0 })[0];
    assert.ok(middle > low && middle < topOfTable,
        `height interpolation failed: ${low}, ${middle}, ${topOfTable}`);
});

test('opaque wall and closed door block light; open doorway transmits it', async () => {
    const { createDirectionalLightField } = await prototype;
    const field = createDirectionalLightField({ width: 10, height: 7,
        levels: [{ id: 0, y: 0 }] });
    const source = lamp({ x: 3.25, z: 3.25, y: 1.4, distance: 8 });
    const solid = { level: 0, x1: 5, z1: 0, x2: 5, z2: 7,
        bottomY: 0, topY: 3 };
    field.rebuild({ lamps: [source], walls: [solid] });
    assert.deepEqual(field.sample(6.25, 1.4, 3.25, { x: -1, y: 0, z: 0 }), [0, 0, 0]);
    const doorway = [
        { ...solid, z2: 2.5 },
        { ...solid, z1: 4.5 },
        { ...solid, z1: 2.5, z2: 4.5, bottomY: 2, topY: 3 },
    ];
    field.rebuild({ lamps: [source], walls: doorway });
    assert.ok(field.sample(6.25, 1.4, 3.25, { x: -1, y: 0, z: 0 })[0] > 0);
    field.rebuild({ lamps: [source], walls: [...doorway,
        { ...solid, z1: 2.5, z2: 4.5, bottomY: 0, topY: 2 }] });
    assert.deepEqual(field.sample(6.25, 1.4, 3.25, { x: -1, y: 0, z: 0 }), [0, 0, 0]);
});

test('levels isolate sources and switched-off lamps do not contribute', async () => {
    const { createDirectionalLightField } = await prototype;
    const field = createDirectionalLightField({ width: 8, height: 8,
        levels: [{ id: 0, y: 0 }, { id: 1, y: 3 }] });
    const sources = [lamp({ intensity: 0 }), lamp({ id: 'upper', level: 1, y: 4.4 })];
    const stats = field.rebuild({ lamps: sources });
    assert.equal(stats.activeLights, 1);
    assert.deepEqual(field.sample(3.25, 1.4, 3.25, { x: -1, y: 0, z: 0 }, 0), [0, 0, 0]);
    assert.ok(field.sample(3.25, 4.4, 3.25, { x: -1, y: 0, z: 0 }, 1)[0] > 0);
    assert.deepEqual(field.sample(3.25, 4.4, 3.25, { x: -1, y: 0, z: 0 }, 0), [0, 0, 0]);
    assert.deepEqual(stats.lampStats.map(item => item.route), ['off', 'field']);
});

test('stationary spotlight illuminates its cone, not the rear or the space behind a wall', async () => {
    const { createDirectionalLightField } = await prototype;
    const field = createDirectionalLightField({ width: 8, height: 8,
        levels: [{ id: 0, y: 0 }] });
    const spot = lamp({ id: 'spot', kind: 'spot', x: 2.25, y: 1.4, z: 3.25,
        height: 2, azimuth: 0 });
    const up = { x: 0, y: 1, z: 0 };
    field.rebuild({ lamps: [spot] });
    assert.ok(field.sample(4.25, 0, 3.25, up)[0] > 0);
    assert.deepEqual(field.sample(0.25, 0, 3.25, up), [0, 0, 0]);
    assert.deepEqual(field.sample(2.25, 0, 5.25, up), [0, 0, 0]);
    field.rebuild({ lamps: [spot], walls: [{ level: 0, x1: 3.5, x2: 3.5,
        z1: 0, z2: 8, bottomY: 0, topY: 3 }] });
    assert.deepEqual(field.sample(4.25, 0, 3.25, up), [0, 0, 0]);
    field.rebuild({ lamps: [{ ...spot, intensity: 0 }] });
    assert.deepEqual(field.sample(4.25, 0, 3.25, up), [0, 0, 0]);
});

test('one packed texture, linear RGBM superposition and material lifecycle', async () => {
    const { createDirectionalLightField, createDirectionalLightFieldTexture,
        applyDirectionalLightFieldMaterial, removeDirectionalLightFieldMaterial } = await prototype;
    const field = createDirectionalLightField({ width: 8, height: 8,
        levels: [{ id: 0, y: 0 }, { id: 1, y: 3 }] });
    field.rebuild({ lamps: [lamp()] });
    const first = field.sample(3.25, 1.4, 3.25, { x: -1, y: 0, z: 0 })[0];
    field.rebuild({ lamps: [lamp(), lamp({ id: 'other' })] });
    const doubled = field.sample(3.25, 1.4, 3.25, { x: -1, y: 0, z: 0 })[0];
    assert.ok(Math.abs(doubled - 2 * first) < 0.02, `${first}, ${doubled}`);
    assert.ok(field.textureHeight < field.rows * field.planes,
        'atlas was stacked vertically instead of packed');
    const THREE = { DataTexture: class {
        constructor(data, width, height) { Object.assign(this, { data, width, height }); }
        dispose() { this.disposed = true; }
    }, RGBAFormat: 1, LinearFilter: 2, ClampToEdgeWrapping: 3 };
    const texture = createDirectionalLightFieldTexture(THREE, field);
    assert.equal(texture.width, field.textureWidth);
    assert.equal(texture.height, field.textureHeight);
    assert.equal(texture.minFilter, THREE.LinearFilter);
    assert.equal(texture.magFilter, THREE.LinearFilter);
    assert.equal(texture.generateMipmaps, false);
    const maps = { map: {}, normalMap: {}, roughnessMap: {}, emissiveMap: {} };
    const oldCompile = () => {};
    const physical = { isMeshStandardMaterial: true, isMeshPhysicalMaterial: true,
        userData: {}, ...maps, onBeforeCompile: oldCompile,
        dispose() { this.disposals = (this.disposals || 0) + 1; },
        customProgramCacheKey: () => 'physical' };
    applyDirectionalLightFieldMaterial(physical, texture, field, 1);
    const shader = { uniforms: {}, vertexShader: '#include <common>\n#include <project_vertex>',
        fragmentShader: '#include <common>\n#include <lights_fragment_end>' };
    physical.onBeforeCompile(shader);
    assert.match(shader.fragmentShader, /inverseTransformDirection\(normal, viewMatrix\)/);
    assert.match(shader.fragmentShader, /vec2 cell = clamp\(position \* directionalFieldSamplesPerMeter - vec2\(0\.5\),/);
    assert.match(shader.fragmentShader, /directionalFieldGrid - vec2\(1\.0\)\)/);
    assert.doesNotMatch(shader.fragmentShader, /floor\(position \* directionalFieldSamplesPerMeter\)/);
    assert.match(shader.fragmentShader, /fieldIrradiance \*= PI/);
    assert.equal(shader.uniforms.directionalFieldLevel.value, 1);
    assert.equal(shader.uniforms.directionalFieldMap.value, texture);
    assert.equal(shader.uniforms.directionalFieldSamplesPerMeter.value, 2);
    assert.equal(shader.uniforms.directionalFieldEnabled, undefined);
    assert.doesNotMatch(shader.fragmentShader, /directionalFieldEnabled/);
    assert.deepEqual(Object.fromEntries(Object.keys(maps).map(key => [key, physical[key]])), maps);
    assert.equal(removeDirectionalLightFieldMaterial(physical), true);
    assert.equal(physical.disposals, 1);
    assert.equal(physical.onBeforeCompile, oldCompile);
    assert.equal(physical.userData.directionalLightFieldPrototype, undefined);
    texture.dispose();
    assert.equal(texture.disposed, true);
});

test('coincident lamp and sample have no black hole; rebuilding clears old lighting', async () => {
    const { createDirectionalLightField } = await prototype;
    const field = createDirectionalLightField({ width: 8, height: 8,
        levels: [{ id: 0, y: 0 }] });
    field.rebuild({ lamps: [lamp()] });
    for (const normal of [{ x: 1, y: 0, z: 0 }, { x: 0, y: -1, z: 0 }]) {
        assert.ok(field.sample(2.25, 1.4, 3.25, normal)[0] > 0,
            `coincident lamp left a dark face: ${JSON.stringify(normal)}`);
    }
    field.rebuild({ lamps: [] });
    assert.deepEqual(field.sample(2.25, 1.4, 3.25, { x: 1, y: 0, z: 0 }), [0, 0, 0]);
    assert.ok(field.pixels.every(value => value === 0));
});

test('rotated face uses its world normal and non-integer dimensions use fixed cell pitch', async () => {
    const { createDirectionalLightField, applyDirectionalLightFieldMaterial,
        removeDirectionalLightFieldMaterial } = await prototype;
    const field = createDirectionalLightField({ width: 8.3, height: 7.7,
        levels: [{ id: 0, y: 0 }], samplesPerMeter: 3 });
    field.rebuild({ lamps: [lamp({ x: 2.25, z: 3.25 })] });
    const x = field.sample(3.25, 1.4, 3.25, { x: -1, y: 0, z: 0 });
    const z = field.sample(3.25, 1.4, 3.25, { x: 0, y: 0, z: 1 });
    const rotated = field.sample(3.25, 1.4, 3.25,
        { x: -Math.SQRT1_2, y: 0, z: Math.SQRT1_2 });
    assert.ok(Math.abs(rotated[0] - Math.SQRT1_2 * (x[0] + z[0])) < 1e-5);
    assert.equal(field.columns, 25);
    assert.equal(field.rows, 24);
    const material = { isMeshStandardMaterial: true, userData: {},
        onBeforeCompile: () => {}, customProgramCacheKey: () => 'base' };
    applyDirectionalLightFieldMaterial(material, {}, field);
    const shader = { uniforms: {}, vertexShader: '#include <common>\n#include <project_vertex>',
        fragmentShader: '#include <common>\n#include <lights_fragment_end>' };
    material.onBeforeCompile(shader);
    assert.equal(shader.uniforms.directionalFieldSamplesPerMeter.value, 3);
    assert.match(shader.fragmentShader, /vec2 cell = clamp\(position \* directionalFieldSamplesPerMeter - vec2\(0\.5\),/);
    assert.equal(material.customProgramCacheKey(), 'base:directional-field-v3');
    removeDirectionalLightFieldMaterial(material);
    assert.equal(material.customProgramCacheKey(), 'base');
});

test('материал пропускает пустое поле и получает изменения без повторной компиляции', async () => {
    const { createDirectionalLightField, applyDirectionalLightFieldMaterial } = await prototype;
    const field = createDirectionalLightField({ width: 8, height: 8, levels: [{ id: 0, y: 0 }] });
    const material = { isMeshStandardMaterial: true, userData: {} };
    applyDirectionalLightFieldMaterial(material, { id: 1 }, field);
    const shader = { uniforms: {}, vertexShader: '#include <common>\n#include <project_vertex>',
        fragmentShader: '#include <common>\n#include <lights_fragment_end>' };
    material.onBeforeCompile(shader);
    const active = shader.uniforms.directionalFieldActive;
    assert.match(shader.fragmentShader, /if \(directionalFieldActive\)/);
    assert.equal(active.value, false);
    field.rebuild({ lamps: [lamp()] });
    assert.equal(active.value, true);
    assert.ok(field.pixels.some(Boolean));
    field.rebuild({ lamps: [lamp({ intensity: 0 })] });
    assert.equal(active.value, false);
    assert.ok(field.pixels.every(value => value === 0));
});

test('many sources reuse atlas memory; all active lamps contribute without a dynamic routing mask', async () => {
    const { createDirectionalLightField } = await prototype;
    const field = createDirectionalLightField({ width: 12, height: 6,
        levels: [{ id: 0, y: 0 }] });
    const pixels = field.pixels;
    const lamps = Array.from({ length: 60 }, (_, index) => lamp({
        id: `lamp-${index}`, x: 2.25 + index % 4, z: 1.25 + index % 3,
    }));
    const stats = field.rebuild({ lamps: lamps.map((l, i) => i === 0 ? { ...l, intensity: 0 } : l) });
    assert.equal(stats.activeLights, 59);
    assert.equal(stats.lampStats[0].route, 'off');
    assert.equal(field.pixels, pixels);
    field.rebuild({ lamps: [{ ...lamps[0], intensity: 0 }] });
    assert.ok(field.pixels.every(value => value === 0));
    assert.equal(field.pixels, pixels);
    assert.equal(field.rebuild({ lamps }).activeLights, 60);
});
