import { AnimationMixer, BackSide, LoopOnce, Vector3 } from 'three';
import { createCharacterFootIK, fitCharacterFeet, restoreCharacterFeet } from './characterFootIK.js';
import { applyCharacterLedgeClimb, createCharacterLedgeClimb, restoreCharacterLedgeClimb } from './characterLedgeClimb.js';
import { applyCharacterBelt } from './characterBelt.js';

export const DEFAULT_CHARACTER_APPEARANCE = Object.freeze({
    top: 'sweater', bottom: 'jeans', hair: 'short', hairColor: 'brown',
    sweaterColor: 'green', jeansColor: 'blue', tacticalColor: 'charcoal', huntingColor: 'olive', expression: 'calm',
    leatherCoatColor: 'black', trenchCoatColor: 'brown',
    beretColor: 'navy', beanieColor: 'gray', capColor: 'olive', panamaColor: 'khaki',
    balaclavaColor: 'charcoal', ghostMaskColor: 'charcoal',
    ushankaColor: 'gray',
    vityazColor: 'olive', udarnikColor: 'green', udarnikMColor: 'black', pervoprohodetsColor: 'green',
    kysa2Color: 'black', gusarColor: 'olive', shz13mColor: 'olive', karbovanetsColor: 'black', volkAColor: 'black',
    respiratorColor: 'black', gp5Color: 'white', gp9vColor: 'black', gp7Color: 'black', sp10Color: 'black',
});

export const GEAR_COLORS = Object.freeze({
    charcoal: ['#4c4d4a', 'Графитовый'], gray: ['#77746d', 'Серый'],
    brown: ['#705a49', 'Коричневый'], olive: ['#6b704c', 'Оливковый'],
    khaki: ['#968666', 'Хаки'], navy: ['#526174', 'Тёмно-синий'],
    burgundy: ['#78515a', 'Бордовый'],
});
const MASK_CLOTH_BASE = '#515556';
const MASK_COLORS = Object.freeze({
    black: ['#242527', 'Чёрный'],
    dark_gray: ['#404447', 'Тёмно-серый'],
    ...GEAR_COLORS,
    charcoal: [MASK_CLOTH_BASE, 'Графитовый'],
    brown: ['#755544', 'Коричневый'],
});
const HELMET_COLORS = Object.freeze({
    green: ['#6f8272', 'Зелёный'], sand: ['#b9a57e', 'Песочный'],
    black: ['#4b4d51', 'Чёрный'], olive: ['#79805a', 'Олива'],
});
const USHANKA_COLORS = Object.freeze({
    gray: ['#ffffff', 'Тёмно-серый'], brown: ['#ffcca0', 'Коричневый'],
});
const GAS_MASK_COLORS = Object.freeze({
    white: ['#e9e3d9', 'Белый'], black: ['#525456', 'Чёрный'],
});

export function gearColorPalette(gear) {
    if (gear === 'headwear_ushanka') return USHANKA_COLORS;
    if (gear.startsWith('helmet_')) return HELMET_COLORS;
    if (gear.startsWith('gasmask_')) return GAS_MASK_COLORS;
    return gear.startsWith('mask_') ? MASK_COLORS : GEAR_COLORS;
}

export const GEAR_COLOR_FIELDS = Object.freeze({
    headwear_beret: ['beretColor', 'Цвет берета'], headwear_beanie: ['beanieColor', 'Цвет шапки'],
    headwear_cap: ['capColor', 'Цвет кепки'], headwear_panama: ['panamaColor', 'Цвет панамы'],
    headwear_ushanka: ['ushankaColor', 'Цвет ушанки', ['ushanka_canvas', 'ushanka_fur']],
    mask_balaclava: ['balaclavaColor', 'Цвет балаклавы'],
    mask_ghost: ['ghostMaskColor', 'Цвет ткани «Призрака»'],
    helmet_vityaz: ['vityazColor', 'Цвет Витязя', 'shell'],
    helmet_udarnik: ['udarnikColor', 'Цвет Ударника', 'shell'],
    helmet_udarnik_m: ['udarnikMColor', 'Цвет Ударника-М', 'shell'],
    helmet_pervoprohodets: ['pervoprohodetsColor', 'Цвет Первопроходца', 'shell'],
    helmet_kysa_2: ['kysa2Color', 'Цвет КыСа-2', 'shell'],
    helmet_gusar: ['gusarColor', 'Цвет Гусара', 'shell'],
    helmet_shz_13m: ['shz13mColor', 'Цвет ШЗ-13м', 'shell'],
    helmet_karbovanets: ['karbovanetsColor', 'Цвет Карбованца', 'shell'],
    helmet_volk_a: ['volkAColor', 'Цвет Волка-А', 'shell'],
    gasmask_respirator: ['respiratorColor', 'Цвет респиратора', 'rubber'],
    gasmask_gp5: ['gp5Color', 'Цвет ГП-5', 'rubber'],
    gasmask_gp9v: ['gp9vColor', 'Цвет ГП-9В', 'rubber'],
    gasmask_gp7: ['gp7Color', 'Цвет ГП-7', 'rubber'],
    gasmask_sp10: ['sp10Color', 'Цвет СП-10', 'rubber'],
});

