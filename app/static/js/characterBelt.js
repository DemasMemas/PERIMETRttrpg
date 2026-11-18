import {
    Box3, BoxGeometry, BufferGeometry, CatmullRomCurve3, CylinderGeometry, DoubleSide, Float32BufferAttribute,
    Group, InstancedMesh, LatheGeometry, Matrix4, Mesh, MeshStandardMaterial, Quaternion, TubeGeometry, Vector2, Vector3,
} from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const finish = {
    webbing: new MeshStandardMaterial({ color: 0x585744, roughness: .94, side: DoubleSide }),
    canvas: new MeshStandardMaterial({ color: 0x66664c, roughness: .9 }),
    leather: new MeshStandardMaterial({ color: 0x594938, roughness: .86 }),
    rib: new MeshStandardMaterial({ color: 0x393a32, roughness: .86 }),
    steel: new MeshStandardMaterial({ color: 0x737775, roughness: .54, metalness: .2 }),
    olive: new MeshStandardMaterial({ color: 0x676840, roughness: .79 }),
    brass: new MeshStandardMaterial({ color: 0xa88a56, roughness: .52, metalness: .22 }),
    lead: new MeshStandardMaterial({ color: 0x8a776c, roughness: .64 }),
    shell: new MeshStandardMaterial({ color: 0x763f34, roughness: .74 }),
    pale: new MeshStandardMaterial({ color: 0xafc3bf, roughness: .64 }),
    label: new MeshStandardMaterial({ color: 0xc5c4ac, roughness: .83 }),
    yellow: new MeshStandardMaterial({ color: 0x9b893a, roughness: .78 }),
    green: new MeshStandardMaterial({ color: 0x5f7950, roughness: .78 }),
    rope: new MeshStandardMaterial({ color: 0x878068, roughness: 1 }),
};
const templates = new Map();
const layouts = new WeakMap();
const waist = 1.065;
const beltCenter = .046;
const mountAngles = [1, -1, 3, -3, 5, -5, 7, -7, 9, -9, 10, 2, -2, 4, -4, 6, -6, 8, -8]
    .map(slot => slot * Math.PI / 10);
const mountWidths = {
    pouch: .083, dressing: .067, grenade: .060, detector: .060, radio: .060,
    tool: .043, artifact: .060, rope: .105, syringe_green: .027, syringe_yellow: .027,
    holster_empty: .092, holster_filled: .092, sheath_empty: .052, sheath_filled: .061,
};
const chestSlotsPerRow = 25;
const zeroRotation = new Quaternion();
const oneScale = new Vector3(1, 1, 1);

function ringPath(radiusX, radiusZ, height, segments = 36) {
    return Array.from({ length: segments + 1 }, (_, i) => {
        const angle = i * Math.PI * 2 / segments;
        return new Vector3(Math.sin(angle) * radiusX, height, beltCenter + Math.cos(angle) * radiusZ);
    });
}

