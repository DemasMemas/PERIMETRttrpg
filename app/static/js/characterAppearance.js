import { AnimationMixer, BackSide, LoopOnce } from 'three';
import { createCharacterFootIK, fitCharacterFeet, restoreCharacterFeet } from './characterFootIK.js';
import { applyCharacterLedgeClimb, createCharacterLedgeClimb, restoreCharacterLedgeClimb } from './characterLedgeClimb.js';

export const DEFAULT_CHARACTER_APPEARANCE = Object.freeze({
    top: 'sweater', bottom: 'jeans', hair: 'short', hairColor: 'brown',
    sweaterColor: 'green', jeansColor: 'blue', tacticalColor: 'charcoal', huntingColor: 'olive', expression: 'calm',
    leatherCoatColor: 'black', trenchCoatColor: 'brown',
});

const HAIR_COLORS = Object.freeze({ black: '#252321', brown: '#483226', blond: '#a88956' });
const SWEATER_COLORS = Object.freeze({ black: '#353637', green: '#626c50', blue: '#4a6178' });
const JEANS_COLORS = Object.freeze({ black: '#34383a', green: '#4b5447', blue: '#354e65' });
const TACTICAL_COLORS = Object.freeze({ charcoal: '#41464a', khaki: '#8b7958' });
const HUNTING_COLORS = Object.freeze({ olive: '#656b42', brown: '#755b45' });
const LEATHER_COAT_COLORS = Object.freeze({ black: '#353332', brown: '#5f4233' });
const TRENCH_COAT_COLORS = Object.freeze({ black: '#38383a', brown: '#776349', navy: '#35445a' });
const choices = {
    top: ['sweater', 'gorka', 'leather_coat', 'trench_coat'], bottom: ['jeans', 'gorka', 'tactical', 'hunting'], hair: ['short', 'bald'],
    expression: ['calm', 'wounded'], hairColor: Object.keys(HAIR_COLORS),
    sweaterColor: Object.keys(SWEATER_COLORS), jeansColor: Object.keys(JEANS_COLORS),
    tacticalColor: Object.keys(TACTICAL_COLORS), huntingColor: Object.keys(HUNTING_COLORS),
    leatherCoatColor: Object.keys(LEATHER_COAT_COLORS), trenchCoatColor: Object.keys(TRENCH_COAT_COLORS),
};

export const HUMAN_APPEARANCE_DIMENSIONS = Object.freeze({ width: 0.65, depth: 0.52, height: 1.8 });
export const HUMAN_APPEARANCE_ASSET = Object.freeze({
    dimensions: [HUMAN_APPEARANCE_DIMENSIONS.width, HUMAN_APPEARANCE_DIMENSIONS.depth, HUMAN_APPEARANCE_DIMENSIONS.height],
    visual: {
        renderMode: 'glb', modelPath: '/static/models/characters/field_male.glb?v=field-rig-18',
        authoredHeight: HUMAN_APPEARANCE_DIMENSIONS.height,
    },
});

export function normalizeCharacterAppearance(value) {
    const appearance = Object.fromEntries(Object.entries(choices).map(([key, allowed]) => [
        key, allowed.includes(value?.[key]) ? value[key] : DEFAULT_CHARACTER_APPEARANCE[key],
    ]));
    appearance.gear = Array.isArray(value?.gear) && value.gear.includes('mp50') ? ['mp50'] : [];
    return appearance;
}

export function characterEquipmentVisuals(equipment) {
    const mask = equipment?.gasMask;
    const mp50 = /(?:^|[^\p{L}\p{N}_])мп[\s\-–‑]*50(?![\p{L}\p{N}_])/iu;
    return (mask?.templateId || mask?.model)
        && [mask.name, mask.model, mask.attributes?.catalog_name].some(name => typeof name === 'string' && mp50.test(name))
        ? ['mp50'] : [];
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
        .then(({ GLTFLoader }) => new GLTFLoader().loadAsync('/static/models/characters/field_fall.glb?v=17'))
        .then(({ animations }) => {
            collapseClips = new Map(animations.map(clip => [clip.name, clip]));
        })
        .catch(error => console.warn('Не удалось загрузить анимации персонажа', error));
    return collapseClipRequest;
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
    state.mixer.update(0);
    model.userData.shadowPoseRevision = (model.userData.shadowPoseRevision || 0) + 1;
}

export function applyCharacterAppearanceModel(model, value, pose = 'standing', { immediate = false } = {}) {
    const appearance = normalizeCharacterAppearance(value);
    const activePose = poses.includes(pose) || collapsePoses.includes(pose) ? pose : 'standing';
    const previousPose = model.userData.characterAppearancePose;
    const signature = JSON.stringify(appearance);
    if (model.userData.characterAppearanceSignature !== signature) {
        const parts = new Set(['base', 'top_' + appearance.top, 'bottom_' + appearance.bottom, 'face_' + appearance.expression]);
        if (appearance.hair === 'short') parts.add('hair_short');
        for (const gear of appearance.gear) parts.add('gear_' + gear);
        const palette = {
            hair_color: HAIR_COLORS[appearance.hairColor],
            sweater_fabric: SWEATER_COLORS[appearance.sweaterColor],
            jeans_fabric: JEANS_COLORS[appearance.jeansColor],
            tactical_fabric: TACTICAL_COLORS[appearance.tacticalColor],
            hunting_fabric: HUNTING_COLORS[appearance.huntingColor],
            coat_leather: LEATHER_COAT_COLORS[appearance.leatherCoatColor],
            trench_fabric: TRENCH_COAT_COLORS[appearance.trenchCoatColor],
        };
        const tinted = new Set();
        model.traverse(child => {
            if (!child.isSkinnedMesh) return;
            // GLTFLoader помещает детали с несколькими материалами в группу с именем детали.
            const part = child.parent?.isGroup && child.parent.name.startsWith('top_') ? child.parent.name : child.name;
            child.visible = parts.has(part);
            child.userData.staticIdleShadow = true;
            // Берцы входят в base. Тень от изнанки убирает полосы на лицевой стороне одежды.
            const isOutfitPart = part === 'base' || part.startsWith('top_') || part.startsWith('bottom_');
            const materials = Array.isArray(child.material) ? child.material : [child.material];
            for (const material of materials) {
                if (material && isOutfitPart) {
                    material.shadowSide = BackSide;
                }
                const color = palette[material?.name];
                if (!color || tinted.has(material)) continue;
                material.color.set(color).convertSRGBToLinear();
                material.userData.darkBaseColor?.copy(material.color);
                tinted.add(material);
            }
        });
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
    if (state.fading && state.easedBlendDuration) {
        const remainingBlend = Math.max(0, state.blendRemaining - step) / state.easedBlendDuration;
        const idleWeight = remainingBlend ** 2;
        state.fading.setEffectiveWeight(idleWeight);
        state.current.setEffectiveWeight(1 - idleWeight);
    }
    const blending = Boolean(state.fading);
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
