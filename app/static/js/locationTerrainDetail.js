const SIZE = 128;
const textureCache = new Map();

function hash(x, y, seed = 0) {
    let value = Math.imul(x + 1 + seed, 374761393) + Math.imul(y + 1, 668265263);
    value = Math.imul(value ^ (value >>> 13), 1274126177);
    return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

function tileableNoise(x, y, period, seed) {
    const cellX = Math.floor(x);
    const cellY = Math.floor(y);
    const tx = x - cellX;
    const ty = y - cellY;
    const sx = tx * tx * (3 - 2 * tx);
    const sy = ty * ty * (3 - 2 * ty);
    const x0 = cellX % period;
    const y0 = cellY % period;
    const x1 = (x0 + 1) % period;
    const y1 = (y0 + 1) % period;
    const top = hash(x0, y0, seed) * (1 - sx) + hash(x1, y0, seed) * sx;
    const bottom = hash(x0, y1, seed) * (1 - sx) + hash(x1, y1, seed) * sx;
    return top * (1 - sy) + bottom * sy;
}

function paintDefects(pixels, kind, seed, size) {
    const counts = { terrain: 72, local_terrain: 9, wall: 2, road: 80, floor: 0, fence: 13, water: 10, roof: 18 };
    for (let index = 0; index < (counts[kind] ?? 12); index++) {
        const x = Math.floor(hash(index, 11, seed) * size);
        const y = Math.floor(hash(index, 23, seed) * size);
        const width = 2 + Math.floor(hash(index, 37, seed) * (kind === 'road' ? 4 : 8));
        const height = Math.max(1, Math.round(width * (kind === 'water' ? 0.35 : 0.65)));
        const strength = kind === 'water' ? 0.08 : kind === 'wall' ? 0.025
            : kind === 'local_terrain' ? 0.12
            : 0.12 + hash(index, 41, seed) * 0.18;
        for (let dy = -height; dy <= height; dy++) {
            for (let dx = -width; dx <= width; dx++) {
                const radius = (dx / width) ** 2 + (dy / height) ** 2;
                if (radius >= 1 || hash(dx + index, dy, seed) < radius * 0.25) continue;
                const px = (x + dx + size) % size;
                const py = (y + dy + size) % size;
                const offset = (py * size + px) * 4;
                const factor = kind === 'water' ? 1 + strength * (1 - radius) : 1 - strength * (1 - radius);
                for (let channel = 0; channel < 3; channel++) pixels[offset + channel] *= factor;
            }
        }
    }
}

export function createSurfaceTexture(THREE, kind, canvas = document.createElement('canvas')) {
    const size = ['terrain', 'road', 'local_terrain'].includes(kind) ? 256 : SIZE;
    const broadCells = kind === 'local_terrain' ? 16 : size === 256 ? 32 : 8;
    const fineCells = kind === 'local_terrain' ? 64 : size === 256 ? 128 : 32;
    canvas.width = canvas.height = size;
    const context = canvas.getContext('2d');
    const image = context.createImageData(size, size);
    const seed = { terrain: 1, local_terrain: 7, wall: 2, road: 3, floor: 4, fence: 5, water: 6, roof: 8 }[kind] || 1;
    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const broad = tileableNoise(x / size * broadCells, y / size * broadCells, broadCells, seed);
            const fine = tileableNoise(x / size * fineCells, y / size * fineCells, fineCells, seed + 17);
            const grain = hash(x, y, seed);
            const offset = (y * size + x) * 4;
            if (kind === 'water') {
                const u = x / size * Math.PI * 2;
                const v = y / size * Math.PI * 2;
                const waves = Math.sin(u * 3 + Math.sin(v * 2) * 0.6)
                    + 0.4 * Math.sin(v * 5 - u * 2);
                const shade = Math.round(waves * 4 + (fine - 0.5) * 5);
                image.data[offset] = 55 + shade;
                image.data[offset + 1] = 107 + shade;
                image.data[offset + 2] = 124 + shade;
            } else {
                let tone = kind === 'local_terrain'
                    ? 0.88 + 0.08 * broad + 0.025 * fine + 0.008 * grain
                    : 0.68 + 0.26 * broad + 0.1 * fine + 0.04 * grain;
                if (kind === 'wall') {
                    tone = 0.955 + 0.035 * broad + 0.008 * fine;
                } else if (kind === 'floor') {
                    tone = 0.86 + 0.08 * broad + 0.02 * fine;
                    if (y % 32 < 1) tone *= 0.88;
                    tone += 0.008 * Math.sin(x * Math.PI * 8 / size
                        + Math.sin(y * Math.PI * 2 / size) * 0.5);
                } else if (kind === 'fence') {
                    if (x % 16 < 2) tone *= 0.72;
                    tone += 0.025 * Math.sin(x * Math.PI * 7 / 32 + Math.sin(y * Math.PI / 32));
                } else if (kind === 'road') {
                    tone += 0.08 * (grain - 0.5);
                } else if (kind === 'roof') {
                    const row = Math.floor(y / 12);
                    const seam = (x + (row % 2) * 12) % 24 < 2;
                    if (y % 12 < 2 || seam) tone *= 0.88;
                    tone += 0.035 * (grain - 0.5);
                }
                const shade = Math.round(255 * Math.min(1, Math.max(0, tone)));
                image.data[offset] = shade;
                image.data[offset + 1] = shade;
                image.data[offset + 2] = shade;
            }
            image.data[offset + 3] = 255;
        }
    }
    paintDefects(image.data, kind, seed, size);
    context.putImageData(image, 0, 0);
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.magFilter = THREE.LinearFilter;
    if (kind === 'water') texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
}

