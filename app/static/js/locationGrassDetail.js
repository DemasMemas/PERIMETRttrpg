function hash(x, z, salt = 0) {
    let value = Math.imul(x + 137 + salt, 374761393) ^ Math.imul(z + 211, 668265263);
    value = Math.imul(value ^ (value >>> 13), 1274126177);
    return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

function patchNoise(x, z, scale, salt) {
    const px = x * scale;
    const pz = z * scale;
    const ix = Math.floor(px);
    const iz = Math.floor(pz);
    const fx = px - ix;
    const fz = pz - iz;
    const sx = fx * fx * (3 - 2 * fx);
    const sz = fz * fz * (3 - 2 * fz);
    const top = hash(ix, iz, salt) * (1 - sx) + hash(ix + 1, iz, salt) * sx;
    const bottom = hash(ix, iz + 1, salt) * (1 - sx) + hash(ix + 1, iz + 1, salt) * sx;
    return top * (1 - sz) + bottom * sz;
}

function grassDensity(x, z) {
    const patch = patchNoise(x, z, 0.42, 811) * 0.72
        + patchNoise(x, z, 1.35, 812) * 0.28;
    const t = Math.max(0, Math.min(1, (patch - 0.35) / 0.3));
    return 0.07 + 0.88 * t * t * (3 - 2 * t);
}

function detailTerrainWeight(tiles, kind, px, pz) {
    const cx = px - 0.5;
    const cz = pz - 0.5;
    const ix = Math.floor(cx);
    const iz = Math.floor(cz);
    const smooth = value => {
        const t = Math.max(0, Math.min(1, (value - 0.28) / 0.44));
        return t * t * (3 - 2 * t);
    };
    const wx = smooth(cx - ix);
    const wz = smooth(cz - iz);
    const at = (x, z) => Number(tiles[z]?.[x]?.terrain === kind);
    return at(ix, iz) * (1 - wx) * (1 - wz)
        + at(ix + 1, iz) * wx * (1 - wz)
        + at(ix, iz + 1) * (1 - wx) * wz
        + at(ix + 1, iz + 1) * wx * wz;
}

export function acceptsDetail(tiles, kind, px, pz, salt) {
    const surface = tiles[Math.floor(pz)]?.[Math.floor(px)]?.terrain;
    if (!surface || surface === 'water') return false;
    if(kind==='grass'&&['concrete','granite_tiles','metal','wood_floor'].includes(surface))return false;
    return hash(Math.floor(px * 1024), Math.floor(pz * 1024), salt)
        < detailTerrainWeight(tiles, kind, px, pz);
}

export function edgePosition(tile, local, salt) {
    const fringe = 0.04 + 0.24 * hash(Math.floor(tile * 17), Math.floor(local * 997), salt);
    if (local < 0.25) return tile + local - fringe * (1 - local / 0.25);
    if (local > 0.75) return tile + local + fringe * (local - 0.75) / 0.25;
    return tile + local;
}

export function grassPlacements(tiles, isCovered = () => false, includeGrass = true,
    includeFlowers = true) {
    const tufts = [];
    const flowers = [];
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            if (tiles[z][x].terrain !== 'grass') continue;
            const understory = includeGrass === 'understory';
            for (let cell = 0; includeGrass && cell < (understory ? 20 : 25); cell++) {
                const localX = hash(x, z, 40 + cell);
                const localZ = hash(x, z, 80 + cell);
                const px = edgePosition(x, localX, 610 + z * 25 + cell);
                const pz = edgePosition(z, localZ, 710 + x * 25 + cell);
                if (hash(x, z, 120 + cell) > grassDensity(px, pz)
                    * (understory ? (tiles[z][x].grass_subtype === 'forest' ? 0.45 : 0.9) : 1)) continue;
                if (understory && tiles[Math.floor(pz)]?.[Math.floor(px)]?.terrain !== 'grass') continue;
                if (!isCovered(px, pz) && acceptsDetail(tiles, 'grass', px, pz, 180 + cell)) tufts.push({ x: px, z: pz,
                    size: understory ? 0.52 + hash(x, z, 220 + cell) * 0.2
                        : 0.58 + hash(x, z, 220 + cell) * 0.24,
                    width: 0.78 + hash(x, z, 260 + cell) * 0.5,
                    height: understory ? 0.78 + hash(x, z, 300 + cell) * 0.27
                        : 0.8 + hash(x, z, 300 + cell) * 0.38,
                    lean: (hash(x, z, 340 + cell) - 0.5) * 0.34,
                    turn: hash(x, z, 380 + cell) * Math.PI * 2,
                    color: hash(x, z, 420 + cell), understory });
            }
            if (includeFlowers && hash(x, z, 7) < 0.07) {
                const centerX = x + 0.25 + hash(x, z, 8) * 0.5;
                const centerZ = z + 0.25 + hash(x, z, 9) * 0.5;
                for (let flower = 0; flower < 3; flower++) {
                    const px = centerX + (hash(x, z, 130 + flower) - 0.5) * 0.22;
                    const pz = centerZ + (hash(x, z, 140 + flower) - 0.5) * 0.22;
                    if (!isCovered(px, pz) && acceptsDetail(tiles, 'grass', px, pz, 190 + flower)) flowers.push({ x: px, z: pz,
                        size: 0.55 + hash(x, z, 150 + flower) * 0.28,
                        turn: hash(x, z, 160 + flower) * Math.PI * 2 });
                }
            }
        }
    }
    return { tufts, flowers };
}

