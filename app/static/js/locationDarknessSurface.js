export function writeDarknessPixels(factors, pixels) {
    for (let index = 0; index < factors.length; index++) {
        const shade = Math.round(Math.max(0, Math.min(1, factors[index])) * 255);
        const offset = index * 4;
        pixels[offset] = shade;
        pixels[offset + 1] = shade;
        pixels[offset + 2] = shade;
        pixels[offset + 3] = 255;
    }
}

export function applySurfaceDarkness(material, texture, size, terrainDetail = null,
    grassDetail = null) {
    const previousCompile = material.onBeforeCompile;
    const previousKey = material.customProgramCacheKey?.bind(material);
    material.customProgramCacheKey = () => `${previousKey ? previousKey() : ''}:${terrainDetail ? 'location-surface-detail' : 'location-surface-plain'}${grassDetail ? ':photo-grass' : ''}`;
    material.onBeforeCompile = shader => {
        previousCompile?.(shader);
        shader.uniforms.locationDarknessMap = { value: texture };
        shader.uniforms.locationDarknessSize = { value: size };
        if (terrainDetail) shader.uniforms.locationTerrainDetail = { value: terrainDetail };
        if (grassDetail) {
            shader.uniforms.locationGrassEarth = { value: grassDetail.earth };
            shader.uniforms.locationGrassForest = { value: grassDetail.forest };
            shader.uniforms.locationGrassBlend = { value: grassDetail.blend };
            shader.uniforms.locationGrassReady = grassDetail.ready;
        }
        const grassVertexVaryings = grassDetail
            ? 'varying vec4 vLocationGrassSide;' : '';
        const grassVertexPosition = grassDetail ? `
                vLocationGrassSide = vec4(normalize(mat3(modelMatrix) * normal),
                    (modelMatrix * locationDarkWorld).y);
            ` : '';
        shader.vertexShader = shader.vertexShader
            .replace('#include <common>', `#include <common>
                varying vec2 vLocationDarkXZ;
                ${grassVertexVaryings}`)
            .replace('#include <project_vertex>', `
                vec4 locationDarkWorld = vec4(transformed, 1.0);
                #ifdef USE_INSTANCING
                    locationDarkWorld = instanceMatrix * locationDarkWorld;
                #endif
                vLocationDarkXZ = (modelMatrix * locationDarkWorld).xz;
                ${grassVertexPosition}
                #include <project_vertex>
            `);
        const detailUniform = terrainDetail ? 'uniform sampler2D locationTerrainDetail;' : '';
        const grassUniforms = grassDetail ? `
                uniform sampler2D locationGrassEarth;
                uniform sampler2D locationGrassForest;
                uniform sampler2D locationGrassBlend;
                uniform float locationGrassReady;
                varying vec4 vLocationGrassSide;
            ` : '';
        const detailColor = terrainDetail ? `
                float terrainShade = texture2D(locationTerrainDetail, vLocationDarkXZ * 0.14).r;
                float grassTintMask = smoothstep(0.045, 0.14, diffuseColor.g - diffuseColor.r);
                float broadGrassTone = texture2D(locationTerrainDetail,
                    vLocationDarkXZ * 0.055 + vec2(0.23, 0.41)).r;
                float patch = smoothstep(0.91, 0.98,
                    broadGrassTone + 0.06 * (terrainShade - 0.94));
                vec3 grassTint = mix(vec3(1.16, 0.92, 0.78),
                    vec3(0.71, 1.13, 0.73), patch);
                diffuseColor.rgb *= mix(vec3(1.0), grassTint, grassTintMask * 0.88);
                float chroma = max(max(diffuseColor.r, diffuseColor.g), diffuseColor.b)
                    - min(min(diffuseColor.r, diffuseColor.g), diffuseColor.b);
                float rockMask = 1.0 - smoothstep(0.018, 0.105, chroma);
                float concrete = smoothstep(0.895, 0.98,
                    terrainShade + 0.04 * (broadGrassTone - 0.94));
                diffuseColor.rgb *= mix(vec3(1.0), vec3(0.72 + 0.38 * concrete), rockMask * 0.82);
                float luminance = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));
                float swampMask = (1.0 - smoothstep(0.22, 0.36, luminance))
                    * (1.0 - grassTintMask);
                float wet = smoothstep(0.94, 0.99, broadGrassTone);
                vec3 swampTint = mix(vec3(0.91, 1.06, 0.79),
                    vec3(0.71, 0.97, 1.15), wet);
                diffuseColor.rgb *= mix(vec3(1.0), swampTint, swampMask * 0.63);
                diffuseColor.rgb *= 0.61 + 0.44 * terrainShade;
            ` : '';
        const grassBlend = grassDetail ? `
                vec2 grassWeights = texture2D(locationGrassBlend,
                    clamp(vLocationDarkXZ / locationDarknessSize, vec2(0.0), vec2(1.0))).rg;
                float grassCoverage = min(1.0, grassWeights.r + grassWeights.g);
            ` : '';
        const grassColor = grassDetail ? `
                if (grassCoverage > 0.0) {
                    vec3 grassPhoto = vec3(0.112, 0.087, 0.050);
                    if (locationGrassReady > 0.5) {
                        vec2 grassUv = vLocationDarkXZ * 0.125;
                        vec3 earthPhoto = texture2D(locationGrassEarth, grassUv).rgb;
                        vec3 forestPhoto = texture2D(locationGrassForest, grassUv).rgb;
                        vec3 slopeNormal = abs(normalize(vLocationGrassSide.xyz));
                        float slopeProjection = smoothstep(0.12, 0.55, 1.0 - slopeNormal.y);
                        if (slopeProjection > 0.001) {
                            vec2 sideXUv = vec2(vLocationDarkXZ.y, vLocationGrassSide.w) * 0.125;
                            vec2 sideZUv = vec2(vLocationDarkXZ.x, vLocationGrassSide.w) * 0.125;
                            float sideZWeight = slopeNormal.z
                                / max(slopeNormal.x + slopeNormal.z, 0.0001);
                            vec3 sideEarth = mix(
                                texture2D(locationGrassEarth, sideXUv).rgb,
                                texture2D(locationGrassEarth, sideZUv).rgb,
                                sideZWeight);
                            vec3 sideForest = mix(
                                texture2D(locationGrassForest, sideXUv).rgb,
                                texture2D(locationGrassForest, sideZUv).rgb,
                                sideZWeight);
                            earthPhoto = mix(earthPhoto, sideEarth, slopeProjection);
                            forestPhoto = mix(forestPhoto, sideForest, slopeProjection);
                        }
                        float litter = texture2D(locationGrassForest, grassUv * 0.34 + vec2(0.21, 0.63)).g;
                        vec3 plainPhoto = mix(earthPhoto, forestPhoto, 0.14 + 0.22 * smoothstep(0.24, 0.56, litter));
                        vec3 luma = vec3(0.2126, 0.7152, 0.0722);
                        plainPhoto = mix(plainPhoto, vec3(dot(plainPhoto, luma)), 0.58)
                            * vec3(1.02, 0.98, 0.91);
                        plainPhoto = mix(vec3(0.29, 0.269, 0.237), plainPhoto, 0.84);
                        vec3 woodlandPhoto = mix(forestPhoto, vec3(dot(forestPhoto, luma)), 0.42)
                            * vec3(0.93, 0.92, 0.86);
                        grassPhoto = (plainPhoto * grassWeights.r
                            + woodlandPhoto * grassWeights.g) / (grassWeights.r + grassWeights.g);
                    }
                    diffuseColor.rgb = mix(diffuseColor.rgb, grassPhoto, grassCoverage);
                }
            ` : '';
        shader.fragmentShader = shader.fragmentShader
            .replace('#include <common>', `#include <common>
                uniform sampler2D locationDarknessMap;
                uniform vec2 locationDarknessSize;
                varying vec2 vLocationDarkXZ;
                ${detailUniform}
                ${grassUniforms}
            `)
            .replace('#include <color_fragment>', `${grassBlend}#include <color_fragment>
                ${detailColor}
                ${grassColor}
                diffuseColor.rgb *= texture2D(locationDarknessMap,
                    clamp(vLocationDarkXZ / locationDarknessSize, vec2(0.0), vec2(1.0))).r;
            `);
    };
}