const HAIR_COLORS = Object.freeze({ black: '#252321', brown: '#483226', blond: '#a88956' });
const SWEATER_COLORS = Object.freeze({ black: '#353637', green: '#626c50', blue: '#4a6178' });
const JEANS_COLORS = Object.freeze({ black: '#34383a', green: '#4b5447', blue: '#354e65' });
const TACTICAL_COLORS = Object.freeze({ charcoal: '#41464a', khaki: '#8b7958' });
const HUNTING_COLORS = Object.freeze({ olive: '#656b42', brown: '#755b45' });
const LEATHER_COAT_COLORS = Object.freeze({ black: '#353332', brown: '#5f4233' });
const TRENCH_COAT_COLORS = Object.freeze({ black: '#38383a', brown: '#776349', navy: '#35445a' });
const FULL_FACE_HELMETS = new Set(['helmet_blockpost', 'helmet_voevoda', 'helmet_kuznets']);
const VISOR_HELMETS = new Set(['helmet_kysa_2', 'helmet_gusar', 'helmet_shz_13m', 'helmet_karbovanets', 'helmet_volk_a']);
const EQUIPMENT_MODELS = {
    gasMask: [
        ['mp50', /(?:^|[^\p{L}\p{N}_])мп[\s\-–‑]*50(?![\p{L}\p{N}_])/iu],
        ['gasmask_respirator', /^(?:противогаз\s+)?респиратор$/iu],
        ['gasmask_gp5', /^(?:противогаз\s+)?гп[\s\-–‑]*5$/iu],
        ['gasmask_gp9v', /^(?:противогаз\s+)?гп[\s\-–‑]*9[вv]$/iu],
        ['gasmask_gp7', /^(?:противогаз\s+)?гп[\s\-–‑]*7$/iu],
        ['gasmask_sp10', /^(?:противогаз\s+)?сп[\s\-–‑]*10$/iu],
    ],
    helmet: [
        ['helmet_68g', /^советский\s+кот[её]лок\s+68г$/iu], ['headwear_beret', /^берет$/iu],
        ['headwear_beanie', /^шапка$/iu], ['headwear_cap', /^кепка$/iu], ['headwear_panama', /^панама$/iu],
        ['headwear_ushanka', /^(?:прочее\s+)?ушанка$/iu],
        ['helmet_vityaz', /^(?:шлем\s+)?витязь\s+4[вbv]76$/iu],
        ['helmet_udarnik', /^(?:шлем\s+)?ударник$/iu],
        ['helmet_udarnik_m', /^(?:шлем\s+)?ударник[\s\-–‑]+м$/iu],
        ['helmet_pervoprohodets', /^(?:шлем\s+)?первопроходец$/iu],
        ['helmet_blockpost', /^(?:шлем\s+)?блокпост$/iu],
        ['helmet_voevoda', /^(?:шлем\s+)?воевода$/iu],
        ['helmet_kuznets', /^(?:шлем\s+)?кузнец$/iu],
        ['helmet_kysa_2', /^(?:шлем\s+)?кыса[\s\-–‑]+2$/iu],
        ['helmet_gusar', /^(?:шлем\s+)?гусар$/iu],
        ['helmet_shz_13m', /^(?:шлем\s+)?шз[\s\-–‑]+13м$/iu],
        ['helmet_karbovanets', /^(?:шлем\s+)?карбованец$/iu],
        ['helmet_volk_a', /^(?:шлем\s+)?волк[\s\-–‑]+а$/iu],
    ],
    mask: [['mask_balaclava', /^балаклава$/iu], ['mask_ghost', /^балаклава\s+призрак$/iu]],
};
const choices = {
    top: ['sweater', 'gorka', 'leather_coat', 'trench_coat'], bottom: ['jeans', 'gorka', 'tactical', 'hunting'], hair: ['short', 'bald'],
    expression: ['calm', 'wounded'], hairColor: Object.keys(HAIR_COLORS),
    sweaterColor: Object.keys(SWEATER_COLORS), jeansColor: Object.keys(JEANS_COLORS),
    tacticalColor: Object.keys(TACTICAL_COLORS), huntingColor: Object.keys(HUNTING_COLORS),
    leatherCoatColor: Object.keys(LEATHER_COAT_COLORS), trenchCoatColor: Object.keys(TRENCH_COAT_COLORS),
    ...Object.fromEntries(Object.entries(GEAR_COLOR_FIELDS).map(([gear, [key]]) => [key, Object.keys(gearColorPalette(gear))])),
};