export function groundPlacements(tiles, isCovered = () => false) {
    const pebbles = [];
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            if (tiles[z][x].terrain !== 'sand') continue;
            const density = hash(x, z, 190);
            const count = density < 0.48 ? 0 : density < 0.85 ? 1 : 2;
            for (let index = 0; index < count; index++) {
                const localX = 0.1 + hash(x, z, 210 + index) * 0.8;
                const localZ = 0.1 + hash(x, z, 230 + index) * 0.8;
                const px = edgePosition(x, localX, 810 + z * 2 + index);
                const pz = edgePosition(z, localZ, 910 + x * 2 + index);
                if (!isCovered(px, pz) && acceptsDetail(tiles, 'sand', px, pz, 300 + index)) pebbles.push({ x: px, z: pz,
                    size: hash(x, z, 250 + index),
                    turn: hash(x, z, 270 + index) * Math.PI * 2 });
            }
        }
    }
    return { pebbles };
}

export function settlePebbles(pebbles, getHeight) {
    return pebbles.flatMap(item => {
        const radius = 0.07 + item.size * 0.09;
        const heights = [getHeight(item.x, item.z),
            getHeight(item.x - radius, item.z), getHeight(item.x + radius, item.z),
            getHeight(item.x, item.z - radius), getHeight(item.x, item.z + radius)];
        const groundHeight = Math.min(...heights);
        return Math.max(...heights) - groundHeight < 0.08 ? [{ ...item, groundHeight }] : [];
    });
}

