// Shared resources and scale; neither scene's geometry or saved tile format lives here.
const maps = id => ({ color: `/static/materials/terrain/${id}/color.jpg`,
    normal: `/static/materials/terrain/${id}/normal.jpg`, arm: `/static/materials/terrain/${id}/arm.jpg` });
export const TERRAIN_MATERIALS = Object.freeze([
    { terrain: 'rock', asset: 'rock_face', ...maps('rock_face'), normalStrength: 0.65,
        albedo: { saturation: 0, gain: 1.35, lift: 0.09 } },
    { terrain: 'sand', asset: 'dense_sand', ...maps('dense_sand'), repeat: 6,
        normalStrength: 0.35, variation: 0.06, warp: 0.18,
        albedo: { saturation: 0.5, gain: 1.02, lift: 0.12, tint: [1, 0.99, 0.98] } },
    { terrain: 'swamp', asset: 'mud_forest', ...maps('mud_forest'), normalStrength: 0.55,
        albedo: { saturation: 0.3, gain: 1.1, lift: 0.2, tint: [0.88, 1, 0.86] } },
    { terrain: 'concrete', asset: 'concrete_floor_worn_001', ...maps('concrete_floor_worn_001'),
        normalStrength: 0.6, albedo: { gain: 1.12, lift: 0.025 } },
    { terrain: 'gravel', asset: 'gravel_stones', ...maps('gravel_stones'), repeat: 2,
        normalStrength: 0.65, albedo: { saturation: 0.2, gain: 1, lift: 0.32 } },
    // Full-resolution snow uses Color RGB / Roughness A and NormalGL. AO=1, metal=0.
    // Keep the saved terrain ID and atlas index; do not duplicate snow in atlas cells.
    { terrain: 'snow', asset: 'snow001', color: '/static/materials/terrain/snow001/color_roughness.png',
        normal: '/static/materials/terrain/snow001/normal.jpg', roughnessInColorAlpha: true,
        repeat: 3, normalStrength: 0.22, world: { repeat: 0.03, normalStrength: 0.12 },
        albedo: { saturation: 0.35, gain: 0.88, tint: [0.98, 1, 1.015] } },
    { terrain: 'metal', asset: 'rusty_metal_sheet', color: '/static/materials/art/rusty_metal/color.jpg',
        normal: '/static/materials/art/rusty_metal/normal.jpg', arm: '/static/materials/art/rusty_metal/arm.jpg',
        variation: 0.18 },
    { terrain: null, asset: 'quarry_wall', ...maps('quarry_wall') },
    { terrain: 'bare_dirt', asset: 'sand_03', ...maps('sand_03') },
    { terrain: 'granite_tiles', localOnly: true, asset: 'concrete_pavement_02', ...maps('concrete_pavement_02') },
    { terrain: 'wood_floor', localOnly: true, asset: 'wood_planks_dirt', repeat: 1.5, normalStrength: 0.6,
        albedo: { saturation: 0.45, gain: 1, lift: 0.06, tint: [0.9, 1, 1.08] },
        color: '/static/materials/art/aged_wood/color.jpg', normal: '/static/materials/art/aged_wood/normal.jpg',
        arm: '/static/materials/art/aged_wood/arm.jpg' },
    // Новый материал добавляется в конец, сохраняя индексы существующих покрытий.
    { terrain: 'ice', asset: 'ice001', ...maps('ice001'),
        // Локально полотно 4 м; на глобальной карте рисунок укрупнён до 120 м
        // для читаемости с игровой камеры, без мелкой повторяющейся сетки.
        arm: '/static/materials/terrain/ice001/arm.png', repeat: 4, normalStrength: 0.14,
        world: { repeat: 1.2, normalStrength: 0.02, warp: 0.28 },
        albedo: { saturation: 0.2, gain: 0.32, lift: 0.58, tint: [0.93, 1, 1.04] } },
]);
export const TERRAIN_REPEAT_TILES = 4;
export const TERRAIN_ATLAS_CELL = 512;
export const TERRAIN_ATLAS_GUTTER = 8;
export const TERRAIN_ATLAS_COLUMNS = 4;
// Keep power-of-two dimensions for the existing WebGL1 mipmap path.
export const TERRAIN_ATLAS_ROWS = 2 ** Math.ceil(Math.log2(Math.ceil(TERRAIN_MATERIALS.length / TERRAIN_ATLAS_COLUMNS)));
export const TERRAIN_OPTIONS = Object.freeze([
    ['concrete', 'Промышленный бетон'], ['gravel', 'Гравий'],
    ['snow', 'Снег'], ['ice', 'Лёд'], ['metal', 'Ржавый металл'],
    ['bare_dirt', 'Земля без травы'], ['granite_tiles', 'Гранитная плитка'], ['wood_floor', 'Деревянный пол'],
]);

// Adjust only the runtime color atlas; original Color/Normal/ARM files stay intact.
export function adjustTerrainAlbedo(pixels, entry) {
    if (!entry.albedo) return;
    const { saturation = 1, gain = 1, lift = 0, tint = [1, 1, 1], floor = 0 } = entry.albedo;
    for (let offset = 0; offset < pixels.length; offset += 4) {
        const gray = pixels[offset] * 0.2126 + pixels[offset + 1] * 0.7152 + pixels[offset + 2] * 0.0722;
        for (let channel = 0; channel < 3; channel++) {
            const value = (gray + (pixels[offset + channel] - gray) * saturation) / 255;
            pixels[offset + channel] = Math.round(255 * Math.max(floor,
                Math.min(0.98, value * gain * tint[channel] + lift)));
        }
    }
}

export function terrainMaterialIndex(tile, side = false) {
    const terrain = typeof tile === 'string' ? tile : tile?.terrain;
    if (side && terrain === 'rock') return 8;
    const index = TERRAIN_MATERIALS.findIndex(entry => entry.terrain !== null && entry.terrain === terrain);
    return index < 0 ? 0 : index + 1;
}

// World cells cover 100 x 100 metres. Building floors belong to local editors;
// the full catalog above still renders legacy world tiles without any migration.
export function addTerrainMaterialOptions(select, { world = false } = {}) {
    if (!select) return;
    for (const [value, label] of TERRAIN_OPTIONS) {
        const existing = [...select.options].find(option => option.value === value);
        if (world && TERRAIN_MATERIALS.find(entry => entry.terrain === value)?.localOnly) {
            existing?.remove();
            continue;
        }
        if (existing) existing.textContent = label;
        else {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            select.appendChild(option);
        }
    }
}