export const HUMAN_APPEARANCE_DIMENSIONS = Object.freeze({ width: 0.65, depth: 0.52, height: 1.8 });
export const HUMAN_APPEARANCE_ASSET = Object.freeze({
    dimensions: [HUMAN_APPEARANCE_DIMENSIONS.width, HUMAN_APPEARANCE_DIMENSIONS.depth, HUMAN_APPEARANCE_DIMENSIONS.height],
    visual: {
        renderMode: 'glb', modelPath: '/static/models/characters/field_male.glb?v=field-rig-37',
        authoredHeight: HUMAN_APPEARANCE_DIMENSIONS.height,
    },
});

export function normalizeCharacterAppearance(value) {
    const appearance = Object.fromEntries(Object.entries(choices).map(([key, allowed]) => [
        key, allowed.includes(value?.[key]) ? value[key] : DEFAULT_CHARACTER_APPEARANCE[key],
    ]));
    appearance.gear = Array.isArray(value?.gear) ? Object.values(EQUIPMENT_MODELS)
        .map(models => models.find(([model]) => value.gear.includes(model))?.[0]).filter(Boolean) : [];
    if (appearance.gear.includes('headwear_ushanka') && value.gear.includes('headwear_ushanka_star')) {
        appearance.gear.push('headwear_ushanka_star');
    }
    if (value?.belt && typeof value.belt === 'object') {
        const belt = value.belt;
        const count = (value, maximum) => Number.isInteger(value) ? Math.min(maximum, Math.max(0, value)) : 0;
        appearance.belt = Object.fromEntries(Object.entries({
            pouches: 7, ties: 15, tool: 4, syringe: 2, dressing: 2, grenade: 1, detector: 1, radio: 1, artifact: 1,
        }).map(([kind, maximum]) => [kind, count(belt[kind], maximum)]));
        if (belt.rope === true) appearance.belt.rope = true;
        if (belt.armor === true) appearance.belt.armor = true;
        for (const kind of ['holster', 'sheath']) {
            if (['empty', 'filled'].includes(belt[kind])) appearance.belt[kind] = belt[kind];
        }
        if (Array.isArray(belt.headgear)) {
            const known = new Set([...EQUIPMENT_MODELS.helmet, ...EQUIPMENT_MODELS.gasMask].map(([model]) => model));
            appearance.belt.headgear = belt.headgear.filter(model => known.has(model)).slice(0, 2);
        }
        if (Array.isArray(belt.ammo)) {
            appearance.belt.ammo = belt.ammo.filter(kind => ['pistol', 'rifle', 'intermediate', 'shell'].includes(kind)).slice(0, 50);
        }
    }
    return appearance;
}

function equipmentVisual(item, models) {
    for (const name of [item?.attributes?.catalog_name, item?.model, item?.name]) {
        if (typeof name !== 'string') continue;
        const matched = models.find(([, pattern]) => pattern.test(name.trim()));
        if (matched) return matched[0];
    }
    return null;
}

export function isCosmeticMask(item) {
    return Boolean(equipmentVisual(item, EQUIPMENT_MODELS.mask));
}

export function characterEquipmentVisuals(equipment) {
    const gear = Object.entries(EQUIPMENT_MODELS).map(([slot, models]) => {
        const item = equipment?.[slot] || (slot === 'mask' ? equipment?.helmet : null);
        return item?.templateId || item?.model ? equipmentVisual(item, models) : null;
    }).filter(Boolean);
    const modifications = equipment?.helmet?.modifications;
    if (gear.includes('headwear_ushanka') && Array.isArray(modifications)
        && modifications.some(modification => modification?.ruleId === 'armor-g7')) {
        gear.push('headwear_ushanka_star');
    }
    return gear;
}

const poses = ['standing', 'sitting', 'prone'];
const strideLengths = { walk: [1.28, .76], run: [1.76, 1.02], sprint: [1.91, 1.23],
    crouch_walk: [.92, .67], crawl: [.59, .53] };
const locomotionClips = new Set(Object.keys(strideLengths).flatMap(name =>
    [name, name + '_left', name + '_right', name + '_back']));
const gaitByPosture = { sitting: 'crouch_walk', prone: 'crawl' };
const collapsePoses = ['fall_back', 'fall_back_crouch', 'prone_drop', 'prone_back_drop'];
const postureTransitions = {
    standing: { sitting: 'standing_to_crouch', prone: 'standing_to_prone' },
    sitting: { standing: 'crouch_to_standing', prone: 'crouch_to_prone' },
    prone: { sitting: 'prone_to_crouch', standing: 'prone_to_standing' },
};
const transitionTargets = Object.fromEntries(Object.values(postureTransitions).flatMap(transitions =>
    Object.entries(transitions).map(([pose, clip]) => [clip, pose])));