export function swampPlacements(tiles, isCovered = () => false, naturalDetail = null) {
    const pools = [];
    const reeds = [];
    const candidates = [];
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            if (tiles[z][x].terrain !== 'swamp' || !(naturalDetail ?? tiles[z][x].natural_detail ?? true)) continue;
            if (hash(x, z, 310) < 0.23) {
                const px = x + 0.32 + hash(x, z, 320) * 0.36;
                const pz = z + 0.32 + hash(x, z, 330) * 0.36;
                if (!isCovered(px, pz)) {
                    candidates.push({ x: px, z: pz,
                        radius: 0.18 + hash(x, z, 340) * 0.08,
                        turn: hash(x, z, 350) * Math.PI * 2,
                        seed: hash(x, z, 360), priority: hash(x, z, 370) });
                }
            }
        }
    }
    // Три центра с размахом меньше 3 м по обеим осям помещаются в один квадрат 3×3 м.
    // Проверяем соседние ячейки, чтобы ограничение работало и на границе сетки.
    const accepted = new Map();
    candidates.sort((a, b) => a.priority - b.priority || a.z - b.z || a.x - b.x);
    for (const candidate of candidates) {
        const nearby = [];
        const cx = Math.floor(candidate.x);
        const cz = Math.floor(candidate.z);
        for (let z = cz - 3; z <= cz + 3; z++) {
            for (let x = cx - 3; x <= cx + 3; x++) {
                for (const pool of accepted.get(`${x},${z}`) || []) {
                    if (Math.abs(pool.x - candidate.x) < 3
                        && Math.abs(pool.z - candidate.z) < 3) nearby.push(pool);
                }
            }
        }
        let crowded = false;
        for (let a = 0; a < nearby.length && !crowded; a++) {
            for (let b = a + 1; b < nearby.length; b++) {
                if (Math.max(candidate.x, nearby[a].x, nearby[b].x)
                        - Math.min(candidate.x, nearby[a].x, nearby[b].x) < 3
                    && Math.max(candidate.z, nearby[a].z, nearby[b].z)
                        - Math.min(candidate.z, nearby[a].z, nearby[b].z) < 3) {
                    crowded = true;
                    break;
                }
            }
        }
        if (crowded) continue;
        const { priority, ...pool } = candidate;
        pools.push(pool);
        const key = `${cx},${cz}`;
        if (!accepted.has(key)) accepted.set(key, []);
        accepted.get(key).push(pool);
    }
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            if (tiles[z][x].terrain !== 'swamp' || !(naturalDetail ?? tiles[z][x].natural_detail ?? true)) continue;
            const pool = accepted.get(`${x},${z}`)?.[0] || null;
            const density = patchNoise(x, z, .36, 373);
            const count = density > .59 ? 2 : density > .39 ? 1 : 0;
            for (let index = 0; index < count; index++) {
                const localX = 0.08 + hash(x, z, 380 + index) * 0.84;
                const localZ = 0.08 + hash(x, z, 400 + index) * 0.84;
                const px = edgePosition(x, localX, 1010 + z * 5 + index);
                const pz = edgePosition(z, localZ, 1110 + x * 5 + index);
                if (!isCovered(px, pz) && acceptsDetail(tiles, 'swamp', px, pz, 470 + index)
                    && (!pool || Math.hypot(px - pool.x, pz - pool.z) > pool.radius + 0.04)) {
                    reeds.push({ x: px, z: pz,
                        size: 0.48 + hash(x, z, 420 + index) * 0.36,
                        turn: hash(x, z, 440 + index) * Math.PI * 2,
                        color: hash(x, z, 460 + index) });
                }
            }
        }
    }
    return { pools, reeds };
}

export function swampPoolEdge(pool, side) {
    const angle = side * Math.PI / 6;
    const wobble = 0.78 + hash(Math.floor(pool.x * 100), Math.floor(pool.z * 100), side) * 0.38;
    const localX = Math.cos(angle) * pool.radius * wobble;
    const localZ = Math.sin(angle) * pool.radius * 0.72 * wobble;
    const cosine = Math.cos(pool.turn);
    const sine = Math.sin(pool.turn);
    return [pool.x + localX * cosine - localZ * sine,
        pool.z + localX * sine + localZ * cosine];
}

const SWAMP_POOL_MAX_RELIEF = .06;

function joinedSwampPoolRim(a, b) {
    const angle = Math.atan2(b.z - a.z, b.x - a.x);
    const dx = Math.cos(angle), dz = Math.sin(angle);
    const point = (x, z, along, across) => [x + dx * along - dz * across, z + dz * along + dx * across];
    const arc = (pool, start) => Array.from({ length: 7 }, (_, i) => {
        const theta = start + i * Math.PI / 6;
        const radius = pool.radius * (.88 + .17 * hash(Math.floor(pool.x * 100), Math.floor(pool.z * 100), i));
        return point(pool.x, pool.z, Math.cos(theta) * radius, Math.sin(theta) * radius * .85);
    });
    const between = (t, side) => point(a.x + (b.x - a.x) * t, a.z + (b.z - a.z) * t, 0,
        side * (a.radius * (1 - t) + b.radius * t) * (.73 + .1 * a.seed));
    return [...arc(b, -Math.PI / 2), between(.58, 1), ...arc(a, Math.PI / 2), between(.40, -1)];
}