function ribbon(points, width, shoulder = false) {
    const geometry = new BufferGeometry();
    const vertices = [], indices = [];
    points.forEach(({ x, y, z }, i) => {
        const j = i * 2;
        if (shoulder) vertices.push(x - width / 2, y, z, x + width / 2, y, z);
        else vertices.push(x, y - width / 2, z, x, y + width / 2, z);
        if (i) indices.push(j - 2, j, j - 1, j - 1, j, j + 1);
    });
    geometry.setAttribute('position', new Float32BufferAttribute(vertices, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    return geometry;
}

function modelParts(kind) {
    if (templates.has(kind)) return templates.get(kind);
    const pieces = new Map();
    const add = (surface, geometry, x = 0, y = 0, z = 0) => {
        geometry.translate(x, y, z);
        const flat = geometry.index ? geometry.toNonIndexed() : geometry;
        if (flat !== geometry) geometry.dispose();
        const group = pieces.get(surface) || [];
        group.push(flat);
        pieces.set(surface, group);
    };
    const box = (surface, x, y, z, w, h, d) => add(surface, new BoxGeometry(w, h, d), x, y, z);
    const cylinder = (surface, x, y, z, top, bottom, length, sides = 10) =>
        add(surface, new CylinderGeometry(top, bottom, length, sides), x, y, z);
    const curve = (surface, points, radius, closed = false, count = 16, sides = 5) => {
        const path = new CatmullRomCurve3(points.map(point => new Vector3(...point)), closed);
        add(surface, new TubeGeometry(path, count, radius, sides, closed));
    };
    const buckle = (x, y, z, width = .065, height = .035) => {
        for (const offset of [-1, 1]) {
            box('steel', x, y + offset * height * .45, z, width, .006, .009);
            box('steel', x + offset * width * .45, y, z, .006, height, .009);
        }
        box('rib', x, y, z + .004, .006, height, .008);
    };
    const sleeve = (surface, width, length, depth, drop = 0) => {
        box(surface, 0, drop - length * .5, .004, width, length, depth);
        box('canvas', 0, drop + .003, -.01, width * .72, .040, .014);
    };
    if (kind === 'buckle') {
        buckle(0, 0, 0);
    } else if (kind === 'headgear_clip') {
        box('webbing', 0, .002, -.003, .038, .055, .017);
        box('steel', 0, -.008, .008, .043, .010, .013);
        curve('leather', [[-.014, -.007, .009], [-.017, -.022, .041], [0, -.064, .063],
            [.017, -.022, .041], [.014, -.007, .009]], .0048);
    } else if (kind === 'pouch' || kind === 'dressing') {
        const compact = kind === 'dressing';
        const w = compact ? .062 : .080, h = compact ? .075 : .112, d = compact ? .039 : .052;
        sleeve('canvas', w, h, d, .016);
        box('webbing', 0, -.006, d * .52, w * 1.04, .040, .013);
        box('rib', 0, -.035, d * .64, .023, .035, .010);
        box('steel', 0, -.024, d * .70, .011, .008, .004);
        for (const side of [-1, 1]) box('webbing', side * w * .44, -.063, d * .51, .007, h * .60, .010);
    } else if (kind.startsWith('sheath')) {
        const width = .049;
        sleeve('leather', width, .157, .027, .012);
        box('rib', 0, .012, .008, width * 1.09, .018, .035);
        box('leather', 0, -.15, .006, .035, .017, .028);
        box('steel', .017, -.118, .023, .005, .005, .004);
        if (kind === 'sheath_filled') {
            box('steel', 0, .026, .008, .064, .008, .026);
            cylinder('rib', 0, .063, .008, .015, .013, .071, 8);
            box('steel', 0, .104, .008, .025, .010, .024);
        }
    } else if (kind.startsWith('holster')) {
        sleeve('leather', .075, .132, .044, .015);
        box('rib', -.010, .013, .012, .083, .016, .050);
        box('leather', .021, -.070, .014, .042, .094, .042);
        box('steel', .026, -.040, .038, .011, .011, .005);
        if (kind === 'holster_filled') {
            box('steel', .002, .039, .012, .063, .038, .028);
            add('rib', new BoxGeometry(.035, .065, .028).rotateZ(-.40), -.036, .048, .013);
        }
    } else if (kind === 'grenade') {
        const body = [[0, -.086], [.020, -.086], [.032, -.065], [.032, -.038], [.019, -.013], [0, -.013]];
        add('olive', new LatheGeometry(body.map(point => new Vector2(...point)), 12));
        cylinder('rib', 0, -.010, 0, .012, .015, .020);
        box('steel', .025, -.024, 0, .008, .062, .019);
        curve('steel', [[-.015, .002, .009], [-.022, .010, .010], [-.030, .001, .011], [-.023, -.008, .010]], .0024, true, 10);
    } else if (kind === 'syringe_green' || kind === 'syringe_yellow') {
        const accent = kind === 'syringe_green' ? 'green' : 'yellow';
        cylinder('pale', 0, -.045, 0, .0105, .0105, .091);
        cylinder(accent, 0, .005, 0, .012, .012, .050);
        cylinder(accent, 0, -.093, 0, .012, .012, .014);
        box(accent, 0, .002, .012, .011, .035, .005);
        box('label', 0, -.042, .010, .013, .035, .003);
        box('rib', 0, -.041, .012, .007, .004, .002);
        box('webbing', 0, -.065, .006, .024, .012, .018);
    } else if (kind === 'detector' || kind === 'radio' || kind === 'tool' || kind === 'artifact') {
        if (kind === 'tool') {
            sleeve('canvas', .039, .115, .030, .018);
            box('steel', 0, .031, .007, .020, .038, .017);
        } else {
            sleeve(kind === 'radio' ? 'steel' : 'olive', .060, .088, .034, .016);
            box('rib', 0, -.006, .024, .048, .022, .008);
            if (kind === 'radio') {
                cylinder('rib', -.014, .043, .004, .003, .004, .064, 8);
                for (let i = 0; i < 3; i++) box('rib', 0, -.032 - .008 * i, .022, .034, .003, .004);
            } else {
                box('pale', 0, -.006, .029, .034, .013, .004);
                box('steel', 0, -.039, .022, .013, .010, .006);
            }
        }
    } else if (kind === 'rope') {
        for (let i = 0; i < 3; i++) {
            const z = i * .011;
            curve('rope', [[-.038, -.008, z], [-.049, -.070, z], [-.018, -.127, z],
                [.029, -.121, z], [.045, -.061, z], [.028, -.016, z]], .007, true, 22);
        }
        box('leather', 0, -.012, .010, .089, .023, .052);
        curve('rope', [[.024, -.052, .034], [.055, -.09, .043], [.038, -.147, .021]], .007);
    } else if (kind === 'tie') {
        curve('rib', [[0, 0, 0], [-.016, -.022, 0], [-.023, -.068, 0],
            [.004, -.082, 0], [.020, -.048, 0], [.008, -.008, 0]], .0018, true, 12, 4);
        box('rib', 0, -.003, 0, .008, .010, .006);
    } else {
        const [radius, total, casing] = {
            pistol: [.0046, .033, .025], rifle: [.0048, .064, .043],
            intermediate: [.0042, .049, .033], shell: [.0063, .053, .011],
        }[kind];
        const shotgun = kind === 'shell';
        cylinder('brass', 0, casing / 2, 0, radius * .96, radius, casing, 8);
        cylinder(shotgun ? 'shell' : 'lead', 0, (total + casing) / 2, 0,
            shotgun ? radius : radius * .31, radius * (shotgun ? 1 : .76), total - casing, 8);
        cylinder('brass', 0, .002, 0, radius * 1.09, radius * 1.09, .004, 8);
        box('webbing', 0, total * .29, .002, radius * 2.35, .015, radius * 2.15);
    }
    const result = [...pieces].map(([surface, geometries]) => {
        const geometry = mergeGeometries(geometries);
        geometry.userData.characterEquipmentShared = true;
        geometries.forEach(part => part.dispose());
        return { geometry, surface };
    });
    templates.set(kind, result);
    return result;
}

export function createLooseBeltItem(kind) {
    const model = new Group();
    for (const { geometry, surface } of modelParts(kind)) model.add(new Mesh(geometry, finish[surface].clone()));
    return model;
}

function modelAnchor(skin, boneName) {
    const { bones, boneInverses } = skin.skeleton;
    const index = bones.findIndex(bone => bone.name === boneName);
    if (index < 0) return null;
    const anchor = new Group();
    anchor.name = boneName === 'Hips' ? 'equipment_belt' : 'equipment_bandolier';
    anchor.matrix.copy(boneInverses[index]).multiply(skin.bindMatrix);
    anchor.matrixAutoUpdate = false;
    bones[index].add(anchor);
    return anchor;
}

function clearLayout(model) {
    for (const group of layouts.get(model) || []) {
        group.removeFromParent();
        group.traverse(part => {
            if (!part.isMesh) return;
            if (!part.geometry.userData.characterEquipmentShared && !part.userData.beltSourcePart) part.geometry.dispose();
            part.material.dispose();
            if (part.isInstancedMesh) part.dispose();
        });
    }
    layouts.delete(model);
}

function attachMeshes(anchor, placements) {
    for (const [kind, matrices] of placements) {
        for (const { geometry, surface } of modelParts(kind)) {
            const material = finish[surface].clone();
            const piece = matrices.length === 1 ? new Mesh(geometry, material)
                : new InstancedMesh(geometry, material, matrices.length);
            piece.name = kind;
            if (piece.isInstancedMesh) matrices.forEach((matrix, i) => piece.setMatrixAt(i, matrix));
            else {
                piece.matrix.copy(matrices[0]);
                piece.matrixAutoUpdate = false;
            }
            piece.castShadow = true;
            piece.receiveShadow = true;
            anchor.add(piece);
        }
    }
}

function addPlacement(placements, kind, position, angle = 0) {
    const matrices = placements.get(kind) || [];
    const rotation = angle ? new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), angle) : zeroRotation;
    matrices.push(new Matrix4().compose(position, rotation, oneScale));
    placements.set(kind, matrices);
}

