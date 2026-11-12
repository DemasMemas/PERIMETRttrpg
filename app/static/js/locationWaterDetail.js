import { waterLevelForTile } from './locationWaterEdge.js';

function hash(x, z, salt) {
    let value = Math.imul(x + 317 + salt, 374761393) ^ Math.imul(z + 97, 668265263);
    value = Math.imul(value ^ (value >>> 13), 1274126177);
    return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

export function waterDetailPlacements(tiles, regions, naturalDetail = null, shallowLevel, compactPools) {
    const film = [];
    const silt = [];
    const reeds = [];
    const branches = [];
    const stones = [];
    const rebar = [];
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            const tile = tiles[z][x];
            const shallow = tile.terrain === 'shallow_water';
            if (!shallow && tile.terrain !== 'water') continue;
            const decorate = naturalDetail ?? tile.natural_detail ?? true;
            const kind = shallow ? 'swamp' : regions.get(`${x},${z}`);
            const point = salt => {
                const margin = kind === 'rock' ? .32 : .20;
                const px = x + margin + hash(x, z, salt) * (1-2*margin);
                const pz = z + margin + hash(x, z, salt + 1) * (1-2*margin);
                return { x: px, z: pz, y: shallow && shallowLevel ? shallowLevel(px, pz) : waterLevelForTile(tile, kind),
                    turn: hash(x, z, salt + 2) * Math.PI * 2,
                    size: kind === 'rock' ? .72 + hash(x, z, salt + 3) * .5 : .64 + hash(x, z, salt + 3) * .46 };
            };
            const bank = shallow || [[1,0],[-1,0],[0,1],[0,-1]].some(([dx,dz]) =>
                [tiles[z+dz]?.[x+dx]?.terrain, tiles[z+dz*2]?.[x+dx*2]?.terrain]
                    .some(type => type && type !== 'water'));
            const peat = shallow || tile.water_profile === 'peat'
                || kind === 'swamp' && (!tile.water_profile || tile.water_profile === 'auto');
            const accumulation = .12 * Math.sin(x * .91 + z * .62) + .09 * Math.cos(z * 1.21 - x * .41);
            if (peat && bank && hash(x, z, 91) < (shallow ? .53 : .23) + accumulation) {
                const compact = compactPools?.has(`${x},${z}`) ?? false;
                const patch = point(93);
                if (compact) patch.size *= .52;
                silt.push({ ...patch, compact, duckweed: decorate });
            }
            if (!decorate) continue;
            if (kind === 'swamp') {
                if (bank && hash(x, z, 12) < (shallow ? .24 : .16)) reeds.push(point(30));
                if (bank && hash(x, z, 13) < (shallow ? .18 : .10)) branches.push(point(40));
            } else if (kind === 'rock') {
                if (hash(x, z, 14) < 0.17) film.push({ ...point(50), kind });
                if (hash(x, z, 15) < 0.13) stones.push(point(60));
                if (hash(x, z, 16) < 0.08) rebar.push(point(70));
            }
        }
    }
    return { film, silt, reeds, branches, stones, rebar };
}

export function createWaterDetailSampler(geometry) {
    const vertices = geometry.getAttribute('position').array;
    const depth = geometry.getAttribute('waterDepth')?.array;
    const byTile = new Map();
    const key = (x, z) => Math.floor(x) + ',' + Math.floor(z);
    for (let i = 0; i < vertices.length; i += 9) {
        const id = key((vertices[i] + vertices[i + 3] + vertices[i + 6]) / 3,
            (vertices[i + 2] + vertices[i + 5] + vertices[i + 8]) / 3);
        if (!byTile.has(id)) byTile.set(id, []);
        byTile.get(id).push(i);
    }
    return (x, z) => {
        for (const i of byTile.get(key(x, z)) || []) {
            const ax = vertices[i], az = vertices[i + 2];
            const ux = vertices[i + 3] - ax, uz = vertices[i + 5] - az;
            const vx = vertices[i + 6] - ax, vz = vertices[i + 8] - az;
            const area = ux * vz - uz * vx;
            if (Math.abs(area) < 1e-8) continue;
            const u = ((x - ax) * vz - (z - az) * vx) / area;
            const v = (ux * (z - az) - uz * (x - ax)) / area;
            const w = 1 - u - v;
            if (Math.min(w, u, v) < -1e-5) continue;
            const j = i / 3 * 2 + 1;
            if (depth && w * depth[j] + u * depth[j + 2] + v * depth[j + 4] < .36) return null;
            return w * vertices[i + 1] + u * vertices[i + 4] + v * vertices[i + 7];
        }
        return null;
    };
}