const animatedModels = new WeakMap();
let collapseClips;
let collapseClipRequest;

export function preloadCharacterCollapse() {
    collapseClipRequest ??= import('three/addons/loaders/GLTFLoader.js')
        .then(({ GLTFLoader }) => new GLTFLoader().loadAsync('/static/models/characters/field_fall.glb?v=19'))
        .then(({ animations }) => {
            collapseClips = new Map(animations.map(clip => [clip.name, clip]));
        })
        .catch(error => console.warn('Не удалось загрузить анимации персонажа', error));
    return collapseClipRequest;
}

const deviceGrips = new WeakMap();

function applyDeviceGrip(model, restore = false) {
    const state = deviceGrips.get(model);
    if (!state) return;
    const { joints, aim } = state;
    for (const { bone, grip, animated } of joints) {
        if (restore) bone.quaternion.copy(animated);
        else {
            animated.copy(bone.quaternion);
            bone.quaternion.copy(grip);
        }
    }
    if (!aim) return;
    const { wrist, forearm, wristAnimation, armAnimation } = aim;
    if (restore) {
        wrist.quaternion.copy(wristAnimation);
        forearm.quaternion.copy(armAnimation);
    } else {
        wristAnimation.copy(wrist.quaternion);
        armAnimation.copy(forearm.quaternion);
        if (!collapsePoses.includes(model.userData.characterAppearancePose)) aimHeldFlashlight(model, aim);
    }
}

function aimHeldFlashlight(model, { wrist, forearm, lensAxis, direction, goal, origin, rotation }) {
    wrist.getWorldPosition(direction);
    const wristHeight = direction.y - model.getWorldPosition(origin).y;
    // Когда кисть рядом с землёй, поднимаем фонарь поворотом предплечья.
    const lift = Math.max(0, Math.min(.065, (.32 - wristHeight) * .28));
    if (lift > 0) {
        direction.sub(forearm.getWorldPosition(origin));
        goal.copy(direction).y += lift;
        forearm.parent.getWorldQuaternion(rotation).invert();
        direction.applyQuaternion(rotation).normalize();
        goal.applyQuaternion(rotation).normalize();
        forearm.quaternion.premultiply(rotation.setFromUnitVectors(direction, goal));
    }
    // Ось фонаря на ровной площадке встречает пол примерно в 20 метрах.
    const pitch = -Math.max(.06, wristHeight + lift) / 20;
    goal.set(0, pitch, 1).normalize().applyQuaternion(model.getWorldQuaternion(rotation));
    goal.applyQuaternion(forearm.getWorldQuaternion(rotation).invert());
    direction.copy(lensAxis).applyQuaternion(wrist.quaternion);
    wrist.quaternion.premultiply(rotation.setFromUnitVectors(direction, goal));
}

function setDeviceGrip(model, skeleton, torch, holdsGlow) {
    if (!skeleton) {
        applyDeviceGrip(model, true);
        deviceGrips.delete(model);
        return;
    }
    const previous = deviceGrips.get(model);
    if (previous && Boolean(previous.aim) === torch) {
        previous.holdsGlow = holdsGlow;
        return;
    }
    if (previous) applyDeviceGrip(model, true);
    const joints = skeleton.bones.flatMap((bone, index) => {
        const finger = /^(?:Index|Middle|Ring|Pinky)([123])\.?R$/.exec(bone.name);
        if (!finger) return [];
        const inverse = skeleton.boneInverses[index];
        const parentInverse = skeleton.boneInverses[skeleton.bones.indexOf(bone.parent)];
        const rest = inverse.clone().invert().premultiply(parentInverse);
        const grip = bone.quaternion.clone().setFromRotationMatrix(rest);
        // Ось сгиба проходит поперёк ладони в позе привязки скелета.
        const axis = new Vector3(0, 0, 1).transformDirection(inverse);
        const curl = [.08, .92, .69][Number(finger[1]) - 1];
        grip.multiply(bone.quaternion.clone().setFromAxisAngle(axis, curl));
        return [{ bone, grip, animated: bone.quaternion.clone() }];
    });
    const index = torch ? skeleton.bones.findIndex(bone => /^Wrist\.?R$/.test(bone.name)) : -1;
    const wrist = skeleton.bones[index], forearm = wrist?.parent;
    const aim = forearm ? {
        wrist, forearm,
        wristAnimation: wrist.quaternion.clone(),
        armAnimation: forearm.quaternion.clone(),
        lensAxis: new Vector3(0, 0, 1).transformDirection(skeleton.boneInverses[index]),
        direction: new Vector3(), goal: new Vector3(), origin: new Vector3(), rotation: wrist.quaternion.clone(),
    } : null;
    deviceGrips.set(model, { joints, aim, holdsGlow });
    applyDeviceGrip(model);
}

