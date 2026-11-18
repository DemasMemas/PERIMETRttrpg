import {
    Box3, BoxGeometry, Float32BufferAttribute, Group, LatheGeometry, Mesh,
    MeshBasicMaterial, MeshStandardMaterial, TorusGeometry, Vector2, Vector3,
} from 'three';
import { characterEquipmentVisuals, HUMAN_APPEARANCE_ASSET } from './characterAppearance.js';
import { createLooseBeltItem } from './characterBelt.js';
import { mountLocationModel } from './locationModelAssets.js';

const defaultGlow = '#dcf0aa';

export function isGroundItem(object) {
    return object?.type === 'ground_item' || object?.properties?.is_ground_item === true;
}

function itemName(item) {
    return String(item?.name || '').trim().toLowerCase();
}

function glowAppearance(item) {
    if (itemName(item) === 'хис') return { model: 'his_hand', range: 8 };
    if (itemName(item) === 'светящаяся палочка') return { model: 'glowstick_hand', range: 5 };
    return null;
}

function authoredItem(item, glow) {
    if (glow) return { model: 'device_' + glow.model, tilt: 0 };
    const name = itemName(item);
    const light = item.attributes?.portable_light;
    if (light === 'head' || ['налобный фонарик', 'налобный фонарь'].includes(name)) {
        return { model: 'device_headlamp', tilt: 0 };
    }
    if (['hand', 'improved'].includes(light) || ['фонарик', 'улучшенный фонарик'].includes(name)) {
        return { model: 'device_flashlight_hand', tilt: 0 };
    }
    const equipmentSlot = { gas_mask: 'gasMask', helmet: 'helmet', mask: 'mask' }[item.category];
    if (!equipmentSlot) return null;
    const gear = { ...item, model: item.model || item.name };
    const [model] = characterEquipmentVisuals({ [equipmentSlot]: gear });
    return model ? { model, tilt: item.category === 'gas_mask' ? -Math.PI * .42 : 0 } : null;
}

function looseItem(item) {
    const name = itemName(item);
    if (item.category === 'pouch') return 'pouch';
    if (name === 'ргд-5') return 'grenade';
    if (name === 'рация') return 'radio';
    if (name === 'хомут пластиковый') return 'tie';
    if (/^вер[её]вка/.test(name)) return 'rope';
    if (item.attributes?.consumable?.direct?.application_form === 'injectable') return 'syringe_green';
    return null;
}

function makeBundle() {
    const group = new Group();
    group.name = 'ground_loot_bundle';
    const profile = [[.035, -.211], [.075, -.190], [.113, -.158], [.124, -.091], [.119, .077],
        [.112, .150], [.075, .196], [.030, .216]];
    const geometry = new LatheGeometry([[0, -.211], ...profile, [0, .216]].map(p => new Vector2(...p)), 18);
    geometry.rotateZ(Math.PI / 2).scale(1, .58, 1).translate(0, .077, 0);
    const positions = geometry.attributes.position;
    const colors = [];
    for (let i = 0; i < positions.count; i++) {
        const x = positions.getX(i), y = positions.getY(i), z = positions.getZ(i);
        const crease = Math.sin(x * 73 + z * 22) * Math.cos(z * 25);
        positions.setXYZ(i, x + z * .065, Math.max(.004, y + crease * .006), z + x * .071);
        const shade = .89 + .09 * crease + .04 * Math.cos(z * 96 - x * 21);
        colors.push(shade, shade, shade);
    }
    geometry.setAttribute('color', new Float32BufferAttribute(colors, 3));
    geometry.computeVertexNormals();
    const fabric = new Mesh(geometry, new MeshStandardMaterial({
        color: 0x6b6351, roughness: .98, vertexColors: true,
    }));
    group.add(fabric);
    const strap = new MeshStandardMaterial({ color: 0x48483d, roughness: .94 });
    for (const x of [-.095, .085]) {
        const rim = new Mesh(new TorusGeometry(.119, .007, 4, 20), strap);
        rim.rotation.y = Math.PI / 2;
        rim.scale.y = .59;
        rim.position.set(x, .077, x * .071);
        group.add(rim);
    }
    const buckle = new Mesh(new BoxGeometry(.023, .010, .029),
        new MeshStandardMaterial({ color: 0x777163, roughness: .7, metalness: .2 }));
    buckle.position.set(.084, .151, .010);
    group.add(buckle);
    group.rotation.y = -.26;
    return group;
}

