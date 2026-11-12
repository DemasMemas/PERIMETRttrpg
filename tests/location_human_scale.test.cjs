const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
const appearanceSource = fs.readFileSync(path.join(__dirname, '../app/static/js/characterAppearance.js'), 'utf8')
    .replace(/^import .*$/gm, '')
    .replace(/^export /gm, '');
const start = source.indexOf('function showHumanLocationPose(');
const end = source.indexOf('const CHARACTER_LABEL_NEAR_DISTANCE', start);

class Transform {
    constructor() { this.x = 0; this.y = 0; this.z = 0; }
    set(x, y, z) { Object.assign(this, { x, y, z }); }
}

class Mesh {
    constructor(geometry, material) {
        this.geometry = geometry;
        this.material = material;
        this.position = new Transform();
        this.rotation = new Transform();
        this.userData = {};
        this.children = [];
    }
    add(child) { this.children.push(child); }
}

const THREE = {
    Group: class { constructor() { this.children = []; this.userData = {}; } add(child) { this.children.push(child); } },
    Mesh,
    CylinderGeometry: class {
        constructor(top, bottom, height) { Object.assign(this, { top, bottom, height, kind: 'cylinder' }); }
    },
    SphereGeometry: class {
        constructor(radius) { Object.assign(this, { radius, kind: 'sphere' }); }
    },
    BoxGeometry: class {
        constructor(width, height, depth) { Object.assign(this, { width, height, depth, kind: 'box' }); }
    },
    ConeGeometry: class {
        constructor(radius, height) { Object.assign(this, { radius, height, kind: 'cone' }); }
    },
    MeshStandardMaterial: class { constructor(properties) { Object.assign(this, properties); } },
};

const { createCharacterModel, poseStandardHumanModel, characterLabelOffset } = new Function(
    'THREE', 'getUserColor', `${appearanceSource}\n${source.slice(start, end)}
        return { createCharacterModel, poseStandardHumanModel, characterLabelOffset };`,
)(THREE, () => '#8899aa');

function verticalBounds(mesh) {
    const { geometry, rotation, position } = mesh;
    let halfHeight;
    if (geometry.kind === 'sphere') halfHeight = geometry.radius;
    else if (geometry.kind === 'box') halfHeight = geometry.height / 2;
    else if (Math.abs(rotation.x) > 1) halfHeight = Math.max(geometry.top || geometry.radius,
        geometry.bottom || geometry.radius);
    else halfHeight = geometry.height / 2;
    return [position.y - halfHeight, position.y + halfHeight];
}

test('standard human is 1.80 m tall and stays above ground in every posture', async () => {
    const model = createCharacterModel(1);
    assert.equal(model.userData.isStandardHuman, true);
    const catalogSource = fs.readFileSync(path.join(__dirname, '../app/static/js/locationAssetCatalog.js'), 'utf8');
    const { LOCATION_ASSETS } = await import(`data:text/javascript;base64,${Buffer.from(catalogSource).toString('base64')}`);
    const heightOf = id => LOCATION_ASSETS.find(asset => asset.id === id).dimensions[2];
    assert.ok(Math.abs(heightOf('door') - 1.8 - 0.2) < 1e-9);
    assert.equal(heightOf('table'), 1);
    for (const [posture, expectedTop] of [['standing', 1.8], ['sitting', 1.33], ['prone', 0.58]]) {
        poseStandardHumanModel(model, posture);
        const bounds = model.children.filter(child => child.visible !== false).map(verticalBounds);
        const bottom = Math.min(...bounds.map(([low]) => low));
        const top = Math.max(...bounds.map(([, high]) => high));
        assert.ok(bottom >= -0.001, `${posture}: bottom ${bottom}`);
        assert.ok(Math.abs(top - expectedTop) < 0.001, `${posture}: top ${top}`);
        assert.ok(model.children.filter(child => child.userData.posturePart !== 'facingMarker')
            .every(child => child.castShadow && child.receiveShadow));
    }
    poseStandardHumanModel(model, 'standing');
    assert.equal(model.children.find(child => child.userData.posturePart === 'head').position.y, 1.62);
    assert.equal(model.children.find(child => child.userData.posturePart === 'facingMarker').position.y, 1.3);
    poseStandardHumanModel(model, 'sitting');
    const part = name => model.children.find(child => child.userData.posturePart === name);
    assert.equal(part('lowerLeg-1').rotation.x, Math.PI / 2);
    assert.equal(part('lowerLeg-1').position.y, 0.11);
    assert.equal(part('lowerLeg1').rotation.x, 0);
    assert.ok(part('foot-1').position.z < 0 && part('foot1').position.z > 0);
    assert.equal(part('head').rotation.x, 0);
    poseStandardHumanModel(model, 'prone', false);
    assert.equal(part('head').rotation.x, 0.25);
    assert.equal(part('neck').visible, false);
    assert.ok(part('head').position.z > 0);
    assert.ok(part('foot1').position.z < 0);
    assert.ok(part('head').position.y > part('body').position.y);
    poseStandardHumanModel(model, 'prone', true);
    assert.equal(part('head').rotation.x, 0.25);
    assert.equal(part('neck').visible, false);
    assert.ok(part('head').position.z > 0);
    assert.ok(part('foot1').position.z < 0);
    assert.ok(part('facingMarker').position.z > part('foot1').position.z);
    assert.ok(Math.cos(part('head').rotation.x) > 0);
    assert.ok(Math.min(...model.children.filter(child => child.visible !== false)
        .map(child => verticalBounds(child)[0])) >= -0.001);
});