function playClip(model, state, name, { immediate = false } = {}) {
    const clip = collapseClips?.get(name === 'prone' ? 'prone_idle' : name);
    const action = clip ? state.mixer.clipAction(clip) : state.actions.get(name);
    if (!action) return;
    const previous = state.current;
    if (state.footIK) restoreCharacterFeet(state.footIK);
    if (state.ledgeClimb) restoreCharacterLedgeClimb(state.ledgeClimb);
    state.followUp = transitionTargets[name];
    state.fading?.stop();
    state.fading = null;
    state.easedBlendDuration = 0;
    state.current = action.reset().setEffectiveTimeScale(locomotionClips.has(name) ? 0 : 1).setEffectiveWeight(1).play();
    state.once = Boolean(clip && (state.followUp || collapsePoses.includes(name)));
    if (state.once) {
        action.setLoop(LoopOnce, 1);
        action.clampWhenFinished = true;
    }
    if (state.once && immediate) {
        action.time = action.getClip().duration;
        action.paused = true;
    }
    if (previous && previous !== action) {
        const interrupted = transitionTargets[previous.getClip().name] && !previous.paused;
        const changingGait = locomotionClips.has(name) || locomotionClips.has(previous.getClip().name);
        if (!immediate && (state.once || interrupted || changingGait)) {
            state.blendRemaining = interrupted ? .16 : name === 'fall_back' ? .075 : .10;
            if (interrupted || name === 'fall_back') {
                // Переходим из текущего кадра, чтобы два движения коленей не накладывались.
                state.easedBlendDuration = state.blendRemaining;
                previous.paused = true;
                previous.setEffectiveWeight(1);
                action.setEffectiveWeight(0);
            } else {
                previous.crossFadeTo(action, state.blendRemaining, false);
            }
            state.fading = previous;
        } else previous.stop();
    }
    applyDeviceGrip(model, true);
    state.mixer.update(0);
    applyDeviceGrip(model);
    model.userData.shadowPoseRevision = (model.userData.shadowPoseRevision || 0) + 1;
}

const maskClothTints = new WeakMap();
const balaclavaFaceVisibility = new WeakMap();

