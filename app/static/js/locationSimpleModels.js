function triangleGeometry(THREE, triangles) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(triangles.flat(), 3));
    geometry.computeVertexNormals();
    return geometry;
}

export function createCorrugatedPanelGeometry(THREE, span, depth) {
    const { start, end, bottom, top } = span;
    const centerX = (start + end) / 2;
    const centerY = (bottom + top) / 2;
    // Размеры трапециевидного профиля в метрах, от впадины до следующей впадины.
    const pitch = 0.1375;
    const folds = [0, 0.0675, 0.085, 0.12, pitch];
    const profileHeight = Math.min(0.018, depth * 0.85);
    const halfThickness = Math.min(0.0015, depth - profileHeight) / 2;
    const xValues = [start];
    for (let period = Math.floor(start / pitch); period * pitch < end; period++) {
        for (const fold of folds.slice(0, -1)) {
            const x = period * pitch + fold;
            if (x > start + 1e-7 && x < end - 1e-7) xValues.push(x);
        }
    }
    xValues.push(end);
    const section = xValues.map(x => {
        const offset = x - Math.floor(x / pitch) * pitch;
        const rise = Math.max(0, Math.min(1,
            (offset - folds[1]) / (folds[2] - folds[1]),
            (pitch - offset) / (pitch - folds[3])));
        const z = (rise - 0.5) * profileHeight;
        return {
            frontBottom: [x, bottom, z + halfThickness],
            frontTop: [x, top, z + halfThickness],
            backBottom: [x, bottom, z - halfThickness],
            backTop: [x, top, z - halfThickness],
        };
    });
    const positions = [], uvs = [], indices = [];
    const quad = (vertices, surface) => {
        const first = positions.length / 3;
        for (const [x, y, z] of vertices) {
            positions.push(x - centerX, y - centerY, z);
            uvs.push((surface === 'end' ? z : x) / 2, (surface === 'rim' ? z : y) / 2);
        }
        indices.push(first, first + 1, first + 2, first, first + 2, first + 3);
    };
    for (let i = 1; i < section.length; i++) {
        const l = section[i - 1], r = section[i];
        quad([l.frontBottom, r.frontBottom, r.frontTop, l.frontTop], 'face');
        quad([r.backBottom, l.backBottom, l.backTop, r.backTop], 'face');
        quad([l.backTop, l.frontTop, r.frontTop, r.backTop], 'rim');
        quad([l.frontBottom, l.backBottom, r.backBottom, r.frontBottom], 'rim');
    }
    const left = section[0], right = section.at(-1);
    quad([left.backBottom, left.frontBottom, left.frontTop, left.backTop], 'end');
    quad([right.frontBottom, right.backBottom, right.backTop, right.frontTop], 'end');
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const uv = new THREE.Float32BufferAttribute(uvs, 2);
    geometry.setAttribute('uv', uv);
    geometry.setAttribute('uv2', uv.clone());
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    return geometry;
}

function rodBetween(THREE, group, from, to, radius, color) {
    const start = new THREE.Vector3(...from);
    const end = new THREE.Vector3(...to);
    const delta = end.clone().sub(start);
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, delta.length(), 6),
        new THREE.MeshStandardMaterial({ color, roughness: 1 }));
    mesh.position.copy(start.add(end).multiplyScalar(0.5));
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.normalize());
    group.add(mesh);
}