export function createTerrainDetailTexture(THREE, canvas) {
    return createSurfaceTexture(THREE, 'terrain', canvas);
}

// Shared scene materials keep these small textures alive for the lifetime of the page.
export function getSurfaceTexture(THREE, kind) {
    if (!textureCache.has(kind)) textureCache.set(kind, createSurfaceTexture(THREE, kind));
    return textureCache.get(kind);
}

export function releaseSurfaceTextures(kinds) {
    for (const kind of kinds) textureCache.get(kind)?.dispose();
}

export function applySurfacePattern(material, texture, scale = 0.25, projection = 'sides') {
    const previousCompile = material.onBeforeCompile;
    const previousKey = material.customProgramCacheKey?.bind(material);
    material.userData.surfacePattern = { previousCompile, normal: projection !== 'top',
        batchKey: `${texture.id}:${scale}:${projection}` };
    material.customProgramCacheKey = () => `${previousKey ? previousKey() : ''}:surface-pattern:${projection}`;
    material.onBeforeCompile = shader => {
        previousCompile?.(shader);
        shader.uniforms.surfacePatternMap = { value: texture };
        shader.uniforms.surfacePatternScale = { value: scale };
        const normalVarying = projection === 'top' ? '' : 'varying vec3 vSurfaceWorldNormal;';
        const normalVertex = projection === 'top' ? '' : `
                vec3 surfaceNormal = normal;
                #ifdef USE_INSTANCING
                    surfaceNormal = mat3(instanceMatrix) * surfaceNormal;
                #endif
                vSurfaceWorldNormal = normalize(mat3(modelMatrix) * surfaceNormal);
            `;
        const normalFragment = projection === 'top' ? '' : 'vec3 surfaceAxis = abs(normalize(vSurfaceWorldNormal));';
        const surfaceCoords = projection === 'top' ? 'vSurfaceWorldPos.xz' : `
                (surfaceAxis.y > max(surfaceAxis.x, surfaceAxis.z)
                    ? vSurfaceWorldPos.xz
                    : (surfaceAxis.x > surfaceAxis.z ? vSurfaceWorldPos.zy : vSurfaceWorldPos.xy))`;
        shader.vertexShader = shader.vertexShader
            .replace('#include <common>', `#include <common>
                varying vec3 vSurfaceWorldPos;
                ${normalVarying}
            `)
            .replace('#include <project_vertex>', `
                vec4 surfaceWorld = vec4(transformed, 1.0);
                #ifdef USE_INSTANCING
                    surfaceWorld = instanceMatrix * surfaceWorld;
                #endif
                vSurfaceWorldPos = (modelMatrix * surfaceWorld).xyz;
                ${normalVertex}
                #include <project_vertex>
            `);
        shader.fragmentShader = shader.fragmentShader
            .replace('#include <common>', `#include <common>
                uniform sampler2D surfacePatternMap;
                uniform float surfacePatternScale;
                varying vec3 vSurfaceWorldPos;
                ${normalVarying}
            `)
            .replace('#include <color_fragment>', `#include <color_fragment>
                ${normalFragment}
                vec2 surfaceUv = ${surfaceCoords};
                float surfaceShade = texture2D(surfacePatternMap, surfaceUv * surfacePatternScale).r;
                diffuseColor.rgb *= 0.55 + 0.55 * surfaceShade;
            `);
    };
    material.userData.surfacePattern.compile = material.onBeforeCompile;
}
