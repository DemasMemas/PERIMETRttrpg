import { TERRAIN_MATERIALS, TERRAIN_ATLAS_CELL, TERRAIN_REPEAT_TILES,
    TERRAIN_ATLAS_COLUMNS, TERRAIN_ATLAS_ROWS, TERRAIN_ATLAS_GUTTER, adjustTerrainAlbedo,
    terrainMaterialIndex } from './terrainMaterialCatalog.js';

// One atlas set per Three.js module instance, shared by world and local scenes.
// Shared power-of-two atlases; adding surfaces never creates per-chunk PBR maps.
const resources = new WeakMap();
const WINTER_TEXTURE_SIZE = 1024;

function mixedWinterTextureData(atlas) {
    atlas.mixedWinterData ??= atlas.promise.then(ready => {
        if (!ready) return null;
        const size = WINTER_TEXTURE_SIZE;
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = size;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        context.imageSmoothingEnabled = false;
        const layerBytes = size * size * 4;
        const data = new Uint8Array(layerBytes * 4);
        for (const [layer, texture] of [...atlas.snowTextures, ...atlas.iceTextures].entries()) {
            context.clearRect(0, 0, size, size);
            context.drawImage(texture.image, 0, 0, size, size);
            const rgba = context.getImageData(0, 0, size, size).data;
            // Текстурный массив не переворачивает строки при загрузке, в отличие от CanvasTexture.
            const rowBytes = size * 4;
            for (let y = 0; y < size; y++) {
                const from = y * rowBytes;
                data.set(rgba.subarray(from, from + rowBytes), layer * layerBytes + (size - 1 - y) * rowBytes);
            }
        }
        return data;
    });
    return atlas.mixedWinterData;
}

function sharedAtlases(THREE) {
    if (resources.has(THREE)) return resources.get(THREE);
    const size = TERRAIN_ATLAS_CELL;
    const canvases = ['color', 'normal', 'arm'].map(() => {
        const canvas = document.createElement('canvas');
        canvas.width = size * TERRAIN_ATLAS_COLUMNS;
        canvas.height = size * TERRAIN_ATLAS_ROWS;
        return canvas;
    });
    const textures = canvases.map(canvas => {
        const texture = new THREE.CanvasTexture(canvas);
        texture.flipY = false;
        texture.minFilter = THREE.LinearMipmapLinearFilter;
        texture.magFilter = THREE.LinearFilter;
        texture.anisotropy = 4;
        return texture;
    });
    // One full-resolution snow set, shared by chunks, previews and scene layers.
    // A whole repeating texture has isolated mip levels; the atlas cannot provide
    // that at snow's 3 m scale without bleeding neighbouring material cells.
    const snowTextures = [0, 1].map(() => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 1024;
        const texture = new THREE.CanvasTexture(canvas);
        texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
        texture.minFilter = THREE.LinearMipmapLinearFilter;
        texture.magFilter = THREE.LinearFilter;
        texture.anisotropy = 4;
        return texture;
    });
    // У мелкого льда собственные mip-уровни: при масштабе 100 м атлас
    // захватывал соседние материалы и рисовал сетку вместо усреднения деталей.
    const iceTextures = [0, 1].map(() => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 512;
        const texture = new THREE.CanvasTexture(canvas);
        texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
        texture.minFilter = THREE.LinearMipmapLinearFilter;
        texture.magFilter = THREE.LinearFilter;
        texture.anisotropy = 4;
        return texture;
    });
    const result = { textures, snowTextures, iceTextures, ready: { value: 0 }, error: null, promise: null };
    const load = url => new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error(`Terrain map unavailable: ${url}`));
        image.src = url;
    });
    result.promise = Promise.all(TERRAIN_MATERIALS.map(async (entry, index) => {
        if (entry.separateColor) return;
        if (entry.roughnessInColorAlpha) {
            const images = await Promise.all(['color', 'normal'].map(channel => load(entry[channel])));
            images.forEach((image, channel) => {
                const context = snowTextures[channel].image.getContext('2d', { willReadFrequently: channel === 0 });
                context.drawImage(image, 0, 0, 1024, 1024);
                if (channel === 0) {
                    const pixels = context.getImageData(0, 0, 1024, 1024);
                    adjustTerrainAlbedo(pixels.data, entry); // Alpha remains source roughness.
                    context.putImageData(pixels, 0, 0);
                }
            });
            return;
        }
        const images = await Promise.all(['color', 'normal', 'arm'].map(channel => load(entry[channel])));
        if (entry.terrain === 'ice') {
            const context = iceTextures[0].image.getContext('2d', { willReadFrequently: true });
            context.drawImage(images[2], 0, 0, 512, 512);
            const arm = context.getImageData(0, 0, 512, 512).data;
            context.drawImage(images[0], 0, 0, 512, 512);
            const pixels = context.getImageData(0, 0, 512, 512);
            adjustTerrainAlbedo(pixels.data, entry);
            // Ice001: AO=1, металл=0; шероховатость сохраняется в альфа-канале цвета.
            for (let i = 0; i < pixels.data.length; i += 4) pixels.data[i + 3] = arm[i + 1];
            context.putImageData(pixels, 0, 0);
            iceTextures[1].image.getContext('2d').drawImage(images[1], 0, 0, 512, 512);
            return;
        }
        const slot = entry.atlasSlot ?? index;
        images.forEach((image, channel) => {
            const context = canvases[channel].getContext('2d', { willReadFrequently: channel === 0 });
            const x = slot % TERRAIN_ATLAS_COLUMNS * size, y = Math.floor(slot / TERRAIN_ATLAS_COLUMNS) * size;
            // Match TextureLoader's flipY for each cell, without reversing atlas rows.
            // Wrapped gutters keep bilinear, mip and anisotropic taps inside this material.
            const core = size - 2 * TERRAIN_ATLAS_GUTTER;
            context.save();
            context.beginPath();
            context.rect(x, y, size, size);
            context.clip();
            context.translate(x + TERRAIN_ATLAS_GUTTER, y + size - TERRAIN_ATLAS_GUTTER);
            context.scale(1, -1);
            for (let dz = -1; dz <= 1; dz++) for (let dx = -1; dx <= 1; dx++) {
                context.drawImage(image, dx * core, dz * core, core, core);
            }
            context.restore();
            if (entry.albedo && channel === 0) {
                const pixels = context.getImageData(x, y, size, size);
                adjustTerrainAlbedo(pixels.data, entry);
                context.putImageData(pixels, x, y);
            }
        });
    })).then(() => {
        textures.forEach(texture => { texture.needsUpdate = true; });
        snowTextures.forEach(texture => { texture.needsUpdate = true; });
        iceTextures.forEach(texture => { texture.needsUpdate = true; });
        result.ready.value = 1;
        return true;
    }).catch(error => {
        result.error = String(error);
        console.warn('Terrain PBR unavailable; keeping existing terrain surfaces', error);
        return false;
    });
    resources.set(THREE, result);
    return result;
}

