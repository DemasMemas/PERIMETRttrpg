// Существующие terrain/visual_asset; внешний вид воды не задаёт её глубину.
export function buildMovementSurfaceCosts(location, footprint, levelOf) {
    const costs = new Map(), objects = [...(location?.objects || [])];
    const vegetation = new Set(['bush', 'tall_grass'].flatMap(species =>
        ['green', 'dry', 'dead'].map(state => 'nature_' + species + '_' + state)));
    const key = (x, y, level) => x + ':' + y + ':' + level;
    (location?.tiles_data || []).forEach((row, y) => {
        if (!Array.isArray(row)) return;
        row.forEach((tile, x) => {
        if (['snow', 'sand', 'swamp'].includes(tile?.terrain)) costs.set(key(x, y, 0), 2);
        for (const object of tile?.objects || []) objects.push({ ...object, tile_x: x, tile_y: y });
        });
    });
    // Пол над грунтом использует свою поверхность, а не снег под ним.
    for (const object of objects.filter(o => o.type === 'floor')) {
        for (const {x, y} of footprint(object)) costs.delete(key(x, y, levelOf(object)));
    }
    for (const object of objects) {
        if (object.type !== 'bush' && !vegetation.has(object.properties?.visual_asset)) continue;
        for (const {x, y} of footprint(object)) costs.set(key(x, y, levelOf(object)), 2);
    }
    return costs;
}

export function ordinaryHeightTransitionAllowed(before, after) {
    return Number.isFinite(before) && Number.isFinite(after) && Math.abs(after - before) < 1;
}