test('labels clear the human silhouette without changing other model offsets', () => {
    const human = createCharacterModel(1);
    for (const [posture, expected] of [['standing', 2.05], ['sitting', 1.55], ['prone', 0.78]]) {
        assert.equal(characterLabelOffset({ model: human, posture }), expected);
    }
    const other = { userData: { labelHeight: 1.9 } };
    assert.equal(characterLabelOffset({ model: other, posture: 'standing' }), 1.9);
    assert.equal(characterLabelOffset({ model: other, posture: 'sitting' }), 1.25);
    assert.equal(characterLabelOffset({ model: other, posture: 'prone' }), 0.95);
});

test('Импортированный персонаж сохраняет одежду и скрывает указатель во всех позах', () => {
    const model = createCharacterModel(1);
    const marker = model.children.find(child => child.userData.posturePart === 'facingMarker');
    const parts = ['base','top_sweater','top_gorka','bottom_jeans','bottom_gorka',
        'face_calm','face_wounded','hair_short'].map(name => ({ name, userData: {}, isSkinnedMesh: true }));
    const coat = { name: 'top_leather_coat', isGroup: true };
    const coatMeshes = ['Кожа', 'Пуговицы'].map(name => ({ name, parent: coat, userData: {}, isSkinnedMesh: true }));
    parts.push(...coatMeshes);
    const appearance = { name: 'human_appearance', userData: {}, traverse: visit => parts.forEach(visit) };
    model.add(appearance);
    model.userData.characterAppearance = { top: 'gorka', bottom: 'jeans', hair: 'bald', expression: 'wounded' };
    for (const [posture, onBack, expected] of [
        ['standing', false, 'standing'],
        ['sitting', false, 'sitting'],
        ['prone', false, 'prone'],
        ['prone', true, 'prone'],
    ]) {
        poseStandardHumanModel(model, posture, onBack);
        assert.equal(marker.visible, false);
        assert.equal(appearance.userData.characterAppearancePose, expected);
        assert.deepEqual(parts.filter(part => part.visible).map(part => part.name),
            ['base','top_gorka','bottom_jeans','face_wounded']);
    }
    model.userData.characterAppearance.top = 'leather_coat';
    poseStandardHumanModel(model, 'standing');
    assert.deepEqual(parts.filter(part => part.visible).map(part => part.name),
        ['base','bottom_jeans','face_wounded','Кожа','Пуговицы']);
});

test('Игровой GLB содержит общий скелет и три цикла покоя', () => {
    const file = fs.readFileSync(path.join(__dirname, '../app/static/models/characters/field_male.glb'));
    const gltf = JSON.parse(file.toString('utf8', 20, 20 + file.readUInt32LE(12)));
    assert.equal(gltf.skins.length, 1);
    assert.deepEqual(gltf.animations.map(clip => clip.name).sort(), ['prone', 'sitting', 'standing']);
    const meshes = gltf.nodes.filter(node => node.mesh !== undefined);
    assert.equal(meshes.length, 13);
    assert.ok(meshes.every(node => node.skin === 0));
    for (const name of ['bottom_tactical', 'bottom_hunting', 'gear_mp50']) {
        assert.ok(meshes.some(mesh => mesh.name === name), name);
    }
    for (const [name, fabric, fasteners] of [
        ['top_leather_coat', 'coat_leather', 'coat_buttons'],
        ['top_trench_coat', 'trench_fabric', 'trench_buttons'],
    ]) {
        const node = meshes.find(mesh => mesh.name === name);
        assert.ok(node, name);
        const materials = gltf.meshes[node.mesh].primitives.map(primitive => gltf.materials[primitive.material]);
        assert.deepEqual(materials.map(material => material.name).sort(), [fabric, fasteners].sort());
        assert.ok(materials.find(material => material.name === fabric).normalTexture, name);
    }
});