function hideCoveredFace(material, covered, gasMask) {
    let visibility = balaclavaFaceVisibility.get(material);
    if (!visibility) {
        visibility = { balaclava: { value: covered }, mask: { value: 0 } };
        balaclavaFaceVisibility.set(material, visibility);
        const previousCompile = material.onBeforeCompile;
        const previousKey = material.customProgramCacheKey?.();
        material.customProgramCacheKey = () => `${previousKey || ''}:balaclava-face-coverage`;
        material.onBeforeCompile = (shader, renderer) => {
            previousCompile?.call(material, shader, renderer);
            shader.uniforms.balaclavaCoversFace = visibility.balaclava;
            shader.uniforms.gasMaskFaceCoverage = visibility.mask;
            shader.vertexShader = shader.vertexShader
                .replace('#include <common>', '#include <common>\nvarying vec3 vBalaclavaFacePosition;')
                .replace('#include <begin_vertex>', '#include <begin_vertex>\nvBalaclavaFacePosition = position;');
            // Границы в позе привязки лежат под тканью, вне прорези для глаз.
            shader.fragmentShader = shader.fragmentShader
                .replace('#include <common>', '#include <common>\nvarying vec3 vBalaclavaFacePosition;\nuniform bool balaclavaCoversFace;\nuniform int gasMaskFaceCoverage;')
                .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
                    vec3 skin = vBalaclavaFacePosition;
                    if (balaclavaCoversFace) {
                        bool lowerFace = skin.y > 1.525 && skin.y < 1.673;
                        bool ear = abs(skin.x) > 0.083 && skin.z < 0.10 && skin.y > 1.647 && skin.y < 1.747;
                        if (lowerFace || ear) discard;
                    }
                    if (gasMaskFaceCoverage == 2) {
                        bool belowMask = skin.y < 1.565;
                        bool behindLens = skin.z > 0.128 && skin.y > 1.666 && skin.y < 1.735;
                        if (!belowMask && !behindLens) discard;
                    } else if (gasMaskFaceCoverage == 1) {
                        float chinWidth = mix(0.039, 0.071, smoothstep(1.571, 1.602, skin.y));
                        if (skin.y > 1.565 && skin.y < 1.639 && skin.z > 0.104 && abs(skin.x) < chinWidth) discard;
                    }`);
        };
        material.needsUpdate = true;
    }
    visibility.balaclava.value = covered;
    visibility.mask.value = gasMask === 'gasmask_gp5' ? 2 : gasMask ? 1 : 0;
}

function tintMaskCloth(mesh, material, color) {
    let tint = maskClothTints.get(material);
    if (!tint) {
        const geometry = mesh.geometry;
        const weights = geometry.getAttribute('uv1') || geometry.getAttribute('uv2');
        if (!weights) return;
        if (!geometry.hasAttribute('maskClothWeight')) geometry.setAttribute('maskClothWeight', weights);
        const previousCompile = material.onBeforeCompile;
        const previousKey = material.customProgramCacheKey();
        tint = { value: material.color.clone() };
        maskClothTints.set(material, tint);
        tint.base = material.color.clone().set(MASK_CLOTH_BASE);
        material.customProgramCacheKey = () => `${previousKey}:mask-cloth`;
        material.onBeforeCompile = (shader, renderer) => {
            previousCompile?.call(material, shader, renderer);
            shader.uniforms.maskClothTint = tint;
            shader.vertexShader = shader.vertexShader
                .replace('#include <color_pars_vertex>', '#include <color_pars_vertex>\nattribute vec2 maskClothWeight;\nuniform vec3 maskClothTint;')
                .replace('#include <color_vertex>', '#include <color_vertex>\nvColor.rgb += maskClothTint * maskClothWeight.x;');
        };
        material.needsUpdate = true;
    }
    // Цвет обеих тканей меняется до освещения. У рисунка вес ткани равен нулю.
    tint.value.set(color);
    tint.value.r -= tint.base.r;
    tint.value.g -= tint.base.g;
    tint.value.b -= tint.base.b;
}

export function applyCharacterAppearanceModel(model, value, pose = 'standing', { immediate = false, devices = {} } = {}) {
    const appearance = normalizeCharacterAppearance(value);
    const activePose = poses.includes(pose) || collapsePoses.includes(pose) ? pose : 'standing';
    const previousPose = model.userData.characterAppearancePose;
    const signature = JSON.stringify([appearance, devices]);
    if (model.userData.characterAppearanceSignature !== signature) {
        const parts = new Set(['base', 'top_' + appearance.top, 'bottom_' + appearance.bottom, 'face_' + appearance.expression]);
        for (const device of Object.keys(devices)) {
            if (!appearance.belt || !device.endsWith('_belt')) parts.add('gear_device_' + device);
        }
        const headwear = appearance.gear.find(gear => gear.startsWith('headwear_'));
        const fullFace = appearance.gear.some(gear => FULL_FACE_HELMETS.has(gear));
        const gasMask = !fullFace && !appearance.gear.some(gear => VISOR_HELMETS.has(gear))
            ? appearance.gear.find(gear => gear === 'mp50' || gear.startsWith('gasmask_')) : null;
        const hasHelmet = appearance.gear.some(gear => gear.startsWith('helmet_'));
        const wearsPervoprohodets = appearance.gear.includes('helmet_pervoprohodets');
        const hasMaskLenses = gasMask && gasMask !== 'gasmask_respirator';
        const wearsBalaclava = !fullFace && gasMask !== 'gasmask_gp5' && appearance.gear.some(gear => gear.startsWith('mask_'));
        const coversHair = wearsBalaclava || gasMask === 'gasmask_gp5'
            || hasHelmet || headwear === 'headwear_ushanka';
        const showHair = appearance.hair === 'short' && !coversHair && headwear !== 'headwear_beanie';
        if (showHair) parts.add('hair_short');
        if (gasMask === 'mp50') parts.add('gear_mp50_ear_seals');
        for (const gear of appearance.gear) {
            if (((gear === 'mp50' || gear.startsWith('gasmask_')) && gear !== gasMask)
                || (gear.startsWith('mask_') && !wearsBalaclava)) continue;
            parts.add('gear_' + gear);
        }
        const palette = {
            hair_color: HAIR_COLORS[appearance.hairColor],
            sweater_fabric: SWEATER_COLORS[appearance.sweaterColor],
            jeans_fabric: JEANS_COLORS[appearance.jeansColor],
            tactical_fabric: TACTICAL_COLORS[appearance.tacticalColor],
            hunting_fabric: HUNTING_COLORS[appearance.huntingColor],
            coat_leather: LEATHER_COAT_COLORS[appearance.leatherCoatColor],
            trench_fabric: TRENCH_COAT_COLORS[appearance.trenchCoatColor],
            ...Object.fromEntries(Object.entries(GEAR_COLOR_FIELDS).flatMap(([gear, [key, , surface = 'fabric']]) => {
                const surfaces = Array.isArray(surface) ? surface : [gear + '_' + surface];
                return surfaces.map(material => [material, gearColorPalette(gear)[appearance[key]][0]]);
            })),
        };
        const tinted = new Set();
        let handSkeleton;
        model.traverse(child => {
            if (!child.isSkinnedMesh) return;
            // GLTFLoader помещает детали с несколькими материалами в группу с именем детали.
            const parent = child.parent;
            const part = parent?.isGroup && /^(?:top|gear|face)_/.test(parent.name) ? parent.name : child.name;
            child.visible = parts.has(part);
            if (part.startsWith('gear_device_')) {
                const device = part.slice('gear_device_'.length);
                child.userData.deviceShadow ??= child.castShadow;
                child.castShadow = child.userData.deviceShadow && !devices[device];
            }
            if (child.visible && part.startsWith('gear_device_') && part.endsWith('_hand')) handSkeleton = child.skeleton;
            const strap = child.morphTargetDictionary?.bare_head_strap;
            if (strap !== undefined) child.morphTargetInfluences[strap] = showHair && !headwear ? 0 : 1;
            const maskBrowFit = child.morphTargetDictionary?.under_headwear;
            const helmetBrowFit = child.morphTargetDictionary?.under_pervoprohodets;
            const useHelmetFit = helmetBrowFit !== undefined && wearsPervoprohodets;
            if (maskBrowFit !== undefined) child.morphTargetInfluences[maskBrowFit] = (headwear || hasHelmet) && !useHelmetFit ? 1 : 0;
            if (helmetBrowFit !== undefined) child.morphTargetInfluences[helmetBrowFit] = useHelmetFit ? 1 : 0;
            const headwearFit = child.morphTargetDictionary?.over_gas_mask;
            if (headwearFit !== undefined) child.morphTargetInfluences[headwearFit] = hasMaskLenses ? 1 : 0;
            const gp5Fit = child.morphTargetDictionary?.over_gp5;
            if (gp5Fit !== undefined) child.morphTargetInfluences[gp5Fit] = gasMask === 'gasmask_gp5' ? 1 : 0;
            const maskFit = child.morphTargetDictionary?.under_mp50;
            if (part.startsWith('gear_mask_') && maskFit !== undefined) child.morphTargetInfluences[maskFit] = gasMask === 'mp50' ? 1 : 0;
            const faceFit = child.morphTargetDictionary?.under_balaclava;
            if (part.startsWith('face_') && faceFit !== undefined) child.morphTargetInfluences[faceFit] = wearsBalaclava ? 1 : 0;
            const hairFit = child.morphTargetDictionary?.soft_headwear_fit;
            if (part === 'hair_short' && hairFit !== undefined) child.morphTargetInfluences[hairFit] = headwear ? 1 : 0;
            child.userData.staticIdleShadow = true;
            // Берцы входят в base. Тень от изнанки убирает полосы на лицевой стороне одежды.
            const isOutfitPart = part === 'base' || part.startsWith('top_') || part.startsWith('bottom_');
            const materials = Array.isArray(child.material) ? child.material : [child.material];
            if (materials.every(material => material?.name === 'field_ear')) {
                child.visible = child.visible && !hasMaskLenses && !fullFace && !wearsBalaclava;
            }
            for (const material of materials) {
                if (material?.emissive && material.name.startsWith('device_') && material.name.endsWith('_emitter')) {
                    const device = material.name.slice('device_'.length, -'_emitter'.length);
                    material.emissive.set(devices[device] || '#000000');
                }
                if (material?.name === 'field_face') hideCoveredFace(material, wearsBalaclava, gasMask);
                if (material && isOutfitPart) {
                    material.shadowSide = BackSide;
                }
                const color = palette[material?.name];
                if (!color || tinted.has(material)) continue;
                if (part.startsWith('gear_mask_')) {
                    tintMaskCloth(child, material, color);
                } else {
                    material.color.set(color).convertSRGBToLinear();
                    material.userData.darkBaseColor?.copy(material.color);
                }
                tinted.add(material);
            }
        });
        if (appearance.belt || model.userData.characterBeltBuilt) {
            applyCharacterBelt(model, appearance, devices);
            model.userData.characterBeltBuilt = Boolean(appearance.belt);
        }
        setDeviceGrip(model, handSkeleton, parts.has('gear_device_flashlight_hand'),
            Boolean(devices.glowstick_hand || devices.his_hand));
        model.userData.shadowPoseRevision = (model.userData.shadowPoseRevision || 0) + 1;
        model.userData.characterAppearanceSignature = signature;
    }

    if (previousPose === activePose) return;
    model.userData.characterAppearancePose = activePose;
    if (!model.animations?.length) return;
    let state = animatedModels.get(model);
    if (!state) {
        const mixer = new AnimationMixer(model);
        state = { mixer, actions: new Map(model.animations.map(clip => [clip.name, mixer.clipAction(clip)])) };
        animatedModels.set(model, state);
    }
    const playing = state.current?.getClip().name;
    const from = locomotionClips.has(playing) ? previousPose : playing === 'prone_idle' ? 'prone' : playing;
    const transition = immediate ? null : postureTransitions[from]?.[activePose];
    const playPose = () => {
        const name = collapseClips?.has(transition) ? transition : activePose;
        playClip(model, state, name, { immediate });
    };
    state.playPose = playPose;
    if ((transition || activePose === 'prone' || collapsePoses.includes(activePose)) && !collapseClips) {
        if (!state.current) playClip(model, state, 'standing');
        preloadCharacterCollapse().then(() => {
            if (state.playPose !== playPose) return;
            if (!collapseClips && collapsePoses.includes(activePose)) playClip(model, state, 'prone', { immediate });
            else playPose();
        });
    } else playPose();
}

export function animateCharacterAppearanceIdle(model, timeMs, characterId, nearby = true, visible = nearby,
    movement, surface, ledgeClimb) {
    const state = animatedModels.get(model);
    if (!state?.current) return;
    const pose = model.userData.characterAppearancePose;
    const playing = state.current.getClip().name;
    const position = model.parent?.position;
    const distance = position && state.lastX !== undefined
        ? Math.min(.65, Math.hypot(position.x - state.lastX, position.z - state.lastZ)) : 0;
    state.lastX = position?.x;
    state.lastZ = position?.z;
    let stride = 0;
    if (!state.once) {
        const base = nearby && movement && (pose === 'standing' ? movement.mode : gaitByPosture[pose]);
        const direction = movement?.direction || 'forward';
        const gait = base && (direction === 'forward' ? base : base + '_' + direction);
        if (gait && collapseClips?.has(gait)) {
            stride = strideLengths[base][direction === 'forward' ? 0 : 1];
            if (direction === 'left' || direction === 'right') stride *= base === 'crawl' ? .78 : .65;
            if (playing !== gait) {
                const phase = locomotionClips.has(playing) ? state.current.time / state.current.getClip().duration : 0;
                playClip(model, state, gait);
                state.current.time = phase * state.current.getClip().duration;
            }
        } else if (locomotionClips.has(playing)) {
            playClip(model, state, pose, { immediate: !nearby });
        }
    }
    const lastTime = state.lastTime;
    state.lastTime = timeMs;
    if (state.footIK) restoreCharacterFeet(state.footIK);
    if (state.ledgeClimb) restoreCharacterLedgeClimb(state.ledgeClimb);
    if (state.once ? state.current.paused : !nearby && !state.fading) {
        if (state.footIK) fitCharacterFeet(model, state.footIK, null, false);
        return;
    }
    const delta = lastTime === undefined ? 0 : Math.max(0, (timeMs - lastTime) / 1000);
    let step = Math.min(delta, .1);
    if (state.once) {
        const remaining = state.current.getClip().duration - state.current.time;
        step = visible ? delta : remaining + .001;
    } else if (stride) {
        const duration = state.current.getClip().duration;
        state.current.time = (state.current.time + distance * duration / stride) % duration;
    } else if (lastTime === undefined) {
        state.current.time = characterId * 1.379 % state.current.getClip().duration;
    }
    // Сохраняем кадр покоя, чтобы кисть, трубка и её тень стояли вместе.
    if (!state.once && !state.fading && !stride && !ledgeClimb && deviceGrips.get(model)?.holdsGlow) step = 0;
    if (state.fading && state.easedBlendDuration) {
        const remainingBlend = Math.max(0, state.blendRemaining - step) / state.easedBlendDuration;
        const idleWeight = remainingBlend ** 2;
        state.fading.setEffectiveWeight(idleWeight);
        state.current.setEffectiveWeight(1 - idleWeight);
    }
    const blending = Boolean(state.fading);
    applyDeviceGrip(model, true);
    state.mixer.update(step);
    if (state.fading && (state.blendRemaining -= step) <= 0) {
        state.fading.stop();
        state.fading = null;
    }
    const fitSurface = !state.once && (pose === 'standing' || pose === 'sitting') ? surface : null;
    if ((fitSurface || ledgeClimb) && state.footIK === undefined) state.footIK = createCharacterFootIK(model);
    if (ledgeClimb && pose === 'standing' && !state.once) {
        if (state.ledgeClimb === undefined) state.ledgeClimb = createCharacterLedgeClimb(model, state.footIK);
        applyCharacterLedgeClimb(model, state.ledgeClimb, ledgeClimb);
    }
    applyDeviceGrip(model);
    const feetAdjusted = Boolean(state.footIK && fitCharacterFeet(model, state.footIK, fitSurface, stride > 0 && !blending));
    const supportChanged = feetAdjusted !== Boolean(state.feetAdjusted) || feetAdjusted && fitSurface !== state.footSurface;
    if (state.once && state.current.paused && state.followUp) playClip(model, state, state.followUp);
    else if (state.once || blending || stride || supportChanged || ledgeClimb || state.lastLedgeClimb) {
        model.userData.shadowPoseRevision = (model.userData.shadowPoseRevision || 0) + 1;
    }
    state.feetAdjusted = feetAdjusted;
    state.footSurface = fitSurface;
    state.lastLedgeClimb = ledgeClimb;
}
