import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

class TextureLoader {
    load(url, onLoad) { onLoad({ url, disposals:0, dispose(){this.disposals++;} }); }
}

class Vector3 {
    fromBufferAttribute(attribute, index) {
        this.x = attribute.getX(index);
        this.y = attribute.getY(index);
        this.z = attribute.getZ(index);
        return this;
    }
}

const context = vm.createContext({ console });
const shaderChunks = {
    map_fragment: '#ifdef USE_MAP\nvec4 texelColor = texture2D(map, vMapUv);\n#endif',
    normal_fragment_maps: '#ifdef USE_NORMALMAP\nvec3 mapN = texture2D(normalMap, vNormalMapUv).xyz;\n#endif',
    roughnessmap_fragment: '#ifdef USE_ROUGHNESSMAP\nroughnessFactor *= texture2D(roughnessMap, vRoughnessMapUv).g;\n#endif',
    metalnessmap_fragment: '#ifdef USE_METALNESSMAP\nmetalnessFactor *= texture2D(metalnessMap, vMetalnessMapUv).b;\n#endif',
    aomap_fragment: '#ifdef USE_AOMAP\nambientOcclusion *= texture2D(aoMap, vAoMapUv).r;\n#endif',
};
const three = new vm.SyntheticModule(['TextureLoader', 'Vector3', 'RepeatWrapping', 'SRGBColorSpace', 'ShaderChunk'], function () {
    this.setExport('TextureLoader', TextureLoader);
    this.setExport('Vector3', Vector3);
    this.setExport('RepeatWrapping', 'repeat');
    this.setExport('SRGBColorSpace', 'srgb');
    this.setExport('ShaderChunk', shaderChunks);
}, { context });
const source = await readFile(new URL('../app/static/js/locationPbrMaterials.js', import.meta.url), 'utf8');
const module = new vm.SourceTextModule(source, { context });
await module.link(specifier => {
    assert.equal(specifier, 'three');
    return three;
});
await module.evaluate();
const { alignLocationPbrFloorUvs, applyLocationPbrMaterial, releaseLocationPbrMaterial, releaseLocationPbrTextures,
    tileLocationPbrBoxUvs } = module.namespace;