function readAnimationTracks(asset, clipName) {
    const file = fs.readFileSync(path.join(__dirname, '../app/static/models/characters', asset));
    const jsonLength = file.readUInt32LE(12);
    const gltf = JSON.parse(file.toString('utf8', 20, 20 + jsonLength));
    const clip = gltf.animations.find(animation => animation.name === clipName);
    return new Map(clip.channels.map(channel => {
        const { output } = clip.samplers[channel.sampler];
        const accessor = gltf.accessors[output];
        const view = gltf.bufferViews[accessor.bufferView];
        const offset = 28 + jsonLength + (view.byteOffset || 0) + (accessor.byteOffset || 0);
        const components = channel.target.path === 'rotation' ? 4 : 3;
        const frames = Array.from({ length: accessor.count }, (_, frame) =>
            Array.from({ length: components }, (_, component) => file.readFloatLE(offset + (frame * components + component) * 4)));
        return [gltf.nodes[channel.target.node].name + '.' + channel.target.path, frames];
    }));
}

function poseDifference(a, b) {
    const distance = values => Math.max(...a.map((value, index) => Math.abs(value - values[index])));
    return a.length === 4 ? Math.min(distance(b), distance(b.map(value => -value))) : distance(b);
}

test('Все режимы передвижения замыкаются в цикл в четырёх направлениях', () => {
    for (const name of ['walk', 'run', 'sprint', 'crouch_walk', 'crawl']
        .flatMap(gait => ['', '_left', '_right', '_back'].map(direction => gait + direction))) {
        const tracks = readAnimationTracks('field_fall.glb', name);
        const shins = [...tracks].filter(([bone]) => /^LowerLeg.*\.rotation$/.test(bone));
        assert.equal(shins.length, 2, name);
        assert.ok(shins.every(([, frames]) => frames.some(frame => poseDifference(frame, frames[0]) > .05)), name);
        for (const [bone, frames] of tracks) {
            assert.ok(poseDifference(frames[0], frames.at(-1)) < 1e-4, `${name}: ${bone}`);
        }
    }
});