// The tiny nearest-filtered lookup stores material IDs, never color or PBR pixels.
// A world layer spans the map, so adjacent chunks use precisely the same lookup.
export function createTerrainMaterialLayer(THREE, { world = false, onSeasonalSurface, winterTextureArray = false } = {}) {
    const atlas = sharedAtlases(THREE);
    // Keep CPU images shared, but give a disposable local renderer its own
    // texture wrappers. Otherwise cached textures retain every renderer's
    // onTextureDispose listener, its WebGL context and its detached canvas.
    const textures = world ? atlas.textures : atlas.textures.map(texture => texture.clone());
    const snowTextures = world ? atlas.snowTextures : atlas.snowTextures.map(texture => texture.clone());
    const iceTextures = world ? atlas.iceTextures : atlas.iceTextures.map(texture => texture.clone());
    let disposed = false;
    if (!world) atlas.promise.then(() => {
        if (!disposed) [...textures, ...snowTextures, ...iceTextures].forEach(texture => { texture.needsUpdate = true; });
    });
    const uniforms = { terrainAtlasColor: { value: textures[0] },
        terrainWetColor: { value: null },
        terrainSnowColorRoughness: { value: snowTextures[0] },
        terrainSnowNormal: { value: snowTextures[1] },
        terrainIceColorRoughness: { value: iceTextures[0] },
        terrainIceNormal: { value: iceTextures[1] },
        terrainWinterLayers: { value: null },
        terrainAtlasNormal: { value: textures[1] }, terrainAtlasArm: { value: textures[2] },
        terrainAtlasReady: atlas.ready, terrainKinds: { value: null },
        terrainMaterialDarkness: { value: textures[0] }, terrainMaterialHasDarkness: { value: 0 },
        terrainKindsSize: { value: new THREE.Vector2(1, 1) } };
    let texture = null;
    const materials=new Set(),seasonal={snow:world,ice:world};
    const leafId = terrainMaterialIndex('brown_mud_leaves_01');
    const swampId = terrainMaterialIndex('swamp');
    let wetColorLoading = false;
    function loadWetColor() {
        wetColorLoading = true;
        const entries = [TERRAIN_MATERIALS[swampId - 1], TERRAIN_MATERIALS[leafId - 1]];
        Promise.all(entries.map(entry => new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => resolve(image);
            image.onerror = reject;
            image.src = entry.color;
        }))).then(images => {
            if (disposed) return;
            const size = 1024, gutter = 16, core = size - gutter * 2;
            const canvas = document.createElement('canvas');
            canvas.width = size * 2;
            canvas.height = size;
            const context = canvas.getContext('2d', { willReadFrequently: true });
            // Два исходника в полном разрешении на одном канале текстур. Поля
            // повторяют противоположный край для плавных стыков и фильтрации.
            images.forEach((image, index) => {
                context.save();
                context.beginPath();
                context.rect(index * size, 0, size, size);
                context.clip();
                for (let z = -1; z <= 1; z++) for (let x = -1; x <= 1; x++) {
                    context.drawImage(image, index * size + gutter + x * core, gutter + z * core, core, core);
                }
                context.restore();
                const entry = entries[index];
                if (entry.albedo) {
                    const pixels = context.getImageData(index * size, 0, size, size);
                    adjustTerrainAlbedo(pixels.data, entry);
                    context.putImageData(pixels, index * size, 0);
                }
            });
            const map = new THREE.CanvasTexture(canvas);
            map.anisotropy = 4;
            uniforms.terrainWetColor.value = map;
            materials.forEach(material => { material.needsUpdate = true; });
        }).catch(() => { wetColorLoading = false; });
    }
    const mixedWinter = () => winterTextureArray && !world && seasonal.snow && seasonal.ice;
    function prepareMixedWinterTextures() {
        mixedWinterTextureData(atlas).then(data => {
            if (disposed || !data) return;
            const size = WINTER_TEXTURE_SIZE;
            const ArrayTexture = THREE.DataArrayTexture ?? THREE.DataTexture2DArray;
            const layers = new ArrayTexture(data, size, size, 4);
            layers.wrapS = layers.wrapT = THREE.RepeatWrapping;
            layers.minFilter = THREE.LinearMipmapLinearFilter;
            layers.magFilter = THREE.LinearFilter;
            layers.generateMipmaps = true;
            layers.anisotropy = 4;
            layers.needsUpdate = true;
            uniforms.terrainWinterLayers.value = layers;
        });
    }
    const layer = {
        ready: atlas.promise,
        setShoreGrass(detail) {
            uniforms.terrainShoreEarth={value:detail.earth};
            uniforms.terrainShoreForest={value:detail.forest};
            uniforms.terrainShoreGrassReady=detail.ready;
        },
        setDarkness(texture) {
            uniforms.terrainMaterialDarkness.value = texture || textures[0];
            uniforms.terrainMaterialHasDarkness.value = texture ? 1 : 0;
        },
        resize(width, height) {
            if (texture?.image.width === width && texture?.image.height === height) return;
            texture?.dispose();
            texture = new THREE.DataTexture(new Uint8Array(width * height * 4), width, height,
                THREE.RGBAFormat, THREE.UnsignedByteType);
            texture.magFilter = texture.minFilter = THREE.NearestFilter;
            texture.generateMipmaps = false;
            texture.needsUpdate = true;
            uniforms.terrainKinds.value = texture;
            uniforms.terrainKindsSize.value.set(width, height);
        },
        update(x, z, tile) {
            if (!texture || x < 0 || z < 0 || x >= texture.image.width || z >= texture.image.height) return;
            const offset = (z * texture.image.width + x) * 4;
            const top = terrainMaterialIndex(tile), side = terrainMaterialIndex(tile, true);
            const water = tile?.terrain === 'water' ? 255 : 0;
            if (texture.image.data[offset] === top && texture.image.data[offset + 1] === side
                && texture.image.data[offset + 2] === water) return;
            texture.image.data[offset] = top;
            texture.image.data[offset + 1] = side;
            texture.image.data[offset + 2] = water;
            texture.needsUpdate = true;
            if (!world && !wetColorLoading && (top === leafId || top === swampId)) loadWetColor();
            // Не занимаем четыре канала текстур снега/льда в летней подлокации:
            // они нужны картам теней фонарей. Редактор включает их при добавлении.
            for(const [kind,id] of [['snow',6],['ice',12]])if(!seasonal[kind]&&(top===id||side===id)){
                seasonal[kind]=true;
                if (mixedWinter()) prepareMixedWinterTextures();
                materials.forEach(material=>{material.needsUpdate=true;});
                onSeasonalSurface?.();
            }
        },
        fill(tiles, originX = 0, originZ = 0) {
            tiles.forEach((row, z) => row.forEach((tile, x) => layer.update(originX + x, originZ + z, tile)));
        },
        clear(originX, originZ, width, height) {
            for (let z = 0; z < height; z++) for (let x = 0; x < width; x++) {
                layer.update(originX + x, originZ + z, null);
            }
        },
        dispose() {
            if (disposed) return;
            disposed = true;
            texture?.dispose(); texture = null; uniforms.terrainKinds.value = null;
            if (!world) [...textures, ...snowTextures, ...iceTextures].forEach(texture => texture.dispose());
            uniforms.terrainWinterLayers.value?.dispose();
            uniforms.terrainWetColor.value?.dispose();
            materials.clear();
        },
        diagnostics() { return { ready: atlas.ready.value === 1, error: atlas.error,
            atlasBytes: TERRAIN_ATLAS_CELL ** 2 * TERRAIN_ATLAS_COLUMNS * TERRAIN_ATLAS_ROWS * 4 * 4,
            snowBytes: Math.ceil(2 * 1024 ** 2 * 4 * 4 / 3),
            iceBytes: Math.ceil(2 * 512 ** 2 * 4 * 4 / 3),
            materials: TERRAIN_MATERIALS.length, lookupBytes: texture?.image.data.byteLength || 0 }; },
        apply(material, { sides = false, smooth = false, shore = false, waterSides = world,
            iceGlaze = false, grassBlend = false } = {}) {
            shore = world && shore;
            materials.add(material);
            material.extensions = { ...material.extensions, derivatives: true, shaderTextureLOD: true };
            const previousCompile = material.onBeforeCompile;
            const previousKey = material.customProgramCacheKey?.bind(material);
            material.customProgramCacheKey = () => `${previousKey?.() || ''}:terrain-pbr-v21:${world}:${sides}:${smooth}:${shore}:${waterSides}:${iceGlaze}:${seasonal.snow}:${seasonal.ice}:${mixedWinter()}:${Boolean(uniforms.terrainWetColor.value)}:${grassBlend}`;
            material.onBeforeCompile = shader => {
                previousCompile?.(shader);
                Object.assign(shader.uniforms, uniforms);
                if(shore){
                    shader.uniforms.terrainShoreEarth ||= {value:textures[0]};
                    shader.uniforms.terrainShoreForest ||= {value:textures[0]};
                    shader.uniforms.terrainShoreGrassReady ||= {value:0};
                }
                const topAttribute = smooth && !shader.vertexShader.includes('attribute float terrainTop;')
                    ? 'attribute float terrainTop;' : '';
                shader.vertexShader = shader.vertexShader
                    .replace('#include <common>', `#include <common>
                        varying vec3 vTerrainMaterialPosition;
                        varying vec3 vTerrainMaterialNormal;
                        ${smooth ? `attribute vec2 terrainTileCoord; ${topAttribute} varying vec3 vTerrainOwner;` : ''}`)
                    .replace('#include <project_vertex>', `
                        vec4 terrainPosition = vec4(transformed, 1.0);
                        vec3 terrainNormal = normal;
                        #ifdef USE_INSTANCING
                            terrainPosition = instanceMatrix * terrainPosition;
                            terrainNormal = mat3(instanceMatrix) * terrainNormal;
                        #endif
                        vTerrainMaterialPosition = (modelMatrix * terrainPosition).xyz;
                        vTerrainMaterialNormal = normalize(mat3(modelMatrix) * terrainNormal);
                        ${smooth ? 'vTerrainOwner = vec3(terrainTileCoord, terrainTop);' : ''}
                        #include <project_vertex>`);
                shader.fragmentShader = shader.fragmentShader
                    .replace('#include <common>', `#include <common>\n${fragmentHelpers(sides, smooth, world, iceGlaze, mixedWinter(), uniforms.terrainWetColor.value ? [swampId, leafId] : null)}
                        ${shore?'uniform sampler2D terrainShoreEarth; uniform sampler2D terrainShoreForest; uniform float terrainShoreGrassReady;':''}`)
                    // Inject after map/color, before scene darkness and static light callbacks.
                    .replace('#include <color_fragment>', `#include <color_fragment>\n${fragmentSample(sides, smooth, waterSides, shore, world, iceGlaze, grassBlend)}`)
                    .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
                        roughnessFactor = mix(roughnessFactor, max(0.08, terrainArm.g), terrainCoverage);`)
                    .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>
                        metalnessFactor = mix(metalnessFactor, terrainArm.b, terrainCoverage);`)
                    .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
                        if (terrainCoverage > 0.001) {
                            normal = normalize(mix(normal, terrainPerturbNormal(-vViewPosition, normal,
                                terrainUv, normalize(terrainNormalSample), faceDirection), terrainCoverage));
                        }`)
                    .replace('#include <aomap_fragment>', `#include <aomap_fragment>
                        reflectedLight.indirectDiffuse *= mix(1.0, mix(1.0, terrainArm.r, 0.7), terrainCoverage);`);
                if(!seasonal.snow)shader.fragmentShader=shader.fragmentShader
                    .replace('uniform sampler2D terrainSnowColorRoughness;','#define terrainSnowColorRoughness terrainAtlasColor\n')
                    .replace('uniform sampler2D terrainSnowNormal;','#define terrainSnowNormal terrainAtlasNormal\n');
                if(!seasonal.ice)shader.fragmentShader=shader.fragmentShader
                    .replace('uniform sampler2D terrainIceColorRoughness;','#define terrainIceColorRoughness terrainAtlasColor\n')
                    .replace('uniform sampler2D terrainIceNormal;','#define terrainIceNormal terrainAtlasNormal\n');
            };
        },
    };
    layer.resize(1, 1);
    return layer;
}

function fragmentHelpers(sides, smooth, world, iceGlaze, mixedWinter, wetIds) {
    const winterLayer = { SnowColorRoughness: 0, SnowNormal: 1, IceColorRoughness: 2, IceNormal: 3 };
    const winterSample = (kind, channel) => mixedWinter
        ? `textureGrad(terrainWinterLayers, vec3(uv, ${winterLayer[kind + channel].toFixed(1)}), dx, dy)`
        : `terrainSnowSample(terrain${kind}${channel}, uv, dx, dy)`;
    return `
        varying vec3 vTerrainMaterialPosition;
        varying vec3 vTerrainMaterialNormal;
        ${iceGlaze ? 'float terrainIceCrack; float terrainIceOpacity;' : ''}
        ${smooth ? 'varying vec3 vTerrainOwner;' : ''}
        uniform sampler2D terrainAtlasColor;
        uniform sampler2D terrainAtlasNormal;
        uniform sampler2D terrainAtlasArm;
        ${wetIds ? 'uniform sampler2D terrainWetColor;' : ''}
        ${mixedWinter ? 'uniform highp sampler2DArray terrainWinterLayers;' : `uniform sampler2D terrainSnowColorRoughness;
        uniform sampler2D terrainSnowNormal;
        uniform sampler2D terrainIceColorRoughness;
        uniform sampler2D terrainIceNormal;`}
        uniform sampler2D terrainKinds;
        uniform vec2 terrainKindsSize;
        uniform float terrainAtlasReady;
        ${sides ? 'uniform sampler2D terrainMaterialDarkness; uniform float terrainMaterialHasDarkness;' : ''}
        vec3 terrainId(vec2 tile) {
            return floor(texture2D(terrainKinds, (clamp(tile, vec2(0.0), terrainKindsSize - 1.0)
                + 0.5) / terrainKindsSize).rgb * 255.0 + 0.5);
        }
        vec2 terrainAtlasUv(float id, vec2 uv) {
            float slot = max(0.0, id - 1.0);
            ${TERRAIN_MATERIALS.map((entry, index) => entry.atlasSlot === undefined ? '' :
                `if (id == ${(index + 1).toFixed(1)}) slot = ${entry.atlasSlot.toFixed(1)};`).join('\n')}
            vec2 cellUv = (fract(uv) * ${(TERRAIN_ATLAS_CELL - 2 * TERRAIN_ATLAS_GUTTER).toFixed(1)}
                + ${TERRAIN_ATLAS_GUTTER.toFixed(1)}) / ${TERRAIN_ATLAS_CELL.toFixed(1)};
            return (vec2(mod(slot, ${TERRAIN_ATLAS_COLUMNS.toFixed(1)}), floor(slot / ${TERRAIN_ATLAS_COLUMNS.toFixed(1)}))
                + cellUv) / vec2(${TERRAIN_ATLAS_COLUMNS.toFixed(1)}, ${TERRAIN_ATLAS_ROWS.toFixed(1)});
        }
        vec3 terrainLinear(vec3 color) {
            return mix(color / 12.92, pow((color + 0.055) / 1.055, vec3(2.4)), step(vec3(0.04045), color));
        }
        vec4 terrainAtlasSample(sampler2D atlas, float id, vec2 uv, vec2 dx, vec2 dy) {
            // Derivatives must use continuous UVs, not fract(atlasUv): otherwise
            // every repeat boundary selects the coarsest mip of the entire atlas.
            #if defined(GL_EXT_shader_texture_lod) || __VERSION__ >= 300
                return texture2DGradEXT(atlas, terrainAtlasUv(id, uv),
                    dx * ${((TERRAIN_ATLAS_CELL - 2 * TERRAIN_ATLAS_GUTTER) / TERRAIN_ATLAS_CELL).toFixed(5)}
                        / vec2(${TERRAIN_ATLAS_COLUMNS.toFixed(1)}, ${TERRAIN_ATLAS_ROWS.toFixed(1)}),
                    dy * ${((TERRAIN_ATLAS_CELL - 2 * TERRAIN_ATLAS_GUTTER) / TERRAIN_ATLAS_CELL).toFixed(5)}
                        / vec2(${TERRAIN_ATLAS_COLUMNS.toFixed(1)}, ${TERRAIN_ATLAS_ROWS.toFixed(1)}));
            #else
                return texture2D(atlas, terrainAtlasUv(id, uv));
            #endif
        }
        vec4 terrainSnowSample(sampler2D map, vec2 uv, vec2 dx, vec2 dy) {
            #if defined(GL_EXT_shader_texture_lod) || __VERSION__ >= 300
                return texture2DGradEXT(map, uv, dx, dy);
            #else
                return texture2D(map, uv);
            #endif
        }
        vec3 terrainPerturbNormal(vec3 eye, vec3 n, vec2 uv, vec3 mapNormal, float faceSign) {
            vec3 q0 = dFdx(eye), q1 = dFdy(eye);
            vec2 st0 = dFdx(uv), st1 = dFdy(uv);
            vec3 t = cross(q1, n) * st0.x + cross(n, q0) * st1.x;
            vec3 b = cross(q1, n) * st0.y + cross(n, q0) * st1.y;
            float scale = faceSign * inversesqrt(max(max(dot(t, t), dot(b, b)), 0.00000001));
            return normalize(t * mapNormal.x * scale + b * mapNormal.y * scale + n * mapNormal.z);
        }
        float terrainNoise(vec2 p) {
            vec2 cell = floor(p), t = fract(p);
            t = t * t * (3.0 - 2.0 * t);
            vec4 h = fract(sin(vec4(dot(cell, vec2(127.1,311.7)),
                dot(cell + vec2(1.0,0.0), vec2(127.1,311.7)),
                dot(cell + vec2(0.0,1.0), vec2(127.1,311.7)),
                dot(cell + 1.0, vec2(127.1,311.7)))) * 43758.5453);
            return mix(mix(h.x,h.y,t.x), mix(h.z,h.w,t.x), t.y);
        }
        vec2 terrainWarp(vec2 uv, float strength) {
            return uv + strength * (vec2(terrainNoise(uv * 1.73), terrainNoise(uv * 1.31 + 13.7)) - 0.5);
        }
        vec4 terrainParameters(float id) {
            ${TERRAIN_MATERIALS.map((entry, index) => `if (id == ${(index + 1).toFixed(1)}) return vec4(${[
                (world ? entry.world?.repeat : null) ?? entry.repeat ?? TERRAIN_REPEAT_TILES,
                (world ? entry.world?.normalStrength : null) ?? entry.normalStrength ?? 1,
                entry.variation ?? 0, (world ? entry.world?.warp : null) ?? entry.warp ?? 0].map(value => value.toFixed(3)).join(',')});`).join('\n')}
            return vec4(${TERRAIN_REPEAT_TILES.toFixed(1)},1.0,0.0,0.0);
        }
        ${world ? `// Общая палитра снега и припорошённого льда глобальной карты.
        vec3 terrainWorldSnowColor(vec3 detail, vec2 uv) {
            vec3 macroSnow;
            #if defined(GL_EXT_shader_texture_lod) || __VERSION__ >= 300
                macroSnow = texture2DLodEXT(terrainSnowColorRoughness, uv * 0.0375, 6.0).rgb;
            #else
                macroSnow = texture2D(terrainSnowColorRoughness, uv * 0.0375, 6.0).rgb;
            #endif
            return mix(detail, terrainLinear(macroSnow), 0.35) * 0.78;
        }` : ''}
        void terrainReadMaterial(float id, vec2 uv, vec2 dx, vec2 dy,
            out vec3 color, out vec3 mapNormal, out vec3 arm) {
            if (id == 12.0) {
                vec4 ice = ${winterSample('Ice', 'ColorRoughness')};
                color = terrainLinear(ice.rgb);
                ${world ? `// Тон берётся из снега при том же масштабе и освещении;
                // исходный лёд задаёт только слабый рисунок, без голубой заливки.
                float snowScale = terrainParameters(12.0).x / terrainParameters(6.0).x;
                vec2 snowUv = uv * snowScale;
                // Крупный цвет снега сохраняет общий тон, без снежной зернистости.
                vec3 snowColor;
                #if defined(GL_EXT_shader_texture_lod) || __VERSION__ >= 300
                    snowColor = texture2DLodEXT(terrainSnowColorRoughness, snowUv * 0.0375, 6.0).rgb;
                #else
                    snowColor = texture2D(terrainSnowColorRoughness, snowUv * 0.0375, 6.0).rgb;
                #endif
                snowColor = terrainLinear(snowColor);
                color = terrainWorldSnowColor(snowColor, snowUv);
                float iceTone = dot(ice.rgb, vec3(0.2126, 0.7152, 0.0722));
                ${iceGlaze ? `// Высокочастотные светлые линии отделены от широких белых пятен.
                vec3 broadIce;
                #if defined(GL_EXT_shader_texture_lod) || __VERSION__ >= 300
                    broadIce = texture2DLodEXT(terrainIceColorRoughness, uv, 4.0).rgb;
                #else
                    broadIce = texture2D(terrainIceColorRoughness, uv, 4.0).rgb;
                #endif
                float crack = smoothstep(0.005, 0.045, iceTone - dot(broadIce, vec3(0.2126, 0.7152, 0.0722)));
                terrainIceCrack = crack;
                color *= mix(0.96, 1.06, crack);
                ice.a = mix(0.30, 0.48, crack);` : `color *= 0.94 + (iceTone - 0.72) * 0.20;
                color *= vec3(0.99, 1.0, 1.005);
                ice.a = mix(0.48, ice.a, 0.10);`}` : ''}
                mapNormal = ${winterSample('Ice', 'Normal')}.xyz * 2.0 - 1.0;
                arm = vec3(1.0, ice.a, 0.0);
                return;
            }
            if (id == 6.0) {
                vec4 snow = ${winterSample('Snow', 'ColorRoughness')};
                color = terrainLinear(snow.rgb);
                ${world ? 'color = terrainWorldSnowColor(color, uv);' : ''}
                mapNormal = ${winterSample('Snow', 'Normal')}.xyz * 2.0 - 1.0;
                arm = vec3(1.0, clamp(snow.a, 0.88, 0.98), 0.0);
                return;
            }
            vec3 albedo = terrainAtlasSample(terrainAtlasColor, id, uv, dx, dy).rgb;
            ${wetIds ? `if (id == ${wetIds[0].toFixed(1)} || id == ${wetIds[1].toFixed(1)}) {
                bool leaf = id == ${wetIds[1].toFixed(1)};
                vec2 scale = vec2(.484375,.96875);
                vec2 wetUV = fract(uv) * scale + vec2(leaf ? .5078125 : .0078125,.015625);
                vec3 detailed = terrainSnowSample(terrainWetColor, wetUV, dx * scale, dy * scale).rgb;
                if (leaf) detailed = mix(vec3(dot(detailed,vec3(.2126,.7152,.0722))),detailed,.65)
                    * vec3(.91,.88,.81);
                float footprint = max(length(dx),length(dy));
                albedo = mix(detailed, albedo, smoothstep(.14,.29,footprint));
            }` : ''}
            color = terrainLinear(albedo);
            mapNormal = terrainAtlasSample(terrainAtlasNormal, id, uv, dx, dy).xyz * 2.0 - 1.0;
            arm = terrainAtlasSample(terrainAtlasArm, id, uv, dx, dy).rgb;
        }
        void terrainAccumulate(float id, float weight, vec2 uv, vec2 dx, vec2 dy, inout vec3 color,
            inout vec3 mapNormal, inout vec3 arm, inout float coverage) {
            if (id < 0.5 || weight < 0.001) return;
            vec4 settings = terrainParameters(id);
            float uvScale = ${TERRAIN_REPEAT_TILES.toFixed(1)} / settings.x;
            uv *= uvScale; dx *= uvScale; dy *= uvScale;
            if (settings.w > 0.0) {
                vec2 warped = terrainWarp(uv, settings.w);
                dx = terrainWarp(uv + dx, settings.w) - warped;
                dy = terrainWarp(uv + dy, settings.w) - warped;
                uv = warped;
            }
            vec3 materialColor, materialNormal, materialArm;
            terrainReadMaterial(id, uv, dx, dy, materialColor, materialNormal, materialArm);
            ${iceGlaze ? 'if (id == 12.0) terrainIceOpacity += weight * mix(0.12, 0.40, terrainIceCrack);' : ''}
            // Only sand/snow use a second continuous projection. This retains detail
            // without enlarging one low-resolution atlas cell over 16-18 tiles.
            if (id == 2.0 || id == 6.0) {
                mat2 turn = mat2(0.8, 0.6, -0.6, 0.8);
                vec3 otherColor, otherNormal, otherArm;
                terrainReadMaterial(id, turn * uv * 1.37 + vec2(0.31, 0.73),
                    turn * dx * 1.37, turn * dy * 1.37, otherColor, otherNormal, otherArm);
                // Bring the second tangent normal back to the first UV frame.
                otherNormal.xy = vec2(dot(vec2(0.8, 0.6), otherNormal.xy),
                    dot(vec2(-0.6, 0.8), otherNormal.xy));
                float balance = 0.4 + 0.2 * terrainNoise(vTerrainMaterialPosition.xz * 0.17);
                materialColor = mix(materialColor, otherColor, balance);
                materialNormal = normalize(mix(materialNormal, otherNormal, balance));
                materialArm = mix(materialArm, otherArm, balance);
            }
            materialNormal.xy *= settings.y;
            if (id == 1.0 || id == 5.0) materialArm.r = mix(1.0, materialArm.r, 0.65);
            if (id == 3.0) materialArm.r = mix(1.0, materialArm.r, 0.55);
            if (settings.z > 0.0) {
                float variation = terrainNoise(vTerrainMaterialPosition.xz * 0.19)
                    * 0.7 + terrainNoise(vTerrainMaterialPosition.xz * 0.73 + 8.3) * 0.3;
                materialColor *= 1.0 + (variation - 0.5) * settings.z * 2.0;
                if (id == 7.0) {
                    materialColor *= mix(vec3(0.91,0.96,1.0), vec3(1.07,0.94,0.84), variation);
                    materialArm.g = clamp(materialArm.g + (variation - 0.5) * 0.12, 0.08, 1.0);
                    materialArm.b *= 1.0 - variation * 0.2;
                }
            }
            color += materialColor * weight;
            mapNormal += normalize(materialNormal) * weight;
            arm += materialArm * weight;
            coverage += weight;
        }
    `;
}

function fragmentSample(sides, smooth, waterSides, shore, world, iceGlaze, grassBlend) {
    return `
        float terrainCoverage = 0.0;
        ${iceGlaze ? 'terrainIceOpacity = 0.0;' : ''}
        ${shore ? 'float shoreGrassWeight=terrainId(vTerrainOwner.xy).g==0.0?1.0:0.0;' : ''}
        vec3 terrainColor = vec3(0.0), terrainNormalSample = vec3(0.0), terrainArm = vec3(0.0);
        // Smooth lighting normals must not switch the bank's UV axis inside a face.
        vec3 terrainAxis = abs(normalize(${shore?'cross(dFdx(vTerrainMaterialPosition),dFdy(vTerrainMaterialPosition))':'vTerrainMaterialNormal'}));
        bool terrainSide = ${sides ? (smooth ? 'vTerrainOwner.z < 0.5' : 'terrainAxis.y < 0.5') : 'false'};
        vec2 terrainUv = vTerrainMaterialPosition.xz / ${TERRAIN_REPEAT_TILES.toFixed(1)};
        if (terrainSide${shore ? ' && terrainAxis.y < 0.6' : ''}) terrainUv = (terrainAxis.x > terrainAxis.z
            ? vTerrainMaterialPosition.zy : vTerrainMaterialPosition.xy) / ${TERRAIN_REPEAT_TILES.toFixed(1)};
        // Derivatives must be evaluated before the non-uniform material/weight branches.
        vec2 terrainDx = dFdx(terrainUv), terrainDy = dFdy(terrainUv);
        if (terrainAtlasReady > 0.5) {
            vec4 terrainSampleIds;
            vec4 terrainSampleWeights;
            if (terrainSide) {
                vec2 owner = ${smooth ? 'vTerrainOwner.xy' : 'floor(vTerrainMaterialPosition.xz - vTerrainMaterialNormal.xz * 0.02)'};
                float sideId = terrainId(owner).g;
                ${waterSides || shore ? `
                // Grass has no PBR side atlas entry. Use existing bare earth
                // only on the world shore and land walls adjoining water.
                if (sideId == 0.0) {
                    float nearWater = ${shore ? '1.0' : `0.0;
                    for (int z=-1; z<=1; z++) for (int x=-1; x<=1; x++) {
                        vec2 cell = clamp(owner + vec2(float(x),float(z)),vec2(0.0),terrainKindsSize-1.0);
                        nearWater = max(nearWater,texture2D(terrainKinds,(cell+0.5)/terrainKindsSize).b);
                    }`}${shore ? ';' : ''}
                    if (nearWater > 0.5) sideId = ${terrainMaterialIndex('bare_dirt').toFixed(1)};
                }` : ''}
                terrainSampleIds=vec4(sideId);
                terrainSampleWeights=vec4(1.0,0.0,0.0,0.0);
            } else {
                vec2 cell = floor(vTerrainMaterialPosition.xz - 0.5);
                vec2 fraction = fract(vTerrainMaterialPosition.xz - 0.5);
                vec2 blend = smoothstep(vec2(0.28), vec2(0.72), fraction);
                vec3 entry0=terrainId(cell),entry1=terrainId(cell+vec2(1.0,0.0)),
                    entry2=terrainId(cell+vec2(0.0,1.0)),entry3=terrainId(cell+1.0);
                terrainSampleIds=vec4(entry0.r,entry1.r,entry2.r,entry3.r);
                // Water has no ground material. It must not act as grass and
                // create a square foreign-colour fringe in the adjoining land.
                terrainSampleWeights=vec4((1.0-blend.x)*(1.0-blend.y),blend.x*(1.0-blend.y),
                    (1.0-blend.x)*blend.y,blend.x*blend.y);
                ${world ? '' : `
                vec4 groundMask = vec4(1.0) - step(vec4(0.5), vec4(entry0.b,entry1.b,entry2.b,entry3.b));
                terrainSampleWeights *= groundMask;
                // Продолжаем рисунок окружающего грунта до уреза воды.
                float extension = 1.0 - smoothstep(0.0, 0.24, dot(terrainSampleWeights, vec4(1.0)));
                if (extension > 0.0) {
                    vec2 wide = smoothstep(vec2(0.0), vec2(1.0), fraction);
                    vec4 wideWeights = vec4((1.0-wide.x)*(1.0-wide.y),wide.x*(1.0-wide.y),
                        (1.0-wide.x)*wide.y,wide.x*wide.y);
                    terrainSampleWeights += extension * wideWeights * groundMask;
                }
                terrainSampleWeights /= max(dot(terrainSampleWeights, vec4(1.0)), 0.02);`}
            }
            // Одна точка вызова сохраняет веса и порядок выборок, не размножая код материалов при компиляции.
            bool singleMaterial = all(equal(terrainSampleIds, vec4(terrainSampleIds.x)));
            int terrainSampleCount = singleMaterial ? 1 : 4;
            #if __VERSION__ >= 300
            for (int sampleIndex=0; sampleIndex<terrainSampleCount; sampleIndex++) {
            #else
            for (int sampleIndex=0; sampleIndex<4; sampleIndex++) {
                if (sampleIndex>=terrainSampleCount) break;
            #endif
                float materialWeight = singleMaterial ? ${world ? '1.0' : 'dot(terrainSampleWeights,vec4(1.0))'} : terrainSampleWeights[sampleIndex];
                terrainAccumulate(terrainSampleIds[sampleIndex], materialWeight, terrainUv, terrainDx, terrainDy,
                    terrainColor, terrainNormalSample, terrainArm, terrainCoverage);
            }
            if (terrainCoverage > 0.001) {
                ${shore ? `
                // Continue the accepted grass photos onto gentle bank shoulders,
                // leaving the wet edge and exposed cliffs as existing bare soil.
                if(terrainShoreGrassReady>0.5 && shoreGrassWeight>0.005){
                    float shoreDistance=length(max(abs(vTerrainMaterialPosition.xz-vTerrainOwner.xy-0.5)-0.5,vec2(0.0)));
                    float grassShoulder=(1.0-smoothstep(0.04,0.24,shoreDistance))
                        *smoothstep(0.45,0.94,terrainAxis.y)*0.96*shoreGrassWeight;
                    if(grassShoulder>0.005){
                        vec3 earth=terrainLinear(texture2D(terrainShoreEarth,vTerrainMaterialPosition.xz*0.125).rgb);
                        vec3 forest=terrainLinear(texture2D(terrainShoreForest,vTerrainMaterialPosition.xz*0.125).rgb);
                        terrainColor=mix(terrainColor,mix(earth,forest,0.82)*vec3(0.86,1.06,0.84)*terrainCoverage,grassShoulder);
                    }
                }` : ''}
                ${shore ? `terrainColor *= mix(1.0,0.8,smoothstep(0.03,0.24,length(max(abs(vTerrainMaterialPosition.xz-vTerrainOwner.xy-0.5)-0.5,vec2(0.0)))));` : ''}
                ${sides ? `if (terrainMaterialHasDarkness > 0.5) terrainColor *= texture2D(terrainMaterialDarkness,
                    clamp(vTerrainMaterialPosition.xz / terrainKindsSize, vec2(0.0), vec2(1.0))).r;` : ''}
                ${grassBlend ? `
                // Цвет соседних покрытий смешивается внутри доли, оставшейся после травы.
                float baseCoverage = clamp(terrainCoverage / max(1.0 - grassCoverage, 0.001), 0.0, 1.0);
                diffuseColor.rgb = mix(diffuseColor.rgb, terrainColor / terrainCoverage, baseCoverage);
                ` : 'diffuseColor.rgb = diffuseColor.rgb * (1.0 - terrainCoverage) + terrainColor;'}
                terrainArm /= terrainCoverage;
            }
        }
        ${iceGlaze ? 'diffuseColor.a *= clamp(terrainIceOpacity, 0.0, 0.58);' : ''}
    `;
}
