import { grassSubtype } from './locationGrassSubtype.js';

const COLORS = {
    grass: [94, 83, 63],
    sand: [193, 175, 125],
    swamp: [75, 65, 45],
    rock: [126, 128, 123],
    water: [43, 70, 62],
    concrete: [179, 177, 168], gravel: [130, 121, 107],
    snow: [212, 217, 217], ice: [194, 207, 209], metal: [133, 101, 77],
    bare_dirt: [122, 105, 79], granite_tiles: [179, 177, 168], wood_floor: [119, 86, 53],
};

let forestGroundPixels = null;
let plainGroundPixels = null;
let forestGroundImage = null;
let plainGroundImage = null;
let grassGroundLoading = null;
const FOREST_TEXTURE_SIZE = 512;
const FOREST_TEXELS_PER_TILE = 64;

function loadGroundPixels(url) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => {
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = FOREST_TEXTURE_SIZE;
            const context = canvas.getContext('2d', { willReadFrequently: true });
            if (!context) { reject(new Error('Forest ground canvas unavailable')); return; }
            context.drawImage(image, 0, 0, FOREST_TEXTURE_SIZE, FOREST_TEXTURE_SIZE);
            resolve({ image, pixels: context.getImageData(0, 0,
                FOREST_TEXTURE_SIZE, FOREST_TEXTURE_SIZE).data });
        };
        image.onerror = reject;
        image.src = url;
    });
}

export function loadGrassGroundPixels() {
    if (plainGroundPixels && forestGroundPixels) return Promise.resolve();
    if (grassGroundLoading) return grassGroundLoading;
    grassGroundLoading = Promise.all([
        loadGroundPixels('/static/materials/art/dirty_earth/color.jpg'),
        loadGroundPixels('/static/textures/vegetation/forrest_ground_01_diff_1k.jpg'),
    ]).then(([plain, forest]) => {
        plainGroundPixels = plain.pixels;
        forestGroundPixels = forest.pixels;
        plainGroundImage = plain.image;
        forestGroundImage = forest.image;
    }).catch(error => {
        grassGroundLoading = null;
        throw error;
    });
    return grassGroundLoading;
}

export function getGrassGroundImages() {
    return plainGroundImage && forestGroundImage
        ? { plain: plainGroundImage, forest: forestGroundImage } : null;
}

// Только цветовые данные: рабочим потокам не нужны DOM и исходные Image.
export function getGrassGroundPixelData() {
    return { plain: plainGroundPixels, forest: forestGroundPixels };
}

export function setGrassGroundPixelData({ plain, forest }) {
    plainGroundPixels = plain;
    forestGroundPixels = forest;
}