test('ИК ставит берцы на разную высоту и удерживает опорную ногу при смещении корпуса', async () => {
    const vendor = pathToFileURL(path.join(__dirname, '../app/static/vendor/three-0.162.0/build/three.module.min.js'));
    const T = await import(vendor);
    const file = fs.readFileSync(path.join(__dirname, '../app/static/js/characterFootIK.js'), 'utf8');
    const { createCharacterFootIK, fitCharacterFeet, restoreCharacterFeet } = await import(
        `data:text/javascript;base64,${Buffer.from(file.replace("'three'", JSON.stringify(vendor.href))).toString('base64')}`);
    const model = new T.Group();
    const makeBone = (name, x, y, z) => Object.assign(new T.Bone(), { name }).translateX(x).translateY(y).translateZ(z);
    const body = makeBone('Body', 0, .83, 0);
    const localUp = new T.Vector3(0, 1, 0);
    const upper = new T.Vector3(0, -.42, .08).normalize();
    const lower = new T.Vector3(0, -.42, -.08).normalize();
    model.add(body);
    const bones = [body], feet = [], vertices = [], indices = [], weights = [];
    for (const [side, x] of [['L', .13], ['R', -.13]]) {
        const thigh = makeBone('UpperLeg' + side, x, 0, 0);
        const shin = makeBone('LowerLeg' + side, 0, .43, 0);
        const boot = makeBone('Foot' + side, x, .06, 0);
        thigh.quaternion.setFromUnitVectors(localUp, upper);
        shin.quaternion.setFromUnitVectors(localUp, lower).premultiply(thigh.quaternion.clone().invert());
        body.add(thigh);
        thigh.add(shin);
        model.add(boot);
        bones.push(thigh, shin, boot);
        feet.push(boot);
        for (const z of [-.07, .10]) {
            vertices.push(x, 0, z);
            indices.push(bones.length - 1, 0, 0, 0);
            weights.push(1, 0, 0, 0);
        }
    }
    const geometry = new T.BufferGeometry();
    geometry.setAttribute('position', new T.Float32BufferAttribute(vertices, 3));
    geometry.setAttribute('skinIndex', new T.Uint16BufferAttribute(indices, 4));
    geometry.setAttribute('skinWeight', new T.Float32BufferAttribute(weights, 4));
    const skin = new T.SkinnedMesh(geometry);
    skin.name = 'base';
    model.add(skin);
    model.updateMatrixWorld(true);
    skin.bind(new T.Skeleton(bones));
    const ik = createCharacterFootIK(model);
    const point = new T.Vector3();
    const bootHeight = boot => boot.getWorldPosition(point).y;
    assert.ok(fitCharacterFeet(model, ik, x => x > 0 ? .18 : 0, false));
    assert.ok(Math.abs(bootHeight(feet[0]) - .24) < .005);
    assert.ok(Math.abs(bootHeight(feet[1]) - .06) < .005);
    restoreCharacterFeet(ik);
    assert.ok(Math.abs(bootHeight(feet[0]) - .06) < .001);
    fitCharacterFeet(model, ik, () => 0, true);
    restoreCharacterFeet(ik);
    model.position.z += .1;
    assert.ok(fitCharacterFeet(model, ik, () => 0, true));
    assert.ok(Math.abs(feet[0].getWorldPosition(point).z) < .005);
    assert.ok(Math.abs(feet[1].getWorldPosition(point).z) < .005);

    restoreCharacterFeet(ik);
    const slope = (x, z) => -.44 + .9 * x + .08 * z;
    assert.ok(fitCharacterFeet(model, ik, slope, false));
    model.updateMatrixWorld(true);
    for (let i = 0; i < feet.length; i++) {
        const clearance = [i * 2, i * 2 + 1].map(index => {
            skin.getVertexPosition(index, point).applyMatrix4(skin.matrixWorld);
            return point.y - slope(point.x, point.z);
        });
        assert.ok(Math.min(...clearance) >= -.003 && Math.max(...clearance) < .015, clearance);
        assert.ok(feet[i].quaternion.angleTo(new T.Quaternion()) > .5);
    }
    restoreCharacterFeet(ik);
    assert.ok(feet.every(boot => boot.quaternion.angleTo(new T.Quaternion()) < .001));
    assert.equal(fitCharacterFeet(model, ik, () => null, false), false);
});

test('Переходы между приседом и положением на животе стыкуются с циклами покоя', () => {
    const crouch = readAnimationTracks('field_male.glb', 'sitting');
    const transition = readAnimationTracks('field_fall.glb', 'crouch_to_prone');
    const rise = readAnimationTracks('field_fall.glb', 'prone_to_crouch');
    const idle = readAnimationTracks('field_fall.glb', 'prone_idle');
    const relaxed = readAnimationTracks('field_fall.glb', 'prone_drop');
    assert.deepEqual([...transition.keys()].sort(), [...crouch.keys()].sort());
    for (const [track, frames] of transition) {
        assert.ok(poseDifference(frames[0], crouch.get(track)[0]) < 1e-5, track + ' в начале перехода');
        assert.ok(poseDifference(frames.at(-1), idle.get(track)[0]) < 1e-5, track + ' в конце перехода');
        assert.ok(poseDifference(rise.get(track)[0], idle.get(track)[0]) < 1e-5, track + ' перед подъёмом');
        assert.ok(poseDifference(rise.get(track).at(-1), crouch.get(track)[0]) < 1e-5, track + ' после подъёма');
        assert.ok(poseDifference(idle.get(track)[0], relaxed.get(track)[0]) < 1e-5, track + ' перед расслаблением');
        assert.ok(poseDifference(idle.get(track)[0], idle.get(track).at(-1)) < 1e-5, track + ' в цикле дыхания');
    }
    const breathing = idle.get('Torso.rotation');
    assert.ok(poseDifference(breathing[0], breathing[Math.floor(breathing.length / 2)]) > .005);
});