function addSiltMesh(THREE, group, patches, waterSurface) {
    const positions = [], colors = [], uv = [];
    const mud = new THREE.Color(), rim = new THREE.Color(0x3b3d30), color = new THREE.Color();
    const leaves = [0x71784a, 0x81815a, 0x676b49, 0x8a8862].map(hex => new THREE.Color(hex));
    const mudColors = [0x626345, 0x595b41, 0x656049, 0x5c5e43];
    const append = (vertex, tint) => {
        positions.push(...vertex);
        colors.push(tint.r, tint.g, tint.b);
        uv.push(vertex[0] * 2.9, vertex[2] * 2.9);
    };
    for (const patch of patches) {
        const { x, z, size, turn, duckweed } = patch;
        const sx = Math.round(x * 200), sz = Math.round(z * 200);
        const random = salt => hash(sx, sz, salt);
        const sample = waterSurface || (() => patch.y);
        const base = sample(x, z);
        if (!Number.isFinite(base)) continue;
        const radius = (.42 + random(1) * .24) * size;
        const width = .55 + random(2) * .23;
        const depth = (.017 + random(3) * .031) * (patch.compact ? .52 : 1);
        mud.setHex(mudColors[Math.floor(random(4) * .9999 * mudColors.length)]);
        const sides = 17;
        const rings = [[], [], []], shades = [[], [], []];
        const c = Math.cos(turn), s = Math.sin(turn);
        const footprint = (along, across) => [x + along * c - across * s, z + along * s + across * c];
        for (let i = 0; i < sides; i++) {
            const angle = i / sides * Math.PI * 2;
            const rough = .95 + .16 * Math.sin(3 * angle + turn) + .12 * Math.cos(5 * angle - turn)
                + .19 * (random(30 + i) - .5);
            const along = Math.cos(angle) * radius * rough;
            const across = Math.sin(angle) * radius * rough * width;
            let edgeScale = 1;
            while (edgeScale > .19 && !Number.isFinite(sample(...footprint(along * edgeScale, across * edgeScale)))) {
                edgeScale *= .74;
            }
            for (const [ring, fraction, lift, dark] of [[0, .43, depth, .10],
                [1, .78, depth * .46, .28], [2, 1, -.016, 1]]) {
                const [px, pz] = footprint(along * fraction * edgeScale, across * fraction * edgeScale);
                rings[ring].push([px, (sample(px, pz) ?? base) + lift + (random(60 + ring * sides + i) - .5) * .014, pz]);
                shades[ring].push(mud.clone().lerp(rim, dark).multiplyScalar(.85 + random(140 + ring * sides + i) * .31));
            }
        }
        const center = [x, base + depth * .82, z];
        for (let i = 0; i < sides; i++) {
            const next = (i + 1) % sides;
            append(center, mud);
            append(rings[0][next], shades[0][next]);
            append(rings[0][i], shades[0][i]);
            for (let ring = 0; ring < 2; ring++) {
                for (const [layer, j] of [[ring, i], [ring, next], [ring + 1, i],
                    [ring, next], [ring + 1, next], [ring + 1, i]]) {
                    append(rings[layer][j], shades[layer][j]);
                }
            }
        }
        const flecks = patch.compact ? (duckweed ? 24 : 8) : duckweed ? 77 : 25;
        for (let i = 0; i < flecks; i++) {
            const angle = random(210 + i * 4) * Math.PI * 2;
            const distance = .63 + random(211 + i * 4) * .67;
            const [px, pz] = footprint(radius * Math.cos(angle) * distance,
                radius * width * Math.sin(angle) * distance);
            const level = sample(px, pz);
            if (!Number.isFinite(level)) continue;
            const y = level + .005 + Math.max(0, 1 - distance) * depth * 1.9;
            const r = .012 + random(212 + i * 4) * .023;
            const angleLeaf = random(213 + i * 4) * Math.PI * 2;
            const cos = Math.cos(angleLeaf) * r, sin = Math.sin(angleLeaf) * r;
            color.copy(duckweed ? leaves[i % leaves.length] : mud).multiplyScalar(.80 + random(610 + i) * .35);
            const a = [px - cos, y, pz - sin], b = [px - sin * .65, y, pz + cos * .65];
            const c = [px + cos, y, pz + sin], d = [px + sin * .65, y, pz - cos * .65];
            const peak = [px, y + .006, pz];
            for (const vertex of [a, b, peak, b, c, peak, c, d, peak, d, a, peak]) append(vertex, color);
        }
    }
    if (!positions.length) return;
    const grain = new Uint8Array(128 * 128 * 4);
    for (let z = 0; z < 128; z++) for (let x = 0; x < 128; x++) {
        const i = (z * 128 + x) * 4;
        const value = 182 + hash(x, z, 59) * 47 + hash(x >> 2, z >> 2, 61) * 23;
        grain[i] = grain[i + 1] = grain[i + 2] = value;
        grain[i + 3] = 255;
    }
    const map = new THREE.DataTexture(grain, 128, 128);
    map.wrapS = map.wrapT = THREE.RepeatWrapping;
    map.magFilter = THREE.LinearFilter;
    map.minFilter = THREE.LinearMipmapLinearFilter;
    map.generateMipmaps = true;
    map.colorSpace = THREE.SRGBColorSpace;
    map.needsUpdate = true;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geometry.computeVertexNormals();
    const material = new THREE.MeshStandardMaterial({ map, vertexColors: true, roughness: .92 });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.texture = map;
    mesh.name = 'location-water-silt';
    mesh.raycast = () => {};
    mesh.userData.waterDetailKind = 'silt';
    group.add(mesh);
}