export function createGroundItemVisual(object, onMounted) {
    const root = new Group();
    const emitters = root.userData.groundLightEmitters = new Map();
    const contents = Array.isArray(object.properties?.contents) ? object.properties.contents.filter(Boolean) : [];
    const items = contents.map((item, index) => {
        const glow = glowAppearance(item);
        return { item, index, glow, active: Boolean(glow && item.glowState?.remaining_seconds > 0) };
    });
    const singles = items.filter(({ active }) => active);
    const others = items.reduce((count, { item, active }) => count + Math.max(1, Number(item.quantity) || 1) - Number(active), 0);
    if (others === 1 && !singles.length) singles.push(items[0]);
    else if (others > 0 || !items.length) root.add(makeBundle());

    for (const [visualIndex, { item, index, glow, active }] of singles.entries()) {
        const slot = new Group();
        const slotIndex = root.children.length;
        if (slotIndex || singles.length > 1) {
            const angle = Math.PI * 2 * visualIndex / Math.max(1, singles.length);
            slot.position.set(Math.cos(angle) * .29, 0, Math.sin(angle) * .29);
            slot.rotation.y = angle + .7;
        }
        root.add(slot);
        const authored = authoredItem(item, glow);
        if (authored) {
            mountLocationModel(slot, {
                visual: {
                    renderMode: 'glb', modelPath: HUMAN_APPEARANCE_ASSET.visual.modelPath,
                    modelNode: 'gear_' + authored.model,
                    bindPose: true, restTilt: authored.tilt,
                },
            }, {}, model => {
                for (const placeholder of [...slot.children]) {
                    placeholder.removeFromParent();
                    const materials = new Set();
                    placeholder.traverse(child => {
                        child.geometry?.dispose();
                        if (child.material) materials.add(child.material);
                    });
                    materials.forEach(material => material.dispose());
                }
                slot.add(model);
                model.traverse(child => {
                    if (!child.isMesh) return;
                    child.castShadow = !active;
                    child.receiveShadow = true;
                    child.userData.groundLightPart = active;
                    if (!active || child.material.name !== 'device_' + glow.model + '_emitter') return;
                    const color = item.glowState?.color || defaultGlow;
                    child.material.emissive.set(color);
                    emitters.set(item.id ?? index, { mesh: child, range: glow.range, color });
                });
                onMounted?.(model);
            }, () => {
                if (!slot.children.length) slot.add(makeBundle());
            });
            if (!active && !slot.children.length) slot.add(makeBundle());
        } else {
            const kind = looseItem(item);
            if (!kind) slot.add(makeBundle());
            else {
                const model = createLooseBeltItem(kind);
                model.rotation.z = -.31;
                model.rotation.x = -Math.PI / 2;
                model.updateMatrixWorld(true);
                const bounds = new Box3().setFromObject(model);
                model.position.copy(bounds.getCenter(new Vector3())).negate();
                model.position.y = -bounds.min.y;
                slot.add(model);
            }
        }
    }

    // Сохраняем удобное попадание мышью даже по тонкой палочке.
    const hitArea = new Mesh(new BoxGeometry(.58, .12, .58),
        new MeshBasicMaterial({ visible: false }));
    hitArea.position.y = .06;
    hitArea.userData.groundLightPart = true;
    root.add(hitArea);
    return root;
}
