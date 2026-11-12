import * as THREE from 'three';

const loader = new THREE.TextureLoader();
const textureCache = new Map();
const compressedTextureCache = new WeakMap();

function loadColorTexture(maps, renderer) {
    const loadOriginal = () => loadTexture(maps.color, true);
    if (!maps.colorKtx2 || !renderer?.capabilities?.isWebGL2) return loadOriginal();

    let textures = compressedTextureCache.get(renderer);
    if (!textures) compressedTextureCache.set(renderer, textures = new Map());
    if (!textures.has(maps.colorKtx2)) {
        const texture = import('three/addons/loaders/KTX2Loader.js').then(async ({ KTX2Loader }) => {
            const compressedLoader = new KTX2Loader()
                .setTranscoderPath('/static/vendor/three-0.162.0/examples/jsm/libs/basis/')
                .setWorkerLimit(1)
                .detectSupport(renderer);
            try {
                const result = await compressedLoader.loadAsync(maps.colorKtx2);
                result.wrapS = result.wrapT = THREE.RepeatWrapping;
                return result;
            } finally {
                compressedLoader.dispose();
            }
        });
        textures.set(maps.colorKtx2, texture);
    }
    return textures.get(maps.colorKtx2).catch(loadOriginal);
}

function loadTexture(url, colorSpace = false) {
    if (!textureCache.has(url)) {
        textureCache.set(url, new Promise((resolve, reject) => {
            loader.load(url, texture => {
                texture.wrapS = THREE.RepeatWrapping;
                texture.wrapT = THREE.RepeatWrapping;
                if (colorSpace) texture.colorSpace = THREE.SRGBColorSpace;
                resolve(texture);
            }, undefined, reject);
        }).catch(error => {
            textureCache.delete(url);
            throw error;
        }));
    }
    return textureCache.get(url);
}

function replaceShaderChunk(shader, name, uvName) {
    const token = `#include <${name}>`;
    const chunk = THREE.ShaderChunk[name];
    if (!chunk || !shader.fragmentShader.includes(token)) return false;
    shader.fragmentShader = shader.fragmentShader.replace(token,
        chunk.replaceAll(uvName, `(${uvName} + locationParallaxOffset)`));
    return true;
}

