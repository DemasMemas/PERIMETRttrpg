// The global terrain keeps its low-frequency color in the chunk map; its alpha is grass coverage.
export function createWorldGrassDetail(THREE) {
    const createTexture = () => {
        const texture = new THREE.Texture();
        texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
        texture.magFilter = THREE.LinearFilter;
        texture.minFilter = THREE.LinearMipmapLinearFilter;
        texture.generateMipmaps = true;
        texture.anisotropy = 4;
        texture.colorSpace = THREE.SRGBColorSpace;
        return texture;
    };
    const earth = createTexture();
    const forest = createTexture();
    const ready = { value: 0 };

    return {
        textures() { return { earth, forest, ready }; },
        setImages(images) {
            if (!images) { ready.value = 0; return; }
            if (earth.image !== images.plain || forest.image !== images.forest) {
                earth.image = images.plain;
                forest.image = images.forest;
                earth.needsUpdate = forest.needsUpdate = true;
            }
            ready.value = 1;
        },
        apply(material) {
            const previousCompile = material.onBeforeCompile;
            const previousKey = material.customProgramCacheKey?.bind(material);
            material.customProgramCacheKey = () => `${previousKey ? previousKey() : ''}:world-grass-detail-v1`;
            material.onBeforeCompile = shader => {
                previousCompile?.(shader);
                shader.uniforms.worldGrassEarth = { value: earth };
                shader.uniforms.worldGrassForest = { value: forest };
                shader.uniforms.worldGrassReady = ready;
                shader.vertexShader = shader.vertexShader
                    .replace('#include <common>', `#include <common>
                        varying vec3 vWorldGrassPosition;
                    `)
                    .replace('#include <project_vertex>', `
                        vWorldGrassPosition = (modelMatrix * vec4(transformed, 1.0)).xyz;
                        #include <project_vertex>
                    `);
                shader.fragmentShader = shader.fragmentShader
                    .replace('#include <common>', `#include <common>
                        uniform sampler2D worldGrassEarth;
                        uniform sampler2D worldGrassForest;
                        uniform float worldGrassReady;
                        varying vec3 vWorldGrassPosition;
                    `)
                    .replace('#include <map_fragment>', `
                        float worldGrassCoverage = 0.0;
                        #ifdef USE_MAP
                            vec4 worldGroundTexel = texture2D(map, vMapUv);
                            worldGrassCoverage = worldGroundTexel.a;
                            diffuseColor.rgb *= worldGroundTexel.rgb;
                        #endif
                    `)
                    .replace('#include <color_fragment>', `#include <color_fragment>
                        if (worldGrassReady > 0.5 && worldGrassCoverage > 0.005) {
                            float grassFade = 1.0 - smoothstep(55.0, 200.0,
                                distance(vWorldGrassPosition, cameraPosition));
                            if (grassFade > 0.005) {
                                vec2 worldGrassUv = vWorldGrassPosition.xz * 0.125;
                                vec3 earthPhoto = texture2D(worldGrassEarth, worldGrassUv).rgb;
                                vec3 forestPhoto = texture2D(worldGrassForest, worldGrassUv).rgb;
                                vec3 grassPhoto = mix(earthPhoto, forestPhoto, 0.82)
                                    * vec3(0.86, 1.06, 0.84);
                                diffuseColor.rgb = mix(diffuseColor.rgb, grassPhoto,
                                    worldGrassCoverage * grassFade * 0.85);
                            }
                        }
                    `);
            };
        },
    };
}