function attachExisting(model, anchor, name, point, angle) {
    const asset = model.getObjectByName(name);
    if (!asset) return;
    const sources = [];
    const bounds = new Box3();
    asset.traverse(part => {
        if (!part.isSkinnedMesh) return;
        if (!part.geometry.boundingBox) part.geometry.computeBoundingBox();
        bounds.union(part.geometry.boundingBox);
        sources.push(part);
    });
    if (!sources.length) return;
    const offset = bounds.getCenter(new Vector3());
    offset.y = bounds.max.y;
    const hanging = new Group();
    hanging.name = 'belt_' + name;
    hanging.position.copy(point);
    hanging.rotation.y = angle;
    for (const source of sources) {
        const part = new Mesh(source.geometry, source.material.clone());
        part.position.copy(offset).negate();
        part.userData.beltSourcePart = true;
        part.castShadow = source.castShadow;
        part.receiveShadow = true;
        hanging.add(part);
    }
    anchor.add(hanging);
}

export function applyCharacterBelt(model, appearance, devices) {
    clearLayout(model);
    const belt = appearance.belt;
    if (!belt) return;
    const skin = model.getObjectByName('base');
    if (!skin?.skeleton?.boneInverses) return;
    const anchor = modelAnchor(skin, 'Hips');
    if (!anchor) return;
    layouts.set(model, [anchor]);
    const [radiusX, radiusZ] = {
        sweater: [.238, .157], gorka: [.214, .139],
        leather_coat: [.233, .153], trench_coat: [.233, .153],
    }[appearance.top];
    const webbing = new Mesh(ribbon(ringPath(radiusX, radiusZ, waist), .049), finish.webbing.clone());
    webbing.name = 'belt_webbing';
    webbing.castShadow = true;
    webbing.receiveShadow = true;
    anchor.add(webbing);

    const placements = new Map();
    const point = (angle, depth = 0, y = waist) => new Vector3(
        (radiusX + depth) * Math.sin(angle), y, beltCenter + (radiusZ + depth) * Math.cos(angle));
    addPlacement(placements, 'buckle', point(0, .009));
    const candidates = mountAngles.map(angle => ({ angle, position: point(angle, .029) }));
    const occupied = [{ position: point(0, .029), width: .063 }];
    const reserve = width => {
        // Старые настройки могут содержать больше навески, чем помещается по текущим правилам.
        if (!candidates.length) {
            candidates.push(...mountAngles.map(angle => ({ angle, position: point(angle, .052, waist - .108) })));
        }
        let bestIndex = 0, bestClearance = -Infinity;
        for (let i = 0; i < candidates.length; i++) {
            const { position } = candidates[i];
            const clearance = Math.min(...occupied.map(other =>
                position.distanceTo(other.position) - (width + other.width) / 2));
            if (clearance > bestClearance) {
                bestIndex = i;
                bestClearance = clearance;
            }
            if (clearance >= .008) break;
        }
        const [mount] = candidates.splice(bestIndex, 1);
        occupied.push({ position: mount.position, width });
        return mount;
    };
    for (const device of Object.keys(devices).filter(name => name.endsWith('_belt'))) {
        const { angle, position } = reserve(device === 'flashlight_belt' ? .061 : .035);
        attachExisting(model, anchor, 'gear_device_' + device, position.clone().add(new Vector3(0, .022, 0)), angle);
    }
    const items = [
        ...Array(belt.pouches || 0).fill('pouch'),
        belt.holster && 'holster_' + belt.holster, belt.sheath && 'sheath_' + belt.sheath,
        belt.grenade && 'grenade', belt.detector && 'detector', belt.radio && 'radio',
        belt.artifact && 'artifact', belt.rope && 'rope',
        ...Array(belt.tool || 0).fill('tool'), ...Array(belt.dressing || 0).fill('dressing'),
        belt.syringe > 0 && 'syringe_green', belt.syringe > 1 && 'syringe_yellow',
    ].filter(Boolean);
    for (const kind of items) {
        const { position, angle } = reserve(mountWidths[kind]);
        addPlacement(placements, kind, position, angle);
    }
    if (belt.ties) {
        const { position, angle } = reserve(.086);
        for (let i = 0; i < belt.ties; i++) {
            const offset = new Vector3((i % 5 - 2) * .008, -.005 * (i % 3), Math.floor(i / 5) * .006);
            addPlacement(placements, 'tie', position.clone().add(offset), angle + (i % 4) * .10);
        }
    }
    belt.headgear?.forEach((gear, i) => {
        const angle = i ? 2.35 : -2.35;
        addPlacement(placements, 'headgear_clip', point(angle, .006), angle);
        attachExisting(model, anchor, 'gear_' + gear, point(angle, .069, waist - .060), angle);
    });
    attachMeshes(anchor, placements);

    if (belt.ammo) mountBandolier(model, skin, belt.ammo, appearance.top, belt.armor);
}

