const EPSILON = 0.000001;
const NEIGHBORS = [[0, -1], [1, 0], [0, 1], [-1, 0]];

function landTile(tiles, x, z) {
    const tile = tiles[z]?.[x];
    return tile && tile.terrain !== 'water' ? tile : null;
}

function tileHeight(tiles, x, z) {
    const value = tiles[z][x].height;
    const height = value == null ? NaN : Number(value);
    return Number.isFinite(height) ? height : 1;
}

export function terrainFoundationHeight(tiles) {
    return tiles.some(row => row.some(tile => tile.terrain !== 'water'
        && tile.height != null && Number(tile.height) <= 0)) ? -3.05 : 0;
}

function canBlend(tiles, ax, az, bx, bz) {
    return Math.abs(ax - bx) + Math.abs(az - bz) === 1
        && Boolean(landTile(tiles, ax, az) && landTile(tiles, bx, bz));
}

export function isSmoothTerrainTile(tiles, x, z) {
    if (!landTile(tiles, x, z)) return false;
    const height = tileHeight(tiles, x, z);
    for (let dz = -1; dz <= 1; dz++) {
        for (let dx = -1; dx <= 1; dx++) {
            if (landTile(tiles, x + dx, z + dz)
                && Math.abs(height - tileHeight(tiles, x + dx, z + dz)) > EPSILON) return true;
        }
    }
    return false;
}

export function waterSurfaceAdjustments(tiles, affected = null) {
    const width = tiles[0]?.length || 0;
    const seen = new Set();
    const changes = [];
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < width; x++) {
            if (tiles[z][x].terrain !== 'water' || seen.has(z * width + x)) continue;
            const cells = [];
            const shore = [];
            let touchesEdit = false;
            const queue = [[x, z]];
            seen.add(z * width + x);
            for (let index = 0; index < queue.length; index++) {
                const [cx, cz] = queue[index];
                cells.push([cx, cz]);
                if (affected?.has(`${cx},${cz}`)) touchesEdit = true;
                for (const [dx, dz] of NEIGHBORS) {
                    const nx = cx + dx;
                    const nz = cz + dz;
                    if (affected?.has(`${nx},${nz}`)) touchesEdit = true;
                    const neighbor = tiles[nz]?.[nx];
                    if (!neighbor) continue;
                    if (neighbor.terrain !== 'water') {
                        const height = Number(neighbor.height);
                        if (Number.isFinite(height)) shore.push(height);
                    } else if (!seen.has(nz * width + nx)) {
                        seen.add(nz * width + nx);
                        queue.push([nx, nz]);
                    }
                }
            }
            if (affected && !touchesEdit) continue;
            if (!shore.length) {
                shore.push(...cells.map(([cx, cz]) => Number(tiles[cz][cx].height) || 1));
            }
            shore.sort((a, b) => a - b);
            const middle = Math.floor(shore.length / 2);
            const level = Math.round((shore.length % 2 ? shore[middle]
                : (shore[middle - 1] + shore[middle]) / 2) * 100) / 100;
            for (const [cx, cz] of cells) {
                if (Math.abs((Number(tiles[cz][cx].height) || 1) - level) > 0.005) {
                    changes.push({ x: cx, z: cz, height: level });
                }
            }
        }
    }
    return changes;
}

function tileSurfaceRing(tiles, x, z) {
    const height = tileHeight(tiles, x, z);
    if (!isSmoothTerrainTile(tiles, x, z)) return [
        [x, height, z], [x + 0.5, height, z], [x + 1, height, z],
        [x + 1, height, z + 0.5], [x + 1, height, z + 1],
        [x + 0.5, height, z + 1], [x, height, z + 1], [x, height, z + 0.5],
    ];
    const corner = (dx, dz) => {
        const candidates = new Map();
        for (const cz of [z + dz - 1, z + dz]) {
            for (const cx of [x + dx - 1, x + dx]) {
                if (landTile(tiles, cx, cz)) candidates.set(`${cx}:${cz}`, [cx, cz]);
            }
        }
        const connected = new Map([[`${x}:${z}`, [x, z]]]);
        const queue = [[x, z]];
        while (queue.length) {
            const [cx, cz] = queue.shift();
            for (const [nx, nz] of candidates.values()) {
                const key = `${nx}:${nz}`;
                if (!connected.has(key) && canBlend(tiles, cx, cz, nx, nz)) {
                    connected.set(key, [nx, nz]);
                    queue.push([nx, nz]);
                }
            }
        }
        return [...connected.values()].reduce((sum, [cx, cz]) => sum + tileHeight(tiles, cx, cz), 0)
            / connected.size;
    };
    const edge = (nx, nz) => canBlend(tiles, x, z, nx, nz)
        ? (height + tileHeight(tiles, nx, nz)) / 2 : height;
    return [
        [x, corner(0, 0), z],
        [x + 0.5, edge(x, z - 1), z],
        [x + 1, corner(1, 0), z],
        [x + 1, edge(x + 1, z), z + 0.5],
        [x + 1, corner(1, 1), z + 1],
        [x + 0.5, edge(x, z + 1), z + 1],
        [x, corner(0, 1), z + 1],
        [x, edge(x - 1, z), z + 0.5],
    ];
}