function hash(x, z, seed = 0) {
    let value = Math.imul(x + seed * 37 + 1, 374761393)
        + Math.imul(z + seed * 53 + 1, 668265263);
    value = Math.imul(value ^ (value >>> 13), 1274126177);
    return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

function noise(x, z, scale, seed) {
    const px = x * scale;
    const pz = z * scale;
    const ix = Math.floor(px);
    const iz = Math.floor(pz);
    const fx = px - ix;
    const fz = pz - iz;
    const sx = fx * fx * (3 - 2 * fx);
    const sz = fz * fz * (3 - 2 * fz);
    const top = hash(ix, iz, seed) * (1 - sx) + hash(ix + 1, iz, seed) * sx;
    const bottom = hash(ix, iz + 1, seed) * (1 - sx) + hash(ix + 1, iz + 1, seed) * sx;
    return top * (1 - sz) + bottom * sz;
}

const smoothstep = value => {
    const t = Math.max(0, Math.min(1, value));
    return t * t * (3 - 2 * t);
};

function terrainAt(tiles, x, z) {
    const clampedZ = Math.max(0, Math.min(tiles.length - 1, z));
    const clampedX = Math.max(0, Math.min(tiles[clampedZ].length - 1, x));
    const tile = tiles[clampedZ][clampedX];
    return tile?.terrain === 'grass' && grassSubtype(tile) === 'forest'
        ? 'grass_forest' : tile?.terrain || 'grass';
}

function terrainColor(kind, detail, x, z, useGrassGroundPixels = true, profile = null) {
    if (useGrassGroundPixels && forestGroundPixels && plainGroundPixels
        && (kind === 'grass' || kind === 'grass_forest')) {
        let photoStartedAt = 0;
        if (profile && ++profile.photoColorCalls % 256 === 0) photoStartedAt = performance.now();
        const sx = x * FOREST_TEXELS_PER_TILE;
        const sz = z * FOREST_TEXELS_PER_TILE;
        const x0 = ((Math.floor(sx) % FOREST_TEXTURE_SIZE) + FOREST_TEXTURE_SIZE) % FOREST_TEXTURE_SIZE;
        const z0 = ((Math.floor(sz) % FOREST_TEXTURE_SIZE) + FOREST_TEXTURE_SIZE) % FOREST_TEXTURE_SIZE;
        const x1 = (x0 + 1) % FOREST_TEXTURE_SIZE;
        const z1 = (z0 + 1) % FOREST_TEXTURE_SIZE;
        const fx = sx - Math.floor(sx);
        const fz = sz - Math.floor(sz);
        const topLeft = (z0 * FOREST_TEXTURE_SIZE + x0) * 4;
        const topRight = (z0 * FOREST_TEXTURE_SIZE + x1) * 4;
        const bottomLeft = (z1 * FOREST_TEXTURE_SIZE + x0) * 4;
        const bottomRight = (z1 * FOREST_TEXTURE_SIZE + x1) * 4;
        const forestOnly = kind === 'grass_forest';
        const cover = 0.82 - detail.dirt * 0.3;
        const result = new Array(3);
        for (let channel = 0; channel < 3; channel++) {
            const forestTop = forestGroundPixels[topLeft + channel] * (1 - fx)
                + forestGroundPixels[topRight + channel] * fx;
            const forestBottom = forestGroundPixels[bottomLeft + channel] * (1 - fx)
                + forestGroundPixels[bottomRight + channel] * fx;
            const forest = forestTop * (1 - fz) + forestBottom * fz;
            if (forestOnly) {
                result[channel] = forest * [0.88, 0.93, 0.84][channel] + detail.broad * 6;
                continue;
            }
            const earthTop = plainGroundPixels[topLeft + channel] * (1 - fx)
                + plainGroundPixels[topRight + channel] * fx;
            const earthBottom = plainGroundPixels[bottomLeft + channel] * (1 - fx)
                + plainGroundPixels[bottomRight + channel] * fx;
            const earth = earthTop * (1 - fz) + earthBottom * fz;
            result[channel] = (forest * cover + earth * (1 - cover))
                * [0.86, 1.06, 0.84][channel] + detail.broad * 7;
        }
        if (photoStartedAt) {
            profile.photoSampledMs += performance.now() - photoStartedAt;
            profile.photoSampledCalls++;
        }
        return result;
    }
    if (kind === 'grass_forest') kind = 'grass';
    const color = COLORS[kind] || COLORS.grass;
    const { broad, fine, speck, dirt, moss, wet } = detail;
    if (kind === 'grass') {
        return [color[0] + broad * 23 + dirt * 33 + fine * 8,
            color[1] + broad * 21 + dirt * 13 + fine * 8,
            color[2] + broad * 17 + dirt * 10 + fine * 7];
    }
    if (kind === 'sand') {
        const grain = speck > 0.988 ? -27 : speck < 0.012 ? 13 : 0;
        return color.map((value, channel) => value + broad * 19 + fine * 9 + grain * (channel === 2 ? 0.8 : 1));
    }
    if (kind === 'swamp') {
        const silt = speck > 0.965 ? -10 : speck < 0.035 ? 8 : 0;
        return [color[0] + broad * 9 + fine * 7 - moss * 12 - wet * 26 + silt,
            color[1] + broad * 9 + fine * 8 + moss * 16 - wet * 5 + silt,
            color[2] + broad * 7 + fine * 6 + moss * 4 + wet * 14 + silt];
    }
    if (kind === 'rock') {
        const grit = speck > 0.976 ? -19 : speck < 0.02 ? 12 : 0;
        return color.map(value => value + broad * 23 + fine * 16 + grit);
    }
    return color.map(value => value + broad * 8);
}

function paintPixels(tiles, image, pixelsPerTile, startX, startZ, endX, endZ,
    originX = 0, originZ = 0, sampleTerrain = (x, z) => terrainAt(tiles, x, z),
    useGrassGroundPixels = true, profile = null) {
    const { data, width } = image;
    // World regions revisit the same few terrain tiles for thousands of pixels.
    let worldRows = null;
    let firstTileX = 0;
    let firstTileZ = 0;
    if (!tiles && endX > startX && endZ > startZ) {
        firstTileX = Math.floor(originX + (startX + 0.5) / pixelsPerTile - 0.5);
        firstTileZ = Math.floor(originZ + (startZ + 0.5) / pixelsPerTile - 0.5);
        const lastTileX = Math.floor(originX + (endX - 0.5) / pixelsPerTile - 0.5) + 1;
        const lastTileZ = Math.floor(originZ + (endZ - 0.5) / pixelsPerTile - 0.5) + 1;
        worldRows = Array.from({ length: lastTileZ - firstTileZ + 1 }, (_, z) =>
            Array.from({ length: lastTileX - firstTileX + 1 }, (_, x) =>
                sampleTerrain(firstTileX + x, firstTileZ + z)));
        if (profile) profile.terrainLookups += worldRows.length * worldRows[0].length;
    }
    if (profile) profile.pixels += (endX - startX) * (endZ - startZ);
    for (let pz = startZ; pz < endZ; pz++) {
        for (let px = startX; px < endX; px++) {
            const x = originX + (px + 0.5) / pixelsPerTile;
            const z = originZ + (pz + 0.5) / pixelsPerTile;
            const cx = x - 0.5;
            const cz = z - 0.5;
            const ix = Math.floor(cx);
            const iz = Math.floor(cz);
            const row = worldRows?.[iz - firstTileZ];
            const nextRow = worldRows?.[iz + 1 - firstTileZ];
            const column = ix - firstTileX;
            const raw00 = row?.[column];
            const raw10 = row?.[column + 1];
            const raw01 = nextRow?.[column];
            const raw11 = nextRow?.[column + 1];
            const uniformWorldTile = worldRows && raw00 === raw10 && raw00 === raw01 && raw00 === raw11;
            const wobbleX = uniformWorldTile ? 0 : (noise(ix + 0.5, z, 2.4, 63) - 0.5) * 0.13;
            const wobbleZ = uniformWorldTile ? 0 : (noise(x, iz + 0.5, 2.4, 71) - 0.5) * 0.13;
            const wx = uniformWorldTile ? 0 : smoothstep((cx - ix - 0.28 - wobbleX) / 0.44);
            const wz = uniformWorldTile ? 0 : smoothstep((cz - iz - 0.28 - wobbleZ) / 0.44);
            const w00 = (1 - wx) * (1 - wz);
            const w10 = wx * (1 - wz);
            const w01 = (1 - wx) * wz;
            const w11 = wx * wz;
            const k00 = w00 ? (worldRows ? raw00 : sampleTerrain(ix, iz)) : null;
            const k10 = w10 ? (worldRows ? raw10 : sampleTerrain(ix + 1, iz)) : null;
            const k01 = w01 ? (worldRows ? raw01 : sampleTerrain(ix, iz + 1)) : null;
            const k11 = w11 ? (worldRows ? raw11 : sampleTerrain(ix + 1, iz + 1)) : null;
            const grass = k00 === 'grass' || k00 === 'grass_forest'
                || k10 === 'grass' || k10 === 'grass_forest'
                || k01 === 'grass' || k01 === 'grass_forest'
                || k11 === 'grass' || k11 === 'grass_forest';
            const swamp = k00 === 'swamp' || k10 === 'swamp' || k01 === 'swamp' || k11 === 'swamp';
            const detailColor = (k00 && k00 !== 'water') || (k10 && k10 !== 'water')
                || (k01 && k01 !== 'water') || (k11 && k11 !== 'water');
            const redundantPhotoDetail = uniformWorldTile && useGrassGroundPixels
                && forestGroundPixels && plainGroundPixels
                && (raw00 === 'grass' || raw00 === 'grass_forest');
            const detail = {
                broad: noise(x, z, 0.82, 7) - 0.5,
                fine: detailColor && !redundantPhotoDetail ? noise(x, z, 5.8, 23) - 0.5 : 0,
                speck: !redundantPhotoDetail ? hash(Math.floor(originX * pixelsPerTile) + px,
                    Math.floor(originZ * pixelsPerTile) + pz, 41) : 0,
                dirt: grass ? smoothstep((noise(x, z, 1.15, 12) - 0.48) * 4) : 0,
                moss: swamp ? smoothstep((noise(x, z, 6.2, 31) - 0.56) * 12) : 0,
                wet: swamp ? smoothstep((noise(x, z, 3.4, 51)
                    + (noise(x, z, 10.4, 52) - 0.5) * 0.22 - 0.62) * 10) : 0,
            };
            const rgb = [0, 0, 0];
            const firstKind = k00 || k10 || k01 || k11;
            if ((!k00 || k00 === firstKind) && (!k10 || k10 === firstKind)
                && (!k01 || k01 === firstKind) && (!k11 || k11 === firstKind)) {
                const sample = terrainColor(firstKind, detail, x, z, useGrassGroundPixels, profile);
                rgb[0] = sample[0]; rgb[1] = sample[1]; rgb[2] = sample[2];
            } else {
                for (const [kind, weight] of [[k00, w00], [k10, w10], [k01, w01], [k11, w11]]) {
                    if (!kind) continue;
                    const sample = terrainColor(kind, detail, x, z, useGrassGroundPixels, profile);
                    for (let channel = 0; channel < 3; channel++) rgb[channel] += sample[channel] * weight;
                }
            }
            const index = (pz * width + px) * 4;
            for (let channel = 0; channel < 3; channel++) {
                data[index + channel] = Math.max(0, Math.min(255, Math.round(rgb[channel])));
            }
            if (image.grassCoverageInAlpha) {
                if (uniformWorldTile || !grass) {
                    data[index + 3] = grass ? 255 : 0;
                } else {
                    const coverage = (k00 === 'grass' || k00 === 'grass_forest' ? w00 : 0)
                        + (k10 === 'grass' || k10 === 'grass_forest' ? w10 : 0)
                        + (k01 === 'grass' || k01 === 'grass_forest' ? w01 : 0)
                        + (k11 === 'grass' || k11 === 'grass_forest' ? w11 : 0);
                    data[index + 3] = Math.round(coverage * 255);
                }
            } else {
                data[index + 3] = 255;
            }
            if (image.grassBlendData) {
                const plain = (k00 === 'grass' ? w00 : 0) + (k10 === 'grass' ? w10 : 0)
                    + (k01 === 'grass' ? w01 : 0) + (k11 === 'grass' ? w11 : 0);
                const forest = (k00 === 'grass_forest' ? w00 : 0)
                    + (k10 === 'grass_forest' ? w10 : 0)
                    + (k01 === 'grass_forest' ? w01 : 0)
                    + (k11 === 'grass_forest' ? w11 : 0);
                image.grassBlendData[index] = Math.round(plain * 255);
                image.grassBlendData[index + 1] = Math.round(forest * 255);
                image.grassBlendData[index + 2] = 0;
                image.grassBlendData[index + 3] = 255;
            }
        }
    }
}

export function createLocationTerrainPixels(tiles, pixelsPerTile = 20,
    includeGrassBlend = false) {
    const width = (tiles[0]?.length || 0) * pixelsPerTile;
    const height = tiles.length * pixelsPerTile;
    const data = new Uint8Array(width * height * 4);
    const image = { data, width, height };
    if (includeGrassBlend) image.grassBlendData = new Uint8Array(data.length);
    paintPixels(tiles, image, pixelsPerTile, 0, 0, width, height);
    return image;
}

export function createTerrainPixelsRegion(sampleTerrain, originX, originZ,
    textureSize = 512, pixelsPerTile = 12, useGrassGroundPixels = true, profile = null,
    grassCoverageInAlpha = false) {
    const data = new Uint8Array(textureSize * textureSize * 4);
    const image = { data, width: textureSize, height: textureSize, grassCoverageInAlpha };
    paintPixels(null, image, pixelsPerTile, 0, 0, textureSize, textureSize,
        originX, originZ, sampleTerrain, useGrassGroundPixels, profile);
    return image;
}

export function updateTerrainPixelsRegion(image, sampleTerrain, originX, originZ,
    bounds, pixelsPerTile = 12, useGrassGroundPixels = true, profile = null,
    grassCoverageInAlpha = image.grassCoverageInAlpha === true) {
    image.grassCoverageInAlpha = grassCoverageInAlpha;
    const startX = Math.max(0, Math.floor((bounds.minX - 1 - originX) * pixelsPerTile));
    const startZ = Math.max(0, Math.floor((bounds.minZ - 1 - originZ) * pixelsPerTile));
    const endX = Math.min(image.width, Math.ceil((bounds.maxX + 2 - originX) * pixelsPerTile));
    const endZ = Math.min(image.height, Math.ceil((bounds.maxZ + 2 - originZ) * pixelsPerTile));
    if (endX <= startX || endZ <= startZ) return false;
    paintPixels(null, image, pixelsPerTile, startX, startZ, endX, endZ,
        originX, originZ, sampleTerrain, useGrassGroundPixels, profile);
    return true;
}

export function updateLocationTerrainPixels(tiles, image, changedTiles) {
    if (!changedTiles.length || !image.width || !image.height) return;
    const pixelsPerTile = image.width / (tiles[0]?.length || 1);
    const minX = Math.min(...changedTiles.map(tile => tile.x));
    const maxX = Math.max(...changedTiles.map(tile => tile.x));
    const minZ = Math.min(...changedTiles.map(tile => tile.z));
    const maxZ = Math.max(...changedTiles.map(tile => tile.z));
    paintPixels(tiles, image, pixelsPerTile,
        Math.max(0, Math.floor((minX - 1) * pixelsPerTile)),
        Math.max(0, Math.floor((minZ - 1) * pixelsPerTile)),
        Math.min(image.width, Math.ceil((maxX + 2) * pixelsPerTile)),
        Math.min(image.height, Math.ceil((maxZ + 2) * pixelsPerTile)));
}