function mountBandolier(model, skin, ammo, top, overArmor) {
    const anchor = modelAnchor(skin, 'Chest');
    if (!anchor) return;
    layouts.get(model).push(anchor);
    const clearance = overArmor ? .042 : 0;
    const front = (top === 'sweater' ? .226 : top === 'gorka' ? .201 : .211) + clearance;
    const chestPoint = (x, y) => new Vector3(x, y, front - x * x * 1.34);
    const rows = [1.387, 1.312];
    for (const y of rows) {
        const path = Array.from({ length: 13 }, (_, i) => {
            const x = -.165 + .33 * i / 12;
            return chestPoint(x, y + x * .19);
        });
        const strap = new Mesh(ribbon(path, .038), finish.webbing.clone());
        strap.name = 'bandolier_webbing';
        strap.castShadow = true;
        strap.receiveShadow = true;
        anchor.add(strap);
    }
    for (const side of [-1, 1]) {
        const x = side * .151;
        const shoulder = new Mesh(ribbon([
            chestPoint(x, rows[1] + x * .19 - .017),
            chestPoint(x, rows[0] + x * .19 + .013),
            new Vector3(side * .142, 1.467, .127 + clearance),
            new Vector3(side * .133, 1.510, .040),
            new Vector3(side * .141, 1.459, -.078 - clearance),
            new Vector3(side * .172, 1.339, -.102 - clearance),
            new Vector3(side * .213, 1.320, .073),
            chestPoint(x, rows[1] + x * .19),
        ], .025, true), finish.webbing.clone());
        shoulder.name = 'bandolier_shoulder_strap';
        shoulder.castShadow = true;
        shoulder.receiveShadow = true;
        anchor.add(shoulder);
    }
    const placements = new Map();
    ammo.forEach((kind, i) => {
        const x = -.153 + .306 * (i % chestSlotsPerRow) / (chestSlotsPerRow - 1);
        const base = chestPoint(x, rows[Math.floor(i / chestSlotsPerRow)] + x * .19 - .017);
        base.z += .009;
        addPlacement(placements, kind, base, x);
    });
    attachMeshes(anchor, placements);
}