export function tileSurfaceTriangles(tiles, x, z, ring = tileSurfaceRing(tiles, x, z)) {
    const height = tileHeight(tiles, x, z);
    const center = [x + 0.5, height, z + 0.5];
    let steep = false;
    for (let dz = -1; dz <= 1 && !steep; dz++) {
        for (let dx = -1; dx <= 1; dx++) {
            if (landTile(tiles, x + dx, z + dz)
                && Math.abs(height - tileHeight(tiles, x + dx, z + dz)) > 1) {
                steep = true;
                break;
            }
        }
    }
    const bands = tiles[z][x]?.terrain === 'sand'
        ? [[0.28, 0.04], [0.48, 0.19], [0.68, 0.47], [0.85, 0.76], [1, 1]]
        : [[0.42, 0.09], [0.76, 0.66], [1, 1]];
    const outline = steep && tiles[z][x]?.terrain === 'sand'
        ? ring.flatMap((point, side) => {
            const next = ring[(side + 1) % ring.length];
            return [point, point.map((value, axis) => (value + next[axis]) / 2)];
        }) : ring;
    const inner = steep ? bands.map(([radius, falloff]) => outline.map(([px, py, pz]) => [
        center[0] + (px - center[0]) * radius,
        height + (py - height) * falloff,
        center[2] + (pz - center[2]) * radius,
    ])) : null;
    const triangles = [];
    for (let side = 0; side < outline.length; side++) {
        const next = (side + 1) % outline.length;
        if (inner) {
            triangles.push([center, inner[0][next], inner[0][side]]);
            for (let band = 0; band < inner.length - 1; band++) {
                const near = inner[band];
                const far = inner[band + 1];
                triangles.push([near[side], near[next], far[next]]);
                triangles.push([near[side], far[next], far[side]]);
            }
        } else {
            triangles.push([center, ring[next], ring[side]]);
        }
    }
    return triangles;
}

// Query the same triangles used by the renderer, including steep terrain bands.
export function terrainSurfaceHeight(tiles, x, z, px, pz, triangles = tileSurfaceTriangles(tiles,x,z)) {
    for (const [a,b,c] of triangles) {
        const denominator=(b[2]-c[2])*(a[0]-c[0])+(c[0]-b[0])*(a[2]-c[2]);
        const u=((b[2]-c[2])*(px-c[0])+(c[0]-b[0])*(pz-c[2]))/denominator;
        const v=((c[2]-a[2])*(px-c[0])+(a[0]-c[0])*(pz-c[2]))/denominator;
        if(u>=-EPSILON&&v>=-EPSILON&&u+v<=1+EPSILON)return u*a[1]+v*b[1]+(1-u-v)*c[1];
    }
    return tileHeight(tiles,x,z);
}

export function createTerrainHeightSampler(tiles,x,z) {
    const triangles=tileSurfaceTriangles(tiles,x,z);
    return (px,pz)=>terrainSurfaceHeight(tiles,x,z,px,pz,triangles);
}

function smoothTopNormals(positions, topByVertex) {
    const normals = new Float32Array(positions.length);
    const shared = new Map();
    for (let offset = 0; offset < positions.length; offset += 9) {
        const ax = positions[offset + 3] - positions[offset];
        const ay = positions[offset + 4] - positions[offset + 1];
        const az = positions[offset + 5] - positions[offset + 2];
        const bx = positions[offset + 6] - positions[offset];
        const by = positions[offset + 7] - positions[offset + 1];
        const bz = positions[offset + 8] - positions[offset + 2];
        const face = [ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx];
        for (let vertex = 0; vertex < 3; vertex++) {
            const index = offset / 3 + vertex;
            if (!topByVertex[index]) {
                const length = Math.hypot(...face) || 1;
                normals.set(face.map(value => value / length), offset + vertex * 3);
                continue;
            }
            const point = offset + vertex * 3;
            const key = `${Math.round(positions[point] * 10000)}:${Math.round(positions[point + 1] * 10000)}:${Math.round(positions[point + 2] * 10000)}`;
            let entry = shared.get(key);
            if (!entry) { entry = { sum: [0, 0, 0], offsets: [] }; shared.set(key, entry); }
            for (let axis = 0; axis < 3; axis++) entry.sum[axis] += face[axis];
            entry.offsets.push(point);
        }
    }
    for (const { sum, offsets } of shared.values()) {
        const length = Math.hypot(...sum) || 1;
        for (const offset of offsets) normals.set(sum.map(value => value / length), offset);
    }
    return normals;
}

