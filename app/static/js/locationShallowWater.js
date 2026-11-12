import { SHALLOW_WATER_OFFSET } from './locationWaterEdge.js';
import { smoothWaterNormals } from './waterGeometryUtils.js';

const shallowCoverage = depth => 1 - .26 * depth;

export function createLocationWaterGeometry(THREE, deepPositions, tiles, getHeight, deepMargins) {
    const shallowTiles = [];
    const terrain = (x, z) => tiles[z]?.[x]?.terrain;
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            if (terrain(x, z) === 'shallow_water') shallowTiles.push([x, z]);
        }
    }
    if (!deepPositions.length && !shallowTiles.length) return null;

    const geometry = new THREE.BufferGeometry();
    const margins = deepMargins?.slice();
    let positions = deepPositions;
    if (shallowTiles.length) {
        positions = deepPositions.slice();
        const depths = [];
        const seam = new Map();
        const key = (x, z) => x + ',' + z;
        const isShallow = (x, z) => terrain(x, z) === 'shallow_water';
        const isWet = (x, z) => isShallow(x, z) || terrain(x, z) === 'water';
        const wetBorder = (x, z) => {
            const east = Math.floor(x), south = Math.floor(z);
            const west = Number.isInteger(x) ? east - 1 : east;
            const north = Number.isInteger(z) ? south - 1 : south;
            return isWet(west, north) && isWet(east, north) && isWet(west, south) && isWet(east, south);
        };
        const shoalAmount = (x, z) => {
            const tileX = Math.floor(x - .5), tileZ = Math.floor(z - .5);
            const u = x - tileX - .5, v = z - tileZ - .5;
            return Number(isShallow(tileX, tileZ)) * (1 - u) * (1 - v)
                + Number(isShallow(tileX + 1, tileZ)) * u * (1 - v)
                + Number(isShallow(tileX, tileZ + 1)) * (1 - u) * v
                + Number(isShallow(tileX + 1, tileZ + 1)) * u * v;
        };
        for (let i = 0; i < deepPositions.length; i += 3) {
            const [x, y, z] = [deepPositions[i], deepPositions[i + 1], deepPositions[i + 2]];
            const shallow = shoalAmount(x, z);
            if (shallow > 0 && (Number.isInteger(x) || Number.isInteger(z))) {
                const edge = key(x, z);
                seam.set(edge, Math.max(seam.get(edge) ?? -Infinity, y));
            }
            depths.push(shallow, 1);
        }

        // Берег и открытая вода используют один шаг сетки и общие высоты на стыке.
        const steps = 4;
        const inside = value => Math.max(.00001, Math.min(.99999, value));
        for (const [tileX, tileZ] of shallowTiles) {
            const grid = Array.from({ length: steps + 1 }, (_, row) =>
                Array.from({ length: steps + 1 }, (_, col) => {
                    const u = col / steps, v = row / steps;
                    const x = tileX + u, z = tileZ + v;
                    const edgeHeight = seam.get(key(x, z));
                    const height = edgeHeight ?? getHeight(tileX + inside(u), tileZ + inside(v)) + SHALLOW_WATER_OFFSET;
                    const shallow = shoalAmount(x, z);
                    // У глубокой части под водой нет грунтового меша. На самом
                    // стыке совпадает не только высота, но и непрозрачность.
                    const coverage = edgeHeight !== undefined ? 1 : wetBorder(x, z) ? shallowCoverage(shallow) : 0;
                    return { x, z, height, shallow, coverage };
                }));
            for (let row = 0; row < steps; row++) {
                for (let col = 0; col < steps; col++) {
                    const a = grid[row][col], b = grid[row][col + 1];
                    const c = grid[row + 1][col], d = grid[row + 1][col + 1];
                    for (const point of [a, c, b, b, c, d]) {
                        positions.push(point.x, point.height, point.z);
                        depths.push(point.shallow, point.coverage);
                        margins?.push(1);
                    }
                }
            }
        }
        geometry.setAttribute('waterDepth', new THREE.Float32BufferAttribute(depths, 2));
    }
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(smoothWaterNormals(positions), 3));
    if (margins) geometry.setAttribute('waterMargin', new THREE.Float32BufferAttribute(margins, 1));
    return geometry;
}
