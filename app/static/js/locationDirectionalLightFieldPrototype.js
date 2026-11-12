export function wallCrossesRay(x1, z1, y1, x2, z2, y2, wall) {
    const rx = x2 - x1;
    const rz = z2 - z1;
    const sx = wall.x2 - wall.x1;
    const sz = wall.z2 - wall.z1;
    const divisor = rx * sz - rz * sx;
    if (Math.abs(divisor) < 1e-8) return false;
    const dx = wall.x1 - x1;
    const dz = wall.z1 - z1;
    const rayFraction = (dx * sz - dz * sx) / divisor;
    const wallFraction = (dx * rz - dz * rx) / divisor;
    const crossingY = y1 + (y2 - y1) * rayFraction;
    return rayFraction > 1e-4 && rayFraction < 1 - 1e-4
        && wallFraction >= -1e-4 && wallFraction <= 1 + 1e-4
        && (wall.bottomY == null || crossingY >= wall.bottomY - 1e-4)
        && (wall.topY == null || crossingY <= wall.topY + 1e-4);
}


export const DIRECTIONAL_FIELD_HEIGHTS = Object.freeze([0, 1.4, 2.8]);
export const DIRECTIONAL_FIELD_RANGE = 64;
const DIRECTIONS = 6;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function rgb(hex) {
    const match = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!match) throw new Error(`Invalid directional field light color: ${hex}`);
    const color = Number.parseInt(match[1], 16);
    return [(color >> 16) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255];
}