export function createTerrainSurfaceHeightSampler(tiles) {
    const cache = new Map();
    return (worldX, worldZ) => {
        const x = Math.floor(worldX);
        const z = Math.floor(worldZ);
        if (!tiles[z]?.[x]) return 1;
        const key = `${x},${z}`;
        let surface = cache.get(key);
        if (!surface) {
            surface = tileSurfaceTriangles(tiles, x, z);
            cache.set(key, surface);
        }
        for (const [center, a, b] of surface) {
            const ax = a[0] - center[0];
            const az = a[2] - center[2];
            const bx = b[0] - center[0];
            const bz = b[2] - center[2];
            const denominator = ax * bz - az * bx;
            if (Math.abs(denominator) < EPSILON) continue;
            const dx = worldX - center[0];
            const dz = worldZ - center[2];
            const alongA = (dx * bz - dz * bx) / denominator;
            const alongB = (ax * dz - az * dx) / denominator;
            if (alongA >= -EPSILON && alongB >= -EPSILON && alongA + alongB <= 1 + EPSILON) {
                return center[1] + alongA * (a[1] - center[1]) + alongB * (b[1] - center[1]);
            }
        }
        return center[1];
    };
}

export function buildTerrainGridData(tiles, includeTile = () => true) {
    const positions = [];
    const tileByVertex = [];
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < (tiles[z]?.length || 0); x++) {
            if (!includeTile(x, z)) continue;
            const ring = tileSurfaceRing(tiles, x, z);
            const segment = (a, b) => {
                for (const index of [a, b]) {
                    const [px, py, pz] = ring[index];
                    positions.push(px, py + 0.025, pz);
                    tileByVertex.push({ x, z });
                }
            };
            segment(0, 1);
            segment(1, 2);
            segment(6, 7);
            segment(7, 0);
            if (!tiles[z][x + 1] || !includeTile(x + 1, z)) {
                segment(2, 3);
                segment(3, 4);
            }
            if (!tiles[z + 1]?.[x] || !includeTile(x, z + 1)) {
                segment(4, 5);
                segment(5, 6);
            }
        }
    }
    return { positions, tileByVertex };
}

export function buildFloorGridData(floors) {
    const positions = [];
    const tileByVertex = [];
    for (const floor of floors) {
        const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => ({
            x: floor.x + a * floor.width / 2 * floor.axis.x + b * floor.depth / 2 * floor.normal.x,
            z: floor.z + a * floor.width / 2 * floor.axis.z + b * floor.depth / 2 * floor.normal.z,
        }));
        const segment = (a, b) => {
            positions.push(a.x, floor.top + 0.025, a.z, b.x, floor.top + 0.025, b.z);
            tileByVertex.push({ ...a, floor: true, floorId: floor.id }, { ...b, floor: true, floorId: floor.id });
        };
        for (const axis of ['x', 'z']) {
            const other = axis === 'x' ? 'z' : 'x';
            const bounds = corners.map(point => point[axis]);
            for (let line = Math.ceil(Math.min(...bounds)); line <= Math.floor(Math.max(...bounds)); line++) {
                const intersections = [];
                for (let side = 0; side < corners.length; side++) {
                    const a = corners[side];
                    const b = corners[(side + 1) % corners.length];
                    const span = b[axis] - a[axis];
                    if (Math.abs(span) < EPSILON) {
                        if (Math.abs(line - a[axis]) < EPSILON) intersections.push(a[other], b[other]);
                        continue;
                    }
                    const t = (line - a[axis]) / span;
                    if (t >= -EPSILON && t <= 1 + EPSILON) {
                        intersections.push(a[other] + t * (b[other] - a[other]));
                    }
                }
                if (intersections.length < 2) continue;
                const start = Math.min(...intersections);
                const end = Math.max(...intersections);
                if (end - start < EPSILON) continue;
                segment({ [axis]: line, [other]: start }, { [axis]: line, [other]: end });
            }
        }
    }
    return { positions, tileByVertex };
}

export function buildFogSurfaceData(tiles, waterSurfaceByTile = []) {
    const positions = [];
    const tileIndices = [];
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            const indices = [];
            const waterSurface = waterSurfaceByTile?.[z * tiles[z].length + x];
            if (waterSurface) {
                for (let index = 0; index < waterSurface.length; index += 3) {
                    indices.push(positions.length / 3);
                    positions.push(waterSurface[index], waterSurface[index + 1] + 0.045,
                        waterSurface[index + 2]);
                }
            } else for (const triangle of tileSurfaceTriangles(tiles, x, z)) {
                for (const [px, py, pz] of triangle) {
                    indices.push(positions.length / 3);
                    positions.push(px, py + 0.045, pz);
                }
            }
            tileIndices.push(indices);
        }
    }
    return { positions, tileIndices };
}