function addLocationHeightParallax(material, texture, maps) {
    material.userData.locationHeightMap = texture;
    material.userData.locationParallaxScale = maps.heightScale ?? 0.012;
    if (material.userData.locationParallaxHookInstalled) {
        material.needsUpdate = true;
        return;
    }
    const previousCompile = material.onBeforeCompile;
    const previousKey = material.customProgramCacheKey?.bind(material);
    material.onBeforeCompile = function(shader, renderer) {
        const owner = this ?? material;
        previousCompile?.call(owner, shader, renderer);
        shader.uniforms.locationHeightMap = { value: owner.userData.locationHeightMap };
        shader.uniforms.locationParallaxScale = { value: owner.userData.locationParallaxScale };
        shader.uniforms.locationParallaxFade = { value: maps.heightFade || [12, 36] };
        shader.uniforms.locationHeightRange = { value: maps.heightRange || [0, 1] };
        shader.fragmentShader = shader.fragmentShader.replace(
            '#include <common>',
            `#include <common>
                uniform sampler2D locationHeightMap;
                uniform float locationParallaxScale;
                uniform vec2 locationParallaxFade;
                uniform vec2 locationHeightRange;
                float locationSampleHeight(vec2 uv) {
                    float height = texture2D(locationHeightMap, uv).r;
                    return clamp((height - locationHeightRange.x) / (locationHeightRange.y - locationHeightRange.x),
                        0.0, 1.0);
                }
                float locationHeightSelfShadow(vec2 uv, float baseHeight, vec3 lightDirection,
                    vec3 tangent, vec3 bitangent, vec3 surfaceNormal, float fade) {
                    vec3 lightTangent = vec3(dot(lightDirection, tangent), dot(lightDirection, bitangent),
                        dot(lightDirection, surfaceNormal));
                    if (lightTangent.z <= 0.03 || lightTangent.z >= 0.95 || fade <= 0.001) return 1.0;
                    vec2 rayDirection = lightTangent.xy / max(lightTangent.z, 0.18);
                    float rayLength = min(length(rayDirection), 3.0);
                    if (rayLength <= 0.001) return 1.0;
                    rayDirection = normalize(rayDirection) * rayLength;
                    float shadow = 1.0;
                    for (int stepIndex = 1; stepIndex <= 3; stepIndex++) {
                        float stepFraction = float(stepIndex) * 0.16;
                        vec2 sampleUv = uv + rayDirection * locationParallaxScale * stepFraction;
                        float rayHeight = baseHeight + stepFraction;
                        float sampleHeight = locationSampleHeight(sampleUv);
                        float blocked = smoothstep(rayHeight + 0.01, rayHeight + 0.06, sampleHeight);
                        shadow = min(shadow, mix(1.0, 0.2, blocked));
                    }
                    return mix(1.0, shadow, fade);
                }`
        );
        const offsetCode = `
            vec3 locationParallaxPosition = -vViewPosition;
            vec3 locationParallaxDp1 = dFdx(locationParallaxPosition);
            vec3 locationParallaxDp2 = dFdy(locationParallaxPosition);
            vec2 locationParallaxDu1 = dFdx(vMapUv);
            vec2 locationParallaxDu2 = dFdy(vMapUv);
            vec3 locationParallaxNormal = normalize(cross(locationParallaxDp1, locationParallaxDp2));
            vec3 locationParallaxView = normalize(vViewPosition);
            if (dot(locationParallaxNormal, locationParallaxView) < 0.0) locationParallaxNormal = -locationParallaxNormal;
            vec3 locationParallaxDp2Perp = cross(locationParallaxDp2, locationParallaxNormal);
            vec3 locationParallaxDp1Perp = cross(locationParallaxNormal, locationParallaxDp1);
            vec3 locationParallaxTangent = locationParallaxDp2Perp * locationParallaxDu1.x
                + locationParallaxDp1Perp * locationParallaxDu2.x;
            vec3 locationParallaxBitangent = locationParallaxDp2Perp * locationParallaxDu1.y
                + locationParallaxDp1Perp * locationParallaxDu2.y;
            float locationParallaxInvMax = inversesqrt(max(max(dot(locationParallaxTangent, locationParallaxTangent),
                dot(locationParallaxBitangent, locationParallaxBitangent)), 0.000001));
            locationParallaxTangent *= locationParallaxInvMax;
            locationParallaxBitangent *= locationParallaxInvMax;
            vec3 locationParallaxViewTangent = vec3(dot(locationParallaxView, locationParallaxTangent),
                dot(locationParallaxView, locationParallaxBitangent),
                max(abs(dot(locationParallaxView, locationParallaxNormal)), 0.2));
            float locationParallaxHeight = locationSampleHeight(vMapUv) - 0.5;
            float locationParallaxDistanceFade = 1.0 - smoothstep(locationParallaxFade.x,
                locationParallaxFade.y, length(vViewPosition));
            vec2 locationParallaxOffset = locationParallaxViewTangent.xy / locationParallaxViewTangent.z
                * locationParallaxHeight * locationParallaxScale * locationParallaxDistanceFade;
            locationParallaxOffset = clamp(locationParallaxOffset, vec2(-0.025), vec2(0.025));
            vec2 locationParallaxUv = vMapUv + locationParallaxOffset;
            float locationReliefHeight = locationSampleHeight(locationParallaxUv);
            float locationCavityShade = 1.0;
            if (locationParallaxDistanceFade > 0.001) {
                // Короткое затенение краями трещин заметно и в рассеянном свете.
                vec2 cavityStep = vec2(0.006, 0.005);
                float rimHeight = max(locationSampleHeight(locationParallaxUv + cavityStep),
                    locationSampleHeight(locationParallaxUv - cavityStep));
                float cavity = smoothstep(0.045, 0.24, rimHeight - locationReliefHeight);
                locationCavityShade = 1.0 - 0.52 * cavity * locationParallaxDistanceFade;
            }
            #if NUM_DIR_LIGHTS == 1
                DirectionalLight locationReliefSun[1];
                locationReliefSun[0] = directionalLights[0];
                locationReliefSun[0].color *= locationHeightSelfShadow(locationParallaxUv,
                    locationReliefHeight, directionalLights[0].direction,
                    locationParallaxTangent, locationParallaxBitangent, locationParallaxNormal,
                    locationParallaxDistanceFade);
                // Подменяем только солнце внутри штатного расчёта света. Фонари и лампы остаются отдельными.
                #define directionalLights locationReliefSun
            #endif
        `;
        const mapToken = '#include <map_fragment>';
        if (shader.fragmentShader.includes(mapToken)) {
            shader.fragmentShader = shader.fragmentShader.replace(mapToken,
                `${offsetCode}\n${THREE.ShaderChunk.map_fragment.replaceAll('vMapUv', 'locationParallaxUv')}`);
            replaceShaderChunk(shader, 'normal_fragment_maps', 'vNormalMapUv');
            replaceShaderChunk(shader, 'roughnessmap_fragment', 'vRoughnessMapUv');
            replaceShaderChunk(shader, 'metalnessmap_fragment', 'vMetalnessMapUv');
            replaceShaderChunk(shader, 'aomap_fragment', 'vAoMapUv');
            shader.fragmentShader = shader.fragmentShader.replace('#include <lights_fragment_end>',
                `#include <lights_fragment_end>
                #if NUM_DIR_LIGHTS == 1
                    #undef directionalLights
                #endif
                reflectedLight.indirectDiffuse *= locationCavityShade;`);
        }
    };
    material.customProgramCacheKey = () => `${previousKey?.() || ''}:location-pbr-height-parallax-v2`;
    material.userData.locationParallaxHookInstalled = true;
    material.needsUpdate = true;
}