export function settleSwampPools(pools, tiles, getHeight, isCovered) {
    const settle = (x, z, rim, seed, extraSamples = []) => {
        const inner = rim.map(([px, pz]) => [x + (px - x) * .73, z + (pz - z) * .73]);
        const samples = [[x, z], ...rim, ...inner, ...extraSamples];
        if (samples.some(([px, pz]) => tiles[Math.floor(pz)]?.[Math.floor(px)]?.terrain !== 'swamp'
            || isCovered(px, pz))) return null;
        const heights = samples.map(([px, pz]) => getHeight(px, pz));
        const high = Math.max(...heights);
        if (high - Math.min(...heights) > SWAMP_POOL_MAX_RELIEF) return null;
        return { x, z, rim, inner, seed, level: high + .014 };
    };
    const byTile = new Map();
    for (const pool of pools) {
        const rim = Array.from({ length: 12 }, (_, side) => swampPoolEdge(pool, side));
        const surface = settle(pool.x, pool.z, rim, pool.seed);
        if (surface) byTile.set(`${Math.floor(pool.x)},${Math.floor(pool.z)}`, { pool, surface });
    }
    const surfaces = [];
    // Три кандидата, связанные по сторонам, попали бы под лимит на квадрат 3×3 м.
    for (const { pool: a, surface } of byTile.values()) {
        const x = Math.floor(a.x), z = Math.floor(a.z);
        byTile.delete(`${x},${z}`);
        let joined = null;
        for (const [dx, dz] of [[1, 0], [0, 1], [-1, 0], [0, -1]]) {
            const key = `${x + dx},${z + dz}`;
            const neighbor = byTile.get(key);
            if (!neighbor || Math.abs(surface.level - neighbor.surface.level) > SWAMP_POOL_MAX_RELIEF) continue;
            const { pool: b } = neighbor;
            const rim = joinedSwampPoolRim(a, b);
            // Проверяем грунт и перекрытия под перемычкой, включая саму границу клеток.
            const centerline = Array.from({ length: 9 }, (_, i) => {
                const t = i / 8;
                return [a.x + (b.x - a.x) * t, a.z + (b.z - a.z) * t];
            });
            if (centerline.some(([px, pz]) => isCovered(px, pz, .8 * Math.max(a.radius, b.radius)))) continue;
            joined = settle((a.x + b.x) / 2, (a.z + b.z) / 2, rim, (a.seed + b.seed) / 2,
                [...centerline, ...rim.map(([px, pz], i) => {
                    const [nx, nz] = rim[(i + 1) % rim.length];
                    return [(px + nx) / 2, (pz + nz) / 2];
                })]);
            if (!joined) continue;
            byTile.delete(key);
            break;
        }
        surfaces.push(joined || surface);
    }
    return surfaces;
}