test('Переходы повторно проигрываются после смены всех трёх поз', async () => {
    const vendor = path.join(__dirname, '../app/static/vendor/three-0.162.0/build/three.module.min.js');
    const { AnimationClip, AnimationMixer, Group, LoopOnce } = await import(pathToFileURL(vendor));
    const { apply, tick, setClips, current } = new Function('AnimationMixer', 'LoopOnce', `${appearanceSource}
        return {
            apply: applyCharacterAppearanceModel,
            tick: animateCharacterAppearanceIdle,
            setClips: clips => { collapseClips = new Map(clips.map(clip => [clip.name, clip])); },
            current: model => animatedModels.get(model).current,
        }; `)(AnimationMixer, LoopOnce);
    const model = new Group();
    model.animations = ['standing', 'sitting'].map(name => new AnimationClip(name, 1, []));
    setClips(['prone_idle', 'crouch_to_prone', 'prone_to_crouch', 'standing_to_crouch', 'crouch_to_standing',
        'standing_to_prone', 'prone_to_standing']
        .map(name => new AnimationClip(name, 1, [])));
    apply(model, null, 'standing', { immediate: true });
    tick(model, 0, 1);
    for (const [index, [pose, transition, idle]] of [
        ['sitting', 'standing_to_crouch', 'sitting'],
        ['prone', 'crouch_to_prone', 'prone_idle'],
        ['sitting', 'prone_to_crouch', 'sitting'],
        ['prone', 'crouch_to_prone', 'prone_idle'],
        ['sitting', 'prone_to_crouch', 'sitting'],
        ['standing', 'crouch_to_standing', 'standing'],
        ['prone', 'standing_to_prone', 'prone_idle'],
        ['standing', 'prone_to_standing', 'standing'],
        ['prone', 'standing_to_prone', 'prone_idle'],
        ['standing', 'prone_to_standing', 'standing'],
    ].entries()) {
        apply(model, null, pose);
        assert.equal(current(model).getClip().name, transition);
        tick(model, index * 1000 + 500, 1);
        assert.equal(current(model).getClip().name, transition);
        assert.equal(current(model).time, .5);
        tick(model, index * 1000 + 1000, 1);
        assert.equal(current(model).getClip().name, idle);
    }
});

test('Переходы из стойки и обратно совпадают с исходными позами на стыках', () => {
    const standing = readAnimationTracks('field_male.glb', 'standing');
    for (const [posture, asset, idle] of [
        ['crouch', 'field_male.glb', 'sitting'],
        ['prone', 'field_fall.glb', 'prone_idle'],
    ]) {
        const ground = readAnimationTracks(asset, idle);
        const down = readAnimationTracks('field_fall.glb', 'standing_to_' + posture);
        const up = readAnimationTracks('field_fall.glb', posture + '_to_standing');
        assert.deepEqual([...down.keys()].sort(), [...standing.keys()].sort());
        for (const [track, frames] of down) {
            assert.ok(poseDifference(frames[0], standing.get(track)[0]) < 1e-5, track + ' в стойке');
            assert.ok(poseDifference(frames.at(-1), ground.get(track)[0]) < 1e-5, track + ' после опускания');
            assert.ok(poseDifference(up.get(track)[0], ground.get(track)[0]) < 1e-5, track + ' перед подъёмом');
            assert.ok(poseDifference(up.get(track).at(-1), standing.get(track)[0]) < 1e-5, track + ' после подъёма');
        }
    }
});

test('Падение из стойки и приседа заканчивается в сохранённой позе на спине', () => {
    const settled = readAnimationTracks('field_fall.glb', 'prone_back_drop');
    for (const [start, clip] of [['standing', 'fall_back'], ['sitting', 'fall_back_crouch']]) {
        const idle = readAnimationTracks('field_male.glb', start);
        const fall = readAnimationTracks('field_fall.glb', clip);
        assert.deepEqual([...fall.keys()].sort(), [...idle.keys()].sort());
        for (const [track, frames] of fall) {
            assert.ok(poseDifference(frames[0], idle.get(track)[0]) < 1e-5, track + ' в начале падения');
            assert.ok(poseDifference(frames.at(-1), settled.get(track).at(-1)) < 1e-5, track + ' после падения');
        }
    }
});

test('saved visual variant reaches initial model, live socket update and fog memory', () => {
    const main = fs.readFileSync(path.join(__dirname, '../app/static/js/main.js'), 'utf8');
    assert.match(main, /s\.posture,\s*s\.prone_on_back/);
    assert.match(main, /data\.character\.posture,\s*data\.character\.prone_on_back/);
    assert.match(main, /socket\.on\('location_character_visual_pose_updated'/);
    assert.match(source, /proneOnBack: Boolean\(entry\.proneOnBack\)/);
    assert.match(source, /applyFogGhostPosture\(ghost, snapshot\.posture, snapshot\.proneOnBack, snapshot\.collapsed\)/);
    assert.match(source, /poseStandardHumanModel\(entry\.model, normalized, back, \{ collapsed, immediate \}\)/);
});
