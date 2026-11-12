const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '../app/static/js');
const detailSource = fs.readFileSync(path.join(root, 'worldGrassDetail.js'), 'utf8');
const textureSource = fs.readFileSync(path.join(root, 'locationTerrainTexture.js'), 'utf8');
const subtypeSource = fs.readFileSync(path.join(root, 'locationGrassSubtype.js'), 'utf8');
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const textureUrl = moduleUrl(textureSource.replace("'./locationGrassSubtype.js'",
    `'${moduleUrl(subtypeSource)}'`));

test('world grass detail reuses two photo textures and keeps the opaque base map', async () => {
    const { createWorldGrassDetail } = await import(moduleUrl(detailSource));
    const textures = [];
    class Texture {
        constructor() { textures.push(this); }
    }
    const THREE = { Texture, RepeatWrapping: 1, LinearFilter: 2,
        LinearMipmapLinearFilter: 3, SRGBColorSpace: 'srgb' };
    const detail = createWorldGrassDetail(THREE);
    const images = { plain: {}, forest: {} };
    detail.setImages(images);
    assert.equal(textures.length, 2);
    assert.equal(textures[0].image, images.plain);
    assert.equal(textures[1].image, images.forest);
    assert.equal(textures[0].wrapS, THREE.RepeatWrapping);
    assert.equal(textures[0].minFilter, THREE.LinearMipmapLinearFilter);
    assert.equal(textures[0].colorSpace, THREE.SRGBColorSpace);
    const first = {};
    const second = {};
    detail.apply(first);
    detail.apply(second);
    assert.equal(first.customProgramCacheKey(), second.customProgramCacheKey());
    const shader = {
        uniforms: {},
        vertexShader: '#include <common>\n#include <project_vertex>',
        fragmentShader: '#include <common>\n#include <map_fragment>\n#include <color_fragment>',
    };
    first.onBeforeCompile(shader);
    assert.equal(shader.uniforms.worldGrassEarth.value, textures[0]);
    assert.equal(shader.uniforms.worldGrassForest.value, textures[1]);
    assert.equal(shader.uniforms.worldGrassReady.value, 1);
    assert.match(shader.vertexShader, /modelMatrix \* vec4\(transformed, 1\.0\)/);
    assert.match(shader.fragmentShader, /worldGroundTexel\.a/);
    assert.match(shader.fragmentShader, /diffuseColor\.rgb \*= worldGroundTexel\.rgb/);
    assert.doesNotMatch(shader.fragmentShader, /diffuseColor \*=.*worldGroundTexel/);
    assert.match(shader.fragmentShader, /vWorldGrassPosition\.xz \* 0\.125/);
    assert.match(shader.fragmentShader, /worldGrassCoverage \* grassFade/);
    detail.setImages(null);
    assert.equal(shader.uniforms.worldGrassReady.value, 0);
});

test('global alpha records exact grass blend without changing RGB or other soils', async () => {
    const { createTerrainPixelsRegion, updateTerrainPixelsRegion } = await import(textureUrl);
    const kinds = ['grass', 'sand', 'swamp', 'rock', 'water'];
    const sample = (x, z) => kinds[Math.max(0, Math.min(4, x))];
    const args = [sample, 0, 0, 80, 16, false, null];
    const oldImage = createTerrainPixelsRegion(...args);
    const image = createTerrainPixelsRegion(...args, true);
    for (let i = 0; i < image.data.length; i += 4) {
        assert.deepEqual(image.data.slice(i, i + 3), oldImage.data.slice(i, i + 3));
    }
    const alpha = x => image.data[(8 * image.width + x) * 4 + 3];
    assert.equal(alpha(2), 255);
    assert.ok(alpha(14) > 0 && alpha(14) < 255);
    assert.equal(alpha(30), 0);
    assert.equal(alpha(46), 0);
    const edited = (x, z) => x < 2 ? 'grass' : sample(x, z);
    updateTerrainPixelsRegion(image, edited, 0, 0, { minX: 1, maxX: 1, minZ: 0, maxZ: 4 },
        16, false, null, true);
    const rebuilt = createTerrainPixelsRegion(edited, 0, 0, 80, 16, false, null, true);
    assert.deepEqual(image.data, rebuilt.data);
});

test('neighboring chunk regions produce identical grass masks at shared world pixels', async () => {
    const { createTerrainPixelsRegion } = await import(textureUrl);
    const sample = (x, z) => (x + z) % 5 < 2 ? 'grass' : 'sand';
    const left = createTerrainPixelsRegion(sample, 0, 0, 64, 4, false, null, true);
    const right = createTerrainPixelsRegion(sample, 8, 0, 64, 4, false, null, true);
    for (let z = 0; z < 64; z++) {
        for (let x = 32; x < 64; x++) {
            const offsetLeft = (z * 64 + x) * 4;
            const offsetRight = (z * 64 + x - 32) * 4;
            assert.deepEqual(left.data.slice(offsetLeft, offsetLeft + 4),
                right.data.slice(offsetRight, offsetRight + 4));
        }
    }
});