export function createDirectionalLightField({ width, height, levels, samplesPerMeter = 2 }) {
    const sliceHeights = DIRECTIONAL_FIELD_HEIGHTS;
    if (!(width > 0 && height > 0) || !Number.isInteger(samplesPerMeter) || samplesPerMeter < 1
        || !Array.isArray(levels) || !levels.length || levels.some(level =>
            !Number.isFinite(Number(level.id)) || !Number.isFinite(Number(level.y)))) {
        throw new Error('Invalid directional field dimensions or height slices');
    }
    const columns = Math.ceil(width * samplesPerMeter);
    const rows = Math.ceil(height * samplesPerMeter);
    const levelIndex = new Map(levels.map((level, index) => [Number(level.id), index]));
    if (levelIndex.size !== levels.length) throw new Error('Duplicate directional field level');
    const planes = levels.length * sliceHeights.length * DIRECTIONS;
    const atlasColumns = Math.ceil(Math.sqrt(planes));
    const atlasRows = Math.ceil(planes / atlasColumns);
    const textureWidth = columns * atlasColumns;
    const textureHeight = rows * atlasRows;
    const atlasCells = textureWidth * textureHeight;
    const pixels = new Uint8Array(atlasCells * 4);
    const radiance = new Float32Array(atlasCells * 3);
    const hasLight = { value: false };

    function planeOffset(level, slice, direction, column, row) {
        const plane = (level * sliceHeights.length + slice) * DIRECTIONS + direction;
        return (Math.floor(plane / atlasColumns) * rows + row) * textureWidth
            + (plane % atlasColumns) * columns + column;
    }

    function rebuild({ lamps = [], walls = [] } = {}) {
        const startedAt = performance.now();
        radiance.fill(0);
        let sampleVisits = 0;
        let blockedSamples = 0;
        let contributions = 0;
        const lampStats = lamps.map(lamp => ({ id: lamp.id,
            route: !(Number(lamp.intensity) > 0) || !(Number(lamp.distance) > 0) ? 'off'
                    : !levelIndex.has(Number(lamp.level)) ? 'missing-level' : 'field',
            contributions: 0 }));
        lamps.forEach((lamp, lampIndex) => {
            if (lampStats[lampIndex].route !== 'field') return;
            const layer = levelIndex.get(Number(lamp.level));
            const x = Number(lamp.x);
            const y = Number(lamp.y);
            const z = Number(lamp.z);
            const radius = Number(lamp.distance);
            if (!Number.isFinite(x + y + z + radius)) {
                lampStats[lampIndex].route = 'invalid';
                return;
            }
            const isSpot = lamp.kind === 'spot';
            const azimuth = Number(lamp.azimuth || 0) * Math.PI / 180;
            const drop = Math.min(3, Number(lamp.height || 0));
            const axisLength = Math.hypot(5, drop);
            const spotAxis = isSpot ? [5 * Math.cos(azimuth) / axisLength,
                -drop / axisLength, 5 * Math.sin(azimuth) / axisLength] : null;
            const coneCos = Math.cos(Math.PI / 5);
            const penumbraCos = Math.cos(Math.PI / 5 * (1 - 0.55));
            const color = rgb(lamp.color);
            const nearbyWalls = walls.filter(wall => Number(wall.level) === Number(lamp.level)
                && Math.min(wall.x1, wall.x2) <= x + radius
                && Math.max(wall.x1, wall.x2) >= x - radius
                && Math.min(wall.z1, wall.z2) <= z + radius
                && Math.max(wall.z1, wall.z2) >= z - radius);
            const xMin = clamp(Math.floor((x - radius) * samplesPerMeter), 0, columns - 1);
            const xMax = clamp(Math.ceil((x + radius) * samplesPerMeter), 0, columns - 1);
            const zMin = clamp(Math.floor((z - radius) * samplesPerMeter), 0, rows - 1);
            const zMax = clamp(Math.ceil((z + radius) * samplesPerMeter), 0, rows - 1);
            for (let slice = 0; slice < sliceHeights.length; slice++) {
                const sampleY = Number(levels[layer].y) + sliceHeights[slice];
                for (let row = zMin; row <= zMax; row++) {
                    const worldZ = (row + 0.5) / samplesPerMeter;
                    for (let column = xMin; column <= xMax; column++) {
                        sampleVisits++;
                        const worldX = (column + 0.5) / samplesPerMeter;
                        const vx = x - worldX;
                        const vy = y - sampleY;
                        const vz = z - worldZ;
                        const distance = Math.hypot(vx, vy, vz);
                        if (distance >= radius) continue;
                        let coneWeight = 1;
                        if (isSpot && distance > 1e-6) {
                            const cosine = (-vx * spotAxis[0] - vy * spotAxis[1]
                                - vz * spotAxis[2]) / distance;
                            if (cosine <= coneCos) continue;
                            const blend = clamp((cosine - coneCos) / (penumbraCos - coneCos), 0, 1);
                            coneWeight = blend * blend * (3 - 2 * blend);
                        }
                        if (nearbyWalls.some(wall => wallCrossesRay(x, z, y,
                            worldX, worldZ, sampleY, wall))) {
                            blockedSamples++;
                            continue;
                        }
                        const strength = Number(lamp.intensity) * coneWeight
                            * (1 - distance / radius) ** 2;
                        const directions = distance < 1e-6
                            ? [1, 1, 1, 1, 1, 1]
                            : [Math.max(vx, 0), Math.max(-vx, 0),
                                Math.max(vy, 0), Math.max(-vy, 0),
                                Math.max(vz, 0), Math.max(-vz, 0)];
                        for (let direction = 0; direction < DIRECTIONS; direction++) {
                            const directionalStrength = strength * directions[direction]
                                / (distance < 1e-6 ? 1 : distance);
                            if (!directionalStrength) continue;
                            const offset = planeOffset(layer, slice, direction, column, row) * 3;
                            for (let channel = 0; channel < 3; channel++) {
                                radiance[offset + channel] += color[channel] * directionalStrength;
                            }
                        }
                        contributions++;
                        lampStats[lampIndex].contributions++;
                    }
                }
            }
        });
        // Общий флаг читают и исходные материалы, и их объединённые копии.
        hasLight.value = contributions > 0;
        if (!hasLight.value) pixels.fill(0);
        const encodedCells = hasLight.value ? atlasCells : 0;
        for (let index = 0; index < encodedCells; index++) {
            const source = index * 3;
            const destination = index * 4;
            const peak = Math.max(radiance[source], radiance[source + 1], radiance[source + 2]);
            const multiplier = Math.ceil(255 * Math.min(peak / DIRECTIONAL_FIELD_RANGE, 1)) / 255;
            for (let channel = 0; channel < 3; channel++) {
                pixels[destination + channel] = multiplier
                    ? Math.round(255 * Math.min(radiance[source + channel]
                        / (multiplier * DIRECTIONAL_FIELD_RANGE), 1)) : 0;
            }
            pixels[destination + 3] = Math.round(multiplier * 255);
        }
        return { buildMs: performance.now() - startedAt, sampleVisits, blockedSamples,
            contributions, activeLights: lampStats.filter(item => item.route === 'field').length,
            fieldBytes: pixels.byteLength + radiance.byteLength, textureBytes: pixels.byteLength,
            lampStats };
    }

    function readPlane(layer, slice, direction, column, row) {
        const offset = planeOffset(layer, slice, direction, column, row) * 4;
        const factor = pixels[offset + 3] * DIRECTIONAL_FIELD_RANGE / (255 * 255);
        return [pixels[offset] * factor, pixels[offset + 1] * factor,
            pixels[offset + 2] * factor];
    }

    function sample(x, y, z, normal, level = 0) {
        const layer = levelIndex.get(Number(level));
        if (layer == null) return [0, 0, 0];
        const column = clamp(Math.floor(x * samplesPerMeter), 0, columns - 1);
        const row = clamp(Math.floor(z * samplesPerMeter), 0, rows - 1);
        const relativeY = y - Number(levels[layer].y);
        const found = sliceHeights.findIndex((height, index) =>
            index < sliceHeights.length - 1 && relativeY <= sliceHeights[index + 1]);
        const lower = found < 0 ? sliceHeights.length - 2 : found;
        const blend = clamp((relativeY - sliceHeights[lower])
            / (sliceHeights[lower + 1] - sliceHeights[lower]), 0, 1);
        const length = Math.hypot(normal.x, normal.y, normal.z) || 1;
        const components = [normal.x / length, normal.y / length, normal.z / length];
        const result = [0, 0, 0];
        components.forEach((component, axis) => {
            if (!component) return;
            const direction = axis * 2 + (component < 0 ? 1 : 0);
            const first = readPlane(layer, lower, direction, column, row);
            const second = readPlane(layer, lower + 1, direction, column, row);
            for (let channel = 0; channel < 3; channel++) {
                result[channel] += Math.abs(component)
                    * (first[channel] * (1 - blend) + second[channel] * blend);
            }
        });
        return result;
    }

    return { width, height, levels, columns, rows, sliceHeights, samplesPerMeter,
        pixels, sample, rebuild, planes, atlasColumns, atlasRows,
        textureWidth, textureHeight, hasLight };
}

