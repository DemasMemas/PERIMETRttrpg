const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scene = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8')
    .replace(/\r\n/g, '\n');

test('GM hidden models use a translucent non-shadow-casting appearance', () => {
    const start = scene.indexOf('function makeGmHiddenGhost(root)');
    const end = scene.indexOf('function cloneFogGhost(root)', start);
    assert.ok(start >= 0 && end > start);
    const material = {
        color: { lerp(_color, amount) { this.tint = amount; } },
        emissive: { set(value) { this.value = value; } },
        opacity: 1,
    };
    const first = { isMesh: true, material, castShadow: true, receiveShadow: true };
    const second = { isMesh: true, material, castShadow: true, receiveShadow: true };
    const root = { userData: {}, traverse(visit) { [first, second].forEach(visit); } };
    const context = vm.createContext({
        THREE: { DoubleSide: 2, Color: class { constructor(value) { this.value = value; } } },
    });
    vm.runInContext(scene.slice(start, end), context);
    assert.equal(context.makeGmHiddenGhost(root), root);
    assert.equal(material.opacity, 0.38);
    assert.equal(material.color.tint, 0.65);
    assert.equal(material.transparent, true);
    assert.equal(material.depthWrite, false);
    assert.equal(material.side, 2);
    assert.equal(first.castShadow, false);
    assert.equal(second.receiveShadow, false);
    assert.equal(root.userData.gmHiddenGhost, true);
    assert.equal(first.raycast, undefined);
});

test('hidden decor and structures show only to GM regardless of editor mode', () => {
    const start = scene.indexOf('    objectMeshes.forEach((mesh) => {', scene.indexOf('    entry.label.visible = visible && onViewedFloor;'));
    const end = scene.indexOf('    syncFogMemoryGhosts(', start);
    const gmVisibility = scene.match(/^    const gmCanSeeHidden = .*;$/m)?.[0];
    assert.ok(gmVisibility);
    assert.ok(start >= 0 && end > start);
    const decor = { position: { x: 1, z: 1 }, userData: { sourceObject: { gm_hidden: true }, objType: 'rock' }, visible: false };
    const structure = { position: { x: 1, z: 1 }, userData: { locationObject: { properties: { gm_hidden: true } } }, visible: false };
    const context = vm.createContext({
        objectMeshes: [decor], locationObjectMeshes: [structure],
        window: { isGM: true }, firstPersonView: null, editMode: false, active: false, sources: [],
        isFogHiddenObject: () => false, locationObjectFloorVisible: () => true,
        decorDisplayLevel: () => 0, structureLevel: () => 0, structureBuildingId: () => null,
        locationLevelVisible: () => true, locationViewLevel: 0, getObjectGridPosition: () => ({ x: 1, y: 1 }),
    });
    const update = vm.runInContext(`() => { ${gmVisibility}
 ${scene.slice(start, end)} }`, context);
    for (const isGM of [true, false]) {
        for (const editMode of [true, false]) {
            context.window.isGM = isGM;
            context.editMode = editMode;
            update();
            assert.equal(decor.visible, isGM);
            assert.equal(structure.visible, isGM);
        }
    }
    assert.match(scene, /if \(properties\.gm_hidden && window\.isGM\) makeGmHiddenGhost\(group\)/);
    assert.match(scene, /if \(obj\.gm_hidden && window\.isGM\) makeGmHiddenGhost\(mesh\)/);
});

test('hidden lamp fixture remains a GM ghost outside editing while players never see it', () => {
    assert.match(scene, /if \(source\.gm_hidden && window\.isGM\) makeGmHiddenGhost\(bulb\)/);
    assert.match(scene, /bulb\.visible = Boolean\(window\.isGM && \(editMode \|\| source\.gm_hidden\)\)/);
    assert.match(scene, /child\.userData\.locationLampBulb && child\.userData\.gmHiddenGhost/);
    assert.match(scene, /picker\.visible = Boolean\(window\.isGM && editMode\)/);
});