export function buildSmoothTerrainData(tiles, includeTile = () => true, foundationY = 0, bounds = null) {
    const positions = [];
    const tileByFace = [];
    const tileByVertex = [];
    const topByVertex = [];
    const foundationByTile = new Map();

    for (let z = bounds?.minZ ?? 0; z <= (bounds?.maxZ ?? tiles.length - 1); z++) {
        for (let x = bounds?.minX ?? 0; x <= (bounds?.maxX ?? tiles[z].length - 1); x++) {
            if (!includeTile(x, z) || !isSmoothTerrainTile(tiles, x, z)) continue;
            const height = tileHeight(tiles, x, z);
            const ring = tileSurfaceRing(tiles, x, z);
            foundationByTile.set(`${x},${z}`,
                Math.max(foundationY + 0.05, Math.min(height, ...ring.map(point => point[1])) - 0.035));
            const triangle = (a, b, c, top = true) => {
                positions.push(...a, ...b, ...c);
                tileByFace.push({ x, z });
                tileByVertex.push({ x, z }, { x, z }, { x, z });
                topByVertex.push(top ? 1 : 0, top ? 1 : 0, top ? 1 : 0);
            };
            for (const [a, b, c] of tileSurfaceTriangles(tiles, x, z, ring)) triangle(a, b, c);
            for (let side = 0; side < NEIGHBORS.length; side++) {
                const [dx, dz] = NEIGHBORS[side];
                if (canBlend(tiles, x, z, x + dx, z + dz)) continue;
                const a = ring[side * 2];
                const b = ring[(side * 2 + 1) % ring.length];
                const c = ring[(side * 2 + 2) % ring.length];
                const groundA = [a[0], foundationY, a[2]];
                const groundB = [b[0], foundationY, b[2]];
                const groundC = [c[0], foundationY, c[2]];
                triangle(a, groundA, b, false);
                triangle(b, groundA, groundB, false);
                triangle(b, groundB, c, false);
                triangle(c, groundB, groundC, false);
            }
        }
    }
    return { positions, normals: smoothTopNormals(positions, topByVertex),
        tileByFace, tileByVertex, topByVertex, foundationByTile };
}

// Shared cap builder for the merged surface and temporary local sculpt regions.
export function buildTerrainSurfaceData(tiles, smoothData, bounds = { minX: 0, minZ: 0, maxX: tiles[0].length - 1, maxZ: tiles.length - 1 }) {
    const width = tiles[0].length, height = tiles.length;
    const positions = [];
    const normals = [];
    const uvs = [];
    const indices = [];
    const subdivisions = 4;
    for (let z = bounds.minZ; z <= bounds.maxZ; z++) {
        for (let x = bounds.minX; x <= bounds.maxX; x++) {
            if (tiles[z][x].terrain === 'water' || isSmoothTerrainTile(tiles, x, z)) continue;
            const first = positions.length / 3;
            // Non-smooth tiles have a constant surface; slopes are copied below.
            const flatHeight = tileHeight(tiles, x, z);
            for (let row = 0; row <= subdivisions; row++) {
                for (let column = 0; column <= subdivisions; column++) {
                    const px = x + column / subdivisions;
                    const pz = z + row / subdivisions;
                    positions.push(px, flatHeight + 0.002, pz);
                    uvs.push(px / width, pz / height);
                    normals.push(0, 1, 0);
                }
            }
            for (let row = 0; row < subdivisions; row++) {
                for (let column = 0; column < subdivisions; column++) {
                    const a = first + row * (subdivisions + 1) + column;
                    const b = a + 1;
                    const d = a + subdivisions + 1;
                    const c = d + 1;
                    indices.push(a, d, b, b, d, c);
                }
            }
        }
    }
    if (smoothData) {
        for (let face = 0; face < smoothData.tileByFace.length; face++) {
            if (smoothData.topByVertex[face * 3] < 0.5) continue;
            const { x, z } = smoothData.tileByFace[face];
            if (x < bounds.minX || x > bounds.maxX || z < bounds.minZ || z > bounds.maxZ) continue;
            const first = positions.length / 3;
            for (let corner = 0; corner < 3; corner++) {
                const source = (face * 3 + corner) * 3;
                const px = smoothData.positions[source], pz = smoothData.positions[source + 2];
                positions.push(px, smoothData.positions[source + 1] + 0.002, pz);
                normals.push(smoothData.normals[source], smoothData.normals[source + 1], smoothData.normals[source + 2]);
                uvs.push(px / width, pz / height);
            }
            indices.push(first, first + 1, first + 2);
        }
    }
    return { positions, normals, uvs, indices };
}
