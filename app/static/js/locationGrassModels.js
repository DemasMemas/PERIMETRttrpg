const forms = {
    low: { blades: 25, height: .31, reach: .22, width: .021, slopeWeight: .76, color: [.37, .40, .28] },
    dry: { blades: 19, height: .43, reach: .18, width: .017, slopeWeight: .57, color: [.45, .37, .27] },
    seeded: { blades: 10, height: .29, reach: .17, width: .018, slopeWeight: .38, color: [.43, .43, .28] },
};

function variation(index, salt) {
    const value = Math.sin(index * 71.19 + salt * 37.91) * 43758.5453;
    return value - Math.floor(value);
}

function createTuft(THREE, kind, form, material) {
    const positions = [], colors = [], indices = [];
    const vertex = (x, y, z, color, shade) => {
        const index = positions.length / 3;
        positions.push(x, y, z);
        colors.push(color[0] * shade, color[1] * shade, color[2] * shade);
        return index;
    };
    // Корни распределены по пятну, а направление и рост листьев меняются внутри него.
    const roots = Array.from({ length: 8 }, (_, index) => [
        (variation(index, 1) - .5) * .31, (variation(index, 2) - .5) * .22,
    ]);
    for (let i = 0; i < form.blades; i++) {
        const [x, z] = roots[i % roots.length];
        const angle = variation(i, 3) * Math.PI * 2;
        const dx = Math.cos(angle), dz = Math.sin(angle);
        const reach = form.reach * (.25 + variation(i, 4) * .75);
        const height = form.height * (.32 + variation(i, 5) * .75);
        const width = form.width * (.47 + variation(i, 6) * .69);
        const shade = .75 + variation(i, 7) * .34;
        const bow = .018 + variation(i, 8) * .031;
        const section = (out, y, halfWidth, bend, light) => {
            const cx = x + dx * out - dz * bend;
            const cz = z + dz * out + dx * bend;
            return [vertex(cx - dz * halfWidth, y, cz + dx * halfWidth, form.color, light * .90),
                vertex(cx + dz * halfWidth, y, cz - dx * halfWidth, form.color, light * .96)];
        };
        const base = section(0, 0, width * .22, 0, shade * .62);
        const middle = section(reach * .30, height * .58, width * .5, bow * .22, shade);
        const bend = section(reach * .68, height, width * .27, bow * .60, shade * 1.05);
        const ridge = vertex(x + dx * reach * .30 - dz * bow * .22, height * .58 + width * .23,
            z + dz * reach * .30 + dx * bow * .22, form.color, shade);
        const tipY = height * (.56 + variation(i, 9) * .51);
        const tip = vertex(x + dx * reach - dz * bow, tipY, z + dz * reach + dx * bow, form.color, shade * 1.17);
        indices.push(base[0], middle[0], ridge, base[0], ridge, base[1], base[1], ridge, middle[1],
            middle[0], bend[0], ridge, ridge, bend[0], bend[1], ridge, bend[1], middle[1], bend[0], tip, bend[1]);
    }

    const stalk = (from, to, width, color) => {
        const ring = ([x, y, z], radius, shade) => [
            vertex(x + radius, y, z, color, shade),
            vertex(x - .5 * radius, y, z - .87 * radius, color, shade),
            vertex(x - .5 * radius, y, z + .87 * radius, color, shade),
        ];
        const base = ring(from, width, .82), top = ring(to, width * .62, 1.12);
        for (let i = 0; i < 3; i++) {
            const j = (i + 1) % 3;
            indices.push(base[i], base[j], top[j], base[i], top[j], top[i]);
        }
    };
    const leaf = ([x, y, z], angle, length, halfWidth, lift, color, broad = false) => {
        const dx = Math.cos(angle), dz = Math.sin(angle);
        const midX = x + length * .42 * dx, midZ = z + length * .42 * dz;
        const base = vertex(x, y, z, color, .81);
        const left = vertex(midX - dz * halfWidth, y + lift * .67, midZ + dx * halfWidth, color, .92);
        const ridge = vertex(midX, y + lift * .67 + halfWidth * .45, midZ, color, 1.10);
        const right = vertex(midX + dz * halfWidth, y + lift * .67, midZ - dx * halfWidth, color, 1);
        const tip = vertex(x + dx * length, y + lift, z + dz * length, color, 1.12);
        if (broad) {
            const shoulderX = x + dx * length * .66, shoulderZ = z + dz * length * .66;
            const leftShoulder = vertex(shoulderX - dz * halfWidth * .68, y + lift * .84,
                shoulderZ + dx * halfWidth * .68, color, .98);
            const rightShoulder = vertex(shoulderX + dz * halfWidth * .60, y + lift * .88,
                shoulderZ - dx * halfWidth * .60, color, 1.04);
            indices.push(base, left, ridge, left, leftShoulder, ridge, leftShoulder, tip, ridge,
                ridge, tip, rightShoulder, ridge, rightShoulder, right, base, ridge, right);
        } else indices.push(base, left, ridge, base, ridge, right, left, tip, ridge, ridge, tip, right);
    };
    if (kind !== 'low') {
        const stems = kind === 'dry' ? 4 : 5;
        const stemColor = kind === 'dry' ? [.40, .33, .25] : [.34, .34, .25];
        const leafColor = kind === 'dry' ? [.49, .40, .29] : [.39, .39, .28];
        const seedColor = kind === 'dry' ? [.52, .43, .32] : [.45, .34, .26];
        for (let i = 0; i < stems; i++) {
            const [x, z] = roots[i];
            const angle = variation(i, 11) * Math.PI * 2;
            const dx = Math.cos(angle), dz = Math.sin(angle);
            const height = (kind === 'dry' ? .39 : .55) * (.68 + variation(i, 12) * .49);
            const reach = .043 + variation(i, 13) * .067;
            const point = t => [x + dx * reach * t * t, height * t, z + dz * reach * t * t];
            stalk(point(0), point(.54), .0055, stemColor);
            stalk(point(.54), point(1), .004, stemColor);
            for (let level = 0; level < (kind === 'dry' ? 2 : 5); level++) {
                const t = .26 + level * .102;
                const turn = angle + level * 2.399963;
                const length = (.075 + .087 * variation(i + level * stems, 14)) * (1.05 - t * .47);
                const broad = kind === 'seeded';
                const width = broad ? .012 + variation(i + level * stems, 16) * .010 : .006;
                leaf(point(t), turn, length, width,
                    .025 - .055 * variation(i + level * stems, 15), leafColor, broad);
            }
            for (let seed = 0; seed < 3; seed++) {
                const t = .78 + seed * .064;
                const turn = angle + seed * 2.399963;
                const length = (kind === 'dry' ? .059 : .079) * (1.24 - t);
                leaf(point(t), turn, length, .004 + .004 * variation(i + seed * stems, 17),
                    .016 + .016 * variation(i + seed * stems, 18), seedColor);
            }
        }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    return { geometry, material, scale: 1, spread: 1, bottom: geometry.boundingBox.min.y,
        slopeWeight: form.slopeWeight };
}

export function createNaturalGrassAssets(THREE) {
    const material = new THREE.MeshStandardMaterial({ vertexColors: true,
        side: THREE.DoubleSide, roughness: .96 });
    return Object.fromEntries(Object.entries(forms).map(([kind, form]) =>
        [kind, createTuft(THREE, kind, form, material)]));
}