export function createDirectionalLightFieldTexture(THREE, field) {
    const texture = new THREE.DataTexture(field.pixels, field.textureWidth,
        field.textureHeight, THREE.RGBAFormat);
    texture.minFilter = texture.magFilter = THREE.LinearFilter;
    texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping;
    texture.generateMipmaps = false;
    texture.unpackAlignment = 1;
    texture.needsUpdate = true;
    return texture;
}

export function applyDirectionalLightFieldMaterial(material, texture, field, level = 0) {
    if (!material?.isMeshStandardMaterial || !field.levels.some(item => Number(item.id) === Number(level))) {
        throw new Error('Directional field requires a PBR material and known level');
    }
    if (material.userData.directionalLightFieldPrototype) {
        throw new Error('Directional field already installed on this material');
    }
    const previousCompile = material.onBeforeCompile;
    const previousKey = material.customProgramCacheKey;
    material.userData.directionalLightFieldPrototype = { previousCompile, previousKey,
        batchKey: `${texture.id}:${field.levels.findIndex(item => Number(item.id) === Number(level))}` };
    material.customProgramCacheKey = () => `${previousKey ? previousKey.call(material) : ''}:directional-field-v3`;
    material.onBeforeCompile = shader => {
        previousCompile?.(shader);
        const vertexMarker = '#include <project_vertex>';
        const fragmentMarker = '#include <lights_fragment_end>';
        if (!shader.vertexShader.includes(vertexMarker) || !shader.fragmentShader.includes(fragmentMarker)) {
            throw new Error('Unsupported Three.js shader chunks for directional field');
        }
        shader.uniforms.directionalFieldMap = { value: texture };
        shader.uniforms.directionalFieldActive = field.hasLight;
        shader.uniforms.directionalFieldGrid = { value: [field.columns, field.rows] };
        shader.uniforms.directionalFieldSize = { value: [field.width, field.height] };
        shader.uniforms.directionalFieldSamplesPerMeter = { value: field.samplesPerMeter };
        shader.uniforms.directionalFieldLevel = { value: field.levels.findIndex(item => Number(item.id) === Number(level)) };
        shader.uniforms.directionalFieldBaseY = { value: Number(field.levels.find(item => Number(item.id) === Number(level)).y) };
        shader.uniforms.directionalFieldAtlasColumns = { value: field.atlasColumns };
        shader.uniforms.directionalFieldAtlasRows = { value: field.atlasRows };
        shader.vertexShader = shader.vertexShader
            .replace('#include <common>', `#include <common>
                varying vec3 vDirectionalFieldWorldPosition;`)
            .replace(vertexMarker, `
                vec4 directionalFieldPosition = vec4(transformed, 1.0);
                #ifdef USE_INSTANCING
                    directionalFieldPosition = instanceMatrix * directionalFieldPosition;
                #endif
                vDirectionalFieldWorldPosition = (modelMatrix * directionalFieldPosition).xyz;
                ${vertexMarker}`);
        shader.fragmentShader = shader.fragmentShader
            .replace('#include <common>', `#include <common>
                uniform sampler2D directionalFieldMap;
                uniform bool directionalFieldActive;
                uniform vec2 directionalFieldGrid;
                uniform vec2 directionalFieldSize;
                uniform float directionalFieldSamplesPerMeter;
                uniform float directionalFieldLevel;
                uniform float directionalFieldBaseY;
                uniform float directionalFieldAtlasColumns;
                uniform float directionalFieldAtlasRows;
                varying vec3 vDirectionalFieldWorldPosition;
                vec3 directionalFieldRead(vec2 cell, float heightSlice, float direction) {
                    float plane = (directionalFieldLevel * ${DIRECTIONAL_FIELD_HEIGHTS.length.toFixed(1)}
                        + heightSlice) * 6.0 + direction;
                    vec2 tile = vec2(mod(plane, directionalFieldAtlasColumns),
                        floor(plane / directionalFieldAtlasColumns));
                    vec2 uv = (tile * directionalFieldGrid + cell + vec2(0.5))
                        / (directionalFieldGrid
                            * vec2(directionalFieldAtlasColumns, directionalFieldAtlasRows));
                    vec4 encoded = texture2D(directionalFieldMap, uv);
                    return encoded.rgb * encoded.a * ${DIRECTIONAL_FIELD_RANGE.toFixed(1)};
                }`)
            .replace(fragmentMarker, `${fragmentMarker}
                if (directionalFieldActive) {
                    vec3 fieldNormal = inverseTransformDirection(normal, viewMatrix);
                    vec2 position = clamp(vDirectionalFieldWorldPosition.xz,
                        vec2(0.0), directionalFieldSize - vec2(0.0001));
                    vec2 cell = clamp(position * directionalFieldSamplesPerMeter - vec2(0.5),
                        vec2(0.0), directionalFieldGrid - vec2(1.0));
                    float relativeY = vDirectionalFieldWorldPosition.y - directionalFieldBaseY;
                    float lower = clamp(floor(relativeY / 1.4), 0.0, 1.0);
                    float blend = clamp((relativeY - lower * 1.4) / 1.4, 0.0, 1.0);
                    vec3 fieldIrradiance = vec3(0.0);
                    for (int axis = 0; axis < 3; axis++) {
                        float component = fieldNormal[axis];
                        float direction = float(axis * 2) + (component < 0.0 ? 1.0 : 0.0);
                        vec3 first = directionalFieldRead(cell, lower, direction);
                        vec3 second = directionalFieldRead(cell, lower + 1.0, direction);
                        fieldIrradiance += abs(component) * mix(first, second, blend);
                    }
                    #ifdef LEGACY_LIGHTS
                        fieldIrradiance *= PI;
                    #endif
                    reflectedLight.indirectDiffuse += fieldIrradiance
                        * BRDF_Lambert(material.diffuseColor);
                }`);
    };
    material.userData.directionalLightFieldPrototype.compile = material.onBeforeCompile;
    material.needsUpdate = true;
}

export function removeDirectionalLightFieldMaterial(material) {
    const state = material?.userData?.directionalLightFieldPrototype;
    if (!state) return false;
    material.onBeforeCompile = state.previousCompile;
    material.customProgramCacheKey = state.previousKey;
    delete material.userData.directionalLightFieldPrototype;
    // r128 caches one uniform set per material; release its old program before reuse.
    material.dispose?.();
    material.needsUpdate = true;
    return true;
}