export function createTentModel(THREE, color = '#9b9778') {
    const group = new THREE.Group();
    const fabric = new THREE.MeshStandardMaterial({ color, roughness: 1, side: THREE.DoubleSide });
    const endFabric = new THREE.MeshStandardMaterial({ color: '#77745b', roughness: 1, side: THREE.DoubleSide });
    const floor = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.045, 1.24),
        new THREE.MeshStandardMaterial({ color: '#383b31', roughness: 1 }));
    floor.position.y = 0.025;
    group.add(floor);
    for (const sign of [-1, 1]) {
        const z = sign * 0.63;
        const panel = triangleGeometry(THREE, [
            [-0.91, 0.07, z, -0.91, 1.18, 0, 0.91, 1.18, 0],
            [-0.91, 0.07, z, 0.91, 1.18, 0, 0.91, 0.07, z],
        ]);
        group.add(new THREE.Mesh(panel, fabric));
    }
    for (const sign of [-1, 1]) {
        const x = sign * 0.91;
        const end = triangleGeometry(THREE, [[x, 0.07, -0.63, x, 0.07, 0.63, x, 1.18, 0]]);
        group.add(new THREE.Mesh(end, endFabric));
        rodBetween(THREE, group, [x, 0.07, -0.63], [x, 1.18, 0], 0.018, '#423f34');
        rodBetween(THREE, group, [x, 1.18, 0], [x, 0.07, 0.63], 0.018, '#423f34');
    }
    const entrance = triangleGeometry(THREE, [
        [0.915, 0.065, -0.28, 0.915, 0.065, 0.28, 0.915, 0.83, 0],
    ]);
    group.add(new THREE.Mesh(entrance,
        new THREE.MeshStandardMaterial({ color: '#171d1a', roughness: 1, side: THREE.DoubleSide })));
    rodBetween(THREE, group, [-1.02, 1.18, 0], [1.02, 1.18, 0], 0.017, '#514b3b');
    for (const sign of [-1, 1]) {
        rodBetween(THREE, group, [sign * 0.91, 1.14, 0], [sign * 1.18, 0.04, 0.85], 0.008, '#a39a75');
        rodBetween(THREE, group, [sign * 0.91, 1.14, 0], [sign * 1.18, 0.04, -0.85], 0.008, '#a39a75');
    }
    return group;
}

export function createFenceModel(THREE, width = 2, height = 1.2, depth = 0.15,
    color = '#8b7458') {
    const group = new THREE.Group();
    const wood = new THREE.MeshStandardMaterial({ color, roughness: 1 });
    const darkWood = new THREE.MeshStandardMaterial({ color: '#5a4936', roughness: 1 });
    const postWidth = Math.min(0.14, width * 0.12);
    for (const sign of [-1, 1]) {
        const post = new THREE.Mesh(new THREE.BoxGeometry(postWidth, height, Math.max(depth, 0.14)), darkWood);
        post.position.set(sign * (width / 2 - postWidth / 2), height / 2, 0);
        group.add(post);
    }
    for (const fraction of [0.24, 0.53, 0.82]) {
        const rail = new THREE.Mesh(new THREE.BoxGeometry(width - postWidth * 0.7,
            Math.min(0.15, height * 0.13), Math.max(0.055, depth * 0.48)), wood);
        rail.position.set(0, height * fraction, depth * 0.3);
        group.add(rail);
    }
    return group;
}

function boxPart(THREE, group, material, size, position) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material);
    mesh.position.set(...position);
    group.add(mesh);
    return mesh;
}

let crateWoodGrain = null;
function getCrateWoodGrain(THREE) {
    if (crateWoodGrain || !globalThis.document?.createElement || !THREE.CanvasTexture) return crateWoodGrain;
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 128;
    const context = canvas.getContext('2d');
    if (!context) return null;
    const pixels = context.createImageData(128, 128);
    for (let y = 0; y < 128; y++) {
        const band = Math.sin(y * 0.39) * 0.08 + Math.sin(y * 1.43) * 0.035;
        for (let x = 0; x < 128; x++) {
            const grain = Math.sin(y * 0.86 + Math.sin(x * 0.075) * 1.8) * 0.055;
            const knots = Math.sin(x * 0.13 + y * 0.05) * Math.sin(x * 0.025 - y * 0.09) * 0.045;
            const noise = ((x * 73 + y * 151 + x * y * 17) % 23) / 23 * 0.06;
            const shade = Math.round(255 * Math.max(0.48, Math.min(0.98,
                0.79 + band + grain + knots + noise)));
            const offset = (y * 128 + x) * 4;
            pixels.data[offset] = pixels.data[offset + 1] = pixels.data[offset + 2] = shade;
            pixels.data[offset + 3] = 255;
        }
    }
    context.putImageData(pixels, 0, 0);
    crateWoodGrain = new THREE.CanvasTexture(canvas);
    crateWoodGrain.wrapS = crateWoodGrain.wrapT = THREE.RepeatWrapping;
    crateWoodGrain.minFilter = THREE.LinearMipmapLinearFilter;
    return crateWoodGrain;
}