test('PBR texture slots use color sRGB and share the packed ARM map', async () => {
    const material = { userData: {}, color: { set(value) { this.value = value; } } };
    assert.equal(applyLocationPbrMaterial(material, {
        color: '/color.jpg', normal: '/normal.jpg', arm: '/arm.jpg',
    }), true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(material.color.value, 0xffffff);
    assert.equal(material.map.colorSpace, 'srgb');
    assert.equal(material.normalMap.url, '/normal.jpg');
    assert.equal(material.aoMap, material.roughnessMap);
    assert.equal(material.roughnessMap, material.metalnessMap);
    assert.equal(material.map.wrapS, 'repeat');
    releaseLocationPbrMaterial(material);
    assert.equal(material.userData.pbrActive, false);
});

test('height parallax self-shadows directional light and composes the existing shader hooks', async () => {
    let previousCompileCount = 0;
    const material = {
        userData: {},
        color: { set(value) { this.value = value; } },
        onBeforeCompile(shader) { previousCompileCount++; shader.fragmentShader += '\n// existing static-light hook'; },
        customProgramCacheKey() { return 'existing-static-light-key'; },
    };
    applyLocationPbrMaterial(material, {
        color: '/concrete/color.jpg', normal: '/concrete/normal.jpg', arm: '/concrete/arm.jpg',
        height: '/concrete/height.jpg', heightScale: 0.02, heightFade: [12, 36],
    });
    await new Promise(resolve => setImmediate(resolve));
    const shader = {
        uniforms: {},
        fragmentShader: `#include <common>
            varying vec2 vMapUv; varying vec2 vAoMapUv; varying vec3 vViewPosition;
            void main() {
                #include <map_fragment>
                #include <roughnessmap_fragment>
                #include <metalnessmap_fragment>
                #include <normal_fragment_maps>
                #include <lights_fragment_end>
                #include <aomap_fragment>
            }`,
    };
    const compile = material.onBeforeCompile;
    compile(shader, {});
    assert.equal(previousCompileCount, 1);
    assert.equal(shader.uniforms.locationHeightMap.value.url, '/concrete/height.jpg');
    assert.match(shader.fragmentShader, /locationHeightSelfShadow/);
    assert.match(shader.fragmentShader, /directionalLights\[0\]\.direction/);
    assert.match(shader.fragmentShader, /locationReliefSun\[0\]\.color \*= locationHeightSelfShadow/);
    assert.doesNotMatch(shader.fragmentShader, /reflectedLight\.direct(?:Diffuse|Specular) \*=/);
    assert.match(shader.fragmentShader, /reflectedLight\.indirectDiffuse \*= locationCavityShade/);
    assert.match(shader.fragmentShader, /#ifdef USE_AOMAP\s+ambientOcclusion \*= texture2D\(aoMap, \(vAoMapUv \+ locationParallaxOffset\)\)/);
    assert.match(shader.fragmentShader, /existing static-light hook/);
    assert.match(material.customProgramCacheKey(), /existing-static-light-key/);
    assert.match(material.customProgramCacheKey(), /height-parallax/);
});

test('PBR box UVs repeat by physical dimensions without changing base UV2 references', () => {
    const coords = Array.from({ length: 24 }, () => [1, 1]);
    const uv = {
        count: 24,
        getX(index) { return coords[index][0]; },
        getY(index) { return coords[index][1]; },
        setXY(index, u, v) { coords[index] = [u, v]; },
        clone() { return { values: coords.map(value => [...value]) }; },
    };
    const geometry = { attributes: { uv }, setAttribute(name, value) { this.attributes[name] = value; } };
    tileLocationPbrBoxUvs(geometry, 4, 2, 0.2);
    assert.equal(coords[0][0], 0.1);
    assert.equal(coords[0][1], 1);
    assert.equal(coords[16][0], 2);
    assert.equal(coords[16][1], 1);
    assert.notEqual(geometry.attributes.uv2, uv);
});

test('scene exit releases cached GPU texture listeners while reopening reuses loaded images',async()=>{
    const create=()=>({userData:{},color:{set(){}}}),maps={color:'/exit-color.jpg',normal:'/exit-normal.jpg',arm:'/exit-arm.jpg'};
    const first=create();applyLocationPbrMaterial(first,maps);
    await new Promise(resolve=>setImmediate(resolve));
    releaseLocationPbrMaterial(first);releaseLocationPbrTextures();
    await new Promise(resolve=>setImmediate(resolve));
    for(const texture of [first.map,first.normalMap,first.aoMap])assert.equal(texture.disposals,1);
    const reopened=create();applyLocationPbrMaterial(reopened,maps);
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(reopened.map,first.map);assert.equal(reopened.aoMap,first.aoMap);
    assert.equal(reopened.userData.pbrActive,true);
    releaseLocationPbrMaterial(reopened);
});

test('neighboring PBR floors share the same UV phase at their world-space edge', () => {
    const makeTile = (centerX, localX) => {
        const values = [];
        const geometry = {
            attributes: {
                position: { count: 1, getX() { return localX; }, getY() { return 0; }, getZ() { return 0; } },
                normal: { getY() { return 1; } },
                uv: { setXY(_index, u, v) { values.push([u, v]); }, clone() { return {}; } },
            },
            setAttribute(name, value) { this.attributes[name] = value; },
        };
        alignLocationPbrFloorUvs(geometry, { localToWorld(point) { point.x += centerX; return point; } });
        assert.ok(geometry.attributes.uv2);
        return values[0];
    };
    assert.deepEqual(makeTile(0.5, 0.5), makeTile(1.5, -0.5));
    assert.deepEqual(makeTile(0.5, 0.5), [0.5, 0]);
});