function makeGrassGeometry(THREE) {
    const positions = [];
    const blades = [
        [0.17, 0.032, 0.19, 0.095],
        [2.31, 0.041, 0.25, 0.065],
        [4.56, 0.028, 0.21, 0.11],
    ];
    for (const [angle, width, height, reach] of blades) {
        const dx = Math.cos(angle);
        const dz = Math.sin(angle);
        const sideX = -dz * width;
        const sideZ = dx * width;
        positions.push(-sideX, 0, -sideZ, sideX, 0, sideZ,
            dx * reach, height, dz * reach);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    return geometry;
}

function makeLowCoverGeometry(THREE) {
    const positions = [];
    const colors = [];
    const blades = [
        [0.22, 0.032, 0.18, 0.11, 0.025],
        [1.43, 0.028, 0.22, 0.13, -0.017],
        [2.67, 0.035, 0.16, 0.09, 0.022],
        [4.02, 0.030, 0.20, 0.12, -0.020],
        [5.27, 0.025, 0.14, 0.10, 0.030],
    ];
    const triangle = (a, b, c, ca, cb, cc) => {
        positions.push(...a, ...b, ...c);
        colors.push(...ca, ...cb, ...cc);
    };
    for (const [angle, width, height, reach, bend] of blades) {
        const dx = Math.cos(angle);
        const dz = Math.sin(angle);
        const ax = -dz;
        const az = dx;
        const baseLeft = [ax * -width, 0, az * -width];
        const baseRight = [ax * width, 0, az * width];
        const midX = dx * reach * 0.55 + ax * bend * 0.5;
        const midZ = dz * reach * 0.55 + az * bend * 0.5;
        const midLeft = [midX - ax * width * 0.52, height * 0.72,
            midZ - az * width * 0.52];
        const midRight = [midX + ax * width * 0.52, height * 0.72,
            midZ + az * width * 0.52];
        const tip = [dx * reach + ax * bend, height, dz * reach + az * bend];
        const root = [0.68, 0.66, 0.57];
        const leaf = [1, 1, 1];
        const end = [0.82, 0.85, 0.75];
        triangle(baseLeft, baseRight, midRight, root, root, leaf);
        triangle(baseLeft, midRight, midLeft, root, leaf, leaf);
        triangle(midLeft, midRight, tip, leaf, leaf, end);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geometry.computeVertexNormals();
    return geometry;
}

function makeFlowerGeometry(THREE) {
    const positions = [-0.008, 0, 0, 0.008, 0, 0, 0, 0.23, 0];
    const colors = [0.22, 0.4, 0.12, 0.22, 0.4, 0.12, 0.22, 0.4, 0.12];
    for (let petal = 0; petal < 5; petal++) {
        const angle = petal * Math.PI * 2 / 5;
        const hue = petal % 2 ? [0.95, 0.87, 0.49] : [0.98, 0.94, 0.72];
        positions.push(0, 0.23, 0,
            Math.cos(angle - 0.35) * 0.052, 0.225, Math.sin(angle - 0.35) * 0.052,
            Math.cos(angle + 0.35) * 0.052, 0.225, Math.sin(angle + 0.35) * 0.052);
        colors.push(...hue, ...hue, ...hue);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geometry.computeVertexNormals();
    return geometry;
}

export function createGrassDetailGroup(THREE, tiles, getHeight, isCovered, useNaturalGrass = false,
    naturalDetail = null, { addModels, createPoolMaterial } = {}) {
    const group = new THREE.Group();
    if (naturalDetail === false) {
        group.userData.detailCounts = { tufts: 0, understory: 0, understoryTriangles: 0,
            flowers: 0, pebbles: 0, pools: 0, reeds: 0 };
        return group;
    }
    const placements = grassPlacements(tiles, isCovered,
        useNaturalGrass ? false : true, !useNaturalGrass);
    const tufts = placements.tufts;
    const { flowers } = placements;
    const { pebbles } = groundPlacements(tiles, isCovered);
    const { pools, reeds } = swampPlacements(tiles, isCovered, naturalDetail);
    const dummy = new THREE.Object3D();
    let tuftTriangles = 0;
    const surfaces = settleSwampPools(pools, tiles, getHeight, isCovered);
    if (surfaces.length) {
        const positions = [];
        const colors = [];
        for (const pool of surfaces) {
            const { rim, level } = pool;
            const center = [pool.x, level, pool.z];
            const inner = pool.inner.map(([x, z]) => [x, level, z]);
            const outer = rim.map(([x,z]) => [x, level, z]);
            const color = (opacity, tone) => [tone, tone, tone, opacity];
            const middleColor = color(.94, .86 + pool.seed*.13);
            const innerColor = color(.79, .93);
            const outerColor = color(0, .93);
            for (let side = 0; side < rim.length; side++) {
                const next = (side+1)%rim.length;
                positions.push(...center, ...inner[next], ...inner[side],
                    ...inner[side], ...inner[next], ...outer[side],
                    ...inner[next], ...outer[next], ...outer[side]);
                colors.push(...middleColor, ...innerColor, ...innerColor,
                    ...innerColor, ...innerColor, ...outerColor, ...innerColor, ...outerColor, ...outerColor);
            }
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 4));
        geometry.computeVertexNormals();
        const material = createPoolMaterial?.() ?? new THREE.MeshStandardMaterial({
            color: 0x665b42, vertexColors: true, side: THREE.DoubleSide,
            transparent: true, depthWrite: false, roughness: .42,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.name = 'location-swamp-pools';
        mesh.texture = material.map;
        mesh.castShadow = false;
        mesh.receiveShadow = false;
        mesh.raycast = () => {};
        group.add(mesh);
    }
    addModels?.(group, 'swamp_reeds_1', reeds, (pose, reed) => {
        pose.position.set(reed.x, getHeight(reed.x, reed.z) + .008, reed.z);
        pose.rotation.set(0, reed.turn, 0);
        pose.scale.setScalar(reed.size);
        return .72 + reed.color*.24;
    });
    if (tufts.length) {
        const mesh = new THREE.InstancedMesh(
            makeGrassGeometry(THREE),
            new THREE.MeshLambertMaterial({ color: 0xffffff,
                side: THREE.DoubleSide }),
            tufts.length);
        tuftTriangles = mesh.geometry.getAttribute('position').count / 3;
        const tuftColor = new THREE.Color();
        for (let index = 0; index < tufts.length; index++) {
            const tuft = tufts[index];
            dummy.position.set(tuft.x, getHeight(tuft.x, tuft.z) + 0.004, tuft.z);
            dummy.rotation.set(tuft.lean || 0, tuft.turn, (tuft.lean || 0) * 0.7);
            dummy.scale.set(tuft.size * (tuft.width || 1),
                tuft.size * (tuft.height || 1), tuft.size * (tuft.width || 1));
            dummy.updateMatrix();
            mesh.setMatrixAt(index, dummy.matrix);
            if (tuft.understory) {
                tuftColor.setHSL(
                    tuft.color < 0.22 ? 0.13 : 0.19 + tuft.color * 0.035,
                    0.22 + tuft.color * 0.07, 0.34 + tuft.color * 0.08);
            } else {
                tuftColor.setHSL(0.17 + tuft.color * 0.12,
                    0.28 + tuft.color * 0.1,
                    0.32 + tuft.color * 0.09);
            }
            mesh.setColorAt(index, tuftColor);
        }
        mesh.instanceMatrix.needsUpdate = true;
        mesh.instanceColor.needsUpdate = true;
        mesh.frustumCulled = false;
        mesh.castShadow = false;
        mesh.receiveShadow = false;
        mesh.raycast = () => {};
        group.add(mesh);
    }
    if (flowers.length) {
        const mesh = new THREE.InstancedMesh(makeFlowerGeometry(THREE),
            new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide }),
            flowers.length);
        for (let index = 0; index < flowers.length; index++) {
            const flower = flowers[index];
            dummy.position.set(flower.x, getHeight(flower.x, flower.z) + 0.004, flower.z);
            dummy.rotation.set(0, flower.turn, 0);
            dummy.scale.setScalar(flower.size);
            dummy.updateMatrix();
            mesh.setMatrixAt(index, dummy.matrix);
        }
        mesh.instanceMatrix.needsUpdate = true;
        mesh.frustumCulled = false;
        mesh.castShadow = false;
        mesh.receiveShadow = false;
        mesh.raycast = () => {};
        group.add(mesh);
    }
    const settledPebbles = settlePebbles(pebbles, getHeight);
    if (settledPebbles.length) {
        const mesh = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(0.5, 0),
            new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true }),
            settledPebbles.length);
        for (let index = 0; index < settledPebbles.length; index++) {
            const item = settledPebbles[index];
            const width = 0.07 + item.size * 0.09;
            const pebbleHeight = 0.045 + item.size * 0.04;
            dummy.position.set(item.x, item.groundHeight + pebbleHeight * 0.2, item.z);
            dummy.rotation.set(0, item.turn, 0);
            dummy.scale.set(width, pebbleHeight, width * (0.62 + item.size * 0.35));
            dummy.updateMatrix();
            mesh.setMatrixAt(index, dummy.matrix);
            mesh.setColorAt(index, new THREE.Color().setHSL(0.11, 0.08, 0.43 + item.size * 0.12));
        }
        mesh.instanceMatrix.needsUpdate = true;
        mesh.instanceColor.needsUpdate = true;
        mesh.frustumCulled = false;
        mesh.castShadow = false;
        mesh.receiveShadow = false;
        mesh.raycast = () => {};
        group.add(mesh);
    }
    const understoryCount = tufts.filter(tuft => tuft.understory).length;
    group.userData.detailCounts = { tufts: tufts.length,
        understory: understoryCount,
        understoryTriangles: understoryCount * tuftTriangles,
        flowers: flowers.length,
        pebbles: settledPebbles.length, pools: surfaces.length, reeds: reeds.length };
    return group;
}