export function applyLocationPbrMaterial(material, maps, renderer) {
    if (!maps?.color || !maps.normal || !maps.arm) return false;
    material.userData.pbrActive = true;
    material.color.set(0xffffff);
    Promise.all([loadColorTexture(maps, renderer), loadTexture(maps.normal), loadTexture(maps.arm)])
        .then(([color, normal, arm]) => {
            if (!material.userData.pbrActive) return;
            material.map = color;
            material.normalMap = normal;
            material.aoMap = arm;
            material.roughnessMap = arm;
            material.metalnessMap = arm;
            material.metalness = 1;
            material.roughness = 1;
            material.aoMapIntensity = 0.7;
            material.userData.pbrMapsReady = true;
            if (material.userData.locationHeightPending && material.userData.pbrActive) {
                addLocationHeightParallax(material, material.userData.locationHeightPending, maps);
            }
            material.needsUpdate = true;
        })
        .catch(error => console.warn('PBR maps unavailable; keeping procedural surface', error));
    if (maps.height) {
        loadTexture(maps.height).then(height => {
            if (!material.userData.pbrActive) return;
            material.userData.locationHeightPending = height;
            if (material.userData.pbrMapsReady) addLocationHeightParallax(material, height, maps);
        }).catch(error => console.warn('PBR height map unavailable; keeping color and normal maps', error));
    }
    return true;
}

export function releaseLocationPbrMaterial(material) {
    if (material?.userData) material.userData.pbrActive = false;
}

// Called after all local materials have been released. Keep the downloaded
// images cached, but remove renderer dispose callbacks and GPU allocations.
export function releaseLocationPbrTextures(renderer) {
    for (const pending of textureCache.values()) pending.then(texture => texture.dispose()).catch(() => {});
    const compressed = compressedTextureCache.get(renderer);
    if (compressed) {
        for (const pending of compressed.values()) pending.then(texture => texture.dispose()).catch(() => {});
        compressedTextureCache.delete(renderer);
    }
}

export function tileLocationPbrBoxUvs(geometry, width, height, depth, metersPerTile = 2) {
    const uv = geometry.attributes.uv;
    if (!uv || uv.count !== 24) return;
    const faces = [
        [depth, height], [depth, height],
        [width, depth], [width, depth],
        [width, height], [width, height],
    ];
    faces.forEach(([u, v], face) => {
        for (let corner = 0; corner < 4; corner++) {
            const index = face * 4 + corner;
            uv.setXY(index, uv.getX(index) * u / metersPerTile,
                uv.getY(index) * v / metersPerTile);
        }
    });
    uv.needsUpdate = true;
    geometry.setAttribute('uv2', uv.clone());
}

export function alignLocationPbrFloorUvs(geometry, mesh, metersPerTile = 2) {
    const position = geometry.attributes.position;
    const normal = geometry.attributes.normal;
    const uv = geometry.attributes.uv;
    if (!position || !normal || !uv) return;
    const point = new THREE.Vector3();
    for (let index = 0; index < position.count; index++) {
        if (Math.abs(normal.getY(index)) < 0.5) continue;
        point.fromBufferAttribute(position, index);
        mesh.localToWorld(point);
        uv.setXY(index, point.x / metersPerTile, point.z / metersPerTile);
    }
    uv.needsUpdate = true;
    geometry.setAttribute('uv2', uv.clone());
}