export function createCrateModel(THREE, width, height, depth, color = '#8b6b4f', military = false) {
    const group = new THREE.Group();
    const grain = military ? null : getCrateWoodGrain(THREE);
    const body = new THREE.MeshStandardMaterial({ color, map: grain, roughness: 0.96 });
    const inset = new THREE.MeshStandardMaterial({ color: military ? '#35442f' : '#594530',
        map: grain, roughness: 1 });
    const frame = new THREE.MeshStandardMaterial({ color: military ? '#28352b' : '#4d3826',
        map: grain, roughness: 1 });
    const metal = new THREE.MeshStandardMaterial({ color: '#abb4a9', metalness: 0.65, roughness: 0.43 });
    boxPart(THREE, group, inset, [width, height, depth], [0, height / 2, 0]);
    const rail = Math.min(width, depth, height) * (military ? 0.075 : 0.105);
    for (const side of [-1, 1]) {
        for (const x of [-1, 1]) {
            boxPart(THREE, group, frame, [rail, height, rail],
                [x * (width / 2 - rail / 2), height / 2, side * (depth / 2 - rail / 2)]);
        }
        for (const y of [rail / 2, height - rail / 2]) {
            boxPart(THREE, group, frame, [width, rail, rail], [0, y, side * (depth / 2 - rail / 2)]);
            boxPart(THREE, group, frame, [rail, rail, depth], [side * (width / 2 - rail / 2), y, 0]);
        }
    }
    if (military) {
        boxPart(THREE, group, body, [width + rail * 0.12, rail * 1.2, depth + rail * 0.12],
            [0, height - rail * 0.45, 0]);
        for (const x of [-width * 0.3, width * 0.3]) {
            boxPart(THREE, group, frame, [rail * 0.72, rail * 0.7, depth + rail * 0.2],
                [x, height + rail * 0.08, 0]);
            boxPart(THREE, group, metal, [rail * 0.8, rail * 1.25, rail * 0.24],
                [x, height * 0.72, depth / 2 + rail * 0.12]);
        }
        for (const side of [-1, 1]) {
            boxPart(THREE, group, metal, [rail * 0.25, height * 0.2, depth * 0.25],
                [side * (width / 2 + rail * 0.1), height * 0.52, 0]);
        }
    } else {
        const plankHeight = (height - rail * 2) / 3;
        for (let index = 0; index < 3; index++) {
            const y = rail + plankHeight * (index + 0.5);
            boxPart(THREE, group, body, [width - rail * 2, plankHeight * 0.91, rail * 0.22],
                [0, y, depth / 2 + rail * 0.03]);
            boxPart(THREE, group, body, [rail * 0.22, plankHeight * 0.91, depth - rail * 2],
                [width / 2 + rail * 0.03, y, 0]);
        }
        for (let index = 0; index < 3; index++) {
            boxPart(THREE, group, body, [width - rail * 2, rail * 0.28, (depth - rail * 2) / 3 * 0.92],
                [0, height + rail * 0.08, (index - 1) * (depth - rail * 2) / 3]);
        }
    }
    return group;
}

export function createShelfModel(THREE, width, height, depth, color = '#78634c') {
    const group = new THREE.Group();
    const wood = new THREE.MeshStandardMaterial({ color, roughness: 0.94 });
    const handle = new THREE.MeshStandardMaterial({ color: '#aaa99d', metalness: 0.55, roughness: 0.45 });
    const board = Math.min(0.09, width * 0.08, depth * 0.22);
    boxPart(THREE, group, wood, [width, height, board], [0, height / 2, -depth / 2 + board / 2]);
    for (const side of [-1, 1]) {
        boxPart(THREE, group, wood, [board, height, depth],
            [side * (width / 2 - board / 2), height / 2, 0]);
    }
    for (const fraction of [0, 0.32, 0.62, 0.96]) {
        boxPart(THREE, group, wood, [width, board, depth],
            [0, Math.min(height - board / 2, Math.max(board / 2, height * fraction)), 0]);
    }
    const doorHeight = height * 0.3;
    for (const side of [-1, 1]) {
        boxPart(THREE, group, wood, [width / 2 - board, doorHeight, board * 0.65],
            [side * (width / 4 - board * 0.18), board + doorHeight / 2, depth / 2 + board * 0.12]);
        boxPart(THREE, group, handle, [board * 0.32, board * 0.85, board * 0.32],
            [side * board * 0.95, board + doorHeight * 0.62, depth / 2 + board * 0.55]);
    }
    return group;
}