export function createWaterDetailGroup(THREE, tiles, regions, naturalDetail = null,
    { addModels, shallowLevel, surfaceGeometry, compactPools } = {}) {
    const group = new THREE.Group();
    const placements = waterDetailPlacements(tiles, regions, naturalDetail, shallowLevel, compactPools);
    const { film, silt, reeds, branches, stones, rebar } = placements;
    if (silt.length) addSiltMesh(THREE, group, silt, surfaceGeometry && createWaterDetailSampler(surfaceGeometry));
    if (film.length) {
        const positions = [];
        const colors = [];
        const centerColor = new THREE.Color();
        const rimColor = new THREE.Color();
        for (const patch of film) {
            const seedX = Math.floor(patch.x * 100);
            const seedZ = Math.floor(patch.z * 100);
            const islands = 2;
            for (let island = 0; island < islands; island++) {
                const angle = patch.turn + island * 2.39996;
                const spread = island === 0 ? 0 : 0.1 + hash(seedX, seedZ, island + 61) * 0.035;
                const x = patch.x + Math.cos(angle) * spread;
                const z = patch.z + Math.sin(angle) * spread;
                const radius = .09 * patch.size
                    * (0.7 + hash(seedX, seedZ, island + 65) * 0.65);
                const palette = [0x85583c, 0x5e5548, 0x9a6b46];
                centerColor.setHex(palette[(island + Math.floor(patch.turn * 3)) % palette.length]);
                rimColor.setHex(0x354046);
                const rim = Array.from({ length: 9 }, (_, side) => {
                    const direction = angle + side * Math.PI * 2 / 9;
                    const rough = 0.53 + hash(seedX + island * 17, seedZ, side + 73) * 0.86;
                    return [x + Math.cos(direction) * radius * rough,
                        patch.y + 0.012, z + Math.sin(direction) * radius * 0.78 * rough];
                });
                for (let side = 0; side < rim.length; side++) {
                    positions.push(x, patch.y + 0.024 + island * 0.005, z,
                        ...rim[(side + 1) % rim.length], ...rim[side]);
                    colors.push(centerColor.r, centerColor.g, centerColor.b,
                        rimColor.r, rimColor.g, rimColor.b,
                        rimColor.r, rimColor.g, rimColor.b);
                }
            }
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
        geometry.computeVertexNormals();
        const material = new THREE.MeshStandardMaterial({
            vertexColors: true, side: THREE.DoubleSide, roughness: 0.92,
            polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.raycast = () => {};
        mesh.userData.waterDetailKind = 'film';
        group.add(mesh);
    }
    const addInstances = (items, createGeometry, createMaterial, transform, colorOf) => {
        if (!items.length) return;
        const mesh = new THREE.InstancedMesh(createGeometry(), createMaterial(), items.length);
        const dummy = new THREE.Object3D();
        items.forEach((item, index) => {
            dummy.position.set(item.x, item.y, item.z);
            dummy.rotation.set(0, item.turn, 0);
            transform(dummy, item);
            dummy.updateMatrix();
            mesh.setMatrixAt(index, dummy.matrix);
            if (colorOf) mesh.setColorAt(index, new THREE.Color(colorOf(item)));
        });
        mesh.instanceMatrix.needsUpdate = true;
        if (colorOf) mesh.instanceColor.needsUpdate = true;
        mesh.frustumCulled = false;
        mesh.castShadow = false;
        mesh.receiveShadow = false;
        mesh.raycast = () => {};
        group.add(mesh);
    };
    addModels?.(group, 'swamp_reeds_1', reeds, (pose, item) => {
        pose.position.set(item.x, item.y - .065, item.z);
        pose.rotation.set(0, item.turn, 0);
        pose.scale.setScalar(item.size*.76);
        return .92;
    });
    addModels?.(group, 'swamp_dry_branches_medium_01_1', branches, (pose, item) => {
        pose.position.set(item.x, item.y - .015, item.z);
        pose.rotation.set(.13, item.turn, 0);
        pose.scale.setScalar(item.size*.52);
        return .85;
    });
    addInstances(stones, () => new THREE.IcosahedronGeometry(0.5, 0),
        () => new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true }),
        (dummy, item) => {
            dummy.position.y += 0.045;
            dummy.scale.set(0.22 * item.size, 0.19 * item.size, 0.16 * item.size);
        }, () => 0x70726a);
    addInstances(rebar, () => new THREE.CylinderGeometry(0.012, 0.012, 0.47, 5),
        () => new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.88 }),
        (dummy, item) => {
            dummy.position.y += 0.14;
            dummy.rotation.x = 0.28;
            dummy.scale.setScalar(item.size);
        }, () => 0x7b4930);
    group.userData.detailCounts = Object.fromEntries(
        Object.entries(placements).map(([kind, items]) => [kind, items.length]));
    return group;
}
